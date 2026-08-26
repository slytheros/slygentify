"""Development-only, deterministic initialization acceptance evidence."""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from slygentify._projection_serialization import dump_scan_projection_json
from slygentify.models import ScanProjection
from slygentify.traceability import implements

_SCHEMA_VERSION = 1
_PENDING_STATUS = "pending-human-review"
_REVIEWED_STATUS = "reviewed"
_CRITERIA = (
    "bootstrap_clarity",
    "component_index_accuracy",
    "map_navigation",
    "boundary_honesty",
    "safety",
    "concision",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMPONENT_LINE = re.compile(r"^- `.*; evidence: .+\.$", re.MULTILINE)
_COMPONENT_OMISSION = re.compile(
    r"^- Additional primary components omitted: ([0-9]+)\.$", re.MULTILINE
)


class InitializationAcceptanceError(ValueError):
    """A malformed or incomplete initialization acceptance record."""


@dataclass(frozen=True, slots=True)
class InitializationReview:
    """Digest-only metrics for one bootstrap document and default root projection."""

    repository: str
    commit: str
    agents_sha256: str
    agents_byte_count: int
    agents_line_count: int
    agents_component_count: int
    agents_omitted_component_count: int
    projection_sha256: str
    projection_byte_count: int
    projection_record_count: int
    projection_omitted_record_count: int
    completion: str


_EMPTY_REVIEW = InitializationReview("x", "x", "0" * 64, 0, 0, 0, 0, "0" * 64, 0, 0, 0, "complete")


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InitializationAcceptanceError(f"{field} must be a non-empty string")
    return value


def _require_sha256(value: object, field: str) -> str:
    digest = _require_string(value, field)
    if _SHA256.fullmatch(digest) is None:
        raise InitializationAcceptanceError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _require_non_negative_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InitializationAcceptanceError(f"{field} must be a non-negative integer")
    return value


def _review_key(review: InitializationReview) -> tuple[str, str]:
    return (review.repository, review.commit)


def _validate_reviews(reviews: Sequence[InitializationReview]) -> tuple[InitializationReview, ...]:
    if not reviews:
        raise InitializationAcceptanceError("review matrix must contain at least one repository")
    keys = [_review_key(item) for item in reviews]
    if len(set(keys)) != len(keys):
        raise InitializationAcceptanceError("review matrix contains duplicate repositories")
    metric_fields = (
        "agents_byte_count",
        "agents_line_count",
        "agents_component_count",
        "agents_omitted_component_count",
        "projection_byte_count",
        "projection_record_count",
        "projection_omitted_record_count",
    )
    for review in reviews:
        _require_string(review.repository, "repository")
        _require_string(review.commit, "commit")
        _require_sha256(review.agents_sha256, "agents_sha256")
        _require_sha256(review.projection_sha256, "projection_sha256")
        for field in metric_fields:
            _require_non_negative_integer(getattr(review, field), field)
        if review.completion not in {"complete", "partial"}:
            raise InitializationAcceptanceError("completion must be complete or partial")
    return tuple(sorted(reviews, key=_review_key))


def _agents_metrics(markdown: str) -> tuple[int, int]:
    component_count = len(_COMPONENT_LINE.findall(markdown))
    omission = _COMPONENT_OMISSION.search(markdown)
    return component_count, 0 if omission is None else int(omission.group(1))


@implements("REQ045")
def initialization_review(
    repository: str,
    commit: str,
    agents_markdown: str,
    projection: ScanProjection,
) -> InitializationReview:
    """Build a digest-only record for one bootstrap-to-map review workflow."""
    agents_bytes = agents_markdown.encode("utf-8")
    projection_bytes = dump_scan_projection_json(projection)
    component_count, omitted_component_count = _agents_metrics(agents_markdown)
    projection_record_count = sum(
        len(records)
        for records in (
            projection.components,
            projection.relationships,
            projection.findings,
            projection.diagnostics,
            projection.skipped_scopes,
            projection.evidence,
        )
    )
    return InitializationReview(
        repository=repository,
        commit=commit,
        agents_sha256=hashlib.sha256(agents_bytes).hexdigest(),
        agents_byte_count=len(agents_bytes),
        agents_line_count=len(agents_markdown.splitlines()),
        agents_component_count=component_count,
        agents_omitted_component_count=omitted_component_count,
        projection_sha256=hashlib.sha256(projection_bytes).hexdigest(),
        projection_byte_count=len(projection_bytes),
        projection_record_count=projection_record_count,
        projection_omitted_record_count=sum(item.count for item in projection.omissions),
        completion=projection.source_completion,
    )


def _review_object(review: InitializationReview) -> dict[str, object]:
    return {
        "repository": review.repository,
        "commit": review.commit,
        "agents_sha256": review.agents_sha256,
        "agents_byte_count": review.agents_byte_count,
        "agents_line_count": review.agents_line_count,
        "agents_component_count": review.agents_component_count,
        "agents_omitted_component_count": review.agents_omitted_component_count,
        "projection_sha256": review.projection_sha256,
        "projection_byte_count": review.projection_byte_count,
        "projection_record_count": review.projection_record_count,
        "projection_omitted_record_count": review.projection_omitted_record_count,
        "completion": review.completion,
    }


@implements("REQ045")
def candidate_review_matrix(reviews: Sequence[InitializationReview]) -> str:
    """Serialize a deterministic human-review template for candidate artifacts."""
    ordered = _validate_reviews(reviews)
    document = {
        "schema_version": _SCHEMA_VERSION,
        "review_status": _PENDING_STATUS,
        "reviews": [
            {
                **_review_object(review),
                "criteria": {criterion: "pending" for criterion in _CRITERIA},
                "overall": "pending",
            }
            for review in ordered
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _load_document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InitializationAcceptanceError(f"could not load review matrix: {error}") from error
    if not isinstance(document, dict):
        raise InitializationAcceptanceError("review matrix must be an object")
    return document


def _review_from_object(value: object, expected_commits: Mapping[str, str]) -> InitializationReview:
    if not isinstance(value, dict):
        raise InitializationAcceptanceError("review entry must be an object")
    required = {*_review_object(_EMPTY_REVIEW), "criteria", "overall"}
    if set(value) != required:
        raise InitializationAcceptanceError("review entry has unsupported or missing fields")
    repository = _require_string(value["repository"], "repository")
    commit = _require_string(value["commit"], "commit")
    if expected_commits.get(repository) != commit:
        raise InitializationAcceptanceError(
            f"review entry does not match the approved commit: {repository}"
        )
    criteria = value["criteria"]
    if not isinstance(criteria, dict) or set(criteria) != set(_CRITERIA):
        raise InitializationAcceptanceError("review criteria are incomplete")
    for criterion in _CRITERIA:
        if criteria[criterion] != "pass":
            raise InitializationAcceptanceError(f"review criterion is not accepted: {criterion}")
    if value["overall"] != "pass":
        raise InitializationAcceptanceError("review overall result must pass")
    return InitializationReview(
        repository=repository,
        commit=commit,
        agents_sha256=_require_sha256(value["agents_sha256"], "agents_sha256"),
        agents_byte_count=_require_non_negative_integer(
            value["agents_byte_count"], "agents_byte_count"
        ),
        agents_line_count=_require_non_negative_integer(
            value["agents_line_count"], "agents_line_count"
        ),
        agents_component_count=_require_non_negative_integer(
            value["agents_component_count"], "agents_component_count"
        ),
        agents_omitted_component_count=_require_non_negative_integer(
            value["agents_omitted_component_count"], "agents_omitted_component_count"
        ),
        projection_sha256=_require_sha256(value["projection_sha256"], "projection_sha256"),
        projection_byte_count=_require_non_negative_integer(
            value["projection_byte_count"], "projection_byte_count"
        ),
        projection_record_count=_require_non_negative_integer(
            value["projection_record_count"], "projection_record_count"
        ),
        projection_omitted_record_count=_require_non_negative_integer(
            value["projection_omitted_record_count"], "projection_omitted_record_count"
        ),
        completion=_require_string(value["completion"], "completion"),
    )


@implements("REQ045")
def load_reviewed_initialization_matrix(
    path: Path, expected_commits: Mapping[str, str]
) -> tuple[InitializationReview, ...]:
    """Load a complete, signed-off matrix for the approved corpus only."""
    document = _load_document(path)
    if document.get("schema_version") != _SCHEMA_VERSION:
        raise InitializationAcceptanceError("review matrix schema_version is unsupported")
    if document.get("review_status") != _REVIEWED_STATUS:
        raise InitializationAcceptanceError("review matrix has not completed human review")
    _require_string(document.get("reviewer"), "reviewer")
    reviewed_on = _require_string(document.get("reviewed_on"), "reviewed_on")
    try:
        date.fromisoformat(reviewed_on)
    except ValueError as error:
        raise InitializationAcceptanceError("reviewed_on must be an ISO-8601 date") from error
    entries = document.get("reviews")
    if not isinstance(entries, list):
        raise InitializationAcceptanceError("review matrix reviews must be a list")
    reviews = _validate_reviews(
        tuple(_review_from_object(entry, expected_commits) for entry in entries)
    )
    if {review.repository for review in reviews} != set(expected_commits):
        raise InitializationAcceptanceError("review matrix does not cover the approved corpus")
    return reviews


@implements("REQ045")
def initialization_corpus_metrics(
    reviews: Sequence[InitializationReview],
) -> dict[str, int | float]:
    """Validate the renewed 20-repository size gates and return aggregate metrics."""
    ordered = _validate_reviews(reviews)
    if len(ordered) != 20:
        raise InitializationAcceptanceError("initialization review requires 20 repositories")
    agents_sizes = [item.agents_byte_count for item in ordered]
    projection_sizes = [item.projection_byte_count for item in ordered]
    median_agents_bytes = float(statistics.median(agents_sizes))
    if max(agents_sizes) > 4096:
        raise InitializationAcceptanceError("default AGENTS.md exceeds 4096 bytes")
    if median_agents_bytes > 2048:
        raise InitializationAcceptanceError("median default AGENTS.md exceeds 2048 bytes")
    if max(projection_sizes) > 8192:
        raise InitializationAcceptanceError("default root projection exceeds 8192 bytes")
    return {
        "repository_count": len(ordered),
        "agents_median_bytes": median_agents_bytes,
        "agents_max_bytes": max(agents_sizes),
        "agents_indexes_with_omissions": sum(
            item.agents_omitted_component_count > 0 for item in ordered
        ),
        "projection_max_bytes": max(projection_sizes),
        "projections_with_omissions": sum(
            item.projection_omitted_record_count > 0 for item in ordered
        ),
    }


@implements("REQ045")
def compare_initialization_reviews(
    reviewed: Sequence[InitializationReview], current: Sequence[InitializationReview]
) -> tuple[str, ...]:
    """Return deterministic, source-free differences between reviewed and current output."""
    reviewed_by_repository = {
        item.repository: item for item in (_validate_reviews(reviewed) if reviewed else ())
    }
    current_by_repository = {
        item.repository: item for item in (_validate_reviews(current) if current else ())
    }
    issues: list[str] = []
    for repository in sorted(set(reviewed_by_repository) | set(current_by_repository)):
        expected = reviewed_by_repository.get(repository)
        actual = current_by_repository.get(repository)
        if expected is None:
            issues.append(f"unexpected repository: {repository}")
        elif actual is None:
            issues.append(f"missing repository: {repository}")
        elif expected != actual:
            issues.append(f"initialization artifacts changed: {repository}")
    return tuple(issues)
