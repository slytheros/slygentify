"""Tests for deterministic private provenance state behavior."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import slygentify._provenance as provenance
from slygentify import plan_initialization, scan_repository
from slygentify._configuration import load_configuration
from slygentify._generation import generate_agents_document
from slygentify._provenance import (
    Artifact,
    Derivation,
    StateConfiguration,
    StateDocument,
    StateError,
    StateInput,
    StateLimit,
    apply_state_write,
    dump_state_json,
    load_state_json,
    plan_state_write,
    read_state_bytes,
    state_from_scan,
    state_json_schema,
)
from tests.scan_samples import sample_result


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _state() -> StateDocument:
    names = (
        "max_depth",
        "max_entries",
        "max_file_bytes",
        "max_total_bytes",
        "max_elapsed_seconds",
        "max_open_files",
        "max_memory_bytes",
    )
    inputs = (
        StateInput(
            "input-a",
            "manifest",
            "pyproject.toml",
            "project.name",
            _digest("raw"),
            None,
            "python.project",
            1,
        ),
    )
    return StateDocument(
        2,
        "0.1.0",
        StateConfiguration("slygentify.toml", _digest("configuration")),
        tuple(StateLimit(name, 1, 1, 1, "default") for name in names),
        inputs,
        (Derivation("component-a", "python.project", "verified", ("input-a",)),),
        (Artifact("AGENTS.md", _digest("artifact"), ("input-a",)),),
        "complete",
        (),
    )


@pytest.mark.verifies("TST036")
def test_state_round_trip_is_canonical_and_schema_is_packaged() -> None:
    state = _state()
    data = dump_state_json(state)

    assert data.endswith(b"\n")
    assert b"\r" not in data
    assert load_state_json(data) == state
    assert dump_state_json(load_state_json(data)) == data
    schema = state_json_schema()
    assert schema["$id"] == "schemas/state-v2.schema.json"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


@pytest.mark.verifies("TST036")
def test_state_loader_classifies_parser_recursion_without_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recursive_parse(*args: object, **kwargs: object) -> object:
        raise RecursionError("untrusted nesting")

    monkeypatch.setattr(json, "loads", recursive_parse)
    with pytest.raises(StateError) as raised:
        load_state_json(b"{}")

    assert raised.value.category == "state.invalid-json"
    assert "untrusted nesting" not in str(raised.value)


@pytest.mark.verifies("TST036")
def test_legacy_v1_state_remains_readable() -> None:
    state = _state()
    legacy = StateDocument(
        1,
        state.producer_version,
        state.configuration,
        state.effective_limits,
        state.inputs,
        state.derivations,
        state.artifacts,
        state.completion,
        state.skipped_scopes,
    )

    loaded = load_state_json(dump_state_json(legacy))

    assert loaded.schema_version == 1
    assert loaded.artifacts[0].ownership == "document"


@pytest.mark.verifies("TST036")
@pytest.mark.parametrize(
    "document",
    [
        b"\xef\xbb\xbf{}",
        b"{",
        b'{"schema_version": 1, "schema_version": 1}',
        b'{"schema_version": 1, "producer_version": "x", "effective_limits": [], "inputs": [], "derivations": [], "artifacts": [], "completion": NaN, "skipped_scopes": []}',
        123,
    ],
)
def test_state_loader_rejects_invalid_json_forms(document: object) -> None:
    with pytest.raises(StateError):
        load_state_json(document)  # type: ignore[arg-type]


@pytest.mark.verifies("TST036")
def test_state_reader_ignores_unknown_same_major_fields_and_rejects_bad_references() -> None:
    document = json.loads(dump_state_json(_state()))
    document["future"] = True
    document["inputs"][0]["future"] = True
    assert load_state_json(json.dumps(document)).producer_version == "0.1.0"

    document["derivations"][0]["evidence_ids"] = ["missing"]
    with pytest.raises(StateError):
        load_state_json(json.dumps(document))


@pytest.mark.verifies("TST036")
def test_state_v2_artifact_ownership_is_validated() -> None:
    document = json.loads(dump_state_json(_state()))
    document["artifacts"][0].pop("location")
    with pytest.raises(StateError):
        load_state_json(json.dumps(document))

    document = json.loads(dump_state_json(_state()))
    document["artifacts"][0]["ownership"] = "unsupported"
    with pytest.raises(StateError):
        load_state_json(json.dumps(document))

    with pytest.raises(StateError, match="schema version"):
        dump_state_json(
            replace(
                _state(),
                schema_version=1,
                artifacts=(replace(_state().artifacts[0], ownership="section"),),
            )
        )


@pytest.mark.verifies("TST036")
@pytest.mark.parametrize(
    ("encoded_version", "category"),
    [
        ("3", "state.unsupported-schema"),
        ("3.0", "state.unsupported-schema"),
        ("3e0", "state.unsupported-schema"),
        ("3.5", "state.invalid-structure"),
        ("2.0", "state.invalid-structure"),
        ("true", "state.invalid-structure"),
        ('"3"', "state.invalid-structure"),
    ],
)
def test_state_loader_classifies_schema_version_numbers(
    encoded_version: str, category: str
) -> None:
    with pytest.raises(StateError) as captured:
        load_state_json('{"schema_version":' + encoded_version + "}")

    assert captured.value.category == category


@pytest.mark.verifies("TST036")
def test_state_loader_rejects_order_digest_and_path_violations() -> None:
    document = json.loads(dump_state_json(_state()))
    document["inputs"][0]["sha256"] = "UPPER"
    with pytest.raises(StateError):
        load_state_json(json.dumps(document))

    document = json.loads(dump_state_json(_state()))
    document["artifacts"][0]["location"] = "../AGENTS.md"
    with pytest.raises(StateError):
        load_state_json(json.dumps(document))

    document = json.loads(dump_state_json(_state()))
    document["effective_limits"] = list(reversed(document["effective_limits"]))
    with pytest.raises(StateError):
        load_state_json(json.dumps(document))


@pytest.mark.verifies("TST037")
def test_state_write_create_no_change_replace_and_malformed_refusal(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    state = _state()

    plan = plan_state_write(root, state)
    assert plan.action == "create"
    assert apply_state_write(plan)

    no_change = plan_state_write(root, state)
    assert no_change.action == "no_change"
    assert not apply_state_write(no_change)

    changed = StateDocument(
        state.schema_version,
        "0.1.1",
        state.configuration,
        state.effective_limits,
        state.inputs,
        state.derivations,
        state.artifacts,
        state.completion,
        state.skipped_scopes,
    )
    replace = plan_state_write(root, changed)
    assert replace.action == "replace"
    assert apply_state_write(replace)
    assert (
        load_state_json((root / ".slygentify" / "state.json").read_bytes()).producer_version
        == "0.1.1"
    )

    (root / ".slygentify" / "state.json").write_text("bad", encoding="utf-8")
    with pytest.raises(StateError, match="existing"):
        plan_state_write(root, state)


@pytest.mark.verifies("TST037", "TST055")
def test_state_write_rebuilds_only_bounded_invalid_state_with_race_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    target = root / ".slygentify" / "state.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"invalid-state")

    recovery = plan_state_write(root, _state(), replace_invalid=True)
    assert recovery.action == "replace"
    target.write_bytes(b"changed-state")
    with pytest.raises(StateError, match="changed concurrently"):
        apply_state_write(recovery)

    target.write_text('{"schema_version": 3}', encoding="utf-8")
    with pytest.raises(StateError) as future:
        plan_state_write(root, _state(), replace_invalid=True)
    assert future.value.category == "state.unsupported-schema"

    target.write_bytes(b"large")
    monkeypatch.setattr(provenance, "_MAX_BYTES", 4)
    with pytest.raises(StateError) as oversized:
        read_state_bytes(target)
    assert oversized.value.category == "state.too-large"
    with pytest.raises(StateError) as oversized_data:
        load_state_json(b"large")
    assert oversized_data.value.category == "state.too-large"

    with pytest.raises(StateError) as missing:
        read_state_bytes(root / "missing.json")
    assert missing.value.category == "state.unreadable"

    monkeypatch.setattr(provenance, "_MAX_BYTES", 128 * 1024 * 1024)
    original_read_bytes = Path.read_bytes

    def failing_read_bytes(path: Path) -> bytes:
        if path == target:
            raise OSError("unreadable")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", failing_read_bytes)
    with pytest.raises(StateError) as unreadable:
        read_state_bytes(target)
    assert unreadable.value.category == "state.unreadable"


@pytest.mark.verifies("TST037")
def test_state_write_refuses_unsafe_and_changed_targets(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".slygentify").write_text("not a directory", encoding="utf-8")
    with pytest.raises(StateError, match="directory"):
        apply_state_write(plan_state_write(root, _state()))

    (root / ".slygentify").unlink()
    first = plan_state_write(root, _state())
    (root / ".slygentify").mkdir()
    (root / ".slygentify" / "state.json").write_bytes(b"{}")
    with pytest.raises(StateError, match="changed"):
        apply_state_write(first)


@pytest.mark.verifies("TST036")
def test_state_from_scan_uses_only_already_read_regular_file_bytes(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    result = sample_result()
    location = result.evidence[0].location
    state = state_from_scan(result, load_configuration(root), {location: b"evidence"})

    assert state.inputs[0].id == result.evidence[0].id
    assert all(
        set(item.evidence_ids) <= {value.id for value in state.inputs} for item in state.derivations
    )
    with pytest.raises(StateError):
        state_from_scan(object(), load_configuration(root), {})  # type: ignore[arg-type]


@pytest.mark.verifies("TST036")
def test_initialization_provenance_captures_real_scanner_inputs(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    manifest = b"[project]\nname = 'sentinel-provenance-secret'\nrequires-python = '>=3.11'\n"
    (root / "pyproject.toml").write_bytes(manifest)

    first = plan_initialization(root)
    repeated = plan_initialization(root)
    state = load_state_json(first.state_json)
    guidance = generate_agents_document(scan_repository(root))
    input_ids = {item.id for item in state.inputs}
    artifact = next(item for item in state.artifacts if item.location == "AGENTS.md")

    assert first.state_json == repeated.state_json
    assert state.inputs
    assert artifact.evidence_ids == tuple(
        item for item in guidance.evidence_ids if item in input_ids
    )
    assert artifact.evidence_ids
    assert all(item in input_ids for item in artifact.evidence_ids)
    assert all(set(item.evidence_ids) <= input_ids for item in state.derivations)
    assert all(
        item.sha256 == hashlib.sha256(manifest).hexdigest()
        for item in state.inputs
        if item.location == "pyproject.toml"
    )
    assert b"sentinel-provenance-secret" not in first.state_json

    (root / "pyproject.toml").write_bytes(manifest.replace(b">=3.11", b">=3.12"))
    changed = load_state_json(plan_initialization(root).state_json)
    assert {item.sha256 for item in changed.inputs if item.location == "pyproject.toml"} != {
        item.sha256 for item in state.inputs if item.location == "pyproject.toml"
    }


@pytest.mark.verifies("TST036", "TST037")
def test_state_defensive_validation_and_write_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = _state()
    document = json.loads(dump_state_json(state))
    updates: tuple[Callable[[dict[str, object]], None], ...] = (
        lambda value: value.update({"configuration": {}}),
        lambda value: value.update({"effective_limits": []}),
        lambda value: value.update({"inputs": {}}),
        lambda value: value.update({"derivations": {}}),
        lambda value: value.update({"artifacts": {}}),
        lambda value: value.update({"completion": "bad"}),
        lambda value: value.update({"skipped_scopes": {}}),
    )
    for update in updates:
        candidate = cast(dict[str, object], json.loads(json.dumps(document)))
        update(candidate)
        with pytest.raises(StateError):
            load_state_json(json.dumps(candidate))
    invalid_paths: tuple[object, ...] = ({}, [], "", "../x", "a\\b")
    for invalid_path in invalid_paths:
        with pytest.raises(StateError):
            provenance._path(invalid_path)
    for invalid_text in (None, "", 1):
        with pytest.raises(StateError):
            provenance._text(invalid_text)
    for invalid_digest in ("x", "A" * 64, 1):
        with pytest.raises(StateError):
            provenance._digest(invalid_digest)
    for invalid_limit in (False, 0, -1):
        with pytest.raises(StateError):
            provenance._limit(invalid_limit)
    for invalid_refs in (None, ["missing"], ["a", "a"]):
        with pytest.raises(StateError):
            provenance._refs(invalid_refs, {"a"})
    with pytest.raises(StateError):
        dump_state_json(object())  # type: ignore[arg-type]
    with pytest.raises(StateError):
        load_state_json("\ud800")

    root = tmp_path / "repository"
    root.mkdir()
    plan = plan_state_write(root, state)
    monkeypatch.setattr(
        "slygentify._provenance.os.replace", lambda *args: (_ for _ in ()).throw(OSError("no"))
    )
    with pytest.raises(StateError, match="unable"):
        apply_state_write(plan)
    assert not list(root.glob(".slygentify/.state-*"))
    with pytest.raises(StateError):
        apply_state_write(object())  # type: ignore[arg-type]


@pytest.mark.verifies("TST036")
def test_state_optional_and_ordered_fields_are_validated() -> None:
    document = json.loads(dump_state_json(_state()))
    document.pop("configuration")
    document["inputs"][0]["value_sha256"] = _digest("value")
    document["inputs"][0]["locator"] = "project.name"
    document["effective_limits"][0]["requested"] = "unlimited"
    document["effective_limits"][0]["effective"] = "unlimited"
    document["effective_limits"][0]["source"] = "configuration"
    assert load_state_json(json.dumps(document)).configuration is None

    for field, value in (("rule_version", True), ("classification", "bad"), ("evidence_ids", "a")):
        candidate = json.loads(json.dumps(document))
        source = candidate["inputs"][0] if field == "rule_version" else candidate["derivations"][0]
        source[field] = value
        with pytest.raises(StateError):
            load_state_json(json.dumps(candidate))
