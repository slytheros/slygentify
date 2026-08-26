"""Deterministic path-scoped projection of normalized scan results."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from slygentify._errors import ScanError
from slygentify._projection_serialization import dump_scan_projection_json
from slygentify._scan.contracts import compose_diagnostic_message
from slygentify._serialization import dump_scan_json, validate_scan
from slygentify.models import (
    Component,
    ComponentRelationship,
    Diagnostic,
    Finding,
    ProjectionNavigation,
    ProjectionOmission,
    ProjectionScope,
    ProjectionSection,
    ScanProjection,
    ScanResult,
    SkippedScope,
)
from slygentify.traceability import implements

DEFAULT_MAP_BYTES = 8 * 1024
_SECTIONS: tuple[ProjectionSection, ...] = (
    "orientation",
    "workflows",
    "architecture",
    "automation",
    "boundaries",
)
_BOUNDARY_TOKENS = {
    "conflict",
    "coexistence",
    "invalid",
    "missing",
    "overlapping",
    "redacted",
    "sensitive",
    "unsafe",
    "unsupported",
    "unresolved",
}

_Record = Component | ComponentRelationship | Finding | Diagnostic | SkippedScope


def _map_error(problem: str, effect: str, recovery: str) -> ScanError:
    return ScanError(compose_diagnostic_message(problem, effect, recovery))


@dataclass(frozen=True, slots=True)
class _Candidate:
    section: ProjectionSection
    record_kind: str
    key: tuple[str, ...]
    record: _Record


def _safe_scope(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise _map_error(
            "The map scope is not a repository-relative POSIX path",
            "No projection was produced",
            "use '.' or a normalized forward-slash path inside the repository",
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.startswith("//")
        or (path.parts and ":" in path.parts[0])
        or (
            value != "."
            and (path.as_posix() != value or any(part in {".", ".."} for part in path.parts))
        )
    ):
        raise _map_error(
            "The map scope is not a repository-relative POSIX path",
            "No projection was produced",
            "use '.' or a normalized forward-slash path inside the repository",
        )
    return value


def _contains(parent: str, child: str) -> bool:
    return parent == "." or child == parent or child.startswith(f"{parent}/")


def _intersects(left: str, right: str) -> bool:
    return _contains(left, right) or _contains(right, left)


def _depth(path: str) -> int:
    return 0 if path == "." else len(PurePosixPath(path).parts)


def _sections(values: Iterable[str] | None) -> tuple[ProjectionSection, ...]:
    if values is None:
        return ("orientation", "boundaries")
    if isinstance(values, (str, bytes)):
        raise _map_error(
            "Map sections were supplied as a single string or byte value",
            "No projection was produced",
            "supply a collection containing supported section names",
        )
    requested = tuple(values)
    if not requested:
        raise _map_error(
            "The map section collection is empty",
            "No projection was produced",
            "select at least one supported section",
        )
    if any(item not in _SECTIONS for item in requested):
        raise _map_error(
            "A requested map section is unsupported",
            "No projection was produced",
            "use orientation, workflows, architecture, automation, or boundaries",
        )
    return tuple(item for item in _SECTIONS if item in requested)


def _limit(value: int | Literal["unlimited"]) -> int | None:
    if value == "unlimited":
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _map_error(
            "Map max_bytes is not a positive integer or 'unlimited'",
            "No projection was produced",
            "supply a positive byte limit or 'unlimited'",
        )
    return value


def _finding_section(finding: Finding) -> ProjectionSection:
    tokens = set(re.findall(r"[a-z0-9]+", finding.code.casefold()))
    if finding.classification in {"unknown", "recommended"} or tokens & _BOUNDARY_TOKENS:
        return "boundaries"
    if "ci" in tokens:
        return "automation"
    if tokens & {"command", "script"}:
        return "workflows"
    if tokens & {"entry", "entrypoint", "bin", "framework", "dependency", "dependencies", "tool"}:
        return "architecture"
    return "orientation"


def _record_key(record: _Record) -> tuple[str, ...]:
    if isinstance(record, Component):
        return ("component", record.id)
    if isinstance(record, ComponentRelationship):
        return ("relationship", record.id)
    if isinstance(record, Finding):
        return ("finding", record.id)
    if isinstance(record, Diagnostic):
        return ("diagnostic", record.id)
    return ("skipped_scope", record.scope, record.reason, record.omitted_scope)


def _candidate_sort(candidate: _Candidate) -> tuple[object, ...]:
    record = candidate.record
    if isinstance(record, Component):
        detail: tuple[object, ...] = (_depth(record.path), record.path, record.id)
    elif isinstance(record, ComponentRelationship):
        detail = (record.kind, record.source_id, record.target_id, record.id)
    elif isinstance(record, Finding):
        detail = (record.code, record.subject_id, record.id)
    elif isinstance(record, Diagnostic):
        detail = (record.code, record.subject_id or record.location or "", record.id)
    else:
        detail = (record.scope, record.reason, record.omitted_scope)
    priority = {
        "boundaries": 0,
        "orientation-relationship": 1,
        "orientation": 2,
        "workflows": 3,
        "architecture": 4,
        "automation": 5,
        "orientation-component": 6,
    }
    category: str = candidate.section
    if candidate.record_kind in {"relationship", "component"}:
        category = f"{candidate.section}-{candidate.record_kind}"
    return (priority[category], *detail)


def _direct_children(
    owner: Component | None, components: tuple[Component, ...]
) -> tuple[Component, ...]:
    descendants = [
        item
        for item in components
        if item != owner and (owner is None or _contains(owner.path, item.path))
    ]
    direct = []
    for item in descendants:
        if any(
            other != item
            and (owner is None or other != owner)
            and _contains(other.path, item.path)
            and (owner is None or _contains(owner.path, other.path))
            for other in descendants
        ):
            continue
        direct.append(item)
    return tuple(sorted(direct, key=lambda item: (_depth(item.path), item.path, item.id)))


def _references(record: _Record) -> tuple[str, ...]:
    return () if isinstance(record, SkippedScope) else record.evidence_ids


def _projection(
    result: ScanResult,
    *,
    source_digest: str,
    scope: ProjectionScope,
    sections: tuple[ProjectionSection, ...],
    required_components: tuple[Component, ...],
    direct_children: tuple[Component, ...],
    candidates: tuple[_Candidate, ...],
    selected: frozenset[tuple[str, ...]],
) -> ScanProjection:
    chosen = [item.record for item in candidates if item.key in selected]
    chosen_relationships = [item for item in chosen if isinstance(item, ComponentRelationship)]
    component_ids = {item.id for item in result.components}
    dependency_ids = {
        endpoint
        for relationship in chosen_relationships
        for endpoint in (relationship.source_id, relationship.target_id)
    }
    dependency_ids.update(
        item.subject_id
        for item in chosen
        if isinstance(item, Diagnostic)
        and item.subject_id is not None
        and item.subject_id in component_ids
    )
    required_ids = {item.id for item in required_components}
    dependent_components = [
        item
        for item in result.components
        if item.id in dependency_ids and item.id not in required_ids
    ]
    components = tuple(
        sorted(
            (
                *required_components,
                *dependent_components,
                *(
                    item
                    for item in chosen
                    if isinstance(item, Component)
                    and item.id not in required_ids
                    and item.id not in dependency_ids
                ),
            ),
            key=lambda item: (item.id, item.path),
        )
    )
    relationships = tuple(
        sorted(
            chosen_relationships,
            key=lambda item: (item.kind, item.source_id, item.target_id, item.id),
        )
    )
    findings = tuple(
        sorted(
            (item for item in chosen if isinstance(item, Finding)),
            key=lambda item: (item.code, item.subject_id, item.id),
        )
    )
    diagnostics = tuple(
        sorted(
            (item for item in chosen if isinstance(item, Diagnostic)),
            key=lambda item: (item.code, item.subject_id or item.location or "", item.id),
        )
    )
    skipped = tuple(
        sorted(
            (item for item in chosen if isinstance(item, SkippedScope)),
            key=lambda item: (item.scope, item.reason),
        )
    )
    evidence_ids = set(result.repository.evidence_ids)
    for component in components:
        evidence_ids.update(component.evidence_ids)
    for item in chosen:
        evidence_ids.update(_references(item))
    evidence = tuple(item for item in result.evidence if item.id in evidence_ids)
    included_keys = {
        *(_record_key(item) for item in components),
        *(_record_key(item) for item in relationships),
        *(_record_key(item) for item in findings),
        *(_record_key(item) for item in diagnostics),
        *(_record_key(item) for item in skipped),
    }
    omitted: dict[tuple[ProjectionSection, str], int] = {}
    for candidate in candidates:
        if candidate.key not in included_keys:
            key = (candidate.section, candidate.record_kind)
            omitted[key] = omitted.get(key, 0) + 1
    section_order = {name: index for index, name in enumerate(_SECTIONS)}
    omissions = tuple(
        ProjectionOmission(section=section, record_kind=kind, count=count)
        for (section, kind), count in sorted(
            omitted.items(), key=lambda item: (section_order[item[0][0]], item[0][1])
        )
    )
    included_component_ids = {item.id for item in components}
    owner_id = scope.matched_component_id
    ancestor_ids = tuple(
        item.id
        for item in sorted(
            (item for item in required_components if item.id != owner_id),
            key=lambda item: (_depth(item.path), item.path, item.id),
        )
    )
    child_ids = tuple(item.id for item in direct_children if item.id in included_component_ids)
    return ScanProjection(
        schema_version=1,
        source_scan_schema_version=result.schema_version,
        producer_version=result.producer_version,
        source_scan_sha256=source_digest,
        source_completion=result.completion,
        scope=scope,
        navigation=ProjectionNavigation(
            ancestors=ancestor_ids,
            owner=owner_id,
            children=child_ids,
        ),
        sections=sections,
        repository=result.repository,
        components=components,
        relationships=relationships,
        findings=findings,
        diagnostics=diagnostics,
        skipped_scopes=skipped,
        evidence=evidence,
        omissions=omissions,
    )


@implements("REQ042")
def project_scan(
    result: ScanResult,
    *,
    scope: str = ".",
    sections: Iterable[str] | None = None,
    max_bytes: int | Literal["unlimited"] = DEFAULT_MAP_BYTES,
) -> ScanProjection:
    """Select one deterministic evidence-closed operating map from a trusted scan."""
    validated = validate_scan(result)
    requested_scope = _safe_scope(scope)
    selected_sections = _sections(sections)
    byte_limit = _limit(max_bytes)
    digest = hashlib.sha256(dump_scan_json(validated)).hexdigest()

    owners = [item for item in validated.components if _contains(item.path, requested_scope)]
    owner = max(owners, key=lambda item: (_depth(item.path), item.path)) if owners else None
    ancestors = (
        []
        if owner is None
        else [
            item
            for item in validated.components
            if item != owner and _contains(item.path, owner.path)
        ]
    )
    ordered_ancestors = tuple(
        sorted(ancestors, key=lambda item: (_depth(item.path), item.path, item.id))
    )
    required_components = tuple(
        sorted(
            (*ordered_ancestors, *((owner,) if owner is not None else ())),
            key=lambda item: (item.id, item.path),
        )
    )
    projection_scope = ProjectionScope(
        requested_path=requested_scope,
        matched_component_id=None if owner is None else owner.id,
        matched_component_path=None if owner is None else owner.path,
    )
    subject_ids = {validated.repository.id, *(item.id for item in required_components)}
    candidates: list[_Candidate] = []

    if "boundaries" in selected_sections:
        for finding in validated.findings:
            if _finding_section(finding) == "boundaries" and finding.subject_id in subject_ids:
                candidates.append(
                    _Candidate("boundaries", "finding", _record_key(finding), finding)
                )
        for diagnostic in validated.diagnostics:
            relevant = diagnostic.subject_id in subject_ids or (
                diagnostic.location is not None
                and _intersects(diagnostic.location, requested_scope)
            )
            if relevant:
                candidates.append(
                    _Candidate("boundaries", "diagnostic", _record_key(diagnostic), diagnostic)
                )
        for skipped in validated.skipped_scopes:
            if _intersects(skipped.scope, requested_scope) or _intersects(
                skipped.omitted_scope, requested_scope
            ):
                candidates.append(
                    _Candidate("boundaries", "skipped_scope", _record_key(skipped), skipped)
                )

    children = _direct_children(owner, validated.components)
    if "orientation" in selected_sections:
        navigable_ids = {item.id for item in (*required_components, *children)}
        for relationship in validated.relationships:
            if relationship.source_id in navigable_ids and relationship.target_id in navigable_ids:
                candidates.append(
                    _Candidate(
                        "orientation", "relationship", _record_key(relationship), relationship
                    )
                )

    for finding in validated.findings:
        section = _finding_section(finding)
        if (
            section != "boundaries"
            and section in selected_sections
            and finding.subject_id in subject_ids
        ):
            candidates.append(_Candidate(section, "finding", _record_key(finding), finding))

    if "orientation" in selected_sections:
        for component in children:
            candidates.append(
                _Candidate("orientation", "component", _record_key(component), component)
            )

    ordered_candidates = tuple(sorted(candidates, key=_candidate_sort))
    selected: frozenset[tuple[str, ...]] = frozenset()
    required = _projection(
        validated,
        source_digest=digest,
        scope=projection_scope,
        sections=selected_sections,
        required_components=required_components,
        direct_children=children,
        candidates=ordered_candidates,
        selected=selected,
    )
    if byte_limit is not None and len(dump_scan_projection_json(required)) > byte_limit:
        raise _map_error(
            "Required map context does not fit within --max-bytes",
            "No incomplete projection was emitted",
            "raise the limit or use 'unlimited'",
        )
    for candidate in ordered_candidates:
        trial_selected = selected | {candidate.key}
        trial = _projection(
            validated,
            source_digest=digest,
            scope=projection_scope,
            sections=selected_sections,
            required_components=required_components,
            direct_children=children,
            candidates=ordered_candidates,
            selected=trial_selected,
        )
        if byte_limit is None or len(dump_scan_projection_json(trial)) <= byte_limit:
            selected = trial_selected
    return _projection(
        validated,
        source_digest=digest,
        scope=projection_scope,
        sections=selected_sections,
        required_components=required_components,
        direct_children=children,
        candidates=ordered_candidates,
        selected=selected,
    )
