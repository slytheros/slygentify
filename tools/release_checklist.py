#!/usr/bin/env python3
"""Run the versioned, human-gated post-1.0 release checklist.

The command deliberately has no operation that mutates GitHub, Git refs, package
indexes, or release environments.  Its only writes are canonical evidence files in
the caller-selected external directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slygentify.traceability import implements
from tools.release import release_version_from_tag

SCHEMA_VERSION = 1
DEFINITION_VERSION = 1
PHASES = (
    "preflight",
    "formal-corpus",
    "initialization-review",
    "supplemental-corpus",
    "scaling",
    "package",
    "gitflow",
    "testpypi",
    "pypi",
    "github-release",
)
NETWORK_PHASES = frozenset({"testpypi", "pypi", "github-release"})
GATES = {
    "formal-corpus": "semantic-corpus",
    "initialization-review": "initialization-usefulness",
    "gitflow": "promotion-and-tag",
    "testpypi": "testpypi-environment",
    "pypi": "production-go-no-go",
    "github-release": "github-release-publication",
}


class ChecklistError(RuntimeError):
    """The requested release checklist state is unsafe or incomplete."""


@dataclass(frozen=True)
class Inputs:
    version: str
    tag: str
    freeze_commit: str
    source_date_epoch: int
    formal_root: Path
    supplemental_root: Path
    composed_root: Path | None
    github_issue: int | None

    def public(self) -> dict[str, object]:
        """Return portable identity values without local paths."""
        return {
            "definition_version": DEFINITION_VERSION,
            "freeze_commit": self.freeze_commit,
            "source_date_epoch": self.source_date_epoch,
            "tag": self.tag,
            "version": self.version,
        }


def _canonical(document: object) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _digest(document: object) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def _write(path: Path, document: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(document)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, check=False, text=True
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "Git failed").strip()
        raise ChecklistError(f"Git inspection failed: {detail}")
    return completed.stdout.strip()


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _safe_external(path: Path, roots: Sequence[Path]) -> Path:
    resolved = path.resolve(strict=False)
    if any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise ChecklistError("evidence output must be outside the repository and corpus roots")
    return resolved


def _parse_commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ChecklistError("--freeze-commit must be a lowercase 40-character Git commit")
    return value


def _load_state(path: Path, inputs: Inputs) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "inputs": inputs.public(), "phases": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ChecklistError(f"could not load checklist state: {error}") from error
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise ChecklistError("checklist state has an unsupported schema")
    if state.get("inputs") != inputs.public() or not isinstance(state.get("phases"), dict):
        raise ChecklistError("checklist state does not match the immutable release inputs")
    return state


def _require_predecessors(state: dict[str, Any], phase: str) -> None:
    index = PHASES.index(phase)
    missing = [name for name in PHASES[:index] if name not in state["phases"]]
    if missing:
        raise ChecklistError(
            f"phase {phase!r} cannot skip unmet prerequisites: {', '.join(missing)}"
        )
    unverified = [
        name
        for name in PHASES[:index]
        if name in GATES and state["phases"][name].get("gate_verified") is not True
    ]
    if unverified:
        raise ChecklistError(
            "a human gate must be verified before continuing: " + ", ".join(unverified)
        )


def _gate_packet(inputs: Inputs, phase: str, evidence_digest: str) -> dict[str, object]:
    gate = GATES[phase]
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": gate,
        "phase": phase,
        "packet_digest": evidence_digest,
        "decision": f"Approve or reject {gate.replace('-', ' ')}.",
        "acceptance": "The phase passed and its evidence digest matches this packet.",
        "rejection": "Record rejection with a reason; correct the release candidate or evidence, then rerun.",
        "consequence": "Approval permits only the next checklist phase; it does not merge, tag, publish, or approve an environment.",
        "resume": f"python -m tools.release_checklist --resume --phase {phase} --tag {inputs.tag}",
    }


def _run_command(command: list[str], *, dry_run: bool) -> dict[str, object]:
    if dry_run:
        return {"command": command, "status": "planned"}
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode:
        raise ChecklistError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return {"status": "passed"}


def _gate_comment(gate: str, digest: str) -> str:
    return f"release-checklist gate={gate} packet_digest={digest} decision=approved"


@implements("REQ056")
def verify_human_gate(inputs: Inputs, evidence_directory: Path, phase: str) -> dict[str, object]:
    """Read a maintainer's GitHub approval record without changing remote state."""
    if phase not in GATES:
        raise ChecklistError(f"phase {phase!r} does not have a human gate")
    if inputs.github_issue is None or inputs.github_issue <= 0:
        raise ChecklistError("--github-issue is required to verify a human gate")
    root = _repository_root()
    evidence = _safe_external(
        evidence_directory, (root, inputs.formal_root, inputs.supplemental_root)
    )
    state_path = evidence / "release-checklist-state.json"
    state = _load_state(state_path, inputs)
    completed = state["phases"].get(phase)
    if not isinstance(completed, dict) or not isinstance(completed.get("digest"), str):
        raise ChecklistError(f"phase {phase!r} has no evidence to approve")
    expected = _gate_comment(GATES[phase], completed["digest"])
    command = ["gh", "issue", "view", str(inputs.github_issue), "--json", "comments"]
    result = subprocess.run(command, capture_output=True, check=False, text=True)
    if result.returncode:
        raise ChecklistError("could not read the GitHub release issue")
    try:
        document = json.loads(result.stdout)
        comments = document["comments"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ChecklistError("GitHub release issue returned invalid comments") from error
    if not isinstance(comments, list) or not any(
        isinstance(comment, dict) and expected in comment.get("body", "") for comment in comments
    ):
        raise ChecklistError(
            "GitHub issue does not contain the required approval record: " + expected
        )
    completed["gate_verified"] = True
    _write(state_path, state)
    return {"phase": phase, "gate": GATES[phase], "status": "approved"}


def _phase_commands(inputs: Inputs, phase: str, evidence: Path) -> list[list[str]]:
    python = sys.executable
    if phase == "preflight":
        return [
            ["uv", "run", "pytest"],
            ["uv", "run", "ruff", "check", "."],
            ["uv", "run", "mypy"],
            ["uv", "run", "doorstop", "-e"],
            ["uv", "run", "--locked", "slygentify", "doctor", "."],
            ["uv", "run", "--locked", "mkdocs", "build", "--strict"],
            ["uv", "run", "pre-commit", "run", "--all-files"],
            ["uv", "build", "--no-sources"],
        ]
    if phase == "formal-corpus":
        return [
            [
                python,
                "-m",
                "tools.measure_acceptance",
                "--formal-root",
                str(inputs.formal_root),
                "--matrix",
                "tests/acceptance/expected-facts-v1.json",
                "--report",
                str(evidence / "formal-report.json"),
            ]
        ]
    if phase == "initialization-review":
        return [
            [
                python,
                "-m",
                "tools.review_initialization",
                "--formal-root",
                str(inputs.formal_root),
                "--reviewed-matrix",
                "tests/acceptance/initialization-review-v1.json",
                "--report",
                str(evidence / "initialization-report.json"),
            ]
        ]
    if phase == "supplemental-corpus":
        return [
            [
                python,
                "-m",
                "tools.measure_acceptance",
                "--supplemental-root",
                str(inputs.supplemental_root),
                "--report",
                str(evidence / "supplemental-report.json"),
            ]
        ]
    if phase == "scaling":
        if inputs.composed_root is None:
            raise ChecklistError("--composed-root is required for the scaling phase")
        return [
            [
                python,
                "tools/benchmark_scan_scaling.py",
                "--isolated-root",
                str(inputs.supplemental_root),
                "--composed-root",
                str(inputs.composed_root),
                "--report",
                str(evidence / "scaling-report.json"),
            ]
        ]
    if phase == "package":
        return [
            ["uv", "build", "--no-sources"],
            [python, "-m", "tools.release", "version", inputs.tag],
        ]
    if phase == "gitflow":
        return []
    return []


def _verify_gitflow(root: Path, inputs: Inputs) -> dict[str, object]:
    if _git(root, "rev-parse", "HEAD") != inputs.freeze_commit:
        raise ChecklistError("checkout HEAD does not match --freeze-commit")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ChecklistError("release checkout is not clean")
    if int(_git(root, "show", "-s", "--format=%ct", "HEAD")) != inputs.source_date_epoch:
        raise ChecklistError("SOURCE_DATE_EPOCH does not match the frozen commit")
    return {"head": inputs.freeze_commit, "status": "passed"}


@implements("REQ056")
def run_checklist(
    inputs: Inputs, evidence_directory: Path, phase: str, *, dry_run: bool, allow_network: bool
) -> dict[str, object]:
    """Execute one ordered phase and emit portable, canonical evidence."""
    if phase not in PHASES:
        raise ChecklistError(f"unknown phase {phase!r}")
    if phase in NETWORK_PHASES and not allow_network:
        raise ChecklistError(f"phase {phase!r} requires --allow-network")
    root = _repository_root()
    evidence = _safe_external(
        evidence_directory, (root, inputs.formal_root, inputs.supplemental_root)
    )
    state_path = evidence / "release-checklist-state.json"
    state = _load_state(state_path, inputs)
    _require_predecessors(state, phase)
    plan = _phase_commands(inputs, phase, evidence)
    if dry_run:
        return {
            "phase": phase,
            "effects": {
                "commands": plan,
                "network": phase in NETWORK_PHASES,
                "writes": ["release-checklist-state.json", f"{phase}.json"],
                "human_gate": GATES.get(phase),
            },
            "status": "planned",
        }
    results = [_run_command(command, dry_run=False) for command in plan]
    if phase in {"preflight", "gitflow"}:
        results.append(_verify_gitflow(root, inputs))
    record: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "inputs": inputs.public(),
        "results": results,
    }
    digest = _write(evidence / f"{phase}.json", record)
    stored = state["phases"].get(phase)
    if stored is not None and stored.get("digest") != digest:
        raise ChecklistError(
            f"completed phase {phase!r} is not reproducible; evidence digest changed"
        )
    state["phases"][phase] = {"digest": digest}
    _write(state_path, state)
    packet = _gate_packet(inputs, phase, digest) if phase in GATES else None
    if packet is not None:
        _write(evidence / f"{phase}-review-packet.json", packet)
    return {"phase": phase, "digest": digest, "human_gate": packet, "status": "passed"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--freeze-commit", required=True)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    parser.add_argument("--formal-root", required=True, type=Path)
    parser.add_argument("--supplemental-root", required=True, type=Path)
    parser.add_argument("--evidence-directory", required=True, type=Path)
    parser.add_argument("--composed-root", type=Path)
    parser.add_argument("--github-issue", type=int)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify-gate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    return parser


@implements("REQ056")
def main(arguments: Sequence[str] | None = None) -> int:
    """Run exactly one fail-closed release-checklist phase."""
    options = _parser().parse_args(arguments)
    expected = release_version_from_tag(options.tag)
    if options.version != expected:
        raise ChecklistError("--version does not match the canonical release tag")
    if options.source_date_epoch <= 0:
        raise ChecklistError("--source-date-epoch must be positive")
    inputs = Inputs(
        options.version,
        options.tag,
        _parse_commit(options.freeze_commit),
        options.source_date_epoch,
        options.formal_root.resolve(),
        options.supplemental_root.resolve(),
        options.composed_root.resolve() if options.composed_root else None,
        options.github_issue,
    )
    if options.verify_gate:
        if not options.allow_network:
            raise ChecklistError("--verify-gate requires --allow-network")
        print(
            json.dumps(
                verify_human_gate(inputs, options.evidence_directory, options.phase), sort_keys=True
            )
        )
        return 0
    print(
        json.dumps(
            run_checklist(
                inputs,
                options.evidence_directory,
                options.phase,
                dry_run=options.dry_run,
                allow_network=options.allow_network,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ChecklistError as error:
        raise SystemExit(f"release checklist failed: {error}") from error
