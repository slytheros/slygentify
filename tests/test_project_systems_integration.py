"""Static build boundaries through canonical APIs and bounded repository access."""

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
    doctor_repository,
    dump_scan_json,
    load_scan_json,
    map_repository,
    plan_initialization,
    scan_repository,
)
from slygentify._git_tracking import _TrackedPaths
from slygentify._presentation import ScanPresentation, render_scan_report
from slygentify._scan import kernel, orchestration


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.mark.verifies("TST062")
def test_mixed_canonical_interfaces_ownership_and_provenance(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "app/meson.build",
        "project('meson', ['c','cpp'], version: '1')\nsubproject('missing')",
    )
    _write(root, "app/configure.ac", "AC_INIT([autotools], [1])\nAC_PROG_CC")
    _write(root, "app/Makefile.am", "app_SOURCES = main.cpp")
    _write(root, "app/MODULE.bazel", 'module(name="bazel", version="1")')
    _write(root, "app/BUILD", 'cc_library(name="app")')
    _write(root, "app/pyproject.toml", '[project]\nname="example"\nversion="1"')
    _write(root, "app/package.json", '{"name":"example","version":"1"}')
    _write(root, "app/CMakeLists.txt", "project(example LANGUAGES CXX)")
    _write(root, "app/Makefile", "all:\n\tfalse")
    _write(root, "app/vcpkg.json", '{"dependencies":["fmt"]}')
    result = scan_repository(root)
    app = next(c for c in result.components if c.path == "app")
    assert {"meson", "autotools", "bazel", "python", "javascript", "cmake"} <= set(app.ecosystems)
    assert len(result.components) == 1
    assert dump_scan_json(result) == dump_scan_json(scan_repository(root))
    assert load_scan_json(dump_scan_json(result)) == result
    for section, codes in [
        (
            "orientation",
            {
                "meson.identity.declaration",
                "autotools.identity.declaration",
                "bazel.identity.declaration",
                "autotools.language.declaration",
                "meson.language.declaration",
                "bazel.language.declaration",
            },
        ),
        ("architecture", {"make.tool.evidence", "vcpkg.dependency.declaration"}),
        ("boundaries", {"meson.declaration.unresolved"}),
    ]:
        projection = map_repository(
            root, scope="app/planned.cpp", sections=[section], max_bytes="unlimited"
        )
        assert projection.scope.matched_component_id == app.id
        assert codes <= {f.code for f in projection.findings}
        assert all(
            set(f.evidence_ids) <= {e.id for e in projection.evidence} for f in projection.findings
        )
    presentation = ScanPresentation(result, root)
    assert any(
        getattr(r, "code", "") == "meson.identity.declaration"
        for group in presentation.component_groups(app.id)
        for r in group.records
    )
    output = StringIO()
    render_scan_report(result, root, Console(file=output, width=180, color_system=None))
    assert all(name in output.getvalue() for name in ("Meson", "Autoconf", "Bazel"))
    apply_initialization(plan_initialization(root))
    agents = (root / "AGENTS.md").read_bytes()
    state = (root / ".slygentify/state.json").read_text()
    assert all(
        name in state
        for name in ("meson.build", "configure.ac", "Makefile.am", "MODULE.bazel", "BUILD")
    )
    _write(root, "app/meson.build", "project('changed', 'c', version: '2')\nsubproject('missing')")
    doctor = doctor_repository(root)
    assert {"doctor.component.drift", "doctor.tooling.drift", "doctor.path.missing"} <= {
        d.code for d in doctor.diagnostics
    }
    assert (root / "AGENTS.md").read_bytes() == agents


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "name,source",
    [
        ("meson.build", "project('app','cpp')"),
        ("configure.ac", "AC_INIT([app],[1])\nAC_PROG_CXX"),
        ("Makefile.am", "app_SOURCES = main.cpp"),
        ("MODULE.bazel", 'module(name="app")'),
        ("REPO.bazel", ""),
        ("WORKSPACE", ""),
        ("WORKSPACE.bazel", ""),
        ("BUILD", ""),
        ("BUILD.bazel", ""),
    ],
)
def test_ignored_and_tracked_static_build_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, source: str
) -> None:
    root = _root(tmp_path)
    _write(root, name, source)
    _write(root, ".gitignore", name + "\n")
    assert not any(e.location == name for e in scan_repository(root).evidence)
    monkeypatch.setattr(
        orchestration,
        "_discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset({name.encode()}), frozenset(), True),
    )
    assert any(e.location == name for e in scan_repository(root).evidence)


@pytest.mark.verifies("TST062")
def test_no_execution_network_or_unbounded_source_macro_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "meson.build",
        "project('app', 'c')\nrun_command('touch', 'SENTINEL')\nsubproject('../outside')\nsubdir('linked')",
    )
    _write(
        root,
        "configure.ac",
        "AC_INIT([app],[1])\nm4_esyscmd([touch SENTINEL])\ncase $x in\n *) touch SENTINEL ;;\nesac\nAC_CONFIG_SUBDIRS([.ssh blocked])",
    )
    _write(
        root,
        "Makefile.am",
        "app_SOURCES = src/main.cpp\ninclude external.am\nall:\n\t@touch SENTINEL",
    )
    _write(root, "MODULE.bazel", 'module(name="app")\nbazel_dep(name="external", version="1")')
    _write(root, "BUILD", 'load("//:external.bzl", "cc_library")\ncc_library(name="app")')
    _write(root, "external.bzl", 'raise RuntimeError("DO_NOT_READ_MACRO")')
    _write(root, "src/main.cpp", "DO_NOT_READ_SOURCE")
    _write(root, "configure", "DO_NOT_READ_GENERATED")
    _write(root, "aclocal.m4", "DO_NOT_READ_MACRO")
    _write(root, "Makefile.in", "DO_NOT_READ_GENERATED")
    _write(root, ".ssh/configure.ac", "AC_INIT([SECRET_BOUNDARY],[1])")
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside, "meson.build", "project('SECRET_BOUNDARY')")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    _write(root, "blocked/configure.ac", "AC_INIT([SECRET_BOUNDARY],[1])")
    _write(root, ".gitignore", "blocked/\n")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("inspection attempted execution or network access")

    original_read = kernel._read_file

    def bounded_read(root: Path, entry: kernel._Entry, limits: kernel._Limits) -> bytes:
        assert entry.path not in {
            "external.bzl",
            "src/main.cpp",
            "configure",
            "aclocal.m4",
            "Makefile.in",
        }
        return original_read(root, entry, limits)

    monkeypatch.setattr(kernel, "_read_file", bounded_read)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(
        orchestration,
        "_discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset(), frozenset(), True),
    )
    result = scan_repository(root)
    assert not result.relationships
    data = dump_scan_json(result)
    assert all(value not in data for value in (b"DO_NOT_READ", b"SECRET_BOUNDARY"))
    assert not any(e.location in {"external.bzl", "src/main.cpp"} for e in result.evidence)
    assert not (root / "SENTINEL").exists()


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "limit", ["max_file_bytes=32", "max_memory_bytes=15000", "max_entries=2", "max_depth=1"]
)
def test_resource_limited_partial_results(tmp_path: Path, limit: str) -> None:
    root = _root(tmp_path)
    _write(root, "meson.build", "project('" + "x" * 200 + "', 'cpp')")
    _write(root, "nested/child/configure.ac", "AC_INIT([child],[1])")
    _write(root, "slygentify.toml", "schema_version=1\n[scan.limits]\n" + limit + "\n")
    result = scan_repository(root)
    assert result.completion == "partial"
    assert result.skipped_scopes


@pytest.mark.verifies("TST062")
def test_shell_case_retains_later_literal_membership_in_scan_and_map(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "configure.ac",
        """AC_INIT([parent],[1])
case "$host_os" in
 mingw* | windows* | cygwin*) enabled=yes ;;
 *) enabled=no ;;
esac
AC_CONFIG_SUBDIRS([libasprintf])
AC_PROG_CXX
""",
    )
    _write(root, "libasprintf/configure.ac", "AC_INIT([libasprintf],[1])\n")
    result = scan_repository(root)
    assert result.completion == "complete"
    names = {c.id: c.path for c in result.components}
    assert any(
        r.kind == "workspace-member"
        and names[r.source_id] == "."
        and names[r.target_id] == "libasprintf"
        for r in result.relationships
    )
    projection = map_repository(root, max_bytes="unlimited")
    assert any(r.kind == "workspace-member" for r in projection.relationships)
    assert any(
        f.code == "autotools.language.declaration" and "AC_PROG_CXX" in f.summary
        for f in projection.findings
    )
    assert dump_scan_json(result) == dump_scan_json(scan_repository(root))


@pytest.mark.verifies("TST062")
def test_repetitive_unsupported_syntax_preserves_default_orientation_and_provenance(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _write(root, "meson.build", "project('example','c',version:'1')\n" + "x=1\n" * 100)
    fragments = {f"fragment-{i}/meson.build" for i in range(8)}
    for path in fragments:
        _write(root, path, "x=1\n" * 50)
    result = scan_repository(root)
    assert len(result.components) == 1
    assert len(result.diagnostics) == 1
    assert len([f for f in result.findings if f.classification == "unknown"]) == 1
    assert fragments <= {e.location for e in result.evidence}
    projection = map_repository(root)
    codes = {f.code for f in projection.findings}
    assert {
        "meson.identity.declaration",
        "meson.version.declaration",
        "meson.language.declaration",
        "meson.declaration.unresolved",
    } <= codes
    assert not projection.omissions
    apply_initialization(plan_initialization(root))
    state = json.loads((root / ".slygentify/state.json").read_text())
    assert fragments <= {entry["location"] for entry in state["inputs"]}
