"""Private scan orchestration over the bounded inspection kernel."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable
from pathlib import Path

from slygentify._configuration import EffectiveConfiguration, load_configuration
from slygentify._git_tracking import _discover_tracked_paths, _InvalidGitExecutableError
from slygentify._repository import RepositoryPathError, find_git_root
from slygentify._scan.contracts import PartialCause
from slygentify._scan.kernel import (
    _inspect,
    _is_reparse,
    _Limits,
    _limits,
    _ScanExecution,
    _ScanFoundationError,
)
from slygentify._scan.normalization import _normalize
from slygentify.traceability import implements


@implements("REQ011", "REQ014", "REQ015", "REQ016", "REQ035", "REQ036")
def _scan_foundation(
    path: Path,
    *,
    git_executable: str | os.PathLike[str] | None = None,
    limits: _Limits | None = None,
    configuration: EffectiveConfiguration | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> _ScanExecution:
    """Build the private normalized scan used by the follow-on public API."""
    try:
        root = find_git_root(path)
    except RepositoryPathError as error:
        raise _ScanFoundationError(str(error)) from error
    root = root.resolve(strict=True)
    marker = root / ".git"
    try:
        marker_metadata = marker.lstat()
    except OSError as error:
        raise _ScanFoundationError("repository marker cannot be inspected safely") from error
    if (
        _is_reparse(marker_metadata)
        or stat.S_ISLNK(marker_metadata.st_mode)
        or not (stat.S_ISREG(marker_metadata.st_mode) or stat.S_ISDIR(marker_metadata.st_mode))
    ):
        raise _ScanFoundationError("repository marker is not a safe file or directory")
    if configuration is None:
        try:
            configuration = load_configuration(root)
        except ValueError as error:
            raise _ScanFoundationError(str(error)) from error
    effective_limits = limits or _limits(configuration)
    started = clock()
    try:
        tracked = _discover_tracked_paths(
            root,
            git_executable=git_executable,
            max_total_bytes=effective_limits.max_total_bytes,
            max_memory_bytes=effective_limits.max_memory_bytes,
            max_elapsed_seconds=effective_limits.max_elapsed_seconds,
            started=started,
            clock=clock,
        )
    except _InvalidGitExecutableError as error:
        raise _ScanFoundationError(str(error)) from error
    inspection = _inspect(
        root,
        limits=effective_limits,
        clock=clock,
        started=started,
        tracked=tracked,
        configured_ignore=configuration.ignore,
    )
    content_fingerprints: dict[str, str] = {}
    partial_causes: list[PartialCause] = []
    result = _normalize(
        root,
        inspection,
        memory_limit=effective_limits.max_memory_bytes,
        configuration=configuration,
        content_fingerprints=content_fingerprints,
        partial_causes=partial_causes,
    )
    return _ScanExecution(
        root,
        result,
        configuration,
        content_fingerprints,
        tuple(partial_causes),
    )
