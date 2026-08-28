"""Cross-cutting diagnostic wording and composition tests."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from slygentify._scan.contracts import DiagnosticCandidate, compose_diagnostic_message
from slygentify.initialization import InitializationDiagnostic
from slygentify.models import Diagnostic, DoctorDiagnostic


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
    candidate = DiagnosticCandidate(code, ".", message, partial, disposition="problem")

    assert candidate.problem
    assert effect in candidate.effect
    assert candidate.message.count("Next:") <= 1
    assert candidate.partial is partial


@pytest.mark.verifies("TST046")
def test_one_sentence_candidates_gain_an_explicit_observable_effect() -> None:
    partial = DiagnosticCandidate(
        "inspection.invalid-file", "bad", "A file is invalid.", True, disposition="problem"
    )
    retained = DiagnosticCandidate(
        "generic.unsupported", "tool", "Tooling is unsupported.", False, disposition="limitation"
    )

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
    candidate = DiagnosticCandidate(
        code, "target", "The condition was observed.", True, disposition="problem"
    )

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
        disposition="problem",
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
            disposition="problem",
        )
    with pytest.raises(TypeError, match="message or explicit structure"):
        DiagnosticCandidate("invalid", ".", disposition="problem")


@pytest.mark.verifies("TST046")
def test_public_diagnostic_dispositions_default_validate_and_remain_immutable() -> None:
    diagnostic = Diagnostic(
        id="diagnostic",
        code="test.diagnostic",
        subject_id="repository",
        location=None,
        message="A diagnostic.",
        evidence_ids=(),
    )
    doctor = DoctorDiagnostic(
        id="doctor",
        code="doctor.test",
        severity="info",
        classification="unknown",
        subject_id="repository",
        location=None,
        problem="A condition was observed.",
        effect="No action was taken.",
        remediation=None,
        evidence_ids=(),
    )
    initialization = InitializationDiagnostic("initialization.test", "A notice.", "Review it.")

    assert diagnostic.disposition == doctor.disposition == initialization.disposition == "problem"
    for disposition in ("problem", "limitation", "notice"):
        assert (
            Diagnostic(
                id=f"diagnostic-{disposition}",
                code="test.diagnostic",
                subject_id="repository",
                location=None,
                message="A diagnostic.",
                evidence_ids=(),
                disposition=disposition,
            ).disposition
            == disposition
        )
    with pytest.raises(FrozenInstanceError):
        diagnostic.disposition = "notice"  # type: ignore[misc]
    with pytest.raises(ValueError, match="disposition"):
        Diagnostic(
            id="invalid",
            code="test.diagnostic",
            subject_id="repository",
            location=None,
            message="A diagnostic.",
            evidence_ids=(),
            disposition="other",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="disposition"):
        DoctorDiagnostic(
            id="invalid",
            code="doctor.test",
            severity="info",
            classification="unknown",
            subject_id="repository",
            location=None,
            problem="A condition was observed.",
            effect="No action was taken.",
            remediation=None,
            evidence_ids=(),
            disposition="other",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="disposition"):
        InitializationDiagnostic(
            "initialization.test",
            "A notice.",
            "Review it.",
            disposition="other",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="disposition"):
        DiagnosticCandidate("test", ".", "A diagnostic.", disposition="other")  # type: ignore[arg-type]


@pytest.mark.verifies("TST046")
def test_every_scan_diagnostic_producer_has_an_explicit_reviewed_disposition() -> None:
    expected = {
        "composition.ambiguous-boundary": "limitation",
        "composition.overlapping-workspace-membership": "problem",
        "composition.unresolved-relationship": "problem",
        "configuration.component-conflict": "problem",
        "configuration.relaxed-limits": "notice",
        "core.component-boundary-unknown": "limitation",
        "inspection.git-tracked-paths-unavailable": "limitation",
        "inspection.invalid-gitignore": "problem",
        "inspection.invalid-manifest": "problem",
        "inspection.invalid-workspace": "problem",
        "inspection.invalid-workspace-member": "problem",
        "inspection.max-memory-bytes": "limitation",
        "inspection.missing-evidence": "limitation",
        "inspection.missing-workspace-member": "problem",
        "inspection.unreadable-evidence": "limitation",
        "inspection.unsafe-directory": "problem",
        "inspection.unsafe-file": "problem",
        "inspection.unsafe-xml": "problem",
        "javascript.ci-include-cycle": "problem",
        "javascript.ci-include-depth": "limitation",
        "javascript.dynamic-ci-command-unknown": "limitation",
        "javascript.dynamic-ci-runtime-unknown": "limitation",
        "javascript.external-ci-include": "limitation",
        "javascript.invalid-bin": "problem",
        "javascript.invalid-ci-include": "problem",
        "javascript.invalid-ci-workflow": "problem",
        "javascript.invalid-dependencies": "problem",
        "javascript.invalid-dependency": "problem",
        "javascript.invalid-manager-configuration": "problem",
        "javascript.invalid-manager-selection": "problem",
        "javascript.invalid-manifest": "problem",
        "javascript.invalid-metadata": "problem",
        "javascript.invalid-runtime": "problem",
        "javascript.invalid-script": "problem",
        "javascript.invalid-scripts": "problem",
        "javascript.invalid-workspace": "problem",
        "javascript.invalid-workspace-pattern": "problem",
        "javascript.manager-conflict": "problem",
        "javascript.missing-workspace-member": "problem",
        "javascript.npm-lock-coexistence": "notice",
        "javascript.overlapping-workspace-membership": "problem",
        "javascript.runtime-compatibility-unknown": "limitation",
        "javascript.runtime-conflict": "problem",
        "javascript.sensitive-command-redacted": "notice",
        "javascript.tool-configuration-conflict": "problem",
        "javascript.typescript-content-unknown": "limitation",
        "javascript.unresolved-typescript-reference": "problem",
        "javascript.unsafe-bin-target": "problem",
        "javascript.unsupported-tooling": "limitation",
        "python.ci-include-cycle": "problem",
        "python.ci-include-depth": "limitation",
        "python.dynamic-ci-command-unknown": "limitation",
        "python.dynamic-ci-runtime-unknown": "limitation",
        "python.dynamic-manifest-unknown": "limitation",
        "python.dynamic-metadata-unknown": "limitation",
        "python.external-ci-include": "limitation",
        "python.invalid-ci-include": "problem",
        "python.invalid-ci-workflow": "problem",
        "python.invalid-configuration": "problem",
        "python.invalid-manifest": "problem",
        "python.invalid-requirement": "problem",
        "python.invalid-requirements": "problem",
        "python.invalid-runtime-file": "problem",
        "python.invalid-workspace": "problem",
        "python.invalid-workspace-member": "problem",
        "python.manager-conflict": "problem",
        "python.missing-workspace-member": "problem",
        "python.runtime-conflict": "problem",
        "python.sensitive-command-redacted": "notice",
        "python.template-manifest-unknown": "limitation",
        "python.tool-configuration-conflict": "problem",
        "python.unbound-evidence": "limitation",
    }
    observed: dict[str, set[str]] = {}
    producer_calls = 0
    for path in Path("src/slygentify/_scan").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in {
                "DiagnosticCandidate",
                "_DiagnosticCandidate",
                "add_diagnostic",
            }:
                continue
            producer_calls += 1
            disposition = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "disposition"), None
            )
            assert disposition is not None, f"{path}:{node.lineno} omits disposition"
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and isinstance(disposition, ast.Constant)
                and isinstance(disposition.value, str)
            ):
                observed.setdefault(node.args[0].value, set()).add(disposition.value)

    assert producer_calls == 99
    assert observed == {code: {disposition} for code, disposition in expected.items()}
    python_source = Path("src/slygentify/_scan/detectors/python.py").read_text(encoding="utf-8")
    assert '"python.unsupported-configuration"' in python_source
    assert 'disposition="limitation" if unsupported else "problem"' in python_source
