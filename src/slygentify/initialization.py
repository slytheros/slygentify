"""Planning and safe application of root AGENTS.md initialization."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from slygentify._diagnostics import DiagnosticDetail, compose_message
from slygentify._generation import generate_agents_document
from slygentify._managed_section import (
    SECTION_BEGIN,
    SECTION_END,
    ManagedSectionError,
    append_managed_section,
    extract_managed_section,
    render_managed_section,
    replace_managed_section,
    section_digest,
)
from slygentify._provenance import (
    Artifact,
    StateError,
    apply_state_write,
    dump_state_json,
    load_state_json,
    plan_state_write,
    read_state_bytes,
    state_from_scan,
)
from slygentify._repository import (
    AGENTS_FILENAME,
    RepositoryPathError,
)
from slygentify._repository import (
    find_git_root as _find_git_root,
)
from slygentify.models import DiagnosticDisposition
from slygentify.traceability import implements

OwnershipState = Literal[
    "new",
    "clean-managed",
    "recoverable-state",
    "unmanaged",
    "human-edited",
    "missing-managed-artifact",
    "invalid-state",
    "unsafe-entry",
]
ArtifactAction = Literal["create", "replace", "no_change"]
StateRecovery = Literal["none", "schema-upgrade", "state-rebuild"]


class InitializationError(Exception):
    """A safe, actionable initialization failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        changed_locations: tuple[str, ...] = (),
        recovery: str = "Run slygentify init --dry-run to review the current state.",
        target: str = ".",
        category: str | None = None,
        effect: str = "Initialization did not complete and no additional repository files were changed.",
        safety_rationale: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.changed_locations = changed_locations
        self.recovery = recovery
        self.diagnostic = DiagnosticDetail(
            code,
            target,
            message,
            effect,
            category,
            recovery,
            safety_rationale,
            disposition="problem",
        )


@implements("REQ040", "REQ046")
@dataclass(frozen=True, slots=True)
class InitializationDiagnostic:
    """A stable initialization diagnostic with an explicit disposition."""

    code: str
    message: str
    recovery: str
    target: str = "."
    category: str | None = None
    problem: str | None = None
    effect: str | None = None
    safety_rationale: str | None = None
    disposition: DiagnosticDisposition = "problem"

    def __post_init__(self) -> None:
        if self.disposition not in {"problem", "limitation", "notice"}:
            raise ValueError(f"unsupported diagnostic disposition: {self.disposition}")


@dataclass(frozen=True, slots=True)
class InitializationPlan:
    """Exact, reviewable bytes and ownership state for one initialization."""

    repository_root: Path
    ownership: OwnershipState
    can_apply: bool
    replace_requested: bool
    adopt_requested: bool
    agents_action: ArtifactAction
    state_action: ArtifactAction
    agents_markdown: str
    managed_section: str | None
    state_json: bytes
    diagnostics: tuple[InitializationDiagnostic, ...]
    warnings: tuple[str, ...] = ()
    state_recovery: StateRecovery = "none"
    agents_source_sha256: str | None = None
    state_source_sha256: str | None = None

    @property
    def agents_bytes(self) -> bytes:
        """Return the exact planned AGENTS.md bytes, including opaque surroundings."""
        return self.agents_markdown.encode("utf-8", errors="surrogateescape")


@dataclass(frozen=True, slots=True)
class InitializationResult:
    """The observable result of applying one initialization plan."""

    repository_root: Path
    ownership: OwnershipState
    agents_action: ArtifactAction
    state_action: ArtifactAction
    changed_locations: tuple[str, ...]
    state_recovery: StateRecovery = "none"


@implements("REQ001")
def find_git_root(path: Path) -> Path:
    """Return the nearest Git root containing *path* without invoking Git."""
    try:
        return _find_git_root(path)
    except RepositoryPathError as error:
        code = (
            "initialization.no-repository"
            if str(error).startswith("no Git")
            else "initialization.path"
        )
        raise InitializationError(code, str(error)) from None


def _regular(path: Path) -> bool:
    metadata = path.lstat()
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _action(path: Path, data: bytes) -> ArtifactAction:
    if not os.path.lexists(path):
        return "create"
    if not _regular(path):
        raise OSError("unsafe entry")
    return "no_change" if path.read_bytes() == data else "replace"


def _diagnostic(
    code: str,
    target: str,
    problem: str,
    effect: str,
    recovery: str,
    *,
    category: str | None = None,
    safety_rationale: str | None = None,
    disposition: DiagnosticDisposition,
) -> InitializationDiagnostic:
    return InitializationDiagnostic(
        code,
        compose_message(problem, effect, recovery),
        recovery,
        target,
        category,
        problem,
        effect,
        safety_rationale,
        disposition,
    )


def _can_replace(ownership: OwnershipState, requested: bool) -> bool:
    return requested and ownership in {"unmanaged", "human-edited", "missing-managed-artifact"}


@implements(
    "REQ002", "REQ003", "REQ004", "REQ005", "REQ038", "REQ039", "REQ044", "REQ053", "REQ054"
)
def plan_initialization(
    path: str | os.PathLike[str] = ".", *, replace: bool = False, adopt: bool = False
) -> InitializationPlan:
    """Scan, render, and classify a reviewable initialization operation without writing."""
    from slygentify._scan import _scan_foundation, _ScanFoundationError

    try:
        execution = _scan_foundation(Path(path))
    except (_ScanFoundationError, OSError, TypeError, ValueError) as error:
        raise InitializationError("initialization.scan", str(error)) from None
    root = execution.root
    agents = root / AGENTS_FILENAME
    state_target = root / ".slygentify" / "state.json"
    guidance = generate_agents_document(
        execution.result,
        max_bytes=execution.configuration.max_agents_bytes,
        max_component_entries=execution.configuration.max_component_entries,
    )
    generated_data = guidance.markdown.encode("utf-8")
    managed_section = render_managed_section(guidance.markdown)
    preliminary_state = state_from_scan(
        execution.result,
        execution.configuration,
        execution.content_fingerprints,
    )
    input_ids = {item.id for item in preliminary_state.inputs}
    fallback_artifact = Artifact(
        AGENTS_FILENAME,
        hashlib.sha256(generated_data).hexdigest(),
        tuple(item for item in guidance.evidence_ids if item in input_ids),
    )
    state_data = dump_state_json(
        state_from_scan(
            execution.result,
            execution.configuration,
            execution.content_fingerprints,
            artifacts=(fallback_artifact,),
        )
    )
    diagnostics: list[InitializationDiagnostic] = []
    ownership: OwnershipState = "invalid-state"
    agents_data = generated_data
    output_section: str | None = None
    existing_state = None
    raw_state: bytes | None = None
    state_error: StateError | None = None
    recorded: Artifact | None = None
    state_recovery: StateRecovery = "none"
    state_write_recovery = False
    blocked_state = False
    invalid_state_has_markers = False
    agents_source_sha256: str | None = None
    state_source_sha256: str | None = None
    unsafe_target = AGENTS_FILENAME
    try:
        if adopt and replace:
            raise InitializationError(
                "initialization.options", "--adopt cannot be combined with --replace"
            )
        if os.path.lexists(agents) and not _regular(agents):
            raise OSError("AGENTS.md is unsafe")
        unsafe_target = ".slygentify"
        parent = state_target.parent
        if os.path.lexists(parent) and (not parent.is_dir() or parent.is_symlink()):
            raise OSError("provenance directory is unsafe")
        unsafe_target = ".slygentify/state.json"
        if os.path.lexists(state_target):
            try:
                raw_state = read_state_bytes(state_target)
                existing_state = load_state_json(raw_state)
            except StateError as error:
                state_error = error

        if state_error is not None:
            protected_categories = {
                "state.too-large",
                "state.unreadable",
                "state.unsafe-entry",
                "state.unsupported-schema",
            }
            if state_error.category in protected_categories:
                blocked_state = True
                ownership = (
                    "unsafe-entry"
                    if state_error.category == "state.unsafe-entry"
                    else "invalid-state"
                )
            elif not os.path.lexists(agents):
                ownership = "recoverable-state"
                state_recovery = "state-rebuild"
                state_write_recovery = True
            else:
                unsafe_target = AGENTS_FILENAME
                current = agents.read_bytes()
                try:
                    current_section = extract_managed_section(current)
                except ManagedSectionError:
                    no_markers = (
                        current.count(SECTION_BEGIN) == 0 and current.count(SECTION_END) == 0
                    )
                    if current == generated_data:
                        agents_data = current
                        ownership = "recoverable-state"
                        state_recovery = "state-rebuild"
                        state_write_recovery = True
                    elif adopt and no_markers:
                        agents_data = append_managed_section(current, managed_section)
                        output_section = managed_section.decode("utf-8")
                        ownership = "unmanaged"
                        state_recovery = "state-rebuild"
                        state_write_recovery = True
                    elif replace:
                        ownership = "unmanaged" if no_markers else "missing-managed-artifact"
                        state_recovery = "state-rebuild"
                        state_write_recovery = True
                    else:
                        blocked_state = True
                        invalid_state_has_markers = not no_markers
                        ownership = "invalid-state"
                else:
                    agents_data = replace_managed_section(
                        current, section_digest(current_section), managed_section
                    )
                    output_section = managed_section.decode("utf-8")
                    ownership = "recoverable-state"
                    state_recovery = "state-rebuild"
                    state_write_recovery = True
        elif existing_state is None and not os.path.lexists(agents):
            ownership = "new"
        elif existing_state is None:
            unsafe_target = AGENTS_FILENAME
            current = agents.read_bytes()
            if current == generated_data:
                agents_data = current
                ownership = "recoverable-state"
                state_recovery = "state-rebuild"
            elif adopt:
                agents_data = append_managed_section(current, managed_section)
                output_section = managed_section.decode("utf-8")
                ownership = "unmanaged"
            else:
                ownership = "unmanaged"
        else:
            recorded = next(
                (item for item in existing_state.artifacts if item.location == AGENTS_FILENAME),
                None,
            )
            if recorded is None:
                ownership = "unmanaged"
            elif not os.path.lexists(agents):
                ownership = "missing-managed-artifact"
            else:
                unsafe_target = AGENTS_FILENAME
                current = agents.read_bytes()
                if recorded.ownership == "section":
                    try:
                        current_section = extract_managed_section(current)
                    except ManagedSectionError:
                        ownership = "missing-managed-artifact"
                    else:
                        if section_digest(current_section) == recorded.sha256:
                            agents_data = replace_managed_section(
                                current, recorded.sha256, managed_section
                            )
                            output_section = managed_section.decode("utf-8")
                            ownership = "clean-managed"
                        elif current_section == managed_section:
                            agents_data = current
                            output_section = managed_section.decode("utf-8")
                            ownership = "recoverable-state"
                        else:
                            ownership = "human-edited"
                else:
                    digest = hashlib.sha256(current).hexdigest()
                    if digest == recorded.sha256:
                        ownership = "clean-managed"
                    elif current == generated_data:
                        agents_data = current
                        ownership = "recoverable-state"
                    else:
                        ownership = "human-edited"

        ownership_mode: Literal["document", "section"] = "section" if output_section else "document"
        artifact = Artifact(
            AGENTS_FILENAME,
            section_digest(managed_section)
            if ownership_mode == "section"
            else hashlib.sha256(agents_data).hexdigest(),
            tuple(item for item in guidance.evidence_ids if item in input_ids),
            ownership_mode,
        )
        state = state_from_scan(
            execution.result,
            execution.configuration,
            execution.content_fingerprints,
            artifacts=(artifact,),
        )
        state_data = dump_state_json(state)
        unsafe_target = AGENTS_FILENAME
        agents_action = _action(agents, agents_data)
        unsafe_target = ".slygentify/state.json"
        state_action = (
            "replace"
            if blocked_state and os.path.lexists(state_target)
            else "create"
            if blocked_state
            else plan_state_write(root, state, replace_invalid=state_write_recovery).action
        )
        if existing_state is not None and existing_state.schema_version == 1:
            state_recovery = "schema-upgrade"
        unsafe_target = AGENTS_FILENAME
        agents_source_sha256 = (
            hashlib.sha256(agents.read_bytes()).hexdigest()
            if os.path.lexists(agents) and _regular(agents)
            else None
        )
        state_source_sha256 = (
            hashlib.sha256(raw_state).hexdigest() if raw_state is not None else None
        )
    except InitializationError:
        raise
    except StateError as error:
        raise InitializationError(
            "initialization.concurrent-change",
            "Provenance state changed during initialization planning; no files were changed.",
            target=".slygentify/state.json",
            category=error.category,
        ) from None
    except OSError:
        blocked_state = True
        ownership = "unsafe-entry"
        agents_action = "replace" if os.path.lexists(agents) else "create"
        state_action = "replace" if os.path.lexists(state_target) else "create"
        state_error = StateError(
            category=(
                "artifact.unsafe-entry"
                if unsafe_target == AGENTS_FILENAME
                else "state.unsafe-entry"
            )
        )

    if blocked_state and state_error is not None:
        category = state_error.category
        if category == "state.unsupported-schema":
            recovery = (
                "Install a reviewed Slygentify build that supports the newer state schema, "
                "then rerun slygentify init . --dry-run. --replace does not authorize a downgrade."
            )
            rationale = (
                "An older binary cannot safely interpret or replace provenance from a newer schema."
            )
        elif category == "state.too-large":
            recovery = (
                "Move .slygentify/state.json to a new collision-safe backup name, then rerun "
                "slygentify init . --dry-run; oversized state is not read or replaced automatically."
            )
            rationale = (
                "State outside the fixed read bound cannot receive digest-based revalidation."
            )
        elif category == "state.unreadable":
            recovery = (
                "Make .slygentify/state.json readable or move it to a new collision-safe backup "
                "name, then rerun slygentify init . --dry-run."
            )
            rationale = "Unreadable state cannot receive digest-based concurrency revalidation."
        elif category == "artifact.unsafe-entry":
            recovery = (
                "Make AGENTS.md readable if it is a regular file, or move or replace the "
                "unsafe AGENTS.md entry manually, then rerun slygentify init . --dry-run."
            )
            rationale = "Following or replacing an unsafe AGENTS.md entry could escape repository containment or discard human guidance."
        elif category == "state.unsafe-entry":
            recovery = (
                "Replace the unsafe entry manually with a safe regular file or move it to a new "
                "collision-safe backup name, then rerun slygentify init . --dry-run."
            )
            rationale = "Following or replacing a symbolic link or non-regular target could escape repository containment."
        else:
            recovery = (
                "Review slygentify init . --replace --dry-run, then use --replace to authorize "
                "full-document and state replacement."
                if invalid_state_has_markers
                else "Review slygentify init . --adopt --dry-run to preserve existing guidance, "
                "or use --replace --dry-run only if the whole document may be discarded."
            )
            rationale = "Invalid state and the current artifact do not establish a safe automatic ownership boundary."
        diagnostic_target = (
            unsafe_target if ownership == "unsafe-entry" else ".slygentify/state.json"
        )
        problem = (
            "AGENTS.md could not be read as a safe regular file."
            if category == "artifact.unsafe-entry"
            else "The generated ownership and provenance record for AGENTS.md could not be safely recovered."
        )
        diagnostics.append(
            _diagnostic(
                "initialization.invalid-state"
                if ownership != "unsafe-entry"
                else "initialization.unsafe-entry",
                diagnostic_target,
                problem,
                "Initialization preserved AGENTS.md and provenance state, and no files were changed.",
                recovery,
                category=category,
                safety_rationale=rationale,
                disposition="problem",
            )
        )

    can_adopt = (
        adopt
        and ownership == "unmanaged"
        and (existing_state is None or state_recovery == "state-rebuild")
    )
    can_apply = (
        ownership in {"new", "clean-managed", "recoverable-state"}
        or _can_replace(ownership, replace)
        or can_adopt
    ) and not blocked_state
    if not can_apply:
        state_recovery = "none"
    if not can_apply and not diagnostics:
        diagnostics.append(
            _diagnostic(
                f"initialization.{ownership}",
                AGENTS_FILENAME,
                (
                    "--adopt requires an existing unmanaged AGENTS.md without provenance state."
                    if adopt
                    else f"Ordinary initialization refuses {ownership.replace('-', ' ')} AGENTS.md content."
                ),
                "Initialization preserved the existing AGENTS.md and did not write provenance state.",
                "Review the dry-run and use --replace only after preserving any content you need.",
                safety_rationale="Slygentify does not replace content whose ownership or human edits it cannot validate without explicit authorization.",
                disposition=("notice" if ownership in {"unmanaged", "human-edited"} else "problem"),
            )
        )
    return InitializationPlan(
        root,
        ownership,
        can_apply,
        replace,
        adopt,
        agents_action,
        state_action,
        agents_data.decode("utf-8", errors="surrogateescape"),
        output_section,
        state_data,
        tuple(diagnostics),
        (("slygentify.toml raises or disables an AGENTS.md byte or component-entry limit."),)
        if execution.configuration.init_relaxed
        else (),
        state_recovery,
        agents_source_sha256,
        state_source_sha256,
    )


def _write_agents(
    root: Path, data: bytes, action: ArtifactAction, expected_sha256: str | None
) -> bool:
    target = root / AGENTS_FILENAME
    if os.path.lexists(target):
        if not _regular(target):
            raise InitializationError("initialization.concurrent-change", "AGENTS.md became unsafe")
        if expected_sha256 != hashlib.sha256(target.read_bytes()).hexdigest():
            raise InitializationError(
                "initialization.concurrent-change", "AGENTS.md changed concurrently"
            )
    elif expected_sha256 is not None or action in {"replace", "no_change"}:
        raise InitializationError("initialization.concurrent-change", "AGENTS.md was removed")
    if action == "no_change":
        return False
    descriptor, temporary_name = tempfile.mkstemp(prefix=".agents-", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise InitializationError(
            "initialization.write-failed", "Unable to write AGENTS.md"
        ) from error
    return True


@implements("REQ003", "REQ005", "REQ039", "REQ054")
def apply_initialization(plan: InitializationPlan) -> InitializationResult:
    """Revalidate and atomically apply one applicable initialization plan."""
    if not isinstance(plan, InitializationPlan):
        raise InitializationError("initialization.plan", "initialization plan is invalid")
    current = plan_initialization(
        plan.repository_root, replace=plan.replace_requested, adopt=plan.adopt_requested
    )
    if (
        not current.can_apply
        or current.agents_markdown != plan.agents_markdown
        or current.state_json != plan.state_json
        or current.agents_action != plan.agents_action
        or current.state_action != plan.state_action
        or current.state_recovery != plan.state_recovery
        or current.agents_source_sha256 != plan.agents_source_sha256
        or current.state_source_sha256 != plan.state_source_sha256
    ):
        raise InitializationError(
            "initialization.concurrent-change",
            "Repository state changed after planning; no files were changed.",
        )
    changed: list[str] = []
    try:
        state = load_state_json(current.state_json)
        state_plan = plan_state_write(
            current.repository_root,
            state,
            replace_invalid=current.state_recovery == "state-rebuild",
        )
    except (OSError, StateError) as error:
        raise InitializationError(
            "initialization.concurrent-change", "Repository state changed after planning."
        ) from error
    if (
        state_plan.action != current.state_action
        or state_plan.sha256 != current.state_source_sha256
    ):
        raise InitializationError(
            "initialization.concurrent-change", "Provenance state changed after planning."
        )
    if _write_agents(
        current.repository_root,
        current.agents_bytes,
        current.agents_action,
        current.agents_source_sha256,
    ):
        changed.append(AGENTS_FILENAME)
    try:
        if apply_state_write(state_plan):
            changed.append(".slygentify/state.json")
    except (StateError, OSError) as error:
        if changed:
            raise InitializationError(
                "initialization.partial-write",
                "AGENTS.md changed but provenance state did not.",
                changed_locations=tuple(changed),
                recovery="Run slygentify init --dry-run again before attempting recovery.",
            ) from error
        raise InitializationError(
            "initialization.write-failed", "Unable to write provenance state"
        ) from error
    return InitializationResult(
        current.repository_root,
        current.ownership,
        current.agents_action,
        current.state_action,
        tuple(changed),
        current.state_recovery,
    )
