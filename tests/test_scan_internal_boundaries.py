"""Deterministic conformance tests for private scan safety boundaries."""

from __future__ import annotations

import ctypes
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import slygentify._scan.kernel as scan
import slygentify._scan.normalization as normalization
from slygentify import Diagnostic, Evidence, Finding, SkippedScope
from slygentify._scan import _scan_foundation, _ScanFoundationError
from slygentify._scan.contracts import (
    ComponentCandidate,
    DetectionResult,
    EvidenceCandidate,
)
from slygentify._scan.detectors import generic
from slygentify._scan.detectors._support import evidence_key
from slygentify._scan.paths import parent, safe_member


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    return root


@pytest.mark.verifies("TST012", "TST014", "TST016")
def test_path_scope_helpers_cover_safe_and_unsafe_forms(tmp_path: Path) -> None:
    rules = scan._IgnoreRules()
    assert rules.add("nested", "*.tmp\n!keep.tmp\n") > 0
    assert not rules.ignored("elsewhere/file.tmp", is_dir=False)
    assert rules.ignored("nested/drop.tmp", is_dir=False)
    assert not rules.ignored("nested/keep.tmp", is_dir=False)
    assert not scan._inside(tmp_path, tmp_path.parent)
    assert safe_member(".", "") is None
    assert safe_member(".", "C:/repo") is None
    assert safe_member("base", "../out") is None
    assert safe_member("base", "child") == "base/child"
    assert parent("Cargo.toml") == "."
    assert scan._relative(".", "file") == "file"
    assert scan._path(tmp_path, ".") == tmp_path

    sensitive = [
        ".env.local",
        "secret.pem",
        ".terraform/providers/file",
        ".ssh/config",
        ".aws/credentials",
        ".kube/config",
    ]
    assert all(scan._sensitive(item) for item in sensitive)
    assert not scan._sensitive("src/config.json.example")


@pytest.mark.verifies("TST012")
def test_open_handle_path_adapters_are_containment_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.write_text("value", encoding="utf-8")

    class FakeFunction:
        argtypes: object = None
        restype: object = None
        value = str(target)
        length: int | None = None

        def __call__(self, handle: int, buffer: object, size: int, flags: int) -> int:
            assert handle == 42
            buffer.value = self.value  # type: ignore[attr-defined]
            return self.length if self.length is not None else len(self.value)

    function = FakeFunction()
    kernel = SimpleNamespace(GetFinalPathNameByHandleW=function)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda descriptor: 42))

    assert scan._windows_final_path(1) == target
    function.value = f"\\\\?\\{target}"
    assert str(scan._windows_final_path(1)).endswith("target")
    function.value = "\\\\?\\UNC\\server\\share\\target"
    assert str(scan._windows_final_path(1)).endswith("target")
    function.length = 0
    with pytest.raises(OSError, match="cannot be resolved"):
        scan._windows_final_path(1)

    monkeypatch.setattr(scan, "_IS_WINDOWS", True)
    monkeypatch.setattr(scan, "_windows_final_path", lambda descriptor: target)
    assert scan._opened_final_path(1, target) == target
    monkeypatch.setattr(scan, "_IS_WINDOWS", False)
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(os.path, "realpath", lambda value, *args, **kwargs: str(target))
    assert scan._opened_final_path(1, target) == target
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert scan._opened_final_path(1, target) == target


@pytest.mark.verifies("TST012")
def test_directory_listing_rejects_wrong_kind_escape_and_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    regular = root / "regular"
    regular.write_text("value", encoding="utf-8")
    with pytest.raises(OSError, match="identity"):
        scan._list_entries(root, "regular")

    monkeypatch.setattr(scan, "_inside", lambda *args: False)
    with pytest.raises(OSError, match="escapes"):
        scan._list_entries(root, ".")
    monkeypatch.undo()

    actual_stat = Path.stat
    calls = 0

    def changing_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        nonlocal calls
        value = actual_stat(path, *args, **kwargs)
        if path == root:
            calls += 1
            if calls == 2:
                values = list(value)
                values[1] += 1
                return os.stat_result(values)
        return value

    monkeypatch.setattr(Path, "stat", changing_stat)
    with pytest.raises(OSError, match="changed"):
        scan._list_entries(root, ".")


@pytest.mark.verifies("TST012", "TST013")
def test_safe_read_rejects_containment_and_identity_races(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    target = root / "Cargo.toml"
    target.write_bytes(b"x")
    metadata = target.stat()
    entry = scan._Entry("Cargo.toml", False, True, False, False, 1, scan._identity(metadata))

    monkeypatch.setattr(scan, "_inside", lambda *args: False)
    with pytest.raises(OSError, match="containment"):
        scan._read_file(root, entry, scan._Limits())
    monkeypatch.undo()

    monkeypatch.setattr(scan, "_opened_final_path", lambda *args: root.parent / "outside")
    with pytest.raises(OSError, match="opened file escapes"):
        scan._read_file(root, entry, scan._Limits())
    monkeypatch.undo()

    wrong = scan._Entry("Cargo.toml", False, True, False, False, 1, (999, 999))
    with pytest.raises(OSError, match="before"):
        scan._read_file(root, wrong, scan._Limits())

    real_fstat = os.fstat
    count = 0

    def changing_fstat(descriptor: int) -> os.stat_result:
        nonlocal count
        value = real_fstat(descriptor)
        count += 1
        if count == 2:
            values = list(value)
            values[1] += 1
            return os.stat_result(values)
        return value

    monkeypatch.setattr(os, "fstat", changing_fstat)
    with pytest.raises(OSError, match="during"):
        scan._read_file(root, entry, scan._Limits())
    monkeypatch.undo()

    class OversizedStream:
        def __enter__(self) -> OversizedStream:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            return b"xx"

    monkeypatch.setattr(os, "fdopen", lambda *args, **kwargs: OversizedStream())
    with pytest.raises(OverflowError, match="max_file_bytes"):
        scan._read_file(root, entry, scan._Limits(max_file_bytes=1))


@pytest.mark.verifies("TST012", "TST013", "TST014")
@pytest.mark.parametrize(
    ("read_effect", "limits", "reason"),
    [
        (OSError(), scan._Limits(), "invalid_gitignore"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), scan._Limits(), "invalid_gitignore"),
        (b"1234", scan._Limits(max_total_bytes=3), "max_total_bytes"),
        (b"abc\n", scan._Limits(max_memory_bytes=1), "max_memory_bytes"),
    ],
)
def test_gitignore_control_file_failures_are_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_effect: BaseException | bytes,
    limits: scan._Limits,
    reason: str,
) -> None:
    root = _repository(tmp_path)
    device = root.stat().st_dev
    entry = scan._Entry(".gitignore", False, True, False, False, 1, (device, 1))
    monkeypatch.setattr(scan, "_list_entries", lambda *args: (entry,))

    def read(*args: object) -> bytes:
        if isinstance(read_effect, BaseException):
            raise read_effect
        return read_effect

    monkeypatch.setattr(scan, "_read_file", read)
    result = scan._inspect(root, limits=limits)
    assert result.partial
    assert reason in {item.reason for item in result.skipped}


@pytest.mark.verifies("TST012", "TST014")
def test_gitignore_on_another_volume_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    device = root.stat().st_dev
    entry = scan._Entry(".gitignore", False, True, False, False, 1, (device + 1, 1))
    monkeypatch.setattr(scan, "_list_entries", lambda *args: (entry,))
    monkeypatch.setattr(scan, "_read_file", lambda *args: pytest.fail("mount was read"))
    result = scan._inspect(root, limits=scan._Limits())
    assert result.partial
    assert result.skipped[0].reason == "mount_boundary"


@pytest.mark.verifies("TST012", "TST014")
def test_entry_kind_mount_and_special_branches_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    device = root.stat().st_dev
    entries = (
        scan._Entry("link", False, False, True, False, 0, (device, 1)),
        scan._Entry("mount", True, False, False, False, 0, (device + 1, 2)),
        scan._Entry("socket", False, False, False, False, 0, (device, 3)),
        scan._Entry("ordinary.txt", False, True, False, False, 0, (device, 4)),
    )
    monkeypatch.setattr(scan, "_list_entries", lambda *args: entries)
    result = scan._inspect(root, limits=scan._Limits())
    assert {item.reason for item in result.skipped} == {
        "link_or_reparse",
        "mount_boundary",
        "special_entry",
    }


@pytest.mark.verifies("TST016")
def test_generic_parser_remaining_valid_and_invalid_branches() -> None:
    evidence, components, diagnostics = generic._cargo(
        "Cargo.toml", b"[workspace]\nmembers = 'invalid'\n", frozenset({"Cargo.toml"})
    )
    assert evidence and components and diagnostics[0].code == "inspection.invalid-workspace"
    assert generic._cargo("Cargo.toml", b"[tool]\n", frozenset()) == ([], [], [])
    assert not generic._cargo(
        "Cargo.toml", b'[workspace]\nmembers = ["member"]\n', frozenset({"member/Cargo.toml"})
    )[2]

    evidence, components, diagnostics = generic._go(
        "go.mod", b"module \xff\n", frozenset({"go.mod"})
    )
    assert not evidence and not components and diagnostics
    evidence, components, diagnostics = generic._go(
        "go.work", b"use (\n./member // comment\n)\n", frozenset({"go.work", "member/go.mod"})
    )
    assert evidence and components and not diagnostics

    assert (
        generic._maven("pom.xml", b"<other/>", frozenset())[2][0].code
        == "inspection.invalid-manifest"
    )
    assert (
        generic._maven(
            "pom.xml",
            b"<project><modules><module>../out</module></modules></project>",
            frozenset({"pom.xml"}),
        )[2][0].code
        == "inspection.invalid-workspace-member"
    )
    assert not generic._maven(
        "pom.xml",
        b"<project><modules><module>member</module></modules></project>",
        frozenset({"pom.xml", "member/pom.xml"}),
    )[2]


@pytest.mark.verifies("TST015")
def test_normalizer_handles_duplicate_and_budget_rejected_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    candidate = EvidenceCandidate(
        "manifest", "Cargo.toml", "package", "Package.", "parse", "rule", "key"
    )
    component = ComponentCandidate(".", "package", (evidence_key(candidate),))
    missing = ComponentCandidate("missing", "package", (("x", "x", None, "x"),))
    monkeypatch.setattr(
        normalization,
        "BUILTIN_DETECTORS",
        (
            lambda view, context: DetectionResult(
                evidence=(candidate, candidate), components=(component, missing)
            ),
        ),
    )
    inspection = scan._Inspection({}, (), (), False)

    result = normalization._normalize(root, inspection, memory_limit=10_000)
    assert len(result.components) == 1
    assert result.completion == "partial"

    limited = normalization._normalize(root, inspection, memory_limit=1)
    assert limited.completion == "partial"
    assert "inspection.max-memory-bytes" in {item.code for item in limited.diagnostics}
    assert normalization._record_size({"a": 1}) > 0


@pytest.mark.verifies("TST011", "TST012")
def test_scan_foundation_translates_discovery_and_marker_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(_ScanFoundationError):
        _scan_foundation(missing)

    root = _repository(tmp_path)
    monkeypatch.setattr(Path, "lstat", lambda self: (_ for _ in ()).throw(OSError()))
    with pytest.raises(_ScanFoundationError, match="cannot be inspected"):
        _scan_foundation(root)
    monkeypatch.undo()

    marker = root / ".git"
    marker.rmdir()
    marker.mkdir()
    fake = SimpleNamespace(st_mode=stat.S_IFCHR, st_file_attributes=0)
    monkeypatch.setattr(Path, "lstat", lambda self: fake if self == marker else self.stat())
    with pytest.raises(_ScanFoundationError, match="not a safe"):
        _scan_foundation(root)


@pytest.mark.verifies("TST010")
def test_model_remaining_optional_and_ordering_branches() -> None:
    optional = Evidence(
        id="e",
        source_kind="manifest",
        location="file",
        locator=None,
        observation="observation",
        verification_method=None,
    )
    assert optional.verification_method is None
    with pytest.raises(ValueError, match="evidence.*canonical"):
        _invalid_order(
            evidence=(
                Evidence(
                    id="z",
                    source_kind="x",
                    location="z",
                    locator=None,
                    observation="z",
                    verification_method=None,
                ),
                optional,
            )
        )
    with pytest.raises(ValueError, match="findings.*canonical"):
        _invalid_order(findings=(_finding("z"), _finding("a")))
    with pytest.raises(ValueError, match="diagnostics.*canonical"):
        _invalid_order(diagnostics=(_diagnostic("z"), _diagnostic("a")))
    with pytest.raises(ValueError, match="skipped_scopes.*canonical"):
        _invalid_order(skipped_scopes=(_skipped("z"), _skipped("a")))


def _finding(identifier: str) -> Finding:
    return Finding(
        id=identifier,
        code=identifier,
        classification="unknown",
        subject_id="repository",
        summary=identifier,
        evidence_ids=(),
    )


def _diagnostic(identifier: str) -> Diagnostic:
    return Diagnostic(
        id=identifier,
        code=identifier,
        subject_id="repository",
        location=None,
        message=identifier,
        evidence_ids=(),
    )


def _skipped(identifier: str) -> SkippedScope:
    return SkippedScope(
        scope=identifier,
        reason=identifier,
        effective_limit=None,
        consumed=None,
        omitted_scope=identifier,
    )


def _invalid_order(**changes: object) -> object:
    from slygentify import Repository, ScanResult

    values: dict[str, object] = {
        "schema_version": 1,
        "producer_version": "0.1.0",
        "completion": "complete",
        "repository": Repository(id="repository", root=".", kind="git", evidence_ids=()),
        "components": (),
        "evidence": (),
        "findings": (),
        "diagnostics": (),
        "skipped_scopes": (),
    }
    values.update(changes)
    return ScanResult(**values)  # type: ignore[arg-type]
