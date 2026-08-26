"""Deterministic bounded-view doubles for detector tests."""

from __future__ import annotations

from collections.abc import Mapping

from slygentify._scan.contracts import PathCandidate
from slygentify._scan.paths import path_metadata


class InMemoryDetectorView:
    """A complete immutable catalog with optional unreadable files."""

    def __init__(
        self,
        files: Mapping[str, bytes | None],
        *,
        paths: tuple[str, ...] | None = None,
    ) -> None:
        self._files = dict(files)
        self._paths = tuple(sorted(paths if paths is not None else self._files))
        self._candidates = tuple(PathCandidate(path, *path_metadata(path)) for path in self._paths)

    def paths(self) -> tuple[str, ...]:
        return self._paths

    def path_candidates(self) -> tuple[PathCandidate, ...]:
        return self._candidates

    def direct_children(self, parent: str) -> tuple[PathCandidate, ...]:
        return tuple(candidate for candidate in self._candidates if candidate.parent == parent)

    def checkpoint(self) -> bool:
        return False

    def read_bytes(self, path: str) -> bytes | None:
        return self._files.get(path)
