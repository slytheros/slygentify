"""Shared, bounded detector helpers."""

from __future__ import annotations

import json
from typing import cast

import yaml  # type: ignore[import-untyped]
from yaml.nodes import (  # type: ignore[import-untyped]
    MappingNode,
    Node,
    ScalarNode,
    SequenceNode,
)

from slygentify._scan.contracts import EvidenceCandidate, EvidenceKey

_YAML_TAGS = frozenset({"tag:yaml.org,2002:str", "tag:yaml.org,2002:seq", "tag:yaml.org,2002:map"})


class StaticStructureError(ValueError):
    """A supported static document has an unsafe or unsupported structure."""


def decode(data: bytes) -> str:
    return data.decode("utf-8", errors="strict")


def strict_yaml_document(data: bytes) -> object:
    """Parse bounded scalar/list/mapping YAML without aliases or custom tags."""

    node = yaml.compose(decode(data), Loader=yaml.BaseLoader)
    if node is None:
        return {}
    seen: set[int] = set()
    nodes = 0

    def convert(current: Node, depth: int) -> object:
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > 100_000 or current.tag not in _YAML_TAGS:
            raise StaticStructureError("unsupported YAML structure")
        identity = id(current)
        if identity in seen:
            raise StaticStructureError("YAML aliases are not supported")
        seen.add(identity)
        if isinstance(current, ScalarNode):
            return current.value
        if isinstance(current, SequenceNode):
            return [convert(item, depth + 1) for item in current.value]
        mapping = cast(MappingNode, current)
        result: dict[str, object] = {}
        for key_node, value_node in mapping.value:
            key = convert(key_node, depth + 1)
            if not isinstance(key, str) or key in result:
                raise StaticStructureError("YAML mapping keys must be unique strings")
            result[key] = convert(value_node, depth + 1)
        return result

    return convert(node, 0)


def pointer(*parts: object) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def quoted(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def evidence_key(candidate: EvidenceCandidate) -> EvidenceKey:
    return candidate.source_kind, candidate.location, candidate.locator, candidate.semantic_key
