"""Supported Python entry points for repository scanning."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from slygentify._errors import ScanError, ScanValidationError
from slygentify._projection import project_scan
from slygentify._scan import _scan_foundation, _ScanFoundationError
from slygentify.models import ScanProjection, ScanResult
from slygentify.traceability import implements

__all__ = ["ScanError", "ScanValidationError", "map_repository", "scan_repository"]


@implements("REQ017")
def scan_repository(
    path: str | os.PathLike[str] = ".",
    *,
    git_executable: str | os.PathLike[str] | None = None,
) -> ScanResult:
    """Inspect the nearest Git repository, optionally using an exact Git executable."""
    try:
        _, result = _scan_foundation(Path(path), git_executable=git_executable)
    except (_ScanFoundationError, OSError, TypeError, ValueError) as error:
        raise ScanError(str(error)) from None
    if not isinstance(result, ScanResult):  # pragma: no cover - private contract
        raise ScanError("scan foundation returned an invalid result")
    return result


@implements("REQ043")
def map_repository(
    path: str | os.PathLike[str] = ".",
    *,
    scope: str = ".",
    sections: Iterable[str] | None = None,
    max_bytes: int | Literal["unlimited"] = 8 * 1024,
    git_executable: str | os.PathLike[str] | None = None,
) -> ScanProjection:
    """Freshly scan and project operating context for one logical repository path."""
    result = scan_repository(path, git_executable=git_executable)
    return project_scan(result, scope=scope, sections=sections, max_bytes=max_bytes)
