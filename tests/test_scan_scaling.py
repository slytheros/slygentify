"""Deterministic regression coverage for composed-repository scan scaling."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest

import slygentify._scan.detectors.generic as generic
import slygentify._scan.detectors.javascript as javascript
import slygentify._scan.detectors.python as python
import slygentify._scan.kernel as scan
import slygentify._scan.normalization as normalization
from slygentify import dump_scan_json
from slygentify._configuration import ComponentDeclaration, EffectiveConfiguration
from slygentify._scan.contracts import (
    ComponentCandidate,
    DetectionContext,
    DetectionResult,
    DiagnosticCandidate,
    EvidenceCandidate,
    FindingCandidate,
    PathCandidate,
    RelationshipCandidate,
)
from slygentify._scan.detectors._support import evidence_key
from slygentify._scan.paths import descendant_paths, nearest_ancestor
from tests.scan_views import InMemoryDetectorView


class _CountingView(scan._RepositoryView):
    def __init__(self, files: dict[str, bytes]) -> None:
        super().__init__(files)
        self.path_catalog_calls = 0
        self.direct_child_calls = 0
        self.direct_child_items = 0

    def path_candidates(self) -> tuple[PathCandidate, ...]:
        self.path_catalog_calls += 1
        return super().path_candidates()

    def direct_children(self, parent: str) -> tuple[PathCandidate, ...]:
        self.direct_child_calls += 1
        result = super().direct_children(parent)
        self.direct_child_items += len(result)
        return result


class _CheckpointView(scan._RepositoryView):
    """Deterministic detector view that expires after a selected checkpoint."""

    def __init__(self, files: dict[str, bytes], stop_after: int) -> None:
        super().__init__(files)
        self._stop_after = stop_after
        self.checkpoints = 0

    def checkpoint(self) -> bool:
        self.checkpoints += 1
        self.partial = self.checkpoints > self._stop_after
        return self.partial


def _composed_files(trees: int) -> dict[str, bytes]:
    files = {
        "package.json": b'{"name":"workspace","private":true,"workspaces":["tree-*"]}',
    }
    for index in range(trees):
        root = f"tree-{index:03d}"
        files.update(
            {
                f"{root}/pyproject.toml": b'[project]\nname = "demo"\n',
                f"{root}/requirements.txt": b"example==1.0\n",
                f"{root}/package.json": b'{"name":"demo"}',
                f"{root}/tsconfig.json": b"{}",
                f"{root}/eslint.config.js": b"export default [];\n",
            }
        )
    return files


def _operation_counts(trees: int) -> tuple[int, int, int, int, int]:
    view = _CountingView(_composed_files(trees))
    python_result = python.detect_python(view, DetectionContext())
    javascript_result = javascript.detect_javascript(view, DetectionContext())
    return (
        view.path_catalog_calls,
        view.direct_child_calls,
        view.direct_child_items,
        len(python_result.components),
        len(javascript_result.components),
    )


@pytest.mark.verifies("TST041")
def test_composed_detector_catalog_operations_scale_linearly() -> None:
    small = _operation_counts(8)
    large = _operation_counts(16)

    assert small == (2, 9, 41, 8, 9)
    assert large == (2, 17, 81, 16, 17)


@pytest.mark.verifies("TST041")
def test_detector_registry_is_ordered_and_normalization_propagates_generic_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from slygentify._scan.detectors import BUILTIN_DETECTORS

    assert (
        generic.detect_generic,
        python.detect_python,
        javascript.detect_javascript,
    ) == BUILTIN_DETECTORS
    assert all(len(inspect.signature(detector).parameters) == 2 for detector in BUILTIN_DETECTORS)

    root = tmp_path / "registry"
    root.mkdir()
    observed: list[frozenset[str]] = []
    evidence = EvidenceCandidate(
        "manifest", "package.json", None, "Manifest.", "test", "test", "root"
    )
    component = ComponentCandidate(".", "package", (evidence_key(evidence),))

    def generic_detector(view: object, context: DetectionContext) -> DetectionResult:
        observed.append(context.generic_component_paths)
        return DetectionResult(evidence=(evidence,), components=(component,))

    def later_detector(view: object, context: DetectionContext) -> DetectionResult:
        observed.append(context.generic_component_paths)
        return DetectionResult()

    monkeypatch.setattr(normalization, "BUILTIN_DETECTORS", (generic_detector, later_detector))
    result = normalization._normalize(
        root, scan._Inspection({}, (), (), False), memory_limit=10_000
    )

    assert observed == [frozenset(), frozenset({"."})]
    assert result.components[0].path == "."
    assert InMemoryDetectorView({}).checkpoint() is False


@pytest.mark.verifies("TST041")
def test_composed_catalog_releases_index_memory_and_preserves_canonical_json(
    tmp_path: Path,
) -> None:
    view = scan._RepositoryView(_composed_files(2))
    indexed_memory = view._memory_consumed
    view.release_path_catalog()
    assert view._memory_consumed < indexed_memory

    root = tmp_path / "composed"
    root.mkdir()
    (root / ".git").mkdir()
    first = normalization._normalize(
        root, scan._Inspection(_composed_files(2), (), (), False), memory_limit=None
    )
    second = normalization._normalize(
        root, scan._Inspection(_composed_files(2), (), (), False), memory_limit=None
    )

    assert first.completion == "complete"
    assert dump_scan_json(first) == dump_scan_json(second)


@pytest.mark.verifies("TST041")
def test_indexed_ignore_rules_and_containment_select_nearest_parent() -> None:
    rules = scan._IgnoreRules()
    rules.add(".", "*.tmp\n")
    rules.add("services", "!keep.tmp\n")
    rules.add("services/api", "*.tmp\n")

    assert rules.ignored("other/file.tmp", is_dir=False)
    assert not rules.ignored("services/keep.tmp", is_dir=False)
    assert rules.ignored("services/api/keep.tmp", is_dir=False)
    assert nearest_ancestor("services/api/tests", {".", "services", "services/api"}) == (
        "services/api"
    )
    assert descendant_paths("services", ("other/a", "services/a", "services/api/b")) == (
        "services/a",
        "services/api/b",
    )


@pytest.mark.verifies("TST013")
def test_detector_checkpoints_stop_python_and_javascript_work() -> None:
    generic_expired = False
    python_expired = False
    javascript_expired = False
    for stop_after in range(0, 128):
        generic_view = _CheckpointView(_composed_files(2), stop_after)
        generic.detect_generic(generic_view, DetectionContext())
        generic_expired = generic_expired or generic_view.partial
        python_view = _CheckpointView(_composed_files(2), stop_after)
        python.detect_python(python_view, DetectionContext())
        python_expired = python_expired or python_view.partial
        javascript_view = _CheckpointView(_composed_files(2), stop_after)
        javascript.detect_javascript(javascript_view, DetectionContext())
        javascript_expired = javascript_expired or javascript_view.partial

    assert generic_expired
    assert python_expired
    assert javascript_expired


@pytest.mark.verifies("TST013")
def test_generic_detector_deadline_stops_artifact_and_component_composition() -> None:
    files = {"project.kicad_pcb": b"", "project.kicad_pro": b"{}"}

    artifact_view = _CheckpointView(files, stop_after=2)
    generic.detect_generic(artifact_view, DetectionContext())
    component_view = _CheckpointView(files, stop_after=3)
    generic.detect_generic(component_view, DetectionContext())

    assert artifact_view.partial
    assert component_view.partial


@pytest.mark.verifies("TST013")
def test_workspace_candidate_checkpoints_stop_descendant_matching() -> None:
    files = _composed_files(2)
    files["pyproject.toml"] = (
        b'[project]\nname = "workspace"\n[tool.uv.workspace]\nmembers = ["tree-*"]\n'
    )
    expired = False
    for stop_after in range(0, 128):
        view = _CheckpointView(files, stop_after)
        python.detect_python(view, DetectionContext())
        expired = expired or view.partial

    assert expired


def _scripted_inspection(root: Path, files: dict[str, bytes], stop_after: int) -> scan._Inspection:
    calls = 0

    def clock() -> float:
        nonlocal calls
        calls += 1
        return 0.0 if calls <= stop_after else 2.0

    return scan._Inspection(
        files,
        (),
        (),
        False,
        {},
        root,
        scan._Limits(max_elapsed_seconds=1),
        0,
        0.0,
        0,
        clock,
    )


@pytest.mark.verifies("TST013")
@pytest.mark.parametrize(
    ("detector", "files"),
    [
        (python.detect_python, _composed_files(2)),
        (javascript.detect_javascript, _composed_files(2)),
    ],
)
def test_scripted_deadline_exhausts_during_detector_work(
    detector: Callable[[scan._RepositoryView, DetectionContext], object],
    files: dict[str, bytes],
    tmp_path: Path,
) -> None:
    root = tmp_path / "detector-deadline"
    root.mkdir()
    view = scan._RepositoryView(_scripted_inspection(root, files, stop_after=2))

    detector(view, DetectionContext())

    assert view.partial
    assert view.skipped[0].reason == "max_elapsed_seconds"


@pytest.mark.verifies("TST013")
def test_scripted_deadline_keeps_normalization_references_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "normalization-deadline"
    root.mkdir()
    (root / ".git").mkdir()
    evidence = EvidenceCandidate(
        "manifest", "package.json", None, "A manifest is present.", "test", "test", "root"
    )
    key = evidence_key(evidence)
    detector_result = DetectionResult(
        evidence=(evidence,),
        components=(
            ComponentCandidate(".", "workspace", (key,)),
            ComponentCandidate("services", "package", (key,)),
            ComponentCandidate("services/api", "package", (key,)),
        ),
        findings=(
            FindingCandidate("test.finding", "verified", "services/api", "A finding.", (key,)),
        ),
        diagnostics=(
            DiagnosticCandidate(
                "test.diagnostic",
                "package.json",
                "A diagnostic.",
                False,
                disposition="problem",
            ),
        ),
        relationships=(
            RelationshipCandidate("workspace-member", ".", "services/api", "verified", (key,)),
        ),
    )
    monkeypatch.setattr(
        normalization, "BUILTIN_DETECTORS", (lambda view, context: detector_result,)
    )

    for stop_after in range(0, 24):
        result = normalization._normalize(
            root,
            _scripted_inspection(root, {}, stop_after),
            memory_limit=10_000,
        )
        assert result.completion == "partial"
        evidence_ids = {item.id for item in result.evidence}
        assert all(set(item.evidence_ids) <= evidence_ids for item in result.components)
        assert all(set(item.evidence_ids) <= evidence_ids for item in result.relationships)


@pytest.mark.verifies("TST013")
def test_deadline_stops_configured_component_normalization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "configuration-deadline"
    root.mkdir()
    (root / ".git").mkdir()
    evidence = EvidenceCandidate(
        "manifest", "package.json", None, "A manifest is present.", "test", "test", "root"
    )
    key = evidence_key(evidence)
    monkeypatch.setattr(
        normalization,
        "BUILTIN_DETECTORS",
        (
            lambda view, context: DetectionResult(
                evidence=(evidence,), components=(ComponentCandidate(".", "package", (key,)),)
            ),
        ),
    )
    configuration = EffectiveConfiguration(
        (),
        (ComponentDeclaration(".", None, None, "scan.components[0]"),),
        (),
        None,
    )

    result = normalization._normalize(
        root,
        _scripted_inspection(root, {}, stop_after=6),
        memory_limit=10_000,
        configuration=configuration,
    )

    assert result.completion == "partial"
    assert result.skipped_scopes[0].reason == "max_elapsed_seconds"


@pytest.mark.verifies("TST013")
def test_expired_configuration_loop_stops_before_component_declarations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "expired-configuration"
    root.mkdir()
    monkeypatch.setattr(normalization, "BUILTIN_DETECTORS", ())
    configuration = EffectiveConfiguration(
        (),
        (ComponentDeclaration(".", None, None, "scan.components[0]"),),
        (),
        None,
    )
    inspection = scan._Inspection(
        {},
        (),
        (),
        False,
        {},
        root,
        scan._Limits(max_elapsed_seconds=1),
        clock=lambda: 2.0,
    )

    result = normalization._normalize(
        root, inspection, memory_limit=10_000, configuration=configuration
    )

    assert result.completion == "partial"
    assert result.components == ()


@pytest.mark.verifies("TST013")
def test_elapsed_catalog_and_read_boundaries_are_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "catalog-boundaries"
    root.mkdir()
    entry = scan._Entry("manifest.json", False, True, False, False, 1, (1, 1))
    inspection = scan._Inspection(
        {},
        (scan._skip(".", "max_elapsed_seconds", 1, 1, omitted_scope="**"),),
        (),
        True,
        {"manifest.json": entry},
        root,
        scan._Limits(max_elapsed_seconds=1),
    )
    view = scan._RepositoryView(inspection)
    assert view.has_path("manifest.json")
    assert view.checkpoint()
    assert view.skipped == []

    duplicate_view = scan._RepositoryView({})
    duplicate_view._limits = scan._Limits(max_elapsed_seconds=1)
    duplicate_view._clock = lambda: 2.0
    duplicate_view.skipped.append(scan._skip(".", "max_elapsed_seconds", 1, 1, omitted_scope="**"))
    assert duplicate_view.checkpoint()
    assert len(duplicate_view.skipped) == 1

    timed_view = scan._RepositoryView(
        scan._Inspection(
            {},
            (),
            (),
            False,
            {"manifest.json": entry},
            root,
            scan._Limits(max_elapsed_seconds=1),
            clock=lambda: 0.0,
        )
    )
    monkeypatch.setattr(scan, "_read_file", lambda *args: (_ for _ in ()).throw(TimeoutError()))

    assert timed_view.read_bytes("manifest.json") is None
    assert timed_view.skipped[0].reason == "max_elapsed_seconds"


@pytest.mark.verifies("TST041")
def test_workspace_prefilter_discards_unrelated_indexed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {
        "packages/root/package.json": b'{"name":"root","workspaces":["apps/*"]}',
        "packages/root/apps/one/package.json": b'{"name":"one"}',
    }
    monkeypatch.setattr(javascript, "_descendant_paths", lambda root, paths: ("unrelated",))

    diagnostics = javascript.detect_javascript(
        scan._RepositoryView(files), DetectionContext()
    ).diagnostics

    assert any(item.code == "javascript.missing-workspace-member" for item in diagnostics)


@pytest.mark.verifies("TST013")
def test_normalization_deadline_returns_a_valid_partial_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "deadline"
    root.mkdir()
    (root / ".git").mkdir()
    evidence = EvidenceCandidate(
        "manifest", "package.json", None, "A manifest is present.", "test", "test", "root"
    )
    component = ComponentCandidate(".", "package", (evidence_key(evidence),))
    detector_result = DetectionResult(evidence=(evidence,), components=(component,))
    monkeypatch.setattr(
        normalization, "BUILTIN_DETECTORS", (lambda view, context: detector_result,)
    )
    ticks = iter((0.0, 0.0, 0.0, 2.0))

    def clock() -> float:
        return next(ticks)

    inspection = scan._Inspection(
        {}, (), (), False, {}, root, scan._Limits(max_elapsed_seconds=1), 0, 0.0, 0, clock
    )

    result = normalization._normalize(root, inspection, memory_limit=10_000)

    assert result.completion == "partial"
    assert {(item.reason, item.scope, item.omitted_scope) for item in result.skipped_scopes} == {
        ("max_elapsed_seconds", ".", "**")
    }
    assert result.components == ()
    assert result.evidence[0].location == ".git"
