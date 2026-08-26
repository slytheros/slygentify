"""Validation and canonical JSON for scoped scan projections."""

from __future__ import annotations

import json
from importlib import resources
from typing import Literal, cast

from pydantic import Field, TypeAdapter, ValidationError

from slygentify._errors import ScanValidationError
from slygentify._serialization import (
    _MAX_DOCUMENT_BYTES,
    _ComponentInput,
    _ComponentRelationshipInput,
    _DiagnosticInput,
    _DuplicateKeyError,
    _EvidenceInput,
    _FindingInput,
    _InputModel,
    _measure_graph,
    _non_finite,
    _object,
    _RepositoryInput,
    _SkippedScopeInput,
)
from slygentify.models import (
    Component,
    ComponentRelationship,
    Diagnostic,
    Evidence,
    Finding,
    ProjectionNavigation,
    ProjectionOmission,
    ProjectionScope,
    Repository,
    ScanProjection,
    SkippedScope,
)
from slygentify.traceability import implements

_Section = Literal["orientation", "workflows", "architecture", "automation", "boundaries"]


class _ProjectionScopeInput(_InputModel):
    requested_path: str
    matched_component_id: str | None = None
    matched_component_path: str | None = None


class _ProjectionOmissionInput(_InputModel):
    section: _Section
    record_kind: str
    count: int


class _ProjectionNavigationInput(_InputModel):
    ancestors: list[str]
    owner: str | None = None
    children: list[str]


class _ProjectionInput(_InputModel):
    schema_version: Literal[1]
    source_scan_schema_version: Literal[1]
    producer_version: str
    source_scan_sha256: str
    source_completion: Literal["complete", "partial"]
    scope: _ProjectionScopeInput
    navigation: _ProjectionNavigationInput
    sections: list[_Section]
    repository: _RepositoryInput
    components: list[_ComponentInput]
    relationships: list[_ComponentRelationshipInput] = Field(default_factory=list)
    findings: list[_FindingInput]
    diagnostics: list[_DiagnosticInput]
    skipped_scopes: list[_SkippedScopeInput]
    evidence: list[_EvidenceInput]
    omissions: list[_ProjectionOmissionInput]


_PROJECTION_ADAPTER: TypeAdapter[_ProjectionInput] = TypeAdapter(_ProjectionInput)


def _optional(value: dict[str, object], name: str, item: object | None) -> None:
    if item is not None:
        value[name] = item


def _projection_mapping(projection: ScanProjection) -> dict[str, object]:
    scope: dict[str, object] = {"requested_path": projection.scope.requested_path}
    _optional(scope, "matched_component_id", projection.scope.matched_component_id)
    _optional(scope, "matched_component_path", projection.scope.matched_component_path)
    navigation: dict[str, object] = {"ancestors": list(projection.navigation.ancestors)}
    _optional(navigation, "owner", projection.navigation.owner)
    navigation["children"] = list(projection.navigation.children)
    repository = {
        "id": projection.repository.id,
        "root": projection.repository.root,
        "kind": projection.repository.kind,
        "evidence_ids": list(projection.repository.evidence_ids),
    }
    components = [
        {
            "id": item.id,
            "path": item.path,
            "ecosystem": item.ecosystem,
            "kind": item.kind,
            "role": item.role,
            "evidence_ids": list(item.evidence_ids),
            "ecosystems": list(item.ecosystems),
        }
        for item in projection.components
    ]
    relationships = [
        {
            "id": item.id,
            "kind": item.kind,
            "source_id": item.source_id,
            "target_id": item.target_id,
            "classification": item.classification,
            "evidence_ids": list(item.evidence_ids),
        }
        for item in projection.relationships
    ]
    findings = [
        {
            "id": item.id,
            "code": item.code,
            "classification": item.classification,
            "subject_id": item.subject_id,
            "summary": item.summary,
            "evidence_ids": list(item.evidence_ids),
        }
        for item in projection.findings
    ]
    diagnostics: list[dict[str, object]] = []
    for diagnostic in projection.diagnostics:
        record: dict[str, object] = {"id": diagnostic.id, "code": diagnostic.code}
        _optional(record, "subject_id", diagnostic.subject_id)
        _optional(record, "location", diagnostic.location)
        record["message"] = diagnostic.message
        record["evidence_ids"] = list(diagnostic.evidence_ids)
        diagnostics.append(record)
    skipped_scopes: list[dict[str, object]] = []
    for skipped in projection.skipped_scopes:
        record = {"scope": skipped.scope, "reason": skipped.reason}
        _optional(record, "effective_limit", skipped.effective_limit)
        _optional(record, "consumed", skipped.consumed)
        record["omitted_scope"] = skipped.omitted_scope
        skipped_scopes.append(record)
    evidence: list[dict[str, object]] = []
    for evidence_item in projection.evidence:
        record = {
            "id": evidence_item.id,
            "source_kind": evidence_item.source_kind,
            "location": evidence_item.location,
        }
        _optional(record, "locator", evidence_item.locator)
        record["observation"] = evidence_item.observation
        _optional(record, "verification_method", evidence_item.verification_method)
        evidence.append(record)
    omissions = [
        {"section": item.section, "record_kind": item.record_kind, "count": item.count}
        for item in projection.omissions
    ]
    return {
        "schema_version": projection.schema_version,
        "source_scan_schema_version": projection.source_scan_schema_version,
        "producer_version": projection.producer_version,
        "source_scan_sha256": projection.source_scan_sha256,
        "source_completion": projection.source_completion,
        "scope": scope,
        "navigation": navigation,
        "sections": list(projection.sections),
        "repository": repository,
        "components": components,
        "relationships": relationships,
        "findings": findings,
        "diagnostics": diagnostics,
        "skipped_scopes": skipped_scopes,
        "evidence": evidence,
        "omissions": omissions,
    }


def _public_projection(value: _ProjectionInput) -> ScanProjection:
    repository = Repository(
        id=value.repository.id,
        root=value.repository.root,
        kind=value.repository.kind,
        evidence_ids=tuple(value.repository.evidence_ids),
    )
    components = tuple(
        Component(
            id=item.id,
            path=item.path,
            ecosystem=item.ecosystem,
            kind=item.kind,
            evidence_ids=tuple(item.evidence_ids),
            ecosystems=tuple(item.ecosystems or (item.ecosystem,)),
            role=item.role,
        )
        for item in value.components
    )
    relationships = tuple(
        ComponentRelationship(
            id=item.id,
            kind=item.kind,
            source_id=item.source_id,
            target_id=item.target_id,
            classification=item.classification,
            evidence_ids=tuple(item.evidence_ids),
        )
        for item in value.relationships
    )
    findings = tuple(
        Finding(
            id=item.id,
            code=item.code,
            classification=item.classification,
            subject_id=item.subject_id,
            summary=item.summary,
            evidence_ids=tuple(item.evidence_ids),
        )
        for item in value.findings
    )
    diagnostics = tuple(
        Diagnostic(
            id=item.id,
            code=item.code,
            subject_id=item.subject_id,
            location=item.location,
            message=item.message,
            evidence_ids=tuple(item.evidence_ids),
        )
        for item in value.diagnostics
    )
    skipped_scopes = tuple(
        SkippedScope(
            scope=item.scope,
            reason=item.reason,
            effective_limit=item.effective_limit,
            consumed=item.consumed,
            omitted_scope=item.omitted_scope,
        )
        for item in value.skipped_scopes
    )
    evidence = tuple(
        Evidence(
            id=item.id,
            source_kind=item.source_kind,
            location=item.location,
            locator=item.locator,
            observation=item.observation,
            verification_method=item.verification_method,
        )
        for item in value.evidence
    )
    return ScanProjection(
        schema_version=value.schema_version,
        source_scan_schema_version=value.source_scan_schema_version,
        producer_version=value.producer_version,
        source_scan_sha256=value.source_scan_sha256,
        source_completion=value.source_completion,
        scope=ProjectionScope(
            requested_path=value.scope.requested_path,
            matched_component_id=value.scope.matched_component_id,
            matched_component_path=value.scope.matched_component_path,
        ),
        navigation=ProjectionNavigation(
            ancestors=tuple(value.navigation.ancestors),
            owner=value.navigation.owner,
            children=tuple(value.navigation.children),
        ),
        sections=tuple(value.sections),
        repository=repository,
        components=components,
        relationships=relationships,
        findings=findings,
        diagnostics=diagnostics,
        skipped_scopes=skipped_scopes,
        evidence=evidence,
        omissions=tuple(
            ProjectionOmission(
                section=item.section,
                record_kind=item.record_kind,
                count=item.count,
            )
            for item in value.omissions
        ),
    )


@implements("REQ042")
def validate_scan_projection(value: object) -> ScanProjection:
    """Validate an untrusted Python object as a schema-major-1 scan projection."""
    candidate: object = _projection_mapping(value) if isinstance(value, ScanProjection) else value
    _measure_graph(candidate)
    try:
        validated = _PROJECTION_ADAPTER.validate_python(candidate, strict=True)
        return _public_projection(validated)
    except (ValidationError, TypeError, ValueError):
        raise ScanValidationError("scan projection object is invalid") from None


@implements("REQ042")
def load_scan_projection_json(data: str | bytes) -> ScanProjection:
    """Load a bounded, forward-compatible scan projection JSON document."""
    if isinstance(data, bytes):
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise ScanValidationError("scan projection document is too large")
        if data.startswith(b"\xef\xbb\xbf"):
            raise ScanValidationError("scan projection document must not contain a BOM")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ScanValidationError("scan projection document is not valid UTF-8") from None
    elif isinstance(data, str):
        if data.startswith("\ufeff"):
            raise ScanValidationError("scan projection document must not contain a BOM")
        try:
            size = len(data.encode("utf-8"))
        except UnicodeEncodeError:
            raise ScanValidationError("scan projection document contains invalid Unicode") from None
        if size > _MAX_DOCUMENT_BYTES:
            raise ScanValidationError("scan projection document is too large")
        text = data
    else:
        raise ScanValidationError("scan projection JSON must be text or bytes")
    try:
        candidate = json.loads(text, object_pairs_hook=_object, parse_constant=_non_finite)
    except (json.JSONDecodeError, _DuplicateKeyError, RecursionError, ValueError):
        raise ScanValidationError("scan projection document is not valid JSON") from None
    return validate_scan_projection(candidate)


@implements("REQ042")
def dump_scan_projection_json(projection: ScanProjection) -> bytes:
    """Serialize a projection as deterministic scan-projection-v1 JSON bytes."""
    if not isinstance(projection, ScanProjection):
        raise ScanValidationError("only ScanProjection values can be serialized")
    validated = validate_scan_projection(projection)
    try:
        text = json.dumps(
            _projection_mapping(validated),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        return f"{text}\n".encode()
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ScanValidationError("scan projection cannot be serialized") from None


@implements("REQ042")
def scan_projection_json_schema() -> dict[str, object]:
    """Return a fresh copy of the packaged scan-projection-v1 JSON Schema."""
    schema_file = resources.files("slygentify").joinpath("schemas/scan-projection-v1.schema.json")
    try:
        value = json.loads(schema_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ScanValidationError("packaged scan projection schema is unavailable") from None
    if not isinstance(value, dict):
        raise ScanValidationError("packaged scan projection schema is invalid")
    return cast(dict[str, object], value)
