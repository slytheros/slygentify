"""Safe scan foundation tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import slygentify._git_tracking as git_tracking
import slygentify._scan.orchestration as orchestration
from slygentify import ScanResult, SkippedScope, scan_repository
from slygentify._git_tracking import _TrackedPaths
from slygentify._scan import _scan_foundation, _ScanFoundationError
from slygentify._scan.contracts import DetectionContext, DiagnosticCandidate, PartialCause
from slygentify._scan.detectors.generic import detect_generic
from slygentify._scan.kernel import _Entry, _inspect, _Inspection, _Limits, _RepositoryView
from slygentify._scan.normalization import _boundary_cause, _matching_boundary, _normalize


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    return repository


@pytest.mark.verifies("TST011", "TST015", "TST016")
def test_scan_foundation_discovers_generic_components_deterministically(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    nested = repository / "services" / "go"
    nested.mkdir(parents=True)
    (repository / "Cargo.toml").write_text('[package]\nname = "example"\n', encoding="utf-8")
    (nested / "go.mod").write_text("module example.test/service\n", encoding="utf-8")
    first_root, first = _scan_foundation(nested)
    second_root, second = _scan_foundation(repository)

    assert first_root == second_root == repository
    assert first == second
    assert first.completion == "complete"
    assert {(item.path, item.ecosystem, item.kind) for item in first.components} == {
        (".", "generic", "package"),
        ("services/go", "generic", "package"),
    }
    assert not first.findings
    assert all(len(item.id.rsplit("_", 1)[-1]) == 64 for item in first.evidence)


@pytest.mark.verifies("TST016")
def test_co_located_manifests_normalize_to_one_workspace_component(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "Cargo.toml").write_text("[workspace]\nmembers = []\n", encoding="utf-8")
    (repository / "go.mod").write_text("module example.test/root\n", encoding="utf-8")
    (repository / "pom.xml").write_text("<project/>", encoding="utf-8")

    _, result = _scan_foundation(repository)

    assert len(result.components) == 1
    assert result.components[0].kind == "workspace"
    assert len(result.components[0].evidence_ids) == 3


@pytest.mark.verifies("TST016")
@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("Cargo.toml", "not = [toml", "inspection.invalid-manifest"),
        ("go.mod", "go 1.25\n", "inspection.invalid-manifest"),
        ("go.work", "go 1.25\n", "inspection.invalid-manifest"),
        ("pom.xml", "<invalid>", "inspection.invalid-manifest"),
        ("pom.xml", "<!DOCTYPE project><project/>", "inspection.unsafe-xml"),
    ],
)
def test_invalid_generic_manifests_are_partial_unknowns(
    tmp_path: Path, name: str, content: str, code: str
) -> None:
    repository = _repository(tmp_path)
    (repository / name).write_text(content, encoding="utf-8")

    _, result = _scan_foundation(repository)

    assert result.completion == "partial"
    assert result.findings[0].classification == "unknown"
    assert code in {item.code for item in result.diagnostics}


@pytest.mark.verifies("TST016")
@pytest.mark.parametrize(
    ("files", "code"),
    [
        (
            {"Cargo.toml": b'[workspace]\nmembers = ["../out"]\n'},
            "inspection.invalid-workspace-member",
        ),
        (
            {"Cargo.toml": b'[workspace]\nmembers = ["missing"]\n'},
            "inspection.missing-workspace-member",
        ),
        ({"go.work": b"use ../out\n"}, "inspection.invalid-workspace-member"),
        ({"go.work": b"use ./missing\n"}, "inspection.missing-workspace-member"),
        (
            {"pom.xml": b"<project><modules><module>missing</module></modules></project>"},
            "inspection.missing-workspace-member",
        ),
    ],
)
def test_workspace_member_problems_remain_visible(files: dict[str, bytes], code: str) -> None:
    result = detect_generic(_RepositoryView(files), DetectionContext())
    assert code in {item.code for item in result.diagnostics}


@pytest.mark.verifies("TST014")
def test_scope_rules_skip_ignored_sensitive_cached_and_nested_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text("ignored/\n*.xml\n!pom.xml\n", encoding="utf-8")
    (repository / "ignored").mkdir()
    (repository / "ignored" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (repository / ".venv").mkdir()
    (repository / ".env").write_text("SECRET=value", encoding="utf-8")
    (repository / "nested").mkdir()
    (repository / "nested" / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    (repository / "nested" / "go.mod").write_text("module hidden\n", encoding="utf-8")
    (repository / "pom.xml").write_text("<project/>", encoding="utf-8")

    _, result = _scan_foundation(repository)
    reasons = {item.reason for item in result.skipped_scopes}

    assert {"gitignore", "built_in_exclusion", "sensitive_content", "nested_repository"} <= reasons
    assert [item.path for item in result.components] == ["."]


@pytest.mark.verifies("TST014")
def test_tracked_manifest_enters_ignored_directory_without_retaining_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    ignored = repository / "ignored"
    ignored.mkdir()
    (repository / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (ignored / "Cargo.toml").write_text('[package]\nname = "tracked"\n', encoding="utf-8")
    (ignored / "go.mod").write_text("module untracked.test/project\n", encoding="utf-8")
    monkeypatch.setattr(
        "slygentify._scan.orchestration._discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(
            frozenset({b"ignored/Cargo.toml"}), frozenset({b"ignored"}), True
        ),
    )

    result = scan_repository(repository)

    assert [(item.path, item.ecosystem) for item in result.components] == [("ignored", "generic")]
    assert ("ignored/go.mod", "gitignore") in {
        (item.scope, item.reason) for item in result.skipped_scopes
    }
    assert all(item.location != "ignored/go.mod" for item in result.evidence)


@pytest.mark.verifies("TST014")
def test_tracked_paths_do_not_override_hard_inspection_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text("*\n", encoding="utf-8")
    (repository / ".env").write_text("SECRET=value\n", encoding="utf-8")
    cache = repository / ".venv"
    cache.mkdir()
    (cache / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    nested = repository / "nested"
    nested.mkdir()
    (nested / ".git").mkdir()
    (nested / "go.mod").write_text("module hidden.test/project\n", encoding="utf-8")
    monkeypatch.setattr(
        "slygentify._scan.orchestration._discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(
            frozenset({b".env", b".venv/Cargo.toml", b"nested/go.mod"}),
            frozenset({b".venv", b"nested"}),
            True,
        ),
    )

    result = scan_repository(repository)

    assert not result.components
    assert {"sensitive_content", "built_in_exclusion", "nested_repository"} <= {
        item.reason for item in result.skipped_scopes
    }


@pytest.mark.verifies("TST011", "TST014")
def test_unavailable_git_tracking_is_an_honest_repository_wide_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    monkeypatch.setattr(
        "slygentify._scan.orchestration._discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset(), frozenset(), False),
    )

    result = scan_repository(repository)

    assert result.completion == "partial"
    assert "inspection.git-tracked-paths-unavailable" in {item.code for item in result.diagnostics}
    assert (".", "git_tracking_unavailable", "**") in {
        (item.scope, item.reason, item.omitted_scope) for item in result.skipped_scopes
    }


@pytest.mark.verifies("TST014")
def test_invalid_gitignore_rules_return_a_partial_result(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text("!\n", encoding="utf-8")
    (repository / "Cargo.toml").write_text('[package]\nname = "example"\n', encoding="utf-8")

    result = scan_repository(repository)

    assert result.completion == "partial"
    assert {(item.scope, item.reason) for item in result.skipped_scopes} >= {
        (".gitignore", "invalid_gitignore")
    }
    assert "inspection.invalid-gitignore" in {item.code for item in result.diagnostics}
    assert [item.path for item in result.components] == ["."]


@pytest.mark.verifies("TST014")
def test_invalid_root_configuration_fails_before_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "slygentify.toml").write_text("schema_version = 2\n", encoding="utf-8")
    traversed = False

    def unexpected(*args: object, **kwargs: object) -> object:
        nonlocal traversed
        traversed = True
        raise AssertionError

    monkeypatch.setattr("slygentify._scan.orchestration._inspect", unexpected)
    with pytest.raises(_ScanFoundationError, match="slygentify.toml"):
        _scan_foundation(repository)
    assert not traversed


@pytest.mark.verifies("TST011", "TST017")
def test_invalid_explicit_git_executable_fails_before_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    traversed = False

    def unexpected(*args: object, **kwargs: object) -> object:
        nonlocal traversed
        traversed = True
        raise AssertionError("traversal must not begin")

    monkeypatch.setattr(
        "slygentify._scan.orchestration._discover_tracked_paths",
        git_tracking._discover_tracked_paths,
    )
    monkeypatch.setattr(orchestration, "_inspect", unexpected)

    with pytest.raises(_ScanFoundationError, match="git_executable"):
        _scan_foundation(repository, git_executable=tmp_path / "missing-git")
    assert not traversed


@pytest.mark.verifies("TST011")
@pytest.mark.parametrize("marker", ["missing", "symlink"])
def test_scan_root_failures_are_controlled(tmp_path: Path, marker: str) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    if marker == "symlink":
        target = tmp_path / "marker"
        target.write_text("gitdir", encoding="utf-8")
        try:
            (repository / ".git").symlink_to(target)
        except OSError:
            pytest.skip("symlink creation is unavailable")
    with pytest.raises(_ScanFoundationError):
        _scan_foundation(repository)


@pytest.mark.verifies("TST012")
def test_link_and_special_entries_are_not_read(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    outside = tmp_path / "outside-Cargo.toml"
    outside.write_text("[package]\n", encoding="utf-8")
    try:
        (repository / "Cargo.toml").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    _, result = _scan_foundation(repository)

    assert not result.components
    assert "link_or_reparse" in {item.reason for item in result.skipped_scopes}


@pytest.mark.verifies("TST013")
@pytest.mark.parametrize(
    ("limits", "reason"),
    [
        (_Limits(max_entries=1), "max_entries"),
        (_Limits(max_depth=1), "max_depth"),
        (_Limits(max_file_bytes=3), "max_file_bytes"),
        (_Limits(max_total_bytes=3), "max_total_bytes"),
        (_Limits(max_memory_bytes=1), "max_memory_bytes"),
    ],
)
def test_deterministic_resource_limits_return_partial_results(
    tmp_path: Path, limits: _Limits, reason: str
) -> None:
    repository = _repository(tmp_path)
    (repository / "child" / "grandchild").mkdir(parents=True)
    (repository / "Cargo.toml").write_text("[package]\n", encoding="utf-8")

    execution = _scan_foundation(repository, limits=limits)
    result = execution.result

    assert result.completion == "partial"
    assert reason in {item.reason for item in result.skipped_scopes}
    cause = next(
        item
        for item in execution.partial_causes
        if item.source_code == f"inspection.boundary.{reason}"
    )
    assert f"scan.limits.{reason}" in (cause.recovery or "")
    assert cause.boundary is not None
    assert cause.boundary.effective_limit is not None
    assert cause.disposition == "limitation"


@pytest.mark.verifies("TST013")
def test_tracked_path_memory_can_exhaust_the_budget_before_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    monkeypatch.setattr(
        "slygentify._scan.orchestration._discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset(), frozenset(), True, memory_consumed=1),
    )

    _, result = _scan_foundation(repository, limits=_Limits(max_memory_bytes=1))

    assert result.completion == "partial"
    assert (".", "max_memory_bytes", 1, 1) in {
        (item.scope, item.reason, item.effective_limit, item.consumed)
        for item in result.skipped_scopes
    }


@pytest.mark.verifies("TST013")
def test_elapsed_limit_returns_an_explicit_partial_result(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    ticks: Iterator[float] = iter((0.0, 61.0))

    execution = _scan_foundation(
        repository, limits=_Limits(max_elapsed_seconds=60), clock=lambda: next(ticks)
    )
    result = execution.result

    assert result.completion == "partial"
    assert result.skipped_scopes[0].reason == "max_elapsed_seconds"
    assert execution.partial_causes[0].source_code == "inspection.boundary.max_elapsed_seconds"
    assert "scan.limits.max_elapsed_seconds" in (execution.partial_causes[0].recovery or "")


@pytest.mark.verifies("TST013", "TST047")
def test_private_partial_cause_normalization_deduplicates_one_boundary_event(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    boundary = SkippedScope(
        scope="bad",
        reason="unsafe_file",
        effective_limit=None,
        consumed=None,
        omitted_scope="bad",
    )
    candidate = DiagnosticCandidate(
        "inspection.unsafe-file",
        "bad",
        "File could not be read safely.",
        True,
        disposition="problem",
    )
    inspection = _Inspection(
        files={},
        skipped=(boundary,),
        diagnostics=(candidate, candidate),
        partial=True,
        root=repository,
        limits=_Limits(max_elapsed_seconds=None),
        partial_skipped=(boundary,),
    )
    causes: list[PartialCause] = []

    _normalize(repository, inspection, memory_limit=None, partial_causes=causes)

    assert len(causes) == 1
    assert causes[0].source_code == "inspection.unsafe-file"

    limit_problem, limit_effect, limit_recovery = _boundary_cause(
        SkippedScope(
            scope="large",
            reason="max_entries",
            effective_limit=10,
            consumed=None,
            omitted_scope="large/**",
        )
    )
    assert "max_entries" in limit_problem
    assert "consumed" not in limit_effect
    assert "scan.limits.max_entries" in limit_recovery
    assert (
        "fixture boundary"
        in _boundary_cause(
            SkippedScope(
                scope="unknown",
                reason="fixture",
                effective_limit=None,
                consumed=None,
                omitted_scope="unknown/**",
            )
        )[0]
    )
    assert (
        _matching_boundary(
            DiagnosticCandidate(
                "other.code", "bad", "Another condition.", False, disposition="problem"
            ),
            (boundary,),
        )
        is None
    )


@pytest.mark.verifies("TST012", "TST013")
def test_kernel_reports_unsafe_directory_and_file_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    device = repository.stat().st_dev
    entry = _Entry("Cargo.toml", False, True, False, False, 10, (device, 1))
    calls = 0

    def list_entries(root: Path, relative: str) -> tuple[_Entry, ...]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return (entry, _Entry("bad", True, False, False, False, 0, (device, 2)))
        raise OSError("unsafe")

    monkeypatch.setattr("slygentify._scan.kernel._list_entries", list_entries)
    monkeypatch.setattr(
        "slygentify._scan.kernel._read_file", lambda *args: (_ for _ in ()).throw(OSError())
    )

    inspection = _inspect(repository, limits=_Limits())

    assert inspection.partial
    assert {item.reason for item in inspection.skipped} == {"unsafe_file", "unsafe_directory"}


@pytest.mark.verifies("TST015")
def test_scan_result_is_a_normalized_public_value(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _, result = _scan_foundation(repository)
    assert isinstance(result, ScanResult)
    assert result.findings[0].code == "core.component-boundary-unknown"
    assert result.repository.root == "."
