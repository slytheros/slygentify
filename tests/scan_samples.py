"""Representative public scan values shared by interface contract tests."""

from slygentify import (
    Component,
    Diagnostic,
    Evidence,
    Finding,
    Repository,
    ScanResult,
    SkippedScope,
)


def sample_result() -> ScanResult:
    evidence_a = Evidence(
        id="evidence_a",
        source_kind="vcs-marker",
        location=".git",
        locator=None,
        observation="Git repository marker is present.",
        verification_method="non-following metadata inspection",
    )
    evidence_b = Evidence(
        id="evidence_b",
        source_kind="manifest",
        location="Cargo.toml",
        locator="package",
        observation="Cargo package boundary is declared.",
        verification_method=None,
    )
    repository = Repository(
        id="repository_a",
        root=".",
        kind="git",
        evidence_ids=(evidence_a.id,),
    )
    component = Component(
        id="component_a",
        path=".",
        ecosystem="generic",
        kind="package",
        evidence_ids=(evidence_b.id,),
        ecosystems=("generic",),
    )
    findings = (
        Finding(
            id="finding_a",
            code="example.unknown",
            classification="unknown",
            subject_id=repository.id,
            summary="A boundary remains unknown.",
            evidence_ids=(),
        ),
        Finding(
            id="finding_b",
            code="example.verified",
            classification="verified",
            subject_id=component.id,
            summary="A package boundary is verified.",
            evidence_ids=(evidence_b.id,),
        ),
    )
    diagnostic = Diagnostic(
        id="diagnostic_a",
        code="example.diagnostic",
        subject_id=component.id,
        location=None,
        message="Review the example diagnostic.",
        evidence_ids=(evidence_b.id,),
    )
    skipped = SkippedScope(
        scope="vendor",
        reason="cache-or-dependency",
        effective_limit=None,
        consumed=None,
        omitted_scope="vendor/**",
    )
    return ScanResult(
        schema_version=1,
        producer_version="0.1.0",
        completion="partial",
        repository=repository,
        components=(component,),
        evidence=(evidence_a, evidence_b),
        findings=findings,
        diagnostics=(diagnostic,),
        skipped_scopes=(skipped,),
        relationships=(),
    )


def sample_mapping() -> dict[str, object]:
    return {
        "schema_version": 1,
        "producer_version": "0.1.0",
        "completion": "partial",
        "repository": {
            "id": "repository_a",
            "root": ".",
            "kind": "git",
            "evidence_ids": ["evidence_a"],
        },
        "components": [
            {
                "id": "component_a",
                "path": ".",
                "ecosystem": "generic",
                "kind": "package",
                "evidence_ids": ["evidence_b"],
                "ecosystems": ["generic"],
            }
        ],
        "relationships": [],
        "evidence": [
            {
                "id": "evidence_a",
                "source_kind": "vcs-marker",
                "location": ".git",
                "observation": "Git repository marker is present.",
                "verification_method": "non-following metadata inspection",
            },
            {
                "id": "evidence_b",
                "source_kind": "manifest",
                "location": "Cargo.toml",
                "locator": "package",
                "observation": "Cargo package boundary is declared.",
            },
        ],
        "findings": [
            {
                "id": "finding_a",
                "code": "example.unknown",
                "classification": "unknown",
                "subject_id": "repository_a",
                "summary": "A boundary remains unknown.",
                "evidence_ids": [],
            },
            {
                "id": "finding_b",
                "code": "example.verified",
                "classification": "verified",
                "subject_id": "component_a",
                "summary": "A package boundary is verified.",
                "evidence_ids": ["evidence_b"],
            },
        ],
        "diagnostics": [
            {
                "id": "diagnostic_a",
                "code": "example.diagnostic",
                "subject_id": "component_a",
                "message": "Review the example diagnostic.",
                "evidence_ids": ["evidence_b"],
            }
        ],
        "skipped_scopes": [
            {
                "scope": "vendor",
                "reason": "cache-or-dependency",
                "omitted_scope": "vendor/**",
            }
        ],
    }
