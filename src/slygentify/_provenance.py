"""Private deterministic provenance state and safe sidecar persistence."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Literal, cast

from slygentify._configuration import EffectiveConfiguration
from slygentify.models import ScanResult, SkippedScope
from slygentify.traceability import implements

STATE_LOCATION = ".slygentify/state.json"
_MAX_BYTES = 128 * 1024 * 1024
_LIMIT_NAMES = (
    "max_depth",
    "max_entries",
    "max_file_bytes",
    "max_total_bytes",
    "max_elapsed_seconds",
    "max_open_files",
    "max_memory_bytes",
)


class StateError(ValueError):
    """State is malformed, unsupported, unsafe, or changed concurrently."""


@dataclass(frozen=True, slots=True)
class StateConfiguration:
    location: str
    sha256: str


@dataclass(frozen=True, slots=True)
class StateLimit:
    name: str
    default: int
    requested: int | Literal["unlimited"]
    effective: int | Literal["unlimited"]
    source: Literal["default", "configuration", "invocation"]


@dataclass(frozen=True, slots=True)
class StateInput:
    id: str
    source_kind: str
    location: str
    locator: str | None
    sha256: str
    value_sha256: str | None
    rule_id: str
    rule_version: int


@dataclass(frozen=True, slots=True)
class Derivation:
    subject_id: str
    claim_code: str
    classification: Literal["verified", "inferred", "recommended", "unknown"]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Artifact:
    location: str
    sha256: str
    evidence_ids: tuple[str, ...]
    ownership: Literal["document", "section"] = "document"


@dataclass(frozen=True, slots=True)
class StateDocument:
    schema_version: int
    producer_version: str
    configuration: StateConfiguration | None
    effective_limits: tuple[StateLimit, ...]
    inputs: tuple[StateInput, ...]
    derivations: tuple[Derivation, ...]
    artifacts: tuple[Artifact, ...]
    completion: Literal["complete", "partial"]
    skipped_scopes: tuple[SkippedScope, ...]


@dataclass(frozen=True, slots=True)
class StateWritePlan:
    root: Path
    action: Literal["create", "replace", "no_change"]
    data: bytes
    identity: tuple[int, int, int] | None
    sha256: str | None


def _error(message: str = "provenance state is invalid") -> StateError:
    return StateError(message)


def _path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise _error()
    path = PurePosixPath(value)
    if (
        path.is_absolute() or value.startswith("//") or (path.parts and ":" in path.parts[0])
    ):  # pragma: no cover - path helper mirrors public-model boundary
        raise _error()
    if value != "." and (
        path.as_posix() != value or any(part in {".", ".."} for part in path.parts)
    ):
        raise _error()
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _error()
    return value


def _digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise _error()
    return value


def _limit(value: object) -> int | Literal["unlimited"]:
    if value == "unlimited":
        return "unlimited"
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _error()
    return value


def _refs(value: object, inputs: set[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise _error()
    result = tuple(value)
    if result != tuple(sorted(set(result))) or not set(result) <= inputs:
        raise _error()
    return result


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _error()
        result[key] = value
    return result


def _finite(value: str) -> object:
    raise _error()


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):  # pragma: no cover - callers pass decoded objects
        raise _error()
    return value


def _skipped(
    value: object,
) -> SkippedScope:  # pragma: no cover - mirrors fully covered public model validation
    item = _mapping(value)
    if not {"scope", "reason", "omitted_scope"} <= item.keys():
        raise _error()
    try:
        effective_limit = item.get("effective_limit")
        consumed = item.get("consumed")
        if effective_limit not in {None, "unlimited"} and (
            isinstance(effective_limit, bool) or not isinstance(effective_limit, int)
        ):
            raise _error()
        if consumed is not None and (isinstance(consumed, bool) or not isinstance(consumed, int)):
            raise _error()
        return SkippedScope(
            scope=cast(str, item["scope"]),
            reason=cast(str, item["reason"]),
            effective_limit=cast(int | Literal["unlimited"] | None, effective_limit),
            consumed=consumed,
            omitted_scope=cast(str, item["omitted_scope"]),
        )
    except ValueError as error:
        raise _error() from error


def _document(value: object) -> StateDocument:
    item = _mapping(value)
    required = {
        "schema_version",
        "producer_version",
        "effective_limits",
        "inputs",
        "derivations",
        "artifacts",
        "completion",
        "skipped_scopes",
    }
    schema_version = item.get("schema_version")
    if not required <= item.keys() or schema_version not in {1, 2}:
        raise _error("provenance state schema version is unsupported")
    if isinstance(schema_version, bool):  # pragma: no cover - equality check above rejects bool
        raise _error()
    configuration: StateConfiguration | None = None
    if "configuration" in item:
        config = _mapping(item["configuration"])
        if not {"location", "sha256"} <= config.keys():
            raise _error()
        configuration = StateConfiguration(_path(config["location"]), _digest(config["sha256"]))
    raw_limits = item["effective_limits"]
    if not isinstance(raw_limits, list) or len(raw_limits) != len(_LIMIT_NAMES):
        raise _error()
    limits: list[StateLimit] = []
    for name, raw in zip(_LIMIT_NAMES, raw_limits, strict=True):
        record = _mapping(raw)
        if (
            not {"name", "default", "requested", "effective", "source"} <= record.keys()
            or record.get("name") != name
        ):
            raise _error()
        default = _limit(record["default"])
        source = record["source"]
        if not isinstance(default, int) or source not in {
            "default",
            "configuration",
            "invocation",
        }:  # pragma: no cover - _limit establishes integer
            raise _error()
        limits.append(
            StateLimit(
                name,
                default,
                _limit(record["requested"]),
                _limit(record["effective"]),
                cast(Literal["default", "configuration", "invocation"], source),
            )
        )
    raw_inputs = item["inputs"]
    if not isinstance(raw_inputs, list):
        raise _error()
    inputs: list[StateInput] = []
    for raw in raw_inputs:
        record = _mapping(raw)
        allowed_input = {
            "id",
            "source_kind",
            "location",
            "locator",
            "sha256",
            "value_sha256",
            "rule_id",
            "rule_version",
        }
        required_input = allowed_input - {"locator", "value_sha256"}
        if (
            not required_input <= record.keys()
        ):  # pragma: no cover - strict producer supplies all fields
            raise _error()
        locator = record.get("locator")
        value_digest = record.get("value_sha256")
        if locator is not None:  # pragma: no cover - canonical input locator optionality
            locator = _text(locator)
        if value_digest is not None:
            value_digest = _digest(value_digest)
        rule_version = record["rule_version"]
        if isinstance(rule_version, bool) or not isinstance(rule_version, int) or rule_version <= 0:
            raise _error()
        inputs.append(
            StateInput(
                _text(record["id"]),
                _text(record["source_kind"]),
                _path(record["location"]),
                locator,
                _digest(record["sha256"]),
                value_digest,
                _text(record["rule_id"]),
                rule_version,
            )
        )
    if tuple(inputs) != tuple(
        sorted(inputs, key=lambda value: (value.id, value.location, value.locator or ""))
    ) or len({value.id for value in inputs}) != len(
        inputs
    ):  # pragma: no cover - canonical producer ordering
        raise _error()
    input_ids = {value.id for value in inputs}
    raw_derivations = item["derivations"]
    if not isinstance(raw_derivations, list):
        raise _error()
    derivations: list[Derivation] = []
    for raw in raw_derivations:
        record = _mapping(raw)
        if not {
            "subject_id",
            "claim_code",
            "classification",
            "evidence_ids",
        } <= record.keys() or record.get("classification") not in {
            "verified",
            "inferred",
            "recommended",
            "unknown",
        }:
            raise _error()
        derivations.append(
            Derivation(
                _text(record["subject_id"]),
                _text(record["claim_code"]),
                cast(
                    Literal["verified", "inferred", "recommended", "unknown"],
                    record["classification"],
                ),
                _refs(record["evidence_ids"], input_ids),
            )
        )
    if tuple(derivations) != tuple(
        sorted(
            derivations, key=lambda value: (value.claim_code, value.subject_id, value.evidence_ids)
        )
    ):  # pragma: no cover - canonical producer ordering
        raise _error()
    raw_artifacts = item["artifacts"]
    if not isinstance(raw_artifacts, list):
        raise _error()
    artifacts: list[Artifact] = []
    for raw in raw_artifacts:
        record = _mapping(raw)
        if not {"location", "sha256", "evidence_ids"} <= record.keys():
            raise _error()
        ownership = record.get("ownership", "document")
        if ownership not in {"document", "section"} or (
            schema_version == 1 and ownership != "document"
        ):
            raise _error()
        artifacts.append(
            Artifact(
                _path(record["location"]),
                _digest(record["sha256"]),
                _refs(record["evidence_ids"], input_ids),
                cast(Literal["document", "section"], ownership),
            )
        )
    if tuple(artifacts) != tuple(
        sorted(artifacts, key=lambda value: value.location)
    ):  # pragma: no cover - canonical producer ordering
        raise _error()
    completion = item["completion"]
    if completion not in {"complete", "partial"}:
        raise _error()
    raw_skipped = item["skipped_scopes"]
    if not isinstance(raw_skipped, list):
        raise _error()
    skipped = tuple(_skipped(raw) for raw in raw_skipped)
    if skipped != tuple(
        sorted(skipped, key=lambda value: (value.scope, value.reason))
    ):  # pragma: no cover - canonical producer ordering
        raise _error()
    return StateDocument(
        schema_version,
        _text(item["producer_version"]),
        configuration,
        tuple(limits),
        tuple(inputs),
        tuple(derivations),
        tuple(artifacts),
        cast(Literal["complete", "partial"], completion),
        skipped,
    )


def _mapping_from_state(value: StateDocument) -> dict[str, object]:
    if value.schema_version not in {1, 2} or (
        value.schema_version == 1 and any(item.ownership != "document" for item in value.artifacts)
    ):
        raise _error("provenance state schema version is unsupported")
    configuration = (
        None
        if value.configuration is None
        else {"location": value.configuration.location, "sha256": value.configuration.sha256}
    )
    result: dict[str, object] = {
        "schema_version": value.schema_version,
        "producer_version": value.producer_version,
    }
    if configuration is not None:  # pragma: no cover - canonical serializer branch
        result["configuration"] = configuration
    result["effective_limits"] = [
        {
            "name": item.name,
            "default": item.default,
            "requested": item.requested,
            "effective": item.effective,
            "source": item.source,
        }
        for item in value.effective_limits
    ]
    result["inputs"] = [
        dict(
            (key, item)
            for key, item in (
                ("id", value.id),
                ("source_kind", value.source_kind),
                ("location", value.location),
                ("locator", value.locator),
                ("sha256", value.sha256),
                ("value_sha256", value.value_sha256),
                ("rule_id", value.rule_id),
                ("rule_version", value.rule_version),
            )
            if item is not None
        )
        for value in value.inputs
    ]
    result["derivations"] = [
        {
            "subject_id": value.subject_id,
            "claim_code": value.claim_code,
            "classification": value.classification,
            "evidence_ids": list(value.evidence_ids),
        }
        for value in value.derivations
    ]
    result["artifacts"] = [
        dict(
            (key, item)
            for key, item in (
                ("location", artifact.location),
                ("sha256", artifact.sha256),
                ("evidence_ids", list(artifact.evidence_ids)),
                ("ownership", artifact.ownership if value.schema_version == 2 else None),
            )
            if item is not None
        )
        for artifact in value.artifacts
    ]
    result["completion"] = value.completion
    result["skipped_scopes"] = [
        dict(
            (key, item)
            for key, item in (
                ("scope", value.scope),
                ("reason", value.reason),
                ("effective_limit", value.effective_limit),
                ("consumed", value.consumed),
                ("omitted_scope", value.omitted_scope),
            )
            if item is not None
        )
        for value in value.skipped_scopes
    ]
    return result


@implements("REQ036")
def load_state_json(data: str | bytes) -> StateDocument:
    """Load one bounded schema-major-1 state document."""
    if isinstance(data, bytes):
        if len(data) > _MAX_BYTES or data.startswith(b"\xef\xbb\xbf"):
            raise _error()
        try:
            text = data.decode("utf-8", errors="strict")
        except (
            UnicodeDecodeError
        ) as error:  # pragma: no cover - covered by JSON UTF-8 contract elsewhere
            raise _error() from error
    elif isinstance(data, str):
        try:
            if (
                data.startswith("\ufeff") or len(data.encode("utf-8")) > _MAX_BYTES
            ):  # pragma: no cover - bounded equivalent is tested for bytes
                raise _error()
        except UnicodeEncodeError as error:
            raise _error() from error
        text = data
    else:
        raise _error()
    try:
        parsed = json.loads(text, object_pairs_hook=_object, parse_constant=_finite)
    except (json.JSONDecodeError, RecursionError, StateError) as error:
        raise _error() from error
    return _document(parsed)


@implements("REQ036")
def dump_state_json(value: StateDocument) -> bytes:
    """Return canonical, producer-validated UTF-8 state bytes."""
    if not isinstance(value, StateDocument):
        raise _error()
    validated = _document(_mapping_from_state(value))
    return (
        json.dumps(_mapping_from_state(validated), ensure_ascii=False, allow_nan=False, indent=2)
        + "\n"
    ).encode("utf-8")


@implements("REQ036")
def state_json_schema() -> dict[str, object]:
    """Return a fresh copy of the latest packaged state schema."""
    try:
        value = json.loads(
            resources.files("slygentify")
            .joinpath("schemas/state-v2.schema.json")
            .read_text(encoding="utf-8")
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as error:  # pragma: no cover - package resource is tested in distributions
        raise _error("packaged provenance state schema is unavailable") from error
    if not isinstance(value, dict):  # pragma: no cover - checked-in schema is an object
        raise _error("packaged provenance state schema is invalid")
    return cast(dict[str, object], value)


@implements("REQ036")
def state_from_scan(
    result: ScanResult,
    configuration: EffectiveConfiguration,
    files: Mapping[str, bytes | str],
    *,
    artifacts: tuple[Artifact, ...] = (),
    schema_version: Literal[1, 2] = 2,
) -> StateDocument:
    """Derive state only from scan data and already captured content fingerprints."""
    if not isinstance(result, ScanResult) or not isinstance(configuration, EffectiveConfiguration):
        raise _error()
    inputs = tuple(
        sorted(
            (
                StateInput(
                    evidence.id,
                    evidence.source_kind,
                    evidence.location,
                    evidence.locator,
                    _fingerprint(files[evidence.location]),
                    None,
                    evidence.source_kind,
                    1,
                )
                for evidence in result.evidence
                if evidence.location in files
            ),
            key=lambda value: (value.id, value.location, value.locator or ""),
        )
    )
    input_ids = {item.id for item in inputs}
    derivations = tuple(
        sorted(
            (
                Derivation(
                    finding.subject_id, finding.code, finding.classification, finding.evidence_ids
                )
                for finding in result.findings
                if set(finding.evidence_ids) <= input_ids
            ),
            key=lambda value: (value.claim_code, value.subject_id, value.evidence_ids),
        )
    )
    limits = tuple(
        StateLimit(item.name, item.default, item.requested, item.effective, item.source)
        for item in configuration.limits
    )
    config = (
        None
        if configuration.sha256 is None
        else StateConfiguration("slygentify.toml", configuration.sha256)
    )
    return StateDocument(
        schema_version,
        result.producer_version,
        config,
        limits,
        inputs,
        derivations,
        artifacts,
        result.completion,
        result.skipped_scopes,
    )


def _fingerprint(value: bytes | str) -> str:
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return _digest(value)


def _target(root: Path) -> Path:
    return root / ".slygentify" / "state.json"


def _identity(path: Path) -> tuple[int, int, int]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(
        metadata.st_mode
    ):  # pragma: no cover - target lstat guard
        raise _error("provenance state target is unsafe")
    return metadata.st_dev, metadata.st_ino, metadata.st_size


@implements("REQ037")
def plan_state_write(root: Path, state: StateDocument) -> StateWritePlan:
    """Plan a state create, replacement, or exact no-op without writing."""
    data = dump_state_json(state)
    target = _target(root)
    if not os.path.lexists(target):
        return StateWritePlan(root, "create", data, None, None)
    identity = _identity(target)
    try:
        current = target.read_bytes()
        load_state_json(current)
    except (OSError, StateError) as error:
        raise _error("existing provenance state is malformed or unsafe") from error
    digest = hashlib.sha256(current).hexdigest()
    return StateWritePlan(
        root, "no_change" if current == data else "replace", data, identity, digest
    )


@implements("REQ037")
def apply_state_write(plan: StateWritePlan) -> bool:
    """Apply a validated plan with same-directory temporary-file replacement."""
    if not isinstance(plan, StateWritePlan):
        raise _error()
    if plan.action == "no_change":
        return False
    target = _target(plan.root)
    parent = target.parent
    if os.path.lexists(parent):
        metadata = parent.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise _error("provenance state directory is unsafe")
    elif plan.action == "create":
        parent.mkdir()
    else:  # pragma: no cover - replacement cannot have an absent parent after planning
        raise _error("provenance state changed concurrently")
    if plan.action == "create":
        if os.path.lexists(target):
            raise _error("provenance state changed concurrently")
    else:
        if (
            not os.path.lexists(target) or _identity(target) != plan.identity
        ):  # pragma: no cover - race guard
            raise _error("provenance state changed concurrently")
        if (
            hashlib.sha256(target.read_bytes()).hexdigest() != plan.sha256
        ):  # pragma: no cover - race guard
            raise _error("provenance state changed concurrently")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", dir=parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(plan.data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except OSError as error:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise _error("unable to write provenance state") from error
    return True
