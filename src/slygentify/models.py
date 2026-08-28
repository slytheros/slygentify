"""Public immutable values for normalized repository scans."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from slygentify.traceability import implements

ClaimClassification = Literal["verified", "inferred", "recommended", "unknown"]
Completion = Literal["complete", "partial"]
DoctorSeverity = Literal["info", "warning", "error"]
DiagnosticDisposition = Literal["problem", "limitation", "notice"]
ComponentRole = Literal["unknown", "auxiliary"]
LimitValue = int | Literal["unlimited"] | None
ProjectionSection = Literal["orientation", "workflows", "architecture", "automation", "boundaries"]
_PROJECTION_SECTIONS: tuple[ProjectionSection, ...] = (
    "orientation",
    "workflows",
    "architecture",
    "automation",
    "boundaries",
)


def _non_empty(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _safe_path(value: str, field: str) -> None:
    _non_empty(value, field)
    if "\\" in value or "\x00" in value:
        raise ValueError(f"{field} must be a repository-relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.startswith("//")
        or (path.parts and ":" in path.parts[0])
        or (value != "." and (path.as_posix() != value or any(part == ".." for part in path.parts)))
    ):
        raise ValueError(f"{field} must be a repository-relative POSIX path")


def _references(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple):
        raise ValueError(f"{field} must be a tuple")
    for value in values:
        _non_empty(value, field)
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{field} must be unique and sorted")


@implements("REQ010", "REQ046")
@dataclass(frozen=True, slots=True, kw_only=True)
class Repository:
    """The selected repository represented without a host-absolute path."""

    id: str
    root: str
    kind: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _non_empty(self.id, "id")
        _safe_path(self.root, "root")
        _non_empty(self.kind, "kind")
        _references(self.evidence_ids, "evidence_ids")


@implements("REQ010", "REQ030")
@dataclass(frozen=True, slots=True, kw_only=True)
class Component:
    """An evidence-backed development unit within the repository."""

    id: str
    path: str
    ecosystem: str
    kind: str
    evidence_ids: tuple[str, ...]
    ecosystems: tuple[str, ...] = ()
    role: ComponentRole = "unknown"

    def __post_init__(self) -> None:
        _non_empty(self.id, "id")
        _safe_path(self.path, "path")
        _non_empty(self.ecosystem, "ecosystem")
        _non_empty(self.kind, "kind")
        _references(self.evidence_ids, "evidence_ids")
        if not self.ecosystems:
            object.__setattr__(self, "ecosystems", (self.ecosystem,))
        _references(self.ecosystems, "ecosystems")
        expected = self.ecosystems[0] if len(self.ecosystems) == 1 else "mixed"
        if self.ecosystem != expected:
            raise ValueError("ecosystem must summarize ecosystems")
        if not isinstance(self.role, str) or self.role not in {"unknown", "auxiliary"}:
            raise ValueError("role is not supported")


@implements("REQ030")
@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentRelationship:
    """An evidence-backed directed relationship between two components."""

    id: str
    kind: str
    source_id: str
    target_id: str
    classification: ClaimClassification
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _non_empty(self.id, "id")
        _non_empty(self.kind, "kind")
        _non_empty(self.source_id, "source_id")
        _non_empty(self.target_id, "target_id")
        if self.source_id == self.target_id:
            raise ValueError("relationship endpoints must differ")
        if self.classification not in {"verified", "inferred", "recommended", "unknown"}:
            raise ValueError("classification is not supported")
        _references(self.evidence_ids, "evidence_ids")


@implements("REQ010")
@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    """A concise observation of an inspectable repository source."""

    id: str
    source_kind: str
    location: str
    locator: str | None
    observation: str
    verification_method: str | None

    def __post_init__(self) -> None:
        _non_empty(self.id, "id")
        _non_empty(self.source_kind, "source_kind")
        _safe_path(self.location, "location")
        if self.locator is not None:
            _non_empty(self.locator, "locator")
        _non_empty(self.observation, "observation")
        if self.verification_method is not None:
            _non_empty(self.verification_method, "verification_method")


@implements("REQ010")
@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """A classified conclusion backed by zero or more evidence records."""

    id: str
    code: str
    classification: ClaimClassification
    subject_id: str
    summary: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _non_empty(self.id, "id")
        _non_empty(self.code, "code")
        if self.classification not in {"verified", "inferred", "recommended", "unknown"}:
            raise ValueError("classification is not supported")
        _non_empty(self.subject_id, "subject_id")
        _non_empty(self.summary, "summary")
        _references(self.evidence_ids, "evidence_ids")


@implements("REQ010")
@dataclass(frozen=True, slots=True, kw_only=True)
class Diagnostic:
    """An inspection diagnostic with an explicit public disposition."""

    id: str
    code: str
    subject_id: str | None
    location: str | None
    message: str
    evidence_ids: tuple[str, ...]
    category: str | None = None
    problem: str | None = None
    effect: str | None = None
    recovery: str | None = None
    safety_rationale: str | None = None
    disposition: DiagnosticDisposition = "problem"

    def __post_init__(self) -> None:
        _non_empty(self.id, "id")
        _non_empty(self.code, "code")
        if self.subject_id is None and self.location is None:
            raise ValueError("diagnostic requires a subject_id or location")
        if self.subject_id is not None:
            _non_empty(self.subject_id, "subject_id")
        if self.location is not None:
            _safe_path(self.location, "location")
        _non_empty(self.message, "message")
        for name, value in (
            ("category", self.category),
            ("problem", self.problem),
            ("effect", self.effect),
            ("recovery", self.recovery),
            ("safety_rationale", self.safety_rationale),
        ):
            if value is not None:
                _non_empty(value, name)
        if (self.problem is None) != (self.effect is None):
            raise ValueError("diagnostic problem and effect must be present together")
        if self.disposition not in {"problem", "limitation", "notice"}:
            raise ValueError("diagnostic disposition is not supported")
        _references(self.evidence_ids, "evidence_ids")


@implements("REQ010")
@dataclass(frozen=True, slots=True, kw_only=True)
class SkippedScope:
    """A repository scope omitted by policy, safety, or a resource boundary."""

    scope: str
    reason: str
    effective_limit: LimitValue
    consumed: int | None
    omitted_scope: str

    def __post_init__(self) -> None:
        _safe_path(self.scope, "scope")
        _non_empty(self.reason, "reason")
        if self.effective_limit not in {"unlimited", None} and (
            not isinstance(self.effective_limit, int)
            or isinstance(self.effective_limit, bool)
            or self.effective_limit <= 0
        ):
            raise ValueError("effective_limit must be positive, 'unlimited', or None")
        if self.consumed is not None and (
            not isinstance(self.consumed, int)
            or isinstance(self.consumed, bool)
            or self.consumed < 0
        ):
            raise ValueError("consumed must be a non-negative integer or None")
        _safe_path(self.omitted_scope, "omitted_scope")


@implements("REQ010", "REQ015", "REQ030")
@dataclass(frozen=True, slots=True, kw_only=True)
class ScanResult:
    """The normalized, deterministic result of repository inspection."""

    schema_version: int
    producer_version: str
    completion: Completion
    repository: Repository
    components: tuple[Component, ...]
    evidence: tuple[Evidence, ...]
    findings: tuple[Finding, ...]
    diagnostics: tuple[Diagnostic, ...]
    skipped_scopes: tuple[SkippedScope, ...]
    relationships: tuple[ComponentRelationship, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        _non_empty(self.producer_version, "producer_version")
        if self.completion not in {"complete", "partial"}:
            raise ValueError("completion is not supported")
        collections = (
            self.components,
            self.evidence,
            self.findings,
            self.diagnostics,
            self.skipped_scopes,
            self.relationships,
        )
        if any(not isinstance(values, tuple) for values in collections):
            raise ValueError("scan collections must be tuples")
        if self.components != tuple(sorted(self.components, key=lambda item: (item.id, item.path))):
            raise ValueError("components are not in canonical order")
        if self.evidence != tuple(sorted(self.evidence, key=lambda item: (item.id, item.location))):
            raise ValueError("evidence is not in canonical order")
        if self.findings != tuple(
            sorted(self.findings, key=lambda item: (item.code, item.subject_id, item.id))
        ):
            raise ValueError("findings are not in canonical order")
        if self.diagnostics != tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (item.code, item.subject_id or item.location or "", item.id),
            )
        ):
            raise ValueError("diagnostics are not in canonical order")
        if self.skipped_scopes != tuple(
            sorted(self.skipped_scopes, key=lambda item: (item.scope, item.reason))
        ):
            raise ValueError("skipped_scopes are not in canonical order")
        if self.relationships != tuple(
            sorted(
                self.relationships,
                key=lambda item: (item.kind, item.source_id, item.target_id, item.id),
            )
        ):
            raise ValueError("relationships are not in canonical order")

        identifiers = [
            self.repository.id,
            *(item.id for item in self.components),
            *(item.id for item in self.evidence),
            *(item.id for item in self.findings),
            *(item.id for item in self.diagnostics),
            *(item.id for item in self.relationships),
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("scan identifiers must be unique")
        if len({item.path for item in self.components}) != len(self.components):
            raise ValueError("component paths must be unique")
        evidence_ids = {item.id for item in self.evidence}
        component_ids = {item.id for item in self.components}
        subject_ids = {self.repository.id, *component_ids}
        reference_sets = [
            self.repository.evidence_ids,
            *(item.evidence_ids for item in self.components),
            *(item.evidence_ids for item in self.findings),
            *(item.evidence_ids for item in self.diagnostics),
            *(item.evidence_ids for item in self.relationships),
        ]
        for references in reference_sets:
            if not set(references) <= evidence_ids:
                raise ValueError("scan contains a dangling evidence reference")
        for finding in self.findings:
            if finding.subject_id not in subject_ids:
                raise ValueError("scan contains a dangling finding subject")
        for diagnostic in self.diagnostics:
            if diagnostic.subject_id is not None and diagnostic.subject_id not in subject_ids:
                raise ValueError("scan contains a dangling diagnostic subject")
        for relationship in self.relationships:
            if (
                relationship.source_id not in component_ids
                or relationship.target_id not in component_ids
            ):
                raise ValueError("scan contains a dangling relationship endpoint")
        if self.completion == "partial" and not self.skipped_scopes and not self.diagnostics:
            raise ValueError("partial scans must explain the omitted work")


@implements("REQ046", "REQ047", "REQ048")
@dataclass(frozen=True, slots=True, kw_only=True)
class DoctorDiagnostic:
    """A stable, evidence-backed assessment of managed repository knowledge."""

    id: str
    code: str
    severity: DoctorSeverity
    classification: ClaimClassification
    subject_id: str | None
    location: str | None
    problem: str
    effect: str
    remediation: str | None
    evidence_ids: tuple[str, ...]
    category: str | None = None
    safety_rationale: str | None = None
    disposition: DiagnosticDisposition = "problem"

    def __post_init__(self) -> None:
        _non_empty(self.id, "id")
        _non_empty(self.code, "code")
        if self.severity not in {"info", "warning", "error"}:
            raise ValueError("severity is not supported")
        if self.classification not in {"verified", "inferred", "recommended", "unknown"}:
            raise ValueError("classification is not supported")
        if self.disposition not in {"problem", "limitation", "notice"}:
            raise ValueError("doctor diagnostic disposition is not supported")
        if self.subject_id is None and self.location is None:
            raise ValueError("doctor diagnostic requires a subject_id or location")
        if self.subject_id is not None:
            _non_empty(self.subject_id, "subject_id")
        if self.location is not None:
            _safe_path(self.location, "location")
        _non_empty(self.problem, "problem")
        _non_empty(self.effect, "effect")
        if self.remediation is not None:
            _non_empty(self.remediation, "remediation")
        if self.category is not None:
            _non_empty(self.category, "category")
        if self.safety_rationale is not None:
            _non_empty(self.safety_rationale, "safety_rationale")
        _references(self.evidence_ids, "evidence_ids")


@implements("REQ047", "REQ048")
@dataclass(frozen=True, slots=True, kw_only=True)
class DoctorResult:
    """The deterministic result of static managed-knowledge checks."""

    schema_version: int
    producer_version: str
    completion: Completion
    repository: Repository
    evidence: tuple[Evidence, ...]
    diagnostics: tuple[DoctorDiagnostic, ...]
    skipped_scopes: tuple[SkippedScope, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("schema_version must be 1")
        _non_empty(self.producer_version, "producer_version")
        if self.completion not in {"complete", "partial"}:
            raise ValueError("completion is not supported")
        collections = (self.evidence, self.diagnostics, self.skipped_scopes)
        if any(not isinstance(values, tuple) for values in collections):
            raise ValueError("doctor collections must be tuples")
        if self.evidence != tuple(sorted(self.evidence, key=lambda item: (item.id, item.location))):
            raise ValueError("evidence is not in canonical order")
        if self.diagnostics != tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (item.code, item.subject_id or "", item.location or "", item.id),
            )
        ):
            raise ValueError("doctor diagnostics are not in canonical order")
        if self.skipped_scopes != tuple(
            sorted(self.skipped_scopes, key=lambda item: (item.scope, item.reason))
        ):
            raise ValueError("skipped scopes are not in canonical order")
        identifiers = [
            self.repository.id,
            *(item.id for item in self.evidence),
            *(item.id for item in self.diagnostics),
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("doctor identifiers must be unique")
        evidence_ids = {item.id for item in self.evidence}
        if not set(self.repository.evidence_ids) <= evidence_ids:
            raise ValueError("doctor result contains a dangling repository evidence reference")
        if any(not set(item.evidence_ids) <= evidence_ids for item in self.diagnostics):
            raise ValueError("doctor result contains a dangling diagnostic evidence reference")
        if self.completion == "partial" and not self.diagnostics and not self.skipped_scopes:
            raise ValueError("partial doctor results must explain omitted work")


@implements("REQ042")
@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionScope:
    """The requested logical path and its deepest owning component, if any."""

    requested_path: str
    matched_component_id: str | None
    matched_component_path: str | None

    def __post_init__(self) -> None:
        _safe_path(self.requested_path, "requested_path")
        if (self.matched_component_id is None) != (self.matched_component_path is None):
            raise ValueError("matched component identity and path must be present together")
        if self.matched_component_id is not None:
            _non_empty(self.matched_component_id, "matched_component_id")
        if self.matched_component_path is not None:
            _safe_path(self.matched_component_path, "matched_component_path")


@implements("REQ042")
@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionNavigation:
    """Explicit component references for iterative map navigation."""

    ancestors: tuple[str, ...]
    owner: str | None
    children: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.ancestors, tuple) or not isinstance(self.children, tuple):
            raise ValueError("projection navigation collections must be tuples")
        for field, values in (("ancestors", self.ancestors), ("children", self.children)):
            if len(values) != len(set(values)):
                raise ValueError(f"projection navigation {field} must be unique")
            for value in values:
                _non_empty(value, field)
        if self.owner is not None:
            _non_empty(self.owner, "owner")
        references = (
            *self.ancestors,
            *((self.owner,) if self.owner is not None else ()),
            *self.children,
        )
        if len(references) != len(set(references)):
            raise ValueError("projection navigation roles must not overlap")


@implements("REQ042")
@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionOmission:
    """A deterministic count of selected records excluded by the output limit."""

    section: ProjectionSection
    record_kind: str
    count: int

    def __post_init__(self) -> None:
        if self.section not in _PROJECTION_SECTIONS:
            raise ValueError("projection omission section is not supported")
        _non_empty(self.record_kind, "record_kind")
        if not isinstance(self.count, int) or isinstance(self.count, bool) or self.count <= 0:
            raise ValueError("projection omission count must be a positive integer")


@implements("REQ042")
@dataclass(frozen=True, slots=True, kw_only=True)
class ScanProjection:
    """A bounded evidence-closed selection from one normalized scan value."""

    schema_version: int
    source_scan_schema_version: int
    producer_version: str
    source_scan_sha256: str
    source_completion: Completion
    scope: ProjectionScope
    navigation: ProjectionNavigation
    sections: tuple[ProjectionSection, ...]
    repository: Repository
    components: tuple[Component, ...]
    relationships: tuple[ComponentRelationship, ...]
    findings: tuple[Finding, ...]
    diagnostics: tuple[Diagnostic, ...]
    skipped_scopes: tuple[SkippedScope, ...]
    evidence: tuple[Evidence, ...]
    omissions: tuple[ProjectionOmission, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.source_scan_schema_version != 1:
            raise ValueError("projection schema versions must be 1")
        _non_empty(self.producer_version, "producer_version")
        if re.fullmatch(r"[0-9a-f]{64}", self.source_scan_sha256) is None:
            raise ValueError("source_scan_sha256 must be a lowercase SHA-256 digest")
        if self.source_completion not in {"complete", "partial"}:
            raise ValueError("source_completion is not supported")
        if not isinstance(self.scope, ProjectionScope):
            raise ValueError("scope must be a ProjectionScope")
        if not isinstance(self.navigation, ProjectionNavigation):
            raise ValueError("navigation must be a ProjectionNavigation")
        if not isinstance(self.sections, tuple) or not self.sections:
            raise ValueError("sections must be a non-empty tuple")
        if any(item not in _PROJECTION_SECTIONS for item in self.sections):
            raise ValueError("projection section is not supported")
        expected_sections = tuple(item for item in _PROJECTION_SECTIONS if item in self.sections)
        if self.sections != expected_sections:
            raise ValueError("projection sections must be unique and canonical")
        collections = (
            self.components,
            self.relationships,
            self.findings,
            self.diagnostics,
            self.skipped_scopes,
            self.evidence,
            self.omissions,
        )
        if any(not isinstance(values, tuple) for values in collections):
            raise ValueError("projection collections must be tuples")
        if self.components != tuple(sorted(self.components, key=lambda item: (item.id, item.path))):
            raise ValueError("projection components are not in canonical order")
        if self.relationships != tuple(
            sorted(
                self.relationships,
                key=lambda item: (item.kind, item.source_id, item.target_id, item.id),
            )
        ):
            raise ValueError("projection relationships are not in canonical order")
        if self.findings != tuple(
            sorted(self.findings, key=lambda item: (item.code, item.subject_id, item.id))
        ):
            raise ValueError("projection findings are not in canonical order")
        if self.diagnostics != tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (item.code, item.subject_id or item.location or "", item.id),
            )
        ):
            raise ValueError("projection diagnostics are not in canonical order")
        if self.skipped_scopes != tuple(
            sorted(self.skipped_scopes, key=lambda item: (item.scope, item.reason))
        ):
            raise ValueError("projection skipped scopes are not in canonical order")
        if self.evidence != tuple(sorted(self.evidence, key=lambda item: (item.id, item.location))):
            raise ValueError("projection evidence is not in canonical order")
        section_order = {name: index for index, name in enumerate(_PROJECTION_SECTIONS)}
        if self.omissions != tuple(
            sorted(
                self.omissions,
                key=lambda item: (section_order[item.section], item.record_kind),
            )
        ):
            raise ValueError("projection omissions are not in canonical order")
        if any(item.section not in self.sections for item in self.omissions):
            raise ValueError("projection omission section was not selected")

        identifiers = [
            self.repository.id,
            *(item.id for item in self.components),
            *(item.id for item in self.relationships),
            *(item.id for item in self.findings),
            *(item.id for item in self.diagnostics),
            *(item.id for item in self.evidence),
        ]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("projection identifiers must be unique")
        evidence_ids = {item.id for item in self.evidence}
        component_ids = {item.id for item in self.components}
        component_by_id = {item.id: item for item in self.components}
        subjects = {self.repository.id, *component_ids}
        reference_sets = [
            self.repository.evidence_ids,
            *(item.evidence_ids for item in self.components),
            *(item.evidence_ids for item in self.relationships),
            *(item.evidence_ids for item in self.findings),
            *(item.evidence_ids for item in self.diagnostics),
        ]
        if any(not set(references) <= evidence_ids for references in reference_sets):
            raise ValueError("projection contains a dangling evidence reference")
        if any(item.subject_id not in subjects for item in self.findings):
            raise ValueError("projection contains a dangling finding subject")
        if any(
            item.subject_id is not None and item.subject_id not in subjects
            for item in self.diagnostics
        ):
            raise ValueError("projection contains a dangling diagnostic subject")
        if any(
            item.source_id not in component_ids or item.target_id not in component_ids
            for item in self.relationships
        ):
            raise ValueError("projection contains a dangling relationship endpoint")
        if (
            self.scope.matched_component_id is not None
            and self.scope.matched_component_id not in component_ids
        ):
            raise ValueError("matched component is absent from projection")
        if self.scope.matched_component_id is not None:
            matched = next(
                item for item in self.components if item.id == self.scope.matched_component_id
            )
            if matched.path != self.scope.matched_component_path:
                raise ValueError("matched component path does not match its identity")
        if self.navigation.owner != self.scope.matched_component_id:
            raise ValueError("navigation owner does not match projection scope")
        navigation_ids = {
            *self.navigation.ancestors,
            *self.navigation.children,
            *((self.navigation.owner,) if self.navigation.owner is not None else ()),
        }
        if not navigation_ids <= component_ids:
            raise ValueError("projection navigation contains a dangling component reference")
        if "orientation" not in self.sections and self.navigation.children:
            raise ValueError("projection navigation children require the orientation section")
        if self.navigation.owner is not None:
            owner_path = component_by_id[self.navigation.owner].path
            expected_ancestors = tuple(
                item.id
                for item in sorted(
                    (
                        item
                        for item in self.components
                        if item.id != self.navigation.owner
                        and (item.path == "." or owner_path.startswith(f"{item.path}/"))
                    ),
                    key=lambda item: (
                        0 if item.path == "." else len(PurePosixPath(item.path).parts),
                        item.path,
                        item.id,
                    ),
                )
            )
            if self.navigation.ancestors != expected_ancestors:
                raise ValueError("projection navigation ancestors are not canonical")
        elif self.navigation.ancestors:
            raise ValueError("unmatched projection navigation cannot have ancestors")
        child_paths = tuple(component_by_id[item].path for item in self.navigation.children)
        expected_child_ids = tuple(
            item.id
            for item in sorted(
                (component_by_id[item] for item in self.navigation.children),
                key=lambda item: (
                    0 if item.path == "." else len(PurePosixPath(item.path).parts),
                    item.path,
                    item.id,
                ),
            )
        )
        if self.navigation.children != expected_child_ids:
            raise ValueError("projection navigation children are not canonical")
        if self.navigation.owner is not None and any(
            not (owner_path == "." or path.startswith(f"{owner_path}/")) for path in child_paths
        ):
            raise ValueError("projection navigation child is outside its owner")
        for child_id in self.navigation.children:
            child_path = component_by_id[child_id].path
            if any(
                item.id not in {self.navigation.owner, child_id}
                and (item.path == "." or child_path.startswith(f"{item.path}/"))
                and (
                    self.navigation.owner is None
                    or owner_path == "."
                    or item.path.startswith(f"{owner_path}/")
                )
                for item in self.components
            ):
                raise ValueError("projection navigation children must be direct")
