"""Static build declarations, uncertainty and bounded parser conformance."""

from __future__ import annotations

import pytest

from slygentify._scan.contracts import DetectionContext, DetectionResult, Detector
from slygentify._scan.detectors.autotools import detect_automake, detect_autotools
from slygentify._scan.detectors.bazel import detect_bazel
from slygentify._scan.detectors.meson import detect_meson
from tests.scan_views import InMemoryDetectorView


def _detect(
    detector: Detector, files: dict[str, bytes | None], owners: tuple[str, ...] = ()
) -> DetectionResult:
    view = InMemoryDetectorView(files)
    result = detector(view, DetectionContext(component_paths=frozenset(owners)))
    if detector is not detect_autotools:
        return result
    fragments = detect_automake(
        view,
        DetectionContext(component_paths=frozenset(owners) | {c.path for c in result.components}),
    )
    return DetectionResult(
        result.evidence + fragments.evidence,
        result.components + fragments.components,
        result.findings + fragments.findings,
        result.diagnostics + fragments.diagnostics,
        result.relationships + fragments.relationships,
    )


def _summaries(result: DetectionResult, code: str = "") -> str:
    return "\n".join(f.summary for f in result.findings if code in f.code)


@pytest.mark.verifies("TST062")
def test_meson_literal_projects_languages_and_composition() -> None:
    result = _detect(
        detect_meson,
        {
            "meson.build": b"""# project('commented', 'c')
project(
 'hello', ['c', 'cpp'], version: '1.2', subproject_dir: 'vendor',
)
subdir('src')
dep = subproject('child')
if get_option('optional')
 add_languages('rust')
 subproject('child')
 project('conditional', 'java')
endif
""",
            "src/meson.build": b"add_languages(['fortran'])\n",
            "vendor/child/meson.build": b"project('child')",
        },
    )
    assert {c.path for c in result.components} == {".", "vendor/child"}
    assert {(r.source_path, r.target_path) for r in result.relationships} == {(".", "vendor/child")}
    assert all(c.ecosystem == "meson" for c in result.components)
    text = _summaries(result)
    assert '"c","cpp"' in text and '"1.2"' in text and '"fortran"' in text
    assert "commented" not in text
    assert any("rust" in f.summary and "conditional" in f.summary for f in result.findings)
    assert any("conditional" in f.summary and f.subject_path is None for f in result.findings)
    assert any("does not establish" in f.summary for f in result.findings)
    assert len({(e.location, e.locator) for e in result.evidence}) == len(result.evidence)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "source",
    [
        "project(NAME)",
        "project()",
        "project('x', version: '1', version: '2')",
        "holder.project('fake', 'c')",
        "text = \"project('fake', 'c')\"",
        "if enabled\nproject('fake','c')\nendif",
        "project('x') + project('fake')",
        "project('x') or project('fake')",
        "project('x')('fake')",
        "project('x' + get_name())",
        "project('x\\q')",
        "project('x\\n')",
    ],
)
def test_meson_opaque_identity_never_fabricates_boundaries(source: str) -> None:
    result = _detect(detect_meson, {"meson.build": source.encode()})
    assert not result.components
    assert result.findings
    assert any(f.classification == "unknown" or "conditional" in f.summary for f in result.findings)


@pytest.mark.verifies("TST062")
def test_meson_dynamic_versions_languages_members_and_identity_conflicts() -> None:
    result = _detect(
        detect_meson,
        {
            "meson.build": b"""project('first', langs, version: version, subproject_dir: custom)
project('second')
subproject('child')
subdir('missing')
subdir('../outside')
subproject(computed)
dependency('external')
""",
            "subprojects/child/meson.build": b"project('child')",
        },
    )
    assert not result.relationships
    text = _summaries(result)
    assert "Competing" in text and "Computed project version" in text
    assert "Computed language" in text and "Computed subproject directory" in text
    assert "../outside" not in text and "custom" not in text and "'external'" not in text


@pytest.mark.verifies("TST062")
def test_autoconf_packages_markers_and_safe_subpackages() -> None:
    result = _detect(
        detect_autotools,
        {
            "configure.ac": b"""dnl AC_INIT([commented], [9])
# AC_PROG_CXX
AC_INIT([hello], [1.0])
AC_PROG_CC
AC_PROG_CXX([g++])
AC_CONFIG_SUBDIRS([child missing ../out])
AC_DEFUN([CUSTOM], [AC_INIT([fake], [1]) AC_PROG_CXX])
AS_IF([true], [AC_INIT([nested], [1])])
if test "$optional" = yes; then
AC_PROG_CXX
AC_CONFIG_SUBDIRS([child])
fi
""",
            "child/configure.ac": b"AC_INIT([child], [2])\n",
        },
    )
    assert {c.path for c in result.components} == {".", "child"}
    assert all(c.ecosystem == "autotools" for c in result.components)
    assert {(r.source_path, r.target_path) for r in result.relationships} == {(".", "child")}
    text = _summaries(result)
    assert all(name not in text for name in ("commented", "fake", "nested", "../out"))
    assert "C compiler check" in text and "C++ compiler check" in text
    assert any("conditional" in f.summary for f in result.findings if "language" in f.code)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "source",
    [
        "AC_INIT(NAME, [1])",
        'AC_INIT("not-m4-quoted", [1])',
        "AC_INIT()",
        "AC_INIT([m4_defn([NAME])], [1])",
        "AC_DEFUN([HELPER], [AC_INIT([hidden], [1])])",
        "AC_INIT([$PACKAGE], [1])",
        "if enabled; then\nAC_INIT([hidden], [1])\nfi",
        "'AC_INIT([hidden], [1])'",
        "AC_INIT([https://user:password@example.com], [1])",
    ],
)
def test_autoconf_dynamic_macro_or_sensitive_identity_is_not_a_boundary(source: str) -> None:
    result = _detect(detect_autotools, {"configure.ac": source.encode()})
    assert not result.components
    assert "password" not in _summaries(result)


@pytest.mark.verifies("TST062")
def test_autoconf_conflicts_and_dynamic_subpackages_and_versions() -> None:
    result = _detect(
        detect_autotools,
        {
            "configure.ac": b"AC_INIT([one], version)\nAC_INIT([two], [2])\nAC_CONFIG_SUBDIRS(dirs)\n"
        },
    )
    assert "Competing" in _summaries(result)
    assert "Computed package version" in _summaries(result)
    assert not result.relationships


@pytest.mark.verifies("TST062")
def test_automake_sources_suffixes_continuations_conditions_and_fragment_ownership() -> None:
    result = _detect(
        detect_autotools,
        {
            "configure.ac": b"AC_INIT([app], [1])",
            "Makefile.am": b"SUBDIRS = src child\nbin_PROGRAMS = app\n",
            "child/configure.ac": b"AC_INIT([child], [1])",
            "src/Makefile.am": b"""bin_SOURCES = main.c util.C a.cc b.cpp c.cxx d.c++ \\
 header.h header.hpp
bin_SOURCES += more.cpp
if OPTIONAL
EXTRA_app_SOURCES := extra.cpp
else
nodist_app_SOURCES ?= generated.c
endif
SUBDIRS = missing
include external.am
all:
\t@touch SENTINEL
""",
        },
    )
    sources = [f for f in result.findings if "language" in f.code]
    assert len(sources) == 9
    assert all(f.subject_path == "." for f in sources)
    assert any("conditional" in f.summary and "EXTRA_app_SOURCES" in f.summary for f in sources)
    assert any("nodist_app_SOURCES" in f.summary for f in sources)
    assert not any("header" in f.summary for f in sources)
    assert {c.path for c in result.components} == {".", "child"}
    assert {(r.source_path, r.target_path) for r in result.relationships} == {(".", "child")}
    assert "does not establish" in _summaries(result)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "assignment",
    [
        "bin_SOURCES = $(DYNAMIC)",
        "bin_SOURCES = @DYNAMIC@",
        "bin_SOURCES = ../outside.cpp",
        "bin_SOURCES = /external.cpp",
        "bin_SOURCES = *.cpp",
        "bin_SOURCES = src/[computed].cpp",
        "bin_SOURCES =",
        "bin_SOURCES = https://user:password@example.com/a.cpp",
        "bin_SOURCES = x\\\\y.cpp",
        "SUBDIRS = $(MAYBE)",
    ],
)
def test_automake_computed_and_unsafe_sources_are_withheld(assignment: str) -> None:
    result = _detect(detect_autotools, {"Makefile.am": assignment.encode()})
    assert not result.components
    assert not any("language" in f.code for f in result.findings)
    assert "password" not in _summaries(result) and "outside" not in _summaries(result)
    assert "no independently established" in _summaries(result)


@pytest.mark.verifies("TST062")
def test_automake_configured_owner_and_conditional_composition() -> None:
    result = _detect(
        detect_autotools,
        {
            "app/Makefile.am": b"# comment\n\nif OPTIONAL\nSUBDIRS = src\nendif\napp_SOURCES = main.cpp\n",
            "app/src/Makefile.am": b"",
        },
        ("app",),
    )
    assert not result.components
    assert not result.relationships
    assert all(f.subject_path == "app" for f in result.findings)
    assert "conditional" in _summaries(result)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "source", [b"app_SOURCES = foo.c \\", b"if X\napp_SOURCES = foo.c", b"\xff"]
)
def test_automake_malformed_input_is_partial(source: bytes) -> None:
    result = _detect(detect_autotools, {"Makefile.am": source})
    assert any(d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST062")
def test_bazel_roots_nested_packages_and_declared_rules() -> None:
    result = _detect(
        detect_bazel,
        {
            "MODULE.bazel": b'module(name="main", version="1")',
            "BUILD": b'cc_library(name="root")',
            "src/BUILD.bazel": b"""# cc_test(name="commented")
load("@rules_cc//cc:defs.bzl", "cc_library", binary="cc_binary")
cc_library(name="lib", srcs=glob(["*.cc"]))
binary(name="app")
""",
            "nested/REPO.bazel": b"",
            "nested/lib/BUILD": b'cc_import(name="import")\ncc_shared_library(name="shared")',
            "other/WORKSPACE.bazel": b'workspace(name="other")',
            "other/BUILD": b'cc_test(name="test")',
            "external.bzl": b'cc_library(name="notread")',
        },
    )
    assert len({c.path for c in result.components}) == 5
    pairs = {(r.source_path, r.target_path) for r in result.relationships}
    assert pairs == {(".", "src"), ("nested", "nested/lib")}
    assert all(a != b for a, b in pairs)
    text = _summaries(result, ".language.")
    assert all(
        rule in text
        for rule in ("cc_library", "cc_binary", "cc_test", "cc_import", "cc_shared_library")
    )
    assert "commented" not in text and "notread" not in text
    assert "standard rules_cc import" in text


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "source",
    [
        'load("@rules_cc//cc:cc_library.bzl", "cc_library")\ncc_library(name="x")',
        'load("@rules_cc//cc:defs.bzl", renamed="cc_library")\nrenamed(name="x")',
    ],
)
def test_bazel_standard_per_rule_imports_and_aliases(source: str) -> None:
    result = _detect(detect_bazel, {"BUILD": source.encode()})
    assert "standard rules_cc import" in _summaries(result, ".language.")
    assert "No catalogued Bazel" in _summaries(result)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "source",
    [
        'load("//local:macros.bzl", "cc_library")\ncc_library(name="x")',
        'load("@rules_cc//cc:defs.bzl", "custom")\ncustom(name="x")',
        'load("@rules_cc//cc:cc_binary.bzl", "cc_library")\ncc_library(name="x")',
        'load(label, "cc_library")\ncc_library(name="x")',
        'load("@rules_cc//cc:defs.bzl", variable)\ncustom(name="x")',
        'cc_library = custom\ncc_library(name="x")',
        'load("@rules_cc//cc:defs.bzl", "cc_library")\ncc_library = custom\ncc_library(name="x")',
        'def cc_library(name):\n custom(name=name)\ncc_library(name="x")',
        "cc_library(name=target)",
        'cc_library("x")',
        'value = cc_library(name="x")',
        'if enabled:\n cc_library(name="x")',
        'if enabled:\n load("@rules_cc//cc:defs.bzl", "cc_library")\ncc_library(name="x")',
        '"""cc_library(name="fake")"""',
    ],
)
def test_bazel_computed_imported_or_shadowed_calls_do_not_claim_language(source: str) -> None:
    result = _detect(detect_bazel, {"BUILD": source.encode()})
    assert not any("language" in f.code for f in result.findings)
    assert result.diagnostics


@pytest.mark.verifies("TST062")
def test_bazel_duplicate_files_and_identity_conflicts_remain_explicit() -> None:
    result = _detect(
        detect_bazel,
        {
            "MODULE.bazel": b'module(name="one")\nmodule(name="two", version=version)',
            "REPO.bazel": b"",
            "BUILD": b"",
            "BUILD.bazel": b"",
            "legacy/WORKSPACE": b'workspace(name="legacy")',
            "legacy/WORKSPACE.bazel": b"",
            "legacy/src/BUILD": b"",
        },
    )
    assert "Competing identity" in _summaries(result)
    assert "Competing Bazel boundary" in _summaries(result)
    assert "Computed Bazel version" in _summaries(result)
    assert not result.relationships


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "source",
    [
        "module()",
        "module(name=variable)",
        'module = custom\nmodule(name="x")',
        'if enabled:\n module(name="x")',
    ],
)
def test_bazel_opaque_identity_preserves_marker_boundary(source: str) -> None:
    result = _detect(detect_bazel, {"MODULE.bazel": source.encode()})
    assert len(result.components) == 1
    assert not any("identity.declaration" in f.code for f in result.findings)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "detector,name,good",
    [
        (detect_meson, "meson.build", b"project('valid')\n"),
        (detect_autotools, "configure.ac", b"AC_INIT([valid], [1])\n"),
        (detect_bazel, "BUILD", b'cc_library(name="valid")\n'),
    ],
)
@pytest.mark.parametrize(
    "bad",
    [
        b"broken(",
        b"'unterminated",
        b")",
        b"broken([)]",
        b"call(" + b"[" * 65,
        b"'escape\\",
        b"'\\x41'",
        b"\xff",
    ],
)
def test_malformed_source_preserves_independent_declarations(
    detector: Detector, name: str, good: bytes, bad: bytes
) -> None:
    result = _detect(detector, {name: good + bad, f"independent/{name}": good})
    # Unsupported escape sequences are opaque, not interpreted as their spelling.
    if bad != b"'\\x41'":
        assert any(d.partial for d in result.diagnostics)
    assert result.components
    assert "valid" in _summaries(result)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "detector,name",
    [(detect_meson, "meson.build"), (detect_autotools, "configure.ac"), (detect_bazel, "BUILD")],
)
def test_unreadable_and_irrelevant_paths_are_not_interpreted(detector: Detector, name: str) -> None:
    result = _detect(detector, {name: None, "unrelated.txt": b"anything"})
    assert not result.components and not result.findings


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "detector,name,source",
    [
        (detect_meson, "meson.build", b"project('x')"),
        (detect_autotools, "configure.ac", b"AC_INIT([x],[1])"),
        (detect_bazel, "BUILD", b"cc_library(name='x')"),
        (detect_automake, "Makefile.am", b"app_SOURCES = main.cpp"),
    ],
)
@pytest.mark.parametrize("stop", ["catalog", "parse", "parser-memory", "candidate-memory"])
def test_resource_stops_release_temporary_parser_memory(
    detector: Detector, name: str, source: bytes, stop: str
) -> None:
    class BoundedView(InMemoryDetectorView):
        def __init__(self) -> None:
            super().__init__({name: source})
            self.calls = 0

        def checkpoint(self) -> bool:
            self.calls += 1
            return (
                self.calls >= (1 if stop == "catalog" else 3)
                if stop in {"catalog", "parse"}
                else False
            )

        def reserve_memory(self, path: str, amount: int) -> bool:
            if (stop == "parser-memory" and amount >= 8192) or (
                stop == "candidate-memory" and amount < 8192
            ):
                return False
            return super().reserve_memory(path, amount)

    view = BoundedView()
    detector(view, DetectionContext(component_paths=frozenset({"."})))
    assert view.memory_reserved == 0


@pytest.mark.verifies("TST062")
def test_computed_language_arrays_unsafe_subproject_directory_and_inline_locators() -> None:
    result = _detect(
        detect_meson,
        {
            "meson.build": b"project('x', ['c', computed], subproject_dir: '../bad'); add_languages('cpp'); add_languages('rust')\n"
        },
    )
    assert "Computed language" in _summaries(result)
    assert "Computed subproject directory" in _summaries(result)
    assert len({e.locator for e in result.evidence}) == len(result.evidence)


@pytest.mark.verifies("TST062")
def test_bazel_unreadable_nested_root_prevents_ancestor_membership() -> None:
    result = _detect(
        detect_bazel, {"MODULE.bazel": b"", "nested/REPO.bazel": None, "nested/pkg/BUILD": b""}
    )
    assert not result.relationships
    assert "No catalogued Bazel" in _summaries(result)


@pytest.mark.verifies("TST062")
def test_quoted_punctuation_control_words_and_m4_markers_remain_data() -> None:
    meson = _detect(
        detect_meson,
        {
            "meson.build": b"'if'\nproject('x', '[' 'cpp' ']', version ':' 'not-a-version')\n'endif'\n"
        },
    )
    assert {c.path for c in meson.components} == {"."}
    assert not any(
        f.code.endswith(("language.declaration", "version.declaration")) for f in meson.findings
    )
    autotools = _detect(
        detect_autotools, {"configure.ac": b"AC_INIT([x],[1])\n[AC_PROG_CC]\n'AC_PROG_CXX'\n"}
    )
    assert not any("language" in f.code for f in autotools.findings)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "rebinding", ["cc_library += other", "cc_library, other = pair", "(other, cc_library) = pair"]
)
def test_bazel_opaque_assignment_cannot_preserve_a_legacy_rule_binding(rebinding: str) -> None:
    result = _detect(detect_bazel, {"BUILD": (rebinding + '\ncc_library(name="x")').encode()})
    assert not any("language" in f.code for f in result.findings)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize("text", [b"else", b"endif"])
def test_automake_unmatched_terminators_are_partial(text: bytes) -> None:
    result = _detect(detect_autotools, {"Makefile.am": text})
    assert any(d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "detector,name", [(detect_meson, "meson.build"), (detect_autotools, "configure.ac")]
)
@pytest.mark.parametrize("text", [b"endif", b"if enabled\n"])
def test_unbalanced_control_blocks_are_partial(detector: Detector, name: str, text: bytes) -> None:
    assert any(d.partial for d in _detect(detector, {name: text}).diagnostics)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "detector,files",
    [
        (
            detect_meson,
            {
                "meson.build": b"project('x', ['c'])\nsubdir('src')\n",
                "src/meson.build": b"add_languages('cpp')",
            },
        ),
        (
            detect_autotools,
            {
                "configure.ac": b"AC_INIT([x], [1])\nAC_PROG_CC\n",
                "Makefile.am": b"app_SOURCES = main.cpp\n",
            },
        ),
        (
            detect_bazel,
            {
                "BUILD": b"load('@rules_cc//cc:defs.bzl', 'cc_library')\n[cc_library(name=n) for n in names]\ncc_library(name='x')",
                "MODULE.bazel": b"module(name='x')",
            },
        ),
        (detect_automake, {"Makefile.am": b"app_SOURCES = main.cpp\n"}),
    ],
)
def test_deadlines_at_every_parser_and_retention_phase_are_bounded(
    detector: Detector, files: dict[str, bytes | None]
) -> None:
    class DeadlineView(InMemoryDetectorView):
        def __init__(self, stop: int) -> None:
            super().__init__(files)
            self.stop = stop
            self.calls = 0

        def checkpoint(self) -> bool:
            self.calls += 1
            return self.calls >= self.stop

    for stop in range(1, 300):
        view = DeadlineView(stop)
        result = detector(view, DetectionContext())
        assert view.memory_reserved >= 0
        keys = {(e.source_kind, e.location, e.locator, e.semantic_key) for e in result.evidence}
        assert all(set(f.evidence_keys) <= keys for f in result.findings)
        assert all(set(c.evidence_keys) <= keys for c in result.components)


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize("stop", ["unreadable", "memory"])
def test_automake_unreadable_and_parser_memory_boundaries(stop: str) -> None:
    class MemoryView(InMemoryDetectorView):
        def reserve_memory(self, path: str, amount: int) -> bool:
            return False

    view = MemoryView({"Makefile.am": b"app_SOURCES = main.cpp" if stop == "memory" else None})
    assert not detect_automake(view, DetectionContext()).components


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "detector,name,source",
    [
        (detect_meson, "meson.build", b"project('valid')\n"),
        (detect_autotools, "configure.ac", b"AC_INIT([valid],[1])\n"),
        (detect_bazel, "BUILD", b"cc_library(name='valid')\n"),
    ],
)
def test_later_oversized_document_preserves_an_independent_boundary(
    detector: Detector, name: str, source: bytes
) -> None:
    class MemoryView(InMemoryDetectorView):
        def reserve_memory(self, path: str, amount: int) -> bool:
            return amount < 100000 and super().reserve_memory(path, amount)

    view = MemoryView({f"a/{name}": source, f"z/{name}": source * 1000})
    result = detector(view, DetectionContext())
    assert {c.path for c in result.components} == {"a"}


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "pattern",
    [
        "linux* | darwin*)",
        "(linux*)",
        "(esac)",
        r"a\))",
        "linux*|\\\n darwin*)",
        "a|\n esac)",
        "'esac')",
        '"a)b")',
        "[a-z]*)",
        "linux*|\n darwin*)",
    ],
)
@pytest.mark.parametrize("terminator", [";;", ";&", ";;&"])
def test_autoconf_case_arms_preserve_conditional_and_later_declarations(
    pattern: str, terminator: str
) -> None:
    source = f"""AC_INIT([parent],[1])
case "$host_os" in
 {pattern} AC_PROG_CC; AC_CONFIG_SUBDIRS([conditional]) {terminator}
 *) ignored=yes ;;
esac
AC_CONFIG_SUBDIRS([child])
AC_PROG_CXX
"""
    result = _detect(
        detect_autotools,
        {
            "configure.ac": source.encode(),
            "child/configure.ac": b"AC_INIT([child],[1])",
            "conditional/configure.ac": b"AC_INIT([conditional],[1])",
        },
    )
    assert {(r.source_path, r.target_path) for r in result.relationships} == {(".", "child")}
    assert not any(d.partial for d in result.diagnostics)
    cc = [
        f
        for f in result.findings
        if f.code == "autotools.language.declaration" and "AC_PROG_CC " in f.summary
    ]
    cxx = [
        f
        for f in result.findings
        if f.code == "autotools.language.declaration" and "AC_PROG_CXX " in f.summary
    ]
    assert cc and all("conditional" in f.summary for f in cc)
    assert cxx and all("conditional" not in f.summary for f in cxx)


@pytest.mark.verifies("TST062")
def test_nested_and_inline_autoconf_cases_and_quoted_macro_bodies() -> None:
    source = b"""AC_INIT([parent],[1])
AC_DEFUN([HIDDEN],[case "$x" in *) AC_INIT([hidden],[1]);; esac])
case "$x" in first) case "$y" in second) AC_PROG_CC;; esac ;; esac
if enabled; then
case "$x" in
 *) AC_PROG_CXX ;;
esac
fi
if enabled; then case "$x" in *) AC_PROG_CXX;; esac; fi
for value in one; do case "$value" in *) AC_PROG_CXX;; esac; done
AC_CONFIG_SUBDIRS([child])
"""
    result = _detect(
        detect_autotools, {"configure.ac": source, "child/configure.ac": b"AC_INIT([child],[1])"}
    )
    assert {c.path for c in result.components} == {".", "child"}
    assert not any(d.partial for d in result.diagnostics)
    assert len(result.relationships) == 1
    assert all(
        "conditional" in f.summary
        for f in result.findings
        if f.code == "autotools.language.declaration"
    )


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "source",
    [
        b"AC_INIT([x],[1])\ncase x in\n *) AC_PROG_CC",
        b"AC_INIT([x],[1])\nAC_PROG_CC)\n",
        b"AC_INIT([x],[1])\ncase x in\n *) f( ;;\nesac\n",
    ],
)
def test_incomplete_cases_and_unmatched_macro_delimiters_stay_partial(source: bytes) -> None:
    result = _detect(detect_autotools, {"configure.ac": source})
    assert {c.path for c in result.components} == {"."}
    assert any(d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST062")
def test_generic_limits_are_aggregated_by_owner_and_category_with_all_source_evidence() -> None:
    result = _detect(
        detect_meson,
        {
            "meson.build": b"project('root','c')\nx=1\ny=2\nsubdir(computed)",
            "fragment/meson.build": b"a=1\nb=2\n",
            "child/meson.build": b"project('child','cpp')\nc=1\nd=2\n",
            "child/fragment/meson.build": b"x=1\ny=2\n",
        },
    )
    generic = [f for f in result.findings if "Additional Meson syntax" in f.summary]
    assert {f.subject_path for f in generic} == {".", "child"}
    assert len(generic) == 2
    assert {e.location for e in result.evidence} == {
        "meson.build",
        "fragment/meson.build",
        "child/meson.build",
        "child/fragment/meson.build",
    }
    assert any("subdirectory reference" in f.summary for f in result.findings)
    assert any(d.code == "meson.unsupported-declaration" for d in result.diagnostics)


@pytest.mark.verifies("TST062")
def test_aggregation_retains_specific_dynamic_and_unsafe_diagnostics() -> None:
    result = _detect(
        detect_bazel,
        {
            "MODULE.bazel": b"module(name='x')\nbazel_dep(name='one')\nbazel_dep(name='two')",
            "BUILD": b"load('//:one.bzl','macro')\nload('//:two.bzl','other')\npackage()\ncc_library(name=dynamic)\ncc_test(name=computed)",
        },
    )
    unknown = [f for f in result.findings if f.code == "bazel.declaration.unresolved"]
    assert len(unknown) == 4  # one generic-expression limit, one load limit, two specific calls
    assert sum("shadowed, computed" in f.summary for f in unknown) == 2
    assert {e.location for e in result.evidence} == {"BUILD", "MODULE.bazel"}


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "prefix,suffix", [("{ ", "; }"), ("{ a='x'\n", "; }"), ("true && ", ""), ("true | ", "")]
)
def test_cases_in_shell_groups_and_pipelines_do_not_discard_later_macros(
    prefix: str, suffix: str
) -> None:
    source = f"""AC_INIT([parent],[1])
{prefix}case `git --version | sed -e 's/^[^0-9]*//'` in
 0.* | 1.[0-5].*) AC_INIT([hidden],[1]); AC_PROG_CC ;;
 *) true ;;
esac{suffix}
AC_CONFIG_SUBDIRS([child])
AC_PROG_CXX
"""
    result = _detect(
        detect_autotools,
        {"configure.ac": source.encode(), "child/configure.ac": b"AC_INIT([child],[1])"},
    )
    assert {c.path for c in result.components} == {".", "child"}
    assert not any(d.partial for d in result.diagnostics)
    assert {(r.source_path, r.target_path) for r in result.relationships} == {(".", "child")}
    assert any(
        f.code == "autotools.language.declaration"
        and "AC_PROG_CXX" in f.summary
        and "conditional" not in f.summary
        for f in result.findings
    )
    assert all(
        "conditional" in f.summary
        for f in result.findings
        if f.code == "autotools.language.declaration" and "AC_PROG_CC " in f.summary
    )


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "recipe",
    [
        "\tapp_SOURCES=not_a_source.cpp\n\tSUBDIRS=child\n",
        "\tcommand \\\napp_SOURCES=not_a_source.cpp \\\nSUBDIRS=child\n",
        "\tif optional; then \\\n\tapp_SOURCES=not_a_source.cpp; fi\n",
    ],
)
def test_automake_recipes_cannot_declare_sources_or_members(recipe: str) -> None:
    result = _detect(
        detect_autotools,
        {
            "configure.ac": b"AC_INIT([app],[1])",
            "child/configure.ac": b"AC_INIT([child],[1])",
            "Makefile.am": ("all:\n" + recipe + "app_SOURCES = \\\n\tactual.c\n").encode(),
        },
    )
    languages = _summaries(result, ".language.")
    assert '".c"' in languages and '".cpp"' not in languages
    assert not result.relationships
    assert "recipes" in _summaries(result)
    assert not any(d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST062")
def test_bazel_complementary_root_markers_preserve_nested_package_membership() -> None:
    result = _detect(
        detect_bazel,
        {
            "MODULE.bazel": b'module(name="main")',
            "REPO.bazel": b"",
            "pkg/BUILD": b"",
            "nested/MODULE.bazel": b'module(name="nested")',
            "nested/REPO.bazel": b"",
            "nested/pkg/BUILD.bazel": b"",
        },
    )
    assert {(r.source_path, r.target_path) for r in result.relationships} == {
        (".", "pkg"),
        ("nested", "nested/pkg"),
    }
    assert "Ambiguous" not in _summaries(result)
    assert {c.path for c in result.components} == {".", "pkg", "nested", "nested/pkg"}


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "prefix",
    [
        'package(default_visibility=["//visibility:public"]); ',
        '"""module documentation\n"""; ',
        'load("@rules_cc//cc:defs.bzl", "cc_library"); ',
        '[cc_library(name=n) for n in ["generated"]]\n',
        'values = [cc_library(name=n) for n in ["generated"]]\n',
        'load("@rules_cc//cc:defs.bzl", "cc_library")\n[cc_library(name=n) for n in ["generated"]]\n',
    ],
)
def test_bazel_top_level_calls_survive_semicolons_and_nested_keywords(prefix: str) -> None:
    result = _detect(detect_bazel, {"BUILD": (prefix + 'cc_library(name="direct")').encode()})
    languages = [f for f in result.findings if ".language." in f.code]
    assert len(languages) == 1
    assert '"direct"' in languages[0].summary
    assert "conditional" not in languages[0].summary


@pytest.mark.verifies("TST062")
@pytest.mark.parametrize(
    "source",
    [
        'if enabled:\n    package(); cc_library(name="deferred")',
        'def deferred(): package(); cc_library(name="deferred")',
        'if enabled: package(); cc_library(name="deferred")',
    ],
)
def test_bazel_indented_semicolon_calls_remain_deferred(source: str) -> None:
    result = _detect(
        detect_bazel,
        {"BUILD": source.encode()},
    )
    assert not _summaries(result, ".language.")


@pytest.mark.verifies("TST062")
def test_bazel_package_lookup_work_grows_linearly(monkeypatch: pytest.MonkeyPatch) -> None:
    from slygentify._scan.detectors import bazel
    from slygentify._scan.paths import parent

    original = parent
    calls = 0

    def counted(path: str) -> str:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(bazel, "parent", counted)
    sizes = (128, 256)
    costs = []
    for size in sizes:
        calls = 0
        result = _detect(
            detect_bazel,
            {"MODULE.bazel": b"", **{f"pkg{i}/BUILD": b"" for i in range(size)}},
        )
        assert len(result.relationships) == size
        costs.append(calls)
    assert costs[1] <= 2 * costs[0]
    assert costs[1] <= 4 * (sizes[1] + 1)
