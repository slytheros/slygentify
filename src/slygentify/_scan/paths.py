"""Pure helpers for validated repository-relative POSIX paths."""

from __future__ import annotations

from bisect import bisect_left
from pathlib import PurePosixPath


def parent(path: str) -> str:
    """Return the lexical parent, using ``.`` for a root child."""

    value, separator, _ = path.rpartition("/")
    return value if separator else "."


def path_metadata(path: str) -> tuple[str, str]:
    """Return the lexical parent and basename for a relative path."""

    value, separator, name = path.rpartition("/")
    return (value if separator else ".", name if separator else path)


def nearest_ancestor(path: str, roots: set[str] | frozenset[str]) -> str | None:
    """Return the nearest candidate root containing *path*, including itself."""

    current = path
    while True:
        if current in roots:
            return current
        if current == ".":
            return None
        current = parent(current)


def descendant_paths(root: str, sorted_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return ordered paths at or below *root* without scanning unrelated prefixes."""

    if root == ".":
        return sorted_paths
    prefix = f"{root}/"
    start = bisect_left(sorted_paths, prefix)
    end = bisect_left(sorted_paths, f"{prefix}{chr(0x10FFFF)}")
    return tuple(path for path in sorted_paths[start:end] if path.startswith(prefix))


def safe_member(base: str, value: str) -> str | None:
    """Return a safe in-root lexical member path, or ``None`` for an unsafe value."""

    normalized = value.replace("\\", "/").strip().rstrip("/")
    if not normalized or normalized.startswith("/") or ":" in normalized:
        return None
    combined = PurePosixPath(base, normalized) if base != "." else PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in combined.parts):
        return None
    return combined.as_posix()
