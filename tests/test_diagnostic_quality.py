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

    assert "The affected evidence was omitted, so the scan is partial." in partial.message
    assert partial.message.endswith(
        "Next: correct the declaration at bad, or intentionally exclude it when it is outside "
        "the intended inspection scope."
    )
    assert "without treating this item as verified" in retained.message


@pytest.mark.verifies("TST046")
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("inspection.missing-workspace-member", "restore the referenced in-repository target"),
        ("inspection.unsafe-file", "safely readable in-repository path"),
        ("python.dynamic-ci-command-unknown", "supported literal declaration"),
        ("python.manager-conflict", "intentionally compatible"),
        ("configuration.relaxed-limits", "restore the default limits"),
        ("inspection.max-memory-bytes", "scan.limits.max_memory_bytes"),
    ],
)
def test_condition_families_gain_safe_specific_recovery(code: str, expected: str) -> None:
    candidate = DiagnosticCandidate(code, "target", "The condition was observed.", True)

    assert expected in (candidate.recovery or "")


@pytest.mark.verifies("TST046")
def test_candidate_contract_preserves_explicit_problem_effect_and_recovery() -> None:
    candidate = DiagnosticCandidate(
        "javascript.invalid-manifest",
        "package.json",
        partial=True,
        problem="The manifest is malformed",
        effect="Its package declarations were omitted",
        recovery="correct the JSON or intentionally exclude the file",
    )

    assert candidate.problem == "The manifest is malformed"
    assert candidate.effect == "Its package declarations were omitted"
    assert candidate.recovery == "correct the JSON or intentionally exclude the file"
    assert candidate.message == (
        "The manifest is malformed. Its package declarations were omitted. "
        "Next: correct the JSON or intentionally exclude the file."
    )
    with pytest.raises(TypeError, match="problem, effect, and recovery"):
        DiagnosticCandidate(
            "invalid",
            ".",
            "A legacy message.",
            problem="A problem",
            effect="An effect",
            recovery="recover",
        )
    with pytest.raises(TypeError, match="message or explicit structure"):
        DiagnosticCandidate("invalid", ".")
