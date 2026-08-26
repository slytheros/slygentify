"""Development-only, deterministic helpers for ADR 0002 acceptance measurements."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from slygentify.models import ScanResult
from slygentify.traceability import implements

ClaimKind = Literal["component", "finding", "relationship"]


class AcceptanceError(ValueError):
    """Raised when acceptance evidence is malformed or incomplete."""


@dataclass(frozen=True, slots=True)
class EvidenceLocator:
    """A source location and optional semantic locator reviewed for one claim."""

    location: str
    locator: str | None


@dataclass(frozen=True, slots=True)
class AcceptanceClaim:
    """A reviewed or candidate factual claim for one acceptance repository."""

    repository: str
    kind: ClaimKind
    subject: str
    code: str
    value: object | None
    evidence: tuple[EvidenceLocator, ...]

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        """Return a stable key independent of opaque scan identifiers."""

        return (
            self.repository,
            self.kind,
            self.subject,
            self.code,
            json.dumps(self.value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )


@dataclass(frozen=True, slots=True)
class Measurement:
    """The formal comparison of reviewed facts with one scan result."""

    expected_count: int
    actual_count: int
    matched_count: int
    unexpected: tuple[AcceptanceClaim, ...]
    missing: tuple[AcceptanceClaim, ...]
    invalid_evidence: tuple[AcceptanceClaim, ...]

    @property
    def precision(self) -> float:
        """Return the fraction of emitted claims that are grounded correctly."""

        if self.actual_count == 0:
            return 1.0
        return (
            self.actual_count - len(self.unexpected) - len(self.invalid_evidence)
        ) / self.actual_count

    @property
    def recall(self) -> float:
        """Return the fraction of reviewed facts emitted with valid evidence."""

        if self.expected_count == 0:
            return 1.0
        return self.matched_count / self.expected_count

    @property
    def passes(self) -> bool:
        """Return whether this result satisfies ADR 0002's formal thresholds."""

        return self.precision == 1.0 and self.recall >= 0.95


def _subject_path(identifier: str, component_paths: dict[str, str], repository_id: str) -> str:
    if identifier == repository_id:
        return "."
    try:
        return component_paths[identifier]
    except KeyError as error:
        raise AcceptanceError(
            f"scan claim has an unknown subject identifier: {identifier}"
        ) from error


def _evidence_locators(
    result: ScanResult, evidence_ids: tuple[str, ...]
) -> tuple[EvidenceLocator, ...]:
    evidence_by_id = {item.id: item for item in result.evidence}
    try:
        locations = tuple(
            EvidenceLocator(evidence_by_id[identifier].location, evidence_by_id[identifier].locator)
            for identifier in evidence_ids
        )
    except KeyError as error:
        raise AcceptanceError(
            f"scan claim has an unknown evidence identifier: {error.args[0]}"
        ) from error
    return tuple(sorted(set(locations), key=lambda item: (item.location, item.locator or "")))


@implements("REQ034")
def claims_from_scan(repository: str, result: ScanResult) -> tuple[AcceptanceClaim, ...]:
    """Return every factual component, Verified finding, and Verified relationship."""

    if not repository:
        raise AcceptanceError("acceptance repository key must be non-empty")
    component_paths = {item.id: item.path for item in result.components}
    claims: list[AcceptanceClaim] = []
    for component in result.components:
        claims.append(
            AcceptanceClaim(
                repository,
                "component",
                component.path,
                component.ecosystem,
                {
                    "ecosystems": list(component.ecosystems),
                    "kind": component.kind,
                    "role": component.role,
                },
                _evidence_locators(result, component.evidence_ids),
            )
        )
    for finding in result.findings:
        if finding.classification == "verified":
            claims.append(
                AcceptanceClaim(
                    repository,
                    "finding",
                    _subject_path(finding.subject_id, component_paths, result.repository.id),
                    finding.code,
                    finding.summary,
                    _evidence_locators(result, finding.evidence_ids),
                )
            )
    for relationship in result.relationships:
        if relationship.classification == "verified":
            source = _subject_path(relationship.source_id, component_paths, result.repository.id)
            target = _subject_path(relationship.target_id, component_paths, result.repository.id)
            claims.append(
                AcceptanceClaim(
                    repository,
                    "relationship",
                    f"{source} -> {target}",
                    relationship.kind,
                    None,
                    _evidence_locators(result, relationship.evidence_ids),
                )
            )
    return tuple(sorted(claims, key=lambda item: item.key))


def _mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AcceptanceError(f"{context} must be an object")
    return cast(dict[str, object], value)


def _string(mapping: dict[str, object], field: str, context: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise AcceptanceError(f"{context}.{field} must be a non-empty string")
    return value


def _claim_from_mapping(value: object) -> AcceptanceClaim:
    mapping = _mapping(value, "claim")
    kind = _string(mapping, "kind", "claim")
    if kind not in {"component", "finding", "relationship"}:
        raise AcceptanceError("claim.kind is not supported")
    evidence_value = mapping.get("evidence")
    if not isinstance(evidence_value, list) or not evidence_value:
        raise AcceptanceError("claim.evidence must be a non-empty array")
    evidence: list[EvidenceLocator] = []
    for index, item in enumerate(evidence_value):
        evidence_mapping = _mapping(item, f"claim.evidence[{index}]")
        locator = evidence_mapping.get("locator")
        if locator is not None and (not isinstance(locator, str) or not locator):
            raise AcceptanceError(
                f"claim.evidence[{index}].locator must be a non-empty string or null"
            )
        evidence.append(
            EvidenceLocator(
                _string(evidence_mapping, "location", f"claim.evidence[{index}]"), locator
            )
        )
    claim = AcceptanceClaim(
        _string(mapping, "repository", "claim"),
        cast(ClaimKind, kind),
        _string(mapping, "subject", "claim"),
        _string(mapping, "code", "claim"),
        mapping.get("value"),
        tuple(sorted(set(evidence), key=lambda item: (item.location, item.locator or ""))),
    )
    if len(claim.evidence) != len(evidence):
        raise AcceptanceError("claim.evidence must not contain duplicates")
    return claim


@implements("REQ034")
def load_reviewed_claims(path: Path) -> tuple[AcceptanceClaim, ...]:
    """Load a version-1, human-reviewed expected-fact matrix."""

    try:
        document = _mapping(json.loads(path.read_text(encoding="utf-8")), "matrix")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"could not load expected-fact matrix: {error}") from error
    if document.get("schema_version") != 1:
        raise AcceptanceError("matrix.schema_version must be 1")
    if document.get("review_status") != "reviewed":
        raise AcceptanceError("matrix.review_status must be 'reviewed' before formal scoring")
    claims_value = document.get("claims")
    if not isinstance(claims_value, list) or not claims_value:
        raise AcceptanceError("matrix.claims must be a non-empty array")
    claims = tuple(
        sorted((_claim_from_mapping(item) for item in claims_value), key=lambda item: item.key)
    )
    if len({item.key for item in claims}) != len(claims):
        raise AcceptanceError("matrix.claims must not contain duplicate claim keys")
    return claims


@implements("REQ034")
def measure_claims(
    expected: tuple[AcceptanceClaim, ...], actual: tuple[AcceptanceClaim, ...]
) -> Measurement:
    """Measure every emitted factual claim against reviewed expected evidence."""

    expected_by_key = {item.key: item for item in expected}
    actual_by_key = {item.key: item for item in actual}
    if len(expected_by_key) != len(expected) or len(actual_by_key) != len(actual):
        raise AcceptanceError("expected and actual claims must each have unique keys")
    unexpected = tuple(item for item in actual if item.key not in expected_by_key)
    missing = tuple(item for item in expected if item.key not in actual_by_key)
    invalid_evidence = tuple(
        item
        for item in actual
        if item.key in expected_by_key
        and not set(expected_by_key[item.key].evidence).issubset(set(item.evidence))
    )
    matched_count = len(expected) - len(missing) - len(invalid_evidence)
    return Measurement(
        len(expected),
        len(actual),
        matched_count,
        unexpected,
        missing,
        invalid_evidence,
    )


@implements("REQ034")
def candidate_matrix(claims: tuple[AcceptanceClaim, ...]) -> bytes:
    """Serialize unreviewed candidate facts without presenting them as ground truth."""

    if not claims:
        raise AcceptanceError("candidate matrix requires at least one claim")
    document = {
        "schema_version": 1,
        "review_status": "candidate",
        "claims": [
            {
                "repository": claim.repository,
                "kind": claim.kind,
                "subject": claim.subject,
                "code": claim.code,
                "value": claim.value,
                "evidence": [
                    {"location": evidence.location, "locator": evidence.locator}
                    for evidence in claim.evidence
                ],
            }
            for claim in claims
        ],
    }
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
