from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import slygentify._doctor as doctor
from slygentify import apply_initialization, plan_initialization
from slygentify._configuration import load_configuration
from slygentify._git_tracking import _TrackedPaths
from slygentify._managed_section import SECTION_BEGIN, SECTION_END
from slygentify._provenance import (
    StateInput,
    dump_state_json,
    load_state_json,
    state_from_scan,
)
from slygentify._scan import _ScanFoundationError
from slygentify._scan.contracts import PartialCause
from slygentify.models import (
    Diagnostic,
    DoctorDiagnostic,
    DoctorResult,
    Evidence,
    Repository,
    SkippedScope,
)
from tests.scan_samples import sample_result


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'doctor-fixture'\nrequires-python = '>=3.11'\n\n[tool.ruff]\nline-length = 88\n",
        encoding="utf-8",
    )
    return root


def _managed(root: Path) -> None:
    plan = plan_initialization(root)
    assert plan.can_apply
    apply_initialization(plan)


def _codes(result: DoctorResult) -> set[str]:
    return {item.code for item in result.diagnostics}


def _sample_execution(root: Path, result: object) -> SimpleNamespace:
    completion = getattr(result, "completion", "complete")
    skipped = getattr(result, "skipped_scopes", ())
    partial_causes = (
        tuple(
            PartialCause(
                source_code=f"inspection.boundary.{boundary.reason}",
                location=boundary.scope,
                subject_path=None,
                problem=f"Inspection reached {boundary.reason}",
                effect=f"{boundary.omitted_scope} was omitted",
                recovery="review the boundary and rerun doctor",
                evidence_ids=(),
                disposition="limitation",
                boundary=boundary,
            )
            for boundary in skipped
        )
        if completion == "partial"
        else ()
    )
    return SimpleNamespace(
        result=result,
        configuration=load_configuration(root),
        content_fingerprints={},
        partial_causes=partial_causes,
    )


@pytest.mark.verifies("TST047")
def test_doctor_models_validate_ordering_references_and_immutability() -> None:
    evidence = Evidence(
        id="evidence",
        source_kind="test",
        location=".",
        locator=None,
        observation="Test evidence.",
        verification_method=None,
    )
    repository = Repository(id="repository", root=".", kind="git", evidence_ids=(evidence.id,))
    first = DoctorDiagnostic(
        id="first",
        code="doctor.a",
        severity="info",
        classification="verified",
        subject_id="repository",
        location=None,
        problem="Problem.",
        effect="Effect.",
        remediation=None,
        evidence_ids=(evidence.id,),
    )
    second = DoctorDiagnostic(
        id="second",
        code="doctor.b",
        severity="warning",
        classification="unknown",
        subject_id=None,
        location="AGENTS.md",
        problem="Problem.",
        effect="Effect.",
        remediation="Review it.",
        evidence_ids=(evidence.id,),
    )
    result = DoctorResult(
        schema_version=1,
        producer_version="test",
        completion="complete",
        repository=repository,
        evidence=(evidence,),
        diagnostics=(first, second),
        skipped_scopes=(),
    )

    assert result.diagnostics[0].severity == "info"
    with pytest.raises(ValueError, match="severity"):
        replace(first, severity="fatal")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="classification"):
        replace(first, classification="unsupported")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires"):
        replace(first, subject_id=None)
    with pytest.raises(ValueError, match="schema_version"):
        replace(result, schema_version=2)
    with pytest.raises(ValueError, match="completion"):
        replace(result, completion="unsupported")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="collections"):
        replace(result, evidence=[evidence])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="canonical"):
        replace(result, diagnostics=(second, first))
    with pytest.raises(ValueError, match="dangling"):
        replace(result, diagnostics=(replace(first, evidence_ids=("missing",)), second))
    with pytest.raises(ValueError, match="partial"):
        replace(result, completion="partial", diagnostics=())

    later = replace(evidence, id="later", location="later.toml")
    with pytest.raises(ValueError, match="evidence is not"):
        replace(result, evidence=(later, evidence))
    later_scope = SkippedScope(
        scope="later", reason="fixture", effective_limit=1, consumed=1, omitted_scope="later"
    )
    first_scope = SkippedScope(
        scope=".", reason="fixture", effective_limit=1, consumed=1, omitted_scope="."
    )
    with pytest.raises(ValueError, match="skipped"):
        replace(result, skipped_scopes=(later_scope, first_scope))
    duplicate = replace(evidence, id="repository")
    duplicate_repository = replace(repository, evidence_ids=(duplicate.id,))
    with pytest.raises(ValueError, match="identifiers"):
        replace(result, repository=duplicate_repository, evidence=(duplicate,))
    with pytest.raises(ValueError, match="repository evidence"):
        replace(result, repository=replace(repository, evidence_ids=("missing",)))


@pytest.mark.verifies("TST047")
def test_clean_managed_repository_is_complete_and_deterministic(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _managed(root)

    first = doctor.doctor_repository(root)
    second = doctor.doctor_repository(root)

    assert first == second
    assert first.completion == "complete"
    assert first.diagnostics == ()


@pytest.mark.verifies("TST047")
def test_unmanaged_guidance_is_informational_without_writes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "AGENTS.md").write_text("human guidance\n", encoding="utf-8")
    before = (root / "AGENTS.md").read_bytes()

    result = doctor.doctor_repository(root)

    assert result.completion == "complete"
    assert _codes(result) == {"doctor.guidance.unmanaged"}
    assert result.diagnostics[0].classification == "unknown"
    assert result.diagnostics[0].disposition == "notice"
    assert (root / "AGENTS.md").read_bytes() == before
    assert not (root / ".slygentify").exists()


@pytest.mark.verifies("TST047")
def test_doctor_checks_managed_section_without_penalizing_surrounding_edits(tmp_path: Path) -> None:
    root = _root(tmp_path)
    agents = root / "AGENTS.md"
    agents.write_text("human guidance\n", encoding="utf-8")
    apply_initialization(plan_initialization(root, adopt=True))

    agents.write_bytes(agents.read_bytes() + b"\nMore human guidance.\n")
    assert doctor.doctor_repository(root).diagnostics == ()

    agents.write_bytes(agents.read_bytes().replace(SECTION_END, b""))
    assert _codes(doctor.doctor_repository(root)) == {"doctor.artifact.missing"}


@pytest.mark.verifies("TST047")
def test_doctor_reports_section_staleness_divergence_and_state_staleness(tmp_path: Path) -> None:
    root = _root(tmp_path)
    agents = root / "AGENTS.md"
    agents.write_text("human guidance\n", encoding="utf-8")
    apply_initialization(plan_initialization(root, adopt=True))

    (root / "package.json").write_text('{"name": "changed"}\n', encoding="utf-8")
    assert "doctor.artifact.stale" in _codes(doctor.doctor_repository(root))
    apply_initialization(plan_initialization(root))

    agents.write_bytes(agents.read_bytes().replace(b"## Safety", b"## Edited safety", 1))
    assert _codes(doctor.doctor_repository(root)) == {"doctor.artifact.diverged"}

    agents.write_bytes(agents.read_bytes().replace(b"## Edited safety", b"## Safety", 1))
    state_target = root / ".slygentify" / "state.json"
    state = load_state_json(state_target.read_bytes())
    stale = replace(
        state,
        artifacts=(replace(state.artifacts[0], sha256="0" * 64),),
    )
    state_target.write_bytes(dump_state_json(stale))
    assert _codes(doctor.doctor_repository(root)) == {"doctor.state.stale"}


@pytest.mark.verifies("TST047")
def test_doctor_rejects_oversized_managed_section_input(tmp_path: Path) -> None:
    root = _root(tmp_path)
    target = root / "AGENTS.md"
    target.write_bytes(b"x" * (doctor._MAX_STATE_BYTES + 1))

    assert doctor._safe_managed_section(root) is None


@pytest.mark.verifies("TST047")
def test_invalid_configuration_stops_before_fresh_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    (root / "slygentify.toml").write_text("not = [valid", encoding="utf-8")

    def no_scan(*args: object, **kwargs: object) -> object:
        raise AssertionError("doctor must not scan invalid configuration")

    monkeypatch.setattr(doctor, "_scan_foundation", no_scan)
    result = doctor.doctor_repository(root)

    assert result.completion == "partial"
    assert _codes(result) == {"doctor.configuration.invalid"}
    assert result.diagnostics[0].severity == "error"


@pytest.mark.verifies("TST047", "TST055")
def test_invalid_state_is_partial_but_allows_fresh_scan(tmp_path: Path) -> None:
    root = _root(tmp_path)
    state_directory = root / ".slygentify"
    state_directory.mkdir()
    (state_directory / "state.json").write_text("{}", encoding="utf-8")

    result = doctor.doctor_repository(root)

    assert result.completion == "partial"
    assert _codes(result) == {"doctor.state.invalid"}
    diagnostic = result.diagnostics[0]
    assert diagnostic.category == "state.invalid-structure"
    assert diagnostic.remediation is not None
    assert "ordinary init can create guidance" in diagnostic.remediation
    assert diagnostic.safety_rationale is not None


@pytest.mark.verifies("TST047", "TST055")
def test_invalid_state_remediation_matches_section_adoption_and_future_schema(
    tmp_path: Path,
) -> None:
    section_root = _root(tmp_path / "section")
    section_agents = section_root / "AGENTS.md"
    section_agents.write_text("human\n", encoding="utf-8")
    apply_initialization(plan_initialization(section_root, adopt=True))
    section_state = section_root / ".slygentify" / "state.json"
    section_state.write_text("{}", encoding="utf-8")
    section_result = doctor.doctor_repository(section_root)
    assert section_result.diagnostics[0].remediation is not None
    assert "replace only the marked section" in section_result.diagnostics[0].remediation

    adopt_root = _root(tmp_path / "adopt")
    (adopt_root / "AGENTS.md").write_text("human\n", encoding="utf-8")
    adopt_state = adopt_root / ".slygentify" / "state.json"
    adopt_state.parent.mkdir()
    adopt_state.write_text("{}", encoding="utf-8")
    adopt_result = doctor.doctor_repository(adopt_root)
    assert adopt_result.diagnostics[0].remediation is not None
    assert "--adopt --dry-run" in adopt_result.diagnostics[0].remediation

    future_root = _root(tmp_path / "future")
    future_state = future_root / ".slygentify" / "state.json"
    future_state.parent.mkdir()
    future_state.write_text('{"schema_version": 3}', encoding="utf-8")
    future_result = doctor.doctor_repository(future_root)
    assert future_result.diagnostics[0].category == "state.unsupported-schema"
    assert future_result.diagnostics[0].remediation is not None
    assert "does not authorize a downgrade" in future_result.diagnostics[0].remediation


@pytest.mark.verifies("TST047", "TST055")
def test_invalid_state_remediation_covers_protected_and_malformed_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    assert "readable" in doctor._invalid_state_remediation(root, "state.unreadable", "fresh")
    assert "unsafe state entry" in doctor._invalid_state_remediation(
        root, "state.unsafe-entry", "fresh"
    )

    agents = root / "AGENTS.md"
    agents.write_bytes(SECTION_BEGIN + b"broken\n")
    assert "--replace --dry-run" in doctor._invalid_state_remediation(
        root, "state.invalid-structure", "fresh"
    )

    unsafe_root = _root(tmp_path / "unsafe-guidance")
    (unsafe_root / "AGENTS.md").mkdir()
    unsafe_state = unsafe_root / ".slygentify" / "state.json"
    unsafe_state.parent.mkdir()
    unsafe_state.write_text("{}", encoding="utf-8")
    unsafe_result = doctor.doctor_repository(unsafe_root)
    unsafe_diagnostic = next(
        item for item in unsafe_result.diagnostics if item.code == "doctor.state.invalid"
    )
    unsafe_recovery = unsafe_diagnostic.remediation or ""
    assert "unsafe AGENTS.md entry" in unsafe_recovery
    assert "--adopt" not in unsafe_recovery

    original_lstat = Path.lstat

    def failing_lstat(path: Path) -> object:
        if path == agents:
            raise OSError("unreadable")
        return original_lstat(path)

    with monkeypatch.context() as context:
        context.setattr(Path, "lstat", failing_lstat)
        unreadable_metadata = doctor._invalid_state_remediation(
            root, "state.invalid-structure", "fresh"
        )
    assert "Make AGENTS.md readable" in unreadable_metadata
    assert "--adopt" not in unreadable_metadata

    with monkeypatch.context() as context:
        context.setattr(doctor, "_MAX_STATE_BYTES", 1)
        oversized_recovery = doctor._invalid_state_remediation(
            root, "state.invalid-structure", "fresh"
        )
    assert "oversized AGENTS.md" in oversized_recovery

    original_read_bytes = Path.read_bytes

    def failing_read_bytes(path: Path) -> bytes:
        if path == agents:
            raise OSError("unreadable")
        return original_read_bytes(path)

    with monkeypatch.context() as context:
        context.setattr(doctor, "_safe_digest", lambda *_args: None)
        context.setattr(Path, "read_bytes", failing_read_bytes)
        unreadable_content = doctor._invalid_state_remediation(
            root, "state.invalid-structure", "fresh"
        )
    assert "Make AGENTS.md readable" in unreadable_content
    assert "--adopt" not in unreadable_content


@pytest.mark.verifies("TST047", "TST046")
def test_doctor_classifies_oversized_state_without_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    state_directory = root / ".slygentify"
    state_directory.mkdir()
    (state_directory / "state.json").write_bytes(b"sentinel-secret-state")
    monkeypatch.setattr(doctor, "_MAX_STATE_BYTES", 8)

    result = doctor.doctor_repository(root)

    diagnostic = next(item for item in result.diagnostics if item.code == "doctor.state.invalid")
    assert diagnostic.category == "state.too-large"
    assert "sentinel-secret-state" not in diagnostic.problem
    assert "sentinel-secret-state" not in (diagnostic.remediation or "")


@pytest.mark.verifies("TST047")
def test_provenance_only_change_is_informational_state_stale(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _managed(root)
    target = root / ".slygentify" / "state.json"
    state = load_state_json(target.read_bytes())
    target.write_bytes(dump_state_json(replace(state, producer_version="0.0.0")))

    result = doctor.doctor_repository(root)

    assert _codes(result) == {"doctor.state.stale"}
    assert result.diagnostics[0].severity == "info"
    assert result.diagnostics[0].disposition == "notice"


@pytest.mark.verifies("TST056")
@pytest.mark.parametrize("volatile_reason", ["built_in_exclusion", "gitignore"])
@pytest.mark.parametrize("schema_version", [1, 2])
def test_old_volatile_state_is_stale_until_explicit_regeneration(
    tmp_path: Path, schema_version: int, volatile_reason: str
) -> None:
    root = _root(tmp_path)
    _managed(root)
    target = root / ".slygentify" / "state.json"
    state = load_state_json(target.read_bytes())
    volatile_scope = ".pytest_cache" if volatile_reason == "built_in_exclusion" else "dist"
    volatile = SkippedScope(
        scope=volatile_scope,
        reason=volatile_reason,
        effective_limit=None,
        consumed=None,
        omitted_scope=volatile_scope,
    )
    target.write_bytes(
        dump_state_json(replace(state, schema_version=schema_version, skipped_scopes=(volatile,)))
    )

    stale = doctor.doctor_repository(root)

    assert _codes(stale) == {"doctor.state.stale"}
    assert stale.diagnostics[0].severity == "info"
    assert stale.diagnostics[0].disposition == "notice"
    regeneration = plan_initialization(root)
    assert regeneration.state_action == "replace"
    assert apply_initialization(regeneration).changed_locations == (".slygentify/state.json",)
    regenerated = load_state_json(target.read_bytes())
    assert regenerated.schema_version == 2
    assert regenerated.skipped_scopes == ()
    assert doctor.doctor_repository(root).diagnostics == ()
    assert plan_initialization(root).state_action == "no_change"


@pytest.mark.verifies("TST047")
def test_recoverable_artifact_digest_is_informational_state_stale(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _managed(root)
    target = root / ".slygentify" / "state.json"
    state = load_state_json(target.read_bytes())
    target.write_bytes(
        dump_state_json(replace(state, artifacts=(replace(state.artifacts[0], sha256="0" * 64),)))
    )

    result = doctor.doctor_repository(root)

    assert _codes(result) == {"doctor.state.stale"}


@pytest.mark.verifies("TST047")
@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing", "doctor.artifact.missing"),
        ("diverged", "doctor.artifact.diverged"),
    ],
)
def test_managed_artifact_missing_and_diverged_are_distinct(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    root = _root(tmp_path)
    _managed(root)
    agents = root / "AGENTS.md"
    if mutation == "missing":
        agents.unlink()
    else:
        agents.write_text("human guidance\n", encoding="utf-8")

    result = doctor.doctor_repository(root)

    assert expected in _codes(result)
    assert not {"doctor.artifact.missing", "doctor.artifact.stale", "doctor.artifact.diverged"} - {
        expected
    } & _codes(result)


@pytest.mark.verifies("TST047")
def test_stale_artifact_is_detected_after_current_component_change(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _managed(root)
    (root / "package.json").write_text('{"name":"fixture","version":"1.0.0"}\n', encoding="utf-8")

    result = doctor.doctor_repository(root)

    assert "doctor.artifact.stale" in _codes(result)
    assert "doctor.artifact.diverged" not in _codes(result)


@pytest.mark.verifies("TST047")
def test_component_and_tooling_drift_use_recorded_derivations(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _managed(root)
    target = root / ".slygentify" / "state.json"
    state = load_state_json(target.read_bytes())
    component_codes = doctor._COMPONENT_CODES
    tooling_codes = doctor._TOOLING_CODES
    assert any(item.claim_code in component_codes for item in state.derivations)
    assert any(item.claim_code in tooling_codes for item in state.derivations)
    target.write_bytes(
        dump_state_json(
            replace(
                state,
                derivations=tuple(
                    item
                    for item in state.derivations
                    if item.claim_code not in component_codes | tooling_codes
                ),
            )
        )
    )

    result = doctor.doctor_repository(root)

    assert {"doctor.component.drift", "doctor.tooling.drift"} <= _codes(result)
    assert "doctor.state.stale" not in _codes(result)


@pytest.mark.verifies("TST047")
def test_missing_recorded_evidence_is_unknown(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _managed(root)
    target = root / ".slygentify" / "state.json"
    state = load_state_json(target.read_bytes())
    missing = StateInput(
        id="missing-input",
        source_kind="manifest",
        location="retired.toml",
        locator=None,
        sha256=hashlib.sha256(b"old").hexdigest(),
        value_sha256=None,
        rule_id="fixture",
        rule_version=1,
    )
    target.write_bytes(
        dump_state_json(
            replace(
                state,
                inputs=tuple(
                    sorted(
                        (*state.inputs, missing),
                        key=lambda item: (item.id, item.location, item.locator or ""),
                    )
                ),
            )
        )
    )

    result = doctor.doctor_repository(root)

    assert "doctor.evidence.missing" in _codes(result)
    diagnostic = next(item for item in result.diagnostics if item.code == "doctor.evidence.missing")
    assert diagnostic.classification == "unknown"


@pytest.mark.verifies("TST047")
def test_path_command_and_partial_rules_are_limited_to_supported_prior_knowledge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    base = sample_result()
    configuration = load_configuration(root)
    state = state_from_scan(base, configuration, {})
    command = StateInput(
        id="old-command",
        source_kind="ci-command",
        location=".gitea/workflows/check.yml",
        locator="jobs.check.steps.0.run",
        sha256=hashlib.sha256(b"old-command").hexdigest(),
        value_sha256=None,
        rule_id="ci-command",
        rule_version=1,
    )
    state = replace(state, inputs=(command,))
    directory = root / ".slygentify"
    directory.mkdir()
    (directory / "state.json").write_bytes(dump_state_json(state))
    diagnostics = tuple(
        sorted(
            (
                *base.diagnostics,
                Diagnostic(
                    id="dynamic-command",
                    code="python.dynamic-ci-command-unknown",
                    subject_id=base.repository.id,
                    location=".gitea/workflows/check.yml",
                    message="Dynamic command was not evaluated.",
                    evidence_ids=(),
                ),
                Diagnostic(
                    id="missing-path",
                    code="inspection.missing-workspace-member",
                    subject_id=base.repository.id,
                    location="pyproject.toml",
                    message="Referenced path is missing.",
                    evidence_ids=(),
                ),
            ),
            key=lambda item: (item.code, item.subject_id or item.location or "", item.id),
        )
    )
    partial = replace(
        base,
        completion="partial",
        diagnostics=diagnostics,
        skipped_scopes=(
            SkippedScope(
                scope=".",
                reason="fixture limit",
                effective_limit=1,
                consumed=1,
                omitted_scope=".",
            ),
        ),
    )
    monkeypatch.setattr(
        doctor, "_scan_foundation", lambda *args, **kwargs: _sample_execution(root, partial)
    )

    result = doctor.doctor_repository(root)

    assert {
        "doctor.command.unverifiable",
        "doctor.path.missing",
        "doctor.inspection.partial",
    } <= _codes(result)
    by_code = {item.code: item for item in result.diagnostics}
    assert by_code["doctor.command.unverifiable"].disposition == "limitation"
    assert by_code["doctor.path.missing"].disposition == "problem"
    assert by_code["doctor.inspection.partial"].disposition == "limitation"
    assert result.completion == "partial"


@pytest.mark.verifies("TST047", "TST050")
def test_doctor_reports_each_scan_partial_cause_with_exact_safe_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    (root / "package.json").write_text('{"name":', encoding="utf-8")
    monkeypatch.setattr(
        "slygentify._scan.orchestration._discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset(), frozenset(), False),
    )

    first = doctor.doctor_repository(root)
    second = doctor.doctor_repository(root)
    partials = tuple(item for item in first.diagnostics if item.code == "doctor.inspection.partial")

    assert len(partials) == 2
    assert [item.id for item in partials] == [
        item.id for item in second.diagnostics if item.code == "doctor.inspection.partial"
    ]
    by_location = {item.location: item for item in partials}
    manifest = by_location["package.json"]
    assert manifest.disposition == "problem"
    assert "package boundary and declarations" in manifest.effect
    assert "intentionally exclude" in (manifest.remediation or "")
    assert "scan.limits" not in (manifest.remediation or "")
    git = by_location["."]
    assert git.disposition == "limitation"
    assert "Gitignore" in git.effect
    assert "Git executable" in (git.remediation or "")
    evidence = {item.id: item for item in first.evidence}
    assert {
        evidence[evidence_id].locator
        for item in partials
        for evidence_id in item.evidence_ids
        if evidence[evidence_id].source_kind == "doctor-provenance"
    } == {"javascript.invalid-manifest", "inspection.git-tracked-paths-unavailable"}


@pytest.mark.verifies("TST047")
def test_doctor_rejects_partial_results_without_private_cause_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    execution = _sample_execution(root, sample_result())
    execution.partial_causes = ()
    monkeypatch.setattr(doctor, "_scan_foundation", lambda *args, **kwargs: execution)

    assert doctor._doctor_sentence("  ") == ""
    with pytest.raises(doctor.DoctorOperationalError, match="structured causal accounting"):
        doctor.doctor_repository(root)


@pytest.mark.verifies("TST047")
def test_input_and_operational_failures_are_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(doctor.DoctorInputError):
        doctor.doctor_repository(tmp_path / "not-a-repository")

    root = _root(tmp_path)

    def fail(*args: object, **kwargs: object) -> object:
        raise _ScanFoundationError("scanner failed")

    monkeypatch.setattr(doctor, "_scan_foundation", fail)
    with pytest.raises(doctor.DoctorOperationalError, match="scanner failed"):
        doctor.doctor_repository(root)

    def bad_git(*args: object, **kwargs: object) -> object:
        raise _ScanFoundationError("git executable is invalid")

    monkeypatch.setattr(doctor, "_scan_foundation", bad_git)
    with pytest.raises(doctor.DoctorInputError, match="git executable"):
        doctor.doctor_repository(root, git_executable="git")

    def os_failure(*args: object, **kwargs: object) -> object:
        raise OSError("disk error")

    monkeypatch.setattr(doctor, "_scan_foundation", os_failure)
    with pytest.raises(doctor.DoctorOperationalError, match="disk error"):
        doctor.doctor_repository(root)


@pytest.mark.verifies("TST047")
def test_private_safe_read_helpers_reject_missing_unsafe_and_unreadable_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    assert doctor._safe_digest(root, "missing.txt") is None
    directory = root / "directory"
    directory.mkdir()
    assert doctor._safe_digest(root, "directory") is None
    artifact = root / "artifact.txt"
    artifact.write_text("artifact", encoding="utf-8")

    def unreadable(*args: object, **kwargs: object) -> object:
        raise OSError("read failure")

    monkeypatch.setattr(Path, "open", unreadable)
    assert doctor._safe_digest(root, "artifact.txt") is None

    state_directory = root / ".slygentify"
    state_directory.mkdir()
    (state_directory / "state.json").mkdir()
    assert doctor._load_state(root) == (None, "state.unsafe-entry")


@pytest.mark.verifies("TST047")
def test_private_state_loader_classifies_unreadable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    state_directory = root / ".slygentify"
    state_directory.mkdir()
    (state_directory / "state.json").write_bytes(b"{}")

    def unreadable(*args: object, **kwargs: object) -> bytes:
        raise OSError("state read failure")

    monkeypatch.setattr(Path, "read_bytes", unreadable)

    assert doctor._load_state(root) == (None, "state.unreadable")
