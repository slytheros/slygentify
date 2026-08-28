"""Tests for doctor presentation, streams, options, and exit behavior."""

from __future__ import annotations

from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

import slygentify.cli as cli
from slygentify import (
    DoctorDiagnostic,
    DoctorInputError,
    DoctorOperationalError,
    DoctorResult,
    dump_doctor_json,
)
from slygentify._doctor_presentation import render_doctor_report
from slygentify.cli import app
from tests.test_doctor_json import sample_doctor_result


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _render(
    result: DoctorResult,
    *,
    verbose: bool = False,
    terminal: bool = False,
    no_color: bool = False,
) -> str:
    stream = StringIO()
    render_doctor_report(
        result,
        Path("repository"),
        Console(
            file=stream,
            force_terminal=terminal,
            color_system="standard" if terminal else None,
            legacy_windows=False if terminal else None,
            _environ={"NO_COLOR": "1"} if no_color else {},
            width=160,
        ),
        verbose=verbose,
    )
    return stream.getvalue()


@pytest.mark.verifies("TST049")
def test_doctor_text_is_concise_complete_and_severity_grouped() -> None:
    output = _render(sample_doctor_result())

    assert output.startswith("Doctor completed\n")
    assert "Repository: repository" in output
    assert "Status: Partial doctor result" in output
    assert "Diagnostics: 0 errors, 1 warning, 1 info" in output
    assert output.index("Warnings (1)") < output.index("Information (1)")
    assert "WARNING VERIFIED [doctor.tooling.drift] AGENTS.md (repository-1)" in output
    assert "INFO UNKNOWN [doctor.guidance.unmanaged] repository-1" in output
    assert "Problem: Tooling knowledge changed." in output
    assert "Effect: Managed workflow guidance may be stale." in output
    assert "Next: Review and regenerate managed guidance." in output
    assert "Evidence IDs:" not in output
    assert "Skipped scopes" not in output
    assert "\x1b[" not in output


@pytest.mark.verifies("TST049")
def test_doctor_verbose_text_exposes_evidence_and_skipped_scopes_once() -> None:
    output = _render(sample_doctor_result(), verbose=True)

    assert "Evidence IDs: evidence-1" in output
    assert "Evidence IDs: evidence-2" in output
    assert "Evidence (2)" in output
    assert output.count("The project name is declared.") == 1
    assert output.count("Managed guidance differs from fresh generation.") == 1
    assert "Verification: static manifest parsing" in output
    assert "Skipped scopes (2)" in output
    assert ".gitignore: reason=invalid_gitignore, omitted=ignored paths" in output
    assert "vendor: reason=entry_limit, omitted=vendor/**, limit=10, consumed=10" in output


@pytest.mark.verifies("TST049")
def test_doctor_text_handles_clean_error_and_location_only_results() -> None:
    original = sample_doctor_result()
    clean = replace(original, completion="complete", diagnostics=(), skipped_scopes=())
    clean_output = _render(clean)
    assert "Diagnostics: 0 errors, 0 warnings, 0 info" in clean_output
    assert "No diagnostics." in clean_output

    location_only = DoctorDiagnostic(
        id="diagnostic-error",
        code="doctor.state.invalid",
        severity="error",
        classification="verified",
        subject_id=None,
        location=".slygentify/state.json",
        problem="State is invalid.",
        effect="Ownership cannot be trusted.",
        category="state.invalid-json",
        safety_rationale="Automatic replacement could overwrite content whose ownership is unknown.",
        remediation="Regenerate through a reviewed flow.",
        evidence_ids=("evidence-1",),
    )
    errored = replace(original, diagnostics=(location_only,))
    output = _render(errored)
    assert "Errors (1)" in output
    assert "ERROR VERIFIED [doctor.state.invalid] .slygentify/state.json" in output
    assert "Category: state.invalid-json" in output
    assert "Why no automatic repair: Automatic replacement could overwrite content" in output


@pytest.mark.verifies("TST049")
def test_doctor_terminal_style_never_carries_meaning() -> None:
    colored = _render(sample_doctor_result(), terminal=True)
    no_color = _render(sample_doctor_result(), terminal=True, no_color=True)

    assert "\x1b[" in colored
    assert "WARNING VERIFIED" in Text.from_ansi(colored).plain
    assert "INFO UNKNOWN" in Text.from_ansi(colored).plain
    assert "\x1b[1;33m" not in no_color
    assert "WARNING VERIFIED" in Text.from_ansi(no_color).plain


@pytest.mark.verifies("TST049")
def test_doctor_help_discloses_static_and_explicit_git_boundaries() -> None:
    result = CliRunner().invoke(app, ["doctor", "--help"])
    plain = " ".join(Text.from_ansi(result.stdout).plain.split())

    assert result.exit_code == 0
    assert "without changing or executing" in plain
    assert "--git-executable" in plain
    assert "not sandboxed" in plain
    assert "arbitrary" in plain
    assert "effects are possible" in plain


@pytest.mark.verifies("TST049")
@pytest.mark.parametrize(
    ("diagnostics", "expected_exit"),
    [
        ((), 0),
        ((sample_doctor_result().diagnostics[0],), 0),
        ((sample_doctor_result().diagnostics[1],), 1),
    ],
)
def test_doctor_json_stdout_and_exit_follow_diagnostic_severity(
    monkeypatch: pytest.MonkeyPatch,
    diagnostics: tuple[DoctorDiagnostic, ...],
    expected_exit: int,
) -> None:
    result = replace(
        sample_doctor_result(),
        completion="complete",
        diagnostics=diagnostics,
        skipped_scopes=(),
    )
    monkeypatch.setattr(cli, "doctor_repository", lambda *args, **kwargs: result)

    invocation = CliRunner().invoke(app, ["doctor", ".", "--format", "json"])

    assert invocation.exit_code == expected_exit
    assert invocation.stdout_bytes == dump_doctor_json(result)
    assert invocation.stderr == ""


@pytest.mark.verifies("TST049")
def test_doctor_text_cli_forwards_git_and_verbose(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _repository(tmp_path)
    captured: list[tuple[Path, Path | None]] = []

    def doctor(path: Path, *, git_executable: Path | None = None) -> DoctorResult:
        captured.append((path, git_executable))
        return sample_doctor_result()

    monkeypatch.setattr(cli, "doctor_repository", doctor)
    invocation = CliRunner().invoke(
        app,
        ["doctor", str(root), "--verbose", "--git-executable", "tools/git"],
    )

    assert invocation.exit_code == 1
    assert invocation.stderr == ""
    assert "Evidence (2)" in invocation.stdout
    assert str(root) in invocation.stdout.replace("\n", "")
    assert captured == [(root, Path("tools/git"))]


@pytest.mark.verifies("TST049")
@pytest.mark.parametrize(
    "arguments",
    [
        ["doctor", ".", "--format", "yaml"],
        ["doctor", ".", "--format", "json", "--verbose"],
        ["doctor", ".", "--unknown"],
    ],
)
def test_doctor_usage_failures_exit_two_before_assessment(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "doctor_repository",
        lambda *args, **kwargs: pytest.fail("doctor must not run"),
    )

    invocation = CliRunner().invoke(app, arguments)

    assert invocation.exit_code == 2
    assert invocation.stdout == ""


@pytest.mark.verifies("TST049")
@pytest.mark.parametrize(
    ("error", "expected_exit", "message"),
    [
        (DoctorInputError("bad target"), 2, "selected input"),
        (DoctorOperationalError("disk failed"), 3, "trustworthy result"),
    ],
)
def test_doctor_core_failures_use_stderr_without_result(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_exit: int,
    message: str,
) -> None:
    monkeypatch.setattr(
        cli,
        "doctor_repository",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    invocation = CliRunner().invoke(app, ["doctor", ".", "--format", "json"])

    assert invocation.exit_code == expected_exit
    assert invocation.stdout == ""
    assert message in invocation.stderr
    assert "Next:" in invocation.stderr


@pytest.mark.verifies("TST049")
@pytest.mark.parametrize(
    "error",
    [DoctorInputError("invalid result"), DoctorOperationalError("encoding failed")],
)
def test_doctor_json_serialization_failures_exit_three(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(cli, "doctor_repository", lambda *args, **kwargs: sample_doctor_result())
    monkeypatch.setattr(
        cli,
        "dump_doctor_json",
        lambda result: (_ for _ in ()).throw(error),
    )

    invocation = CliRunner().invoke(app, ["doctor", ".", "--format", "json"])

    assert invocation.exit_code == 3
    assert invocation.stdout == ""
    assert "serialization failed" in invocation.stderr


@pytest.mark.verifies("TST049")
def test_doctor_report_root_failure_exits_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "doctor_repository", lambda *args, **kwargs: sample_doctor_result())
    monkeypatch.setattr(
        cli,
        "find_git_root",
        lambda path: (_ for _ in ()).throw(OSError("root disappeared")),
    )

    invocation = CliRunner().invoke(app, ["doctor", "."])

    assert invocation.exit_code == 3
    assert invocation.stdout == ""
    assert "report rendering failed" in invocation.stderr
