"""Public normalized scan model tests."""

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from slygentify import (
    Component,
    Diagnostic,
    Evidence,
    Finding,
    Repository,
    ScanResult,
    SkippedScope,
)


def _values() -> tuple[Repository, Component, Evidence, Finding, Diagnostic, SkippedScope]:
    evidence = Evidence(
        id="evidence_1",
        source_kind="manifest",
        location="Cargo.toml",
        locator="package",
        observation="Package boundary.",
        verification_method="strict parse",
    )
    repository = Repository(id="repository_1", root=".", kind="git", evidence_ids=(evidence.id,))
    component = Component(
        id="component_1",
        path=".",
        ecosystem="generic",
        kind="package",
        evidence_ids=(evidence.id,),
    )
    finding = Finding(
        id="finding_1",
        code="test.finding",
        classification="verified",
        subject_id=component.id,
        summary="A finding.",
        evidence_ids=(evidence.id,),
    )
    diagnostic = Diagnostic(
        id="diagnostic_1",
        code="test.diagnostic",
        subject_id=component.id,
        location=None,
        message="A diagnostic.",
        evidence_ids=(evidence.id,),
    )
    skipped = SkippedScope(
        scope="ignored",
        reason="gitignore",
        effective_limit=None,
        consumed=None,
        omitted_scope="ignored",
    )
    return repository, component, evidence, finding, diagnostic, skipped


@pytest.mark.verifies("TST010", "TST030")
def test_public_models_are_frozen_keyword_only_values() -> None:
    repository, component, evidence, finding, diagnostic, skipped = _values()
    result = ScanResult(
        schema_version=1,
        producer_version="0.1.0",
        completion="complete",
        repository=repository,
        components=(component,),
        evidence=(evidence,),
        findings=(finding,),
        diagnostics=(diagnostic,),
        skipped_scopes=(skipped,),
    )

    with pytest.raises(FrozenInstanceError):
        result.completion = "partial"  # type: ignore[misc]
    with pytest.raises(TypeError):
        Repository("repository", ".", "git", ())  # type: ignore[misc]
    assert component.role == "unknown"


@pytest.mark.verifies("TST010")
@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: Repository(id="", root=".", kind="git", evidence_ids=()), "non-empty"),
        (lambda: Repository(id="id", root="../out", kind="git", evidence_ids=()), "POSIX"),
        (lambda: Repository(id="id", root="a//b", kind="git", evidence_ids=()), "POSIX"),
        (lambda: Repository(id="id", root="a/./b", kind="git", evidence_ids=()), "POSIX"),
        (lambda: Repository(id="id", root="C:/repo", kind="git", evidence_ids=()), "POSIX"),
        (lambda: Repository(id="id", root="//server/share", kind="git", evidence_ids=()), "POSIX"),
        (lambda: Repository(id="id", root="C:\\repo", kind="git", evidence_ids=()), "POSIX"),
        (lambda: Repository(id="id", root=".", kind="", evidence_ids=()), "non-empty"),
        (lambda: Repository(id="id", root=".", kind="git", evidence_ids=[]), "tuple"),  # type: ignore[arg-type]
        (lambda: Repository(id="id", root=".", kind="git", evidence_ids=("b", "a")), "sorted"),
        (
            lambda: Component(id="id", path=".", ecosystem="", kind="package", evidence_ids=()),
            "ecosystem",
        ),
        (
            lambda: Component(id="id", path=".", ecosystem="generic", kind="", evidence_ids=()),
            "kind",
        ),
        (
            lambda: Component(
                id="id",
                path=".",
                ecosystem="generic",
                kind="package",
                evidence_ids=(),
                role="primary",  # type: ignore[arg-type]
            ),
            "role",
        ),
        (
            lambda: Component(
                id="id",
                path=".",
                ecosystem="generic",
                kind="package",
                evidence_ids=(),
                role=1,  # type: ignore[arg-type]
            ),
            "role",
        ),
        (
            lambda: Evidence(
                id="id",
                source_kind="",
                location="file",
                locator=None,
                observation="value",
                verification_method=None,
            ),
            "source_kind",
        ),
        (
            lambda: Evidence(
                id="id",
                source_kind="manifest",
                location="file",
                locator="",
                observation="value",
                verification_method=None,
            ),
            "locator",
        ),
        (
            lambda: Evidence(
                id="id",
                source_kind="manifest",
                location="file",
                locator=None,
                observation="",
                verification_method=None,
            ),
            "observation",
        ),
        (
            lambda: Evidence(
                id="id",
                source_kind="manifest",
                location="file",
                locator=None,
                observation="value",
                verification_method="",
            ),
            "verification_method",
        ),
        (
            lambda: Finding(
                id="id",
                code="code",
                classification="certain",  # type: ignore[arg-type]
                subject_id="subject",
                summary="summary",
                evidence_ids=(),
            ),
            "classification",
        ),
        (
            lambda: Diagnostic(
                id="id",
                code="code",
                subject_id=None,
                location=None,
                message="message",
                evidence_ids=(),
            ),
            "requires",
        ),
        (
            lambda: SkippedScope(
                scope=".", reason="limit", effective_limit=0, consumed=None, omitted_scope="."
            ),
            "effective_limit",
        ),
        (
            lambda: SkippedScope(
                scope=".", reason="limit", effective_limit=True, consumed=None, omitted_scope="."
            ),
            "effective_limit",
        ),
        (
            lambda: SkippedScope(
                scope=".",
                reason="limit",
                effective_limit="unlimited",
                consumed=-1,
                omitted_scope=".",
            ),
            "consumed",
        ),
    ],
)
def test_public_models_reject_invalid_local_values(
    factory: Callable[[], Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


@pytest.mark.verifies("TST046")
def test_diagnostic_requires_problem_and_effect_together() -> None:
    _, component, evidence, _, _, _ = _values()

    with pytest.raises(ValueError, match="problem and effect"):
        Diagnostic(
            id="diagnostic_1",
            code="test.diagnostic",
            subject_id=component.id,
            location=None,
            message="A diagnostic.",
            problem="A structured problem.",
            evidence_ids=(evidence.id,),
        )


def _result(**changes: Any) -> ScanResult:
    repository, component, evidence, finding, diagnostic, skipped = _values()
    values: dict[str, Any] = {
        "schema_version": 1,
        "producer_version": "0.1.0",
        "completion": "complete",
        "repository": repository,
        "components": (component,),
        "evidence": (evidence,),
        "findings": (finding,),
        "diagnostics": (diagnostic,),
        "skipped_scopes": (skipped,),
    }
    values.update(changes)
    return ScanResult(**values)


@pytest.mark.verifies("TST010")
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": 2}, "schema_version"),
        ({"producer_version": ""}, "producer_version"),
        ({"completion": "unfinished"}, "completion"),
        ({"components": []}, "tuples"),
        (
            {
                "components": (
                    _values()[1],
                    Component(
                        id="a", path="a", ecosystem="generic", kind="package", evidence_ids=()
                    ),
                )
            },
            "canonical",
        ),
        ({"evidence": (_values()[2], _values()[2])}, "identifiers"),
        (
            {
                "components": (
                    _values()[1],
                    Component(
                        id="other",
                        path=".",
                        ecosystem="generic",
                        kind="package",
                        evidence_ids=(_values()[2].id,),
                    ),
                )
            },
            "paths",
        ),
        (
            {
                "repository": Repository(
                    id="repository_1", root=".", kind="git", evidence_ids=("missing",)
                )
            },
            "evidence",
        ),
        (
            {
                "findings": (
                    Finding(
                        id="finding_1",
                        code="test.finding",
                        classification="verified",
                        subject_id="missing",
                        summary="summary",
                        evidence_ids=(_values()[2].id,),
                    ),
                )
            },
            "finding subject",
        ),
        (
            {
                "diagnostics": (
                    Diagnostic(
                        id="diagnostic_1",
                        code="test.diagnostic",
                        subject_id="missing",
                        location=None,
                        message="message",
                        evidence_ids=(_values()[2].id,),
                    ),
                )
            },
            "diagnostic subject",
        ),
        ({"completion": "partial", "diagnostics": (), "skipped_scopes": ()}, "explain"),
    ],
)
def test_scan_result_rejects_invalid_graphs(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _result(**changes)
