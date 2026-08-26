"""Deterministic repository traceability checks used by pytest."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

REQ_PATTERN = re.compile(r"REQ\d{3}")
TST_PATTERN = re.compile(r"TST\d{3}")


def known_item_ids(directory: Path, pattern: re.Pattern[str]) -> frozenset[str]:
    """Return valid Doorstop item IDs represented by YAML filenames."""
    return frozenset(path.stem for path in directory.glob("*.yml") if pattern.fullmatch(path.stem))


def validate_test_spec_markers(
    markers: Sequence[tuple[tuple[object, ...], Mapping[str, object]]],
    known_ids: frozenset[str],
) -> list[str]:
    """Validate the arguments from all ``verifies`` markers on one test."""
    if not markers:
        return ["missing @pytest.mark.verifies marker"]

    issues: list[str] = []
    for arguments, keyword_arguments in markers:
        if keyword_arguments:
            issues.append("verifies marker does not accept keyword arguments")
        if not arguments:
            issues.append("verifies marker requires at least one TST ID")
        for value in arguments:
            if not isinstance(value, str) or not TST_PATTERN.fullmatch(value):
                issues.append(f"invalid test specification ID: {value!r}")
            elif value not in known_ids:
                issues.append(f"unknown test specification ID: {value}")
    return issues


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        if parent is not None:
            return f"{parent}.{node.attr}"
    return None


def implementation_trace_issues(source_root: Path, known_ids: frozenset[str]) -> list[str]:
    """Statically validate requirement IDs used by imported ``implements`` decorators."""
    issues: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        decorator_names: set[str] = set()

        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "slygentify.traceability":
                decorator_names.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "implements"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "slygentify.traceability":
                        base = alias.asname or alias.name
                        decorator_names.add(f"{base}.implements")

        for descendant in ast.walk(tree):
            decorator_list = getattr(descendant, "decorator_list", ())
            for decorator in decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if _dotted_name(decorator.func) not in decorator_names:
                    continue

                location = f"{path}:{decorator.lineno}"
                if decorator.keywords:
                    issues.append(f"{location}: implements does not accept keyword arguments")
                if not decorator.args:
                    issues.append(f"{location}: implements requires at least one REQ ID")
                for argument in decorator.args:
                    if not isinstance(argument, ast.Constant) or not isinstance(
                        argument.value, str
                    ):
                        issues.append(f"{location}: requirement IDs must be string literals")
                    elif not REQ_PATTERN.fullmatch(argument.value):
                        issues.append(f"{location}: invalid requirement ID: {argument.value}")
                    elif argument.value not in known_ids:
                        issues.append(f"{location}: unknown requirement ID: {argument.value}")
    return issues


def format_collection_issues(item_name: str, issues: Iterable[str]) -> list[str]:
    """Add a pytest item identifier to traceability errors."""
    return [f"{item_name}: {issue}" for issue in issues]
