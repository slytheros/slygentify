"""Private, shared repository selection without initialization dependencies."""

from __future__ import annotations

import os
from pathlib import Path

AGENTS_FILENAME = "AGENTS.md"


class RepositoryPathError(Exception):
    """A selected path cannot safely identify a containing Git repository."""


def find_git_root(path: Path) -> Path:
    """Return the nearest Git root containing *path* without invoking Git."""
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RepositoryPathError(f"path cannot be resolved: {path}") from error
    if not resolved.is_dir():
        raise RepositoryPathError(f"path is not a directory: {path}")
    for candidate in (resolved, *resolved.parents):
        if os.path.lexists(candidate / ".git"):
            return candidate
    raise RepositoryPathError(f"no Git repository contains: {path}")
