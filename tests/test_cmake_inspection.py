"""CMake source declarations, scope, safety, and canonical interface integration."""

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
from slygentify._scan.contracts import DetectionContext, DetectionResult
from slygentify._scan.detectors._ci import inspect_commands
from slygentify._scan.detectors._cmake_syntax import CMakeSyntaxError, commands
from slygentify._scan.detectors.cmake import detect_cmake
from slygentify._scan.kernel import _RepositoryView
from tests.scan_views import InMemoryDetectorView


def _write(root: Path, path: str, text: str) -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def _root(tmp_path: Path, source: str = "project(example LANGUAGES C CXX)\n") -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    _write(root, "CMakeLists.txt", source)
    return root


def _detect(files: dict[str, bytes | None]) -> DetectionResult:
    return detect_cmake(InMemoryDetectorView(files), DetectionContext())


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize("languages", ["C", "CXX", "C CXX"])
def test_explicit_languages_add_facet_without_changing_identity(
    tmp_path: Path, languages: str
) -> None:
    root = _root(tmp_path, "project(example)\n")
    before = scan_repository(root)
    _write(root, "CMakeLists.txt", f"project(example LANGUAGES {languages})\n")
    result = scan_repository(root)
    assert result.components[0].id == before.components[0].id
    assert result.components[0].ecosystems == ("cmake", "generic")
    assert any(
        f.code == "cmake.language.declaration" and languages.split()[0] in f.summary
        for f in result.findings
    )
    assert dump_scan_json(result) == dump_scan_json(scan_repository(root))
    assert load_scan_json(dump_scan_json(result)) == result


@pytest.mark.verifies("TST058")
def test_target_standards_and_header_only_source_are_distinct(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        """project(headers LANGUAGES NONE)
cmake_minimum_required(VERSION 3.25...4.0)
set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD "17" CACHE STRING "selection")
set_target_properties(alpha beta PROPERTIES C_STANDARD 99 CXX_STANDARD 20 OTHER value)
set_property(TARGET gamma PROPERTY CXX_STANDARD 23)
target_compile_features(headers INTERFACE cxx_std_17 c_std_11 other_feature)
""",
    )
    result = scan_repository(root)
    standards = [f for f in result.findings if f.code == "cmake.standard.declaration"]
    assert len(standards) == 7
    assert any('"alpha","beta"' in f.summary and '"20"' in f.summary for f in standards)
    assert any('"gamma"' in f.summary and '"23"' in f.summary for f in standards)
    assert any('"headers"' in f.summary and '"cxx_std_17"' in f.summary for f in standards)
    assert not any("conflict" in d.code for d in result.diagnostics)
    assert result.components[0].ecosystems == ("cmake", "generic")


@pytest.mark.verifies("TST058")
def test_nested_components_ordinary_build_directories_and_mixed_facets(tmp_path: Path) -> None:
    root = _root(
        tmp_path, "project(root CXX)\nadd_subdirectory(lib)\nadd_subdirectory(buildpart)\n"
    )
    _write(root, "lib/CMakeLists.txt", "project(library C)\n")
    _write(root, "buildpart/CMakeLists.txt", "find_package(ZLIB 1.2 REQUIRED COMPONENTS zlib)\n")
    _write(root, "pyproject.toml", '[project]\nname="mixed"\n')
    _write(root, "package.json", '{"name":"mixed"}')
    result = scan_repository(root)
    by_path = {c.path: c for c in result.components}
    assert set(by_path) == {".", "lib"}
    assert by_path["."].ecosystems == ("cmake", "generic", "javascript", "python")
    assert any(
        r.kind == "cmake-subdirectory" and r.target_id == by_path["lib"].id
        for r in result.relationships
    )
    dependency = next(f for f in result.findings if f.code == "cmake.dependency.request")
    assert (
        dependency.subject_id == by_path["."].id and "resolution are unknown" in dependency.summary
    )


@pytest.mark.verifies("TST058")
def test_scope_comments_multiline_brackets_and_deferred_boundaries(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        """# project(fake CXX)
#[=[ project(fake2 C) ]=]
project(
  [=[real]=] VERSION 1.2 DESCRIPTION "C" LANGUAGES CXX
)
if(OPTION)
 find_package(GTest REQUIRED)
 add_subdirectory(child)
else()
 set(CMAKE_CXX_STANDARD 20)
endif()
function(example)
 project(deferred C)
endfunction()
macro(example)
 target_compile_features(foo PUBLIC cxx_std_23)
endmacro()
foreach(item LISTS xs)
 include(Catch)
endforeach()
while(FALSE)
 enable_testing()
endwhile()
block()
 include(CTest)
endblock()
""",
    )
    _write(root, "child/CMakeLists.txt", "if(FALSE)\nproject(no_boundary C)\nendif()\n")
    result = scan_repository(root)
    assert [c.path for c in result.components] == ["."]
    assert not any(r.kind == "cmake-subdirectory" for r in result.relationships)
    assert all("fake" not in f.summary for f in result.findings)
    conditional = [f for f in result.findings if "GTest" in f.summary]
    assert conditional and all("conditional or deferred" in f.summary for f in conditional)
    assert all(f.classification == "verified" for f in conditional)
    assert any(e.locator == "line:3:project" for e in result.evidence)


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize(
    "source",
    ["project(no_defaults)", "project(none LANGUAGES NONE)", "idf_component_register(SRCS main.c)"],
)
def test_no_implicit_c_cpp_and_esp_idf_fallback(tmp_path: Path, source: str) -> None:
    result = scan_repository(_root(tmp_path, source))
    assert result.components[0].ecosystems == ("generic",)


@pytest.mark.verifies("TST058")
def test_dynamic_and_sensitive_declarations_are_unknown(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        """project(real CXX)
set(UNRELATED value)
set(CMAKE_CXX_STANDARD ${VERSION})
find_package(${PACKAGE})
find_package("token=private-value")
include(${MODULE})
include(UnrelatedModule)
set_target_properties(foo PROPERTIES OTHER value)
cmake_minimum_required(VERSION dynamic)
set(CMAKE_C_STANDARD nope)
""",
    )
    result = scan_repository(root)
    assert b"private-value" not in dump_scan_json(result)
    assert any(
        f.classification == "unknown" and f.code == "cmake.declaration.dynamic"
        for f in result.findings
    )
    assert any(f.code == "cmake.declaration.unresolved" for f in result.findings)
    assert any(d.code == "cmake.dynamic-declaration" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize(
    ("source", "literals", "framework"),
    [
        (
            'ortools 9.15.6755 EXACT CONFIG REQUIRED PATHS "${prefix}/share/ortools" NO_DEFAULT_PATH',
            ("ortools", "9.15.6755", "EXACT", "CONFIG", "REQUIRED", "PATHS", "NO_DEFAULT_PATH"),
            False,
        ),
        (
            'nlohmann_json 3.12.0 EXACT CONFIG REQUIRED PATHS "${prefix}/share/nlohmann_json"',
            ("nlohmann_json", "3.12.0", "EXACT", "CONFIG", "REQUIRED"),
            False,
        ),
        ("LLVM ${LLVM_VERSION} REQUIRED CONFIG", ("LLVM", "REQUIRED", "CONFIG"), False),
        ("GTest ${VERSION} REQUIRED", ("GTest", "REQUIRED"), True),
        (
            "Catch2 3 REQUIRED COMPONENTS ${COMPONENTS}",
            ("Catch2", "3", "REQUIRED", "COMPONENTS"),
            True,
        ),
    ],
)
def test_partial_dependency_requests_preserve_literal_fields(
    tmp_path: Path, source: str, literals: tuple[str, ...], framework: bool
) -> None:
    result = scan_repository(
        _root(tmp_path, f"project(real CXX)\nif(OPTION)\nfind_package({source})\nendif()\n")
    )
    requests = [f for f in result.findings if f.code == "cmake.dependency.request"]
    assert len(requests) == 1
    request = requests[0]
    assert request.classification == "verified"
    assert all(f'"{value}"' in request.summary for value in literals)
    assert '"<unresolved>"' in request.summary
    assert "conditional or deferred" in request.summary
    assert "installation and resolution are unknown" in request.summary
    assert b"${" not in dump_scan_json(result)
    assert any(f.code == "cmake.declaration.dynamic" for f in result.findings)
    assert any(d.code == "cmake.dynamic-declaration" for d in result.diagnostics)
    assert any(f.code == "cmake.tool.declaration" for f in result.findings) is framework
    evidence = {e.id: e for e in result.evidence}
    assert all(evidence[e].locator == "line:3:find_package" for e in request.evidence_ids)
    assert load_scan_json(dump_scan_json(result)) == result


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize(
    "source",
    [
        "${PACKAGE} 3 REQUIRED",
        'ortools 9 REQUIRED PATHS "token=private-value" "${prefix}"',
        'ortools 9 REQUIRED PATHS "/private/location" "${prefix}"',
        '"token=private-value" ${VERSION} REQUIRED',
    ],
)
def test_partial_dependency_requests_keep_name_and_value_safety_guards(
    tmp_path: Path, source: str
) -> None:
    result = scan_repository(_root(tmp_path, f"project(real CXX)\nfind_package({source})\n"))
    assert not any(f.code == "cmake.dependency.request" for f in result.findings)
    serialized = dump_scan_json(result)
    assert b"private-value" not in serialized
    assert b"/private/location" not in serialized
    assert any(f.classification == "unknown" for f in result.findings)


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize("name", ["token=private-value", "/private/project", "C:/private/project"])
def test_dynamic_project_identity_is_checked_before_emission(tmp_path: Path, name: str) -> None:
    result = scan_repository(_root(tmp_path, f'project("{name}" LANGUAGES ${{LANGS}})\n'))
    assert name.encode() not in dump_scan_json(result)
    assert not any(f.code == "cmake.identity.declaration" for f in result.findings)
    assert any(f.classification == "unknown" for f in result.findings)


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize(
    ("assignment", "valid"),
    [
        ("17", True),
        ("17 PARENT_SCOPE", True),
        ('17 CACHE STRING "selection"', True),
        ('17 CACHE STRING "selection" FORCE', True),
        ("17 20", False),
        ("17 20 PARENT_SCOPE", False),
        ('17 20 CACHE STRING "selection"', False),
        ("17 CACHE STRING", False),
        ('17 CACHE UNKNOWN "selection"', False),
        ('17 CACHE STRING "selection" OTHER', False),
    ],
)
def test_standard_assignment_requires_one_numeric_value(
    tmp_path: Path, assignment: str, valid: bool
) -> None:
    result = scan_repository(
        _root(tmp_path, f"project(headers LANGUAGES NONE)\nset(CMAKE_CXX_STANDARD {assignment})\n")
    )
    assert any(f.code == "cmake.standard.declaration" for f in result.findings) is valid
    assert ("cmake" in result.components[0].ecosystems) is valid
    if not valid:
        assert any(f.classification == "unknown" for f in result.findings)


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize(
    ("field", "version"), [("include", 3), ("include", 4), ("condition", 2), ("condition", 3)]
)
def test_unresolved_preset_fields_respect_version_availability(
    tmp_path: Path, field: str, version: int
) -> None:
    root = _root(tmp_path)
    preset: dict[str, object] = {"name": "example"}
    document: dict[str, object] = {"version": version, "configurePresets": [preset]}
    if field == "include":
        document[field] = ["shared.json"]
    else:
        preset[field] = None
    _write(root, "CMakePresets.json", json.dumps(document))
    result = scan_repository(root)
    invalid = version < (4 if field == "include" else 3)
    assert any(d.code == "cmake.invalid-presets" for d in result.diagnostics) is invalid
    assert (result.completion == "partial") is invalid
    assert any(f.code == "cmake.preset.unresolved" for f in result.findings)


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize(
    "source",
    [
        "@",
        "project",
        "project name",
        "project(",
        'project("bad)',
        "project([=[bad)",
        "endif()",
        "else()",
        "if(X)\nproject(a C)",
        "project(a\\",
    ],
)
def test_invalid_syntax_does_not_fabricate_boundaries(tmp_path: Path, source: str) -> None:
    result = scan_repository(_root(tmp_path, source))
    assert not result.components
    assert result.completion == "partial"
    assert any(d.code == "cmake.invalid-syntax" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
def test_tokenizer_literal_escapes_and_unresolved_lists() -> None:
    calls = commands(
        'project("escaped\\ name" CXX)\nset(X "a\\nb\\tc\\rd\\\nend\\$x")\nif((A) AND B)\nelseif(C)\nendif()\nset(X a;b)\n# tail',
        lambda: False,
    )
    assert calls[0].arguments[0].value == "escaped name"
    assert calls[1].arguments[1].value == "a\nb\tc\rdend$x"
    assert calls[1].arguments[1].literal
    assert any(not arg.literal for arg in calls[2].arguments)
    assert not calls[-1].arguments[-1].literal
    assert commands(" # comment\n", lambda: False) == ()
    with pytest.raises(CMakeSyntaxError):
        commands('set(X "unterminated', lambda: False)
    with pytest.raises(CMakeSyntaxError):
        commands("set(X unclosed", lambda: False)


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize("reference", ["../outside", "/outside", "missing", "ignored", "linked"])
def test_unsafe_or_unavailable_subdirectories_remain_partial(
    tmp_path: Path, reference: str
) -> None:
    root = _root(tmp_path, f"project(real C)\nadd_subdirectory({reference})\n")
    _write(root, "ignored/CMakeLists.txt", "project(hidden CXX)")
    _write(root, ".gitignore", "ignored/\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside, "CMakeLists.txt", "project(outside CXX)")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    result = scan_repository(root)
    assert len(result.components) == 1
    assert result.completion == "partial"
    assert any(d.code == "cmake.unresolved-subdirectory" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
def test_subdirectory_cycles_are_omitted(tmp_path: Path) -> None:
    root = _root(tmp_path, "project(real C)\nadd_subdirectory(.)\n")
    result = scan_repository(root)
    assert result.completion == "partial"
    assert any(d.code == "cmake.subdirectory-cycle" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
def test_presets_tools_and_testing_declarations(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        "project(real CXX)\nfind_package(Catch2 3 REQUIRED)\ninclude(GoogleTest)\ninclude(CTest)\nenable_testing()\nadd_test(NAME unit COMMAND tests)\n",
    )
    presets = {
        "version": 6,
        "configurePresets": [
            {
                "name": "debug",
                "hidden": True,
                "generator": "Ninja",
                "toolchainFile": "tools/clang.cmake",
            }
        ],
        "buildPresets": [{"name": "debug"}],
        "testPresets": [{"name": "debug"}],
        "workflowPresets": [{"name": "debug"}],
    }
    _write(root, "CMakePresets.json", json.dumps(presets))
    _write(root, "CMakeUserPresets.json", '{"private":"DO_NOT_INSPECT"}')
    _write(root, ".clang-format", "BasedOnStyle: LLVM\n")
    _write(root, ".clang-tidy", "Checks: '*'\n")
    result = scan_repository(root)
    assert len([f for f in result.findings if f.code == "cmake.preset.declaration"]) == 4
    assert len([f for f in result.findings if f.code == "cmake.preset.selection"]) == 2
    assert any("hidden configure preset" in f.summary for f in result.findings)
    assert any(e.locator == "/configurePresets/0/toolchainFile" for e in result.evidence)
    assert all(e.location != "CMakeUserPresets.json" for e in result.evidence)
    assert b"DO_NOT_INSPECT" not in dump_scan_json(result)
    assert {"cmake.tool.declaration", "cmake.tool.configuration", "cmake.dependency.request"} <= {
        f.code for f in result.findings
    }


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize("version", range(1, 13))
def test_each_supported_preset_version(tmp_path: Path, version: int) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "CMakePresets.json",
        json.dumps(
            {"version": version, "configurePresets": [{"name": "literal", "generator": "Ninja"}]}
        ),
    )
    result = scan_repository(root)
    assert any(f.code == "cmake.preset.declaration" for f in result.findings)
    assert not any(d.code == "cmake.invalid-presets" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
@pytest.mark.parametrize(
    "document",
    [
        "[]",
        '{"version":6,"version":6}',
        "{",
        '{"version":true}',
        '{"version":13}',
        '{"version":0}',
        '{"version":1,"buildPresets":[]}',
        '{"version":2,"configurePresets":[{"name":"old","toolchainFile":"x.cmake"}]}',
        '{"version":6,"configurePresets":{}}',
        '{"version":6,"configurePresets":[null,{"name":""},{"name":"a","hidden":"yes"},{"name":"a"},{"name":"a"}]}',
        '{"version":6,"configurePresets":[{"name":"a","generator":false,"toolchainFile":""}]}',
    ],
)
def test_invalid_or_unsupported_presets_are_explicit(tmp_path: Path, document: str) -> None:
    root = _root(tmp_path)
    _write(root, "CMakePresets.json", document)
    result = scan_repository(root)
    assert any(
        d.code in {"cmake.invalid-presets", "cmake.unsupported-presets-version"}
        for d in result.diagnostics
    )


@pytest.mark.verifies("TST058")
def test_unresolved_preset_constructs_retain_direct_declarations(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "CMakePresets.json",
        json.dumps(
            {
                "version": 6,
                "include": ["outside.json"],
                "packagePresets": [],
                "configurePresets": [
                    {
                        "name": "conditional",
                        "inherits": "base",
                        "condition": {"type": "const", "value": False},
                        "generator": "Ninja",
                        "toolchainFile": "${sourceDir}/tools.cmake",
                    },
                    {"name": "unsafe", "toolchainFile": "/private/toolchain.cmake"},
                ],
            }
        ),
    )
    result = scan_repository(root)
    assert any(f.code == "cmake.preset.selection" and "Ninja" in f.summary for f in result.findings)
    assert any(
        f.code == "cmake.preset.unresolved" and f.classification == "unknown"
        for f in result.findings
    )
    assert b"/private/toolchain.cmake" not in dump_scan_json(result)
    assert all(e.location != "outside.json" for e in result.evidence)


@pytest.mark.verifies("TST058")
def test_ci_attribution_redaction_and_supported_platforms(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(root, "child/CMakeLists.txt", "project(child C)\n")
    workflow = """jobs:
  build:
    defaults:
      run:
        working-directory: source/child
    steps:
      - uses: actions/checkout@v4
        with:
          path: source
      - run: cmake --build build
      - run: token=private-secret cmake --build build
      - run: ${{ inputs.command }}
      - uses: actions/checkout@v4
        with:
          path: other
          repository: other/repository
      - run: do-not-attribute
        working-directory: other
"""
    _write(root, ".github/workflows/build.yml", workflow)
    _write(root, ".gitea/workflows/build.yaml", "jobs:\n  build:\n    steps:\n      - run: ctest\n")
    _write(
        root,
        ".gitlab-ci.yml",
        "include:\n  - local: ci/local.yml\n  - remote: https://example.invalid/file\nbuild:\n  script: cmake --preset debug\n",
    )
    _write(root, "ci/local.yml", "test:\n  run:\n    - run: ctest --preset debug\n")
    result = scan_repository(root)
    child = next(c for c in result.components if c.path == "child")
    assert any(
        f.code == "cmake.ci.command" and f.subject_id == child.id and "cmake --build" in f.summary
        for f in result.findings
    )
    assert b"private-secret" not in dump_scan_json(result)
    assert b"do-not-attribute" not in dump_scan_json(result)
    assert any(
        f.code == "cmake.ci.command" and f.classification == "unknown" for f in result.findings
    )
    assert any(d.code == "cmake.external-ci-include" for d in result.diagnostics)
    assert any(e.location == "ci/local.yml" for e in result.evidence)


@pytest.mark.verifies("TST058")
def test_interfaces_provenance_and_doctor_tooling_drift(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "CMakePresets.json",
        '{"version":6,"configurePresets":[{"name":"debug","generator":"Ninja"}]}',
    )
    result = scan_repository(root)
    projection = map_repository(root, sections=["workflows"], max_bytes="unlimited")
    assert any(f.code == "cmake.preset.declaration" for f in projection.findings)
    output = StringIO()
    render_scan_report(result, root, Console(file=output, width=180, color_system=None))
    assert "configure preset" in output.getvalue()
    presentation = ScanPresentation(result, root)
    assert any(
        g.section == "How to work on it"
        and any(getattr(r, "code", "") == "cmake.preset.declaration" for r in g.records)
        for g in presentation.component_groups(result.components[0].id)
    )
    plan = plan_initialization(root)
    state = load_state_json(plan.state_json)
    assert any(i.location == "CMakePresets.json" for i in state.inputs)
    assert any(d.claim_code == "cmake.preset.selection" for d in state.derivations)
    apply_initialization(plan)
    guidance = (root / "AGENTS.md").read_bytes()
    _write(
        root,
        "CMakePresets.json",
        '{"version":6,"configurePresets":[{"name":"debug","generator":"Unix Makefiles"}]}',
    )
    doctor = doctor_repository(root)
    assert any(d.code == "doctor.tooling.drift" for d in doctor.diagnostics)
    assert (root / "AGENTS.md").read_bytes() == guidance


@pytest.mark.verifies("TST058")
def test_partial_sources_and_unsupported_target_forms_are_explicit() -> None:
    result = _detect(
        {
            "unreadable/CMakeLists.txt": None,
            "invalid/CMakeLists.txt": b"\xff",
            "CMakeLists.txt": b"""project(real VERSION 1.2 DESCRIPTION "identity" CXX)
set()
project()
add_library(uninspected source.cpp)
set_property(GLOBAL PROPERTY CXX_STANDARD 23)
set_property(TARGET foo APPEND PROPERTY CXX_STANDARD 23)
set_property(TARGET PROPERTY CXX_STANDARD 23)
set_property(TARGET foo PROPERTY CXX_STANDARD)
set_property(TARGET foo PROPERTY CXX_STANDARD 23 C_STANDARD 11)
set_target_properties(foo CXX_STANDARD 20)
set_target_properties(foo PROPERTIES CXX_STANDARD nope)
target_compile_features(foo cxx_std_23 other)
target_compile_features(foo PUBLIC other)
target_compile_features(foo PUBLIC c_std_11 PRIVATE cxx_std_23)
include()
find_package()
project(literal LANGUAGES ${DYNAMIC})
""",
            "CMakePresets.json": None,
            ".clang-format": None,
            "unowned/.clang-tidy": b"Checks: '*'",
        }
    )
    assert any(
        f.code == "cmake.identity.declaration" and "literal" in f.summary for f in result.findings
    )
    assert any(f.code == "cmake.standard.unresolved" for f in result.findings)
    assert any('"PRIVATE"' in f.summary and '"cxx_std_23"' in f.summary for f in result.findings)
    assert any(d.code == "cmake.invalid-syntax" and d.partial for d in result.diagnostics)
    assert any(d.code == "cmake.unsupported-standard" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
def test_subdirectories_without_project_owners_and_repeated_references() -> None:
    result = _detect(
        {
            "CMakeLists.txt": b"add_subdirectory(build)\nadd_subdirectory(build)\n",
            "build/CMakeLists.txt": b"add_subdirectory(deep)\n",
            "build/deep/CMakeLists.txt": b"find_package(ZLIB)\n",
        }
    )
    assert not result.components and not result.relationships
    assert any(
        f.code == "cmake.dependency.request" and f.subject_path is None for f in result.findings
    )


@pytest.mark.verifies("TST058")
def test_ci_malformed_scopes_includes_and_noncommands(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(root, ".github/workflows/invalid.yml", "[]")
    _write(root, ".gitea/workflows/invalid.yaml", "jobs: [")
    _write(root, ".github/workflows/no-jobs.yml", "jobs: []")
    _write(root, ".github/workflows/skipped.txt", "uninspected")
    _write(
        root,
        ".github/workflows/shapes.yml",
        """jobs:
  ignored: 1
  no_steps: {}
  build:
    steps:
      - 1
      - run: echo root
      - uses: actions/checkout@v4
        with:
          path: ${{ inputs.path }}
      - run: echo unknown
      - uses: actions/checkout@v4
        with:
          path: dynamic
          repository: ${{ inputs.repository }}
      - run: echo dynamic
        working-directory: dynamic
""",
    )
    _write(
        root,
        ".gitlab-ci.yml",
        """include:
  - local: ci/cycle.yml
  - local: ../escape.yml
  - local: missing.yml
  - null
  - local: ci/invalid.yml
  - ""
  - {}
before_script: echo before
after_script:
  - {run: echo after}
  - {unrecognized: value}
  - 4
.template: {script: hidden}
variables: {KEY: value}
job:
  script: [echo job]
""",
    )
    _write(root, "ci/cycle.yml", "include: .gitlab-ci.yml\n")
    _write(root, "ci/invalid.yml", "[]")
    result = scan_repository(root)
    assert {
        "cmake.invalid-ci-workflow",
        "cmake.ci-scope-unresolved",
        "cmake.ci-include-cycle",
        "cmake.invalid-ci-include",
    } <= {d.code for d in result.diagnostics}
    assert any("echo before" in f.summary for f in result.findings)
    assert any("echo after" in f.summary for f in result.findings)
    assert not any("echo unknown" in f.summary for f in result.findings)


@pytest.mark.verifies("TST058")
def test_ci_include_depth_bound_and_unreadable_workflows() -> None:
    files: dict[str, bytes | None] = {
        "CMakeLists.txt": b"project(real CXX)",
        ".github/workflows/unreadable.yml": None,
        ".gitlab-ci.yml": b"include: ci/0.yml",
    }
    files.update(
        {f"ci/{index}.yml": f"include: ci/{index + 1}.yml".encode() for index in range(18)}
    )
    result = _detect(files)
    assert any(d.code == "cmake.ci-include-depth" and d.partial for d in result.diagnostics)
    files[".gitlab-ci.yml"] = None
    assert not any(f.code == "cmake.ci.command" for f in _detect(files).findings)


@pytest.mark.verifies("TST058")
def test_nested_python_workflows_are_not_assigned_to_parent_cmake(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(root, "python/pyproject.toml", '[project]\nname="python"\n')
    _write(
        root,
        ".github/workflows/build.yml",
        "jobs:\n  test:\n    steps:\n      - run: pytest\n        working-directory: python\n",
    )
    result = scan_repository(root)
    assert any(f.code == "python.ci.command" for f in result.findings)
    assert not any(f.code == "cmake.ci.command" for f in result.findings)


@pytest.mark.verifies("TST058")
def test_configured_child_boundary_blocks_parent_ci_attribution(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "declared").mkdir()
    _write(
        root,
        "slygentify.toml",
        'schema_version=1\n[[scan.components]]\npath="declared"\necosystem="other"\n',
    )
    _write(
        root,
        ".github/workflows/build.yml",
        "jobs:\n  test:\n    steps:\n      - run: declared-tool\n        working-directory: declared\n",
    )
    result = scan_repository(root)
    assert any(c.path == "declared" for c in result.components)
    assert not any(f.code == "cmake.ci.command" for f in result.findings)


@pytest.mark.verifies("TST058")
def test_sensitive_preset_names_and_selection_values_are_withheld(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "CMakePresets.json",
        json.dumps(
            {
                "version": 6,
                "configurePresets": [
                    {"name": "token=private-name"},
                    {"name": "literal", "generator": "token=private-generator"},
                ],
            }
        ),
    )
    data = dump_scan_json(scan_repository(root))
    assert b"private-name" not in data and b"private-generator" not in data


@pytest.mark.verifies("TST058")
def test_host_specific_declaration_values_are_withheld(tmp_path: Path) -> None:
    root = _root(tmp_path, "project(real CXX)\nfind_package(ZLIB PATHS /private/package)\n")
    _write(
        root,
        "CMakePresets.json",
        '{"version":6,"configurePresets":[{"name":"a","generator":"/private/generator"}]}',
    )
    result = scan_repository(root)
    assert b"/private/" not in dump_scan_json(result)
    assert any(d.code == "cmake.unsafe-declaration" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
def test_tracked_ignored_presets_are_retained_and_git_unavailability_is_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _write(root, ".gitignore", "CMakePresets.json\n")
    _write(root, "CMakePresets.json", '{"version":6,"configurePresets":[{"name":"committed"}]}')
    assert not any(f.code == "cmake.preset.declaration" for f in scan_repository(root).findings)
    monkeypatch.setattr(
        orchestration,
        "_discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset({b"CMakePresets.json"}), frozenset(), True),
    )
    assert any(f.code == "cmake.preset.declaration" for f in scan_repository(root).findings)
    monkeypatch.setattr(
        orchestration,
        "_discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset(), frozenset(), False),
    )
    result = scan_repository(root)
    assert result.completion == "partial"
    assert any(d.code == "inspection.git-tracked-paths-unavailable" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
def test_cmake_resources_and_effect_guards_preserve_human_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    _write(
        root,
        "CMakePresets.json",
        '{"version":6,"configurePresets":[{"name":"' + "x" * 1024 + '"}]}',
    )
    _write(root, "slygentify.toml", "schema_version=1\n[scan.limits]\nmax_file_bytes=256\n")
    _write(root, "AGENTS.md", "Human guidance.\n")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Inspection must not execute project commands or access networking")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = scan_repository(root)
    assert result.completion == "partial"
    assert any(s.reason == "max_file_bytes" for s in result.skipped_scopes)
    plan = plan_initialization(root)
    assert not plan.can_apply
    assert (root / "AGENTS.md").read_text() == "Human guidance.\n"


@pytest.mark.verifies("TST058")
def test_component_language_drift_is_visible_to_doctor(tmp_path: Path) -> None:
    root = _root(tmp_path)
    apply_initialization(plan_initialization(root))
    _write(root, "CMakeLists.txt", "project(example LANGUAGES C)\n")
    result = doctor_repository(root)
    assert any(d.code == "doctor.component.drift" for d in result.diagnostics)


@pytest.mark.verifies("TST058")
def test_checkpoints_retain_deterministic_evidence_closed_prefixes() -> None:
    class BudgetView(_RepositoryView):
        def __init__(self, files: dict[str, bytes], budget: int) -> None:
            super().__init__(files)
            self.budget = budget

        def checkpoint(self) -> bool:
            self.budget -= 1
            return self.budget < 0

    files = {
        "CMakeLists.txt": b"project(real CXX)\nif(X)\nfind_package(GTest)\nendif()\n",
        "CMakePresets.json": b'{"version":6,"configurePresets":[{"name":"a"},{"name":"b"}]}',
        ".clang-format": b"BasedOnStyle: LLVM",
        ".github/workflows/build.yml": b"jobs:\n  build:\n    steps:\n      - run: cmake --build build\n",
        ".gitlab-ci.yml": b"job:\n  script: ctest",
    }
    for budget in range(110):
        first = detect_cmake(BudgetView(files, budget), DetectionContext())
        second = detect_cmake(BudgetView(files, budget), DetectionContext())
        assert first == second
        keys = {(e.source_kind, e.location, e.locator, e.semantic_key) for e in first.evidence}
        assert all(set(f.evidence_keys) <= keys for f in first.findings)
        assert all(set(c.evidence_keys) <= keys for c in first.components)
    assert commands("if(X)\nendif()", lambda: True) == ()
    for budget in range(12):
        counter = iter([False] * budget + [True] * 100)
        calls = commands("if(X)\nendif()", counter.__next__)
        assert all(call.name in {"if", "endif"} for call in calls)
    for budget in range(25):
        assert isinstance(
            list(inspect_commands(BudgetView(files, budget), lambda *args: None)), list
        )


@pytest.mark.verifies("TST058")
def test_long_tokens_and_whitespace_honor_shared_deadline() -> None:
    for source in (" " * 8192, 'project("' + "a" * 8192 + '" CXX)'):
        assert isinstance(commands(source, lambda: False), tuple)
        checks = iter([False, False, True])
        assert commands(source, checks.__next__) == ()
