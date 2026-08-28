"""Tests for deterministic guidance generation and initialization lifecycle."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import slygentify.initialization as initialization
from slygentify import InitializationError, apply_initialization, plan_initialization
from slygentify._generation import _render_paste_snippet, generate_agents_document
from slygentify._managed_section import (
    SECTION_BEGIN,
    SECTION_END,
    ManagedSectionError,
    append_managed_section,
    extract_managed_section,
    replace_managed_section,
)
from slygentify._provenance import (
    Artifact,
    StateDocument,
    StateError,
    StateWritePlan,
    _fingerprint,
    dump_state_json,
    load_state_json,
)
from slygentify.models import Component, Finding
from tests.scan_samples import sample_result


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'example'\nrequires-python = '>=3.11'\ndependencies = ['pytest']\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.verifies("TST001", "TST038")
def test_generator_is_deterministic_safe_and_explicit_about_partial_results() -> None:
    first = generate_agents_document(sample_result())
    second = generate_agents_document(sample_result())

    assert first == second
    assert "## How to use Slygentify" in first.markdown
    assert "## Bootstrap component index" in first.markdown
    assert "## Maintenance" in first.markdown
    assert "slygentify doctor ." in first.markdown
    assert "## Safety" in first.markdown
    assert "partial scan" in first.markdown
    assert "Cargo.toml" in first.markdown
    assert "vendor" not in first.markdown
    assert "A boundary remains unknown" not in first.markdown
    assert "Review the example diagnostic" not in first.markdown
    assert first.evidence_ids == ("evidence_b",)
    assert len(first.markdown.encode()) <= 4096
    assert _fingerprint("a" * 64) == "a" * 64


@pytest.mark.verifies("TST054")
def test_paste_snippet_is_deterministic_and_removes_document_only_content() -> None:
    guidance = generate_agents_document(sample_result())

    snippet = _render_paste_snippet(guidance.markdown)

    assert snippet.startswith("## Slygentify bootstrap guidance\n\n")
    assert "### How to use Slygentify" in snippet
    assert "### Bootstrap component index" in snippet
    assert "### Safety" in snippet
    assert "# AGENTS.md" not in snippet
    assert "managed-artifact lifecycle" not in snippet
    with pytest.raises(ValueError, match="unexpected document preamble"):
        _render_paste_snippet("not generated guidance\n")


@pytest.mark.verifies("TST039")
def test_managed_section_helpers_reject_malformed_or_changed_content() -> None:
    section = SECTION_BEGIN + b"managed\n" + SECTION_END

    assert append_managed_section(b"", section) == section
    with pytest.raises(ManagedSectionError, match="missing or duplicated"):
        extract_managed_section(SECTION_BEGIN + SECTION_BEGIN + SECTION_END)
    with pytest.raises(ManagedSectionError, match="malformed"):
        extract_managed_section(SECTION_END + SECTION_BEGIN)
    with pytest.raises(ManagedSectionError, match="changed"):
        replace_managed_section(section, "0" * 64, section)


@pytest.mark.verifies("TST038", "TST044")
def test_generator_orders_primary_components_and_applies_both_limits() -> None:
    result = sample_result()
    command = Finding(
        id="command",
        code="python.ci.command",
        classification="verified",
        subject_id="component_a",
        summary="run tests",
        evidence_ids=("evidence_b",),
    )
    components = [result.components[0]]
    for index, path in enumerate(
        ("z", "a/deep", "a", "b", "例/子", "tests/example", "c", "d", "e"), start=1
    ):
        components.append(
            Component(
                id=f"component_{index:02d}",
                path=path,
                ecosystem="generic",
                kind="package",
                evidence_ids=("evidence_b",),
                role="auxiliary" if path == "tests/example" else "unknown",
            )
        )
    expanded = result.__class__(
        schema_version=1,
        producer_version="0.1.0",
        completion="partial",
        repository=result.repository,
        components=tuple(sorted(components, key=lambda item: (item.id, item.path))),
        evidence=result.evidence,
        findings=tuple(
            sorted(
                (*result.findings, command),
                key=lambda item: (item.code, item.subject_id, item.id),
            )
        ),
        diagnostics=result.diagnostics,
        skipped_scopes=result.skipped_scopes,
        relationships=(),
    )
    bounded = generate_agents_document(expanded, max_component_entries=3)
    assert bounded.markdown.index("`.`") < bounded.markdown.index("`a`")
    assert bounded.markdown.index("`a`") < bounded.markdown.index("`b`")
    assert "`z`" not in bounded.markdown
    assert "`a/deep`" not in bounded.markdown
    assert "tests/example" not in bounded.markdown
    assert "Additional primary components omitted: 6" in bounded.markdown
    assert "run tests" not in bounded.markdown

    byte_bounded = generate_agents_document(
        expanded, max_bytes=1536, max_component_entries="unlimited"
    )
    assert len(byte_bounded.markdown.encode()) <= 1536
    assert "Additional primary components omitted" in byte_bounded.markdown

    unlimited = generate_agents_document(
        expanded, max_bytes="unlimited", max_component_entries="unlimited"
    )
    assert "`a/deep`" in unlimited.markdown
    assert "`例/子`" in unlimited.markdown


@pytest.mark.verifies("TST038", "TST044")
def test_generator_handles_no_primary_components_and_too_small_output() -> None:
    result = sample_result()
    no_primary = result.__class__(
        schema_version=result.schema_version,
        producer_version=result.producer_version,
        completion=result.completion,
        repository=result.repository,
        components=(),
        evidence=(result.evidence[0],),
        findings=(result.findings[0],),
        diagnostics=(),
        skipped_scopes=result.skipped_scopes,
        relationships=(),
    )

    guidance = generate_agents_document(no_primary)

    assert "No primary component was established" in guidance.markdown
    assert guidance.evidence_ids == ()
    no_evidence = replace(
        no_primary,
        components=(
            Component(
                id="component_without_evidence",
                path=".",
                ecosystem="generic",
                kind="repository",
                evidence_ids=(),
            ),
        ),
    )
    assert generate_agents_document(no_evidence).evidence_ids == ()
    with pytest.raises(ValueError, match="fixed bootstrap guidance"):
        generate_agents_document(no_primary, max_bytes=1)


@pytest.mark.verifies("TST002", "TST039")
def test_plan_and_apply_create_clean_and_invalid_state(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    created = plan_initialization(root)

    assert created.ownership == "new"
    assert created.can_apply
    assert created.agents_action == "create"
    assert created.state_action == "create"
    result = apply_initialization(created)
    assert result.changed_locations == ("AGENTS.md", ".slygentify/state.json")
    state = load_state_json((root / ".slygentify" / "state.json").read_bytes())
    assert state.artifacts[0].location == "AGENTS.md"

    clean = plan_initialization(root)
    assert clean.ownership == "clean-managed"
    assert clean.agents_action == "no_change"
    assert clean.state_action == "no_change"
    assert apply_initialization(clean).changed_locations == ()

    legacy_state = StateDocument(
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
    (root / ".slygentify" / "state.json").write_bytes(dump_state_json(legacy_state))
    upgraded = plan_initialization(root)
    assert upgraded.ownership == "clean-managed"
    assert upgraded.agents_action == "no_change"
    assert upgraded.state_action == "replace"
    assert apply_initialization(upgraded).changed_locations == (".slygentify/state.json",)

    (root / ".slygentify" / "state.json").write_text("{}", encoding="utf-8")
    invalid = plan_initialization(root)
    assert invalid.ownership == "invalid-state"
    assert not invalid.can_apply


@pytest.mark.verifies("TST039")
def test_find_git_root_and_planner_failure_paths(tmp_path: Path) -> None:
    with pytest.raises(InitializationError, match="cannot be resolved"):
        initialization.find_git_root(tmp_path / "missing")
    file = tmp_path / "file"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(InitializationError, match="not a directory"):
        initialization.find_git_root(file)
    with pytest.raises(InitializationError, match="no Git repository"):
        initialization.find_git_root(tmp_path)
    with pytest.raises(InitializationError) as error:
        plan_initialization(tmp_path)
    assert error.value.code == "initialization.scan"


@pytest.mark.verifies("TST003", "TST039")
def test_unmanaged_edited_missing_and_replace_lifecycle(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "AGENTS.md").write_text("human text", encoding="utf-8")
    unmanaged = plan_initialization(root)
    assert unmanaged.ownership == "unmanaged"
    assert not unmanaged.can_apply
    assert {item.disposition for item in unmanaged.diagnostics} == {"notice"}
    replacement = plan_initialization(root, replace=True)
    assert replacement.can_apply
    apply_initialization(replacement)

    (root / "AGENTS.md").write_text("edited", encoding="utf-8")
    edited = plan_initialization(root)
    assert edited.ownership == "human-edited"
    assert not edited.can_apply
    assert {item.disposition for item in edited.diagnostics} == {"notice"}
    assert plan_initialization(root, replace=True).can_apply


@pytest.mark.verifies("TST039")
def test_adopt_and_replace_are_mutually_exclusive_in_planning(tmp_path: Path) -> None:
    with pytest.raises(InitializationError, match="cannot be combined"):
        plan_initialization(_repository(tmp_path), adopt=True, replace=True)


@pytest.mark.verifies("TST039", "TST054")
def test_adopted_section_preserves_surrounding_guidance_and_rejects_section_edits(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    agents = root / "AGENTS.md"
    agents.write_bytes(b"# Team guidance\n\nKeep this human-owned text.\n")

    plan = plan_initialization(root, adopt=True)
    assert plan.ownership == "unmanaged"
    assert plan.can_apply
    assert plan.managed_section is not None
    assert "human-owned" not in plan.managed_section
    assert plan.agents_markdown.encode("utf-8").startswith(agents.read_bytes())
    assert apply_initialization(plan).changed_locations == ("AGENTS.md", ".slygentify/state.json")

    state = load_state_json((root / ".slygentify" / "state.json").read_bytes())
    assert state.schema_version == 2
    assert state.artifacts[0].ownership == "section"
    original = agents.read_bytes()
    agents.write_bytes(original + b"\nHuman note after adoption.\n")
    unchanged = plan_initialization(root)
    assert unchanged.ownership == "clean-managed"
    assert unchanged.agents_action == "no_change"

    (root / "package.json").write_text('{"name":"web"}\n', encoding="utf-8")
    refreshed = plan_initialization(root)
    assert refreshed.can_apply
    assert refreshed.agents_action == "replace"
    apply_initialization(refreshed)
    assert agents.read_bytes().endswith(b"\nHuman note after adoption.\n")

    edited = agents.read_bytes().replace(b"## Safety", b"## Human safety", 1)
    agents.write_bytes(edited)
    assert plan_initialization(root).ownership == "human-edited"
    agents.write_bytes(edited.replace(SECTION_END, b""))
    assert plan_initialization(root).ownership == "missing-managed-artifact"

    state_document = load_state_json((root / ".slygentify" / "state.json").read_bytes())
    section_stale = StateDocument(
        state_document.schema_version,
        state_document.producer_version,
        state_document.configuration,
        state_document.effective_limits,
        state_document.inputs,
        state_document.derivations,
        (
            Artifact(
                "AGENTS.md",
                "0" * 64,
                state_document.artifacts[0].evidence_ids,
                "section",
            ),
        ),
        state_document.completion,
        state_document.skipped_scopes,
    )
    assert refreshed.managed_section is not None
    (root / "AGENTS.md").write_bytes(
        b"# Team guidance\n\n"
        + refreshed.managed_section.encode("utf-8")
        + b"\nHuman note after adoption.\n"
    )
    (root / ".slygentify" / "state.json").write_bytes(dump_state_json(section_stale))
    assert plan_initialization(root).ownership == "recoverable-state"

    stale = StateDocument(
        state_document.schema_version,
        state_document.producer_version,
        state_document.configuration,
        state_document.effective_limits,
        state_document.inputs,
        state_document.derivations,
        (Artifact("AGENTS.md", "0" * 64, state_document.artifacts[0].evidence_ids),),
        state_document.completion,
        state_document.skipped_scopes,
    )
    state_target = root / ".slygentify" / "state.json"
    state_target.unlink()
    replacement = plan_initialization(root, replace=True)
    (root / "AGENTS.md").write_bytes(replacement.agents_markdown.encode("utf-8"))
    state_target.write_bytes(dump_state_json(stale))
    recoverable = plan_initialization(root)
    assert recoverable.ownership == "recoverable-state"

    (root / "AGENTS.md").unlink()
    missing = plan_initialization(root)
    assert missing.ownership == "missing-managed-artifact"
    assert not missing.can_apply
    assert plan_initialization(root, replace=True).can_apply


@pytest.mark.verifies("TST004", "TST039")
def test_unsafe_and_concurrent_state_are_refused(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "AGENTS.md").mkdir()
    unsafe = plan_initialization(root, replace=True)
    assert unsafe.ownership == "unsafe-entry"
    assert not unsafe.can_apply
    assert {item.disposition for item in unsafe.diagnostics} == {"problem"}
    (root / "AGENTS.md").rmdir()

    plan = plan_initialization(root)
    (root / "AGENTS.md").write_text("raced", encoding="utf-8")
    with pytest.raises(InitializationError, match="changed after planning") as error:
        apply_initialization(plan)
    assert error.value.code == "initialization.concurrent-change"
    assert error.value.changed_locations == ()


@pytest.mark.verifies("TST044")
def test_initialization_uses_committed_init_limits_and_detects_configuration_races(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    configuration = root / "slygentify.toml"
    configuration.write_text(
        """schema_version = 1
[init]
max_agents_bytes = 8192
max_component_entries = "unlimited"
""",
        encoding="utf-8",
    )
    plan = plan_initialization(root)

    assert plan.warnings == (
        "slygentify.toml raises or disables an AGENTS.md byte or component-entry limit.",
    )
    state = load_state_json(plan.state_json)
    assert state.configuration is not None
    assert state.configuration.sha256 == hashlib.sha256(configuration.read_bytes()).hexdigest()

    configuration.write_text(
        """schema_version = 1
[init]
max_agents_bytes = 4096
max_component_entries = 4
""",
        encoding="utf-8",
    )
    with pytest.raises(InitializationError, match="Repository state changed"):
        apply_initialization(plan)


@pytest.mark.verifies("TST039")
def test_state_failure_after_agents_reports_partial_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    plan = plan_initialization(root)
    monkeypatch.setattr(
        initialization, "apply_state_write", lambda _plan: (_ for _ in ()).throw(OSError())
    )

    with pytest.raises(InitializationError) as error:
        apply_initialization(plan)

    assert error.value.code == "initialization.partial-write"
    assert error.value.changed_locations == ("AGENTS.md",)
    assert (root / "AGENTS.md").is_file()


@pytest.mark.verifies("TST039")
def test_internal_write_and_apply_defensive_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    target = root / "AGENTS.md"
    target.write_text("first", encoding="utf-8")
    with pytest.raises(OSError):
        initialization._action(target.parent, b"x")
    with pytest.raises(InitializationError, match="changed concurrently"):
        initialization._write_agents(root, b"next", "replace", hashlib.sha256(b"other").hexdigest())
    target.unlink()
    with pytest.raises(InitializationError, match="was removed"):
        initialization._write_agents(root, b"next", "replace", None)
    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(InitializationError, match="Unable to write"):
        initialization._write_agents(root, b"next", "create", None)
    with pytest.raises(InitializationError, match="plan is invalid"):
        apply_initialization(object())  # type: ignore[arg-type]


@pytest.mark.verifies("TST039")
def test_remaining_ownership_and_apply_failure_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    provenance_parent = root / ".slygentify"
    provenance_parent.write_text("unsafe", encoding="utf-8")
    assert plan_initialization(root).ownership == "unsafe-entry"
    provenance_parent.unlink()
    provenance_parent.mkdir()
    (provenance_parent / "state.json").mkdir()
    assert plan_initialization(root).ownership == "unsafe-entry"
    (provenance_parent / "state.json").rmdir()
    provenance_parent.rmdir()

    apply_initialization(plan_initialization(root))
    state = load_state_json((provenance_parent / "state.json").read_bytes())
    no_artifact = StateDocument(
        state.schema_version,
        state.producer_version,
        state.configuration,
        state.effective_limits,
        state.inputs,
        state.derivations,
        (),
        state.completion,
        state.skipped_scopes,
    )
    (provenance_parent / "state.json").write_bytes(dump_state_json(no_artifact))
    assert plan_initialization(root).ownership == "unmanaged"
    apply_initialization(plan_initialization(root, replace=True))

    (root / "AGENTS.md").unlink()
    (root / "AGENTS.md").mkdir()
    with pytest.raises(InitializationError, match="became unsafe"):
        initialization._write_agents(root, b"next", "replace", None)
    (root / "AGENTS.md").rmdir()

    plan = plan_initialization(root, replace=True)
    original_plan_state_write = cast(
        Callable[[Path, StateDocument], StateWritePlan],
        initialization.__dict__["plan_state_write"],
    )
    calls = 0

    def failing_second_plan(root_arg: Path, state_arg: StateDocument) -> StateWritePlan:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_plan_state_write(root_arg, state_arg)
        raise StateError()

    monkeypatch.setattr("slygentify.initialization.plan_state_write", failing_second_plan)
    with pytest.raises(InitializationError, match="changed after planning"):
        apply_initialization(plan)
    monkeypatch.undo()

    clean = plan_initialization(root, replace=True)
    calls = 0

    def mismatched_second_plan(root_arg: Path, state_arg: StateDocument) -> StateWritePlan:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_plan_state_write(root_arg, state_arg)
        return StateWritePlan(root, "no_change", clean.state_json, None, None)

    monkeypatch.setattr(
        initialization,
        "plan_state_write",
        mismatched_second_plan,
    )
    with pytest.raises(InitializationError, match="Provenance state changed"):
        apply_initialization(clean)
    monkeypatch.undo()

    apply_initialization(plan_initialization(root, replace=True))
    clean = plan_initialization(root)
    monkeypatch.setattr(
        initialization, "apply_state_write", lambda _plan: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(InitializationError, match="Unable to write provenance"):
        apply_initialization(clean)
