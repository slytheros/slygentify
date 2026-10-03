"""Canonical interfaces and containment for static C++ dependency manifests."""

from __future__ import annotations

import json
import socket
import subprocess
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from slygentify import (
    apply_initialization,
    dump_scan_json,
    load_scan_json,
    map_repository,
    plan_initialization,
    scan_repository,
)
from slygentify._doctor import doctor_repository
from slygentify._git_tracking import _TrackedPaths
from slygentify._presentation import ScanPresentation, render_scan_report
from slygentify._provenance import load_state_json
from slygentify._scan import orchestration


def _write(root: Path, path: str, source: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")


def _root(tmp_path: Path, *, cmake: bool = True) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    if cmake:
        _write(root, "CMakeLists.txt", "project(example LANGUAGES CXX)\n")
    return root


@pytest.mark.verifies("TST059")
def test_canonical_interfaces_keep_manager_dependency_and_unknown_evidence(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(root, "vcpkg.json", '{"dependencies":["zlib"]}')
    _write(root, "conanfile.txt", "[requires]\nfmt/[>=10 <12]#revision\n")
    _write(root, "conanfile.py", "raise RuntimeError('must never execute')\n")
    result = scan_repository(root)
    assert dump_scan_json(result) == dump_scan_json(scan_repository(root))
    assert load_scan_json(dump_scan_json(result)) == result
    expected = {
        "vcpkg.manager.evidence",
        "conan.manager.evidence",
        "vcpkg.dependency.declaration",
        "conan.dependency.declaration",
    }
    assert expected <= {f.code for f in result.findings}
    unknowns = {
        f.id
        for f in result.findings
        if f.code.startswith("conan.") and f.classification == "unknown"
    }
    assert unknowns
    for section, codes in (
        ("orientation", {"vcpkg.manager.evidence", "conan.manager.evidence"}),
        ("architecture", {"vcpkg.dependency.declaration", "conan.dependency.declaration"}),
    ):
        projection = map_repository(root, sections=[section], max_bytes="unlimited")
        projected = {f.id for f in projection.findings}
        assert {f.id for f in result.findings if f.code in codes} <= projected
        evidence = {e.id for e in projection.evidence}
        assert all(set(f.evidence_ids) <= evidence for f in projection.findings)
    boundaries = map_repository(root, sections=["boundaries"], max_bytes="unlimited")
    assert unknowns <= {f.id for f in boundaries.findings}
    presentation = ScanPresentation(result, root)
    groups = presentation.component_groups(result.components[0].id)
    assert any(
        g.section == "What it is"
        and any(getattr(r, "code", "") == "vcpkg.manager.evidence" for r in g.records)
        for g in groups
    )
    assert any(
        g.section == "Architecture"
        and any(getattr(r, "code", "") == "conan.dependency.declaration" for r in g.records)
        for g in groups
    )
    output = StringIO()
    render_scan_report(result, root, Console(file=output, width=180, color_system=None))
    assert "zlib" in output.getvalue()
    assert "fmt" in output.getvalue()
    assert any(
        e.location == "vcpkg.json" and e.locator == "/dependencies/0" for e in result.evidence
    )
    assert any(e.location == "conanfile.txt" and "2" in (e.locator or "") for e in result.evidence)


@pytest.mark.verifies("TST059")
@pytest.mark.parametrize("child_kind", ["python", "javascript", "configured"])
def test_nearest_component_owns_manifests_without_new_facets(
    tmp_path: Path, child_kind: str
) -> None:
    root = _root(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname="parent"\nversion="1"\n')
    if child_kind == "python":
        _write(root, "child/pyproject.toml", '[project]\nname="child"\nversion="1"\n')
    elif child_kind == "javascript":
        _write(root, "child/package.json", '{"name":"child","version":"1.0.0"}')
    else:
        (root / "child").mkdir()
        _write(
            root,
            "slygentify.toml",
            'schema_version=1\n[[scan.components]]\npath="child"\necosystem="other"\n',
        )
    before = scan_repository(root)
    _write(root, "vcpkg.json", '{"dependencies":["zlib"]}')
    _write(root, "child/auxiliary/conanfile.txt", "[requires]\nfmt/11.0.0\n")
    result = scan_repository(root)
    assert [(c.id, c.path, c.ecosystems) for c in result.components] == [
        (c.id, c.path, c.ecosystems) for c in before.components
    ]
    owners = {c.path: c.id for c in result.components}
    assert all(f.subject_id == owners["."] for f in result.findings if f.code.startswith("vcpkg."))
    assert all(
        f.subject_id == owners["child"] for f in result.findings if f.code.startswith("conan.")
    )
    projection = map_repository(
        root, scope="child", sections=["architecture"], max_bytes="unlimited"
    )
    assert any(f.code == "conan.dependency.declaration" for f in projection.findings)
    assert all(
        f.subject_id == owners["."]
        for f in projection.findings
        if f.code == "vcpkg.dependency.declaration"
    )


@pytest.mark.verifies("TST059")
def test_unowned_manifests_remain_repository_declarations(tmp_path: Path) -> None:
    root = _root(tmp_path, cmake=False)
    _write(root, "vcpkg.json", '{"dependencies":["zlib"]}')
    _write(root, "conanfile.txt", "[requires]\nfmt/11.0.0\n")
    result = scan_repository(root)
    assert not result.components
    declarations = [f for f in result.findings if f.code.endswith("dependency.declaration")]
    assert len(declarations) == 2
    assert all(f.subject_id == result.repository.id for f in declarations)
    assert any(f.classification == "unknown" for f in result.findings)
    assert any(d.disposition == "limitation" for d in result.diagnostics)


@pytest.mark.verifies("TST059")
@pytest.mark.parametrize("filename", ["vcpkg.json", "conanfile.txt", "conanfile.py"])
def test_tracked_ignored_manifests_are_still_inspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    root = _root(tmp_path)
    _write(root, ".gitignore", filename + "\n")
    _write(
        root,
        filename,
        '{"dependencies":["zlib"]}' if filename == "vcpkg.json" else "[requires]\nfmt/11\n",
    )
    assert not any(e.location == filename for e in scan_repository(root).evidence)
    monkeypatch.setattr(
        orchestration,
        "_discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset({filename.encode()}), frozenset(), True),
    )
    assert any(e.location == filename for e in scan_repository(root).evidence)


@pytest.mark.verifies("TST059")
def test_unsafe_links_never_expose_external_dependency_sources(tmp_path: Path) -> None:
    root = _root(tmp_path)
    outside = tmp_path / "external.json"
    outside.write_text('{"dependencies":["outside-secret-package"]}')
    (root / "vcpkg.json").symlink_to(outside)
    (root / "conanfile.txt").symlink_to(outside)
    result = scan_repository(root)
    assert b"outside-secret-package" not in dump_scan_json(result)
    assert not any(f.code.endswith("dependency.declaration") for f in result.findings)
    assert {"vcpkg.json", "conanfile.txt"} <= {s.scope for s in result.skipped_scopes}


@pytest.mark.verifies("TST059")
def test_credential_values_are_withheld_across_scan_map_and_text(tmp_path: Path) -> None:
    root = _root(tmp_path)
    secret = "private-value-do-not-display"
    _write(
        root,
        "vcpkg.json",
        json.dumps(
            {
                "dependencies": [
                    "zlib",
                    {"name": "fmt", "version>=": "TOKEN=" + secret},
                ]
            }
        ),
    )
    _write(root, "conanfile.txt", "[requires]\nzlib/1.3.1\nfmt/TOKEN=" + secret + "\n")
    result = scan_repository(root)
    assert result.completion == "partial"
    assert secret.encode() not in dump_scan_json(result)
    assert any(
        f.code == "vcpkg.dependency.declaration" and "zlib" in f.summary for f in result.findings
    )
    projection = map_repository(
        root, sections=["orientation", "architecture", "boundaries"], max_bytes="unlimited"
    )
    assert not any(secret in f.summary for f in projection.findings)
    assert not any(
        secret in e.observation or secret in (e.locator or "") for e in projection.evidence
    )
    output = StringIO()
    render_scan_report(result, root, Console(file=output, width=180, color_system=None))
    assert secret not in output.getvalue()


@pytest.mark.verifies("TST059")
def test_provenance_and_doctor_detect_dependency_changes_preserving_human_guidance(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _write(root, "vcpkg.json", '{"dependencies":["zlib"]}')
    _write(root, "conanfile.txt", "[requires]\nfmt/10.0.0\n")
    _write(root, "AGENTS.md", "# Human guidance\nKeep this paragraph.\n")
    plan = plan_initialization(root, adopt=True)
    state = load_state_json(plan.state_json)
    assert {"vcpkg.json", "conanfile.txt"} <= {i.location for i in state.inputs}
    assert {"vcpkg.dependency.declaration", "conan.dependency.declaration"} <= {
        d.claim_code for d in state.derivations
    }
    apply_initialization(plan)
    guidance = (root / "AGENTS.md").read_bytes()
    assert guidance.startswith(b"# Human guidance\nKeep this paragraph.\n")
    _write(root, "conanfile.txt", "[requires]\nfmt/11.0.0\n")
    doctor = doctor_repository(root)
    assert any(d.code == "doctor.tooling.drift" for d in doctor.diagnostics)
    assert (root / "AGENTS.md").read_bytes() == guidance
    updated = load_state_json(plan_initialization(root).state_json)
    assert updated.inputs != state.inputs


@pytest.mark.verifies("TST059")
def test_recipes_and_dependency_tools_never_execute_or_access_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _write(root, "vcpkg.json", '{"dependencies":["zlib"]}')
    _write(root, "conanfile.txt", "[requires]\nfmt/11.0.0\n[generators]\nCMakeDeps\n")
    _write(
        root,
        "conanfile.py",
        "from pathlib import Path\nPath('executed').touch()\nraise RuntimeError()\n",
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Static inspection must not execute commands or access networking")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = scan_repository(root)
    assert any(f.code == "conan.dependency.declaration" for f in result.findings)
    assert not (root / "executed").exists()
    assert not any(e.location == "executed" for e in result.evidence)
    assert b"Path('executed')" not in dump_scan_json(result)


@pytest.mark.verifies("TST059")
@pytest.mark.parametrize("filename", ["vcpkg.json", "conanfile.txt", "conanfile.py"])
def test_manifest_file_budget_is_partial_and_preserves_human_guidance(
    tmp_path: Path, filename: str
) -> None:
    root = _root(tmp_path)
    _write(root, filename, json.dumps({"dependencies": ["x" * 1024]}))
    _write(root, "slygentify.toml", "schema_version=1\n[scan.limits]\nmax_file_bytes=256\n")
    _write(root, "AGENTS.md", "Human guidance.\n")
    result = scan_repository(root)
    assert result.completion == "partial"
    assert any(s.scope == filename and s.reason == "max_file_bytes" for s in result.skipped_scopes)
    assert not plan_initialization(root).can_apply
    assert (root / "AGENTS.md").read_text() == "Human guidance.\n"
