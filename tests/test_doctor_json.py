"""Tests for the public doctor Python, JSON, and schema contract."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pytest

import slygentify
import slygentify._doctor_serialization as serialization
from slygentify import (
    DoctorDiagnostic,
    DoctorInputError,
    DoctorOperationalError,
    DoctorResult,
    Evidence,
    Repository,
    SkippedScope,
    doctor_json_schema,
    dump_doctor_json,
    load_doctor_json,
    validate_doctor,
)
from slygentify.api import ScanValidationError


def sample_doctor_result() -> DoctorResult:
    evidence = (
        Evidence(
            id="evidence-1",
            source_kind="manifest",
            location="pyproject.toml",
            locator="project.name",
            observation="The project name is declared.",
            verification_method="static manifest parsing",
        ),
        Evidence(
            id="evidence-2",
            source_kind="doctor-provenance",
            location="AGENTS.md",
            locator=None,
            observation="Managed guidance differs from fresh generation.",
            verification_method=None,
        ),
    )
    diagnostics = (
        DoctorDiagnostic(
            id="diagnostic-info",
            code="doctor.guidance.unmanaged",
            severity="info",
            classification="unknown",
            subject_id="repository-1",
            location=None,
            problem="Guidance ownership is unknown.",
            effect="Freshness cannot be established.",
            remediation=None,
            evidence_ids=("evidence-1",),
            disposition="notice",
        ),
        DoctorDiagnostic(
            id="diagnostic-warning",
            code="doctor.tooling.drift",
            severity="warning",
            classification="verified",
            subject_id="repository-1",
            location="AGENTS.md",
            problem="Tooling knowledge changed.",
            effect="Managed workflow guidance may be stale.",
            remediation="Review and regenerate managed guidance.",
            evidence_ids=("evidence-2",),
        ),
    )
    skipped_scopes = (
        SkippedScope(
            scope=".gitignore",
            reason="invalid_gitignore",
            effective_limit=None,
            consumed=None,
            omitted_scope="ignored paths",
        ),
        SkippedScope(
            scope="vendor",
            reason="entry_limit",
            effective_limit=10,
            consumed=10,
            omitted_scope="vendor/**",
        ),
    )
    return DoctorResult(
        schema_version=1,
        producer_version="0.1.0",
        completion="partial",
        repository=Repository(
            id="repository-1",
            root=".",
            kind="git",
            evidence_ids=("evidence-1",),
        ),
        evidence=evidence,
        diagnostics=diagnostics,
        skipped_scopes=skipped_scopes,
    )


@pytest.mark.verifies("TST048")
def test_doctor_public_exports_and_round_trip() -> None:
    expected = {
        "DoctorDiagnostic",
        "DoctorInputError",
        "DoctorOperationalError",
        "DoctorResult",
        "doctor_json_schema",
        "doctor_repository",
        "dump_doctor_json",
        "load_doctor_json",
        "validate_doctor",
    }
    result = sample_doctor_result()

    assert expected <= set(slygentify.__all__)
    assert validate_doctor(result) == result
    assert load_doctor_json(dump_doctor_json(result)) == result


@pytest.mark.verifies("TST048")
def test_doctor_json_is_canonical_and_omits_absent_optionals() -> None:
    result = sample_doctor_result()
    encoded = dump_doctor_json(result)
    document = json.loads(encoded)

    assert encoded.endswith(b"\n")
    assert not encoded.startswith(b"\xef\xbb\xbf")
    assert list(document) == [
        "schema_version",
        "producer_version",
        "completion",
        "repository",
        "evidence",
        "diagnostics",
        "skipped_scopes",
    ]
    assert "locator" not in document["evidence"][1]
    assert "verification_method" not in document["evidence"][1]
    assert "location" not in document["diagnostics"][0]
    assert "remediation" not in document["diagnostics"][0]
    assert [item["disposition"] for item in document["diagnostics"]] == ["notice", "problem"]
    assert "effective_limit" not in document["skipped_scopes"][0]
    assert "consumed" not in document["skipped_scopes"][0]
    assert dump_doctor_json(result) == encoded


@pytest.mark.verifies("TST048")
def test_doctor_reader_ignores_additive_same_major_properties() -> None:
    document = json.loads(dump_doctor_json(sample_doctor_result()))
    document["future"] = {"value": True}
    document["repository"]["future"] = "value"
    document["evidence"][0]["future"] = 1
    document["diagnostics"][0]["future"] = []
    document["skipped_scopes"][0]["future"] = None

    assert validate_doctor(document) == sample_doctor_result()


@pytest.mark.verifies("TST048")
def test_doctor_reader_defaults_old_diagnostics_and_schema_rejects_invalid_disposition() -> None:
    result = sample_doctor_result()
    document = json.loads(dump_doctor_json(result))
    for diagnostic in document["diagnostics"]:
        del diagnostic["disposition"]
    expected = replace(
        result,
        diagnostics=tuple(replace(item, disposition="problem") for item in result.diagnostics),
    )

    assert validate_doctor(document) == expected
    invalid = json.loads(dump_doctor_json(result))
    invalid["diagnostics"][0]["disposition"] = "other"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(doctor_json_schema()).validate(invalid)


@pytest.mark.verifies("TST048")
@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"schema_version": True},
        {"schema_version": 1, "producer_version": ""},
    ],
)
def test_doctor_validation_translates_invalid_objects(value: object) -> None:
    with pytest.raises(DoctorInputError, match="doctor object is invalid"):
        validate_doctor(value)


@pytest.mark.verifies("TST048")
def test_doctor_validation_rejects_cycles_and_graph_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cyclic: dict[str, object] = {}
    cyclic["cycle"] = cyclic
    with pytest.raises(DoctorInputError, match="doctor object is invalid"):
        validate_doctor(cyclic)

    monkeypatch.setattr(
        serialization,
        "_measure_graph",
        lambda value: (_ for _ in ()).throw(ScanValidationError("too large")),
    )
    with pytest.raises(DoctorInputError, match="doctor object is invalid"):
        validate_doctor({})


@pytest.mark.verifies("TST048")
@pytest.mark.parametrize(
    ("value", "message"),
    [
        (b"\xef\xbb\xbf{}", "must not contain a BOM"),
        (b"\xff", "not valid UTF-8"),
        ("\ufeff{}", "must not contain a BOM"),
        ("\ud800", "contains invalid Unicode"),
        (object(), "must be text or bytes"),
        ("{", "not valid JSON"),
        ('{"schema_version": 1, "schema_version": 1}', "not valid JSON"),
        ('{"value": NaN}', "not valid JSON"),
    ],
)
def test_doctor_json_rejects_malformed_documents(value: object, message: str) -> None:
    with pytest.raises(DoctorInputError, match=message):
        load_doctor_json(value)  # type: ignore[arg-type]


@pytest.mark.verifies("TST048")
@pytest.mark.parametrize("value", [b"{}", "{}"])
def test_doctor_json_enforces_document_byte_limit(
    monkeypatch: pytest.MonkeyPatch, value: str | bytes
) -> None:
    monkeypatch.setattr(serialization, "_MAX_DOCUMENT_BYTES", 1)
    with pytest.raises(DoctorInputError, match="too large"):
        load_doctor_json(value)


@pytest.mark.verifies("TST048")
def test_doctor_dump_rejects_wrong_values_and_translates_serialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DoctorInputError, match="only DoctorResult"):
        dump_doctor_json({})  # type: ignore[arg-type]

    monkeypatch.setattr(
        "slygentify._doctor_serialization.json.dumps",
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("failure")),
    )
    with pytest.raises(DoctorOperationalError, match="cannot be serialized"):
        dump_doctor_json(sample_doctor_result())


class _SchemaResource:
    def __init__(self, value: str | Exception) -> None:
        self.value = value

    def joinpath(self, name: str) -> _SchemaResource:
        assert name == "schemas/doctor-v1.schema.json"
        return self

    def read_text(self, *, encoding: str) -> str:
        assert encoding == "utf-8"
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.mark.verifies("TST048")
def test_doctor_schema_is_packaged_valid_and_fresh() -> None:
    first = doctor_json_schema()
    second = doctor_json_schema()

    assert first == second
    assert first is not second
    jsonschema.Draft202012Validator.check_schema(second)
    jsonschema.Draft202012Validator(second).validate(
        json.loads(dump_doctor_json(sample_doctor_result()))
    )
    second["title"] = "changed"
    assert doctor_json_schema()["title"] != "changed"


@pytest.mark.verifies("TST048")
@pytest.mark.parametrize(
    "value",
    [OSError("missing"), "{", "[]"],
)
def test_doctor_schema_translates_packaging_failures(
    monkeypatch: pytest.MonkeyPatch, value: str | Exception
) -> None:
    monkeypatch.setattr(
        "slygentify._doctor_serialization.resources.files",
        lambda package: _SchemaResource(value),
    )
    with pytest.raises(DoctorOperationalError, match="packaged doctor schema"):
        doctor_json_schema()


@pytest.mark.verifies("TST048")
def test_doctor_schema_and_adapter_reject_the_same_invalid_diagnostic() -> None:
    document: dict[str, Any] = json.loads(dump_doctor_json(sample_doctor_result()))
    del document["diagnostics"][0]["severity"]

    with pytest.raises(DoctorInputError):
        validate_doctor(document)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(doctor_json_schema()).validate(document)


@pytest.mark.verifies("TST048")
def test_doctor_closed_producer_revalidates_model_invariants() -> None:
    result = sample_doctor_result()

    with pytest.raises(ValueError, match="canonical order"):
        replace(result, diagnostics=tuple(reversed(result.diagnostics)))
