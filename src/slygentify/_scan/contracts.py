"""Private candidate contract shared by built-in scan detectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from slygentify._diagnostics import compose_message
from slygentify.models import SkippedScope
from slygentify.traceability import implements

EvidenceKey = tuple[str, str, str | None, str]
_UNSET = object()


@dataclass(frozen=True, slots=True)
class PathCandidate:
    """One safely catalogued repository path and its reusable lexical metadata."""

    path: str
    parent: str
    name: str


class RepositoryView(Protocol):
    """Bounded catalog and read capability exposed to deterministic detectors."""

    def paths(self) -> tuple[str, ...]: ...

    def path_candidates(self) -> tuple[PathCandidate, ...]: ...

    def direct_children(self, parent: str) -> tuple[PathCandidate, ...]: ...

    def checkpoint(self) -> bool: ...

    def read_bytes(self, path: str) -> bytes | None: ...


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    source_kind: str
    location: str
    locator: str | None
    observation: str
    verification_method: str | None
    rule_id: str
    semantic_key: str


@dataclass(frozen=True, slots=True)
class ComponentCandidate:
    path: str
    kind: str
    evidence_keys: tuple[EvidenceKey, ...]
    ecosystem: str = "generic"


@dataclass(frozen=True, slots=True)
class RelationshipCandidate:
    kind: str
    source_path: str
    target_path: str
    classification: str
    evidence_keys: tuple[EvidenceKey, ...]


@dataclass(frozen=True, slots=True)
class FindingCandidate:
    code: str
    classification: str
    subject_path: str | None
    summary: str
    evidence_keys: tuple[EvidenceKey, ...]


@dataclass(frozen=True, slots=True)
class DetectionContext:
    """Previously detected context available to the next ordered detector."""

    generic_component_paths: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Named private candidate collections emitted by one detector."""

    evidence: tuple[EvidenceCandidate, ...] = ()
    components: tuple[ComponentCandidate, ...] = ()
    findings: tuple[FindingCandidate, ...] = ()
    diagnostics: tuple[DiagnosticCandidate, ...] = ()
    relationships: tuple[RelationshipCandidate, ...] = ()


class Detector(Protocol):
    """One deterministic built-in detector in the scan pipeline."""

    def __call__(self, view: RepositoryView, context: DetectionContext) -> DetectionResult: ...


@dataclass(frozen=True, slots=True, init=False)
class DiagnosticCandidate:
    """Structured private input for one public diagnostic message."""

    code: str
    location: str
    problem: str
    effect: str
    partial: bool
    subject_path: str | None = None
    evidence_keys: tuple[EvidenceKey, ...] = ()
    recovery: str | None = None
    category: str | None = None
    safety_rationale: str | None = None

    def __init__(
        self,
        code: str,
        location: str,
        message: str | None = None,
        partial: bool = False,
        subject_path: str | None = None,
        evidence_keys: tuple[EvidenceKey, ...] = (),
        *,
        problem: str | None = None,
        effect: str | None = None,
        recovery: str | None | object = _UNSET,
        category: str | None = None,
        safety_rationale: str | None = None,
    ) -> None:
        """Accept explicit diagnostic structure, with legacy message migration support."""

        explicit = problem is not None or effect is not None or recovery is not _UNSET
        if explicit:
            if message is not None or problem is None or effect is None or recovery is _UNSET:
                raise TypeError(
                    "explicit diagnostic candidates require problem, effect, and recovery only"
                )
            assert recovery is None or isinstance(recovery, str)
            resolved_problem = problem
            resolved_effect = effect
            resolved_recovery = recovery
        else:
            if message is None:
                raise TypeError("a diagnostic message or explicit structure is required")
            statement, marker, parsed_recovery = message.partition(" Next: ")
            resolved_problem, separator, resolved_effect = statement.partition(". ")
            if not separator:
                resolved_problem = statement
                resolved_effect = (
                    "The affected evidence was omitted, so the scan is partial"
                    if partial
                    else "The scan retained the available evidence without treating this item as verified"
                )
            resolved_recovery = (
                parsed_recovery if marker else _default_recovery(code, location, partial=partial)
            )
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "problem", resolved_problem)
        object.__setattr__(self, "effect", resolved_effect)
        object.__setattr__(self, "partial", partial)
        object.__setattr__(self, "subject_path", subject_path)
        object.__setattr__(self, "evidence_keys", evidence_keys)
        object.__setattr__(self, "recovery", resolved_recovery)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "safety_rationale", safety_rationale)

    @property
    def message(self) -> str:
        """Compose the stable public message without exposing private fields."""

        return compose_diagnostic_message(self.problem, self.effect, self.recovery)


@dataclass(frozen=True, slots=True)
class PartialCause:
    """Private structured reason that a trustworthy scan is partial."""

    source_code: str
    location: str
    subject_path: str | None
    problem: str
    effect: str
    recovery: str | None
    evidence_ids: tuple[str, ...]
    boundary: SkippedScope | None = None


def _default_recovery(code: str, location: str, *, partial: bool) -> str | None:
    """Return a safe fallback only for diagnostic families with a concrete next step."""

    tokens = frozenset(code.replace("_", "-").replace(".", "-").split("-"))
    if code.startswith("inspection.max-"):
        limit = code.removeprefix("inspection.").replace("-", "_")
        return (
            "exclude irrelevant repository content or raise "
            f"scan.limits.{limit} in the root slygentify.toml, then rerun the scan"
        )
    if code == "inspection.git-tracked-paths-unavailable":
        return (
            "restore the standard trusted Git executable on PATH, or explicitly select a "
            "reviewed Git executable, then rerun the scan"
        )
    if "invalid" in tokens:
        return (
            f"correct the declaration at {location}, or intentionally exclude it when it is "
            "outside the intended inspection scope"
        )
    if tokens & {"missing", "unresolved"}:
        return (
            f"restore the referenced in-repository target for {location}, or correct or remove "
            "the declaration"
        )
    if tokens & {"unsafe", "unreadable"}:
        return f"make {location} a safely readable in-repository path, or intentionally exclude it"
    if tokens & {"dynamic", "external", "unsupported"}:
        return (
            "use a supported literal declaration when this knowledge is required, or inspect "
            "the source manually and retain the result as unknown"
        )
    if tokens & {"conflict", "overlapping", "coexistence"}:
        return "confirm the declarations are intentionally compatible, or reconcile them"
    if code == "configuration.relaxed-limits":
        return "review the expanded inspection effects and restore the default limits if unintended"
    if partial:
        return "review the reported source, then correct it or intentionally exclude it"
    return None


@implements("REQ046")
def compose_diagnostic_message(problem: str, effect: str, recovery: str | None = None) -> str:
    """Compose problem, observable effect, and optional safe recovery text."""

    return compose_message(problem, effect, recovery)
