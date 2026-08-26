"""Tests for the supported repository scan Python interface."""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

import slygentify
import slygentify.api as api
from slygentify import ScanError, ScanResult, ScanValidationError, scan_repository
from slygentify._git_tracking import _TrackedPaths
from slygentify._scan import _ScanFoundationError
from tests.scan_samples import sample_result


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    return repository


@pytest.mark.verifies("TST017")
def test_public_scan_names_are_exported_from_the_top_level() -> None:
    assert slygentify.scan_repository is scan_repository
    assert issubclass(ScanValidationError, ScanError)
    assert all(
        name in slygentify.__all__
        for name in (
            "ScanError",
            "ScanValidationError",
            "scan_repository",
            "validate_scan",
            "load_scan_json",
            "dump_scan_json",
            "scan_json_schema",
        )
    )


@pytest.mark.verifies("TST017")
def test_scan_repository_accepts_default_and_pathlike_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "Cargo.toml").write_text('[package]\nname = "example"\n', encoding="utf-8")
    monkeypatch.chdir(repository)

    default_result = scan_repository()
    explicit_result = scan_repository(repository / ".")

    assert isinstance(default_result, ScanResult)
    assert default_result == explicit_result
    assert default_result.components[0].ecosystem == "generic"


@pytest.mark.verifies("TST017")
def test_scan_repository_forwards_git_executable_without_coercion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = Path("tools/git")
    expected = sample_result()
    captured: object | None = None

    def foundation(
        path: Path, *, git_executable: str | os.PathLike[str] | None
    ) -> tuple[Path, ScanResult]:
        nonlocal captured
        captured = git_executable
        return path, expected

    monkeypatch.setattr(api, "_scan_foundation", foundation)

    assert api.scan_repository(".", git_executable=selected) is expected
    assert captured is selected


@pytest.mark.verifies("TST017")
def test_default_scan_allows_only_the_fixed_git_lookup_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "go.mod").write_text("module example.test/project\n", encoding="utf-8")

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

    result = scan_repository(repository)

    assert result.completion == "complete"
    assert lookup_calls == 1


@pytest.mark.verifies("TST017")
def test_scan_repository_rejects_unimplemented_root_configuration(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "slygentify.toml").write_text("unsupported = true\n", encoding="utf-8")

    with pytest.raises(ScanError):
        scan_repository(repository)


@pytest.mark.verifies("TST017")
@pytest.mark.parametrize(
    "failure",
    [
        _ScanFoundationError("foundation"),
        OSError("filesystem"),
        TypeError("path"),
        ValueError("normalization"),
    ],
)
def test_scan_repository_translates_operational_failures(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def fail(
        path: Path, *, git_executable: str | os.PathLike[str] | None
    ) -> tuple[Path, ScanResult]:
        raise failure

    monkeypatch.setattr(api, "_scan_foundation", fail)

    with pytest.raises(ScanError) as captured:
        scan_repository(".")

    assert captured.value.__cause__ is None
