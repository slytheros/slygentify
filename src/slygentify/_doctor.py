"""Private, read-only core for static managed-knowledge diagnostics."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Literal

from slygentify._configuration import EffectiveConfiguration, load_configuration
from slygentify._diagnostics import DiagnosticDetail
from slygentify._generation import generate_agents_document
from slygentify._managed_section import (
    ManagedSectionError,
    extract_managed_section,
    render_managed_section,
    section_digest,
)
from slygentify._provenance import (
    STATE_LOCATION,
    StateDocument,
    StateError,
    StateInput,
    load_state_json,
    state_from_scan,
)
from slygentify._repository import AGENTS_FILENAME, RepositoryPathError, find_git_root
from slygentify._scan import _scan_foundation, _ScanFoundationError
from slygentify._scan.normalization import _id
from slygentify._version import __version__
from slygentify.models import (
    DiagnosticDisposition,
    DoctorDiagnostic,
    DoctorResult,
    Evidence,
    Repository,
    ScanResult,
)
from slygentify.traceability import implements

_MAX_STATE_BYTES = 128 * 1024 * 1024

_COMPONENT_CODES = frozenset(
    {
        "configuration.component",
        "composition.auxiliary-component",
        "composition.overlapping-workspace-membership",
        "composition.unresolved-relationship",
        "generic.cmake",
        "generic.kicad",
        "generic.manifest",
        "javascript.component.unknown",
        "javascript.component.verified",
        "javascript.workspace.member",
        "python.component.candidate",
        "python.component.verified",
        "python.workspace.member",
    }
)
_TOOLING_CODES = frozenset(
    {
        "javascript.ci.command",
        "javascript.framework.declaration",
        "javascript.manager-conflict",
        "javascript.manager.evidence",
        "javascript.runtime-conflict",
        "javascript.runtime.declaration",
        "javascript.script.declaration",
        "javascript.tool-configuration-conflict",
        "javascript.tool.evidence",
        "python.ci.command",
        "python.framework.declaration",
        "python.manager-conflict",
        "python.manager.evidence",
        "python.runtime-conflict",
        "python.runtime.declaration",
        "python.tool-configuration-conflict",
        "python.tool.evidence",
    }
)
_PATH_DIAGNOSTIC_CODES = frozenset(
    {
        "inspection.invalid-workspace-member",
        "inspection.missing-workspace-member",
        "javascript.missing-workspace-member",
        "javascript.unresolved-typescript-reference",
        "javascript.unsafe-bin-target",
        "python.missing-workspace-member",
    }
)
_COMMAND_DIAGNOSTIC_CODES = frozenset(
    {
        "javascript.ci.command.dynamic",
        "javascript.dynamic-ci-command-unknown",
        "javascript.external-ci-include",
        "python.ci.command.dynamic",
        "python.dynamic-ci-command-unknown",
        "python.external-ci-include",
    }
)
_COMMAND_SOURCE_KINDS = frozenset({"ci-command", "declared-command"})


class DoctorInputError(Exception):
    """A caller-selected doctor target or option prevented a repository result."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.diagnostic = DiagnosticDetail(
            "doctor.invalid-input",
            ".",
            "Doctor could not safely validate the selected input.",
            "Doctor did not emit a result and did not modify repository files.",
            recovery="Correct PATH or the explicitly selected Git executable, then rerun doctor.",
            safety_rationale="Doctor cannot safely infer a replacement target or executable from invalid caller input.",
            disposition="problem",
        )


class DoctorOperationalError(Exception):
    """An operational failure prevented a trustworthy doctor result."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.diagnostic = DiagnosticDetail(
            "doctor.operation-failed",
            ".",
            "Doctor could not produce a trustworthy result.",
            "Doctor did not emit a result and did not modify repository files.",
            recovery="Correct the reported environment or tool condition, then retry doctor.",
            safety_rationale="Doctor is read-only and cannot safely repair an operational environment failure.",
            disposition="problem",
        )


def _evidence_id(kind: str, location: str, locator: str | None = None) -> str:
    return _id("doctor-evidence", kind, location, locator or "")


def _diagnostic_id(
    code: str, subject_id: str | None, location: str | None, evidence_ids: Iterable[str]
) -> str:
    return _id("doctor-diagnostic", code, subject_id or "", location or "", *sorted(evidence_ids))


def _safe_digest(root: Path, location: str) -> str | None:
    """Return a regular in-root file digest without following an unsafe entry."""

    target = root.joinpath(*PurePosixPath(location).parts)
    try:
        metadata = target.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return None
    digest = hashlib.sha256()
    try:
        with target.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _safe_managed_section(root: Path) -> bytes | None:
    """Return a bounded, valid managed section without exposing surrounding guidance."""
    target = root / AGENTS_FILENAME
    try:
        metadata = target.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_size > _MAX_STATE_BYTES
        ):
            return None
        return extract_managed_section(target.read_bytes())
    except (OSError, ManagedSectionError):
        return None


def _load_state(root: Path) -> tuple[StateDocument | None, str | None]:
    """Return valid state or a bounded invalid-state marker without raising."""

    target = root.joinpath(*PurePosixPath(STATE_LOCATION).parts)
    if not os.path.lexists(target):
        return None, None
    try:
        metadata = target.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_size > _MAX_STATE_BYTES
        ):
            return (
                None,
                "state.unsafe-entry" if metadata.st_size <= _MAX_STATE_BYTES else "state.too-large",
            )
        return load_state_json(target.read_bytes()), None
    except OSError:
        return None, "state.unreadable"
    except StateError as error:
        return None, error.category


def _projection(state: StateDocument, codes: frozenset[str]) -> tuple[object, ...]:
    return tuple(item for item in state.derivations if item.claim_code in codes)


def _state_changed(recorded: StateDocument, current: StateDocument) -> bool:
    return (
        recorded.producer_version != current.producer_version
        or recorded.configuration != current.configuration
        or recorded.effective_limits != current.effective_limits
        or recorded.inputs != current.inputs
        or recorded.derivations != current.derivations
        or recorded.completion != current.completion
        or recorded.skipped_scopes != current.skipped_scopes
    )


def _repository_evidence() -> Evidence:
    return Evidence(
        id=_evidence_id("repository", ".git"),
        source_kind="doctor-repository",
        location=".git",
        locator=None,
        observation="A Git repository root was selected for static doctor checks.",
        verification_method="safe metadata inspection",
    )


def _invalid_configuration_result(root: Path) -> DoctorResult:
    evidence = _repository_evidence()
    config_evidence = Evidence(
        id=_evidence_id("configuration", "slygentify.toml"),
        source_kind="doctor-configuration",
        location="slygentify.toml",
        locator=None,
        observation="Root configuration could not be validated before repository traversal.",
        verification_method="bounded configuration validation",
    )
    diagnostic = DoctorDiagnostic(
        id=_diagnostic_id(
            "doctor.configuration.invalid", None, "slygentify.toml", (config_evidence.id,)
        ),
        code="doctor.configuration.invalid",
        severity="error",
        classification="verified",
        subject_id=None,
        location="slygentify.toml",
        problem="Root Slygentify configuration is malformed, unsupported, or unsafe.",
        effect="Doctor did not traverse repository content because configuration cannot be trusted.",
        remediation="Correct slygentify.toml for the installed schema, then rerun doctor.",
        evidence_ids=(config_evidence.id,),
        disposition="problem",
    )
    repository = Repository(
        id=_id("repository", "."), root=".", kind="git", evidence_ids=(evidence.id,)
    )
    return DoctorResult(
        schema_version=1,
        producer_version=__version__,
        completion="partial",
        repository=repository,
        evidence=tuple(
            sorted((evidence, config_evidence), key=lambda item: (item.id, item.location))
        ),
        diagnostics=(diagnostic,),
        skipped_scopes=(),
    )


def _comparison_evidence(
    evidence: dict[str, Evidence],
    *,
    kind: str,
    location: str,
    observation: str,
    locator: str | None = None,
) -> str:
    identifier = _evidence_id(kind, location, locator)
    evidence.setdefault(
        identifier,
        Evidence(
            id=identifier,
            source_kind="doctor-provenance",
            location=location,
            locator=locator,
            observation=observation,
            verification_method="deterministic provenance comparison",
        ),
    )
    return identifier


def _append_diagnostic(
    diagnostics: list[DoctorDiagnostic],
    *,
    code: str,
    severity: Literal["info", "warning", "error"],
    classification: Literal["verified", "inferred", "recommended", "unknown"],
    subject_id: str | None,
    location: str | None,
    problem: str,
    effect: str,
    remediation: str | None,
    evidence_ids: Iterable[str],
    disposition: DiagnosticDisposition,
    category: str | None = None,
    safety_rationale: str | None = None,
) -> None:
    references = tuple(sorted(set(evidence_ids)))
    diagnostics.append(
        DoctorDiagnostic(
            id=_diagnostic_id(code, subject_id, location, references),
            code=code,
            severity=severity,
            classification=classification,
            subject_id=subject_id,
            location=location,
            problem=problem,
            effect=effect,
            remediation=remediation,
            evidence_ids=references,
            category=category,
            safety_rationale=safety_rationale,
            disposition=disposition,
        )
    )


def _doctor_sentence(value: str) -> str:
    text = " ".join(value.split()).rstrip(".")
    if not text:
        return text
    return f"{text[0].upper()}{text[1:]}."


def _command_became_unverifiable(
    recorded: StateDocument, current: StateDocument, result: ScanResult
) -> tuple[StateInput, ...]:
    current_ids = {item.id for item in current.inputs}
    current_diagnostics = {
        item.location
        for item in result.diagnostics
        if item.location is not None and item.code in _COMMAND_DIAGNOSTIC_CODES
    }
    return tuple(
        item
        for item in recorded.inputs
        if item.source_kind in _COMMAND_SOURCE_KINDS
        and item.id not in current_ids
        and item.location in current_diagnostics
    )


@implements("REQ047", "REQ048")
def doctor_repository(
    path: str | os.PathLike[str] = ".",
    *,
    git_executable: str | os.PathLike[str] | None = None,
) -> DoctorResult:
    """Assess current managed knowledge without modifying or executing the repository."""

    try:
        root = find_git_root(Path(path)).resolve(strict=True)
    except (RepositoryPathError, OSError, TypeError, ValueError) as error:
        raise DoctorInputError(str(error)) from None
    try:
        configuration: EffectiveConfiguration = load_configuration(root)
    except ValueError:
        return _invalid_configuration_result(root)

    recorded, invalid_state_category = _load_state(root)
    try:
        execution = _scan_foundation(
            root, git_executable=git_executable, configuration=configuration
        )
    except _ScanFoundationError as error:
        if git_executable is not None and "git executable" in str(error):
            raise DoctorInputError(str(error)) from None
        raise DoctorOperationalError(str(error)) from None
    except (OSError, TypeError, ValueError) as error:
        raise DoctorOperationalError(str(error)) from None

    result = execution.result
    evidence = {item.id: item for item in result.evidence}
    diagnostics: list[DoctorDiagnostic] = []
    completion: Literal["complete", "partial"] = result.completion

    if invalid_state_category is not None:
        state_evidence = _comparison_evidence(
            evidence,
            kind="invalid-state",
            location=STATE_LOCATION,
            observation="Recorded provenance state could not be safely parsed or validated.",
        )
        _append_diagnostic(
            diagnostics,
            code="doctor.state.invalid",
            severity="error",
            classification="verified",
            subject_id=result.repository.id,
            location=STATE_LOCATION,
            problem="The generated ownership and provenance record for AGENTS.md could not be validated.",
            effect="Doctor cannot rely on managed ownership or compare recorded repository knowledge.",
            remediation="Upgrade to the latest reviewed Slygentify build and rerun slygentify init . --dry-run. If it still fails, rename the state file to a new non-existing backup name, rerun the dry-run, and apply only a recoverable or otherwise safe plan.",
            evidence_ids=(state_evidence,),
            disposition="problem",
            category=invalid_state_category,
            safety_rationale="Doctor is read-only and invalid state cannot safely establish ownership for automatic replacement.",
        )
        completion = "partial"
    elif recorded is None:
        guidance_evidence = _comparison_evidence(
            evidence,
            kind="unmanaged-guidance",
            location=AGENTS_FILENAME,
            observation="No valid managed provenance record owns root agent guidance.",
        )
        _append_diagnostic(
            diagnostics,
            code="doctor.guidance.unmanaged",
            severity="info",
            classification="unknown",
            subject_id=result.repository.id,
            location=AGENTS_FILENAME,
            problem="Root agent guidance is unmanaged or has not been initialized by Slygentify.",
            effect="Doctor cannot establish whether the guidance reflects current repository knowledge.",
            remediation="Leave human-owned guidance unchanged or adopt it through a reviewed initialization flow.",
            evidence_ids=(guidance_evidence,),
            disposition="notice",
        )
    else:
        current_state = state_from_scan(
            result, execution.configuration, execution.content_fingerprints
        )
        component_drift = _projection(recorded, _COMPONENT_CODES) != _projection(
            current_state, _COMPONENT_CODES
        )
        tooling_drift = _projection(recorded, _TOOLING_CODES) != _projection(
            current_state, _TOOLING_CODES
        )
        path_diagnostics = tuple(
            item for item in result.diagnostics if item.code in _PATH_DIAGNOSTIC_CODES
        )
        if component_drift:
            comparison = _comparison_evidence(
                evidence,
                kind="component-drift",
                location=".",
                observation="Recorded and current component or relationship derivations differ.",
            )
            _append_diagnostic(
                diagnostics,
                code="doctor.component.drift",
                severity="warning",
                classification="verified",
                subject_id=result.repository.id,
                location=".",
                problem="Current component or relationship knowledge differs from recorded provenance.",
                effect="Managed guidance may no longer describe the repository component boundaries.",
                remediation="Review component boundaries and regenerate managed guidance after confirmation.",
                evidence_ids=(comparison,),
                disposition="problem",
            )
        if tooling_drift:
            comparison = _comparison_evidence(
                evidence,
                kind="tooling-drift",
                location=".",
                observation="Recorded and current supported tooling derivations differ.",
            )
            _append_diagnostic(
                diagnostics,
                code="doctor.tooling.drift",
                severity="warning",
                classification="verified",
                subject_id=result.repository.id,
                location=".",
                problem="Current supported tooling knowledge differs from recorded provenance.",
                effect="Managed workflow guidance may no longer identify the current tooling contract.",
                remediation="Confirm the authoritative workflow and regenerate managed guidance after review.",
                evidence_ids=(comparison,),
                disposition="problem",
            )
        for item in path_diagnostics:
            path_evidence = _comparison_evidence(
                evidence,
                kind="missing-path",
                location=item.location or ".",
                locator=item.code,
                observation="A supported declaration now refers to a missing or unsafe in-root path.",
            )
            _append_diagnostic(
                diagnostics,
                code="doctor.path.missing",
                severity="warning",
                classification="verified",
                subject_id=item.subject_id or result.repository.id,
                location=item.location,
                problem="A supported operational path is missing or unsafe.",
                effect="Related repository knowledge cannot be relied upon without review.",
                remediation="Restore the path, update the declaration, or retire the reference.",
                evidence_ids=(*item.evidence_ids, path_evidence),
                disposition="problem",
            )
        missing_locations = {
            item.location
            for item in recorded.inputs
            if item.id not in {current.id for current in current_state.inputs}
            and item.location not in {current.location for current in current_state.inputs}
        }
        for location in sorted(missing_locations):
            missing_evidence = _comparison_evidence(
                evidence,
                kind="missing-evidence",
                location=location,
                observation="Recorded evidence is no longer available for a current provenance comparison.",
            )
            _append_diagnostic(
                diagnostics,
                code="doctor.evidence.missing",
                severity="warning",
                classification="unknown",
                subject_id=result.repository.id,
                location=location,
                problem="Previously recorded evidence is missing, unreadable, excluded, or unsafe.",
                effect="Dependent managed knowledge cannot be reverified from current repository evidence.",
                remediation="Restore or replace the evidence, update configuration, or retire the dependent claim.",
                evidence_ids=(missing_evidence,),
                disposition="problem",
            )
        for command_input in _command_became_unverifiable(recorded, current_state, result):
            command_evidence = _comparison_evidence(
                evidence,
                kind="command-unverifiable",
                location=command_input.location,
                locator=command_input.locator,
                observation="A previously attributable command can no longer be verified statically.",
            )
            _append_diagnostic(
                diagnostics,
                code="doctor.command.unverifiable",
                severity="warning",
                classification="unknown",
                subject_id=result.repository.id,
                location=command_input.location,
                problem="A previously attributable validation command is no longer statically verifiable.",
                effect="Managed command knowledge cannot be relied upon without manual review.",
                remediation="Use a literal supported declaration where practical or verify the command manually in an authorized environment.",
                evidence_ids=(command_evidence,),
                disposition="limitation",
            )
        recorded_artifact = next(
            (item for item in recorded.artifacts if item.location == AGENTS_FILENAME), None
        )
        artifact_state_stale = False
        if recorded_artifact is None:
            guidance_evidence = _comparison_evidence(
                evidence,
                kind="unmanaged-guidance",
                location=AGENTS_FILENAME,
                observation="Valid state does not claim ownership of root agent guidance.",
            )
            _append_diagnostic(
                diagnostics,
                code="doctor.guidance.unmanaged",
                severity="info",
                classification="unknown",
                subject_id=result.repository.id,
                location=AGENTS_FILENAME,
                problem="Root agent guidance is not owned by valid managed provenance.",
                effect="Doctor cannot establish whether the guidance reflects current repository knowledge.",
                remediation="Leave human-owned guidance unchanged or adopt it through a reviewed initialization flow.",
                evidence_ids=(guidance_evidence,),
                disposition="notice",
            )
        else:
            fresh_guidance = generate_agents_document(
                result,
                max_bytes=execution.configuration.max_agents_bytes,
                max_component_entries=execution.configuration.max_component_entries,
            ).markdown
            artifact_evidence = _comparison_evidence(
                evidence,
                kind="managed-artifact",
                location=AGENTS_FILENAME,
                observation="Recorded, current, and freshly generated managed artifact bytes were compared.",
            )
            if recorded_artifact.ownership == "section":
                current_section = _safe_managed_section(root)
                fresh_section = render_managed_section(fresh_guidance)
                if current_section is None:
                    _append_diagnostic(
                        diagnostics,
                        code="doctor.artifact.missing",
                        severity="error",
                        classification="verified",
                        subject_id=result.repository.id,
                        location=AGENTS_FILENAME,
                        problem="A managed Slygentify guidance section is missing or malformed.",
                        effect="The managed guidance contract cannot be relied upon.",
                        remediation="Restore the visible markers or review a dry-run before adopting again.",
                        evidence_ids=(artifact_evidence,),
                        disposition="problem",
                    )
                elif section_digest(current_section) == recorded_artifact.sha256:
                    if current_section != fresh_section:
                        _append_diagnostic(
                            diagnostics,
                            code="doctor.artifact.stale",
                            severity="warning",
                            classification="verified",
                            subject_id=result.repository.id,
                            location=AGENTS_FILENAME,
                            problem="Managed guidance matches recorded bytes but differs from fresh generation.",
                            effect="The managed artifact is stale relative to current repository evidence.",
                            remediation="Review fresh generation and explicitly regenerate the artifact if accepted.",
                            evidence_ids=(artifact_evidence,),
                            disposition="problem",
                        )
                elif current_section != fresh_section:
                    _append_diagnostic(
                        diagnostics,
                        code="doctor.artifact.diverged",
                        severity="warning",
                        classification="unknown",
                        subject_id=result.repository.id,
                        location=AGENTS_FILENAME,
                        problem="Managed guidance differs from both recorded and freshly generated bytes.",
                        effect="The section may be a human edit and cannot be classified automatically.",
                        remediation="Preserve the file and review the visible managed section before replacing it.",
                        evidence_ids=(artifact_evidence,),
                        disposition="problem",
                    )
                else:
                    artifact_state_stale = True
            else:
                current_digest = _safe_digest(root, AGENTS_FILENAME)
                fresh_digest = hashlib.sha256(fresh_guidance.encode("utf-8")).hexdigest()
                if current_digest is None:
                    _append_diagnostic(
                        diagnostics,
                        code="doctor.artifact.missing",
                        severity="error",
                        classification="verified",
                        subject_id=result.repository.id,
                        location=AGENTS_FILENAME,
                        problem="A managed root guidance artifact is missing or not a safe regular file.",
                        effect="The managed-artifact contract cannot be relied upon.",
                        remediation="Review a dry-run and explicitly recreate or retire managed ownership.",
                        evidence_ids=(artifact_evidence,),
                        disposition="problem",
                    )
                elif current_digest == recorded_artifact.sha256 and fresh_digest != current_digest:
                    _append_diagnostic(
                        diagnostics,
                        code="doctor.artifact.stale",
                        severity="warning",
                        classification="verified",
                        subject_id=result.repository.id,
                        location=AGENTS_FILENAME,
                        problem="Managed root guidance matches recorded bytes but differs from fresh generation.",
                        effect="The managed artifact is stale relative to current repository evidence.",
                        remediation="Review fresh generation and explicitly regenerate the artifact if accepted.",
                        evidence_ids=(artifact_evidence,),
                        disposition="problem",
                    )
                elif current_digest not in {recorded_artifact.sha256, fresh_digest}:
                    _append_diagnostic(
                        diagnostics,
                        code="doctor.artifact.diverged",
                        severity="warning",
                        classification="unknown",
                        subject_id=result.repository.id,
                        location=AGENTS_FILENAME,
                        problem="Managed root guidance differs from both recorded and freshly generated bytes.",
                        effect="The content may be a human edit and cannot be classified automatically.",
                        remediation="Preserve the file, review a dry-run or diff, and replace only with explicit authorization.",
                        evidence_ids=(artifact_evidence,),
                        disposition="problem",
                    )
                elif current_digest == fresh_digest and current_digest != recorded_artifact.sha256:
                    artifact_state_stale = True
        specific_codes = {item.code for item in diagnostics}
        if (_state_changed(recorded, current_state) or artifact_state_stale) and not specific_codes:
            stale_evidence = _comparison_evidence(
                evidence,
                kind="state-stale",
                location=STATE_LOCATION,
                observation="Recorded provenance differs from current inputs without a semantic drift finding.",
            )
            _append_diagnostic(
                diagnostics,
                code="doctor.state.stale",
                severity="info",
                classification="verified",
                subject_id=result.repository.id,
                location=STATE_LOCATION,
                problem="Managed provenance is stale relative to current repository inputs.",
                effect="Refreshing provenance is useful, but current normalized knowledge and guidance agree.",
                remediation="Review the changed evidence and regenerate state if the normalized result remains acceptable.",
                evidence_ids=(stale_evidence,),
                disposition="notice",
            )

    if result.completion == "partial":
        component_ids = {item.path: item.id for item in result.components}
        causes = execution.partial_causes
        if not causes:
            raise DoctorOperationalError(
                "fresh inspection was partial without structured causal accounting"
            )
        for cause in causes:
            partial_evidence = _comparison_evidence(
                evidence,
                kind="partial-inspection",
                location=cause.location,
                locator=cause.source_code,
                observation=f"Fresh scan reported {cause.source_code} as a partial cause.",
            )
            _append_diagnostic(
                diagnostics,
                code="doctor.inspection.partial",
                severity="warning",
                classification="unknown",
                subject_id=component_ids.get(cause.subject_path or "", result.repository.id),
                location=cause.location,
                problem=_doctor_sentence(cause.problem),
                effect=_doctor_sentence(
                    f"{cause.effect.rstrip('.')}. Doctor cannot claim drift absence for this boundary"
                ),
                remediation=(
                    _doctor_sentence(cause.recovery) if cause.recovery is not None else None
                ),
                evidence_ids=(*cause.evidence_ids, partial_evidence),
                disposition=cause.disposition,
            )
        completion = "partial"

    return DoctorResult(
        schema_version=1,
        producer_version=result.producer_version,
        completion=completion,
        repository=result.repository,
        evidence=tuple(sorted(evidence.values(), key=lambda item: (item.id, item.location))),
        diagnostics=tuple(
            sorted(
                diagnostics,
                key=lambda item: (item.code, item.subject_id or "", item.location or "", item.id),
            )
        ),
        skipped_scopes=result.skipped_scopes,
    )
