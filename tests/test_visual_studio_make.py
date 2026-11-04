"""Static project and build-tool recognition through the bounded detector capability."""

from __future__ import annotations

import pytest

from slygentify._scan.contracts import DetectionContext, DetectionResult
from slygentify._scan.detectors.visual_studio import _Stopped, _xml, detect_msbuild_make
from tests.scan_views import InMemoryDetectorView

_HEADER = "Microsoft Visual Studio Solution File, Format Version 12.00\n"
_GUID = "11111111-1111-1111-1111-111111111111"


def _entry(path: str, kind: str = _GUID) -> str:
    return f'Project("{{{kind}}}") = "example", "{path}", "{{{_GUID}}}"\nEndProject\n'


def _detect(files: dict[str, bytes | None], owners: tuple[str, ...] = ()) -> DetectionResult:
    return detect_msbuild_make(
        InMemoryDetectorView(files), DetectionContext(component_paths=frozenset(owners))
    )


@pytest.mark.verifies("TST060")
def test_project_declarations_conditions_conflicts_and_unique_locators() -> None:
    result = _detect(
        {
            "a.vcxproj": b"""<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
    <PropertyGroup><ProjectName>A</ProjectName><PlatformToolset>v143</PlatformToolset>
    <PlatformToolset>v142</PlatformToolset><PlatformToolset>v142</PlatformToolset></PropertyGroup>
    <ItemDefinitionGroup Condition="unresolved"><ClCompile><LanguageStandard>stdcpp20</LanguageStandard>
    <LanguageStandard_C>stdc17</LanguageStandard_C></ClCompile></ItemDefinitionGroup>
    <Choose><When Condition="x"><PropertyGroup><PlatformToolset>v141</PlatformToolset></PropertyGroup></When></Choose>
    <Target Name="never"><PropertyGroup><ProjectName>deferred</ProjectName></PropertyGroup></Target>
    <Import Project="$(VCTargetsPath)/Microsoft.Cpp.props"/>
    <PropertyGroup><ProjectName>$(dynamic)</ProjectName><ProjectName><Nested/></ProjectName></PropertyGroup>
    <Other><PlatformToolset>ignored</PlatformToolset></Other></Project>"""
        }
    )
    assert result.components[0].ecosystem == "msbuild"
    assert len({e.locator for e in result.evidence}) == len(result.evidence)
    assert sum(d.code.endswith("declaration-conflict") for d in result.diagnostics) == 1
    declarations = [f for f in result.findings if f.code.endswith(".declaration")]
    assert any("stdcpp20" in f.summary and "conditional" in f.summary for f in declarations)
    assert any("deferred" in f.summary and "conditional" in f.summary for f in declarations)
    assert not any("ignored" in f.summary for f in result.findings)
    assert all(not d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize(
    "extension,source",
    [
        (
            "sln",
            _HEADER
            + _entry("src\\app.vcxproj")
            + _entry("logical", "66a26720-8fb5-11d2-aa7e-00c04f688dde"),
        ),
        (
            "slnx",
            '<Solution><Folder Name="/logical/"><Project Path="src/app.vcxproj"/></Folder></Solution>',
        ),
    ],
)
def test_solution_membership_and_same_directory(extension: str, source: str) -> None:
    result = _detect(
        {
            f"a.{extension}": source.encode(),
            "src/app.vcxproj": b"<Project/>",
            "src/other.vcxproj": b"<Project/>",
        }
    )
    assert len(result.relationships) == 1
    assert result.relationships[0].target_path == "src"
    assert any(c.path == "." and c.kind == "workspace" for c in result.components)
    same = _detect(
        {"a.slnx": b'<Solution><Project Path="a.vcxproj"/></Solution>', "a.vcxproj": b"<Project/>"}
    )
    assert not same.relationships
    assert any(f.code == "msbuild.solution.member" for f in same.findings)


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize(
    "target",
    [
        "../out.vcxproj",
        "C:\\a.vcxproj",
        "\\\\host\\a.vcxproj",
        "/a.vcxproj",
        "$(Project).vcxproj",
        "a.csproj",
        "missing.vcxproj",
        "",
    ],
)
def test_unavailable_unsafe_and_unsupported_members(target: str) -> None:
    import xml.sax.saxutils

    result = _detect(
        {
            "a.slnx": (
                "<Solution><Project Path=" + xml.sax.saxutils.quoteattr(target) + "/></Solution>"
            ).encode()
        }
    )
    assert not result.components
    assert not result.relationships
    assert all(f.classification == "unknown" for f in result.findings)
    assert all(not d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST060")
def test_unknown_solution_constructs_and_conditions() -> None:
    result = _detect(
        {
            "a.slnx": b'<Solution><Project Condition="x" Path="a.vcxproj"/><Configurations/></Solution>',
            "a.vcxproj": b"<Project/>",
        }
    )
    assert not result.relationships
    assert any("Conditional" in f.summary for f in result.findings)
    assert any("Additional" in f.summary for f in result.findings)


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize(
    "name,source",
    [
        ("bad.vcxproj", b"<Project>"),
        ("bad.vcxproj", b"<Wrong/>"),
        ("bad.vcxproj", b'<?xml version="1.0" encoding="unknown"?><Project/>'),
        ("bad.vcxproj", b'<?xml version="1.0" encoding="UTF-7"?><Project/>'),
        ("bad.vcxproj", b'<Project xmlns="urn:other"/>'),
        ("bad.vcxproj", b'<!DOCTYPE Project [<!ENTITY x "secret">]><Project>&x;</Project>'),
        (
            "bad.vcxproj",
            '<!DOCTYPE Project SYSTEM "file:///etc/passwd"><Project/>'.encode("utf-16"),
        ),
        ("bad.vcxproj", b"<Project>" + b"<x>" * 64 + b"</x>" * 64 + b"</Project>"),
        ("bad.slnx", b"<Wrong/>"),
        ("bad.sln", b""),
        ("bad.sln", b"wrong"),
        ("bad.sln", (_HEADER + "Project(bad)\n").encode()),
        ("bad.sln", (_HEADER + "EndProject\n").encode()),
        ("bad.sln", (_HEADER + _entry("a.vcxproj").replace("EndProject\n", "")).encode()),
        (
            "bad.sln",
            (
                _HEADER + _entry("a.vcxproj").replace("EndProject\n", "") + _entry("b.vcxproj")
            ).encode(),
        ),
        ("bad.sln", b"\xff"),
    ],
)
def test_malformed_sources_retain_independent_project(name: str, source: bytes) -> None:
    result = _detect({name: source, "valid/a.vcxproj": b"<Project/>"})
    assert any(d.partial and d.code == "msbuild.invalid-manifest" for d in result.diagnostics)
    assert {c.path for c in result.components} == {"valid"}


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-be"])
def test_xml_encodings_reject_dtd_before_entities_expand(encoding: str) -> None:
    declaration = (
        'encoding="UTF-16BE"'
        if encoding == "utf-16-be"
        else 'encoding="UTF-16"'
        if encoding == "utf-16"
        else 'encoding="UTF-8"'
    )
    source = (
        '<?xml version="1.0" '
        + declaration
        + '?><!DOCTYPE Project [<!ENTITY x "do-not-retain">]><Project><PropertyGroup><ProjectName>&x;</ProjectName></PropertyGroup></Project>'
    )
    result = _detect({"a.vcxproj": source.encode(encoding)})
    assert not result.components
    assert not result.evidence
    assert any(d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_valid_bom_encodings(encoding: str) -> None:
    result = _detect(
        {
            "a.vcxproj": "<Project/>".encode(encoding),
            "a.sln": (_HEADER + _entry("a.vcxproj")).encode(encoding),
        }
    )
    assert any(c.kind == "workspace" for c in result.components)


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("filename", ["Makefile", "makefile", "GNUmakefile"])
@pytest.mark.parametrize("owners", [(), (".",), ("sub",)])
def test_make_never_establishes_boundaries(filename: str, owners: tuple[str, ...]) -> None:
    result = _detect(
        {"sub/" + filename: b"include /etc/passwd\nX=$(shell touch SENTINEL)\n$(X):\n\t@false\n"},
        owners,
    )
    assert not result.components
    assert not result.relationships
    assert result.findings[0].subject_path == (owners[0] if owners else None)
    assert any(d.code == "make.ownership-unresolved" for d in result.diagnostics) == (not owners)


@pytest.mark.verifies("TST060")
def test_unreadable_and_sensitive_sources() -> None:
    result = _detect(
        {
            "a.vcxproj": None,
            "a.slnx": None,
            "Makefile": None,
            "b.vcxproj": b"<Project><PropertyGroup><ProjectName>https://user:password@example.com</ProjectName></PropertyGroup></Project>",
        }
    )
    assert "password" not in repr(result)
    assert any(f.classification == "unknown" for f in result.findings)


@pytest.mark.verifies("TST060")
def test_deadline_interruptions_and_memory_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    view = InMemoryDetectorView({"a.vcxproj": b"<Project/>"})
    monkeypatch.setattr(view, "checkpoint", lambda: True)
    assert not detect_msbuild_make(view, DetectionContext()).evidence
    with pytest.raises(_Stopped):
        _xml(b"<Project/>", lambda: True)
    calls = 0

    def checkpoint() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(_Stopped):
        _xml(b"<Project/>", checkpoint)
    monkeypatch.setattr(view, "checkpoint", lambda: False)
    monkeypatch.setattr(view, "reserve_memory", lambda path, amount: False)
    assert not detect_msbuild_make(view, DetectionContext()).components
    reservations = iter([True, False])
    monkeypatch.setattr(view, "reserve_memory", lambda path, amount: next(reservations))
    assert not detect_msbuild_make(view, DetectionContext()).components


@pytest.mark.verifies("TST060")
def test_in_root_parent_navigation_and_separator_normalization() -> None:
    result = _detect(
        {
            "solutions/a.slnx": b'<Solution><Project Path="../src/./app.vcxproj"/></Solution>',
            "src/app.vcxproj": b"<Project/>",
        }
    )
    assert result.relationships[0].target_path == "src"
    assert result.relationships[0].source_path == "solutions"


@pytest.mark.verifies("TST060")
def test_classic_solution_configuration_sections_are_not_recipes() -> None:
    result = _detect(
        {
            "a.sln": (
                _HEADER
                + "# Visual Studio Version 17\n"
                + _entry("a.vcxproj")
                + "Global\n GlobalSection(SolutionConfigurationPlatforms) = preSolution\n Debug|x64 = Debug|x64\n EndGlobalSection\nEndGlobal\n"
            ).encode(),
            "a.vcxproj": b"<Project/>",
        }
    )
    assert any(f.code == "msbuild.solution.member" for f in result.findings)


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("filename", ["a.vcxproj", "a.sln", "a.slnx"])
def test_deadline_can_interrupt_every_parsing_stage(
    monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    sources = {
        "a.vcxproj": b"<Project><PropertyGroup><ProjectName>Example</ProjectName></PropertyGroup></Project>",
        "a.sln": ("\n" + _HEADER + _entry("b.vcxproj")).encode(),
        "a.slnx": b'<Solution><Project Path="b.vcxproj"/></Solution>',
    }
    for boundary in range(1, 70):
        view = InMemoryDetectorView({filename: sources[filename], "b.vcxproj": b"<Project/>"})
        calls = 0

        def checkpoint(limit: int = boundary) -> bool:
            nonlocal calls
            calls += 1
            return calls >= limit

        monkeypatch.setattr(view, "checkpoint", checkpoint)
        result = detect_msbuild_make(view, DetectionContext())
        keys = {(e.source_kind, e.location, e.locator, e.semantic_key) for e in result.evidence}
        assert all(set(f.evidence_keys) <= keys for f in result.findings)
        assert all(set(c.evidence_keys) <= keys for c in result.components)


@pytest.mark.verifies("TST060")
def test_memory_failure_during_malformed_input_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    view = InMemoryDetectorView({"a.vcxproj": b"<Project>"})
    decisions = iter([True, False])
    monkeypatch.setattr(view, "reserve_memory", lambda path, amount: next(decisions))
    result = detect_msbuild_make(view, DetectionContext())
    assert not result.evidence
    assert not result.diagnostics


@pytest.mark.verifies("TST060")
def test_unsupported_xml_namespaces_do_not_leak_sensitive_namespace_values() -> None:
    result = _detect(
        {
            "a.slnx": b'<Solution xmlns:foreign="https://user:password@example.com"><foreign:Import/></Solution>'
        }
    )
    assert "password" not in repr(result)
    assert any(f.classification == "unknown" for f in result.findings)


@pytest.mark.verifies("TST060")
def test_classic_solution_leading_blank_lines_preserve_source_line_locators() -> None:
    result = _detect(
        {
            "a.sln": ("\n\n" + _HEADER + _entry("a.vcxproj")).encode("utf-8-sig"),
            "a.vcxproj": b"<Project/>",
        }
    )
    assert any(e.locator == "line:4" for e in result.evidence)


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("extension", ["sln", "slnx"])
def test_solution_reference_uses_unique_case_insensitive_catalogue_match(extension: str) -> None:
    declared = "XWBTOOL/XWBTool_Desktop_2022.vcxproj"
    source = (
        _HEADER + _entry(declared.replace("/", "\\"))
        if extension == "sln"
        else f'<Solution><Project Path="{declared}"/></Solution>'
    )
    result = _detect(
        {f"a.{extension}": source.encode(), "XWBTool/xwbtool_Desktop_2022.vcxproj": b"<Project/>"}
    )
    member = next(f for f in result.findings if f.code == "msbuild.solution.member")
    assert "XWBTool/xwbtool_Desktop_2022.vcxproj" in member.summary
    assert "XWBTOOL" in member.summary and "ignoring case" in member.summary
    assert member.classification == "verified"
    assert result.relationships[0].target_path == "XWBTool"
    assert any(c.path == "." and c.kind == "workspace" for c in result.components)


@pytest.mark.verifies("TST060")
def test_exact_solution_path_wins_over_other_case_spellings() -> None:
    result = _detect(
        {
            "a.slnx": b'<Solution><Project Path="src/project.vcxproj"/></Solution>',
            "src/project.vcxproj": b"<Project/>",
            "SRC/PROJECT.VCXPROJ": b"<Project/>",
        }
    )
    assert result.relationships[0].target_path == "src"
    member = next(f for f in result.findings if f.code == "msbuild.solution.member")
    assert '"src/project.vcxproj"' in member.summary
    assert "ignoring case" not in member.summary


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("other", [b"<Project/>", b"<Wrong/>", None])
def test_ambiguous_case_matches_never_select_even_when_one_project_is_invalid(
    other: bytes | None,
) -> None:
    result = _detect(
        {
            "a.slnx": b'<Solution><Project Path="SRC/PROJECT.vcxproj"/></Solution>',
            "src/project.vcxproj": b"<Project/>",
            "src/Project.vcxproj": other,
        }
    )
    assert not result.relationships
    assert not any(f.code == "msbuild.solution.member" for f in result.findings)
    assert any(
        "ambiguous case-insensitive" in f.summary and f.classification == "unknown"
        for f in result.findings
    )
    assert not any(c.kind == "workspace" for c in result.components)


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("data", [None, b"<Wrong/>"])
def test_unique_case_match_still_requires_a_valid_readable_project(data: bytes | None) -> None:
    result = _detect(
        {
            "a.slnx": b'<Solution><Project Path="SRC/PROJECT.vcxproj"/></Solution>',
            "src/project.vcxproj": data,
        }
    )
    assert not result.components
    assert not result.relationships
    assert not any(f.code == "msbuild.solution.member" for f in result.findings)


@pytest.mark.verifies("TST060")
def test_incomplete_case_index_cannot_resolve_a_seemingly_unique_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {
        "a.slnx": b'<Solution><Project Path="SRC/PROJECT.vcxproj"/></Solution>',
        "src/Project.vcxproj": b"<Project/>",
        "src/project.vcxproj": b"<Project/>",
    }
    view = InMemoryDetectorView(files)
    reserve = view.reserve_memory

    def reserve_memory(path: str, amount: int) -> bool:
        if path == "src/project.vcxproj" and amount == 4096 + 32 * len(path):
            return False
        return reserve(path, amount)

    monkeypatch.setattr(view, "reserve_memory", reserve_memory)
    result = detect_msbuild_make(view, DetectionContext())
    assert any(f.code == "msbuild.project.evidence" for f in result.findings)
    assert not any(f.code == "msbuild.solution.member" for f in result.findings)
    assert not result.relationships


@pytest.mark.verifies("TST060")
@pytest.mark.parametrize("source", [b"<Project/>", b"<Project>"])
def test_memory_failure_after_case_index_and_parser_reservations(
    monkeypatch: pytest.MonkeyPatch, source: bytes
) -> None:
    view = InMemoryDetectorView({"a.vcxproj": source})
    decisions = iter([True, True, False])
    monkeypatch.setattr(view, "reserve_memory", lambda path, amount: next(decisions))
    result = detect_msbuild_make(view, DetectionContext())
    assert not result.evidence
    assert not result.components
    assert not result.diagnostics
