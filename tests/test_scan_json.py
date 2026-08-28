"""Tests for the versioned scan JSON and schema contract."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pytest

from slygentify import (
    Component,
    ComponentRelationship,
    Diagnostic,
    Evidence,
    Finding,
    Repository,
    ScanResult,
    ScanValidationError,
    SkippedScope,
    dump_scan_json,
    load_scan_json,
    scan_json_schema,
    validate_scan,
)
from slygentify import _serialization as serialization
from tests.scan_samples import sample_mapping, sample_result


@pytest.mark.verifies("TST018")
def test_validate_scan_accepts_public_values_and_ignores_same_major_unknown_properties() -> None:
    mapping = sample_mapping()
    mapping["future_top_level"] = {"future": True}
    repository = mapping["repository"]
    assert isinstance(repository, dict)
    repository["future_repository_field"] = "ignored"

    result = validate_scan(mapping)

    assert result == sample_result()
    assert validate_scan(result) == result
    loaded = load_scan_json(json.dumps(mapping))
    assert loaded == result
    assert "future_top_level" not in json.loads(dump_scan_json(loaded))
    assert "future_repository_field" not in json.loads(dump_scan_json(loaded))["repository"]


@pytest.mark.verifies("TST018", "TST046")
def test_scan_diagnostic_structure_round_trips_additively() -> None:
    result = sample_result()
    diagnostic = replace(
        result.diagnostics[0],
        category="inspection.invalid-input",
        problem="A manifest could not be validated",
        effect="Its package declarations were omitted",
        recovery="correct the manifest and rerun scan",
        safety_rationale="Scan is read-only and does not rewrite repository declarations",
    )
    structured = replace(result, diagnostics=(diagnostic,))

    document = json.loads(dump_scan_json(structured))

    assert document["diagnostics"][0]["category"] == "inspection.invalid-input"
    assert document["diagnostics"][0]["problem"] == "A manifest could not be validated"
    assert load_scan_json(json.dumps(document)) == structured


@pytest.mark.verifies("TST018")
@pytest.mark.parametrize(
    ("path", "invalid"),
    [
        (("schema_version",), "1"),
        (("producer_version",), 1),
        (("completion",), "done"),
        (("repository", "id"), 1),
        (("repository", "evidence_ids"), ("evidence_a",)),
        (("components",), ()),
        (("components", 0, "ecosystem"), 1),
        (("components", 0, "role"), "primary"),
        (("evidence", 0, "locator"), 1),
        (("findings", 0, "classification"), "certain"),
        (("diagnostics", 0, "subject_id"), 1),
        (("skipped_scopes", 0, "consumed"), 1.0),
    ],
)
def test_validate_scan_is_strict_for_every_public_field_shape(
    path: tuple[str | int, ...], invalid: object
) -> None:
    mapping: Any = sample_mapping()
    target = mapping
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid

    with pytest.raises(ScanValidationError) as captured:
        validate_scan(mapping)

    assert captured.value.__cause__ is None


@pytest.mark.verifies("TST018")
def test_validate_scan_translates_public_invariant_failures() -> None:
    mapping = sample_mapping()
    mapping["components"] = []

    with pytest.raises(ScanValidationError) as captured:
        validate_scan(mapping)

    assert captured.value.__cause__ is None


@pytest.mark.verifies("TST018")
def test_validate_scan_rejects_noncanonical_collection_order() -> None:
    mapping = sample_mapping()
    evidence = mapping["evidence"]
    assert isinstance(evidence, list)
    evidence.reverse()

    with pytest.raises(ScanValidationError):
        validate_scan(mapping)


@pytest.mark.verifies("TST018")
@pytest.mark.parametrize(
    ("constant", "limit", "value"),
    [
        ("_MAX_STRING_BYTES", 3, {"x": "four"}),
        ("_MAX_STRING_BYTES", 3, {"four": 1}),
        ("_MAX_COLLECTION_ENTRIES", 1, [1, 2]),
        ("_MAX_COLLECTION_ENTRIES", 1, {"one": 1, "two": 2}),
        ("_MAX_GRAPH_NODES", 2, {"one": 1}),
        ("_MAX_GRAPH_NODES", 1, {"x": 1}),
        ("_MAX_NESTING", 1, {"nested": {}}),
    ],
)
def test_object_resource_limits_apply_before_unknown_fields_are_ignored(
    monkeypatch: pytest.MonkeyPatch, constant: str, limit: int, value: object
) -> None:
    monkeypatch.setattr(serialization, constant, limit)

    with pytest.raises(ScanValidationError):
        validate_scan(value)


@pytest.mark.verifies("TST018")
def test_object_graph_rejects_cycles_and_invalid_unicode() -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    mapping_cycle: dict[str, object] = {}
    mapping_cycle["cycle"] = mapping_cycle

    with pytest.raises(ScanValidationError, match="cycle"):
        validate_scan(cycle)
    with pytest.raises(ScanValidationError, match="cycle"):
        validate_scan(mapping_cycle)
    with pytest.raises(ScanValidationError, match="Unicode"):
        validate_scan({"future": "\ud800"})
    with pytest.raises(ScanValidationError, match="Unicode"):
        validate_scan({"\ud800": True})
    with pytest.raises(ScanValidationError):
        validate_scan({1: True})
    for non_finite in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ScanValidationError, match="non-finite"):
            validate_scan({"future": non_finite})


@pytest.mark.verifies("TST018")
def test_dump_is_deterministic_canonical_utf8_and_omits_absent_optionals() -> None:
    result = sample_result()

    first = dump_scan_json(result)
    second = dump_scan_json(result)
    document = json.loads(first)

    assert first == second
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")
    assert b"\r" not in first and not first.startswith(b"\xef\xbb\xbf")
    assert first.index(b'"schema_version"') < first.index(b'"producer_version"')
    assert document["components"][0]["role"] == "unknown"
    assert "locator" not in document["evidence"][0]
    assert "verification_method" not in document["evidence"][1]
    assert "location" not in document["diagnostics"][0]
    assert "effective_limit" not in document["skipped_scopes"][0]
    assert "consumed" not in document["skipped_scopes"][0]
    assert load_scan_json(first) == result
    assert load_scan_json(first.decode()) == result


@pytest.mark.verifies("TST018")
@pytest.mark.parametrize(
    "data",
    [
        b"\xef\xbb\xbf{}",
        "\ufeff{}",
        b"\xff",
        "\ud800",
        123,
        "{",
        '{"schema_version": 1, "schema_version": 1}',
        '{"value": NaN}',
        '{"value": Infinity}',
        '{"value": -Infinity}',
    ],
)
def test_load_scan_json_rejects_unsafe_or_invalid_documents(data: object) -> None:
    with pytest.raises(ScanValidationError):
        load_scan_json(data)  # type: ignore[arg-type]


@pytest.mark.verifies("TST018")
@pytest.mark.parametrize("data", [b"{}", "{}"])
def test_load_scan_json_enforces_document_size(
    monkeypatch: pytest.MonkeyPatch, data: str | bytes
) -> None:
    monkeypatch.setattr(serialization, "_MAX_DOCUMENT_BYTES", 1)

    with pytest.raises(ScanValidationError, match="large"):
        load_scan_json(data)


@pytest.mark.verifies("TST018")
def test_load_scan_json_translates_parser_recursion(monkeypatch: pytest.MonkeyPatch) -> None:
    def recurse(*args: object, **kwargs: object) -> object:
        raise RecursionError

    monkeypatch.setattr("slygentify._serialization.json.loads", recurse)

    with pytest.raises(ScanValidationError):
        load_scan_json("{}")


@pytest.mark.verifies("TST018")
def test_dump_revalidates_closed_public_values() -> None:
    result = sample_result()
    object.__setattr__(result.repository, "kind", 7)

    with pytest.raises(ScanValidationError):
        dump_scan_json(result)
    with pytest.raises(ScanValidationError):
        dump_scan_json({})  # type: ignore[arg-type]


@pytest.mark.verifies("TST018")
@pytest.mark.parametrize("invalid", [float("nan"), "\ud800"])
def test_dump_translates_json_encoder_failures(
    monkeypatch: pytest.MonkeyPatch, invalid: object
) -> None:
    original = serialization._result_mapping
    calls = 0

    def mapping(result: ScanResult) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(result)
        return {"invalid": invalid}

    monkeypatch.setattr(serialization, "_result_mapping", mapping)

    with pytest.raises(ScanValidationError):
        dump_scan_json(sample_result())


@pytest.mark.verifies("TST018")
def test_schema_is_packaged_fresh_valid_and_accepts_canonical_documents() -> None:
    first = scan_json_schema()
    second = scan_json_schema()
    first["title"] = "changed"

    assert first is not second
    assert second["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert second["$id"] == "schemas/scan-v1.schema.json"
    jsonschema.Draft202012Validator.check_schema(second)
    jsonschema.Draft202012Validator(second).validate(json.loads(dump_scan_json(sample_result())))
    invalid = sample_mapping()
    repository = invalid["repository"]
    assert isinstance(repository, dict)
    repository["root"] = "../outside"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(second).validate(invalid)
    schema_path = Path(serialization.__file__).parent / "schemas" / "scan-v1.schema.json"
    assert schema_path.is_file()


@pytest.mark.verifies("TST018")
def test_schema_public_model_and_private_adapter_fields_do_not_drift() -> None:
    schema = scan_json_schema()
    generated = serialization._SCAN_ADAPTER.json_schema()
    public_fields = {field.name for field in fields(ScanResult)}
    properties = schema["properties"]
    required = schema["required"]

    assert isinstance(properties, dict)
    assert isinstance(required, list)
    assert set(properties) == public_fields
    assert set(required) == public_fields - {"relationships"}
    assert set(generated["properties"]) == public_fields
    supporting = {
        "repository": Repository,
        "component": Component,
        "componentRelationship": ComponentRelationship,
        "evidence": Evidence,
        "finding": Finding,
        "diagnostic": Diagnostic,
        "skippedScope": SkippedScope,
    }
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    optional_fields = {
        "repository": set(),
        "component": {"ecosystems", "role"},
        "componentRelationship": set(),
        "evidence": {"locator", "verification_method"},
        "finding": set(),
        "diagnostic": {
            "subject_id",
            "location",
            "category",
            "problem",
            "effect",
            "recovery",
            "safety_rationale",
        },
        "skippedScope": {"effective_limit", "consumed"},
    }
    for name, value_type in supporting.items():
        definition = definitions[name]
        assert isinstance(definition, dict)
        assert set(definition["properties"]) == {field.name for field in fields(value_type)}
        assert (
            set(definition["required"])
            == {field.name for field in fields(value_type)} - optional_fields[name]
        )


@pytest.mark.verifies("TST018")
@pytest.mark.parametrize("failure", [OSError("missing"), json.JSONDecodeError("bad", "x", 0)])
def test_schema_resource_failures_are_translated(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    class Resource:
        def joinpath(self, name: str) -> Resource:
            return self

        def read_text(self, *, encoding: str) -> str:
            raise failure

    monkeypatch.setattr("slygentify._serialization.resources.files", lambda package: Resource())

    with pytest.raises(ScanValidationError):
        scan_json_schema()


@pytest.mark.verifies("TST018")
def test_schema_resource_must_contain_an_object(monkeypatch: pytest.MonkeyPatch) -> None:
    resource = SimpleNamespace(
        joinpath=lambda name: SimpleNamespace(read_text=lambda **kwargs: "[]")
    )
    monkeypatch.setattr("slygentify._serialization.resources.files", lambda package: resource)

    with pytest.raises(ScanValidationError):
        scan_json_schema()
