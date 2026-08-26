"""Cross-cutting diagnostic wording and composition tests."""

from __future__ import annotations

import pytest

from slygentify._scan.contracts import DiagnosticCandidate, compose_diagnostic_message


@pytest.mark.verifies("TST046")
def test_diagnostic_composer_is_deterministic_with_optional_recovery() -> None:
    assert compose_diagnostic_message("  A problem happened. ", " Evidence was withheld. ") == (
        "A problem happened. Evidence was withheld."
    )
    assert (
        compose_diagnostic_message(
            "A problem happened", "Evidence was skipped", "review the declaration"
        )
        == "A problem happened. Evidence was skipped. Next: review the declaration."
    )
    with pytest.raises(ValueError, match="must not be empty"):
        compose_diagnostic_message("", "Evidence was skipped")
    with pytest.raises(ValueError, match="must not be empty"):
        compose_diagnostic_message("A problem happened", "")
    with pytest.raises(ValueError, match="must not be empty"):
        compose_diagnostic_message("A problem happened", "Evidence was skipped", " ")


@pytest.mark.verifies("TST046")
@pytest.mark.parametrize(
    ("code", "message", "partial", "effect"),
    [
        (
            "inspection.outside-root",
            "A tracked path resolves outside the repository. The path was skipped, so the scan is partial.",
            True,
            "The path was skipped",
        ),
        (
            "inspection.git-failed",
            "Git tracked-path discovery failed. No untracked fallback was used.",
            True,
            "No untracked fallback was used",
        ),
        (
            "configuration.component-conflict",
            "Configured and detected ecosystems conflict. Both values were retained.",
            False,
            "Both values were retained",
        ),
        (
            "inspection.max-memory-bytes",
            "Normalized evidence exceeded the memory budget. Later evidence was omitted.",
            True,
            "Later evidence was omitted",
        ),
        (
            "python.invalid-manifest",
            "A Python manifest is invalid. Its package evidence was skipped. Next: repair the TOML.",
            True,
            "Its package evidence was skipped",
        ),
        (
            "javascript.sensitive-command-redacted",
            "A command contains credential-shaped text. The command text was withheld.",
            False,
            "The command text was withheld",
        ),
        (
            "composition.ambiguous-boundary",
            "A generic boundary is ambiguous. Evidence was retained without assigning a component.",
            False,
            "Evidence was retained",
        ),
        (
            "composition.unresolved-relationship",
            "Workspace evidence is inconsistent. The relationship was omitted, so the scan is partial.",
            True,
            "The relationship was omitted",
        ),
    ],
)
def test_candidate_contract_covers_each_diagnostic_subsystem(
    code: str, message: str, partial: bool, effect: str
) -> None:
    candidate = DiagnosticCandidate(code, ".", message, partial)

    assert candidate.problem
    assert effect in candidate.effect
    assert candidate.message.count("Next:") <= 1
    assert candidate.partial is partial


@pytest.mark.verifies("TST046")
def test_one_sentence_candidates_gain_an_explicit_observable_effect() -> None:
    partial = DiagnosticCandidate("inspection.invalid-file", "bad", "A file is invalid.", True)
    retained = DiagnosticCandidate("generic.unsupported", "tool", "Tooling is unsupported.", False)

    assert partial.message.endswith("The affected evidence was omitted, so the scan is partial.")
    assert "without treating this item as verified" in retained.message
