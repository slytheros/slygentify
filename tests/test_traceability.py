"""Tests for implementation and repository traceability."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import pytest

from slygentify.traceability import IMPLEMENTATION_REQUIREMENTS_ATTRIBUTE, implements
from tests.traceability import (
    REQ_PATTERN,
    format_collection_issues,
    implementation_trace_issues,
    known_item_ids,
    validate_test_spec_markers,
)


@pytest.mark.verifies("TST005")
def test_implements_preserves_function_and_ordered_metadata() -> None:
    def function() -> str:
        return "result"

    decorated = implements("REQ001", "REQ002")(function)

    assert decorated is function
    assert decorated() == "result"
    assert getattr(decorated, IMPLEMENTATION_REQUIREMENTS_ATTRIBUTE) == ("REQ001", "REQ002")


@pytest.mark.verifies("TST005")
def test_implements_supports_classes_and_stacking() -> None:
    @implements("REQ002")
    @implements("REQ001")
    class Example:
        pass

    assert getattr(Example, IMPLEMENTATION_REQUIREMENTS_ATTRIBUTE) == ("REQ001", "REQ002")


@pytest.mark.verifies("TST005")
@pytest.mark.parametrize("requirement_ids", [(), ("",), ("  ",), (cast(str, 123),)])
def test_implements_rejects_missing_or_invalid_ids(requirement_ids: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        implements(*requirement_ids)


@pytest.mark.verifies("TST005")
def test_implements_rejects_duplicates_in_one_decorator() -> None:
    with pytest.raises(ValueError, match="duplicate requirement IDs"):
        implements("REQ001", "REQ001")


@pytest.mark.verifies("TST005")
def test_implements_rejects_duplicates_when_stacked() -> None:
    @implements("REQ001")
    def function() -> None:
        pass

    with pytest.raises(ValueError, match="duplicate requirement ID: REQ001"):
        implements("REQ001")(function)


@pytest.mark.verifies("TST006")
def test_marker_validation_requires_a_marker() -> None:
    assert validate_test_spec_markers([], frozenset()) == ["missing @pytest.mark.verifies marker"]


@pytest.mark.verifies("TST006")
def test_marker_validation_accepts_multiple_known_ids() -> None:
    markers: list[tuple[tuple[object, ...], dict[str, object]]] = [(("TST001", "TST002"), {})]

    assert validate_test_spec_markers(markers, frozenset({"TST001", "TST002"})) == []


@pytest.mark.verifies("TST006")
def test_marker_validation_reports_invalid_marker_shapes() -> None:
    markers = [
        ((), {"id": "TST001"}),
        ((123, "bad", "TST999"), {}),
    ]

    assert validate_test_spec_markers(markers, frozenset({"TST001"})) == [
        "verifies marker does not accept keyword arguments",
        "verifies marker requires at least one TST ID",
        "invalid test specification ID: 123",
        "invalid test specification ID: 'bad'",
        "unknown test specification ID: TST999",
    ]


@pytest.mark.verifies("TST006")
def test_known_item_ids_ignores_nonconforming_files(tmp_path: Path) -> None:
    (tmp_path / "REQ001.yml").touch()
    (tmp_path / "REQ02.yml").touch()
    (tmp_path / "README.md").touch()

    assert known_item_ids(tmp_path, REQ_PATTERN) == frozenset({"REQ001"})


@pytest.mark.verifies("TST006")
def test_static_trace_validation_accepts_direct_and_aliased_imports(tmp_path: Path) -> None:
    (tmp_path / "valid.py").write_text(
        "from slygentify.traceability import implements as maps_to\n"
        "@maps_to('REQ001')\n"
        "def direct(): pass\n"
        "import slygentify.traceability as trace\n"
        "@trace.implements('REQ002')\n"
        "class Aliased: pass\n",
        encoding="utf-8",
    )

    assert implementation_trace_issues(tmp_path, frozenset({"REQ001", "REQ002"})) == []


@pytest.mark.verifies("TST006")
def test_static_trace_validation_reports_invalid_decorators(tmp_path: Path) -> None:
    (tmp_path / "invalid.py").write_text(
        "from slygentify.traceability import implements\n"
        "value = 'REQ001'\n"
        "@implements\n"
        "def ignored(): pass\n"
        "@implements(id='REQ001')\n"
        "def keywords(): pass\n"
        "@implements()\n"
        "def empty(): pass\n"
        "@implements(value)\n"
        "def dynamic(): pass\n"
        "@implements('REQ01')\n"
        "def malformed(): pass\n"
        "@implements('REQ999')\n"
        "def unknown(): pass\n",
        encoding="utf-8",
    )

    issues = implementation_trace_issues(tmp_path, frozenset({"REQ001"}))

    assert len(issues) == 6
    assert any("does not accept keyword arguments" in issue for issue in issues)
    assert any("requires at least one REQ ID" in issue for issue in issues)
    assert any("must be string literals" in issue for issue in issues)
    assert any("invalid requirement ID: REQ01" in issue for issue in issues)
    assert any("unknown requirement ID: REQ999" in issue for issue in issues)


@pytest.mark.verifies("TST006")
def test_repository_implementation_traces_are_valid() -> None:
    repository_root = Path(__file__).parents[1]
    known_ids = known_item_ids(repository_root / "requirements" / "requirements", REQ_PATTERN)

    assert implementation_trace_issues(repository_root / "src", known_ids) == []


@pytest.mark.verifies("TST006")
def test_internal_modules_do_not_import_through_the_package_facade() -> None:
    source_root = Path(__file__).parents[1] / "src" / "slygentify"
    violations: list[str] = []
    for path in source_root.rglob("*.py"):
        if path.name == "cli.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.ImportFrom) and node.module == "slygentify"
            for node in ast.walk(tree)
        ):
            violations.append(str(path.relative_to(source_root.parent)))

    assert violations == []


@pytest.mark.verifies("TST006")
def test_collection_issue_formatting_adds_item_name() -> None:
    assert format_collection_issues("test_file.py::test_name", ["problem"]) == [
        "test_file.py::test_name: problem"
    ]
