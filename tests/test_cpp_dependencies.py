"""Static dependency declarations and bounded parsing unit scenarios."""

from __future__ import annotations

import json

import pytest

from slygentify._scan.contracts import DetectionContext, DetectionResult
from slygentify._scan.detectors.cpp_dependencies import _document, detect_cpp_dependencies
from tests.scan_views import InMemoryDetectorView


def _detect(files: dict[str, bytes | None], owners: tuple[str, ...] = (".",)) -> DetectionResult:
    return detect_cpp_dependencies(
        InMemoryDetectorView(files), DetectionContext(component_paths=frozenset(owners))
    )


def _vcpkg(value: object) -> DetectionResult:
    return _detect({"vcpkg.json": json.dumps(value).encode()})


@pytest.mark.verifies("TST059")
def test_rich_declarations_scopes_and_conflicts() -> None:
    result = _vcpkg(
        {
            "supports": "windows & !arm",
            "default-features": ["extra"],
            "dependencies": [
                "fmt",
                {"name": "fmt", "version>=": "2.0#1"},
                {
                    "name": "fmt",
                    "host": True,
                    "features": ["tools"],
                    "platform": "windows",
                    "default-features": False,
                },
            ],
            "features": {"extra": {"dependencies": ["fmt"], "supports": "linux"}, "empty": {}},
            "overrides": [
                {"name": "fmt", "version-semver": "1.2.3", "port-version": 2},
                {"name": "fmt", "version-date": "2025-01-01"},
            ],
        }
    )
    assert not result.components
    assert sum(d.code.endswith("dependency-conflict") for d in result.diagnostics) == 2
    assert not any(d.partial for d in result.diagnostics)
    assert any(e.locator == "/features/extra/dependencies/0" for e in result.evidence)
    assert any('"host":true' in f.summary for f in result.findings)
    assert all(f.subject_path == "." for f in result.findings)


@pytest.mark.verifies("TST059")
def test_identical_declarations_host_platform_and_feature_scopes_are_not_conflicts() -> None:
    result = _vcpkg(
        {
            "dependencies": [
                "a",
                "a",
                {"name": "a", "host": True},
                {"name": "a", "platform": "linux"},
            ],
            "features": {"b": {"dependencies": [{"name": "a", "version>=": "2"}]}},
        }
    )
    assert not result.diagnostics


@pytest.mark.verifies("TST059")
@pytest.mark.parametrize(
    "document",
    [
        {"dependencies": False},
        {
            "dependencies": [
                False,
                {},
                {"name": "ABC"},
                {"name": "a", "host": 1},
                {"name": "b", "features": "x"},
                {"name": "c", "version>=": []},
                {"name": "d", "platform": "${x}"},
                "valid",
            ]
        },
        {"supports": False},
        {"default-features": "a"},
        {"features": []},
        {"features": {"invalid/secret": {}, "valid": 1, "bad": {"supports": False}}},
        {"overrides": False},
        {
            "overrides": [
                False,
                {},
                {"name": "a"},
                {"name": "b", "version": "1", "port-version": -1},
                {"name": "c", "version": "1", "port-version": True},
                {"name": "d", "version": "1", "other": True},
                {"name": "e", "version": []},
            ]
        },
    ],
)
def test_invalid_fields_are_partial(document: object) -> None:
    result = _vcpkg(document)
    assert any(d.partial for d in result.diagnostics)
    if isinstance(document, dict) and isinstance(document.get("dependencies"), list):
        assert any('"valid"' in f.summary for f in result.findings)


@pytest.mark.verifies("TST059")
def test_unknown_dependency_fields_retain_safe_supported_fields() -> None:
    result = _vcpkg({"dependencies": [{"name": "fmt", "password": "secret=very-secret"}]})
    assert any(f.code == "vcpkg.dependency.declaration" for f in result.findings)
    assert any(d.code == "vcpkg.unsupported-declaration" for d in result.diagnostics)
    assert "very-secret" not in repr(result)


@pytest.mark.verifies("TST059")
@pytest.mark.parametrize(
    "data",
    [
        b"[]",
        b'{"a":1,"a":2}',
        b'{"x":NaN}',
        b'{"x":1e999}',
        b"\xff",
        b'{"x":"\\ud800"}',
        b'{"a":' + b"[" * 40 + b"0" + b"]" * 40 + b"}",
        b'{"a":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}",
    ],
)
def test_invalid_json_is_diagnosed_without_raw_values(data: bytes) -> None:
    result = _detect({"vcpkg.json": data})
    assert any(d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST059")
def test_conan_categories_ranges_revisions_and_unsupported_sections() -> None:
    result = _detect(
        {
            "conanfile.txt": b"""# comment

[requires]
fmt/[>=1.0 <2.0]@team/stable#rev
fmt/3.0
[tool_requires]
cmake/3.29
[test_requires]
gtest/1.0
[build_requires]
legacy/1.0
[options]
password=secret
[invalid section]
outside
[requires]
${dynamic}
"""
        }
    )
    declarations = [f for f in result.findings if f.code == "conan.dependency.declaration"]
    assert len(declarations) == 5
    assert any("legacy declaration" in f.summary for f in declarations)
    assert any(e.locator == "section:requires:line:4" for e in result.evidence)
    assert any(d.code.endswith("dependency-conflict") for d in result.diagnostics)
    assert any(d.partial for d in result.diagnostics)
    assert "password=secret" not in repr(result)


@pytest.mark.verifies("TST059")
def test_conan_invalid_encoding_and_sensitive_reference() -> None:
    for data in [b"\xff", b"[requires]\nhttps://user:secret@host/x\n", b"[" + b"x" * 129 + b"]"]:
        result = _detect({"conanfile.txt": data})
        assert any(d.partial for d in result.diagnostics)
        assert "user:secret" not in repr(result)


@pytest.mark.verifies("TST059")
def test_recipes_unreadable_other_files_unowned_and_nested_attribution() -> None:
    result = _detect(
        {
            "a/conanfile.py": b'raise RuntimeError("secret")',
            "a/b/conanfile.txt": b"[requires]\nx/1\n",
            "c/vcpkg.json": b"{}",
            "other.txt": b"",
            "vcpkg.json": None,
        },
        ("a", "a/b"),
    )
    assert {f.subject_path for f in result.findings} == {"a", "a/b", None}
    assert any(d.code == "conan.dynamic-recipe" for d in result.diagnostics)
    assert any(d.code == "vcpkg.ownership-unresolved" for d in result.diagnostics)
    assert "RuntimeError" not in repr(result)
    assert not any(d.partial for d in result.diagnostics)


@pytest.mark.verifies("TST059")
def test_checkpoint_interrupts_document_validation() -> None:
    with pytest.raises(ValueError, match="interrupted"):
        _document(b"{}", lambda: True)


@pytest.mark.verifies("TST059")
@pytest.mark.parametrize(
    "filename,document",
    [
        (
            "vcpkg.json",
            {
                "dependencies": ["a", "b"],
                "features": {"a": {}, "b": {}},
                "overrides": [{"name": "a", "version": "1"}, {"name": "b", "version": "2"}],
            },
        ),
        ("conanfile.txt", "[requires]\na/1\nb/2"),
    ],
)
def test_checkpoint_interrupts_every_loop(filename: str, document: object) -> None:
    data = document.encode() if isinstance(document, str) else json.dumps(document).encode()
    for threshold in range(1, 90):

        class LimitedView(InMemoryDetectorView):
            calls = 0

            limit = threshold

            def checkpoint(self) -> bool:
                self.calls += 1
                return self.calls >= self.limit

        detect_cpp_dependencies(LimitedView({filename: data}), DetectionContext())


@pytest.mark.verifies("TST059")
def test_feature_objects_comments_versions_and_invalid_siblings() -> None:
    result = _vcpkg(
        {
            "dependencies": [
                {
                    "name": "fmt",
                    "$comment": "secret=hidden",
                    "features": [
                        "*",
                        {"name": "extra", "platform": "windows", "$comment": "hidden"},
                        {"name": "extra"},
                        False,
                        {"name": "BAD"},
                        {"name": "bad", "platform": False},
                        {"name": "bad", "extra": "secret"},
                    ],
                    "version>=": "2024 March#2",
                }
            ],
            "features": {
                "$comment": "hidden",
                "bad": {"description": False},
                "desc": {"description": ["safe"]},
                "str": {"description": "safe"},
                "bad-desc": {"description": [False]},
            },
            "default-features": [{"name": "extra", "platform": "linux"}, False],
            "overrides": [{"name": "fmt", "version-string": "2024 March", "$comment": "hidden"}],
        }
    )
    assert any("2024 March" in f.summary for f in result.findings)
    assert any(d.partial for d in result.diagnostics)
    assert "secret=hidden" not in repr(result)
    assert not any(d.code == "vcpkg.unsupported-declaration" for d in result.diagnostics)


@pytest.mark.verifies("TST059")
def test_platform_comma_qualifier_and_invalid_feature_names_remain_explicit() -> None:
    result = _vcpkg({"dependencies": [{"name": "zlib", "platform": "windows,linux"}]})
    assert not result.diagnostics
    assert any('"platform":"windows,linux"' in f.summary for f in result.findings)
    result = _vcpkg(
        {"features": {"$comment": "do-not-disclose-feature-comment"}, "default-features": ["*"]}
    )
    assert sum(d.partial for d in result.diagnostics) == 2
    assert "do-not-disclose-feature-comment" not in repr(result)


@pytest.mark.verifies("TST059")
def test_dynamic_values_are_unknown_limitations_and_subset_declarations() -> None:
    result = _vcpkg(
        {
            "dependencies": [{"name": "a", "version>=": "${VERSION}"}],
            "overrides": [{"name": "a", "version": "${VERSION}"}],
        }
    )
    assert not any(d.partial for d in result.diagnostics)
    assert any(d.code == "vcpkg.dynamic-declaration" for d in result.diagnostics)
    assert "${VERSION}" not in repr(result)
    assert not any('"unresolved"' in f.summary for f in result.findings)
    conan = _detect({"conanfile.txt": b"[requires]\n${VERSION}\n"})
    assert not any(d.partial for d in conan.diagnostics)


@pytest.mark.verifies("TST059")
def test_explicit_defaults_additive_features_and_cross_manifest_are_not_conflicts() -> None:
    result = _vcpkg(
        {
            "dependencies": [
                "a",
                {"name": "a", "default-features": True},
                {"name": "a", "host": False},
                {"name": "a", "features": ["x"]},
                {"name": "a", "features": ["y"]},
            ]
        }
    )
    assert not result.diagnostics

    result = _detect(
        {
            "a/vcpkg.json": b'{"dependencies":[{"name":"a","version>=":"1"}]}',
            "b/vcpkg.json": b'{"dependencies":[{"name":"a","version>=":"2"}]}',
        }
    )
    assert not result.diagnostics


@pytest.mark.verifies("TST059")
def test_identical_override_fields_ignore_json_key_order() -> None:
    result = _vcpkg(
        {"overrides": [{"name": "fmt", "version": "1"}, {"version": "1", "name": "fmt"}]}
    )
    assert not result.diagnostics


@pytest.mark.verifies("TST059")
@pytest.mark.parametrize(
    "reference",
    ["sdl/[~2.28]", "pkg/[^1.2]", "pkg/[>1 <2 || ^3.2]", "pkg/[>1 <2, include_prerelease]"],
)
def test_conan_literal_range_operators_are_retained_without_resolution(reference: str) -> None:
    result = _detect({"conanfile.txt": f"[requires]\n{reference}\n".encode()})
    assert not result.diagnostics
    assert any(reference in finding.summary for finding in result.findings)
    assert any(item.locator == "section:requires:line:2" for item in result.evidence)


@pytest.mark.verifies("TST059")
def test_feature_loop_interruption() -> None:
    data = json.dumps({"default-features": ["a", "b"]}).encode()
    for threshold in range(1, 30):

        class LimitedView(InMemoryDetectorView):
            calls = 0
            limit = threshold

            def checkpoint(self) -> bool:
                self.calls += 1
                return self.calls >= self.limit

        detect_cpp_dependencies(LimitedView({"vcpkg.json": data}), DetectionContext())
