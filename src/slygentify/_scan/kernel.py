"""Private containment-aware inspection kernel for repository scans."""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import os
import stat
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, cast

from pathspec import PathSpec
from pathspec.patterns.gitignore import GitIgnorePatternError

from slygentify._configuration import EffectiveConfiguration
from slygentify._git_tracking import _TrackedPaths
from slygentify._scan.contracts import DiagnosticCandidate as _DiagnosticCandidate
from slygentify._scan.contracts import PathCandidate as _PathCandidate
from slygentify._scan.paths import parent as _parent
from slygentify._scan.paths import path_metadata as _path_metadata
from slygentify.models import ScanResult, SkippedScope
from slygentify.traceability import implements

_RELEVANT_NAMES = frozenset({"Cargo.toml", "CMakeLists.txt", "go.mod", "go.work", "pom.xml"})
_BUILTIN_DIRECTORIES = frozenset(
    {".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".venv", "__pycache__", "node_modules"}
)
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "auth.toml",
        "config.json",
        "credentials",
        "kubeconfig",
        "terraform.tfstate",
    }
)
_SENSITIVE_SUFFIXES = (
    ".env",
    ".jks",
    ".kdbx",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".tfplan",
    ".tfstate",
    ".tfstate.backup",
)
_WINDOWS_REPARSE_ATTRIBUTE = 0x400
_IS_WINDOWS = os.name == "nt"


def _generic_content_relevant(name: str) -> bool:
    return name in _RELEVANT_NAMES or name.endswith(".kicad_pro")


def _gitignore_bases(path: str) -> Iterator[str]:
    """Yield Gitignore bases that can apply to *path*, from root to nearest parent."""

    yield "."
    parent = _parent(path)
    if parent == ".":
        return
    current = ""
    for part in parent.split("/"):
        current = part if not current else f"{current}/{part}"
        yield current


class _ScanFoundationError(Exception):
    """A controlled failure at the repository-inspection boundary."""


@dataclass(frozen=True, slots=True)
class _Limits:
    max_depth: int | None = 256
    max_entries: int | None = 1_000_000
    max_file_bytes: int | None = 256 * 1024 * 1024
    max_total_bytes: int | None = 16 * 1024 * 1024 * 1024
    max_elapsed_seconds: float | None = 30 * 60
    max_open_files: int | None = 256
    max_memory_bytes: int | None = 2 * 1024 * 1024 * 1024


def _limits(configuration: EffectiveConfiguration) -> _Limits:
    return _Limits(
        max_depth=configuration.value("max_depth"),
        max_entries=configuration.value("max_entries"),
        max_file_bytes=configuration.value("max_file_bytes"),
        max_total_bytes=configuration.value("max_total_bytes"),
        max_elapsed_seconds=configuration.value("max_elapsed_seconds"),
        max_open_files=configuration.value("max_open_files"),
        max_memory_bytes=configuration.value("max_memory_bytes"),
    )


@dataclass(frozen=True, slots=True)
class _Entry:
    path: str
    is_dir: bool
    is_file: bool
    is_link: bool
    is_reparse: bool
    size: int
    identity: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _Inspection:
    files: Mapping[str, bytes]
    skipped: tuple[SkippedScope, ...]
    diagnostics: tuple[_DiagnosticCandidate, ...]
    partial: bool
    entries: Mapping[str, _Entry] = field(default_factory=dict)
    root: Path | None = None
    limits: _Limits | None = None
    bytes_read: int = 0
    started: float = 0.0
    memory_consumed: int = 0
    clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True, slots=True)
class _ScanExecution:
    """Private scan details retained for later provenance composition."""

    root: Path
    result: ScanResult
    configuration: EffectiveConfiguration
    content_fingerprints: Mapping[str, str]

    def __iter__(self) -> Iterator[Any]:
        """Retain the existing private tuple-unpacking convention."""
        yield self.root
        yield self.result


class _MemoryLedger:
    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.consumed = 0

    def add(self, amount: int) -> bool:
        if self.limit is not None and self.consumed + amount > self.limit:
            return False
        self.consumed += amount
        return True

    def release(self, amount: int) -> None:
        self.consumed -= amount


class _IgnoreRules:
    def __init__(self) -> None:
        self._rules_by_base: dict[str, list[PathSpec[Any]]] = {}

    def add(self, base: str, text: str) -> int:
        lines = text.splitlines()
        self._rules_by_base.setdefault(base, []).append(PathSpec.from_lines("gitignore", lines))
        return sum(len(line.encode("utf-8")) for line in lines)

    def ignored(self, path: str, *, is_dir: bool) -> bool:
        return self.decision(path, is_dir=is_dir) is True

    def decision(self, path: str, *, is_dir: bool) -> bool | None:
        decision: bool | None = None
        for base in _gitignore_bases(path):
            rules_by_base = self._rules_by_base.get(base, ())
            relative = path if base == "." else path[len(base) + 1 :]
            for rules in rules_by_base:
                result = rules.check_file(f"{relative}/" if is_dir else relative)
                if result.include is not None:
                    decision = result.include
        return decision


class _RepositoryView:
    """A detector capability exposing only safely catalogued repository files."""

    def __init__(self, inspection: _Inspection | Mapping[str, bytes]) -> None:
        if isinstance(inspection, _Inspection):
            self._files = dict(inspection.files)
            self._entries = inspection.entries
            self._root = inspection.root
            self._limits = inspection.limits
            self._bytes_read = inspection.bytes_read
            self._started = inspection.started
            self._memory_consumed = inspection.memory_consumed
            self._clock = inspection.clock
            self._elapsed_exhausted = any(
                item.reason == "max_elapsed_seconds" for item in inspection.skipped
            )
        else:
            self._files = dict(inspection)
            self._entries = {}
            self._root = None
            self._limits = None
            self._bytes_read = sum(len(value) for value in inspection.values())
            self._started = 0.0
            self._memory_consumed = sum(len(value) for value in inspection.values())
            self._clock = time.monotonic
            self._elapsed_exhausted = False
        self.skipped: list[SkippedScope] = []
        self.diagnostics: list[_DiagnosticCandidate] = []
        self.partial = False
        self._catalog_memory_consumed = 0
        self._paths = tuple(sorted(set(self._entries) | set(self._files)))
        self._path_candidates, self._children = self._build_path_catalog()

    def _build_path_catalog(
        self,
    ) -> tuple[tuple[_PathCandidate, ...], Mapping[str, tuple[_PathCandidate, ...]]]:
        metadata = tuple((path, *_path_metadata(path)) for path in self._paths)
        retained_strings = {parent for _, parent, _ in metadata} | {name for _, _, name in metadata}
        retained_bytes = sum(len(value.encode("utf-8")) for value in retained_strings)
        if (
            self._limits is not None
            and self._limits.max_memory_bytes is not None
            and self._memory_consumed + retained_bytes > self._limits.max_memory_bytes
        ):
            self.skipped.append(
                _skip(
                    ".",
                    "max_memory_bytes",
                    self._limits.max_memory_bytes,
                    self._memory_consumed,
                    omitted_scope="**",
                )
            )
            self.partial = True
            return (), MappingProxyType({})
        self._memory_consumed += retained_bytes
        self._catalog_memory_consumed = retained_bytes
        parents: dict[str, str] = {}
        names: dict[str, str] = {}
        children: dict[str, list[_PathCandidate]] = {}
        candidates: list[_PathCandidate] = []
        for path, parent, name in metadata:
            parent = parents.setdefault(parent, parent)
            name = names.setdefault(name, name)
            candidate = _PathCandidate(path, parent, name)
            candidates.append(candidate)
            children.setdefault(parent, []).append(candidate)
        return (
            tuple(candidates),
            MappingProxyType({parent: tuple(items) for parent, items in children.items()}),
        )

    def paths(self) -> tuple[str, ...]:
        return self._paths

    def path_candidates(self) -> tuple[_PathCandidate, ...]:
        return self._path_candidates

    def direct_children(self, parent: str) -> tuple[_PathCandidate, ...]:
        return self._children.get(parent, ())

    def has_path(self, path: str) -> bool:
        return path in self._entries or path in self._files

    def release_path_catalog(self) -> None:
        """Release detector-only index accounting before normalized records are retained."""

        self._path_candidates = ()
        self._children = MappingProxyType({})
        self._memory_consumed -= self._catalog_memory_consumed
        self._catalog_memory_consumed = 0

    def checkpoint(self) -> bool:
        if self._elapsed_exhausted:
            self.partial = True
            return True
        elapsed = self._clock() - self._started
        if (
            self._limits is None
            or self._limits.max_elapsed_seconds is None
            or elapsed < self._limits.max_elapsed_seconds
        ):
            return False
        if not any(item.reason == "max_elapsed_seconds" for item in self.skipped):
            self.skipped.append(
                _skip(
                    ".",
                    "max_elapsed_seconds",
                    max(1, int(self._limits.max_elapsed_seconds)),
                    max(0, int(elapsed)),
                    omitted_scope="**",
                )
            )
        self.partial = True
        self._elapsed_exhausted = True
        return True

    def read_bytes(self, path: str) -> bytes | None:
        cached = self._files.get(path)
        if cached is not None:
            return cached
        entry = self._entries.get(path)
        if entry is None or self._root is None or self._limits is None:
            return None

        if self.checkpoint():
            return None
        try:
            data = _read_file(self._root, entry, self._limits)
            if (
                self._limits.max_total_bytes is not None
                and self._bytes_read + len(data) > self._limits.max_total_bytes
            ):
                raise OverflowError("max_total_bytes")
            if (
                self._limits.max_memory_bytes is not None
                and self._memory_consumed + len(data) > self._limits.max_memory_bytes
            ):
                raise MemoryError
            self._files[path] = data
            self._bytes_read += len(data)
            self._memory_consumed += len(data)
            return data
        except TimeoutError:
            self.skipped.append(
                _skip(
                    path,
                    "max_elapsed_seconds",
                    max(1, int(self._limits.max_elapsed_seconds or 1)),
                )
            )
        except OverflowError as error:
            reason = str(error)
            limit = (
                self._limits.max_file_bytes
                if reason == "max_file_bytes"
                else self._limits.max_total_bytes
            )
            self.skipped.append(_skip(path, reason, limit, self._bytes_read))
        except MemoryError:
            self.skipped.append(
                _skip(
                    path,
                    "max_memory_bytes",
                    self._limits.max_memory_bytes,
                    self._memory_consumed,
                )
            )
        except OSError:
            self.skipped.append(_skip(path, "unsafe_file"))
        self.diagnostics.append(
            _DiagnosticCandidate(
                "inspection.unreadable-evidence",
                path,
                "Evidence file could not be read safely within the inspection limits.",
                True,
            )
        )
        self.partial = True
        return None

    def content_fingerprints(self) -> dict[str, str]:
        """Return digests for files already read through this bounded view."""
        return {
            path: hashlib.sha256(data).hexdigest() for path, data in sorted(self._files.items())
        }


def _relative(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent}/{name}"


def _path(root: Path, relative: str) -> Path:
    return root if relative == "." else root.joinpath(*PurePosixPath(relative).parts)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _WINDOWS_REPARSE_ATTRIBUTE)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _windows_final_path(descriptor: int) -> Path:
    msvcrt = importlib.import_module("msvcrt")
    windll = cast(Any, vars(ctypes)["windll"])
    function = windll.kernel32.GetFinalPathNameByHandleW
    function.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
    function.restype = ctypes.c_ulong
    buffer = ctypes.create_unicode_buffer(32_768)
    length = function(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
    if length == 0 or length >= len(buffer):
        raise OSError("opened file path cannot be resolved")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = f"\\\\{value[8:]}"
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _opened_final_path(descriptor: int, candidate: Path) -> Path:
    if _IS_WINDOWS:
        return _windows_final_path(descriptor)
    proc_path = Path(f"/proc/self/fd/{descriptor}")
    if proc_path.exists():
        return Path(os.path.realpath(proc_path))
    return candidate.resolve(strict=True)


def _entry(root: Path, parent: str, name: str) -> _Entry:
    relative = _relative(parent, name)
    candidate = _path(root, relative)
    metadata = candidate.lstat()
    mode = metadata.st_mode
    return _Entry(
        path=relative,
        is_dir=stat.S_ISDIR(mode),
        is_file=stat.S_ISREG(mode),
        is_link=stat.S_ISLNK(mode),
        is_reparse=_is_reparse(metadata),
        size=metadata.st_size,
        identity=_identity(metadata),
    )


def _list_entries(root: Path, relative: str) -> tuple[_Entry, ...]:
    directory = _path(root, relative)
    before = directory.stat(follow_symlinks=False)
    if _is_reparse(before) or not stat.S_ISDIR(before.st_mode):
        raise OSError("directory identity is unsafe")
    resolved = directory.resolve(strict=True)
    if not _inside(root, resolved):
        raise OSError("directory escapes the repository root")
    names = sorted(item.name for item in os.scandir(directory))
    entries = tuple(_entry(root, relative, name) for name in names)
    after = directory.stat(follow_symlinks=False)
    if _identity(before) != _identity(after):
        raise OSError("directory identity changed during enumeration")
    return entries


def _read_file(root: Path, entry: _Entry, limits: _Limits) -> bytes:
    candidate = _path(root, entry.path)
    resolved = candidate.resolve(strict=True)
    if not _inside(root, resolved) or resolved != candidate:
        raise OSError("file containment cannot be proven")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags)
    try:
        opened_path = _opened_final_path(descriptor, candidate)
        if not _inside(root, opened_path) or opened_path != candidate:
            raise OSError("opened file escapes the repository root")
        before = os.fstat(descriptor)
        if _identity(before) != entry.identity or not stat.S_ISREG(before.st_mode):
            raise OSError("file identity changed before read")
        if limits.max_file_bytes is not None and before.st_size > limits.max_file_bytes:
            raise OverflowError("max_file_bytes")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(None if limits.max_file_bytes is None else limits.max_file_bytes + 1)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after) or before.st_size != after.st_size:
            raise OSError("file identity changed during read")
        if limits.max_file_bytes is not None and len(data) > limits.max_file_bytes:
            raise OverflowError("max_file_bytes")
        return data
    finally:
        os.close(descriptor)


def _sensitive(path: str) -> bool:
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    parts = PurePosixPath(lowered).parts
    return (
        name in _SENSITIVE_NAMES
        or name.startswith(".env.")
        or name.endswith(_SENSITIVE_SUFFIXES)
        or ".terraform" in parts
        or ".ssh" in parts
        or (".aws" in parts and name in {"config", "credentials"})
        or (".kube" in parts and name == "config")
    )


def _skip(
    path: str,
    reason: str,
    limit: int | None = None,
    consumed: int | None = None,
    *,
    omitted_scope: str | None = None,
) -> SkippedScope:
    return SkippedScope(
        scope=path,
        reason=reason,
        effective_limit=limit,
        consumed=consumed,
        omitted_scope=omitted_scope or path,
    )


@implements("REQ011", "REQ012", "REQ013", "REQ014")
def _inspect(
    root: Path,
    *,
    limits: _Limits,
    clock: Callable[[], float] = time.monotonic,
    started: float | None = None,
    tracked: _TrackedPaths | None = None,
    configured_ignore: tuple[str, ...] = (),
) -> _Inspection:
    started = clock() if started is None else started
    tracked = tracked or _TrackedPaths(frozenset(), frozenset(), True)
    queue: deque[tuple[str, int]] = deque()
    ledger = _MemoryLedger(limits.max_memory_bytes)
    ledger.add(tracked.memory_consumed)
    ignore = _IgnoreRules()
    try:
        configured_rules = PathSpec.from_lines("gitignore", configured_ignore)
    except (
        GitIgnorePatternError
    ) as error:  # pragma: no cover - configuration validates patterns first
        raise _ScanFoundationError("slygentify.toml is invalid or unsafe") from error
    files: dict[str, bytes] = {}
    catalog: dict[str, _Entry] = {}
    skipped: list[SkippedScope] = []
    diagnostics: list[_DiagnosticCandidate] = []
    entries_examined = 0
    bytes_read = tracked.bytes_read
    partial = not tracked.available
    stop = False
    root_device = root.stat().st_dev

    if not tracked.available:
        skipped.append(_skip(".", "git_tracking_unavailable", omitted_scope="**"))
        diagnostics.append(
            _DiagnosticCandidate(
                "inspection.git-tracked-paths-unavailable",
                ".",
                "Tracked paths were unavailable; checked-out Gitignore rules were applied "
                "without tracked-file exceptions.",
                True,
            )
        )
    if ledger.add(1):
        queue.append((".", 0))
    else:
        skipped.append(_skip(".", "max_memory_bytes", limits.max_memory_bytes, ledger.consumed))
        partial = True

    while queue and not stop:
        relative, depth = queue.popleft()
        ledger.release(len(relative.encode("utf-8")))
        elapsed = clock() - started
        if limits.max_elapsed_seconds is not None and elapsed >= limits.max_elapsed_seconds:
            skipped.append(
                _skip(
                    ".",
                    "max_elapsed_seconds",
                    max(1, int(limits.max_elapsed_seconds)),
                    max(0, int(elapsed)),
                    omitted_scope="**",
                )
            )
            partial = True
            break
        try:
            entries = _list_entries(root, relative)
        except OSError:
            skipped.append(_skip(relative, "unsafe_directory"))
            diagnostics.append(
                _DiagnosticCandidate(
                    "inspection.unsafe-directory",
                    relative,
                    "Directory could not be inspected safely.",
                    True,
                )
            )
            partial = True
            continue

        if relative != "." and any(PurePosixPath(item.path).name == ".git" for item in entries):
            skipped.append(_skip(relative, "nested_repository"))
            continue

        gitignore = next(
            (item for item in entries if PurePosixPath(item.path).name == ".gitignore"), None
        )
        if (
            gitignore is not None
            and gitignore.is_file
            and not gitignore.is_link
            and not gitignore.is_reparse
        ):
            try:
                if gitignore.identity[0] != root_device:
                    raise OverflowError("mount_boundary")
                data = _read_file(root, gitignore, limits)
                if (
                    limits.max_total_bytes is not None
                    and bytes_read + len(data) > limits.max_total_bytes
                ):
                    raise OverflowError("max_total_bytes")
                text = data.decode("utf-8", errors="strict")
                pattern_bytes = ignore.add(relative, text)
                if not ledger.add(pattern_bytes):
                    raise MemoryError
                bytes_read += len(data)
            except (GitIgnorePatternError, OSError, UnicodeError):
                skipped.append(_skip(gitignore.path, "invalid_gitignore"))
                diagnostics.append(
                    _DiagnosticCandidate(
                        "inspection.invalid-gitignore",
                        gitignore.path,
                        "Gitignore rules could not be read safely.",
                        True,
                    )
                )
                partial = True
            except OverflowError as error:
                reason = str(error)
                limit = (
                    None
                    if reason == "mount_boundary"
                    else limits.max_file_bytes
                    if reason == "max_file_bytes"
                    else limits.max_total_bytes
                )
                skipped.append(_skip(gitignore.path, reason, limit, bytes_read))
                partial = True
            except MemoryError:
                skipped.append(
                    _skip(
                        gitignore.path, "max_memory_bytes", limits.max_memory_bytes, ledger.consumed
                    )
                )
                partial = True

        for entry in entries:
            entries_examined += 1
            if limits.max_entries is not None and entries_examined > limits.max_entries:
                skipped.append(
                    _skip(entry.path, "max_entries", limits.max_entries, entries_examined - 1)
                )
                partial = True
                stop = True
                break
            name = PurePosixPath(entry.path).name
            if name in {".git", ".gitignore"}:
                continue
            if entry.is_link or entry.is_reparse:
                skipped.append(_skip(entry.path, "link_or_reparse"))
                continue
            if entry.identity[0] != root_device:
                skipped.append(_skip(entry.path, "mount_boundary"))
                continue
            if _sensitive(entry.path):
                skipped.append(_skip(entry.path, "sensitive_content"))
                continue
            relative_path = f"{entry.path}/" if entry.is_dir else entry.path
            configured = configured_rules.check_file(relative_path).include
            gitignore_decision = ignore.decision(entry.path, is_dir=entry.is_dir)
            if configured is True:
                skipped.append(_skip(entry.path, "configuration"))
                continue
            if (
                configured is not False
                and gitignore_decision is not False
                and entry.is_dir
                and name in _BUILTIN_DIRECTORIES
            ):
                skipped.append(_skip(entry.path, "built_in_exclusion"))
                continue
            tracked_path = os.fsencode(entry.path)
            retained_by_tracking = (entry.is_file and tracked_path in tracked.files) or (
                entry.is_dir and tracked_path in tracked.directory_prefixes
            )
            if configured is not False and gitignore_decision is True and not retained_by_tracking:
                skipped.append(_skip(entry.path, "gitignore"))
                continue
            if entry.is_dir:
                if limits.max_depth is not None and depth + 1 > limits.max_depth:
                    skipped.append(_skip(entry.path, "max_depth", limits.max_depth, depth))
                    partial = True
                    continue
                path_bytes = len(entry.path.encode("utf-8"))
                if not ledger.add(path_bytes):
                    skipped.append(
                        _skip(
                            entry.path, "max_memory_bytes", limits.max_memory_bytes, ledger.consumed
                        )
                    )
                    partial = True
                    continue
                queue.append((entry.path, depth + 1))
                continue
            if entry.is_file:
                catalog_size = len(entry.path.encode("utf-8"))
                if not ledger.add(catalog_size):
                    skipped.append(
                        _skip(
                            entry.path,
                            "max_memory_bytes",
                            limits.max_memory_bytes,
                            ledger.consumed,
                        )
                    )
                    partial = True
                    continue
                catalog[entry.path] = entry
            if not entry.is_file or not _generic_content_relevant(name):
                if not entry.is_file:
                    skipped.append(_skip(entry.path, "special_entry"))
                continue
            try:
                data = _read_file(root, entry, limits)
                if (
                    limits.max_total_bytes is not None
                    and bytes_read + len(data) > limits.max_total_bytes
                ):
                    raise OverflowError("max_total_bytes")
                if not ledger.add(len(data)):
                    raise MemoryError
                files[entry.path] = data
                bytes_read += len(data)
            except OSError:
                skipped.append(_skip(entry.path, "unsafe_file"))
                diagnostics.append(
                    _DiagnosticCandidate(
                        "inspection.unsafe-file", entry.path, "File could not be read safely.", True
                    )
                )
                partial = True
            except OverflowError as error:
                reason = str(error)
                limit = (
                    limits.max_file_bytes if reason == "max_file_bytes" else limits.max_total_bytes
                )
                skipped.append(_skip(entry.path, reason, limit, bytes_read))
                partial = True
            except MemoryError:
                skipped.append(
                    _skip(entry.path, "max_memory_bytes", limits.max_memory_bytes, ledger.consumed)
                )
                partial = True

    return _Inspection(
        files=MappingProxyType(files),
        skipped=tuple(sorted(skipped, key=lambda item: (item.scope, item.reason))),
        diagnostics=tuple(diagnostics),
        partial=partial,
        entries=MappingProxyType(catalog),
        root=root,
        limits=limits,
        bytes_read=bytes_read,
        started=started,
        memory_consumed=ledger.consumed,
        clock=clock,
    )
