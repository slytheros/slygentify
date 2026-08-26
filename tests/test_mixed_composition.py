"""Mixed repository composition and generic engineering evidence tests."""

from __future__ import annotations

import json
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import slygentify._scan.detectors.generic as generic
import slygentify._scan.kernel as kernel
import slygentify._scan.normalization as normalization
from slygentify import (
    Component,
    ComponentRelationship,
    Finding,
    ScanResult,
    dump_scan_json,
    load_scan_json,
    scan_repository,
)
from slygentify._presentation import render_scan_report
from slygentify._scan.contracts import (
    ComponentCandidate,
    DetectionContext,
    DetectionResult,
    EvidenceCandidate,
    RelationshipCandidate,
)
from slygentify._scan.detectors._support import evidence_key
from tests.scan_samples import sample_mapping, sample_result


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    return repository


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value))


@pytest.mark.verifies("TST030", "TST031", "TST032")
def test_mixed_repository_preserves_facets_relationships_and_stable_ids(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write(
        repository / "CMakeLists.txt",
        "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\nproject(firmware)\n",
    )
    _write(
        repository / "pyproject.toml",
        '[tool.uv.workspace]\nmembers = ["python"]\n',
    )
    _write(repository / "python" / "pyproject.toml", '[project]\nname = "tools"\n')
    _write_json(repository / "package.json", {"private": True, "workspaces": ["web"]})
    _write_json(repository / "web" / "package.json", {"name": "web"})
    _write_json(repository / "hardware" / "controller.kicad_pro", {})
    _write(repository / "hardware" / "controller.kicad_sch", "(kicad_sch)")
    _write(repository / "hardware" / "controller.kicad_pcb", "(kicad_pcb)")

    first = scan_repository(repository)
    second = scan_repository(repository)
    by_path = {component.path: component for component in first.components}

    assert first == second
    assert dump_scan_json(first) == dump_scan_json(second)
    assert by_path["."].ecosystem == "mixed"
    assert by_path["."].ecosystems == ("generic", "javascript", "python")
    assert by_path["hardware"].ecosystems == ("generic",)
    assert by_path["python"].ecosystems == ("python",)
    assert by_path["web"].ecosystems == ("javascript",)
    relationships = {
        (
            item.kind,
            next(path for path, component in by_path.items() if component.id == item.source_id),
            next(path for path, component in by_path.items() if component.id == item.target_id),
        )
        for item in first.relationships
    }
    assert relationships == {
        ("contains", ".", "hardware"),
        ("contains", ".", "python"),
        ("contains", ".", "web"),
        ("workspace-member", ".", "python"),
        ("workspace-member", ".", "web"),
    }
    hardware_locations = {
        item.location for item in first.evidence if item.id in by_path["hardware"].evidence_ids
    }
    assert hardware_locations == {
        "hardware/controller.kicad_pcb",
        "hardware/controller.kicad_pro",
        "hardware/controller.kicad_sch",
    }


@pytest.mark.verifies("TST030")
def test_auxiliary_component_roles_are_path_segment_based_and_evidence_backed(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _write(repository / "test" / "pyproject.toml", '[project]\nname = "test-package"\n')
    _write_json(repository / "tests" / "package.json", {"name": "tests-package"})
    _write(repository / "example" / "Cargo.toml", '[package]\nname = "example-package"\n')
    _write(repository / "examples" / "CMakeLists.txt", "project(examples)\n")
    _write(repository / "docs" / "pom.xml", "<project/>")
    _write(repository / "template" / "pyproject.toml", '[project]\nname = "template-package"\n')
    _write_json(repository / "template" / "package.json", {"name": "template-package"})
    _write(repository / "templates" / "go.mod", "module example.test/templates\n")
    _write(repository / "testing" / "pyproject.toml", '[project]\nname = "testing-package"\n')
    _write_json(repository / "examples2" / "package.json", {"name": "examples2-package"})
    _write(repository / "DocSExtras" / "Cargo.toml", '[package]\nname = "capitalized-package"\n')

    first = scan_repository(repository)
    second = scan_repository(repository)
    by_path = {component.path: component for component in first.components}
    auxiliary_paths = {"test", "tests", "example", "examples", "docs", "template", "templates"}

    assert first == second
    assert {
        path for path, component in by_path.items() if component.role == "auxiliary"
    } == auxiliary_paths
    assert {path for path, component in by_path.items() if component.role == "unknown"} == {
        "testing",
        "examples2",
        "DocSExtras",
    }
    assert by_path["template"].ecosystem == "mixed"
    assert by_path["test"].id == normalization._id("component", "test")

    findings = {
        item.subject_id: item
        for item in first.findings
        if item.code == "composition.auxiliary-component"
    }
    assert set(findings) == {by_path[path].id for path in auxiliary_paths}
    for path in auxiliary_paths:
        component = by_path[path]
        finding = findings[component.id]
        assert finding.classification == "inferred"
        assert finding.summary == "Path convention indicates an auxiliary component."
        assert finding.evidence_ids == component.evidence_ids


@pytest.mark.verifies("TST030")
def test_auxiliary_component_role_does_not_fold_path_segment_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    evidence = EvidenceCandidate(
        "manifest", "DocS/Cargo.toml", "package", "Package.", "parse", "rule", "key"
    )
    component = ComponentCandidate("DocS", "package", (evidence_key(evidence),))
    monkeypatch.setattr(
        normalization,
        "BUILTIN_DETECTORS",
        (lambda view, context: DetectionResult(evidence=(evidence,), components=(component,)),),
    )

    result = normalization._normalize(
        root, kernel._Inspection({}, (), (), False), memory_limit=10_000
    )

    assert result.components[0].role == "unknown"
    assert "composition.auxiliary-component" not in {item.code for item in result.findings}


@pytest.mark.verifies("TST030")
@pytest.mark.parametrize(
    ("files", "source", "target"),
    [
        (
            {
                "Cargo.toml": b'[workspace]\nmembers = ["member"]\n',
                "member/Cargo.toml": b"[package]\nname = 'member'\n",
            },
            ".",
            "member",
        ),
        (
            {"go.work": b"use ./member\n", "member/go.mod": b"module example.test/member\n"},
            ".",
            "member",
        ),
        (
            {
                "pom.xml": b"<project><modules><module>member</module></modules></project>",
                "member/pom.xml": b"<project/>",
            },
            ".",
            "member",
        ),
    ],
)
def test_generic_workspaces_emit_membership_relationships(
    tmp_path: Path, files: dict[str, bytes], source: str, target: str
) -> None:
    repository = _repository(tmp_path)
    for path, data in files.items():
        destination = repository / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    result = scan_repository(repository)
    by_path = {component.path: component.id for component in result.components}

    assert any(
        item.kind == "workspace-member"
        and item.source_id == by_path[source]
        and item.target_id == by_path[target]
        and item.classification == "verified"
        and item.evidence_ids
        for item in result.relationships
    )


@pytest.mark.verifies("TST030", "TST032")
def test_generic_workspace_relationship_parser_handles_bounded_edge_cases() -> None:
    detected = generic.detect_generic(
        kernel._RepositoryView(
            {
                "Cargo.toml": b"[workspace]\nmembers = [1]\n",
                "go.work": b"use (\n\n  ./member\n)\n",
                "member/go.mod": b"module example.test/member\n",
            }
        ),
        DetectionContext(),
    )

    assert detected.findings == ()
    assert "inspection.invalid-workspace" in {item.code for item in detected.diagnostics}
    assert [(item.source_path, item.target_path) for item in detected.relationships] == [
        (".", "member")
    ]
    evidence = generic._evidence(
        "go.work", "use", "Go work file declares a workspace boundary.", "go-workspace"
    )
    assert (
        generic._generic_workspace_relationships("go.work", b"\xff", frozenset(), [evidence]) == ()
    )


@pytest.mark.verifies("TST030", "TST032", "TST046")
def test_overlapping_workspace_parents_recommend_narrowing_declarations(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {"workspaces": ["groups/*", "groups/*/*"]})
    _write_json(
        repository / "groups" / "nested" / "package.json",
        {"workspaces": ["member"]},
    )
    _write_json(repository / "groups" / "nested" / "member" / "package.json", {})

    result = scan_repository(repository)
    target_id = next(item.id for item in result.components if item.path == "groups/nested/member")
    parents = {
        item.source_id
        for item in result.relationships
        if item.kind == "workspace-member" and item.target_id == target_id
    }
    diagnostic = next(
        item
        for item in result.diagnostics
        if item.code == "composition.overlapping-workspace-membership"
    )

    assert len(parents) == 2
    assert result.completion == "complete"
    assert "All relationships were retained" in diagnostic.message
    assert "Next: narrow or exclude the overlapping workspace declarations" in diagnostic.message
    assert "[[scan.components]]" not in diagnostic.message
    assert "not loaded by scan yet" not in diagnostic.message


@pytest.mark.verifies("TST031", "TST032", "TST046")
def test_cmake_and_esp_idf_markers_are_generic_without_execution(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write(repository / "CMakeLists.txt", "project(firmware)\n")
    _write(
        repository / "component" / "CMakeLists.txt",
        "idf_component_register(SRCS main.c)\n",
    )
    _write(repository / "ambiguous" / "CMakeLists.txt", "add_library(example main.c)\n")
    invalid = repository / "invalid" / "CMakeLists.txt"
    invalid.parent.mkdir(parents=True)
    invalid.write_bytes(b"\xff")

    result = scan_repository(repository)
    by_path = {component.path: component for component in result.components}

    assert by_path["."].ecosystems == ("generic",)
    assert by_path["component"].ecosystems == ("generic",)
    assert result.completion == "partial"
    assert {item.code for item in result.diagnostics} >= {
        "composition.ambiguous-boundary",
        "inspection.invalid-manifest",
    }
    ambiguous = next(
        item for item in result.diagnostics if item.location == "ambiguous/CMakeLists.txt"
    )
    assert 'path = "ambiguous"' in ambiguous.message
    assert "Next:" in ambiguous.message


@pytest.mark.verifies("TST031", "TST032")
def test_kicad_artifact_without_project_is_unknown_and_malformed_projects_are_partial(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _write(repository / "orphan" / "board.kicad_pcb", "(kicad_pcb)")
    _write(repository / "broken" / "board.kicad_pro", "[]")
    _write(repository / "duplicate" / "board.kicad_pro", '{"a": 1, "a": 2}')

    result = scan_repository(repository)

    assert result.completion == "partial"
    assert "generic.engineering-boundary.unknown" in {item.code for item in result.findings}
    diagnostic = next(
        item
        for item in result.diagnostics
        if item.code == "composition.ambiguous-boundary"
        and item.location == "orphan/board.kicad_pcb"
    )
    assert '[[scan.components]] with path = "orphan"' in diagnostic.message
    assert sum(item.code == "inspection.invalid-manifest" for item in result.diagnostics) == 2


@pytest.mark.verifies("TST030")
def test_legacy_documents_derive_facets_and_default_relationships() -> None:
    mapping = sample_mapping()
    component = mapping["components"]
    assert isinstance(component, list) and isinstance(component[0], dict)
    component[0].pop("ecosystems")
    mapping.pop("relationships")

    loaded = load_scan_json(json.dumps(mapping))

    assert loaded.components[0].ecosystems == ("generic",)
    assert loaded.relationships == ()


@pytest.mark.verifies("TST030")
def test_relationship_models_enforce_summary_and_graph_integrity() -> None:
    with pytest.raises(ValueError, match="summarize"):
        Component(
            id="component",
            path=".",
            ecosystem="python",
            ecosystems=("javascript", "python"),
            kind="package",
            evidence_ids=(),
        )
    with pytest.raises(ValueError, match="endpoints"):
        ComponentRelationship(
            id="relationship",
            kind="contains",
            source_id="same",
            target_id="same",
            classification="inferred",
            evidence_ids=(),
        )
    with pytest.raises(ValueError, match="classification"):
        ComponentRelationship(
            id="relationship",
            kind="contains",
            source_id="source",
            target_id="target",
            classification="invalid",  # type: ignore[arg-type]
            evidence_ids=(),
        )
    scan = sample_result()
    dangling = ComponentRelationship(
        id="relationship",
        kind="contains",
        source_id=scan.components[0].id,
        target_id="missing",
        classification="inferred",
        evidence_ids=(),
    )
    with pytest.raises(ValueError, match="dangling relationship"):
        replace(scan, relationships=(dangling,))
    child = replace(scan.components[0], id="component_child", path="child")
    contains = replace(
        dangling,
        id="contains",
        target_id=child.id,
    )
    workspace = replace(contains, id="workspace", kind="workspace-member")
    with pytest.raises(ValueError, match="canonical order"):
        replace(
            scan,
            components=tuple(
                sorted((*scan.components, child), key=lambda item: (item.id, item.path))
            ),
            relationships=(workspace, contains),
        )


@pytest.mark.verifies("TST030", "TST032")
def test_unresolved_and_budget_rejected_relationships_are_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    evidence = EvidenceCandidate(
        "manifest", "Cargo.toml", "package", "Package.", "parse", "rule", "key"
    )
    component = ComponentCandidate(".", "package", (evidence_key(evidence),))
    unresolved = RelationshipCandidate(
        "workspace-member", ".", "missing", "verified", (evidence_key(evidence),)
    )
    monkeypatch.setattr(
        normalization,
        "BUILTIN_DETECTORS",
        (
            lambda view, context: DetectionResult(
                evidence=(evidence,), components=(component,), relationships=(unresolved,)
            ),
        ),
    )

    unresolved_result = normalization._normalize(
        root, kernel._Inspection({}, (), (), False), memory_limit=10_000
    )

    assert unresolved_result.completion == "partial"
    assert "composition.unresolved-relationship" in {
        item.code for item in unresolved_result.diagnostics
    }

    child_evidence = replace(evidence, location="child/Cargo.toml", semantic_key="child")
    child = ComponentCandidate("child", "package", (evidence_key(child_evidence),))
    monkeypatch.setattr(
        normalization,
        "BUILTIN_DETECTORS",
        (
            lambda view, context: DetectionResult(
                evidence=(evidence, child_evidence), components=(component, child)
            ),
        ),
    )
    monkeypatch.setattr(
        normalization,
        "_record_size",
        lambda value: 10_000 if isinstance(value, ComponentRelationship) else 1,
    )
    limited = normalization._normalize(
        root, kernel._Inspection({}, (), (), False), memory_limit=100
    )

    assert limited.completion == "partial"
    assert limited.relationships == ()
    assert "inspection.max-memory-bytes" in {item.code for item in limited.diagnostics}

    auxiliary = ComponentCandidate("tests", "package", (evidence_key(evidence),))
    monkeypatch.setattr(
        normalization,
        "BUILTIN_DETECTORS",
        (lambda view, context: DetectionResult(evidence=(evidence,), components=(auxiliary,)),),
    )
    monkeypatch.setattr(
        normalization,
        "_record_size",
        lambda value: 10_000 if isinstance(value, Finding) else 1,
    )
    auxiliary_limited = normalization._normalize(
        root, kernel._Inspection({}, (), (), False), memory_limit=100
    )

    assert auxiliary_limited.completion == "partial"
    assert auxiliary_limited.components[0].role == "auxiliary"
    assert not auxiliary_limited.findings
    assert "inspection.max-memory-bytes" in {item.code for item in auxiliary_limited.diagnostics}


@pytest.mark.verifies("TST030")
def test_text_report_lists_relationship_paths() -> None:
    scan = sample_result()
    child = replace(
        scan.components[0],
        id="component_child",
        path="child",
    )
    relationship = ComponentRelationship(
        id="relationship",
        kind="contains",
        source_id=scan.components[0].id,
        target_id=child.id,
        classification="inferred",
        evidence_ids=scan.components[0].evidence_ids,
    )
    result: ScanResult = replace(
        scan,
        components=tuple(sorted((*scan.components, child), key=lambda item: (item.id, item.path))),
        relationships=(relationship,),
    )

    stream = StringIO()
    render_scan_report(
        result,
        Path("repository"),
        Console(file=stream, force_terminal=False, width=160),
    )
    output = stream.getvalue()

    assert "Relationships (1)" in output
    assert "INFERRED contains: . -> child" in output
