"""Private validation and JSON adapters for the public scan contract."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from importlib import resources
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from slygentify._errors import ScanValidationError
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

_MAX_DOCUMENT_BYTES = 128 * 1024 * 1024
_MAX_NESTING = 32
_MAX_STRING_BYTES = 4 * 1024 * 1024
_MAX_COLLECTION_ENTRIES = 100_000
_MAX_GRAPH_NODES = 5_000_000


class _InputModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _RepositoryInput(_InputModel):
    id: str
    root: str
    kind: str
    evidence_ids: list[str]


class _ComponentInput(_InputModel):
    id: str
    path: str
    ecosystem: str
    kind: str
    evidence_ids: list[str]
    ecosystems: list[str] | None = None
    role: Literal["unknown", "auxiliary"] = "unknown"


class _ComponentRelationshipInput(_InputModel):
    id: str
    kind: str
    source_id: str
    target_id: str
    classification: Literal["verified", "inferred", "recommended", "unknown"]
    evidence_ids: list[str]


class _EvidenceInput(_InputModel):
    id: str
    source_kind: str
    location: str
    locator: str | None = None
    observation: str
    verification_method: str | None = None


class _FindingInput(_InputModel):
    id: str
    code: str
    classification: Literal["verified", "inferred", "recommended", "unknown"]
    subject_id: str
    summary: str
    evidence_ids: list[str]


class _DiagnosticInput(_InputModel):
    id: str
    code: str
    subject_id: str | None = None
    location: str | None = None
    message: str
    evidence_ids: list[str]


class _SkippedScopeInput(_InputModel):
    scope: str
    reason: str
    effective_limit: int | Literal["unlimited"] | None = None
    consumed: int | None = None
    omitted_scope: str


class _ScanInput(_InputModel):
    schema_version: Literal[1]
    producer_version: str
    completion: Literal["complete", "partial"]
    repository: _RepositoryInput
    components: list[_ComponentInput]
    evidence: list[_EvidenceInput]
    findings: list[_FindingInput]
    diagnostics: list[_DiagnosticInput]
    skipped_scopes: list[_SkippedScopeInput]
    relationships: list[_ComponentRelationshipInput] = Field(default_factory=list)


_SCAN_ADAPTER: TypeAdapter[_ScanInput] = TypeAdapter(_ScanInput)


class _DuplicateKeyError(ValueError):
    pass


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(key)
        value[key] = item
    return value


def _non_finite(value: str) -> object:
    raise ValueError(value)


def _measure_graph(value: object) -> None:
    stack: list[tuple[object, int, bool]] = [(value, 1, False)]
    active_containers: set[int] = set()
    nodes = 0
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active_containers.remove(id(current))
            continue
        nodes += 1
        if nodes > _MAX_GRAPH_NODES:
            raise ScanValidationError("scan object graph is too large")
        if depth > _MAX_NESTING:
            raise ScanValidationError("scan object nesting is too deep")
        if isinstance(current, str):
            try:
                size = len(current.encode("utf-8"))
            except UnicodeEncodeError as error:
                raise ScanValidationError("scan contains invalid Unicode") from error
            if size > _MAX_STRING_BYTES:
                raise ScanValidationError("scan contains an oversized string")
            continue
        if isinstance(current, float) and not math.isfinite(current):
            raise ScanValidationError("scan contains a non-finite number")
        if isinstance(current, Mapping):
            if len(current) > _MAX_COLLECTION_ENTRIES:
                raise ScanValidationError("scan contains an oversized object")
            identity = id(current)
            if identity in active_containers:
                raise ScanValidationError("scan object graph contains a cycle")
            active_containers.add(identity)
            stack.append((current, depth, True))
            for key, item in current.items():
                nodes += 1
                if nodes > _MAX_GRAPH_NODES:
                    raise ScanValidationError("scan object graph is too large")
                if isinstance(key, str):
                    try:
                        key_size = len(key.encode("utf-8"))
                    except UnicodeEncodeError as error:
                        raise ScanValidationError("scan contains invalid Unicode") from error
                    if key_size > _MAX_STRING_BYTES:
                        raise ScanValidationError("scan contains an oversized string")
                stack.append((item, depth + 1, False))
            continue
        if isinstance(current, (list, tuple)):
            if len(current) > _MAX_COLLECTION_ENTRIES:
                raise ScanValidationError("scan contains an oversized collection")
            identity = id(current)
            if identity in active_containers:
                raise ScanValidationError("scan object graph contains a cycle")
            active_containers.add(identity)
            stack.append((current, depth, True))
            stack.extend((item, depth + 1, False) for item in current)


def _optional(value: dict[str, object], name: str, item: object | None) -> None:
    if item is not None:
        value[name] = item


def _result_mapping(result: ScanResult) -> dict[str, object]:
    repository = {
        "id": result.repository.id,
        "root": result.repository.root,
        "kind": result.repository.kind,
        "evidence_ids": list(result.repository.evidence_ids),
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
        for item in result.components
    ]
    evidence: list[dict[str, object]] = []
    for evidence_item in result.evidence:
        record: dict[str, object] = {
            "id": evidence_item.id,
            "source_kind": evidence_item.source_kind,
            "location": evidence_item.location,
        }
        _optional(record, "locator", evidence_item.locator)
        record["observation"] = evidence_item.observation
        _optional(record, "verification_method", evidence_item.verification_method)
        evidence.append(record)
    findings = [
        {
            "id": item.id,
            "code": item.code,
            "classification": item.classification,
            "subject_id": item.subject_id,
            "summary": item.summary,
            "evidence_ids": list(item.evidence_ids),
        }
        for item in result.findings
    ]
    diagnostics: list[dict[str, object]] = []
    for diagnostic_item in result.diagnostics:
        record = {"id": diagnostic_item.id, "code": diagnostic_item.code}
        _optional(record, "subject_id", diagnostic_item.subject_id)
        _optional(record, "location", diagnostic_item.location)
        record["message"] = diagnostic_item.message
        record["evidence_ids"] = list(diagnostic_item.evidence_ids)
        diagnostics.append(record)
    skipped_scopes: list[dict[str, object]] = []
    for skipped_item in result.skipped_scopes:
        record = {"scope": skipped_item.scope, "reason": skipped_item.reason}
        _optional(record, "effective_limit", skipped_item.effective_limit)
        _optional(record, "consumed", skipped_item.consumed)
        record["omitted_scope"] = skipped_item.omitted_scope
        skipped_scopes.append(record)
    relationships = [
        {
            "id": item.id,
            "kind": item.kind,
            "source_id": item.source_id,
            "target_id": item.target_id,
            "classification": item.classification,
            "evidence_ids": list(item.evidence_ids),
        }
        for item in result.relationships
    ]
    return {
        "schema_version": result.schema_version,
        "producer_version": result.producer_version,
        "completion": result.completion,
        "repository": repository,
        "components": components,
        "relationships": relationships,
        "evidence": evidence,
        "findings": findings,
        "diagnostics": diagnostics,
        "skipped_scopes": skipped_scopes,
    }


def _public(value: _ScanInput) -> ScanResult:
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
    return ScanResult(
        schema_version=value.schema_version,
        producer_version=value.producer_version,
        completion=value.completion,
        repository=repository,
        components=components,
        evidence=evidence,
        findings=findings,
        diagnostics=diagnostics,
        skipped_scopes=skipped_scopes,
        relationships=relationships,
    )


@implements("REQ018")
def validate_scan(value: object) -> ScanResult:
    """Validate an untrusted Python object as a schema-major-1 scan."""
    candidate: object = _result_mapping(value) if isinstance(value, ScanResult) else value
    _measure_graph(candidate)
    try:
        validated = _SCAN_ADAPTER.validate_python(candidate, strict=True)
        return _public(validated)
    except (ValidationError, TypeError, ValueError):
        raise ScanValidationError("scan object is invalid") from None


@implements("REQ018")
def load_scan_json(data: str | bytes) -> ScanResult:
    """Load a bounded canonical or forward-compatible scan JSON document."""
    if isinstance(data, bytes):
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise ScanValidationError("scan document is too large")
        if data.startswith(b"\xef\xbb\xbf"):
            raise ScanValidationError("scan document must not contain a BOM")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ScanValidationError("scan document is not valid UTF-8") from None
    elif isinstance(data, str):
        if data.startswith("\ufeff"):
            raise ScanValidationError("scan document must not contain a BOM")
        try:
            size = len(data.encode("utf-8"))
        except UnicodeEncodeError:
            raise ScanValidationError("scan document contains invalid Unicode") from None
        if size > _MAX_DOCUMENT_BYTES:
            raise ScanValidationError("scan document is too large")
        text = data
    else:
        raise ScanValidationError("scan JSON must be text or bytes")
    try:
        value = json.loads(text, object_pairs_hook=_object, parse_constant=_non_finite)
    except (json.JSONDecodeError, _DuplicateKeyError, RecursionError, ValueError):
        raise ScanValidationError("scan document is not valid JSON") from None
    return validate_scan(value)


@implements("REQ018")
def dump_scan_json(result: ScanResult) -> bytes:
    """Serialize a scan result as deterministic schema-major-1 JSON bytes."""
    if not isinstance(result, ScanResult):
        raise ScanValidationError("only ScanResult values can be serialized")
    validated = validate_scan(result)
    try:
        text = json.dumps(
            _result_mapping(validated),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        return f"{text}\n".encode()
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ScanValidationError("scan result cannot be serialized") from None


@implements("REQ018")
def scan_json_schema() -> dict[str, object]:
    """Return a fresh copy of the packaged schema-major-1 JSON Schema."""
    schema_file = resources.files("slygentify").joinpath("schemas/scan-v1.schema.json")
    try:
        value = json.loads(schema_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ScanValidationError("packaged scan schema is unavailable") from None
    if not isinstance(value, dict):
        raise ScanValidationError("packaged scan schema is invalid")
    return cast(dict[str, object], value)
