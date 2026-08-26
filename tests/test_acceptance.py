"""Tests for the private ADR 0002 acceptance-measurement helpers."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from slygentify.models import (
    Component,
    ComponentRelationship,
    Evidence,
    Finding,
    Repository,
    ScanResult,
)
from tools.support.acceptance import (
    AcceptanceClaim,
    AcceptanceError,
    EvidenceLocator,
    candidate_matrix,
    claims_from_scan,
    load_reviewed_claims,
    measure_claims,
)


def _scan() -> ScanResult:
    evidence = (
        Evidence(
            id="e-component",
            source_kind="toml",
            location="pyproject.toml",
            locator="project",
            observation="component",
            verification_method="static",
        ),
        Evidence(
            id="e-finding",
            source_kind="toml",
            location="pyproject.toml",
            locator="project.name",
            observation="name",
            verification_method="static",
        ),
    )
    repository = Repository(id="repository", root=".", kind="git", evidence_ids=())
    component = Component(
        id="component",
        path="app",
        ecosystem="python",
        ecosystems=("python",),
        kind="package",
        role="unknown",
        evidence_ids=("e-component",),
    )
    second_component = Component(
        id="component2",
        path="lib",
        ecosystem="python",
        ecosystems=("python",),
        kind="package",
        role="unknown",
        evidence_ids=("e-component",),
    )
    return ScanResult(
        schema_version=1,
        producer_version="0.1.0",
        completion="complete",
        repository=repository,
        components=(component, second_component),
        evidence=evidence,
        findings=(
            Finding(
                id="finding-repository",
                code="repository.verified",
                classification="verified",
                subject_id="repository",
                summary="repository fact",
                evidence_ids=("e-finding",),
            ),
            Finding(
                id="finding-unknown",
                code="test.unknown",
                classification="unknown",
                subject_id="component",
                summary="unknown",
                evidence_ids=("e-finding",),
            ),
            Finding(
                id="finding-verified",
                code="test.verified",
                classification="verified",
                subject_id="component",
                summary="declares test data",
                evidence_ids=("e-finding",),
            ),
        ),
        diagnostics=(),
        skipped_scopes=(),
        relationships=(
            ComponentRelationship(
                id="relationship-inferred",
                kind="contains",
                source_id="component",
                target_id="component2",
                classification="inferred",
                evidence_ids=("e-component",),
            ),
        ),
    )


def _claim(
    *, code: str = "test.verified", evidence: tuple[EvidenceLocator, ...] | None = None
) -> AcceptanceClaim:
    return AcceptanceClaim(
        "example",
        "finding",
        "app",
        code,
        "declares test data",
        evidence or (EvidenceLocator("pyproject.toml", "project.name"),),
    )


@pytest.mark.verifies("TST034")
def test_claims_from_scan_and_candidate_matrix_are_deterministic() -> None:
    result = _scan()
    initial_claims = claims_from_scan("example", result)
    assert [(claim.kind, claim.subject, claim.code) for claim in initial_claims] == [
        ("component", "app", "python"),
        ("component", "lib", "python"),
        ("finding", ".", "repository.verified"),
        ("finding", "app", "test.verified"),
    ]
    relationship = ComponentRelationship(
        id="relationship-verified",
        kind="workspace-member",
        source_id="component",
        target_id="component2",
        classification="verified",
        evidence_ids=("e-component",),
    )
    object.__setattr__(result, "relationships", (relationship,))

    claims = claims_from_scan("example", result)

    assert [(claim.kind, claim.subject, claim.code) for claim in claims] == [
        ("component", "app", "python"),
        ("component", "lib", "python"),
        ("finding", ".", "repository.verified"),
        ("finding", "app", "test.verified"),
        ("relationship", "app -> lib", "workspace-member"),
    ]
    first = candidate_matrix(claims)
    assert first == candidate_matrix(claims)
    document = json.loads(first)
    assert document["review_status"] == "candidate"
    assert document["claims"][0]["value"] == {
        "ecosystems": ["python"],
        "kind": "package",
        "role": "unknown",
    }


@pytest.mark.verifies("TST034")
def test_claims_from_scan_rejects_blank_repository_and_invalid_references() -> None:
    result = _scan()
    with pytest.raises(AcceptanceError, match="repository key"):
        claims_from_scan("", result)

    unknown_subject = replace(
        next(item for item in result.findings if item.code == "test.verified")
    )
    object.__setattr__(unknown_subject, "subject_id", "missing")
    object.__setattr__(result, "findings", (unknown_subject,))
    with pytest.raises(AcceptanceError, match="unknown subject"):
        claims_from_scan("example", result)

    unknown_evidence = replace(unknown_subject)
    object.__setattr__(unknown_evidence, "subject_id", "component")
    object.__setattr__(unknown_evidence, "evidence_ids", ("missing",))
    object.__setattr__(result, "findings", (unknown_evidence,))
    with pytest.raises(AcceptanceError, match="unknown evidence"):
        claims_from_scan("example", result)


@pytest.mark.verifies("TST034")
@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "matrix must be an object"),
        ({"schema_version": 2}, "schema_version"),
        ({"schema_version": 1, "review_status": "candidate"}, "review_status"),
        ({"schema_version": 1, "review_status": "reviewed", "claims": []}, "claims"),
        (
            {
                "schema_version": 1,
                "review_status": "reviewed",
                "claims": [{"repository": "example"}],
            },
            "claim.kind",
        ),
        (
            {
                "schema_version": 1,
                "review_status": "reviewed",
                "claims": [
                    {
                        "repository": "example",
                        "kind": "unsupported",
                        "subject": "app",
                        "code": "test",
                        "evidence": [{"location": "a"}],
                    }
                ],
            },
            "kind is not supported",
        ),
        (
            {
                "schema_version": 1,
                "review_status": "reviewed",
                "claims": [
                    {
                        "repository": "example",
                        "kind": "finding",
                        "subject": "app",
                        "code": "test",
                        "evidence": [],
                    }
                ],
            },
            "evidence",
        ),
    ],
)
def test_load_reviewed_claims_rejects_incomplete_or_malformed_documents(
    tmp_path: Path, document: object, message: str
) -> None:
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(AcceptanceError, match=message):
        load_reviewed_claims(path)


@pytest.mark.verifies("TST034")
def test_load_reviewed_claims_rejects_unreadable_json_invalid_locators_and_duplicates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "matrix.json"
    path.write_bytes(b"\xff")
    with pytest.raises(AcceptanceError, match="could not load"):
        load_reviewed_claims(path)

    path.write_text("{", encoding="utf-8")
    with pytest.raises(AcceptanceError, match="could not load"):
        load_reviewed_claims(path)

    document = {
        "schema_version": 1,
        "review_status": "reviewed",
        "claims": [
            {
                "repository": "example",
                "kind": "finding",
                "subject": "app",
                "code": "test",
                "evidence": [{"location": "pyproject.toml", "locator": ""}],
            }
        ],
    }
    claims = cast(list[dict[str, object]], document["claims"])
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AcceptanceError, match="locator"):
        load_reviewed_claims(path)

    claims[0]["evidence"] = [
        {"location": "pyproject.toml", "locator": "project.name"},
        {"location": "pyproject.toml", "locator": "project.name"},
    ]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AcceptanceError, match="duplicates"):
        load_reviewed_claims(path)

    claims[0]["evidence"] = [{"location": "pyproject.toml", "locator": None}]
    claims.append(claims[0].copy())
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(AcceptanceError, match="duplicate claim keys"):
        load_reviewed_claims(path)


@pytest.mark.verifies("TST034")
def test_load_reviewed_claims_and_measure_claims_distinguish_each_failure_mode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "matrix.json"
    document = {
        "schema_version": 1,
        "review_status": "reviewed",
        "claims": [
            {
                "repository": "example",
                "kind": "finding",
                "subject": "app",
                "code": "test.verified",
                "value": "declares test data",
                "evidence": [{"location": "pyproject.toml", "locator": "project.name"}],
            },
            {
                "repository": "example",
                "kind": "finding",
                "subject": "app",
                "code": "test.missing",
                "value": "missing",
                "evidence": [{"location": "pyproject.toml", "locator": "project"}],
            },
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    expected = load_reviewed_claims(path)
    actual = (
        _claim(evidence=(EvidenceLocator("pyproject.toml", "project"),)),
        _claim(code="test.unexpected"),
    )

    measurement = measure_claims(expected, actual)

    assert measurement.expected_count == 2
    assert measurement.actual_count == 2
    assert measurement.matched_count == 0
    assert len(measurement.unexpected) == 1
    assert len(measurement.missing) == 1
    assert len(measurement.invalid_evidence) == 1
    assert measurement.precision == 0.0
    assert measurement.recall == 0.0
    assert not measurement.passes


@pytest.mark.verifies("TST034")
def test_measure_claims_passes_exact_comparison_and_rejects_duplicate_keys() -> None:
    claim = _claim()
    measurement = measure_claims((claim,), (claim,))
    assert measurement.precision == 1.0
    assert measurement.recall == 1.0
    assert measurement.passes
    assert measure_claims((), ()).passes

    with pytest.raises(AcceptanceError, match="unique keys"):
        measure_claims((claim, claim), (claim,))
    with pytest.raises(AcceptanceError, match="unique keys"):
        measure_claims((claim,), (claim, claim))
    with pytest.raises(AcceptanceError, match="at least one claim"):
        candidate_matrix(())


@pytest.mark.verifies("TST034")
def test_public_corpus_manifest_preserves_the_approved_20_repository_shape() -> None:
    path = Path(__file__).parent / "acceptance" / "corpus-v1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    repositories = document["repositories"]

    assert document["schema_version"] == 1
    assert len(repositories) == 20
    assert Counter(entry["category"] for entry in repositories) == {
        "python": 5,
        "javascript-typescript": 5,
        "mixed": 5,
        "generic-unsupported": 5,
    }
    assert {entry["id"] for entry in repositories} >= {"flask", "fastapi", "express", "fastify"}
    assert all(len(entry["commit"]) == 40 for entry in repositories)
