#!/usr/bin/env python3
"""Run the explicit, local ADR 0002 acceptance measurement workflow."""

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

from slygentify import dump_scan_json, scan_repository
from tools.support.acceptance import (
    AcceptanceClaim,
    AcceptanceError,
    candidate_matrix,
    claims_from_scan,
    load_reviewed_claims,
    measure_claims,
)


class CorpusError(RuntimeError):
    """Raised when a selected local corpus input is unsuitable for measurement."""


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _validate_checkout_entries(path: Path) -> None:
    """Reject metadata that could redirect Git outside a formal checkout."""
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
            try:
                in_git_directory = entry_path.is_relative_to(git_directory)
            except ValueError:
                in_git_directory = False
            is_link = stat.S_ISLNK(metadata.st_mode)
            if _is_link_or_reparse(metadata):
                if in_git_directory or not is_link:
                    raise CorpusError(f"formal checkout contains unsafe Git metadata: {path.name}")
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


def _load_corpus(path: Path) -> tuple[dict[str, object], ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CorpusError(f"could not load corpus manifest: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise CorpusError("corpus manifest must be a version-1 object")
    entries = document.get("repositories")
    if not isinstance(entries, list) or len(entries) != 20:
        raise CorpusError("corpus manifest must contain exactly 20 repositories")
    result: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(field), str) and entry[field]
            for field in ("id", "source_url", "commit")
        ):
            raise CorpusError("corpus repository entries require id, source_url, and commit")
        result.append(entry)
    if len({entry["id"] for entry in result}) != len(result):
        raise CorpusError("corpus repository identifiers must be unique")
    return tuple(result)


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
            env=_git_environment(),
        )
    except OSError as error:
        raise CorpusError(f"could not run Git for {path.name}: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Git failed").strip()
        raise CorpusError(f"Git could not inspect {path.name}: {detail}")
    return result.stdout.strip()


def _verify_formal_checkout(root: Path, entry: dict[str, object], hooks_directory: Path) -> Path:
    identifier = str(entry["id"])
    checkout = root / identifier
    try:
        corpus_root = root.resolve(strict=True)
    except OSError as error:
        raise CorpusError(f"formal checkout is unsafe: {identifier}") from error
    checkout = _require_direct_checkout(checkout)
    if checkout.parent != corpus_root:
        raise CorpusError(f"formal checkout is not a direct corpus directory: {identifier}")
    git_root = _git(checkout, hooks_directory, "rev-parse", "--show-toplevel")
    try:
        resolved_git_root = Path(git_root).resolve(strict=True)
    except OSError as error:
        raise CorpusError(f"Git reported an unsafe worktree root: {identifier}") from error
    if resolved_git_root != checkout:
        raise CorpusError(f"Git worktree escapes the formal checkout: {identifier}")
    if _git(checkout, hooks_directory, "rev-parse", "HEAD") != entry["commit"]:
        raise CorpusError(f"formal checkout is not at its approved commit: {identifier}")
    if _git(checkout, hooks_directory, "remote", "get-url", "origin") != entry["source_url"]:
        raise CorpusError(f"formal checkout has an unexpected origin: {identifier}")
    status = _git(checkout, hooks_directory, "status", "--porcelain=v1", "--untracked-files=all")
    if any(not line.startswith("?? ") for line in status.splitlines()):
        raise CorpusError(f"formal checkout has tracked changes: {identifier}")
    return checkout


def _snapshot(checkout: Path, commit: str, destination: Path, hooks_directory: Path) -> Path:
    try:
        shutil.copytree(checkout, destination, symlinks=True)
    except OSError as error:
        raise CorpusError(
            f"could not create disposable snapshot for {checkout.name}: {error}"
        ) from error
    snapshot = _require_direct_checkout(destination)
    git_root = _git(snapshot, hooks_directory, "rev-parse", "--show-toplevel")
    try:
        resolved_git_root = Path(git_root).resolve(strict=True)
    except OSError as error:
        raise CorpusError(f"copied Git worktree is unsafe: {checkout.name}") from error
    if resolved_git_root != snapshot:
        raise CorpusError(f"copied Git worktree escapes its snapshot: {checkout.name}")
    if _git(snapshot, hooks_directory, "clean", "--force", "-d", "-x", "--quiet") != "":
        raise CorpusError(f"could not clean the copied checkout: {checkout.name}")
    if _git(snapshot, hooks_directory, "checkout", "--quiet", "--detach", commit) != "":
        raise CorpusError(f"could not select approved commit for {checkout.name}")
    return snapshot


def _scan_twice(identifier: str, checkout: Path) -> tuple[AcceptanceClaim, ...]:
    first = scan_repository(checkout)
    second = scan_repository(checkout)
    if dump_scan_json(first) != dump_scan_json(second):
        raise CorpusError(f"scan JSON was not deterministic: {identifier}")
    return claims_from_scan(identifier, first)


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _formal_measurement(
    root: Path,
    entries: tuple[dict[str, object], ...],
    matrix: Path | None,
    candidate_output: Path | None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="slygentify-acceptance-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        hooks_directory = temporary_root / "disabled-hooks"
        hooks_directory.mkdir()
        claims = tuple(
            claim
            for entry in entries
            for claim in _scan_twice(
                str(entry["id"]),
                _snapshot(
                    _verify_formal_checkout(root, entry, hooks_directory),
                    str(entry["commit"]),
                    temporary_root / str(entry["id"]),
                    hooks_directory,
                ),
            )
        )
    if candidate_output is not None:
        candidate_output.parent.mkdir(parents=True, exist_ok=True)
        candidate_output.write_bytes(candidate_matrix(claims))
    if matrix is None:
        return {"mode": "candidate", "claim_count": len(claims), "passes": False}
    measurement = measure_claims(load_reviewed_claims(matrix), claims)
    return {
        "mode": "formal",
        "expected_count": measurement.expected_count,
        "actual_count": measurement.actual_count,
        "matched_count": measurement.matched_count,
        "unexpected_count": len(measurement.unexpected),
        "missing_count": len(measurement.missing),
        "invalid_evidence_count": len(measurement.invalid_evidence),
        "precision": measurement.precision,
        "recall": measurement.recall,
        "passes": measurement.passes,
    }


def _supplemental_measurement(root: Path) -> dict[str, object]:
    try:
        corpus_root = root.resolve(strict=True)
        entries = tuple(root.iterdir())
    except OSError as error:
        raise CorpusError("could not inspect the supplemental corpus root") from error
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    checkouts: list[Path] = []
    for path in entries:
        try:
            metadata = path.lstat()
        except OSError as error:
            raise CorpusError(f"supplemental corpus entry is unsafe: {path.name}") from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
            or path.parent.resolve() != corpus_root
        ):
            raise CorpusError(
                f"supplemental checkout is not a direct corpus directory: {path.name}"
            )
        if stat.S_ISDIR(metadata.st_mode) and (path / ".git").exists():
            checkouts.append(path)
    checkouts.sort(key=lambda path: path.name)
    if len(checkouts) != 71:
        raise CorpusError(
            f"supplemental corpus must contain exactly 71 Git checkouts, found {len(checkouts)}"
        )
    results = [scan_repository(path) for path in checkouts]
    return {
        "mode": "supplemental",
        "repositories": len(results),
        "complete": sum(result.completion == "complete" for result in results),
        "partial": sum(result.completion == "partial" for result in results),
        "components": sum(len(result.components) for result in results),
        "findings": sum(len(result.findings) for result in results),
        "diagnostics": sum(len(result.diagnostics) for result in results),
        "skipped_scopes": sum(len(result.skipped_scopes) for result in results),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--formal-root", type=Path, help="Directory containing the 20 pinned checkouts"
    )
    parser.add_argument("--corpus", type=Path, default=Path("tests/acceptance/corpus-v1.json"))
    parser.add_argument(
        "--matrix", type=Path, help="Reviewed expected-fact matrix for formal scoring"
    )
    parser.add_argument(
        "--candidate-output", type=Path, help="Write an unreviewed candidate fact matrix"
    )
    parser.add_argument(
        "--supplemental-root",
        type=Path,
        help="Directory containing the 71 public breadth checkouts",
    )
    parser.add_argument(
        "--report", required=True, type=Path, help="Write a sanitized report outside the repository"
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    if options.formal_root is None and options.supplemental_root is None:
        raise CorpusError("select --formal-root, --supplemental-root, or both")
    if options.matrix is not None and options.formal_root is None:
        raise CorpusError("--matrix requires --formal-root")
    if options.candidate_output is not None and options.formal_root is None:
        raise CorpusError("--candidate-output requires --formal-root")
    report: dict[str, Any] = {}
    if options.formal_root is not None:
        report["formal"] = _formal_measurement(
            options.formal_root.resolve(),
            _load_corpus(options.corpus),
            options.matrix,
            options.candidate_output,
        )
    if options.supplemental_root is not None:
        report["supplemental"] = _supplemental_measurement(options.supplemental_root.resolve())
    _write_json(options.report, report)
    return 0 if all(item["passes"] for item in report.values() if item["mode"] == "formal") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceError, CorpusError) as error:
        raise SystemExit(f"acceptance measurement failed: {error}") from error
