"""Observable initialization lifecycle and usefulness-acceptance tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

import slygentify.initialization as initialization
from slygentify import InitializationError, apply_initialization, plan_initialization
from slygentify._provenance import Artifact, StateDocument, dump_state_json, load_state_json
from slygentify.cli import app


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'example'\nrequires-python = '>=3.11'\n", encoding="utf-8"
    )
    return root


def _managed_repository(tmp_path: Path) -> Path:
    root = _repository(tmp_path)
    apply_initialization(plan_initialization(root))
    return root


@pytest.mark.verifies("TST039", "TST040")
def test_first_dry_run_create_no_change_and_regeneration_from_inputs(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    dry_run = plan_initialization(root)

    assert dry_run.ownership == "new"
    assert dry_run.can_apply
    assert not (root / "AGENTS.md").exists()
    assert not (root / ".slygentify").exists()

    created = apply_initialization(dry_run)
    assert created.changed_locations == ("AGENTS.md", ".slygentify/state.json")
    first_agents = (root / "AGENTS.md").read_bytes()
    first_state = (root / ".slygentify" / "state.json").read_bytes()
    no_change = plan_initialization(root)
    assert no_change.agents_action == "no_change"
    assert no_change.state_action == "no_change"
    assert no_change.agents_markdown.encode("utf-8") == first_agents
    assert no_change.state_json == first_state
    assert apply_initialization(no_change).changed_locations == ()

    (root / "pyproject.toml").write_text(
        "[project]\nname = 'example'\nrequires-python = '>=3.12'\n", encoding="utf-8"
    )
    manifest_change = plan_initialization(root)
    assert manifest_change.ownership == "clean-managed"
    assert manifest_change.agents_action == "no_change"
    assert manifest_change.state_action == "replace"
    assert apply_initialization(manifest_change).changed_locations == (".slygentify/state.json",)

    (root / "slygentify.toml").write_text(
        "schema_version = 1\n\n[[scan.components]]\npath = '.'\nkind = 'application'\n",
        encoding="utf-8",
    )
    configuration_change = plan_initialization(root)
    assert configuration_change.ownership == "clean-managed"
    assert configuration_change.agents_action == "replace"
    assert configuration_change.state_action == "replace"
    apply_initialization(configuration_change)
    assert apply_initialization(plan_initialization(root)).changed_locations == ()


@pytest.mark.verifies("TST039")
def test_protected_entries_stale_recovery_and_schema_refusal(tmp_path: Path) -> None:
    root = _managed_repository(tmp_path)
    agents = root / "AGENTS.md"
    state_target = root / ".slygentify" / "state.json"
    original_state = state_target.read_bytes()
    agents.write_text("sentinel-secret-human-content", encoding="utf-8")

    edited = plan_initialization(root)
    assert edited.ownership == "human-edited"
    assert not edited.can_apply
    assert "sentinel-secret-human-content" not in edited.diagnostics[0].message
    assert state_target.read_bytes() == original_state
    with pytest.raises(InitializationError) as error:
        apply_initialization(edited)
    assert error.value.changed_locations == ()
    assert agents.read_text(encoding="utf-8") == "sentinel-secret-human-content"

    replacement = plan_initialization(root, replace=True)
    assert replacement.can_apply
    apply_initialization(replacement)
    state = load_state_json(state_target.read_bytes())
    stale = StateDocument(
        state.schema_version,
        state.producer_version,
        state.configuration,
        state.effective_limits,
        state.inputs,
        state.derivations,
        (Artifact("AGENTS.md", "0" * 64, state.artifacts[0].evidence_ids),),
        state.completion,
        state.skipped_scopes,
    )
    state_target.write_bytes(dump_state_json(stale))
    recoverable = plan_initialization(root)
    assert recoverable.ownership == "recoverable-state"
    assert recoverable.agents_action == "no_change"
    assert recoverable.state_action == "replace"
    assert apply_initialization(recoverable).changed_locations == (".slygentify/state.json",)

    state_target.write_text('{"schema_version": 2}', encoding="utf-8")
    invalid = plan_initialization(root, replace=True)
    assert invalid.ownership == "invalid-state"
    assert not invalid.can_apply
    assert state_target.read_text(encoding="utf-8") == '{"schema_version": 2}'


@pytest.mark.verifies("TST039")
@pytest.mark.parametrize("changed", ["agents", "state", "manifest", "configuration", "removed"])
def test_revalidation_rejects_lifecycle_races(tmp_path: Path, changed: str) -> None:
    root = _managed_repository(tmp_path)
    plan = plan_initialization(root)
    agents = root / "AGENTS.md"
    state = root / ".slygentify" / "state.json"
    if changed == "agents":
        agents.write_text("raced", encoding="utf-8")
    elif changed == "state":
        state.write_text('{"schema_version": 2}', encoding="utf-8")
    elif changed == "manifest":
        (root / "pyproject.toml").unlink()
    elif changed == "configuration":
        (root / "slygentify.toml").write_text("schema_version = 1\n", encoding="utf-8")
    else:
        agents.unlink()
        agents.mkdir()

    with pytest.raises(InitializationError) as error:
        apply_initialization(plan)
    assert error.value.code == "initialization.concurrent-change"
    assert error.value.changed_locations == ()


@pytest.mark.verifies("TST039", "TST040", "TST054")
def test_write_failures_leave_safe_recovery_paths_and_actionable_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    plan = plan_initialization(root)
    monkeypatch.setattr(
        os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("write failure")),
    )
    with pytest.raises(InitializationError) as agents_error:
        apply_initialization(plan)
    assert agents_error.value.code == "initialization.write-failed"
    assert not (root / "AGENTS.md").exists()
    assert not (root / ".slygentify" / "state.json").exists()
    assert not list(root.glob(".agents-*"))
    monkeypatch.undo()

    plan = plan_initialization(root)
    monkeypatch.setattr(
        initialization,
        "apply_state_write",
        lambda _plan: (_ for _ in ()).throw(OSError("state failure")),
    )
    with pytest.raises(InitializationError) as state_error:
        apply_initialization(plan)
    assert state_error.value.code == "initialization.partial-write"
    assert state_error.value.changed_locations == ("AGENTS.md",)
    assert not (root / ".slygentify" / "state.json").exists()
    monkeypatch.undo()

    recovery = plan_initialization(root)
    assert recovery.ownership == "recoverable-state"
    assert apply_initialization(recovery).changed_locations == (".slygentify/state.json",)

    (root / "AGENTS.md").write_text("secret-do-not-print", encoding="utf-8")
    result = CliRunner().invoke(app, ["init", str(root)])
    assert result.exit_code == 4
    assert result.stderr == ""
    assert "Existing AGENTS.md was preserved." in result.stdout
    assert "secret-do-not-print" not in result.stderr
    assert "secret-do-not-print" not in result.stdout
