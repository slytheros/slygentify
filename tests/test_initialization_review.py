"""Tests for deterministic bootstrap-to-map human-review evidence."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from slygentify import project_scan
from slygentify._generation import generate_agents_document
from tests.scan_samples import sample_result
from tools import review_initialization as review_tool
from tools.support.initialization_acceptance import (
    InitializationAcceptanceError,
    InitializationReview,
    candidate_review_matrix,
    compare_initialization_reviews,
    initialization_corpus_metrics,
    initialization_review,
    load_reviewed_initialization_matrix,
)

_CRITERIA = (
    "bootstrap_clarity",
    "component_index_accuracy",
    "map_navigation",
    "boundary_honesty",
    "safety",
    "concision",
)


def _run_git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *arguments],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def _checkout(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    checkout = tmp_path / "example"
    checkout.mkdir()
    _run_git(checkout, "init", "--quiet")
    _run_git(checkout, "config", "user.email", "acceptance@example.invalid")
    _run_git(checkout, "config", "user.name", "Acceptance Test")
    (checkout / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (checkout / "pyproject.toml").write_text(
        "[project]\nname = 'example'\nrequires-python = '>=3.11'\n",
        encoding="utf-8",
    )
    hooks = checkout / ".hooks"
    hooks.mkdir()
    for name, sentinel in (("post-checkout", "checkout-hook-ran"), ("fsmonitor", "fsmonitor-ran")):
        hook = hooks / name
        hook.write_text(
            f"#!/bin/sh\nprintf invoked > ../{sentinel}\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
    _run_git(checkout, "add", "--all")
    _run_git(checkout, "commit", "--quiet", "-m", "fixture")
    commit = _run_git(checkout, "rev-parse", "HEAD")
    source_url = "https://example.invalid/example.git"
    _run_git(checkout, "remote", "add", "origin", source_url)
    _run_git(checkout, "config", "core.hooksPath", ".hooks")
    _run_git(checkout, "config", "core.fsmonitor", ".hooks/fsmonitor")
    return checkout, {
        "id": "example",
        "source_url": source_url,
        "commit": commit,
        "category": "python",
    }


def _review(repository: str = "example") -> InitializationReview:
    result = sample_result()
    return initialization_review(
        repository,
        "a" * 40,
        generate_agents_document(result).markdown,
        project_scan(result),
    )


def _reviewed_document(review: InitializationReview) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "review_status": "reviewed",
        "reviewer": "maintainer",
        "reviewed_on": "2026-08-20",
        "reviews": [
            {
                **asdict(review),
                "criteria": {criterion: "pass" for criterion in _CRITERIA},
                "overall": "pass",
            }
        ],
    }


@pytest.mark.verifies("TST045")
def test_candidate_matrix_binds_both_artifacts_and_only_safe_metrics() -> None:
    review = _review()

    first = candidate_review_matrix((review,))
    second = candidate_review_matrix((review,))
    document = json.loads(first)

    assert first == second
    assert document["review_status"] == "pending-human-review"
    entry = document["reviews"][0]
    assert entry["agents_sha256"] == review.agents_sha256
    assert entry["projection_sha256"] == review.projection_sha256
    assert entry["criteria"] == {criterion: "pending" for criterion in _CRITERIA}
    assert "# AGENTS.md" not in first
    assert review.agents_byte_count <= 4096
    assert review.agents_component_count == 1
    assert review.projection_byte_count <= 8192
    assert review.projection_record_count > 0


@pytest.mark.verifies("TST045")
def test_disposable_snapshot_is_contained_clean_and_deterministic(tmp_path: Path) -> None:
    checkout, entry = _checkout(tmp_path)
    ignored = checkout / "ignored.txt"
    ignored.write_text("source-only", encoding="utf-8")
    hooks_directory = tmp_path / "disabled-hooks"
    hooks_directory.mkdir()

    verified = review_tool._verify_checkout(tmp_path, entry, hooks_directory)
    snapshot = review_tool._snapshot(
        verified,
        entry["commit"],
        tmp_path / "snapshot",
        hooks_directory,
    )
    first = review_tool._review_checkout(entry, snapshot)
    second = review_tool._review_checkout(entry, snapshot)

    assert ignored.read_text(encoding="utf-8") == "source-only"
    assert not (snapshot / "ignored.txt").exists()
    assert not (tmp_path / "checkout-hook-ran").exists()
    assert not (tmp_path / "fsmonitor-ran").exists()
    assert first == second
    assert review_tool._git(snapshot, hooks_directory, "rev-parse", "--show-toplevel")


@pytest.mark.verifies("TST045")
def test_disposable_snapshot_preserves_links_without_reading_targets(tmp_path: Path) -> None:
    checkout, entry = _checkout(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must-not-be-copied", encoding="utf-8")
    link = checkout / "linked-secret.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    _run_git(checkout, "-c", "core.fsmonitor=false", "add", "linked-secret.txt")
    _run_git(
        checkout,
        "-c",
        "core.fsmonitor=false",
        "commit",
        "--quiet",
        "-m",
        "tracked link",
    )
    entry["commit"] = _run_git(
        checkout,
        "-c",
        "core.fsmonitor=false",
        "rev-parse",
        "HEAD",
    )
    hooks_directory = tmp_path / "disabled-hooks"
    hooks_directory.mkdir()

    verified = review_tool._verify_checkout(tmp_path, entry, hooks_directory)
    snapshot = review_tool._snapshot(
        verified,
        entry["commit"],
        tmp_path / "snapshot",
        hooks_directory,
    )
    copied_link = snapshot / "linked-secret.txt"

    assert copied_link.is_symlink()
    assert os.readlink(copied_link) == os.readlink(link)
    assert not (snapshot / "outside-secret.txt").exists()


@pytest.mark.verifies("TST045")
@pytest.mark.parametrize("marker_kind", ["file", "symlink"])
def test_formal_checkout_rejects_nonstandalone_git_metadata(
    tmp_path: Path,
    marker_kind: str,
) -> None:
    checkout = tmp_path / "example"
    checkout.mkdir()
    marker = checkout / ".git"
    if marker_kind == "file":
        marker.write_text("gitdir: ../shared", encoding="utf-8")
    else:
        target = tmp_path / "shared"
        target.mkdir()
        try:
            marker.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation is unavailable")
    hooks_directory = tmp_path / "disabled-hooks"
    hooks_directory.mkdir()
    entry = {
        "id": "example",
        "source_url": "https://example.invalid/example.git",
        "commit": "a" * 40,
        "category": "python",
    }

    with pytest.raises(review_tool.CorpusError, match="standalone Git checkout"):
        review_tool._verify_checkout(tmp_path, entry, hooks_directory)


@pytest.mark.verifies("TST045")
def test_formal_checkout_rejects_linked_git_metadata(tmp_path: Path) -> None:
    checkout, entry = _checkout(tmp_path)
    outside = tmp_path / "outside-config"
    outside.write_text("external metadata must not be read", encoding="utf-8")
    metadata_link = checkout / ".git" / "linked-metadata"
    try:
        metadata_link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    hooks_directory = tmp_path / "disabled-hooks"
    hooks_directory.mkdir()

    with pytest.raises(review_tool.CorpusError, match="unsafe Git metadata"):
        review_tool._verify_checkout(tmp_path, entry, hooks_directory)


@pytest.mark.verifies("TST045")
@pytest.mark.parametrize(
    "reviews",
    [
        (),
        (_review(), _review()),
        (replace(_review(), agents_sha256="bad"),),
        (replace(_review(), projection_sha256="bad"),),
        (replace(_review(), agents_byte_count=-1),),
        (replace(_review(), projection_omitted_record_count=-1),),
        (replace(_review(), completion="unknown"),),
        (replace(_review(), repository=""),),
        (replace(_review(), commit=""),),
    ],
)
def test_candidate_review_matrix_rejects_invalid_records(
    reviews: tuple[InitializationReview, ...],
) -> None:
    with pytest.raises(InitializationAcceptanceError):
        candidate_review_matrix(reviews)


@pytest.mark.verifies("TST045")
def test_reviewed_matrix_requires_complete_human_signoff(tmp_path: Path) -> None:
    review = _review()
    path = tmp_path / "matrix.json"
    document = _reviewed_document(review)
    path.write_text(json.dumps(document), encoding="utf-8")

    assert load_reviewed_initialization_matrix(path, {"example": "a" * 40}) == (review,)

    document["review_status"] = "pending-human-review"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(InitializationAcceptanceError, match="completed human review"):
        load_reviewed_initialization_matrix(path, {"example": "a" * 40})

    document = _reviewed_document(review)
    document["reviewer"] = ""
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(InitializationAcceptanceError, match="reviewer"):
        load_reviewed_initialization_matrix(path, {"example": "a" * 40})

    document = _reviewed_document(review)
    document["reviewed_on"] = "not-a-date"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(InitializationAcceptanceError, match="reviewed_on"):
        load_reviewed_initialization_matrix(path, {"example": "a" * 40})


@pytest.mark.verifies("TST045")
@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "must be an object"),
        ({"schema_version": 2}, "schema_version"),
        (
            {
                "schema_version": 1,
                "review_status": "reviewed",
                "reviewer": "maintainer",
                "reviewed_on": "2026-08-20",
                "reviews": "not-a-list",
            },
            "reviews",
        ),
        (
            {
                "schema_version": 1,
                "review_status": "reviewed",
                "reviewer": "maintainer",
                "reviewed_on": "2026-08-20",
                "reviews": ["not-an-object"],
            },
            "entry",
        ),
    ],
)
def test_reviewed_matrix_rejects_invalid_top_level_shapes(
    tmp_path: Path, document: object, message: str
) -> None:
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(InitializationAcceptanceError, match=message):
        load_reviewed_initialization_matrix(path, {"example": "a" * 40})


@pytest.mark.verifies("TST045")
@pytest.mark.parametrize(
    ("update", "message"),
    [
        (lambda entry: entry.pop("overall"), "unsupported or missing"),
        (lambda entry: entry.__setitem__("commit", "b" * 40), "approved commit"),
        (lambda entry: entry.__setitem__("agents_sha256", "bad"), "SHA-256"),
        (lambda entry: entry.__setitem__("projection_sha256", "bad"), "SHA-256"),
        (lambda entry: entry.__setitem__("agents_byte_count", True), "agents_byte_count"),
        (lambda entry: entry.__setitem__("projection_record_count", -1), "projection_record_count"),
        (lambda entry: entry.__setitem__("completion", "unknown"), "completion"),
        (lambda entry: entry.__setitem__("criteria", {}), "criteria"),
        (
            lambda entry: entry["criteria"].__setitem__("map_navigation", "pending"),
            "map_navigation",
        ),
        (lambda entry: entry.__setitem__("overall", "pending"), "overall"),
    ],
)
def test_reviewed_matrix_rejects_invalid_entries(
    tmp_path: Path, update: Callable[[dict[str, Any]], None], message: str
) -> None:
    review = _review()
    document = _reviewed_document(review)
    entry = document["reviews"][0]
    update(entry)
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(InitializationAcceptanceError, match=message):
        load_reviewed_initialization_matrix(path, {"example": "a" * 40})


@pytest.mark.verifies("TST045")
def test_reviewed_matrix_rejects_duplicate_missing_and_unreadable_reviews(
    tmp_path: Path,
) -> None:
    review = _review()
    path = tmp_path / "matrix.json"
    path.write_bytes(b"\xff")
    with pytest.raises(InitializationAcceptanceError, match="could not load"):
        load_reviewed_initialization_matrix(path, {"example": "a" * 40})

    document = _reviewed_document(review)
    duplicate = document["reviews"][0].copy()
    assert isinstance(document["reviews"], list)
    document["reviews"].append(duplicate)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(InitializationAcceptanceError, match="duplicate"):
        load_reviewed_initialization_matrix(path, {"example": "a" * 40})

    document = _reviewed_document(review)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(InitializationAcceptanceError, match="approved corpus"):
        load_reviewed_initialization_matrix(path, {"example": "a" * 40, "missing": "b" * 40})


@pytest.mark.verifies("TST045")
def test_corpus_metrics_enforce_all_default_size_gates() -> None:
    reviews = tuple(
        replace(_review(f"repo-{index:02d}"), commit=f"{index:040x}") for index in range(20)
    )

    metrics = initialization_corpus_metrics(reviews)

    assert metrics["repository_count"] == 20
    assert metrics["agents_median_bytes"] <= 2048
    assert metrics["agents_max_bytes"] <= 4096
    assert metrics["projection_max_bytes"] <= 8192
    with pytest.raises(InitializationAcceptanceError, match="20 repositories"):
        initialization_corpus_metrics(reviews[:-1])
    with pytest.raises(InitializationAcceptanceError, match="4096"):
        initialization_corpus_metrics((replace(reviews[0], agents_byte_count=4097), *reviews[1:]))
    with pytest.raises(InitializationAcceptanceError, match="median"):
        initialization_corpus_metrics(
            tuple(replace(review, agents_byte_count=2049) for review in reviews)
        )
    with pytest.raises(InitializationAcceptanceError, match="8192"):
        initialization_corpus_metrics(
            (replace(reviews[0], projection_byte_count=8193), *reviews[1:])
        )


@pytest.mark.verifies("TST045")
def test_review_comparison_reports_only_repository_level_differences() -> None:
    review = _review()
    changed = replace(review, projection_sha256="b" * 64)
    other = replace(_review("other"), commit="c" * 40)

    assert compare_initialization_reviews((review,), (review,)) == ()
    assert compare_initialization_reviews((review,), ()) == ("missing repository: example",)
    assert compare_initialization_reviews((), (review,)) == ("unexpected repository: example",)
    assert compare_initialization_reviews((review,), (changed,)) == (
        "initialization artifacts changed: example",
    )
    assert compare_initialization_reviews((review,), (review, other)) == (
        "unexpected repository: other",
    )


@pytest.mark.verifies("TST045")
def test_committed_pending_matrix_covers_every_approved_repository() -> None:
    acceptance_directory = Path(__file__).parent / "acceptance"
    corpus = json.loads((acceptance_directory / "corpus-v1.json").read_text(encoding="utf-8"))
    matrix = json.loads(
        (acceptance_directory / "initialization-review-v1.json").read_text(encoding="utf-8")
    )

    assert matrix["schema_version"] == 1
    assert matrix["review_status"] == "pending-human-review"
    assert {item["repository"] for item in matrix["reviews"]} == {
        item["id"] for item in corpus["repositories"]
    }
    assert all(len(item["agents_sha256"]) == 64 for item in matrix["reviews"])
    assert all(len(item["projection_sha256"]) == 64 for item in matrix["reviews"])
    assert all(item["agents_byte_count"] <= 4096 for item in matrix["reviews"])
    assert all(item["projection_byte_count"] <= 8192 for item in matrix["reviews"])
