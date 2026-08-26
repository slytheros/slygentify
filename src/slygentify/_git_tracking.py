"""Bounded Git-backed tracked-path discovery for repository inspection."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from slygentify.traceability import implements

_GIT_TIMEOUT_SECONDS = 10.0
_STDERR_LIMIT = 64 * 1024
_READ_SIZE = 64 * 1024
_POLL_SECONDS = 0.01
_IS_WINDOWS = os.name == "nt"


class _InvalidGitExecutableError(Exception):
    """An explicit Git executable selection is invalid."""


@dataclass(frozen=True, slots=True)
class _TrackedPaths:
    files: frozenset[bytes]
    directory_prefixes: frozenset[bytes]
    available: bool
    bytes_read: int = 0
    memory_consumed: int = 0


@dataclass(frozen=True, slots=True)
class _Executable:
    path: Path
    identity: tuple[int, int, int, int, int]


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _validate_executable(path: Path) -> _Executable:
    resolved = path.resolve(strict=True)
    metadata = resolved.stat(follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise OSError("Git executable is not a regular executable file")
    return _Executable(resolved, _identity(metadata))


def _select_executable(
    root: Path,
    explicit: str | os.PathLike[str] | None,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> _Executable | None:
    if explicit is not None:
        try:
            raw = os.path.expanduser(os.fspath(explicit))
            return _validate_executable(Path(raw))
        except (OSError, TypeError, ValueError) as error:
            raise _InvalidGitExecutableError(
                "git_executable must identify an existing regular executable file"
            ) from error

    try:
        discovered = which("git")
    except (OSError, ValueError):
        return None
    if discovered is None:
        return None
    try:
        executable = _validate_executable(Path(discovered))
    except (OSError, ValueError):
        return None
    if _inside(root, executable.path):
        return None
    return executable


def _git_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = {
        key: value for key, value in source.items() if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _capture_stream(
    stream: BinaryIO,
    limit: int,
    destination: bytearray,
    exhausted: threading.Event,
) -> None:
    try:
        while chunk := stream.read(_READ_SIZE):
            remaining = limit - len(destination)
            if len(chunk) > remaining:
                destination.extend(chunk[: max(0, remaining)])
                exhausted.set()
                return
            destination.extend(chunk)
    except OSError:
        exhausted.set()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=0.25)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=0.25)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run_git(
    executable: _Executable,
    root: Path,
    *,
    timeout: float,
    stdout_limit: int,
    environment: Mapping[str, str],
    clock: Callable[[], float],
) -> tuple[int, bytes, bytes] | None:
    try:
        if _identity(executable.path.stat(follow_symlinks=False)) != executable.identity:
            return None
    except OSError:
        return None

    arguments = [
        os.fspath(executable.path),
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
    try:
        process = subprocess.Popen(
            arguments,
            cwd=os.fspath(root),
            env=dict(environment),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
    except OSError:
        return None
    if process.stdout is None or process.stderr is None:
        _stop_process(process)
        return None

    stdout = bytearray()
    stderr = bytearray()
    exhausted = threading.Event()
    stdout_thread = threading.Thread(
        target=_capture_stream,
        args=(process.stdout, stdout_limit, stdout, exhausted),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_capture_stream,
        args=(process.stderr, _STDERR_LIMIT, stderr, exhausted),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    deadline = clock() + timeout
    try:
        while process.poll() is None:
            remaining = deadline - clock()
            if exhausted.is_set() or remaining <= 0:
                _stop_process(process)
                return None
            try:
                process.wait(timeout=min(_POLL_SECONDS, remaining))
            except subprocess.TimeoutExpired:
                continue
        stdout_thread.join(timeout=0.25)
        stderr_thread.join(timeout=0.25)
        if stdout_thread.is_alive() or stderr_thread.is_alive() or exhausted.is_set():
            return None
        return process.returncode, bytes(stdout), bytes(stderr)
    finally:
        process.stdout.close()
        process.stderr.close()
        _stop_process(process)


def _safe_path(path: bytes) -> bool:
    if not path or path.startswith(b"/") or path.endswith(b"/"):
        return False
    if _IS_WINDOWS and (b"\\" in path or (len(path) >= 2 and path[1:2] == b":")):
        return False
    return all(part not in {b"", b".", b".."} for part in path.split(b"/"))


def _parse_paths(output: bytes) -> tuple[frozenset[bytes], frozenset[bytes]] | None:
    if not output:
        return frozenset(), frozenset()
    if not output.endswith(b"\0"):
        return None
    records = output[:-1].split(b"\0")
    if any(not _safe_path(path) for path in records):
        return None
    files = frozenset(records)
    prefixes: set[bytes] = set()
    for path in files:
        parts = path.split(b"/")
        prefixes.update(b"/".join(parts[:index]) for index in range(1, len(parts)))
    return files, frozenset(prefixes)


@implements("REQ011", "REQ013", "REQ014", "REQ017")
def _discover_tracked_paths(
    root: Path,
    *,
    git_executable: str | os.PathLike[str] | None,
    max_total_bytes: int | None,
    max_memory_bytes: int | None,
    max_elapsed_seconds: float | None,
    started: float,
    clock: Callable[[], float] = time.monotonic,
) -> _TrackedPaths:
    executable = _select_executable(root, git_executable)
    if executable is None:
        return _TrackedPaths(frozenset(), frozenset(), False)
    remaining = (
        float("inf") if max_elapsed_seconds is None else max_elapsed_seconds - (clock() - started)
    )
    timeout = min(_GIT_TIMEOUT_SECONDS, remaining)
    bounded = (value for value in (max_total_bytes, max_memory_bytes) if value is not None)
    stdout_limit = min(bounded, default=2**63 - 1)
    if timeout <= 0 or stdout_limit < 0:
        return _TrackedPaths(frozenset(), frozenset(), False)
    result = _run_git(
        executable,
        root,
        timeout=timeout,
        stdout_limit=stdout_limit,
        environment=_git_environment(os.environ),
        clock=clock,
    )
    if result is None:
        return _TrackedPaths(frozenset(), frozenset(), False)
    returncode, stdout, stderr = result
    if returncode != 0 or stderr:
        return _TrackedPaths(frozenset(), frozenset(), False)
    parsed = _parse_paths(stdout)
    if parsed is None:
        return _TrackedPaths(frozenset(), frozenset(), False)
    files, prefixes = parsed
    memory_consumed = len(stdout) + sum(len(item) for item in prefixes)
    if max_memory_bytes is not None and memory_consumed + 1 > max_memory_bytes:
        return _TrackedPaths(frozenset(), frozenset(), False)
    return _TrackedPaths(files, prefixes, True, len(stdout), memory_consumed)
