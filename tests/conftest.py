"""Pytest enforcement for test-to-specification traceability."""

from __future__ import annotations

from pathlib import Path

import pytest

from slygentify._git_tracking import _TrackedPaths
from slygentify._scan import orchestration
from tests.traceability import (
    TST_PATTERN,
    format_collection_issues,
    known_item_ids,
    validate_test_spec_markers,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
TEST_SPECIFICATION_DIRECTORY = REPOSITORY_ROOT / "requirements" / "test-specifications"


@pytest.fixture(autouse=True)
def deterministic_git_tracking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep existing scan fixtures independent of a host Git installation."""

    def tracked_paths(*args: object, **kwargs: object) -> _TrackedPaths:
        return _TrackedPaths(frozenset(), frozenset(), True)

    monkeypatch.setattr(orchestration, "_discover_tracked_paths", tracked_paths)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Reject collected tests without valid Doorstop test-specification links."""
    known_ids = known_item_ids(TEST_SPECIFICATION_DIRECTORY, TST_PATTERN)
    issues: list[str] = []

    for item in items:
        markers = [(marker.args, marker.kwargs) for marker in item.iter_markers(name="verifies")]
        item_issues = validate_test_spec_markers(markers, known_ids)
        issues.extend(format_collection_issues(item.nodeid, item_issues))

    if issues:
        formatted = "\n".join(f"- {issue}" for issue in issues)
        raise pytest.UsageError(f"test traceability validation failed:\n{formatted}")
