"""Canonical interfaces, provenance and containment for MSBuild and Make."""

from __future__ import annotations

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
from slygentify._scan import orchestration


def _write(root: Path, path: str, content: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    return root


@pytest.mark.verifies("TST060")
def test_canonical_interfaces_mixed_facets_dependency_ownership_and_provenance(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "app/a.vcxproj",
        '<Project><PropertyGroup><ProjectName>Example</ProjectName><PlatformToolset>v143</PlatformToolset></PropertyGroup><ItemDefinitionGroup><ClCompile><LanguageStandard>stdcpp20</LanguageStandard></ClCompile></ItemDefinitionGroup><Import Project="$(unresolved)"/></Project>',
    )
    _write(
        root,
        "app/b.vcxproj",
        "<Project><PropertyGroup><ProjectName>Other</ProjectName></PropertyGroup></Project>",
    )
    _write(
        root,
        "a.slnx",
        '<Solution><Folder Name="/logical/"><Project Path="app/a.vcxproj"/><Project Path="app/b.vcxproj"/></Folder></Solution>',
    )
    _write(root, "app/pyproject.toml", '[project]\nname="example"\nversion="1"\n')
    _write(root, "app/package.json", '{"name":"example","version":"1.0.0"}')
    _write(root, "app/CMakeLists.txt", "project(example LANGUAGES CXX)\n")
    _write(root, "app/vcpkg.json", '{"dependencies":["fmt"]}')
    _write(root, "app/Makefile", "all:\n\t@false\n")
    result = scan_repository(root)
    assert dump_scan_json(result) == dump_scan_json(scan_repository(root))
    assert load_scan_json(dump_scan_json(result)) == result
    app = next(c for c in result.components if c.path == "app")
    assert {"msbuild", "python", "javascript", "cmake"} <= set(app.ecosystems)
    assert len([c for c in result.components if c.path == "app"]) == 1
    assert all(f.subject_id == app.id for f in result.findings if f.code.startswith("vcpkg."))
    assert any(d.code == "msbuild.project-attribution-unresolved" for d in result.diagnostics)
    for section, codes in (
        (
            "orientation",
            {
                "msbuild.identity.declaration",
                "msbuild.toolset.declaration",
                "msbuild.standard.declaration",
            },
        ),
        ("architecture", {"make.tool.evidence"}),
        ("boundaries", {"msbuild.declaration.unresolved", "make.declaration.unresolved"}),
    ):
        projection = map_repository(root, scope="app", sections=[section], max_bytes="unlimited")
        assert codes <= {f.code for f in projection.findings}
        assert all(
            set(f.evidence_ids) <= {e.id for e in projection.evidence} for f in projection.findings
        )
    presentation = ScanPresentation(result, root)
    assert any(
        g.section == "Architecture"
        and any(getattr(r, "code", "") == "make.tool.evidence" for r in g.records)
        for g in presentation.component_groups(app.id)
    )
    output = StringIO()
    render_scan_report(result, root, Console(file=output, width=180, color_system=None))
    assert "stdcpp20" in output.getvalue()
    plan = plan_initialization(root)
    apply_initialization(plan)
    agents = (root / "AGENTS.md").read_bytes()
    _write(
        root,
        "app/a.vcxproj",
        "<Project><PropertyGroup><PlatformToolset>v142</PlatformToolset></PropertyGroup></Project>",
    )
    doctor = doctor_repository(root)
    assert any(d.code == "doctor.tooling.drift" for d in doctor.diagnostics)
    assert (root / "AGENTS.md").read_bytes() == agents


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("configured", [False, True])
def test_make_boundary_requires_independent_evidence(tmp_path: Path, configured: bool) -> None:
    root = _root(tmp_path)
    _write(root, "app/Makefile", "include ../external.mk\nall:\n\ttouch SENTINEL\n")
    if configured:
        _write(
            root,
            "slygentify.toml",
            'schema_version=1\n[[scan.components]]\npath="app"\necosystem="custom"\n',
        )
    result = scan_repository(root)
    assert bool(result.components) == configured
    assert not any("make" in c.ecosystems for c in result.components)
    assert not (root / "SENTINEL").exists()


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize(
    "name,content",
    [
        ("a.vcxproj", "<Project/>"),
        ("a.slnx", '<Solution><Project Path="a.vcxproj"/></Solution>'),
        ("Makefile", "all:\n\tfalse\n"),
    ],
)
def test_ignored_and_tracked_build_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, content: str
) -> None:
    root = _root(tmp_path)
    _write(root, name, content)
    _write(root, ".gitignore", name + "\n")
    assert not any(e.location == name for e in scan_repository(root).evidence)
    monkeypatch.setattr(
        orchestration,
        "_discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset({name.encode()}), frozenset(), True),
    )
    assert any(e.location == name for e in scan_repository(root).evidence)


@pytest.mark.verifies("TST060")
def test_links_and_effect_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path)
    outside = tmp_path / "outside.vcxproj"
    outside.write_text(
        "<Project><PropertyGroup><ProjectName>OUTSIDESECRET</ProjectName></PropertyGroup></Project>"
    )
    (root / "linked.vcxproj").symlink_to(outside)
    _write(
        root,
        "a.slnx",
        '<Solution><Project Path="linked.vcxproj"/><Project Path="../outside.vcxproj"/></Solution>',
    )
    _write(root, "Makefile", "X=$(shell touch SENTINEL)\ninclude /etc/passwd\n")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("inspection attempted execution or network access")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(
        orchestration,
        "_discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset(), frozenset(), True),
    )
    result = scan_repository(root)
    assert b"OUTSIDESECRET" not in dump_scan_json(result)
    assert not result.components
    assert not (root / "SENTINEL").exists()


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("limit", ["max_file_bytes=32", "max_memory_bytes=15000"])
def test_resource_limited_scan_retains_partial_boundary(tmp_path: Path, limit: str) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "a.vcxproj",
        "<Project><PropertyGroup><ProjectName>"
        + "x" * 200
        + "</ProjectName></PropertyGroup></Project>",
    )
    _write(root, "slygentify.toml", "schema_version=1\n[scan.limits]\n" + limit + "\n")
    result = scan_repository(root)
    assert result.completion == "partial"
    assert result.skipped_scopes
