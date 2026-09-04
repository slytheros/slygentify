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
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from slygentify.traceability import implements
from tools.release import release_version_from_tag, verify_release_bundle

SCHEMA_VERSION = 1
DEFINITION_VERSION = 1
PHASES = (
    "preflight",
    "formal-corpus",
    "initialization-review",
    "supplemental-corpus",
    "scaling",
    "package",
    "promotion-gate",
    "verify-gitflow",
    "testpypi-gate",
    "verify-testpypi",
    "pypi-gate",
    "verify-pypi",
    "github-release-gate",
    "verify-github-release",
)
NETWORK_PHASES = frozenset({"verify-testpypi", "verify-pypi", "verify-github-release"})
GITHUB_REPOSITORY = "slytheros/slygentify"
RELEASE_MAINTAINER = "slytheros"
RESUME_CONTEXT_FILENAME = ".release-checklist-resume.json"
GATES = {
    "formal-corpus": "semantic-corpus",
    "initialization-review": "initialization-usefulness",
    "promotion-gate": "promotion-and-tag",
    "testpypi-gate": "testpypi-environment",
    "pypi-gate": "production-go-no-go",
    "github-release-gate": "github-release-publication",
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
    promotion_commit: str | None = None

    def public(self) -> dict[str, object]:
        """Return portable identity values without local paths."""
        return {
            "definition_version": DEFINITION_VERSION,
            "composed_root_id": _path_identity(self.composed_root),
            "freeze_commit": self.freeze_commit,
            "promotion_commit": self.promotion_commit,
            "formal_root_id": _path_identity(self.formal_root),
            "github_issue": self.github_issue,
            "source_date_epoch": self.source_date_epoch,
            "supplemental_root_id": _path_identity(self.supplemental_root),
            "tag": self.tag,
            "version": self.version,
        }

    def local_context(self) -> dict[str, object]:
        """Return the private, uncommitted arguments needed for a local resume."""
        return {
            "composed_root": str(self.composed_root) if self.composed_root is not None else None,
            "formal_root": str(self.formal_root),
            "freeze_commit": self.freeze_commit,
            "github_issue": self.github_issue,
            "promotion_commit": self.promotion_commit,
            "source_date_epoch": self.source_date_epoch,
            "supplemental_root": str(self.supplemental_root),
            "tag": self.tag,
            "version": self.version,
        }


def _canonical(document: object) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _digest(document: object) -> str:
    return hashlib.sha256(_canonical(document)).hexdigest()


def _path_identity(path: Path | None) -> str | None:
    """Return a path-sanitized identity derived from a corpus tree's contents."""
    if path is None:
        return None
    digest = hashlib.sha256()
    root = path.resolve()
    for candidate in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(candidate.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(candidate.read_bytes()).digest())
    return digest.hexdigest()


def _write(path: Path, document: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(document)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _write_resume_context(evidence: Path, inputs: Inputs) -> Path:
    """Write private local paths separately from portable release evidence."""
    path = evidence / RESUME_CONTEXT_FILENAME
    _write(path, inputs.local_context())
    with suppress(OSError):
        os.chmod(path, 0o600)
    return path


def _load_resume_context(path: Path) -> Inputs:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ChecklistError(f"could not load private resume context: {error}") from error
    if not isinstance(document, dict):
        raise ChecklistError("private resume context must be an object")
    required = {
        "version",
        "tag",
        "freeze_commit",
        "source_date_epoch",
        "formal_root",
        "supplemental_root",
        "composed_root",
        "github_issue",
        "promotion_commit",
    }
    if set(document) != required or not all(
        isinstance(document.get(field), str)
        for field in ("version", "tag", "freeze_commit", "formal_root", "supplemental_root")
    ):
        raise ChecklistError("private resume context is invalid")
    epoch = document["source_date_epoch"]
    issue = document["github_issue"]
    composed = document["composed_root"]
    promotion = document["promotion_commit"]
    if (
        not isinstance(epoch, int)
        or isinstance(epoch, bool)
        or not isinstance(issue, int | None)
        or not isinstance(composed, str | None)
        or not isinstance(promotion, str | None)
    ):
        raise ChecklistError("private resume context is invalid")
    return Inputs(
        document["version"],
        document["tag"],
        _parse_commit(document["freeze_commit"]),
        epoch,
        Path(document["formal_root"]).resolve(),
        Path(document["supplemental_root"]).resolve(),
        Path(composed).resolve() if composed is not None else None,
        issue,
        _parse_commit(promotion) if promotion is not None else None,
    )


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
    if any(
        resolved == root or resolved.is_relative_to(root) or root.is_relative_to(resolved)
        for root in roots
    ):
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


def _require_predecessors(state: dict[str, Any], phase: str, evidence: Path) -> None:
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
    for name in PHASES[:index]:
        completed = state["phases"][name]
        artifact = evidence / f"{name}.json"
        if not artifact.is_file() or hashlib.sha256(
            artifact.read_bytes()
        ).hexdigest() != completed.get("digest"):
            raise ChecklistError(f"predecessor evidence is stale or missing: {name}")
        try:
            record = json.loads(artifact.read_text(encoding="utf-8"))
            artifacts = record["artifacts"]
        except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
            raise ChecklistError(f"predecessor evidence has invalid artifacts: {name}") from error
        if not isinstance(artifacts, dict) or artifacts != completed.get("artifacts"):
            raise ChecklistError(f"predecessor evidence has invalid artifacts: {name}")
        for filename, digest in artifacts.items():
            candidate = evidence / filename
            if (
                not isinstance(filename, str)
                or Path(filename).is_absolute()
                or ".." in Path(filename).parts
                or not isinstance(digest, str)
                or not candidate.is_file()
                or hashlib.sha256(candidate.read_bytes()).hexdigest() != digest
            ):
                raise ChecklistError(f"predecessor artifact is stale or missing: {name}")


def _gate_packet(
    inputs: Inputs, phase: str, evidence_digest: str, resume_context: Path
) -> dict[str, object]:
    gate = GATES[phase]
    next_phase = PHASES[PHASES.index(phase) + 1] if phase != PHASES[-1] else phase
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": gate,
        "phase": phase,
        "packet_digest": evidence_digest,
        "decision": f"Approve or reject {gate.replace('-', ' ')}.",
        "acceptance": "The phase passed and its evidence digest matches this packet.",
        "rejection": "Record rejection with a reason; correct the release candidate or evidence, then rerun.",
        "consequence": "Approval permits only the next checklist phase; it does not merge, tag, publish, or approve an environment.",
        "resume": (
            "python -m tools.release_checklist --resume "
            f"--resume-context {resume_context} --phase {next_phase} --allow-network"
        ),
    }


def _run_command(
    command: list[str],
    *,
    dry_run: bool,
    environment: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict[str, object]:
    if dry_run:
        return {"command": command, "status": "planned"}
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **environment} if environment is not None else None,
        cwd=cwd,
    )
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
    roots: tuple[Path, ...] = (root, inputs.formal_root, inputs.supplemental_root)
    if inputs.composed_root is not None:
        roots += (inputs.composed_root,)
    evidence = _safe_external(evidence_directory, roots)
    state_path = evidence / "release-checklist-state.json"
    state = _load_state(state_path, inputs)
    completed = state["phases"].get(phase)
    if not isinstance(completed, dict) or not isinstance(completed.get("digest"), str):
        raise ChecklistError(f"phase {phase!r} has no evidence to approve")
    expected = _gate_comment(GATES[phase], completed["digest"])
    command = [
        "gh",
        "issue",
        "view",
        str(inputs.github_issue),
        "--repo",
        GITHUB_REPOSITORY,
        "--json",
        "comments",
    ]
    result = subprocess.run(command, capture_output=True, check=False, text=True)
    if result.returncode:
        raise ChecklistError("could not read the GitHub release issue")
    try:
        document = json.loads(result.stdout)
        comments = document["comments"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ChecklistError("GitHub release issue returned invalid comments") from error
    approval = (
        next(
            (
                comment
                for comment in comments
                if isinstance(comment, dict)
                and expected in comment.get("body", "")
                and isinstance(comment.get("author"), dict)
                and comment["author"].get("login") == RELEASE_MAINTAINER
                and isinstance(comment.get("createdAt"), str)
            ),
            None,
        )
        if isinstance(comments, list)
        else None
    )
    if approval is None:
        raise ChecklistError(
            "GitHub issue does not contain the required approval record: " + expected
        )
    completed["gate_verified"] = True
    completed["gate_verified_at"] = approval["createdAt"]
    _write(state_path, state)
    return {"phase": phase, "gate": GATES[phase], "status": "approved"}


def _phase_commands(inputs: Inputs, phase: str, output: Path) -> list[list[str]]:
    python = sys.executable
    if phase == "preflight":
        return [
            ["uv", "run", "--offline", "--no-sync", "pytest"],
            ["uv", "run", "--offline", "--no-sync", "ruff", "check", "."],
            ["uv", "run", "--offline", "--no-sync", "mypy"],
            ["uv", "run", "--offline", "--no-sync", "doorstop", "-e"],
            ["uv", "run", "--offline", "--no-sync", "slygentify", "doctor", "."],
            ["uv", "run", "--offline", "--no-sync", "mkdocs", "build", "--strict"],
            ["uv", "run", "--offline", "--no-sync", "pre-commit", "run", "--all-files"],
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
                str(output / "formal-report.json"),
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
                str(output / "initialization-report.json"),
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
                str(output / "supplemental-report.json"),
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
                str(output / "scaling-report.json"),
            ]
        ]
    if phase == "package":
        return [
            [
                "uv",
                "build",
                "--no-sources",
                "--offline",
                "--no-build-isolation",
                "--no-create-gitignore",
                "--out-dir",
                str(output / "package-dist"),
            ],
            [
                python,
                "-m",
                "tools.release",
                "prepare",
                "--tag",
                inputs.tag,
                "--dist",
                str(output / "package-dist"),
                "--bundle",
                str(output / "package-bundle"),
            ],
            [
                python,
                "-m",
                "tools.release",
                "verify-bundle",
                "--tag",
                inputs.tag,
                "--bundle",
                str(output / "package-bundle"),
            ],
        ]
    if phase == "verify-gitflow":
        return []
    return []


def _phase_artifacts(phase: str) -> tuple[str, ...]:
    return {
        "formal-corpus": ("formal-report.json",),
        "initialization-review": ("initialization-report.json",),
        "supplemental-corpus": ("supplemental-report.json",),
        "scaling": ("scaling-report.json",),
        "package": ("package-bundle/release-manifest.json", "package-bundle/SHA256SUMS"),
    }.get(phase, ())


def _phase_writes(phase: str) -> tuple[str, ...]:
    """List every known write a non-dry-run phase may make."""
    writes = [
        f"evidence/{RESUME_CONTEXT_FILENAME}",
        "evidence/release-checklist-state.json",
        f"evidence/{phase}.json",
        *(f"evidence/{artifact}" for artifact in _phase_artifacts(phase)),
    ]
    if phase in GATES:
        writes.append(f"evidence/{phase}-review-packet.json")
    if phase == "package":
        writes.extend(("evidence/package-dist/", "evidence/package-bundle/dist/"))
    if phase == "preflight":
        writes.extend(
            (
                "repository/.coverage",
                "repository/.mypy_cache/",
                "repository/.pytest_cache/",
                "repository/.ruff_cache/",
                "repository/site/",
                "tool-managed cache directories outside the repository",
            )
        )
    return tuple(writes)


def _verify_gitflow(root: Path, inputs: Inputs) -> dict[str, object]:
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ChecklistError("release checkout is not clean")
    if (
        int(_git(root, "show", "-s", "--format=%ct", inputs.freeze_commit))
        != inputs.source_date_epoch
    ):
        raise ChecklistError("SOURCE_DATE_EPOCH does not match the frozen commit")
    if _git(root, "cat-file", "-t", f"refs/tags/{inputs.tag}") != "tag":
        raise ChecklistError("release tag must exist and be annotated")
    tagged = _git(root, "rev-list", "-n", "1", inputs.tag)
    if inputs.promotion_commit is None or tagged != inputs.promotion_commit:
        raise ChecklistError("release tag does not dereference to the expected promotion commit")
    frozen = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", inputs.freeze_commit, tagged],
        check=False,
        capture_output=True,
        text=True,
    )
    if frozen.returncode:
        raise ChecklistError("release tag is not derived from the frozen commit")
    for reference, commit in (("origin/main", tagged), ("origin/develop", tagged)):
        completed = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", commit, reference],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise ChecklistError(f"{reference} does not contain the promoted tagged commit")
    return {"tag": inputs.tag, "tagged_commit": tagged, "status": "passed"}


def _verify_frozen_checkout(root: Path, inputs: Inputs) -> dict[str, object]:
    """Bind candidate-building phases to the exact clean frozen checkout."""
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ChecklistError("release checkout is not clean")
    if _git(root, "rev-parse", "HEAD") != inputs.freeze_commit:
        raise ChecklistError("local release checks must run at the frozen commit")
    return {"commit": inputs.freeze_commit, "status": "passed"}


def _verify_hosted_phase(
    root: Path,
    inputs: Inputs,
    phase: str,
    gate_verified_at: str | None,
    evidence: Path | None = None,
) -> dict[str, object]:
    if gate_verified_at is None:
        raise ChecklistError("hosted verification requires a recorded human-gate time")
    try:
        gate_time = datetime.fromisoformat(gate_verified_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ChecklistError("human-gate time is invalid") from error
    if phase == "verify-github-release":
        command = ["gh", "api", f"repos/{GITHUB_REPOSITORY}/releases/tags/{inputs.tag}"]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode:
            raise ChecklistError("GitHub Release is missing for the immutable tag")
        try:
            release = json.loads(completed.stdout)
            assets = release["assets"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ChecklistError("GitHub Release returned invalid release data") from error
        expected = {
            f"slygentify-{inputs.version}-py3-none-any.whl",
            f"slygentify-{inputs.version}.tar.gz",
            "SHA256SUMS",
        }
        names = (
            {item.get("name") for item in assets if isinstance(item, dict)}
            if isinstance(assets, list)
            else set()
        )
        sizes: dict[str, int] = (
            {
                item["name"]: item["size"]
                for item in assets
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("size"), int)
            }
            if isinstance(assets, list)
            else {}
        )
        expected_sizes: dict[str, int] = {}
        expected_digests: dict[str, str] = {}
        if evidence is not None:
            try:
                manifest = json.loads(
                    (evidence / "package-bundle" / "release-manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                expected_sizes = {
                    item["filename"]: item["size"]
                    for item in manifest["artifacts"]
                    if isinstance(item, dict)
                    and isinstance(item.get("filename"), str)
                    and isinstance(item.get("size"), int)
                }
                expected_digests = {
                    item["filename"]: item["sha256"]
                    for item in manifest["artifacts"]
                    if isinstance(item, dict)
                    and isinstance(item.get("filename"), str)
                    and isinstance(item.get("sha256"), str)
                }
                checksums = "".join(
                    f"{item['sha256']}  {item['filename']}\n" for item in manifest["artifacts"]
                )
                expected_digests["SHA256SUMS"] = hashlib.sha256(
                    checksums.encode("utf-8")
                ).hexdigest()
            except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
                raise ChecklistError(
                    "package manifest is unavailable for release-asset validation"
                ) from error
        if (
            not isinstance(release, dict)
            or release.get("tag_name") != inputs.tag
            or release.get("draft") is not False
            or not isinstance(release.get("published_at"), str)
            or not _postdates(release["published_at"], gate_time)
            or names != expected
            or {name: sizes.get(name) for name in expected_sizes} != expected_sizes
            or {
                item.get("name"): item.get("digest", "").removeprefix("sha256:")
                for item in assets
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
            != expected_digests
        ):
            raise ChecklistError("GitHub Release does not expose the expected immutable assets")
        return {"release": inputs.tag, "assets": sorted(expected), "status": "passed"}
    workflow = "Rehearse release on TestPyPI" if phase == "verify-testpypi" else "Release to PyPI"
    command = [
        "gh",
        "run",
        "list",
        "--repo",
        GITHUB_REPOSITORY,
        "--workflow",
        workflow,
        "--branch",
        inputs.tag,
        "--limit",
        "100",
        "--json",
        "databaseId,createdAt,headBranch,headSha,conclusion,status,url",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode:
        raise ChecklistError(f"could not inspect the {workflow} workflow")
    try:
        runs = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ChecklistError(f"{workflow} returned invalid workflow data") from error
    tagged = _git(root, "rev-list", "-n", "1", inputs.tag)
    matching = (
        next(
            (
                run
                for run in runs
                if isinstance(run, dict)
                and run.get("headSha") == tagged
                and run.get("headBranch") == inputs.tag
                and run.get("status") == "completed"
                and run.get("conclusion") == "success"
                and isinstance(run.get("createdAt"), str)
                and (phase == "verify-pypi" or _postdates(run["createdAt"], gate_time))
            ),
            None,
        )
        if isinstance(runs, list)
        else None
    )
    if matching is None:
        raise ChecklistError(f"no successful {workflow} run exists for the immutable tag")
    if phase == "verify-pypi":
        run_id = matching.get("databaseId")
        if not isinstance(run_id, int):
            raise ChecklistError("PyPI workflow run has no stable identifier")
        published = subprocess.run(
            ["gh", "run", "view", str(run_id), "--repo", GITHUB_REPOSITORY, "--json", "jobs"],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            jobs = json.loads(published.stdout)["jobs"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ChecklistError("PyPI workflow jobs are unavailable") from error
        if published.returncode or not any(
            isinstance(job, dict)
            and job.get("name") == "Publish approved files to PyPI"
            and isinstance(job.get("startedAt"), str)
            and _postdates(job["startedAt"], gate_time)
            for job in jobs
        ):
            raise ChecklistError("PyPI publication job did not start after the approval record")
    return {"tag": inputs.tag, "workflow": workflow, "status": "passed"}


def _postdates(value: str, reference: datetime) -> bool:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) >= reference
    except ValueError:
        return False


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
    roots: tuple[Path, ...] = (root, inputs.formal_root, inputs.supplemental_root)
    if inputs.composed_root is not None:
        roots += (inputs.composed_root,)
    evidence = _safe_external(evidence_directory, roots)
    state_path = evidence / "release-checklist-state.json"
    state = _load_state(state_path, inputs)
    _require_predecessors(state, phase, evidence)
    stored = state["phases"].get(phase)
    plan = _phase_commands(inputs, phase, evidence)
    if dry_run:
        return {
            "phase": phase,
            "effects": {
                "commands": plan,
                "network": phase in NETWORK_PHASES,
                "writes": _phase_writes(phase),
                "human_gate": GATES.get(phase),
            },
            "status": "planned",
        }
    with tempfile.TemporaryDirectory(prefix="slygentify-release-checklist-") as temporary:
        output = Path(temporary)
        plan = _phase_commands(inputs, phase, output)
        if phase in PHASES[: PHASES.index("promotion-gate")]:
            checkout = _verify_frozen_checkout(root, inputs)
        else:
            checkout = None
        environment = (
            {"SOURCE_DATE_EPOCH": str(inputs.source_date_epoch)} if phase == "package" else None
        )
        results = [
            _run_command(command, dry_run=False, environment=environment, cwd=root)
            for command in plan
        ]
        if checkout is not None:
            results.append(checkout)
        if phase == "verify-gitflow":
            results.append(_verify_gitflow(root, inputs))
        if phase in NETWORK_PHASES:
            previous = PHASES[PHASES.index(phase) - 1]
            verified_at = state["phases"].get(previous, {}).get("gate_verified_at")
            results.append(_verify_hosted_phase(root, inputs, phase, verified_at, evidence))
        if phase == "package":
            try:
                verify_release_bundle(output / "package-bundle", inputs.tag)
            except ValueError as error:
                raise ChecklistError(
                    f"package bundle failed release validation: {error}"
                ) from error
        artifacts = {
            filename: hashlib.sha256((output / filename).read_bytes()).hexdigest()
            for filename in _phase_artifacts(phase)
            if (output / filename).is_file()
        }
        if set(artifacts) != set(_phase_artifacts(phase)):
            raise ChecklistError(f"phase {phase!r} did not produce its required evidence artifacts")
        record: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "phase": phase,
            "inputs": inputs.public(),
            "artifacts": artifacts,
            "results": results,
        }
        digest = _digest(record)
        if stored is not None:
            if stored.get("digest") != digest:
                raise ChecklistError(
                    f"completed phase {phase!r} is not reproducible; evidence digest changed"
                )
            return {"phase": phase, "digest": digest, "human_gate": None, "status": "passed"}
        for filename in _phase_artifacts(phase):
            source, destination = output / filename, evidence / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        resume_context = _write_resume_context(evidence, inputs)
        _write(evidence / f"{phase}.json", record)
        state["phases"][phase] = {"artifacts": artifacts, "digest": digest}
        _write(state_path, state)
        packet = _gate_packet(inputs, phase, digest, resume_context) if phase in GATES else None
        if packet is not None:
            _write(evidence / f"{phase}-review-packet.json", packet)
        return {"phase": phase, "digest": digest, "human_gate": packet, "status": "passed"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    parser.add_argument("--tag")
    parser.add_argument("--freeze-commit")
    parser.add_argument("--source-date-epoch", type=int)
    parser.add_argument("--formal-root", type=Path)
    parser.add_argument("--supplemental-root", type=Path)
    parser.add_argument("--evidence-directory", type=Path)
    parser.add_argument("--composed-root", type=Path)
    parser.add_argument("--github-issue", type=int)
    parser.add_argument("--promotion-commit")
    parser.add_argument("--resume-context", type=Path)
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
    if options.resume_context is not None:
        if any(
            value is not None
            for value in (
                options.version,
                options.tag,
                options.freeze_commit,
                options.source_date_epoch,
                options.formal_root,
                options.supplemental_root,
                options.evidence_directory,
                options.composed_root,
                options.github_issue,
                options.promotion_commit,
            )
        ):
            raise ChecklistError("--resume-context cannot be combined with immutable input options")
        inputs = _load_resume_context(options.resume_context)
        evidence_directory = options.resume_context.resolve().parent
    else:
        if any(
            value is None
            for value in (
                options.version,
                options.tag,
                options.freeze_commit,
                options.source_date_epoch,
                options.formal_root,
                options.supplemental_root,
                options.evidence_directory,
            )
        ):
            raise ChecklistError("immutable inputs are required without --resume-context")
        assert options.version is not None
        assert options.tag is not None
        assert options.freeze_commit is not None
        assert options.source_date_epoch is not None
        assert options.formal_root is not None
        assert options.supplemental_root is not None
        assert options.evidence_directory is not None
        inputs = Inputs(
            options.version,
            options.tag,
            _parse_commit(options.freeze_commit),
            options.source_date_epoch,
            options.formal_root.resolve(),
            options.supplemental_root.resolve(),
            options.composed_root.resolve() if options.composed_root else None,
            options.github_issue,
            _parse_commit(options.promotion_commit) if options.promotion_commit else None,
        )
        evidence_directory = options.evidence_directory
    expected = release_version_from_tag(inputs.tag)
    if inputs.version != expected or inputs.source_date_epoch <= 0:
        raise ChecklistError("release inputs are not canonical")
    if (
        inputs.composed_root is None
        or inputs.github_issue is None
        or inputs.github_issue <= 0
        or inputs.promotion_commit is None
    ):
        raise ChecklistError(
            "--composed-root, --github-issue, and --promotion-commit are required for a resumable release"
        )
    if options.verify_gate:
        if options.dry_run:
            raise ChecklistError("--verify-gate cannot be combined with --dry-run")
        if not options.allow_network:
            raise ChecklistError("--verify-gate requires --allow-network")
        print(
            json.dumps(verify_human_gate(inputs, evidence_directory, options.phase), sort_keys=True)
        )
        return 0
    if options.resume:
        previous = PHASES[PHASES.index(options.phase) - 1] if options.phase != PHASES[0] else None
        if previous in GATES:
            if options.dry_run:
                raise ChecklistError("--resume --dry-run cannot verify a human gate")
            if not options.allow_network:
                raise ChecklistError("resuming after a human gate requires --allow-network")
            verify_human_gate(inputs, evidence_directory, previous)
    print(
        json.dumps(
            run_checklist(
                inputs,
                evidence_directory,
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
