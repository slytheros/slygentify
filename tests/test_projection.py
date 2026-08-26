"""Tests for task-scoped, evidence-closed scan projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from importlib import resources as importlib_resources

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

import slygentify._projection_serialization as projection_serialization
from slygentify import (
    Component,
    ComponentRelationship,
    Diagnostic,
    Evidence,
    Finding,
    ProjectionNavigation,
    ProjectionOmission,
    ProjectionScope,
    Repository,
    ScanError,
    ScanProjection,
    ScanResult,
    ScanValidationError,
    SkippedScope,
    dump_scan_json,
    dump_scan_projection_json,
    load_scan_json,
    load_scan_projection_json,
    project_scan,
    scan_projection_json_schema,
    validate_scan_projection,
)


def _evidence(identifier: str, location: str) -> Evidence:
    return Evidence(
        id=identifier,
        source_kind="manifest",
        location=location,
        locator=None,
        observation=f"Observed {location}.",
        verification_method="local parsing",
    )


def _result(*, completion: str = "partial") -> ScanResult:
    evidence = tuple(
        sorted(
            (
                _evidence("e_api", "apps/api/pyproject.toml"),
                _evidence("e_apps", "apps/package.json"),
                _evidence("e_arch", "apps/api/src/main.py"),
                _evidence("e_auto", ".github/workflows/test.yml"),
                _evidence("e_boundary", "apps/api/pyproject.toml"),
                _evidence("e_lib", "lib/package.json"),
                _evidence("e_rel", "workspace.toml"),
                _evidence("e_repo", ".git"),
                _evidence("e_root", "pyproject.toml"),
                _evidence("e_web", "apps/web/package.json"),
                _evidence("e_work", "apps/api/pyproject.toml"),
            ),
            key=lambda item: (item.id, item.location),
        )
    )
    repository = Repository(id="repo", root=".", kind="git", evidence_ids=("e_repo",))
    components = tuple(
        sorted(
            (
                Component(
                    id="c_api",
                    path="apps/api",
                    ecosystem="python",
                    kind="application",
                    evidence_ids=("e_api",),
                ),
                Component(
                    id="c_apps",
                    path="apps",
                    ecosystem="javascript",
                    kind="workspace",
                    evidence_ids=("e_apps",),
                ),
                Component(
                    id="c_lib",
                    path="lib",
                    ecosystem="javascript",
                    kind="package",
                    evidence_ids=("e_lib",),
                ),
                Component(
                    id="c_root",
                    path=".",
                    ecosystem="python",
                    kind="workspace",
                    evidence_ids=("e_root",),
                ),
                Component(
                    id="c_web",
                    path="apps/web",
                    ecosystem="javascript",
                    kind="application",
                    evidence_ids=("e_web",),
                ),
            ),
            key=lambda item: (item.id, item.path),
        )
    )
    relationships = tuple(
        sorted(
            (
                ComponentRelationship(
                    id="r_apps_api",
                    kind="contains",
                    source_id="c_apps",
                    target_id="c_api",
                    classification="verified",
                    evidence_ids=("e_rel",),
                ),
                ComponentRelationship(
                    id="r_apps_web",
                    kind="contains",
                    source_id="c_apps",
                    target_id="c_web",
                    classification="verified",
                    evidence_ids=("e_rel",),
                ),
                ComponentRelationship(
                    id="r_root_apps",
                    kind="contains",
                    source_id="c_root",
                    target_id="c_apps",
                    classification="verified",
                    evidence_ids=("e_rel",),
                ),
            ),
            key=lambda item: (item.kind, item.source_id, item.target_id, item.id),
        )
    )
    findings = tuple(
        sorted(
            (
                Finding(
                    id="f_arch",
                    code="python.entrypoint",
                    classification="verified",
                    subject_id="c_api",
                    summary="The application entry point is declared.",
                    evidence_ids=("e_arch",),
                ),
                Finding(
                    id="f_auto",
                    code="python.ci.command",
                    classification="verified",
                    subject_id="c_api",
                    summary="CI declares a test task.",
                    evidence_ids=("e_auto",),
                ),
                Finding(
                    id="f_boundary",
                    code="python.unsupported-runtime",
                    classification="unknown",
                    subject_id="c_api",
                    summary="A runtime boundary is unknown.",
                    evidence_ids=("e_boundary",),
                ),
                Finding(
                    id="f_identity",
                    code="repository.identity",
                    classification="verified",
                    subject_id="repo",
                    summary="The repository is Git-backed.",
                    evidence_ids=("e_repo",),
                ),
                Finding(
                    id="f_irrelevant",
                    code="javascript.unsupported-tool",
                    classification="unknown",
                    subject_id="c_lib",
                    summary="A library boundary is unknown.",
                    evidence_ids=("e_lib",),
                ),
                Finding(
                    id="f_runtime",
                    code="python.runtime",
                    classification="verified",
                    subject_id="c_root",
                    summary="Python is declared.",
                    evidence_ids=("e_root",),
                ),
                Finding(
                    id="f_work",
                    code="python.command.test",
                    classification="verified",
                    subject_id="c_api",
                    summary="A local test task is declared.",
                    evidence_ids=("e_work",),
                ),
            ),
            key=lambda item: (item.code, item.subject_id, item.id),
        )
    )
    diagnostics = tuple(
        sorted(
            (
                Diagnostic(
                    id="d_api",
                    code="python.conflict",
                    subject_id="c_api",
                    location="apps/api",
                    message="API metadata conflicts.",
                    evidence_ids=("e_boundary",),
                ),
                Diagnostic(
                    id="d_lib",
                    code="javascript.conflict",
                    subject_id="c_lib",
                    location="lib",
                    message="Library metadata conflicts.",
                    evidence_ids=("e_lib",),
                ),
            ),
            key=lambda item: (item.code, item.subject_id or item.location or "", item.id),
        )
    )
    skipped = (
        SkippedScope(
            scope="apps/api",
            reason="configured-ignore",
            effective_limit=None,
            consumed=None,
            omitted_scope="apps/api/**",
        ),
        SkippedScope(
            scope="lib/vendor",
            reason="cache-or-dependency",
            effective_limit=None,
            consumed=None,
            omitted_scope="lib/vendor/**",
        ),
    )
    return ScanResult(
        schema_version=1,
        producer_version="0.1.0",
        completion=completion,  # type: ignore[arg-type]
        repository=repository,
        components=components,
        relationships=relationships,
        evidence=evidence,
        findings=findings,
        diagnostics=diagnostics,
        skipped_scopes=skipped if completion == "partial" else (),
    )


@pytest.mark.verifies("TST042")
def test_projection_selects_owner_ancestors_children_relationships_and_boundaries() -> None:
    source = _result()

    api = project_scan(source, scope="apps/api/planned.py", max_bytes="unlimited")

    assert api.sections == ("orientation", "boundaries")
    assert api.scope == ProjectionScope(
        requested_path="apps/api/planned.py",
        matched_component_id="c_api",
        matched_component_path="apps/api",
    )
    assert api.navigation == ProjectionNavigation(
        ancestors=("c_root", "c_apps"), owner="c_api", children=()
    )
    assert {item.id for item in api.components} == {"c_root", "c_apps", "c_api"}
    assert {item.id for item in api.relationships} == {"r_root_apps", "r_apps_api"}
    assert {item.id for item in api.findings} == {
        "f_identity",
        "f_runtime",
        "f_boundary",
    }
    assert {item.id for item in api.diagnostics} == {"d_api"}
    assert {item.scope for item in api.skipped_scopes} == {"apps/api"}
    assert not api.omissions
    assert api.source_completion == "partial"
    assert api.source_scan_sha256 == hashlib.sha256(dump_scan_json(source)).hexdigest()

    apps = project_scan(source, scope="apps", max_bytes="unlimited")
    assert apps.navigation == ProjectionNavigation(
        ancestors=("c_root",), owner="c_apps", children=("c_api", "c_web")
    )
    assert {item.id for item in apps.components} == {"c_root", "c_apps", "c_api", "c_web"}
    assert {item.id for item in apps.relationships} == {
        "r_root_apps",
        "r_apps_api",
        "r_apps_web",
    }


@pytest.mark.verifies("TST042")
def test_projection_handles_unmatched_paths_and_exact_section_selection() -> None:
    source = _result(completion="complete")
    without_root = replace(
        source,
        components=tuple(item for item in source.components if item.id != "c_root"),
        relationships=tuple(
            item
            for item in source.relationships
            if item.source_id != "c_root" and item.target_id != "c_root"
        ),
        findings=tuple(item for item in source.findings if item.subject_id != "c_root"),
    )

    unmatched = project_scan(without_root, scope="planned/new.py", max_bytes="unlimited")

    assert unmatched.scope.matched_component_id is None
    assert unmatched.navigation == ProjectionNavigation(
        ancestors=(), owner=None, children=("c_apps", "c_lib")
    )
    assert {item.path for item in unmatched.components} == {"apps", "lib"}
    assert {item.id for item in unmatched.findings} == {"f_identity"}

    selected = project_scan(
        source,
        scope="apps/api",
        sections=("architecture", "workflows", "architecture"),
        max_bytes="unlimited",
    )
    assert selected.sections == ("workflows", "architecture")
    assert selected.navigation.children == ()
    assert {item.id for item in selected.findings} == {"f_work", "f_arch"}
    assert not selected.relationships
    assert not selected.diagnostics
    assert not selected.skipped_scopes


@pytest.mark.verifies("TST042")
def test_projection_default_limit_is_evidence_closed_and_reports_omissions() -> None:
    source = _result()
    extras = tuple(
        Finding(
            id=f"f_extra_{index:02d}",
            code=f"python.unsupported.{index:02d}",
            classification="unknown",
            subject_id="c_api",
            summary="Unknown boundary. " + "x" * 300,
            evidence_ids=("e_boundary",),
        )
        for index in range(40)
    )
    bloated = replace(
        source,
        findings=tuple(
            sorted(
                (*source.findings, *extras), key=lambda item: (item.code, item.subject_id, item.id)
            )
        ),
    )

    bounded = project_scan(bloated, scope="apps/api")
    unlimited = project_scan(bloated, scope="apps/api", max_bytes="unlimited")

    assert len(dump_scan_projection_json(bounded)) <= 8192
    assert len(dump_scan_projection_json(unlimited)) > 8192
    omitted_findings = next(
        item.count
        for item in bounded.omissions
        if item.section == "boundaries" and item.record_kind == "finding"
    )
    unlimited_boundaries = sum(
        item.classification in {"unknown", "recommended"} for item in unlimited.findings
    )
    bounded_boundaries = sum(
        item.classification in {"unknown", "recommended"} for item in bounded.findings
    )
    assert omitted_findings == unlimited_boundaries - bounded_boundaries
    evidence_ids = {item.id for item in bounded.evidence}
    referenced = {
        *bounded.repository.evidence_ids,
        *(identifier for item in bounded.components for identifier in item.evidence_ids),
        *(identifier for item in bounded.relationships for identifier in item.evidence_ids),
        *(identifier for item in bounded.findings for identifier in item.evidence_ids),
        *(identifier for item in bounded.diagnostics for identifier in item.evidence_ids),
    }
    assert referenced <= evidence_ids
    with pytest.raises(ScanError, match="Required map context does not fit"):
        project_scan(source, max_bytes=1)

    navigation_source = replace(
        source,
        completion="complete",
        findings=(),
        diagnostics=(),
        skipped_scopes=(),
        relationships=(),
    )
    full_root = project_scan(navigation_source, max_bytes="unlimited")
    capped_root = project_scan(
        navigation_source,
        max_bytes=len(dump_scan_projection_json(full_root)) - 1,
    )
    assert set(capped_root.navigation.children) < set(full_root.navigation.children)
    assert any(
        item.section == "orientation" and item.record_kind == "component"
        for item in capped_root.omissions
    )


@pytest.mark.verifies("TST042")
@pytest.mark.parametrize(
    "scope",
    ["", "/absolute", "a\\b", "a\x00b", "./a", "a/../b", "C:a"],
)
def test_projection_rejects_unsafe_scopes(scope: str) -> None:
    with pytest.raises(ScanError, match="repository-relative POSIX"):
        project_scan(_result(), scope=scope)


@pytest.mark.verifies("TST042")
def test_projection_rejects_invalid_sections_and_limits() -> None:
    source = _result()
    for sections in ("orientation", (), ("unknown",)):
        with pytest.raises(ScanError, match="section"):
            project_scan(source, sections=sections)
    for limit in (0, True, "all"):
        with pytest.raises(ScanError, match="max_bytes"):
            project_scan(source, max_bytes=limit)  # type: ignore[arg-type]


@pytest.mark.verifies("TST042")
def test_projection_json_round_trip_schema_and_forward_compatibility() -> None:
    projection = project_scan(_result(), scope="apps/api", max_bytes="unlimited")
    data = dump_scan_projection_json(projection)

    assert data.endswith(b"\n")
    assert load_scan_projection_json(data) == projection
    assert validate_scan_projection(projection) == projection
    mapping = json.loads(data)
    Draft202012Validator(scan_projection_json_schema()).validate(mapping)
    mapping["future_projection_field"] = {"retained_by_future_producers": True}
    mapping["repository"]["future_repository_field"] = 1
    mapping["navigation"]["future_navigation_field"] = 1
    assert load_scan_projection_json(json.dumps(mapping)) == projection
    with pytest.raises(ScanValidationError):
        load_scan_json(data)
    with pytest.raises(ScanValidationError, match="only ScanProjection"):
        dump_scan_projection_json(_result())  # type: ignore[arg-type]


@pytest.mark.verifies("TST042")
@pytest.mark.parametrize(
    "data",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        "\ufeff{}",
        "{",
        '{"schema_version":1,"schema_version":1}',
        '{"schema_version":NaN}',
        1,
    ],
)
def test_projection_json_rejects_malformed_untrusted_documents(data: object) -> None:
    with pytest.raises(ScanValidationError):
        load_scan_projection_json(data)  # type: ignore[arg-type]


@pytest.mark.verifies("TST042")
def test_projection_public_models_reject_inconsistent_values() -> None:
    valid = project_scan(_result(), max_bytes="unlimited")
    apps = project_scan(_result(), scope="apps", max_bytes="unlimited")
    scoped = project_scan(_result(), scope="apps/api", max_bytes="unlimited")
    with pytest.raises(ValueError, match="present together"):
        ProjectionScope(
            requested_path=".", matched_component_id="c_root", matched_component_path=None
        )
    with pytest.raises(ValueError, match="collections must be tuples"):
        ProjectionNavigation(ancestors=[], owner=None, children=())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be unique"):
        ProjectionNavigation(ancestors=("c_root", "c_root"), owner=None, children=())
    with pytest.raises(ValueError, match="roles must not overlap"):
        ProjectionNavigation(ancestors=("c_root",), owner="c_root", children=())
    with pytest.raises(ValueError, match="positive integer"):
        ProjectionOmission(section="orientation", record_kind="finding", count=0)
    with pytest.raises(ValueError, match="section is not supported"):
        ProjectionOmission(
            section="future",  # type: ignore[arg-type]
            record_kind="finding",
            count=1,
        )
    with pytest.raises(ValueError, match="schema versions"):
        replace(valid, schema_version=2)
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        replace(valid, source_scan_sha256="A" * 64)
    with pytest.raises(ValueError, match="source_completion"):
        replace(valid, source_completion="future")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ProjectionScope"):
        replace(valid, scope=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ProjectionNavigation"):
        replace(valid, navigation=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty tuple"):
        replace(valid, sections=())
    with pytest.raises(ValueError, match="section is not supported"):
        replace(valid, sections=("future",))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="canonical"):
        replace(valid, sections=("boundaries", "orientation"))
    with pytest.raises(ValueError, match="collections must be tuples"):
        replace(valid, components=list(valid.components))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="components are not in canonical order"):
        replace(valid, components=tuple(reversed(valid.components)))
    with pytest.raises(ValueError, match="relationships are not in canonical order"):
        replace(apps, relationships=tuple(reversed(apps.relationships)))
    with pytest.raises(ValueError, match="findings are not in canonical order"):
        replace(valid, findings=tuple(reversed(valid.findings)))
    with pytest.raises(ValueError, match="diagnostics are not in canonical order"):
        replace(valid, diagnostics=tuple(reversed(valid.diagnostics)))
    with pytest.raises(ValueError, match="skipped scopes are not in canonical order"):
        replace(valid, skipped_scopes=tuple(reversed(valid.skipped_scopes)))
    with pytest.raises(ValueError, match="evidence is not in canonical order"):
        replace(valid, evidence=tuple(reversed(valid.evidence)))
    wrong_omissions = (
        ProjectionOmission(section="boundaries", record_kind="finding", count=1),
        ProjectionOmission(section="orientation", record_kind="component", count=1),
    )
    with pytest.raises(ValueError, match="omissions are not in canonical order"):
        replace(valid, omissions=wrong_omissions)
    with pytest.raises(ValueError, match="section was not selected"):
        replace(
            valid,
            sections=("orientation",),
            omissions=(ProjectionOmission(section="boundaries", record_kind="finding", count=1),),
        )
    duplicate_evidence = replace(valid.evidence[0], id=valid.components[0].id)
    with pytest.raises(ValueError, match="identifiers must be unique"):
        replace(valid, evidence=(duplicate_evidence, *valid.evidence[1:]))
    with pytest.raises(ValueError, match="dangling evidence"):
        replace(valid, evidence=tuple(item for item in valid.evidence if item.id != "e_repo"))
    with pytest.raises(ValueError, match="dangling finding subject"):
        replace(valid, findings=(replace(valid.findings[0], subject_id="missing"),))
    with pytest.raises(ValueError, match="dangling diagnostic subject"):
        replace(valid, diagnostics=(replace(valid.diagnostics[0], subject_id="missing"),))
    with pytest.raises(ValueError, match="dangling relationship endpoint"):
        replace(
            apps,
            relationships=(replace(apps.relationships[0], source_id="missing"),),
        )
    with pytest.raises(ValueError, match="matched component is absent"):
        replace(
            valid,
            scope=ProjectionScope(
                requested_path=".",
                matched_component_id="missing",
                matched_component_path=".",
            ),
        )
    with pytest.raises(ValueError, match="path does not match"):
        replace(
            valid,
            scope=ProjectionScope(
                requested_path=".",
                matched_component_id=valid.scope.matched_component_id,
                matched_component_path="apps",
            ),
        )
    with pytest.raises(ValueError, match="owner does not match"):
        replace(valid, navigation=replace(valid.navigation, owner=None))
    with pytest.raises(ValueError, match="dangling component"):
        replace(valid, navigation=replace(valid.navigation, children=("missing",)))
    with pytest.raises(ValueError, match="require the orientation"):
        replace(
            apps,
            sections=("boundaries",),
            navigation=replace(apps.navigation, children=("c_api",)),
            relationships=(),
            findings=tuple(
                item for item in apps.findings if item.classification in {"unknown", "recommended"}
            ),
        )
    with pytest.raises(ValueError, match="ancestors are not canonical"):
        replace(scoped, navigation=replace(scoped.navigation, ancestors=("c_root",)))
    with pytest.raises(ValueError, match="cannot have ancestors"):
        replace(
            valid,
            scope=ProjectionScope(
                requested_path="planned", matched_component_id=None, matched_component_path=None
            ),
            navigation=ProjectionNavigation(ancestors=("c_root",), owner=None, children=()),
        )
    with pytest.raises(ValueError, match="children are not canonical"):
        replace(apps, navigation=replace(apps.navigation, children=("c_web", "c_api")))
    lib_component = next(item for item in _result().components if item.id == "c_lib")
    lib_evidence = next(item for item in _result().evidence if item.id == "e_lib")
    apps_with_lib = replace(
        apps,
        components=tuple(
            sorted((*apps.components, lib_component), key=lambda item: (item.id, item.path))
        ),
        evidence=tuple(
            sorted((*apps.evidence, lib_evidence), key=lambda item: (item.id, item.location))
        ),
    )
    with pytest.raises(ValueError, match="outside its owner"):
        replace(apps_with_lib, navigation=replace(apps.navigation, children=("c_lib",)))
    with pytest.raises(ValueError, match="must be direct"):
        replace(
            apps,
            scope=ProjectionScope(
                requested_path=".", matched_component_id="c_root", matched_component_path="."
            ),
            navigation=ProjectionNavigation(ancestors=(), owner="c_root", children=("c_api",)),
        )
    assert isinstance(valid, ScanProjection)


@pytest.mark.verifies("TST042")
def test_projection_serialization_defensive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = project_scan(_result(), max_bytes="unlimited")
    with pytest.raises(ScanValidationError, match="object is invalid"):
        validate_scan_projection({})

    monkeypatch.setattr(projection_serialization, "_MAX_DOCUMENT_BYTES", 2)
    with pytest.raises(ScanValidationError, match="too large"):
        load_scan_projection_json(b"{}\n")
    with pytest.raises(ScanValidationError, match="too large"):
        load_scan_projection_json("{}\n")
    monkeypatch.undo()

    with pytest.raises(ScanValidationError, match="invalid Unicode"):
        load_scan_projection_json("\ud800")
    monkeypatch.setattr(
        json,
        "dumps",
        lambda *args, **kwargs: (_ for _ in ()).throw(TypeError()),
    )
    with pytest.raises(ScanValidationError, match="cannot be serialized"):
        dump_scan_projection_json(projection)
    monkeypatch.undo()

    class _SchemaResource:
        def __init__(self, text: str | None) -> None:
            self.text = text

        def joinpath(self, *parts: str) -> _SchemaResource:
            return self

        def read_text(self, *, encoding: str) -> str:
            if self.text is None:
                raise OSError
            return self.text

    monkeypatch.setattr(
        importlib_resources,
        "files",
        lambda package: _SchemaResource(None),
    )
    with pytest.raises(ScanValidationError, match="schema is unavailable"):
        scan_projection_json_schema()
    monkeypatch.setattr(
        importlib_resources,
        "files",
        lambda package: _SchemaResource("[]"),
    )
    with pytest.raises(ScanValidationError, match="schema is invalid"):
        scan_projection_json_schema()
