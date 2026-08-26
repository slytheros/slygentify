"""Runtime-neutral metadata for tracing implementation to requirements."""

from collections.abc import Callable
from typing import Any, TypeVar, cast

IMPLEMENTATION_REQUIREMENTS_ATTRIBUTE = "__slygentify_requirements__"

Implementation = TypeVar("Implementation")


def implements(*requirement_ids: str) -> Callable[[Implementation], Implementation]:
    """Attach ordered requirement identifiers to a function or class without wrapping it."""
    if not requirement_ids:
        raise ValueError("at least one requirement ID is required")

    for requirement_id in requirement_ids:
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            raise ValueError("requirement IDs must be nonblank strings")

    if len(set(requirement_ids)) != len(requirement_ids):
        raise ValueError("duplicate requirement IDs are not allowed")

    def decorator(implementation: Implementation) -> Implementation:
        existing = cast(
            tuple[str, ...],
            getattr(implementation, IMPLEMENTATION_REQUIREMENTS_ATTRIBUTE, ()),
        )
        duplicates = set(existing).intersection(requirement_ids)
        if duplicates:
            duplicate = min(duplicates)
            raise ValueError(f"duplicate requirement ID: {duplicate}")

        metadata = (*existing, *requirement_ids)
        setattr(cast(Any, implementation), IMPLEMENTATION_REQUIREMENTS_ATTRIBUTE, metadata)
        return implementation

    return decorator
