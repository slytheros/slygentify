"""Candidate aggregation and canonical public scan-result construction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any, cast

from slygentify._configuration import EffectiveConfiguration
from slygentify._scan.contracts import (
    ComponentCandidate as _ComponentCandidate,
)
from slygentify._scan.contracts import (
    DetectionContext,
    DetectionResult,
)
from slygentify._scan.contracts import (
    DiagnosticCandidate as _DiagnosticCandidate,
)
from slygentify._scan.contracts import (
    EvidenceCandidate as _EvidenceCandidate,
)
from slygentify._scan.contracts import PartialCause as _PartialCause
from slygentify._scan.detectors import BUILTIN_DETECTORS
from slygentify._scan.detectors._support import evidence_key as _evidence_key
from slygentify._scan.kernel import _Inspection, _MemoryLedger, _RepositoryView
from slygentify._scan.paths import nearest_ancestor as _nearest_ancestor
from slygentify._scan.paths import parent as _parent
from slygentify._version import __version__
from slygentify.models import (
    Component,
    ComponentRelationship,
    Diagnostic,
    Evidence,
    Finding,
    Repository,
    ScanResult,
    SkippedScope,
)
from slygentify.traceability import implements

_AUXILIARY_COMPONENT_PATH_PARTS = frozenset(
    {"test", "tests", "example", "examples", "docs", "template", "templates"}
)
_RESOURCE_REASONS = frozenset(
    {
        "max_depth",
        "max_entries",
        "max_file_bytes",
        "max_total_bytes",
        "max_elapsed_seconds",
        "max_open_files",
        "max_memory_bytes",
    }
)


def _boundary_cause(boundary: SkippedScope) -> tuple[str, str, str]:
    """Describe one partial boundary without treating routine exclusions as failures."""

    if boundary.reason in _RESOURCE_REASONS:
        accounting = f"limit {boundary.effective_limit}"
        if boundary.consumed is not None:
            accounting = f"{accounting}; consumed {boundary.consumed}"
        return (
            f"Fresh inspection reached the {boundary.reason} resource boundary at {boundary.scope}",
            f"{boundary.omitted_scope} was omitted after {accounting}",
            "exclude irrelevant repository content or raise "
            f"scan.limits.{boundary.reason} in the root slygentify.toml, then rerun doctor",
        )
    if boundary.reason == "git_tracking_unavailable":
        return (
            "Tracked Git paths were unavailable during fresh inspection",
            "Tracked files hidden by checked-out Gitignore rules may have been omitted",
            "restore the standard trusted Git executable on PATH, or explicitly select a reviewed "
            "Git executable, then rerun doctor",
        )
    if boundary.reason in {"unsafe_file", "unsafe_directory", "invalid_gitignore"}:
        return (
            f"Fresh inspection could not safely read {boundary.scope}",
            f"{boundary.omitted_scope} was omitted from the fresh repository evidence",
            f"make {boundary.scope} safely readable, or intentionally exclude it in the root "
            "slygentify.toml, then rerun doctor",
        )
    return (
        f"Fresh inspection reached the {boundary.reason} boundary at {boundary.scope}",
        f"{boundary.omitted_scope} was omitted from the fresh repository evidence",
        "correct the reported repository condition or intentionally exclude the affected scope, "
        "then rerun doctor",
    )


def _matching_boundary(
    candidate: _DiagnosticCandidate,
    boundaries: tuple[SkippedScope, ...],
) -> SkippedScope | None:
    for boundary in boundaries:
        if boundary.scope != candidate.location:
            continue
        if boundary.reason in _RESOURCE_REASONS and candidate.code in {
            "inspection.max-memory-bytes",
            "inspection.unreadable-evidence",
        }:
            return boundary
        expected = {
            "git_tracking_unavailable": "inspection.git-tracked-paths-unavailable",
            "unsafe_directory": "inspection.unsafe-directory",
            "unsafe_file": "inspection.unsafe-file",
            "invalid_gitignore": "inspection.invalid-gitignore",
        }.get(boundary.reason)
        if expected == candidate.code:
            return boundary
    return None


def _id(kind: str, *values: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"slygentify:v1\0")
    digest.update(kind.encode("utf-8"))
    for value in values:
        digest.update(b"\0")
        digest.update(value.encode("utf-8"))
    return f"{kind}_{digest.hexdigest()}"


def _record_size(value: object) -> int:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(cast(Any, value))
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


@implements("REQ015", "REQ030", "REQ032", "REQ041")
def _normalize(
    root: Path,
    inspection: _Inspection,
    *,
    memory_limit: int | None,
    configuration: EffectiveConfiguration | None = None,
    content_fingerprints: dict[str, str] | None = None,
    partial_causes: list[_PartialCause] | None = None,
) -> ScanResult:
    view = _RepositoryView(inspection)
    detector_results: list[DetectionResult] = []
    generic_component_paths: frozenset[str] = frozenset()
    for detector in BUILTIN_DETECTORS:
        if view.checkpoint():
            break
        detector_result = detector(view, DetectionContext(generic_component_paths))
        detector_results.append(detector_result)
        generic_component_paths = frozenset(
            {
                *generic_component_paths,
                *(
                    candidate.path
                    for candidate in detector_result.components
                    if candidate.ecosystem == "generic"
                ),
            }
        )
    evidence_candidates = tuple(
        candidate for result in detector_results for candidate in result.evidence
    )
    component_candidates = tuple(
        candidate for result in detector_results for candidate in result.components
    )
    finding_candidates = tuple(
        candidate for result in detector_results for candidate in result.findings
    )
    relationship_candidates = tuple(
        candidate for result in detector_results for candidate in result.relationships
    )
    detector_diagnostics = tuple(
        candidate for result in detector_results for candidate in result.diagnostics
    )
    if configuration is not None:
        evidence_candidates = (
            *evidence_candidates,
            *(
                _EvidenceCandidate(
                    "maintainer-declaration",
                    "slygentify.toml",
                    item.locator,
                    "A maintainer component declaration is present.",
                    "strict bounded parse",
                    "configuration.component",
                    item.path,
                )
                for item in configuration.components
            ),
        )
    if configuration is not None:
        component_candidates = (
            *component_candidates,
            *(
                _ComponentCandidate(
                    item.path,
                    item.kind or "component",
                    (("maintainer-declaration", "slygentify.toml", item.locator, item.path),),
                    item.ecosystem or "generic",
                )
                for item in configuration.components
            ),
        )
    detector_diagnostics = (
        *detector_diagnostics,
        *view.diagnostics,
    )
    view.release_path_catalog()
    ledger = _MemoryLedger(memory_limit)
    ledger.consumed = (
        view._memory_consumed if memory_limit is None else min(view._memory_consumed, memory_limit)
    )
    evidence_by_key: dict[tuple[str, str, str | None, str], Evidence] = {}
    diagnostics_candidates = [*inspection.diagnostics, *detector_diagnostics]
    if configuration is not None and configuration.relaxed:
        diagnostics_candidates.append(
            _DiagnosticCandidate(
                "configuration.relaxed-limits",
                "slygentify.toml",
                "Configuration raises or disables one or more inspection limits.",
                False,
            )
        )
    partial = (
        inspection.partial or view.partial or any(item.partial for item in detector_diagnostics)
    )

    repository_evidence = Evidence(
        id=_id("evidence", "vcs-marker", ".git", "", "repository-root"),
        source_kind="vcs-marker",
        location=".git",
        locator=None,
        observation="Git repository marker is present.",
        verification_method="non-following metadata inspection",
    )
    evidence: list[Evidence] = [repository_evidence]
    ledger.add(_record_size(repository_evidence))
    for candidate in evidence_candidates:
        if view.checkpoint():
            break
        key = _evidence_key(candidate)
        if key in evidence_by_key:
            continue
        item = Evidence(
            id=_id(
                candidate.rule_id.replace(".", "-"),
                candidate.source_kind,
                candidate.location,
                candidate.locator or "",
                candidate.semantic_key,
            ),
            source_kind=candidate.source_kind,
            location=candidate.location,
            locator=candidate.locator,
            observation=candidate.observation,
            verification_method=candidate.verification_method,
        )
        if not ledger.add(_record_size(item)):
            diagnostics_candidates.append(
                _DiagnosticCandidate(
                    "inspection.max-memory-bytes",
                    candidate.location,
                    "Normalized evidence exceeded the memory budget.",
                    True,
                )
            )
            partial = True
            continue
        evidence_by_key[key] = item
        evidence.append(item)

    configured = {item.path: item for item in configuration.components} if configuration else {}
    merged: dict[str, tuple[str, set[str], set[str]]] = {}
    for component_candidate in component_candidates:
        if view.checkpoint():
            break
        ids = {
            evidence_by_key[key].id
            for key in component_candidate.evidence_keys
            if key in evidence_by_key
        }
        if not ids:
            diagnostics_candidates.append(
                _DiagnosticCandidate(
                    "inspection.missing-evidence",
                    component_candidate.path,
                    "Component evidence was unavailable after normalization.",
                    True,
                )
            )
            partial = True
            continue
        previous = merged.get(component_candidate.path)
        if previous is None:
            merged[component_candidate.path] = (
                component_candidate.kind,
                {component_candidate.ecosystem},
                ids,
            )
        else:
            prior_kind, prior_ecosystems, prior_ids = previous
            merged[component_candidate.path] = (
                "workspace"
                if "workspace" in {prior_kind, component_candidate.kind}
                else prior_kind,
                prior_ecosystems | {component_candidate.ecosystem},
                prior_ids | ids,
            )

    for path, declaration in configured.items():
        if view.checkpoint():
            break
        current = merged.get(path)
        if current is None:  # pragma: no cover - declaration evidence is normalized atomically
            continue
        kind, ecosystems, ids = current
        if declaration.kind is not None:  # pragma: no branch - optional declaration override
            kind = declaration.kind
        merged[path] = (kind, ecosystems, ids)
        conflicting = declaration.ecosystem is not None and any(
            item != declaration.ecosystem for item in ecosystems
        )
        if conflicting:  # pragma: no branch - conflict is independently represented
            diagnostics_candidates.append(
                _DiagnosticCandidate(
                    "configuration.component-conflict",
                    path,
                    "Configured component ecosystem conflicts with detected evidence; both were retained.",
                    False,
                    path,
                )
            )

    components: list[Component] = []
    for path, (kind, ecosystems, ids) in merged.items():
        if view.checkpoint():
            break
        components.append(
            Component(
                id=_id("component", path),
                path=path,
                ecosystem=next(iter(ecosystems)) if len(ecosystems) == 1 else "mixed",
                kind=kind,
                evidence_ids=tuple(sorted(ids)),
                ecosystems=tuple(sorted(ecosystems)),
                role=(
                    "auxiliary"
                    if _AUXILIARY_COMPONENT_PATH_PARTS & set(PurePosixPath(path).parts)
                    else "unknown"
                ),
            )
        )
    repository_id = _id("repository", ".")
    component_ids = {item.path: item.id for item in components}
    components_by_path = {item.path: item for item in components}
    relationship_data: dict[tuple[str, str, str], tuple[str, set[str]]] = {}
    for relationship_candidate in relationship_candidates:
        if view.checkpoint():
            break
        source_id = component_ids.get(relationship_candidate.source_path)
        target_id = component_ids.get(relationship_candidate.target_path)
        if source_id is None or target_id is None or source_id == target_id:
            diagnostics_candidates.append(
                _DiagnosticCandidate(
                    "composition.unresolved-relationship",
                    relationship_candidate.target_path,
                    f"A declared {relationship_candidate.kind} relationship from "
                    f"{relationship_candidate.source_path} to "
                    f"{relationship_candidate.target_path} has missing or inconsistent "
                    "component/workspace evidence. The relationship was omitted, so the scan "
                    "is partial. Next: repair the workspace declaration or declare the missing "
                    "component boundary in the root slygentify.toml.",
                    True,
                    relationship_candidate.target_path if target_id is not None else None,
                    relationship_candidate.evidence_keys,
                )
            )
            partial = True
            continue
        relationship_key = (relationship_candidate.kind, source_id, target_id)
        prior_classification, prior_evidence = relationship_data.get(
            relationship_key, (relationship_candidate.classification, set())
        )
        relationship_data[relationship_key] = (
            prior_classification,
            prior_evidence
            | {
                evidence_by_key[item].id
                for item in relationship_candidate.evidence_keys
                if item in evidence_by_key
            },
        )

    component_paths = frozenset(components_by_path)
    for target_path, target in components_by_path.items():
        if view.checkpoint():
            break
        if target_path == ".":
            continue
        source_path = _nearest_ancestor(_parent(target_path), component_paths)
        if source_path is None:
            continue
        source = components_by_path[source_path]
        relationship_data[("contains", source.id, target.id)] = (
            "inferred",
            set((*source.evidence_ids, *target.evidence_ids)),
        )

    workspace_parents: dict[str, set[str]] = {}
    for kind, source_id, target_id in relationship_data:
        if view.checkpoint():
            break
        if kind == "workspace-member":
            workspace_parents.setdefault(target_id, set()).add(source_id)
    paths_by_component_id = {item.id: item.path for item in components}
    for target_id, source_ids in sorted(workspace_parents.items()):
        if view.checkpoint():
            break
        if len(source_ids) <= 1:
            continue
        target_path = paths_by_component_id[target_id]
        source_paths = sorted(paths_by_component_id[item] for item in source_ids)
        diagnostics_candidates.append(
            _DiagnosticCandidate(
                "composition.overlapping-workspace-membership",
                target_path,
                f"Component {target_path} belongs to multiple workspace roots: "
                f"{', '.join(source_paths)}. All relationships were retained and no single "
                "workspace owner was selected. Next: narrow or exclude the overlapping "
                "workspace declarations if the overlap is unintended.",
                False,
                target_path,
            )
        )

    relationships: list[ComponentRelationship] = []
    for (kind, source_id, target_id), (classification, evidence_ids) in sorted(
        relationship_data.items()
    ):
        if view.checkpoint():
            break
        relationship = ComponentRelationship(
            id=_id("relationship", kind, source_id, target_id),
            kind=kind,
            source_id=source_id,
            target_id=target_id,
            classification=cast(Any, classification),
            evidence_ids=tuple(sorted(evidence_ids)),
        )
        if ledger.add(_record_size(relationship)):
            relationships.append(relationship)
        else:
            diagnostics_candidates.append(
                _DiagnosticCandidate(
                    "inspection.max-memory-bytes",
                    paths_by_component_id[target_id],
                    "Normalized component relationships exceeded the memory budget.",
                    True,
                    paths_by_component_id[target_id],
                )
            )
            partial = True
    findings_by_id: dict[str, Finding] = {}
    for component in components:
        if view.checkpoint():
            break
        if component.role != "auxiliary":
            continue
        finding_item = Finding(
            id=_id(
                "finding",
                "composition.auxiliary-component",
                "inferred",
                component.id,
                "Path convention indicates an auxiliary component.",
                *component.evidence_ids,
            ),
            code="composition.auxiliary-component",
            classification="inferred",
            subject_id=component.id,
            summary="Path convention indicates an auxiliary component.",
            evidence_ids=component.evidence_ids,
        )
        if ledger.add(_record_size(finding_item)):
            findings_by_id[finding_item.id] = finding_item
        else:
            diagnostics_candidates.append(
                _DiagnosticCandidate(
                    "inspection.max-memory-bytes",
                    component.path,
                    "Normalized auxiliary component findings exceeded the memory budget.",
                    True,
                    component.path,
                )
            )
            partial = True
    for finding_candidate in finding_candidates:
        if view.checkpoint():
            break
        finding_evidence_ids = tuple(
            sorted(
                evidence_by_key[key].id
                for key in finding_candidate.evidence_keys
                if key in evidence_by_key
            )
        )
        finding_subject_id = component_ids.get(finding_candidate.subject_path or "", repository_id)
        identifier = _id(
            "finding",
            finding_candidate.code,
            finding_candidate.classification,
            finding_subject_id,
            finding_candidate.summary,
            *finding_evidence_ids,
        )
        finding_item = Finding(
            id=identifier,
            code=finding_candidate.code,
            classification=cast(Any, finding_candidate.classification),
            subject_id=finding_subject_id,
            summary=finding_candidate.summary,
            evidence_ids=finding_evidence_ids,
        )
        if ledger.add(_record_size(finding_item)):
            findings_by_id[finding_item.id] = finding_item
        else:
            diagnostics_candidates.append(
                _DiagnosticCandidate(
                    "inspection.max-memory-bytes",
                    finding_candidate.subject_path or ".",
                    "Normalized findings exceeded the memory budget.",
                    True,
                )
            )
            partial = True
    findings = list(findings_by_id.values())
    if not components:
        boundary_summary = (
            "Component-specific workflows and architecture could not be established because no "
            "supported component boundary was found."
        )
        findings.append(
            Finding(
                id=_id("finding", "core.component-boundary-unknown", repository_id),
                code="core.component-boundary-unknown",
                classification="unknown",
                subject_id=repository_id,
                summary=boundary_summary,
                evidence_ids=(),
            )
        )
        diagnostics_candidates.append(
            _DiagnosticCandidate(
                "core.component-boundary-unknown",
                ".",
                problem="No supported component boundary was found",
                effect="Component-specific workflows and architecture remain unknown",
                recovery=(
                    "add a supported manifest, declare the component in [[scan.components]] in "
                    "the root slygentify.toml, or retain this unknown when the repository "
                    "intentionally has no supported component"
                ),
            )
        )

    diagnostics_by_id: dict[str, Diagnostic] = {}
    cause_candidates: list[tuple[_DiagnosticCandidate, tuple[str, ...]]] = []
    for diagnostic_candidate in diagnostics_candidates:
        if view.checkpoint():
            break
        diagnostic_evidence_ids = tuple(
            sorted(
                evidence_by_key[key].id
                for key in diagnostic_candidate.evidence_keys
                if key in evidence_by_key
            )
        )
        diagnostic_subject_id = component_ids.get(diagnostic_candidate.subject_path or "")
        identifier = _id(
            "diagnostic",
            diagnostic_candidate.code,
            diagnostic_candidate.location,
            diagnostic_subject_id or "",
            diagnostic_candidate.message,
            *diagnostic_evidence_ids,
        )
        diagnostics_by_id[identifier] = Diagnostic(
            id=identifier,
            code=diagnostic_candidate.code,
            subject_id=diagnostic_subject_id,
            location=diagnostic_candidate.location,
            message=diagnostic_candidate.message,
            evidence_ids=diagnostic_evidence_ids,
        )
        if diagnostic_candidate.partial:
            cause_candidates.append((diagnostic_candidate, diagnostic_evidence_ids))
    diagnostics = list(diagnostics_by_id.values())
    partial = partial or view.partial
    result = ScanResult(
        schema_version=1,
        producer_version=__version__,
        completion="partial" if partial else "complete",
        repository=Repository(
            id=repository_id, root=".", kind="git", evidence_ids=(repository_evidence.id,)
        ),
        components=tuple(sorted(components, key=lambda item: (item.id, item.path))),
        evidence=tuple(sorted(evidence, key=lambda item: (item.id, item.location))),
        findings=tuple(sorted(findings, key=lambda item: (item.code, item.subject_id, item.id))),
        diagnostics=tuple(
            sorted(
                diagnostics,
                key=lambda item: (item.code, item.subject_id or item.location or "", item.id),
            )
        ),
        skipped_scopes=tuple(
            sorted(
                set((*inspection.skipped, *view.skipped)),
                key=lambda item: (item.scope, item.reason),
            )
        ),
        relationships=tuple(
            sorted(
                relationships,
                key=lambda item: (item.kind, item.source_id, item.target_id, item.id),
            )
        ),
    )
    if content_fingerprints is not None:
        content_fingerprints.update(view.content_fingerprints())
    if partial_causes is not None:
        boundaries = tuple(
            sorted(
                set((*inspection.partial_skipped, *view.partial_skipped)),
                key=lambda item: (item.scope, item.reason),
            )
        )
        consumed_boundaries: set[SkippedScope] = set()
        causes: list[_PartialCause] = []
        for diagnostic_candidate, diagnostic_evidence_ids in cause_candidates:
            recovery: str | None
            boundary = _matching_boundary(diagnostic_candidate, boundaries)
            if boundary is not None:
                if boundary in consumed_boundaries:
                    continue
                consumed_boundaries.add(boundary)
                boundary_problem, boundary_effect, boundary_recovery = _boundary_cause(boundary)
                problem = (
                    boundary_problem
                    if boundary.reason in _RESOURCE_REASONS
                    else diagnostic_candidate.problem
                )
                effect = boundary_effect
                recovery = (
                    boundary_recovery
                    if boundary.reason in _RESOURCE_REASONS
                    else diagnostic_candidate.recovery or boundary_recovery
                )
                source_code = (
                    f"inspection.boundary.{boundary.reason}"
                    if boundary.reason in _RESOURCE_REASONS
                    else diagnostic_candidate.code
                )
            else:
                problem = diagnostic_candidate.problem
                effect = diagnostic_candidate.effect
                recovery = diagnostic_candidate.recovery
                source_code = diagnostic_candidate.code
            causes.append(
                _PartialCause(
                    source_code=source_code,
                    location=diagnostic_candidate.location,
                    subject_path=diagnostic_candidate.subject_path,
                    problem=problem,
                    effect=effect,
                    recovery=recovery,
                    evidence_ids=diagnostic_evidence_ids,
                    boundary=boundary,
                )
            )
        for boundary in boundaries:
            if boundary in consumed_boundaries:
                continue
            problem, effect, recovery = _boundary_cause(boundary)
            causes.append(
                _PartialCause(
                    source_code=f"inspection.boundary.{boundary.reason}",
                    location=boundary.scope,
                    subject_path=None,
                    problem=problem,
                    effect=effect,
                    recovery=recovery,
                    evidence_ids=(),
                    boundary=boundary,
                )
            )
        partial_causes.extend(
            sorted(
                set(causes),
                key=lambda item: (
                    item.source_code,
                    item.subject_path or "",
                    item.location,
                    item.boundary.reason if item.boundary is not None else "",
                ),
            )
        )
    return result
