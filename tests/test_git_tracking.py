"""Deterministic tests for the bounded Git tracked-path capability."""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import threading
from pathlib import Path
from typing import Any, cast

import pytest

import slygentify._git_tracking as tracking

pytestmark = pytest.mark.verifies("TST011", "TST013", "TST014", "TST017")


def _executable(path: Path) -> Path:
    path.write_bytes(b"tool")
    path.chmod(
        stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )
    return path


class _Process:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        *,
        running: bool = False,
        wait_times_out: bool = False,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode: int | None = None if running else 0
        self.wait_times_out = wait_times_out
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_times_out and self.returncode is None:
            raise subprocess.TimeoutExpired("git", timeout or 0.0)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _as_popen(process: _Process) -> subprocess.Popen[bytes]:
    return cast("subprocess.Popen[bytes]", process)


def test_explicit_selection_resolves_relative_and_repository_contained_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    selected = _executable(repository / "git-tool")
    monkeypatch.chdir(tmp_path)

    executable = tracking._select_executable(repository, Path("repository/git-tool"))

    assert executable is not None
    assert executable.path == selected
    assert tracking._inside(repository, selected)
    assert not tracking._inside(repository, tmp_path)


@pytest.mark.parametrize("kind", ["missing", "directory", "non_executable", "invalid_type"])
def test_invalid_explicit_selection_never_falls_back(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "selected"
    if kind == "directory":
        selected.mkdir()
    elif kind == "non_executable":
        selected.write_bytes(b"tool")
        selected.chmod(stat.S_IRUSR | stat.S_IWUSR)
        if os.access(selected, os.X_OK):
            pytest.skip("this platform does not expose executable mode through os.access")
    value: object = 123 if kind == "invalid_type" else selected
    discovered = False

    def which(name: str) -> str | None:
        nonlocal discovered
        discovered = True
        return None

    monkeypatch.setattr(shutil, "which", which)

    with pytest.raises(tracking._InvalidGitExecutableError):
        tracking._select_executable(tmp_path, cast(Any, value))
    assert not discovered


def test_automatic_selection_handles_absence_lookup_failure_and_unsafe_results(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    inside = _executable(repository / "git")
    directory = tmp_path / "directory"
    directory.mkdir()
    outside = _executable(tmp_path / "outside-git")

    assert tracking._select_executable(repository, None, which=lambda name: None) is None
    assert (
        tracking._select_executable(
            repository, None, which=lambda name: cast(str, (_ for _ in ()).throw(OSError()))
        )
        is None
    )
    assert tracking._select_executable(repository, None, which=lambda name: str(directory)) is None
    assert tracking._select_executable(repository, None, which=lambda name: str(inside)) is None
    assert tracking._select_executable(repository, None, which=lambda name: str(outside)) == (
        tracking._validate_executable(outside)
    )


def test_explicit_override_takes_precedence_over_path_discovery(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    selected = _executable(tmp_path / "selected-git")

    def unexpected(name: str) -> str | None:
        raise AssertionError("PATH discovery must not run")

    executable = tracking._select_executable(repository, selected, which=unexpected)

    assert executable is not None
    assert executable.path == selected


def test_explicit_repository_executable_is_authorized_for_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    selected = _executable(repository / "selected-git")
    captured: list[Path] = []

    def run(
        executable: tracking._Executable, *args: object, **kwargs: object
    ) -> tuple[int, bytes, bytes]:
        captured.append(executable.path)
        return 0, b"tracked\0", b""

    monkeypatch.setattr(tracking, "_run_git", run)

    result = tracking._discover_tracked_paths(
        repository,
        git_executable=selected,
        max_total_bytes=100,
        max_memory_bytes=100,
        max_elapsed_seconds=60,
        started=0,
        clock=lambda: 1,
    )

    assert result.available
    assert captured == [selected]


def test_git_environment_removes_inherited_overrides_and_disables_optional_effects() -> None:
    environment = tracking._git_environment(
        {"PATH": "tools", "GIT_DIR": "elsewhere", "git_work_tree": "outside", "KEEP": "yes"}
    )

    assert environment["PATH"] == "tools"
    assert environment["KEEP"] == "yes"
    assert "GIT_DIR" not in environment
    assert "git_work_tree" not in environment
    assert environment == {
        "PATH": "tools",
        "KEEP": "yes",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
    }


def test_path_output_validation_is_byte_preserving_deduplicated_and_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tracking._parse_paths(b"") == (frozenset(), frozenset())
    assert tracking._parse_paths(b"a/b/file\0a/b/file\0root\0") == (
        frozenset({b"a/b/file", b"root"}),
        frozenset({b"a", b"a/b"}),
    )
    for output in (
        b"unterminated",
        b"\0",
        b"/absolute\0",
        b"trailing/\0",
        b"a//b\0",
        b"./file\0",
        b"a/../file\0",
    ):
        assert tracking._parse_paths(output) is None

    monkeypatch.setattr(tracking, "_IS_WINDOWS", True)
    assert tracking._parse_paths(b"a\\b\0") is None
    assert tracking._parse_paths(b"C:file\0") is None
    monkeypatch.setattr(tracking, "_IS_WINDOWS", False)
    assert tracking._parse_paths(b"a\\b\0") == (frozenset({b"a\\b"}), frozenset())


def test_capture_stream_enforces_limit_and_converts_read_errors_to_exhaustion() -> None:
    destination = bytearray()
    exhausted = threading.Event()
    tracking._capture_stream(io.BytesIO(b"abcdef"), 3, destination, exhausted)
    assert destination == b"abc"
    assert exhausted.is_set()

    class Broken(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise OSError("unreadable")

    destination.clear()
    exhausted.clear()
    tracking._capture_stream(Broken(), 3, destination, exhausted)
    assert destination == b""
    assert exhausted.is_set()


def test_stop_process_handles_completed_terminated_and_killed_children() -> None:
    completed = _Process()
    tracking._stop_process(_as_popen(completed))
    assert not completed.terminated

    terminated = _Process(running=True)
    tracking._stop_process(_as_popen(terminated))
    assert terminated.terminated
    assert not terminated.killed

    killed = _Process(running=True, wait_times_out=True)

    def ineffective_terminate() -> None:
        killed.terminated = True

    killed.terminate = ineffective_terminate  # type: ignore[method-assign]
    tracking._stop_process(_as_popen(killed))
    assert killed.terminated
    assert killed.killed

    abandoned = _Process(running=True, wait_times_out=True)

    def abandoned_terminate() -> None:
        abandoned.terminated = True

    def failed_kill() -> None:
        raise OSError("cannot kill")

    abandoned.terminate = abandoned_terminate  # type: ignore[method-assign]
    abandoned.kill = failed_kill  # type: ignore[method-assign]
    tracking._stop_process(_as_popen(abandoned))
    assert abandoned.terminated


def test_run_git_uses_exact_bounded_noninteractive_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    selected = tracking._validate_executable(_executable(tmp_path / "git"))
    captured: dict[str, object] = {}
    process = _Process(b"tracked/file\0")

    def popen(arguments: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        captured["arguments"] = arguments
        captured.update(kwargs)
        return _as_popen(process)

    monkeypatch.setattr(subprocess, "Popen", popen)

    result = tracking._run_git(
        selected,
        repository,
        timeout=1.0,
        stdout_limit=1024,
        environment={"SAFE": "1"},
        clock=lambda: 0.0,
    )

    assert result == (0, b"tracked/file\0", b"")
    assert captured["arguments"] == [
        str(selected.path),
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "ls-files",
        "--cached",
        "--full-name",
        "-z",
        "--",
    ]
    assert captured["cwd"] == str(repository)
    assert captured["env"] == {"SAFE": "1"}
    assert captured["shell"] is False
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.PIPE
    assert captured["stderr"] is subprocess.PIPE
    assert captured["close_fds"] is True


def test_run_git_revalidates_identity_and_handles_spawn_and_pipe_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    path = _executable(tmp_path / "git")
    selected = tracking._validate_executable(path)
    path.write_bytes(b"replacement")

    assert (
        tracking._run_git(
            selected,
            repository,
            timeout=1,
            stdout_limit=1,
            environment={},
            clock=lambda: 0,
        )
        is None
    )

    selected = tracking._validate_executable(path)
    path.unlink()
    assert (
        tracking._run_git(
            selected,
            repository,
            timeout=1,
            stdout_limit=1,
            environment={},
            clock=lambda: 0,
        )
        is None
    )
    _executable(path)
    selected = tracking._validate_executable(path)

    def spawn_failure(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        raise OSError("cannot spawn")

    monkeypatch.setattr(subprocess, "Popen", spawn_failure)
    assert (
        tracking._run_git(
            selected,
            repository,
            timeout=1,
            stdout_limit=1,
            environment={},
            clock=lambda: 0,
        )
        is None
    )

    process = _Process()
    process.stdout = cast(Any, None)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: _as_popen(process))
    assert (
        tracking._run_git(
            selected,
            repository,
            timeout=1,
            stdout_limit=1,
            environment={},
            clock=lambda: 0,
        )
        is None
    )


def test_run_git_terminates_on_deadline_and_stream_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    selected = tracking._validate_executable(_executable(tmp_path / "git"))
    timed_out = _Process(running=True, wait_times_out=True)
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: _as_popen(timed_out))

    assert (
        tracking._run_git(
            selected,
            repository,
            timeout=1,
            stdout_limit=10,
            environment={},
            clock=lambda: next(ticks),
        )
        is None
    )
    assert timed_out.terminated

    exhausted = _Process(b"too much output", running=True, wait_times_out=True)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: _as_popen(exhausted))
    assert (
        tracking._run_git(
            selected,
            repository,
            timeout=1,
            stdout_limit=1,
            environment={},
            clock=lambda: 0,
        )
        is None
    )
    assert exhausted.terminated

    completed_exhaustion = _Process(b"too much output")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: _as_popen(completed_exhaustion)
    )
    assert (
        tracking._run_git(
            selected,
            repository,
            timeout=1,
            stdout_limit=1,
            environment={},
            clock=lambda: 0,
        )
        is None
    )

    completed_stderr_exhaustion = _Process(stderr=b"x" * (tracking._STDERR_LIMIT + 1))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: _as_popen(completed_stderr_exhaustion),
    )
    assert (
        tracking._run_git(
            selected,
            repository,
            timeout=1,
            stdout_limit=1,
            environment={},
            clock=lambda: 0,
        )
        is None
    )


@pytest.mark.parametrize(
    ("run_result", "expected_available"),
    [
        (None, False),
        ((1, b"file\0", b""), False),
        ((0, b"file\0", b"warning"), False),
        ((0, b"unterminated", b""), False),
        ((0, b"file\0", b""), True),
    ],
)
def test_discovery_accepts_only_a_clean_safe_success(
    run_result: tuple[int, bytes, bytes] | None,
    expected_available: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tracking._validate_executable(_executable(tmp_path / "git"))
    monkeypatch.setattr(tracking, "_select_executable", lambda root, explicit: executable)
    monkeypatch.setattr(tracking, "_run_git", lambda *args, **kwargs: run_result)

    result = tracking._discover_tracked_paths(
        tmp_path,
        git_executable=None,
        max_total_bytes=100,
        max_memory_bytes=100,
        max_elapsed_seconds=60,
        started=0,
        clock=lambda: 1,
    )

    assert result.available is expected_available
    assert result.files == (frozenset({b"file"}) if expected_available else frozenset())


def test_discovery_applies_remaining_time_stream_and_memory_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tracking._validate_executable(_executable(tmp_path / "git"))
    monkeypatch.setattr(tracking, "_select_executable", lambda root, explicit: executable)
    captured: dict[str, object] = {}

    def run(*args: object, **kwargs: object) -> tuple[int, bytes, bytes]:
        captured.update(kwargs)
        return 0, b"a/b/c\0", b""

    monkeypatch.setattr(tracking, "_run_git", run)
    result = tracking._discover_tracked_paths(
        tmp_path,
        git_executable=None,
        max_total_bytes=50,
        max_memory_bytes=20,
        max_elapsed_seconds=12,
        started=0,
        clock=lambda: 3,
    )

    assert result.available
    assert result.directory_prefixes == frozenset({b"a", b"a/b"})
    assert result.bytes_read == len(b"a/b/c\0")
    assert result.memory_consumed == len(b"a/b/c\0") + len(b"a") + len(b"a/b")
    assert captured["timeout"] == 9
    assert captured["stdout_limit"] == 20

    monkeypatch.setattr(tracking, "_run_git", lambda *args, **kwargs: (0, b"a/b/c\0", b""))
    assert not tracking._discover_tracked_paths(
        tmp_path,
        git_executable=None,
        max_total_bytes=7,
        max_memory_bytes=7,
        max_elapsed_seconds=12,
        started=0,
        clock=lambda: 3,
    ).available


def test_discovery_falls_back_before_spawn_when_selection_or_deadline_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tracking, "_select_executable", lambda root, explicit: None)
    assert not tracking._discover_tracked_paths(
        tmp_path,
        git_executable=None,
        max_total_bytes=1,
        max_memory_bytes=1,
        max_elapsed_seconds=1,
        started=0,
        clock=lambda: 0,
    ).available

    executable = tracking._validate_executable(_executable(tmp_path / "git"))
    monkeypatch.setattr(tracking, "_select_executable", lambda root, explicit: executable)
    monkeypatch.setattr(
        tracking,
        "_run_git",
        lambda *args, **kwargs: pytest.fail("expired lookup must not spawn"),
    )
    assert not tracking._discover_tracked_paths(
        tmp_path,
        git_executable=None,
        max_total_bytes=1,
        max_memory_bytes=1,
        max_elapsed_seconds=1,
        started=0,
        clock=lambda: 2,
    ).available
