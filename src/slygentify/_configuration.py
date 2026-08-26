"""Private, strict repository configuration loading for scans."""

from __future__ import annotations

import hashlib
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pathspec import PathSpec
from pathspec.patterns.gitignore import GitIgnorePatternError

from slygentify.traceability import implements

CONFIGURATION_FILENAME = "slygentify.toml"
_BOOTSTRAP_BYTES = 1024 * 1024
_LIMIT_NAMES = (
    "max_depth",
    "max_entries",
    "max_file_bytes",
    "max_total_bytes",
    "max_elapsed_seconds",
    "max_open_files",
    "max_memory_bytes",
)
_DEFAULTS = {
    "max_depth": 256,
    "max_entries": 1_000_000,
    "max_file_bytes": 256 * 1024 * 1024,
    "max_total_bytes": 16 * 1024 * 1024 * 1024,
    "max_elapsed_seconds": 30 * 60,
    "max_open_files": 256,
    "max_memory_bytes": 2 * 1024 * 1024 * 1024,
}
DEFAULT_MAX_AGENTS_BYTES = 4096
DEFAULT_MAX_COMPONENT_ENTRIES = 8
MIN_MAX_AGENTS_BYTES = 1536


class ConfigurationError(ValueError):
    """An untrusted root configuration cannot be used safely."""


@dataclass(frozen=True, slots=True)
class LimitRecord:
    name: str
    default: int
    requested: int | Literal["unlimited"]
    effective: int | Literal["unlimited"]
    source: Literal["default", "configuration", "invocation"]


@dataclass(frozen=True, slots=True)
class ComponentDeclaration:
    path: str
    ecosystem: str | None
    kind: str | None
    locator: str


@dataclass(frozen=True, slots=True)
class EffectiveConfiguration:
    ignore: tuple[str, ...]
    components: tuple[ComponentDeclaration, ...]
    limits: tuple[LimitRecord, ...]
    sha256: str | None
    max_agents_bytes: int | Literal["unlimited"] = DEFAULT_MAX_AGENTS_BYTES
    max_component_entries: int | Literal["unlimited"] = DEFAULT_MAX_COMPONENT_ENTRIES
    init_relaxed: bool = False

    @property
    def relaxed(self) -> bool:
        return any(
            item.source == "configuration"
            and (item.effective == "unlimited" or item.effective > item.default)
            for item in self.limits
        )

    def value(self, name: str) -> int | None:
        for item in self.limits:
            if item.name == name:
                return None if item.effective == "unlimited" else item.effective
        raise KeyError(name)


def _error() -> ConfigurationError:
    return ConfigurationError(
        "slygentify.toml is invalid or unsafe; no repository files were changed. "
        "Next: correct or remove slygentify.toml."
    )


def _safe_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise _error()
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.startswith("//")
        or (path.parts and ":" in path.parts[0])
        or (
            value != "."
            and (path.as_posix() != value or any(part in {".", ".."} for part in path.parts))
        )
    ):
        raise _error()
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error()
    return value


def _limit(value: object) -> int | Literal["unlimited"]:
    if value == "unlimited":
        return "unlimited"
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _error()
    return value


def _regular_bytes(root: Path, target: Path) -> bytes:
    try:
        before = target.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise OSError
        if before.st_size > _BOOTSTRAP_BYTES:  # pragma: no cover - platform size race guard
            raise OSError
        resolved = target.resolve(strict=True)
        if resolved != target or root not in (
            resolved,
            *resolved.parents,
        ):  # pragma: no cover - containment guard
            raise OSError
        with target.open("rb") as stream:
            data = stream.read(_BOOTSTRAP_BYTES + 1)
        after = target.lstat()
        if len(data) > _BOOTSTRAP_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ) != (  # pragma: no cover - read race guard
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            raise OSError
        return data
    except OSError as error:
        raise _error() from error


def _validate_component(root: Path, declaration: ComponentDeclaration) -> None:
    target = root if declaration.path == "." else root.joinpath(*declaration.path.split("/"))
    try:
        metadata = target.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(
            metadata.st_mode
        ):  # pragma: no cover - lstat guard
            raise OSError
        resolved = target.resolve(strict=True)
        if resolved != target or root not in (
            resolved,
            *resolved.parents,
        ):  # pragma: no cover - containment guard
            raise OSError
        if declaration.path != "." and os.path.lexists(target / ".git"):
            raise OSError
    except OSError as error:
        raise _error() from error


def _defaults() -> tuple[LimitRecord, ...]:
    return tuple(
        LimitRecord(name, _DEFAULTS[name], _DEFAULTS[name], _DEFAULTS[name], "default")
        for name in _LIMIT_NAMES
    )


@implements("REQ035", "REQ044")
def load_configuration(root: Path) -> EffectiveConfiguration:
    """Load the sole optional root configuration without searching elsewhere."""
    target = root / CONFIGURATION_FILENAME
    if not os.path.lexists(target):
        return EffectiveConfiguration((), (), _defaults(), None)
    data = _regular_bytes(root, target)
    try:
        text = data.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):  # pragma: no cover - tomllib rejects this before use
            raise ValueError
        value = tomllib.loads(text)
    except (UnicodeError, tomllib.TOMLDecodeError, ValueError) as error:
        raise _error() from error
    if not isinstance(value, dict) or set(value) - {"schema_version", "scan", "init"}:
        raise _error()
    if isinstance(value.get("schema_version"), bool) or value.get("schema_version") != 1:
        raise _error()
    scan = value.get("scan", {})
    if not isinstance(scan, dict) or set(scan) - {"ignore", "components", "limits"}:
        raise _error()
    raw_ignore = scan.get("ignore", [])
    if not isinstance(raw_ignore, list) or any(not isinstance(item, str) for item in raw_ignore):
        raise _error()
    try:
        PathSpec.from_lines("gitignore", raw_ignore)
    except GitIgnorePatternError as error:
        raise _error() from error
    raw_components = scan.get("components", [])
    if not isinstance(raw_components, list):
        raise _error()
    components: list[ComponentDeclaration] = []
    for index, item in enumerate(raw_components):
        if (
            not isinstance(item, dict)
            or set(item) - {"path", "ecosystem", "kind"}
            or "path" not in item
        ):  # pragma: no cover - TOML array shape
            raise _error()
        ecosystem = item.get("ecosystem")
        kind = item.get("kind")
        declaration = ComponentDeclaration(
            _safe_path(item["path"]),
            _identifier(ecosystem) if ecosystem is not None else None,
            _identifier(kind) if kind is not None else None,
            f"scan.components[{index}].path",
        )
        _validate_component(root, declaration)
        components.append(declaration)
    if len({item.path for item in components}) != len(
        components
    ):  # pragma: no cover - duplicate declarations
        raise _error()
    raw_limits = scan.get("limits", {})
    if not isinstance(raw_limits, dict) or set(raw_limits) - set(_LIMIT_NAMES):
        raise _error()
    records: list[LimitRecord] = []
    for name in _LIMIT_NAMES:
        default = _DEFAULTS[name]
        requested = _limit(raw_limits[name]) if name in raw_limits else default
        records.append(
            LimitRecord(
                name,
                default,
                requested,
                requested,
                "configuration" if name in raw_limits else "default",
            )
        )
    raw_init = value.get("init", {})
    if not isinstance(raw_init, dict) or set(raw_init) - {
        "max_agents_bytes",
        "max_component_entries",
    }:
        raise _error()
    max_agents_bytes = (
        _limit(raw_init["max_agents_bytes"])
        if "max_agents_bytes" in raw_init
        else DEFAULT_MAX_AGENTS_BYTES
    )
    if max_agents_bytes != "unlimited" and max_agents_bytes < MIN_MAX_AGENTS_BYTES:
        raise _error()
    max_component_entries = (
        _limit(raw_init["max_component_entries"])
        if "max_component_entries" in raw_init
        else DEFAULT_MAX_COMPONENT_ENTRIES
    )
    init_relaxed = (
        "max_agents_bytes" in raw_init
        and (max_agents_bytes == "unlimited" or max_agents_bytes > DEFAULT_MAX_AGENTS_BYTES)
    ) or (
        "max_component_entries" in raw_init
        and (
            max_component_entries == "unlimited"
            or max_component_entries > DEFAULT_MAX_COMPONENT_ENTRIES
        )
    )
    return EffectiveConfiguration(
        tuple(raw_ignore),
        tuple(components),
        tuple(records),
        hashlib.sha256(data).hexdigest(),
        max_agents_bytes,
        max_component_entries,
        init_relaxed,
    )
