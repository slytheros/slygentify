#!/usr/bin/env python3
"""Measure isolated and composed scan performance without retaining source content."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TypedDict

from slygentify import dump_scan_json, scan_repository

_EXPECTED_COMPOSED_COMMIT = "54c71895fed1c4582cf46f39532c31add55deb0e"


class BenchmarkError(RuntimeError):
    """A selected local performance corpus is unsuitable for measurement."""


class _Measurement(TypedDict):
    wall_seconds: float
    cpu_seconds: float
    json_sha256: str
    completion: str
    components: int
    findings: int
    diagnostics: int
    skipped_scopes: int


class _IsolatedMeasurement(TypedDict):
    wall_seconds: float
    cpu_seconds: float
    repositories: int
    complete: int
    partial: int
    components: int
    findings: int
    diagnostics: int
    skipped_scopes: int


def _git(path: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise BenchmarkError(f"could not inspect {path.name} with Git") from error
    if completed.returncode != 0:
        raise BenchmarkError(f"Git could not inspect {path.name}")
    return completed.stdout.strip()


def _manifest(composed_root: Path) -> tuple[tuple[str, str], ...]:
    try:
        document = json.loads((composed_root / "mega-repo.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkError("could not read the composed repository manifest") from error
    repositories = document.get("repositories") if isinstance(document, dict) else None
    if not isinstance(repositories, list) or len(repositories) != 71:
        raise BenchmarkError("composed repository manifest must contain exactly 71 repositories")
    result: list[tuple[str, str]] = []
    for item in repositories:
        if not isinstance(item, dict):
            raise BenchmarkError("composed repository manifest contains an invalid entry")
        name = item.get("path")
        commit = item.get("commit")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(commit, str)
            or len(commit) != 40
        ):
            raise BenchmarkError(
                "composed repository manifest contains an invalid repository identity"
            )
        result.append((name, commit))
    if len({name for name, _ in result}) != len(result):
        raise BenchmarkError("composed repository manifest contains duplicate repository paths")
    return tuple(result)


def _verify_corpus(
    isolated_root: Path, composed_root: Path, manifest: tuple[tuple[str, str], ...]
) -> tuple[Path, ...]:
    if not (composed_root / ".git").is_dir():
        raise BenchmarkError("composed root is not a Git repository")
    if _git(composed_root, "rev-parse", "HEAD") != _EXPECTED_COMPOSED_COMMIT:
        raise BenchmarkError("composed repository is not at the required issue-70 commit")
    if _git(composed_root, "status", "--porcelain=v1", "--untracked-files=no"):
        raise BenchmarkError("composed repository has tracked changes")
    checkouts: list[Path] = []
    for name, commit in manifest:
        checkout = isolated_root / name
        if not (checkout / ".git").exists():
            raise BenchmarkError(f"isolated checkout is missing: {name}")
        if _git(checkout, "rev-parse", "HEAD") != commit:
            raise BenchmarkError(
                f"isolated checkout is not at the composed manifest commit: {name}"
            )
        if _git(checkout, "status", "--porcelain=v1", "--untracked-files=no"):
            raise BenchmarkError(f"isolated checkout has tracked changes: {name}")
        checkouts.append(checkout)
    return tuple(checkouts)


def _measurement(path: Path) -> _Measurement:
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    result = scan_repository(path)
    payload = dump_scan_json(result)
    return {
        "wall_seconds": time.perf_counter() - started_wall,
        "cpu_seconds": time.process_time() - started_cpu,
        "json_sha256": hashlib.sha256(payload).hexdigest(),
        "completion": result.completion,
        "components": len(result.components),
        "findings": len(result.findings),
        "diagnostics": len(result.diagnostics),
        "skipped_scopes": len(result.skipped_scopes),
    }


def _isolated_measurement(checkouts: tuple[Path, ...]) -> _IsolatedMeasurement:
    records = tuple(_measurement(checkout) for checkout in checkouts)
    return {
        "wall_seconds": sum(float(record["wall_seconds"]) for record in records),
        "cpu_seconds": sum(float(record["cpu_seconds"]) for record in records),
        "repositories": len(records),
        "complete": sum(record["completion"] == "complete" for record in records),
        "partial": sum(record["completion"] == "partial" for record in records),
        "components": sum(int(record["components"]) for record in records),
        "findings": sum(int(record["findings"]) for record in records),
        "diagnostics": sum(int(record["diagnostics"]) for record in records),
        "skipped_scopes": sum(int(record["skipped_scopes"]) for record in records),
    }


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _run_trials(
    checkouts: tuple[Path, ...], composed_root: Path, trials: int
) -> tuple[list[_IsolatedMeasurement], list[_Measurement]]:
    isolated: list[_IsolatedMeasurement] = []
    composed: list[_Measurement] = []
    for index in range(trials):
        if index % 2 == 0:
            isolated.append(_isolated_measurement(checkouts))
            composed.append(_measurement(composed_root))
        else:
            composed.append(_measurement(composed_root))
            isolated.append(_isolated_measurement(checkouts))
    return isolated, composed


def _report(
    composed_root: Path,
    manifest: tuple[tuple[str, str], ...],
    isolated: list[_IsolatedMeasurement],
    composed: list[_Measurement],
) -> dict[str, object]:
    isolated_median = _median([record["wall_seconds"] for record in isolated])
    composed_median = _median([record["wall_seconds"] for record in composed])
    ratio = composed_median / isolated_median if isolated_median else float("inf")
    return {
        "schema_version": 1,
        "benchmark": "composed-repository-scan-scaling",
        "python_version": sys.version.split()[0],
        "composed_commit": _git(composed_root, "rev-parse", "HEAD"),
        "repository_count": len(manifest),
        "isolated_trials": isolated,
        "composed_trials": composed,
        "isolated_median_wall_seconds": isolated_median,
        "composed_median_wall_seconds": composed_median,
        "composed_to_isolated_wall_ratio": ratio,
        "passes": ratio <= 2.0,
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-root", required=True, type=Path)
    parser.add_argument("--composed-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--trials", type=int, default=3)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    if options.trials < 1 or options.trials % 2 == 0:
        raise BenchmarkError("--trials must be a positive odd integer")
    isolated_root = options.isolated_root.resolve()
    composed_root = options.composed_root.resolve()
    manifest = _manifest(composed_root)
    checkouts = _verify_corpus(isolated_root, composed_root, manifest)

    _isolated_measurement(checkouts)
    _measurement(composed_root)
    isolated, composed = _run_trials(checkouts, composed_root, options.trials)
    report = _report(composed_root, manifest, isolated, composed)
    _write_report(options.report, report)
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as error:
        raise SystemExit(f"benchmark failed: {error}") from error
