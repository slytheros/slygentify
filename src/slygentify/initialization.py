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
    state_from_scan,
)
from slygentify._repository import (
    AGENTS_FILENAME,
    RepositoryPathError,
)
from slygentify._repository import (
    find_git_root as _find_git_root,
)
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
            code, target, message, effect, category, recovery, safety_rationale
        )


@dataclass(frozen=True, slots=True)
class InitializationDiagnostic:
    """A stable, actionable initialization diagnostic."""

    code: str
    message: str
    recovery: str
    target: str = "."
    category: str | None = None
    problem: str | None = None
    effect: str | None = None
    safety_rationale: str | None = None


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


@dataclass(frozen=True, slots=True)
class InitializationResult:
    """The observable result of applying one initialization plan."""

    repository_root: Path
    ownership: OwnershipState
    agents_action: ArtifactAction
    state_action: ArtifactAction
    changed_locations: tuple[str, ...]


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
    )


def _can_replace(ownership: OwnershipState, requested: bool) -> bool:
    return requested and ownership in {"unmanaged", "human-edited", "missing-managed-artifact"}


@implements("REQ002", "REQ003", "REQ004", "REQ005", "REQ038", "REQ039", "REQ044", "REQ053")
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
    ownership: OwnershipState
    agents_data = generated_data
    output_section: str | None = None
    existing_state = None
    recorded: Artifact | None = None
    try:
        if os.path.lexists(agents) and not _regular(agents):
            raise OSError("AGENTS.md is unsafe")
        parent = state_target.parent
        if os.path.lexists(parent) and (not parent.is_dir() or parent.is_symlink()):
            raise OSError("provenance directory is unsafe")
        if os.path.lexists(state_target):
            if not _regular(state_target):
                raise OSError("provenance state is unsafe")
            existing_state = load_state_json(state_target.read_bytes())
        if adopt and replace:
            raise InitializationError(
                "initialization.options", "--adopt cannot be combined with --replace"
            )
        if existing_state is None and not os.path.lexists(agents):
            ownership = "new"
        elif existing_state is None:
            current = agents.read_bytes()
            if current == generated_data:
                ownership = "recoverable-state"
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
        agents_action = _action(agents, agents_data)
        state_action = plan_state_write(root, state).action
    except InitializationError:
        raise
    except StateError as error:
        ownership = "invalid-state"
        agents_action = "replace" if os.path.lexists(agents) else "create"
        state_action = "replace" if os.path.lexists(state_target) else "create"
        diagnostics.append(
            _diagnostic(
                "initialization.invalid-state",
                ".slygentify/state.json",
                "The generated ownership and provenance record for AGENTS.md could not be validated.",
                "Initialization did not trust, replace, or write the provenance state, and no files were changed.",
                "Upgrade to the latest reviewed Slygentify build and rerun slygentify init . --dry-run. If it still fails, rename the state file to a new non-existing backup name, rerun the dry-run, and apply only a recoverable or otherwise safe plan.",
                category=error.category,
                safety_rationale="Slygentify cannot establish ownership of AGENTS.md from invalid state, so automatic replacement could overwrite user-managed guidance.",
            )
        )
    except OSError:
        ownership = "unsafe-entry"
        agents_action = "replace" if os.path.lexists(agents) else "create"
        state_action = "replace" if os.path.lexists(state_target) else "create"
        diagnostics.append(
            _diagnostic(
                "initialization.unsafe-entry",
                ".slygentify/state.json",
                "An AGENTS.md artifact or provenance target is an unsafe filesystem entry.",
                "Initialization did not follow, replace, or write the unsafe entry, and no files were changed.",
                "Inspect the entry manually; symbolic links and non-regular targets are never replaced.",
                category="state.unsafe-entry",
                safety_rationale="Following or replacing a symbolic link or non-regular target could escape repository containment or overwrite an unintended file.",
            )
        )
    can_adopt = adopt and existing_state is None and ownership == "unmanaged"
    can_apply = (
        ownership in {"new", "clean-managed", "recoverable-state"}
        or _can_replace(ownership, replace)
        or can_adopt
    )
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
        agents_data.decode("utf-8"),
        output_section,
        state_data,
        tuple(diagnostics),
        (("slygentify.toml raises or disables an AGENTS.md byte or component-entry limit."),)
        if execution.configuration.init_relaxed
        else (),
    )


def _write_agents(
    root: Path, data: bytes, action: ArtifactAction, expected_sha256: str | None
) -> bool:
    target = root / AGENTS_FILENAME
    if action == "no_change":
        return False
    if os.path.lexists(target):
        if not _regular(target):
            raise InitializationError("initialization.concurrent-change", "AGENTS.md became unsafe")
        if expected_sha256 != hashlib.sha256(target.read_bytes()).hexdigest():
            raise InitializationError(
                "initialization.concurrent-change", "AGENTS.md changed concurrently"
            )
    elif action == "replace":
        raise InitializationError("initialization.concurrent-change", "AGENTS.md was removed")
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


@implements("REQ003", "REQ005", "REQ039")
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
    ):
        raise InitializationError(
            "initialization.concurrent-change",
            "Repository state changed after planning; no files were changed.",
        )
    changed: list[str] = []
    try:
        agents_target = current.repository_root / AGENTS_FILENAME
        expected_agents = (
            None
            if not os.path.lexists(agents_target)
            else hashlib.sha256(agents_target.read_bytes()).hexdigest()
        )
        state = load_state_json(current.state_json)
        state_plan = plan_state_write(current.repository_root, state)
    except (OSError, StateError) as error:
        raise InitializationError(
            "initialization.concurrent-change", "Repository state changed after planning."
        ) from error
    if state_plan.action != current.state_action:
        raise InitializationError(
            "initialization.concurrent-change", "Provenance state changed after planning."
        )
    if _write_agents(
        current.repository_root,
        current.agents_markdown.encode("utf-8"),
        current.agents_action,
        expected_agents,
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
    )
