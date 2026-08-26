#!/usr/bin/env python3
"""Run the explicit, local initialization usefulness-review workflow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from slygentify import dump_scan_projection_json, map_repository, plan_initialization
from slygentify._provenance import load_state_json
from slygentify.traceability import implements
from tools.support.initialization_acceptance import (
    InitializationAcceptanceError,
    InitializationReview,
    candidate_review_matrix,
    compare_initialization_reviews,
    initialization_corpus_metrics,
    initialization_review,
    load_reviewed_initialization_matrix,
)


class CorpusError(RuntimeError):
    """A selected formal corpus cannot be measured safely."""


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _validate_checkout_entries(path: Path) -> None:
    pending = [path]
    git_directory = path / ".git"
    while pending:
        directory = pending.pop()
        try:
            entries = tuple(os.scandir(directory))
        except OSError as error:
            raise CorpusError(
                f"formal checkout contains unreadable metadata: {path.name}"
            ) from error
        for entry in entries:
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise CorpusError(
                    f"formal checkout contains an unsafe filesystem entry: {path.name}"
                ) from error
            entry_path = Path(entry.path)
            is_symlink = stat.S_ISLNK(metadata.st_mode)
            is_reparse = _is_link_or_reparse(metadata)
            try:
                in_git_directory = entry_path == git_directory or entry_path.is_relative_to(
                    git_directory
                )
            except ValueError:
                in_git_directory = False
            if is_reparse:
                if in_git_directory or not is_symlink:
                    raise CorpusError(
                        f"formal checkout contains unsafe Git metadata or an unsupported link: "
                        f"{path.name}"
                    )
                continue
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(entry_path)
            elif not stat.S_ISREG(metadata.st_mode):
                raise CorpusError(
                    f"formal checkout contains an unsupported filesystem entry: {path.name}"
                )


def _require_direct_checkout(path: Path) -> Path:
    try:
        metadata = path.lstat()
        marker_metadata = (path / ".git").lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise CorpusError(f"formal checkout is unsafe: {path.name}") from error
    if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise CorpusError(f"formal checkout is not a direct directory: {path.name}")
    if _is_link_or_reparse(marker_metadata) or not stat.S_ISDIR(marker_metadata.st_mode):
        raise CorpusError(f"formal checkout is not a standalone Git checkout: {path.name}")
    _validate_checkout_entries(path)
    return resolved


def _load_corpus(path: Path) -> tuple[dict[str, str], ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CorpusError(f"could not load corpus manifest: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise CorpusError("corpus manifest must be a version-1 object")
    entries = document.get("repositories")
    if not isinstance(entries, list) or len(entries) != 20:
        raise CorpusError("corpus manifest must contain exactly 20 repositories")
    corpus: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise CorpusError("corpus repository entry must be an object")
        values = {name: entry.get(name) for name in ("id", "source_url", "commit", "category")}
        if not all(isinstance(value, str) and value for value in values.values()):
            raise CorpusError("corpus repository entry is incomplete")
        identifier = str(values["id"])
        if identifier in {".", ".."} or any(char in identifier for char in ("/", "\\", "\0")):
            raise CorpusError("corpus repository identifier must be one safe path segment")
        corpus.append({name: str(value) for name, value in values.items()})
    if len({entry["id"] for entry in corpus}) != len(corpus):
        raise CorpusError("corpus repository identifiers must be unique")
    return tuple(corpus)


def _git(path: Path, hooks_directory: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={hooks_directory}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-C",
                str(path),
                *arguments,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as error:
        raise CorpusError(f"Git could not inspect {path.name}") from error
    if result.returncode != 0:
        raise CorpusError(f"Git could not inspect {path.name}")
    return result.stdout.strip()


@implements("REQ045")
def _verify_checkout(root: Path, entry: dict[str, str], hooks_directory: Path) -> Path:
    candidate = root / entry["id"]
    if not os.path.lexists(candidate):
        raise CorpusError(f"formal checkout is missing: {entry['id']}")
    checkout = _require_direct_checkout(candidate)
    try:
        corpus_root = root.resolve(strict=True)
    except OSError as error:
        raise CorpusError("formal corpus root is unsafe") from error
    if checkout.parent != corpus_root:
        raise CorpusError(f"formal checkout escapes the corpus root: {entry['id']}")
    git_root = _git(checkout, hooks_directory, "rev-parse", "--show-toplevel")
    try:
        resolved_git_root = Path(git_root).resolve(strict=True)
    except OSError as error:
        raise CorpusError(f"Git reported an unsafe worktree root: {entry['id']}") from error
    if resolved_git_root != checkout:
        raise CorpusError(f"Git worktree escapes the formal checkout: {entry['id']}")
    if _git(checkout, hooks_directory, "rev-parse", "HEAD") != entry["commit"]:
        raise CorpusError(f"formal checkout is not at its approved commit: {entry['id']}")
    if _git(checkout, hooks_directory, "remote", "get-url", "origin") != entry["source_url"]:
        raise CorpusError(f"formal checkout has an unexpected origin: {entry['id']}")
    status = _git(
        checkout,
        hooks_directory,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if any(not line.startswith("?? ") for line in status.splitlines()):
        raise CorpusError(f"formal checkout has tracked changes: {entry['id']}")
    return checkout


@implements("REQ045")
def _snapshot(
    checkout: Path,
    commit: str,
    destination: Path,
    hooks_directory: Path,
) -> Path:
    try:
        shutil.copytree(checkout, destination, symlinks=True)
    except (OSError, shutil.Error) as error:
        raise CorpusError(f"could not create disposable snapshot for {checkout.name}") from error
    resolved = _require_direct_checkout(destination)
    git_root = _git(resolved, hooks_directory, "rev-parse", "--show-toplevel")
    try:
        resolved_git_root = Path(git_root).resolve(strict=True)
    except OSError as error:
        raise CorpusError(f"copied Git worktree is unsafe: {checkout.name}") from error
    if resolved_git_root != resolved:
        raise CorpusError(f"copied Git worktree escapes its snapshot: {checkout.name}")
    if _git(resolved, hooks_directory, "rev-parse", "HEAD") != commit:
        raise CorpusError(f"copied checkout changed commit: {checkout.name}")
    _git(resolved, hooks_directory, "clean", "--force", "-d", "-x", "--quiet")
    remaining = _git(resolved, hooks_directory, "clean", "--dry-run", "--force", "-d", "-x")
    if remaining:
        raise CorpusError(f"disposable snapshot could not be cleaned: {checkout.name}")
    return resolved


def _review_checkout(
    entry: dict[str, str], checkout: Path
) -> tuple[InitializationReview, str, bytes]:
    first = plan_initialization(checkout)
    second = plan_initialization(checkout)
    if first.agents_markdown != second.agents_markdown or first.state_json != second.state_json:
        raise CorpusError(f"initialization planning was not deterministic: {entry['id']}")
    first_projection = map_repository(checkout)
    second_projection = map_repository(checkout)
    first_projection_json = dump_scan_projection_json(first_projection)
    if first_projection_json != dump_scan_projection_json(second_projection):
        raise CorpusError(f"root map was not deterministic: {entry['id']}")
    if load_state_json(first.state_json).completion != first_projection.source_completion:
        raise CorpusError(f"initialization and root map completion disagree: {entry['id']}")
    return (
        initialization_review(
            entry["id"],
            entry["commit"],
            first.agents_markdown,
            first_projection,
        ),
        first.agents_markdown,
        first_projection_json,
    )


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _destination_is_safe(path: Path, protected_roots: Sequence[Path]) -> bool:
    candidate = path.resolve(strict=False)
    return all(not candidate.is_relative_to(root.resolve()) for root in protected_roots)


def _review_report(review: InitializationReview) -> dict[str, object]:
    return {
        "id": review.repository,
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


def _candidate_report(
    reviews: Sequence[InitializationReview], metrics: dict[str, int | float]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "candidate",
        "metrics": metrics,
        "repositories": [_review_report(review) for review in reviews],
    }


def _formal_report(
    reviews: Sequence[InitializationReview],
    metrics: dict[str, int | float],
    issues: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "formal",
        "reviewed_repositories": len(reviews),
        "metrics": metrics,
        "issues": list(issues),
        "passes": not issues,
    }


def _parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--candidate-matrix", type=Path)
    mode.add_argument("--reviewed-matrix", type=Path)
    parser.add_argument("--artifacts-directory", type=Path)
    parsed = parser.parse_args(arguments)
    if (parsed.candidate_matrix is None) != (parsed.artifacts_directory is None):
        parser.error("--candidate-matrix and --artifacts-directory must be supplied together")
    return parsed


@implements("REQ045")
def main(arguments: Sequence[str] | None = None) -> int:
    args = _parse_arguments(arguments)
    repository_root = Path(__file__).parents[1]
    protected_roots = (repository_root, args.formal_root)
    outputs = [args.report]
    if args.candidate_matrix is not None:
        outputs.extend((args.candidate_matrix, args.artifacts_directory))
    if not all(_destination_is_safe(output, protected_roots) for output in outputs):
        raise CorpusError(
            "review outputs must be outside the Slygentify repository and formal corpus"
        )
    if any(output.exists() for output in outputs):
        raise CorpusError("review output targets must not already exist")

    corpus_path = repository_root / "tests" / "acceptance" / "corpus-v1.json"
    corpus = _load_corpus(corpus_path)
    reviews: list[InitializationReview] = []

    with tempfile.TemporaryDirectory(prefix="slygentify-initialization-review-") as temporary:
        temporary_root = Path(temporary)
        hooks_directory = temporary_root / "disabled-hooks"
        hooks_directory.mkdir()
        source_checkouts = tuple(
            _verify_checkout(args.formal_root, entry, hooks_directory) for entry in corpus
        )
        artifacts_staging = temporary_root / "artifacts"
        if args.candidate_matrix is not None:
            artifacts_staging.mkdir()
        for entry, checkout in zip(corpus, source_checkouts, strict=True):
            snapshot = _snapshot(
                checkout,
                entry["commit"],
                temporary_root / entry["id"],
                hooks_directory,
            )
            review, markdown, projection_json = _review_checkout(entry, snapshot)
            reviews.append(review)
            if args.candidate_matrix is not None:
                repository_artifacts = artifacts_staging / entry["id"]
                repository_artifacts.mkdir()
                (repository_artifacts / "AGENTS.md").write_text(markdown, encoding="utf-8")
                (repository_artifacts / "root-map.json").write_bytes(projection_json)

        try:
            metrics = initialization_corpus_metrics(reviews)
        except InitializationAcceptanceError as error:
            raise CorpusError(str(error)) from error
        if args.candidate_matrix is not None:
            args.candidate_matrix.parent.mkdir(parents=True, exist_ok=True)
            args.artifacts_directory.parent.mkdir(parents=True, exist_ok=True)
            args.candidate_matrix.write_text(candidate_review_matrix(reviews), encoding="utf-8")
            shutil.move(str(artifacts_staging), str(args.artifacts_directory))
            _write_json(args.report, _candidate_report(reviews, metrics))
            return 0

    expected_commits = {entry["id"]: entry["commit"] for entry in corpus}
    try:
        reviewed = load_reviewed_initialization_matrix(args.reviewed_matrix, expected_commits)
    except InitializationAcceptanceError as error:
        raise CorpusError(str(error)) from error
    issues = compare_initialization_reviews(reviewed, reviews)
    _write_json(args.report, _formal_report(reviews, metrics, issues))
    return 0 if not issues else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CorpusError as error:
        raise SystemExit(f"Error: {error}") from error
