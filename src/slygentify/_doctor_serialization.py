"""Validation and canonical JSON adapters for the public doctor contract."""

from __future__ import annotations

import json
from importlib import resources
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from slygentify._doctor import DoctorInputError, DoctorOperationalError
from slygentify._errors import ScanValidationError
from slygentify._serialization import (
    _MAX_DOCUMENT_BYTES,
    _DuplicateKeyError,
    _EvidenceInput,
    _InputModel,
    _measure_graph,
    _non_finite,
    _object,
    _optional,
    _RepositoryInput,
    _SkippedScopeInput,
)
from slygentify.models import (
    DoctorDiagnostic,
    DoctorResult,
    Evidence,
    Repository,
    SkippedScope,
)
from slygentify.traceability import implements


class _DoctorDiagnosticInput(_InputModel):
    id: str
    code: str
    severity: Literal["info", "warning", "error"]
    classification: Literal["verified", "inferred", "recommended", "unknown"]
    subject_id: str | None = None
    location: str | None = None
    problem: str
    effect: str
    remediation: str | None = None
    evidence_ids: list[str]
    category: str | None = None
    safety_rationale: str | None = None


class _DoctorInput(_InputModel):
    schema_version: Literal[1]
    producer_version: str
    completion: Literal["complete", "partial"]
    repository: _RepositoryInput
    evidence: list[_EvidenceInput]
    diagnostics: list[_DoctorDiagnosticInput]
    skipped_scopes: list[_SkippedScopeInput]


_DOCTOR_ADAPTER: TypeAdapter[_DoctorInput] = TypeAdapter(_DoctorInput)


def _doctor_mapping(result: DoctorResult) -> dict[str, object]:
    repository = {
        "id": result.repository.id,
        "root": result.repository.root,
        "kind": result.repository.kind,
        "evidence_ids": list(result.repository.evidence_ids),
    }
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
    diagnostics: list[dict[str, object]] = []
    for diagnostic_item in result.diagnostics:
        record = {
            "id": diagnostic_item.id,
            "code": diagnostic_item.code,
            "severity": diagnostic_item.severity,
            "classification": diagnostic_item.classification,
        }
        _optional(record, "subject_id", diagnostic_item.subject_id)
        _optional(record, "location", diagnostic_item.location)
        record["problem"] = diagnostic_item.problem
        record["effect"] = diagnostic_item.effect
        _optional(record, "remediation", diagnostic_item.remediation)
        record["evidence_ids"] = list(diagnostic_item.evidence_ids)
        _optional(record, "category", diagnostic_item.category)
        _optional(record, "safety_rationale", diagnostic_item.safety_rationale)
        diagnostics.append(record)
    skipped_scopes: list[dict[str, object]] = []
    for skipped_item in result.skipped_scopes:
        record = {"scope": skipped_item.scope, "reason": skipped_item.reason}
        _optional(record, "effective_limit", skipped_item.effective_limit)
        _optional(record, "consumed", skipped_item.consumed)
        record["omitted_scope"] = skipped_item.omitted_scope
        skipped_scopes.append(record)
    return {
        "schema_version": result.schema_version,
        "producer_version": result.producer_version,
        "completion": result.completion,
        "repository": repository,
        "evidence": evidence,
        "diagnostics": diagnostics,
        "skipped_scopes": skipped_scopes,
    }


def _public_doctor(value: _DoctorInput) -> DoctorResult:
    repository = Repository(
        id=value.repository.id,
        root=value.repository.root,
        kind=value.repository.kind,
        evidence_ids=tuple(value.repository.evidence_ids),
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
    diagnostics = tuple(
        DoctorDiagnostic(
            id=item.id,
            code=item.code,
            severity=item.severity,
            classification=item.classification,
            subject_id=item.subject_id,
            location=item.location,
            problem=item.problem,
            effect=item.effect,
            remediation=item.remediation,
            evidence_ids=tuple(item.evidence_ids),
            category=item.category,
            safety_rationale=item.safety_rationale,
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
    return DoctorResult(
        schema_version=value.schema_version,
        producer_version=value.producer_version,
        completion=value.completion,
        repository=repository,
        evidence=evidence,
        diagnostics=diagnostics,
        skipped_scopes=skipped_scopes,
    )


def _measure_doctor(value: object) -> None:
    try:
        _measure_graph(value)
    except ScanValidationError:
        raise DoctorInputError("doctor object is invalid") from None


@implements("REQ048")
def validate_doctor(value: object) -> DoctorResult:
    """Validate an untrusted Python object as a schema-major-1 doctor result."""

    candidate: object = _doctor_mapping(value) if isinstance(value, DoctorResult) else value
    _measure_doctor(candidate)
    try:
        validated = _DOCTOR_ADAPTER.validate_python(candidate, strict=True)
        return _public_doctor(validated)
    except (ValidationError, TypeError, ValueError):
        raise DoctorInputError("doctor object is invalid") from None


@implements("REQ048")
def load_doctor_json(data: str | bytes) -> DoctorResult:
    """Load bounded, forward-compatible schema-major-1 doctor JSON."""

    if isinstance(data, bytes):
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise DoctorInputError("doctor document is too large")
        if data.startswith(b"\xef\xbb\xbf"):
            raise DoctorInputError("doctor document must not contain a BOM")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise DoctorInputError("doctor document is not valid UTF-8") from None
    elif isinstance(data, str):
        if data.startswith("\ufeff"):
            raise DoctorInputError("doctor document must not contain a BOM")
        try:
            size = len(data.encode("utf-8"))
        except UnicodeEncodeError:
            raise DoctorInputError("doctor document contains invalid Unicode") from None
        if size > _MAX_DOCUMENT_BYTES:
            raise DoctorInputError("doctor document is too large")
        text = data
    else:
        raise DoctorInputError("doctor JSON must be text or bytes")
    try:
        candidate = json.loads(text, object_pairs_hook=_object, parse_constant=_non_finite)
    except (json.JSONDecodeError, _DuplicateKeyError, RecursionError, ValueError):
        raise DoctorInputError("doctor document is not valid JSON") from None
    return validate_doctor(candidate)


@implements("REQ048")
def dump_doctor_json(result: DoctorResult) -> bytes:
    """Serialize a doctor result as deterministic schema-major-1 JSON bytes."""

    if not isinstance(result, DoctorResult):
        raise DoctorInputError("only DoctorResult values can be serialized")
    validated = validate_doctor(result)
    try:
        text = json.dumps(
            _doctor_mapping(validated),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
        )
        return f"{text}\n".encode()
    except (TypeError, ValueError, UnicodeEncodeError):
        raise DoctorOperationalError("doctor result cannot be serialized") from None


@implements("REQ048")
def doctor_json_schema() -> dict[str, object]:
    """Return a fresh copy of the packaged schema-major-1 doctor JSON Schema."""

    schema_file = resources.files("slygentify").joinpath("schemas/doctor-v1.schema.json")
    try:
        value = json.loads(schema_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DoctorOperationalError("packaged doctor schema is unavailable") from None
    if not isinstance(value, dict):
        raise DoctorOperationalError("packaged doctor schema is invalid")
    return cast(dict[str, object], value)
