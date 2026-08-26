"""Private candidate contract shared by built-in scan detectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from slygentify.traceability import implements

EvidenceKey = tuple[str, str, str | None, str]


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

    def __init__(
        self,
        code: str,
        location: str,
        message: str,
        partial: bool,
        subject_path: str | None = None,
        evidence_keys: tuple[EvidenceKey, ...] = (),
    ) -> None:
        """Split detector wording into the structured private contract."""

        statement, marker, recovery = message.partition(" Next: ")
        problem, separator, effect = statement.partition(". ")
        if not separator:
            problem = statement
            effect = (
                "The affected evidence was omitted, so the scan is partial"
                if partial
                else "The scan retained the available evidence without treating this item as verified"
            )
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "problem", problem)
        object.__setattr__(self, "effect", effect)
        object.__setattr__(self, "partial", partial)
        object.__setattr__(self, "subject_path", subject_path)
        object.__setattr__(self, "evidence_keys", evidence_keys)
        object.__setattr__(self, "recovery", recovery if marker else None)

    @property
    def message(self) -> str:
        """Compose the stable public message without exposing private fields."""

        return compose_diagnostic_message(self.problem, self.effect, self.recovery)


def _sentence(text: str) -> str:
    normalized = " ".join(text.split()).rstrip(".")
    if not normalized:
        raise ValueError("diagnostic text must not be empty")
    return f"{normalized}."


@implements("REQ046")
def compose_diagnostic_message(problem: str, effect: str, recovery: str | None = None) -> str:
    """Compose problem, observable effect, and optional safe recovery text."""

    message = f"{_sentence(problem)} {_sentence(effect)}"
    if recovery is not None:
        message = f"{message} Next: {_sentence(recovery)}"
    return message
