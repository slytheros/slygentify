"""Tests for the public command-line interface."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from slygentify import (
    InitializationError,
    InitializationResult,
    plan_initialization,
    scan_repository,
)
from slygentify._generation import _render_paste_snippet, generate_agents_document
from slygentify.cli import app


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='example'\n", encoding="utf-8")
    return root


@pytest.mark.verifies("TST002", "TST040")
def test_init_cli_dry_run_and_creation(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    runner = CliRunner()

    dry_run = runner.invoke(app, ["init", str(root), "--dry-run"])
    assert dry_run.exit_code == 0
    assert "Ownership: new" in dry_run.stdout
    assert "--- AGENTS.md ---" in dry_run.stdout
    assert "--- .slygentify/state.json ---" in dry_run.stdout
    assert not (root / "AGENTS.md").exists()

    created = runner.invoke(app, ["init", str(root)])
    assert created.exit_code == 0
    assert created.stdout == "Created AGENTS.md and .slygentify/state.json\n"
    assert (root / "AGENTS.md").is_file()


@pytest.mark.verifies("TST003", "TST040", "TST054")
def test_init_cli_refusal_replacement_and_no_change(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "AGENTS.md").write_text("human", encoding="utf-8")
    runner = CliRunner()

    paste_guidance = runner.invoke(app, ["init", str(root)])
    assert paste_guidance.exit_code == 4
    assert paste_guidance.stderr == ""
    assert "Existing AGENTS.md was preserved." in paste_guidance.stdout
    assert "## Slygentify bootstrap guidance" in paste_guidance.stdout
    assert "# AGENTS.md" not in paste_guidance.stdout
    assert "managed-artifact lifecycle" not in paste_guidance.stdout
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "human"

    replaced = runner.invoke(app, ["init", str(root), "--replace"])
    assert replaced.exit_code == 0
    assert "Warning: --replace" in replaced.stderr
    assert "Regenerated" in replaced.stdout

    unchanged = runner.invoke(app, ["init", str(root)])
    assert unchanged.exit_code == 0
    assert unchanged.stdout == "No changes.\n"


@pytest.mark.verifies("TST040")
def test_init_cli_reports_planning_and_apply_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    runner = CliRunner()
    (root / "AGENTS.md").write_text("human", encoding="utf-8")

    dry_run = runner.invoke(app, ["init", str(root), "--dry-run"])
    assert dry_run.exit_code == 4
    assert "Ownership: unmanaged" in dry_run.stdout
    assert "--- AGENTS.md ---" in dry_run.stdout
    assert "--- .slygentify/state.json ---" in dry_run.stdout
    assert dry_run.stderr == ""
    assert plan_initialization(root, replace=True).can_apply

    def fail_plan(*_args: object, **_kwargs: object) -> object:
        raise InitializationError("initialization.path", "bad path")

    monkeypatch.setattr("slygentify.cli.plan_initialization", fail_plan)
    planning_error = runner.invoke(app, ["init", str(root)])
    assert planning_error.exit_code == 1
    assert planning_error.stderr == (
        "Error [initialization.path]: bad path\n"
        "Next: Run slygentify init --dry-run to review the current state.\n"
    )
    monkeypatch.undo()

    def fail_apply(*_args: object, **_kwargs: object) -> object:
        raise InitializationError(
            "initialization.partial-write",
            "state write failed",
            changed_locations=("AGENTS.md",),
            recovery="rerun review",
        )

    monkeypatch.setattr("slygentify.cli.apply_initialization", fail_apply)
    applied_error = runner.invoke(app, ["init", str(root), "--replace"])
    assert applied_error.exit_code == 1
    assert "Changed: AGENTS.md" in applied_error.stderr
    assert "Next: rerun review" in applied_error.stderr
    monkeypatch.undo()

    def fail_without_changes(*_args: object, **_kwargs: object) -> object:
        raise InitializationError("initialization.write-failed", "write failed")

    monkeypatch.setattr("slygentify.cli.apply_initialization", fail_without_changes)
    unchanged_error = runner.invoke(app, ["init", str(root), "--replace"])
    assert unchanged_error.exit_code == 1
    assert "Changed:" not in unchanged_error.stderr
    monkeypatch.undo()

    def repaired(*_args: object, **_kwargs: object) -> InitializationResult:
        return InitializationResult(
            repository_root=root,
            ownership="recoverable-state",
            agents_action="no_change",
            state_action="replace",
            changed_locations=(".slygentify/state.json",),
        )

    monkeypatch.setattr("slygentify.cli.apply_initialization", repaired)
    repaired_result = runner.invoke(app, ["init", str(root), "--replace"])
    assert repaired_result.exit_code == 0
    assert repaired_result.stdout == "Repaired .slygentify/state.json\n"


@pytest.mark.verifies("TST054")
def test_init_cli_prints_deterministic_paste_guidance_for_human_edits(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(root)]).exit_code == 0
    agents = root / "AGENTS.md"
    agents.write_text("human-owned guidance\n", encoding="utf-8")
    before = agents.read_bytes()

    result = runner.invoke(app, ["init", str(root)])

    expected = _render_paste_snippet(generate_agents_document(scan_repository(root)).markdown)
    assert result.exit_code == 4
    assert result.stderr == ""
    assert result.stdout.endswith(expected)
    assert "# AGENTS.md" not in result.stdout
    assert "managed-artifact lifecycle" not in result.stdout
    assert agents.read_bytes() == before


@pytest.mark.verifies("TST040", "TST054")
def test_init_cli_retains_diagnostics_for_invalid_state(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    state = root / ".slygentify" / "state.json"
    state.parent.mkdir()
    state.write_text('{"schema_version": 2}', encoding="utf-8")
    runner = CliRunner()

    dry_run = runner.invoke(app, ["init", str(root), "--dry-run"])
    ordinary = runner.invoke(app, ["init", str(root)])

    assert dry_run.exit_code == 1
    assert "initialization.invalid-state" in dry_run.stderr
    assert ordinary.exit_code == 1
    assert "initialization.invalid-state" in ordinary.stderr


@pytest.mark.verifies("TST004", "TST040")
def test_init_cli_preserves_typer_usage_exit_code() -> None:
    assert CliRunner().invoke(app, ["init", ".", "--unknown-option"]).exit_code == 2


@pytest.mark.verifies("TST044")
def test_init_cli_warns_for_relaxed_committed_context_limits(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "slygentify.toml").write_text(
        """schema_version = 1
[init]
max_component_entries = "unlimited"
""",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["init", str(root), "--dry-run"])

    assert result.exit_code == 0
    assert "raises or disables an AGENTS.md byte or component-entry limit" in result.stderr
    assert "--- AGENTS.md ---" in result.stdout
