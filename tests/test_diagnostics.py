"""Tests for the shared safe diagnostic contract."""

from __future__ import annotations

import pytest

from slygentify._diagnostics import DiagnosticDetail, render_diagnostic


@pytest.mark.verifies("TST046")
def test_shared_diagnostic_renders_all_actionable_fields_deterministically() -> None:
    detail = DiagnosticDetail(
        "state.invalid-json",
        ".slygentify/state.json",
        "The generated ownership record cannot be parsed.",
        "Slygentify did not change any files.",
        category="state.invalid-json",
        safety_rationale="Automatic replacement could overwrite content whose ownership is unknown.",
        recovery="Rename the record to a new backup name and rerun the dry-run.",
    )

    assert detail.message == (
        "The generated ownership record cannot be parsed. "
        "Slygentify did not change any files. Next: Rename the record to a new backup name "
        "and rerun the dry-run."
    )
    assert render_diagnostic(detail, "Error") == (
        "Error [state.invalid-json] .slygentify/state.json\n"
        "Category: state.invalid-json\n"
        "Problem: The generated ownership record cannot be parsed.\n"
        "Effect: Slygentify did not change any files.\n"
        "Why no automatic repair: Automatic replacement could overwrite content whose ownership "
        "is unknown.\n"
        "Next: Rename the record to a new backup name and rerun the dry-run."
    )


@pytest.mark.verifies("TST046")
@pytest.mark.parametrize(
    "changes",
    (
        {"code": ""},
        {"category": ""},
    ),
)
def test_shared_diagnostic_rejects_empty_required_or_optional_values(
    changes: dict[str, str],
) -> None:
    values = {
        "code": "diagnostic.code",
        "target": "AGENTS.md",
        "problem": "A safe problem.",
        "effect": "A safe effect.",
    }
    values.update(changes)

    with pytest.raises(ValueError, match="must not be empty"):
        DiagnosticDetail(**values)
