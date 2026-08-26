"""Realistic lifecycle, interface, portability, and effect tests for static doctor."""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import slygentify._scan.orchestration as scan
import slygentify.cli as cli
from slygentify import (
    DoctorInputError,
    DoctorOperationalError,
    DoctorResult,
    apply_initialization,
    doctor_repository,
    dump_doctor_json,
    load_doctor_json,
    plan_initialization,
    validate_doctor,
)
from slygentify._doctor_presentation import render_doctor_report
from slygentify._git_tracking import _TrackedPaths
from slygentify.cli import app


@dataclass(frozen=True)
class ManagedRepository:
    root: Path
    manifest: Path
    member_manifest: Path
    lockfile: Path
    workflow: Path
    original_files: dict[str, bytes]


_DIAGNOSTIC_CONTRACT = {
    "doctor.artifact.diverged": ("warning", "unknown"),
    "doctor.artifact.missing": ("error", "verified"),
    "doctor.artifact.stale": ("warning", "verified"),
    "doctor.command.unverifiable": ("warning", "unknown"),
    "doctor.component.drift": ("warning", "verified"),
    "doctor.configuration.invalid": ("error", "verified"),
    "doctor.evidence.missing": ("warning", "unknown"),
    "doctor.guidance.unmanaged": ("info", "unknown"),
    "doctor.inspection.partial": ("warning", "unknown"),
    "doctor.path.missing": ("warning", "verified"),
    "doctor.state.invalid": ("error", "verified"),
    "doctor.state.stale": ("info", "verified"),
    "doctor.tooling.drift": ("warning", "verified"),
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _managed_repository(tmp_path: Path, ecosystem: str = "python") -> ManagedRepository:
    root = tmp_path / f"{ecosystem}-repository"
    root.mkdir()
    (root / ".git").mkdir()
    workflow = root / ".gitea" / "workflows" / "checks.yml"

    if ecosystem == "python":
        manifest = root / "pyproject.toml"
        member = root / "packages" / "api" / "pyproject.toml"
        lockfile = root / "uv.lock"
        _write(
            manifest,
            """[project]
name = "doctor-root"
requires-python = ">=3.11"

[tool.uv.workspace]
members = ["packages/api"]

[tool.ruff]
line-length = 100
""",
        )
        _write(member, '[project]\nname = "doctor-api"\n')
        _write(lockfile, "version = 1\n")
        _write(
            workflow,
            """jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - run: uv run pytest
""",
        )
    elif ecosystem == "javascript":
        manifest = root / "package.json"
        member = root / "packages" / "web" / "package.json"
        lockfile = root / "pnpm-lock.yaml"
        _write(
            manifest,
            json.dumps(
                {
                    "name": "doctor-root",
                    "private": True,
                    "packageManager": "pnpm@9.0.0",
                    "workspaces": ["packages/web"],
                    "scripts": {"test": "vitest run"},
                },
                separators=(",", ":"),
            )
            + "\n",
        )
        _write(member, '{"name":"@doctor/web","version":"1.0.0"}\n')
        _write(lockfile, "lockfileVersion: '9.0'\n")
        _write(
            workflow,
            """jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - run: pnpm test
""",
        )
    else:
        raise AssertionError(f"unsupported fixture ecosystem: {ecosystem}")

    plan = plan_initialization(root)
    assert plan.can_apply
    applied = apply_initialization(plan)
    assert set(applied.changed_locations) == {"AGENTS.md", ".slygentify/state.json"}
    originals = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in (
            manifest,
            member,
            lockfile,
            workflow,
            root / "AGENTS.md",
            root / ".slygentify/state.json",
        )
    }
    return ManagedRepository(root, manifest, member, lockfile, workflow, originals)


def _restore(repository: ManagedRepository, *locations: str) -> None:
    for location in locations:
        target = repository.root.joinpath(*location.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(repository.original_files[location])


def _codes(result: DoctorResult) -> set[str]:
    return {diagnostic.code for diagnostic in result.diagnostics}


def _assert_contract(result: DoctorResult) -> None:
    evidence_ids = {item.id for item in result.evidence}
    for diagnostic in result.diagnostics:
        assert diagnostic.code in _DIAGNOSTIC_CONTRACT
        assert (diagnostic.severity, diagnostic.classification) == _DIAGNOSTIC_CONTRACT[
            diagnostic.code
        ]
        assert diagnostic.problem
        assert diagnostic.effect
        assert diagnostic.evidence_ids
        assert set(diagnostic.evidence_ids) <= evidence_ids
        if diagnostic.location is not None:
            assert "\\" not in diagnostic.location
            assert not Path(diagnostic.location).is_absolute()


def _assert_clean(
    repository: ManagedRepository, expected: DoctorResult | None = None
) -> DoctorResult:
    first = doctor_repository(repository.root)
    second = doctor_repository(
        repository.root / repository.member_manifest.parent.relative_to(repository.root)
    )
    assert first == second
    assert first.completion == "complete"
    assert first.diagnostics == ()
    assert load_doctor_json(dump_doctor_json(first)) == first
    if expected is not None:
        assert first == expected
    return first


def _snapshot(root: Path) -> tuple[tuple[str, str, bytes | str], ...]:
    entries: list[tuple[str, str, bytes | str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            entries.append((relative, "link", os.readlink(path)))
        elif stat.S_ISREG(metadata.st_mode):
            entries.append((relative, "file", path.read_bytes()))
        else:
            entries.append((relative, "directory", b""))
    return tuple(entries)


def _render(result: DoctorResult, root: Path) -> str:
    stream = StringIO()
    render_doctor_report(
        result,
        root,
        Console(file=stream, color_system=None, _environ={}, width=160),
        verbose=True,
    )
    return stream.getvalue()


@pytest.mark.verifies("TST050")
@pytest.mark.parametrize("ecosystem", ["python", "javascript"])
def test_clean_manifest_staleness_and_recovery_are_deterministic(
    tmp_path: Path, ecosystem: str
) -> None:
    repository = _managed_repository(tmp_path, ecosystem)
    clean = _assert_clean(repository)

    if ecosystem == "python":
        repository.manifest.write_bytes(repository.manifest.read_bytes() + b"\n# formatting only\n")
    else:
        document = json.loads(repository.manifest.read_text(encoding="utf-8"))
        repository.manifest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    stale = doctor_repository(repository.root)
    _assert_contract(stale)
    assert stale.completion == "complete"
    assert _codes(stale) == {"doctor.state.stale"}
    assert dump_doctor_json(stale) == dump_doctor_json(doctor_repository(repository.root))

    _restore(repository, repository.manifest.relative_to(repository.root).as_posix())
    _assert_clean(repository, clean)


@pytest.mark.verifies("TST050")
def test_unmanaged_guidance_controls_and_public_recovery(tmp_path: Path) -> None:
    root = tmp_path / "unmanaged-repository"
    root.mkdir()
    (root / ".git").mkdir()
    _write(root / "pyproject.toml", '[project]\nname = "unmanaged"\n')

    absent = doctor_repository(root)
    _assert_contract(absent)
    assert _codes(absent) == {"doctor.guidance.unmanaged"}
    assert absent.completion == "complete"
    assert not (root / "AGENTS.md").exists()
    assert not (root / ".slygentify").exists()

    _write(root / "AGENTS.md", "human-owned guidance\n")
    before = (root / "AGENTS.md").read_bytes()
    human = doctor_repository(root)
    _assert_contract(human)
    assert _codes(human) == {"doctor.guidance.unmanaged"}
    assert (root / "AGENTS.md").read_bytes() == before

    plan = plan_initialization(root, replace=True)
    assert plan.can_apply
    apply_initialization(plan)
    assert doctor_repository(root).diagnostics == ()


@pytest.mark.verifies("TST050")
@pytest.mark.parametrize(
    "configuration",
    ["not = [valid", "schema_version = 2\n"],
)
def test_invalid_configuration_stops_safely_and_recovers(
    tmp_path: Path, configuration: str
) -> None:
    repository = _managed_repository(tmp_path)
    clean = _assert_clean(repository)
    target = repository.root / "slygentify.toml"
    target.write_text(configuration, encoding="utf-8")
    before = _snapshot(repository.root)

    result = doctor_repository(repository.root)

    _assert_contract(result)
    assert result.completion == "partial"
    assert _codes(result) == {"doctor.configuration.invalid"}
    assert result.skipped_scopes == ()
    assert _snapshot(repository.root) == before
    target.unlink()
    _assert_clean(repository, clean)


@pytest.mark.verifies("TST050")
@pytest.mark.parametrize("state_kind", ["malformed", "unknown-schema"])
def test_invalid_state_continues_safe_inspection_without_ownership_noise(
    tmp_path: Path, state_kind: str
) -> None:
    repository = _managed_repository(tmp_path)
    clean = _assert_clean(repository)
    target = repository.root / ".slygentify" / "state.json"
    if state_kind == "malformed":
        target.write_bytes(b"{}")
    else:
        document = json.loads(target.read_bytes())
        document["schema_version"] = 2
        target.write_text(json.dumps(document), encoding="utf-8")
    agents_before = (repository.root / "AGENTS.md").read_bytes()

    result = doctor_repository(repository.root)

    _assert_contract(result)
    assert result.completion == "partial"
    assert _codes(result) == {"doctor.state.invalid"}
    assert "doctor.guidance.unmanaged" not in _codes(result)
    assert (repository.root / "AGENTS.md").read_bytes() == agents_before
    _restore(repository, ".slygentify/state.json")
    _assert_clean(repository, clean)


@pytest.mark.verifies("TST050")
@pytest.mark.parametrize("ecosystem", ["python", "javascript"])
def test_removed_component_path_and_evidence_are_specific_and_recoverable(
    tmp_path: Path, ecosystem: str
) -> None:
    repository = _managed_repository(tmp_path, ecosystem)
    clean = _assert_clean(repository)
    member_location = repository.member_manifest.relative_to(repository.root).as_posix()
    repository.member_manifest.unlink()

    result = doctor_repository(repository.root)

    _assert_contract(result)
    codes = _codes(result)
    assert {"doctor.component.drift", "doctor.evidence.missing", "doctor.path.missing"} <= codes
    assert "doctor.state.stale" not in codes
    assert "doctor.artifact.diverged" not in codes
    _restore(repository, member_location)
    _assert_clean(repository, clean)


@pytest.mark.verifies("TST050")
@pytest.mark.parametrize("ecosystem", ["python", "javascript"])
def test_removed_tooling_is_specific_and_unrelated_files_are_clean_controls(
    tmp_path: Path, ecosystem: str
) -> None:
    repository = _managed_repository(tmp_path, ecosystem)
    clean = _assert_clean(repository)
    unrelated = repository.root / "notes.txt"
    unrelated.write_text("not detector input\n", encoding="utf-8")
    assert doctor_repository(repository.root).diagnostics == ()
    unrelated.unlink()

    manifest_location = repository.manifest.relative_to(repository.root).as_posix()
    if ecosystem == "python":
        content = repository.manifest.read_text(encoding="utf-8")
        repository.manifest.write_text(
            content.replace("\n[tool.ruff]\nline-length = 100\n", "\n"), encoding="utf-8"
        )
    else:
        content = json.loads(repository.manifest.read_text(encoding="utf-8"))
        del content["scripts"]
        repository.manifest.write_text(
            json.dumps(content, separators=(",", ":")) + "\n", encoding="utf-8"
        )
    result = doctor_repository(repository.root)

    _assert_contract(result)
    assert "doctor.tooling.drift" in _codes(result)
    assert "doctor.state.stale" not in _codes(result)
    _restore(repository, manifest_location)
    _assert_clean(repository, clean)


@pytest.mark.verifies("TST050")
@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "doctor.artifact.missing"),
        ("diverged", "doctor.artifact.diverged"),
    ],
)
def test_artifact_ambiguity_is_mutually_exclusive_and_recoverable(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    repository = _managed_repository(tmp_path)
    clean = _assert_clean(repository)
    agents = repository.root / "AGENTS.md"
    if mutation == "missing":
        agents.unlink()
    else:
        agents.write_text("valuable human edit\nCANARY-DO-NOT-DISCLOSE\n", encoding="utf-8")
    before = _snapshot(repository.root)

    result = doctor_repository(repository.root)

    _assert_contract(result)
    assert _codes(result) == {expected}
    assert (
        len(
            _codes(result)
            & {"doctor.artifact.missing", "doctor.artifact.stale", "doctor.artifact.diverged"}
        )
        == 1
    )
    assert b"CANARY-DO-NOT-DISCLOSE" not in dump_doctor_json(result)
    assert _snapshot(repository.root) == before
    _restore(repository, "AGENTS.md")
    _assert_clean(repository, clean)


@pytest.mark.verifies("TST050")
def test_semantic_component_change_reports_stale_artifact_without_generic_staleness(
    tmp_path: Path,
) -> None:
    repository = _managed_repository(tmp_path)
    clean = _assert_clean(repository)
    original = repository.manifest.read_text(encoding="utf-8")
    repository.manifest.write_text(
        original.replace(
            'members = ["packages/api"]',
            'members = ["packages/api", "packages/worker"]',
        ),
        encoding="utf-8",
    )
    worker = repository.root / "packages" / "worker" / "pyproject.toml"
    _write(worker, '[project]\nname = "doctor-worker"\n')

    result = doctor_repository(repository.root)

    _assert_contract(result)
    assert "doctor.component.drift" in _codes(result)
    assert "doctor.artifact.stale" in _codes(result)
    assert "doctor.state.stale" not in _codes(result)
    assert "doctor.artifact.diverged" not in _codes(result)
    worker.unlink()
    _restore(repository, "pyproject.toml")
    _assert_clean(repository, clean)


@pytest.mark.verifies("TST050")
def test_bounded_inspection_reports_accounting_and_recovers(tmp_path: Path) -> None:
    repository = _managed_repository(tmp_path)
    clean = _assert_clean(repository)
    configuration = repository.root / "slygentify.toml"
    configuration.write_text(
        "schema_version = 1\n[scan.limits]\nmax_entries = 1\n", encoding="utf-8"
    )

    result = doctor_repository(repository.root)

    _assert_contract(result)
    assert result.completion == "partial"
    assert "doctor.inspection.partial" in _codes(result)
    assert result.skipped_scopes
    assert all(item.reason and item.omitted_scope for item in result.skipped_scopes)
    configuration.unlink()
    _assert_clean(repository, clean)


@pytest.mark.verifies("TST050")
def test_command_uncertainty_requires_prior_attribution_and_recovers(tmp_path: Path) -> None:
    repository = _managed_repository(tmp_path)
    clean = _assert_clean(repository)
    workflow_location = repository.workflow.relative_to(repository.root).as_posix()
    repository.workflow.write_text(
        """jobs:
  checks:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        command: [pytest]
    steps:
      - run: ${{ matrix.command }}
""",
        encoding="utf-8",
    )

    result = doctor_repository(repository.root)

    _assert_contract(result)
    assert "doctor.command.unverifiable" in _codes(result)
    assert "doctor.state.stale" not in _codes(result)
    assert "safe" not in " ".join(item.problem.casefold() for item in result.diagnostics)
    _restore(repository, workflow_location)
    _assert_clean(repository, clean)

    unrelated = repository.root / ".github" / "workflows" / "dynamic.yml"
    _write(unrelated, "jobs:\n  check:\n    steps:\n      - run: ${{ matrix.command }}\n")
    control = doctor_repository(repository.root)
    _assert_contract(control)
    assert "doctor.command.unverifiable" not in _codes(control)
    assert not {item.severity for item in control.diagnostics} & {"warning", "error"}


@pytest.mark.verifies("TST050")
def test_public_python_json_text_cli_streams_and_exits_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _managed_repository(tmp_path)
    clean = doctor_repository(repository.root)
    clean_json = CliRunner().invoke(app, ["doctor", str(repository.root), "--format", "json"])
    assert clean_json.exit_code == 0
    assert clean_json.stdout_bytes == dump_doctor_json(clean)
    assert clean_json.stderr == ""

    (repository.root / "AGENTS.md").write_text("review this edit\n", encoding="utf-8")
    first = doctor_repository(repository.root)
    second = validate_doctor(json.loads(dump_doctor_json(first)))
    assert first == second
    assert dump_doctor_json(first) == dump_doctor_json(doctor_repository(repository.root))

    json_result = CliRunner().invoke(app, ["doctor", str(repository.root), "--format", "json"])
    assert json_result.exit_code == 1
    assert json_result.stdout_bytes == dump_doctor_json(first)
    assert json_result.stderr == ""
    text_result = CliRunner().invoke(app, ["doctor", str(repository.root), "--verbose"])
    assert text_result.exit_code == 1
    assert text_result.stderr == ""
    assert "WARNING UNKNOWN [doctor.artifact.diverged]" in text_result.stdout

    missing = CliRunner().invoke(app, ["doctor", str(tmp_path / "missing")])
    assert missing.exit_code == 2
    assert missing.stdout == ""
    assert "Next:" in missing.stderr

    monkeypatch.setattr(
        cli,
        "doctor_repository",
        lambda *args, **kwargs: (_ for _ in ()).throw(DoctorOperationalError("failure")),
    )
    failed = CliRunner().invoke(app, ["doctor", str(repository.root)])
    assert failed.exit_code == 3
    assert failed.stdout == ""
    assert "Next:" in failed.stderr

    document = json.loads(dump_doctor_json(first))
    document["schema_version"] = 2
    with pytest.raises(DoctorInputError):
        validate_doctor(document)


@pytest.mark.verifies("TST050")
def test_doctor_effect_audit_preserves_content_and_blocks_ambient_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _managed_repository(tmp_path)
    canary = "CANARY-REPOSITORY-SECRET-7E1744"
    _write(repository.root / "setup.py", f'raise RuntimeError("{canary}")\n')
    _write(repository.root / "conftest.py", f'raise RuntimeError("{canary}")\n')
    (repository.root / "AGENTS.md").write_text(f"human edit {canary}\n", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text(canary, encoding="utf-8")
    link = repository.root / "linked-secret.txt"
    with suppress(OSError):
        link.symlink_to(outside)
    before = _snapshot(repository.root)
    lookup_calls = 0

    def tracked_paths(*args: object, **kwargs: object) -> _TrackedPaths:
        nonlocal lookup_calls
        lookup_calls += 1
        return _TrackedPaths(frozenset(), frozenset(), True)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("forbidden ambient effect")

    monkeypatch.setattr(scan, "_discover_tracked_paths", tracked_paths)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)

    result = doctor_repository(repository.root)
    encoded = dump_doctor_json(result)
    rendered = _render(result, repository.root)

    assert lookup_calls == 1
    assert "doctor.artifact.diverged" in _codes(result)
    assert canary not in encoded.decode("utf-8")
    assert canary not in rendered
    assert str(repository.root) not in encoded.decode("utf-8")
    assert _snapshot(repository.root) == before
