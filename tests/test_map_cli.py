"""Tests for the fresh repository map Python and CLI interfaces."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

import pytest
from rich.text import Text
from typer.testing import CliRunner

import slygentify
import slygentify.api as api
import slygentify.cli as cli
from slygentify import (
    ScanError,
    ScanProjection,
    dump_scan_projection_json,
    map_repository,
    project_scan,
)
from slygentify._git_tracking import _TrackedPaths
from slygentify.cli import app
from tests.scan_samples import sample_result


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname = 'example'\n", encoding="utf-8")
    return repository


@pytest.mark.verifies("TST043")
def test_map_public_names_and_fresh_scan_forwarding(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = sample_result()
    calls: list[tuple[object, object]] = []
    selected_git = Path("tools/git")

    def scan(path: object = ".", *, git_executable: object = None) -> object:
        calls.append((path, git_executable))
        return expected

    monkeypatch.setattr(api, "scan_repository", scan)

    first = map_repository("repo", scope="planned.py", git_executable=selected_git)
    second = map_repository("repo", scope="planned.py", git_executable=selected_git)

    assert isinstance(first, ScanProjection)
    assert first == second
    assert calls == [("repo", selected_git), ("repo", selected_git)]
    assert slygentify.map_repository is map_repository
    assert all(
        name in slygentify.__all__
        for name in (
            "ProjectionScope",
            "ProjectionOmission",
            "ProjectionSection",
            "ScanProjection",
            "project_scan",
            "map_repository",
            "validate_scan_projection",
            "load_scan_projection_json",
            "dump_scan_projection_json",
            "scan_projection_json_schema",
        )
    )


@pytest.mark.verifies("TST043")
def test_map_repository_remains_local_read_only_and_network_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    before = sorted(path.relative_to(repository).as_posix() for path in repository.rglob("*"))
    lookup_calls = 0

    def fixed_lookup(*args: object, **kwargs: object) -> _TrackedPaths:
        nonlocal lookup_calls
        lookup_calls += 1
        return _TrackedPaths(frozenset(), frozenset(), True)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("forbidden effect")

    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr("slygentify._scan.orchestration._discover_tracked_paths", fixed_lookup)

    projection = map_repository(repository)

    after = sorted(path.relative_to(repository).as_posix() for path in repository.rglob("*"))
    assert projection.scope.requested_path == "."
    assert lookup_calls == 1
    assert after == before


@pytest.mark.verifies("TST043")
def test_map_cli_emits_canonical_json_and_forwards_exact_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = project_scan(sample_result(), max_bytes="unlimited")
    captured: dict[str, object] = {}

    def map_call(
        path: Path,
        *,
        scope: str,
        sections: tuple[str, ...] | None,
        max_bytes: object,
        git_executable: Path | None,
    ) -> ScanProjection:
        captured.update(
            path=path,
            scope=scope,
            sections=sections,
            max_bytes=max_bytes,
            git_executable=git_executable,
        )
        return projection

    monkeypatch.setattr(cli, "map_repository", map_call)

    result = CliRunner().invoke(
        app,
        [
            "map",
            "repo",
            "--scope",
            "src/new.py",
            "--section",
            "architecture",
            "--section",
            "workflows",
            "--max-bytes",
            "unlimited",
            "--git-executable",
            "tools/git",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout_bytes == dump_scan_projection_json(projection)
    assert result.stderr == ""
    assert captured == {
        "path": Path("repo"),
        "scope": "src/new.py",
        "sections": ("architecture", "workflows"),
        "max_bytes": "unlimited",
        "git_executable": Path("tools/git"),
    }
    assert json.loads(result.stdout)["schema_version"] == 1


@pytest.mark.verifies("TST043")
def test_map_cli_defaults_help_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    projection = project_scan(sample_result(), max_bytes="unlimited")
    captured: list[tuple[object, ...]] = []

    def map_call(
        path: Path,
        *,
        scope: str,
        sections: tuple[str, ...] | None,
        max_bytes: object,
        git_executable: Path | None,
    ) -> ScanProjection:
        captured.append((path, scope, sections, max_bytes, git_executable))
        return projection

    monkeypatch.setattr(cli, "map_repository", map_call)
    runner = CliRunner()

    default = runner.invoke(app, ["map"])
    help_result = runner.invoke(app, ["map", "--help"], terminal_width=160)
    invalid_limit = runner.invoke(app, ["map", "--max-bytes", "08192"])
    non_integer_limit = runner.invoke(app, ["map", "--max-bytes", "many"])
    monkeypatch.setattr(
        cli,
        "map_repository",
        lambda *args, **kwargs: (_ for _ in ()).throw(ScanError("unsafe scope")),
    )
    operational_error = runner.invoke(app, ["map", "--scope", "../outside"])

    assert default.exit_code == 0
    assert captured == [(Path("."), ".", None, 8192, None)]
    plain_help = " ".join(Text.from_ansi(help_result.stdout).plain.split())
    assert help_result.exit_code == 0
    assert "--git-executable" in plain_help
    assert "Trusted code" in plain_help
    assert "sandboxed" in plain_help
    assert "arbitrary" in plain_help
    assert "effects are possible" in plain_help
    assert invalid_limit.exit_code != 0
    assert "positive integer or 'unlimited'" in invalid_limit.stderr
    assert non_integer_limit.exit_code != 0
    assert "positive integer or 'unlimited'" in non_integer_limit.stderr
    assert operational_error.exit_code == 1
    assert operational_error.stdout == ""
    assert operational_error.stderr == "Error: unsafe scope\n"
