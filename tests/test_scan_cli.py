"""Tests for scan presentation, JSON streams, and exit behavior."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

import slygentify._git_tracking as git_tracking
import slygentify.cli as cli
from slygentify import (
    ComponentRelationship,
    Diagnostic,
    Finding,
    ScanError,
    ScanResult,
    SkippedScope,
    dump_scan_json,
    load_scan_json,
)
from slygentify._git_tracking import _TrackedPaths
from slygentify._presentation import (
    ScanPresentation,
    record_classification,
    record_kind,
    record_label,
    render_scan_report,
)
from slygentify.cli import app
from slygentify.models import ClaimClassification
from tests.scan_samples import sample_result


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    return repository


@pytest.mark.verifies("TST019")
def test_scan_help_discloses_explicit_executable_trust_boundary() -> None:
    result = CliRunner().invoke(app, ["scan", "--help"])

    plain = Text.from_ansi(result.stdout).plain
    normalized = " ".join(plain.split())
    assert result.exit_code == 0
    assert "--git-executable" in normalized
    assert "not sandboxed" in normalized
    assert "arbitrary" in normalized
    assert "effects are possible" in normalized


@pytest.mark.verifies("TST019")
def test_invalid_explicit_git_executable_is_an_operational_cli_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    monkeypatch.setattr(
        "slygentify._scan.orchestration._discover_tracked_paths",
        git_tracking._discover_tracked_paths,
    )

    result = CliRunner().invoke(
        app,
        ["scan", str(repository), "--git-executable", str(tmp_path / "missing-git")],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: git_executable must identify" in result.stderr


@pytest.mark.verifies("TST019", "TST033")
@pytest.mark.parametrize(
    "arguments",
    [
        ["--format", "text"],
        ["--format", "json"],
        ["--interactive"],
    ],
)
def test_git_executable_is_forwarded_in_every_scan_mode(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    captured: list[Path | None] = []

    def scan(path: Path, *, git_executable: Path | None = None) -> ScanResult:
        captured.append(git_executable)
        return sample_result()

    monkeypatch.setattr(cli, "scan_repository", scan)
    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "run_scan_explorer", lambda root, scan: scan())

    result = CliRunner().invoke(
        app,
        ["scan", str(repository), "--git-executable", "tools/git", *arguments],
    )

    assert result.exit_code == 0
    assert captured == [Path("tools/git")]


@pytest.mark.verifies("TST019", "TST033")
def test_git_tracking_partial_is_visible_in_text_json_and_interactive_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    (repository / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    monkeypatch.setattr(
        "slygentify._scan.orchestration._discover_tracked_paths",
        lambda *args, **kwargs: _TrackedPaths(frozenset(), frozenset(), False),
    )

    text_result = CliRunner().invoke(app, ["scan", str(repository), "--format", "text"])
    json_result = CliRunner().invoke(app, ["scan", str(repository), "--format", "json"])

    assert text_result.exit_code == 0
    assert "inspection.git-tracked-paths-unavailable" in text_result.stdout
    assert "git_tracking_unavailable" in text_result.stdout
    document = json.loads(json_result.stdout)
    assert json_result.exit_code == 0
    assert document["completion"] == "partial"
    assert document["diagnostics"][0]["code"] == "inspection.git-tracked-paths-unavailable"
    assert document["skipped_scopes"][0]["reason"] == "git_tracking_unavailable"

    captured: list[ScanResult] = []
    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "run_scan_explorer", lambda root, scan: captured.append(scan()))
    interactive_result = CliRunner().invoke(app, ["scan", str(repository), "--interactive"])

    assert interactive_result.exit_code == 0
    assert captured[0].diagnostics[0].code == "inspection.git-tracked-paths-unavailable"


@pytest.mark.verifies("TST019")
def test_relaxed_configuration_warns_once_without_affecting_json_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    scan = replace(
        sample_result(),
        diagnostics=(
            Diagnostic(
                id="diagnostic-configuration",
                code="configuration.relaxed-limits",
                subject_id=None,
                location="slygentify.toml",
                message="limits relaxed",
                evidence_ids=(),
            ),
        ),
    )
    monkeypatch.setattr(cli, "scan_repository", lambda *args, **kwargs: scan)

    result = CliRunner().invoke(app, ["scan", str(repository), "--format", "json"])

    assert result.exit_code == 0
    assert result.stderr.count("Warning:") == 1
    assert json.loads(result.stdout)["diagnostics"][0]["code"] == "configuration.relaxed-limits"


def _render(scan: ScanResult, *, terminal: bool = False, width: int = 120) -> str:
    stream = StringIO()
    render_scan_report(
        scan,
        Path("repository"),
        Console(
            file=stream,
            force_terminal=terminal,
            color_system="standard" if terminal else None,
            legacy_windows=False if terminal else None,
            width=width,
        ),
    )
    return stream.getvalue()


@pytest.mark.verifies("TST019")
def test_scan_text_is_complete_component_first_default(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "Cargo.toml").write_text('[package]\nname = "example"\n', encoding="utf-8")

    result = CliRunner().invoke(app, ["scan", str(repository)])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout.startswith("Scan completed\n")
    assert "Repository map -" in result.stdout
    assert str(repository) in result.stdout.replace("\n", "")
    assert "Status: Complete scan - Finished within current support" in result.stdout
    assert "At a glance" in result.stdout
    assert "Component paths (primary navigation) (1)" in result.stdout
    assert "Auxiliary components (secondary navigation) (0)" in result.stdout
    assert ". - generic/package (facets: generic; role: unknown)" in result.stdout
    assert "What it is" in result.stdout
    assert "Repository-wide workflows" in result.stdout
    assert "Needs attention" in result.stdout
    assert "Sources & provenance (2)" in result.stdout
    assert "[manifest] Cargo.toml" in result.stdout
    assert "Claim terms: VERIFIED = directly supported" in result.stdout
    assert "verbose" not in result.stdout
    assert "\x1b[" not in result.stdout


@pytest.mark.verifies("TST019")
def test_scan_text_represents_marker_only_and_empty_sections(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    result = CliRunner().invoke(app, ["scan", str(repository)])

    assert result.exit_code == 0
    assert "Component paths (primary navigation) (0)" in result.stdout
    assert "Auxiliary components (secondary navigation) (0)" in result.stdout
    assert "[vcs-marker] .git: Git repository marker is present." in result.stdout
    normalized = " ".join(result.stdout.split())
    assert "Repository-wide workflows (0)" in normalized
    assert "Sources & provenance (1)" in normalized


@pytest.mark.verifies("TST019", "TST030")
def test_text_report_separates_auxiliary_components_without_losing_findings() -> None:
    scan = sample_result()
    auxiliary = replace(
        scan.components[0],
        id="component_auxiliary",
        path="tests",
        role="auxiliary",
    )
    auxiliary_finding = Finding(
        id="finding_auxiliary",
        code="composition.auxiliary-component",
        classification="inferred",
        subject_id=auxiliary.id,
        summary="Path convention indicates an auxiliary component.",
        evidence_ids=auxiliary.evidence_ids,
    )
    result = replace(
        scan,
        components=(scan.components[0], auxiliary),
        findings=tuple(
            sorted(
                (*scan.findings, auxiliary_finding),
                key=lambda item: (item.code, item.subject_id, item.id),
            )
        ),
    )

    output = _render(result, width=160)

    assert "Component paths (primary navigation) (1)" in output
    assert "Auxiliary components (secondary navigation) (1)" in output
    assert ". - generic/package (facets: generic; role: unknown)" in output
    assert "tests - generic/package (facets: generic; role: auxiliary)" in output
    assert "INFERRED Path convention indicates an auxiliary component." in output
    assert "[composition.auxiliary-component]" in output


@pytest.mark.verifies("TST019")
def test_scan_text_preserves_claims_diagnostics_and_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    monkeypatch.setattr(
        cli, "scan_repository", lambda path, *, git_executable=None: sample_result()
    )

    invocation = CliRunner().invoke(app, ["scan", str(repository), "--format", "text"])

    assert invocation.exit_code == 0
    assert "Status: Partial scan - Some safe inspection was incomplete" in invocation.stdout
    assert "UNKNOWN A boundary remains unknown. [example.unknown]" in invocation.stdout
    assert "VERIFIED A package boundary is verified. [example.verified]" in invocation.stdout
    assert "example.diagnostic" in invocation.stdout
    assert "Review the example diagnostic." in invocation.stdout
    assert "cache-or-dependency (1)" in invocation.stdout
    assert "vendor: cache-or-dependency (omitted: vendor/**)" in invocation.stdout


@pytest.mark.verifies("TST019")
def test_static_report_lists_every_record_once_in_its_canonical_section() -> None:
    scan = sample_result()
    child = replace(scan.components[0], id="component_child", path="child")
    relationship = ComponentRelationship(
        id="relationship_child",
        kind="contains",
        source_id=scan.components[0].id,
        target_id=child.id,
        classification="inferred",
        evidence_ids=scan.components[0].evidence_ids,
    )
    scan = replace(
        scan,
        components=tuple(sorted((*scan.components, child), key=lambda item: (item.id, item.path))),
        relationships=(relationship,),
    )

    output = _render(scan, width=200)

    assert output.count("A boundary remains unknown.") == 1
    assert output.count("A package boundary is verified.") == 1
    assert output.count("Git repository marker is present.") == 1
    assert output.count("Review the example diagnostic.") == 1
    assert output.count("omitted: vendor/**") == 1
    assert output.count("INFERRED contains: . -> child") == 1
    assert output.count("child - generic/package") == 1


@pytest.mark.verifies("TST019")
def test_static_report_terminal_rendering_uses_rich_tree_and_literal_claims() -> None:
    output = _render(sample_result(), terminal=True, width=100)

    assert "\x1b[" in output
    assert "├──" in output
    assert "UNKNOWN" in output
    assert "VERIFIED" in output


@pytest.mark.verifies("TST019")
def test_static_report_uses_ascii_hierarchy_for_limited_terminals() -> None:
    stream = StringIO()
    render_scan_report(
        sample_result(),
        Path("repository"),
        Console(
            file=stream,
            force_terminal=True,
            legacy_windows=True,
            color_system="windows",
            width=40,
        ),
    )

    output = stream.getvalue()
    assert "|- At a glance" in output
    assert "├──" not in output


@pytest.mark.verifies("TST019")
def test_static_report_respects_no_color_without_hiding_claim_labels() -> None:
    stream = StringIO()
    console = Console(
        file=stream,
        force_terminal=True,
        legacy_windows=False,
        color_system="standard",
        _environ={"NO_COLOR": "1"},
    )

    render_scan_report(sample_result(), Path("repository"), console)

    output = stream.getvalue()
    assert console.no_color
    assert "\x1b[1;32m" not in output
    assert "\x1b[1;35m" not in output
    assert "VERIFIED" in output
    assert "UNKNOWN" in output


@pytest.mark.verifies("TST019")
def test_presentation_index_covers_all_record_kinds_and_user_focused_groups() -> None:
    scan = sample_result()
    child = replace(scan.components[0], id="component_child", path="packages/child")
    relationship = ComponentRelationship(
        id="relationship_without_evidence",
        kind="contains",
        source_id=child.id,
        target_id=scan.components[0].id,
        classification="inferred",
        evidence_ids=(),
    )
    finding_specs: tuple[tuple[str, ClaimClassification], ...] = (
        ("workspace.component", "verified"),
        ("project.runtime", "verified"),
        ("other.fact", "verified"),
        ("task.setup.command", "verified"),
        ("task.run.command", "verified"),
        ("task.test.command", "verified"),
        ("task.lint.command", "verified"),
        ("task.build.command", "verified"),
        ("task.command", "verified"),
        ("architecture.entry-point", "verified"),
        ("architecture.framework", "verified"),
        ("architecture.dependency", "verified"),
        ("architecture.tool", "verified"),
        ("python.ci.command", "verified"),
        ("manager-conflict", "unknown"),
        ("open-question", "unknown"),
        ("suggested.improvement", "recommended"),
    )
    findings = tuple(
        sorted(
            (
                Finding(
                    id=f"category_{index}",
                    code=code,
                    classification=classification,
                    subject_id=child.id,
                    summary=f"Category {index}",
                    evidence_ids=(),
                )
                for index, (code, classification) in enumerate(finding_specs)
            ),
            key=lambda item: (item.code, item.subject_id, item.id),
        )
    )
    skipped = SkippedScope(
        scope="large",
        reason="resource-limit",
        effective_limit=10,
        consumed=10,
        omitted_scope="large/**",
    )
    result = replace(
        scan,
        components=(scan.components[0], child),
        relationships=(relationship,),
        findings=findings,
        diagnostics=(replace(scan.diagnostics[0], subject_id=child.id),),
        skipped_scopes=(skipped,),
    )
    index = ScanPresentation(result, Path("repository"))

    assert index.children_of(scan.components[0].id) == (child,)

    assert [(group.section, group.subsection) for group in index.component_groups(child.id)] == [
        ("What it is", "Identity & role"),
        ("What it is", "Runtime & package managers"),
        ("What it is", "Other observations"),
        ("How to work on it", "Setup"),
        ("How to work on it", "Run"),
        ("How to work on it", "Test"),
        ("How to work on it", "Lint & format"),
        ("How to work on it", "Build"),
        ("How to work on it", "Other tasks"),
        ("Architecture", "Entry points"),
        ("Architecture", "Frameworks"),
        ("Architecture", "Dependencies"),
        ("Architecture", "Tools"),
        ("Architecture", "Relationships"),
        ("Automation", "CI workflows & commands"),
        ("Needs attention", "Problems & next steps"),
        ("Needs attention", "Unknowns to confirm"),
        ("Needs attention", "Recommendations"),
    ]
    assert [record_kind(record) for record in index.iter_records()] == [
        "repository",
        "component",
        "component",
        "relationship",
        *("finding" for _ in findings),
        "evidence",
        "evidence",
        "diagnostic",
        "skipped-scope",
    ]
    assert record_classification(relationship) == "inferred"
    assert record_classification(scan.evidence[0]) is None
    assert record_label(index, result.repository).plain == ". - git"
    assert record_label(index, relationship).plain == "INFERRED contains: packages/child -> ."
    assert "limit=10, consumed=10" in record_label(index, skipped).plain
    assert "INFERRED: contains relationship" in index.record_detail(relationship)
    assert "Inspection boundary: large" in index.record_detail(skipped)
    assert json.loads(index.record_json(skipped))["omitted_scope"] == "large/**"
    relationship_with_method = replace(relationship, evidence_ids=(scan.evidence[0].id,))
    assert "Checked by: non-following metadata inspection" in index.record_detail(
        relationship_with_method
    )

    output = _render(result, width=46)
    for heading in (
        "What it is",
        "How to work on it",
        "Architecture",
        "Automation",
        "Needs attention",
        "Setup (1)",
        "Relationships (1)",
        "Problems & next steps",
    ):
        assert heading in output


@pytest.mark.verifies("TST019")
def test_repository_findings_are_organized_by_user_question() -> None:
    scan = sample_result()
    findings = tuple(
        sorted(
            (
                Finding(
                    id="repository_identity",
                    code="repository.identity",
                    classification="verified",
                    subject_id=scan.repository.id,
                    summary="The repository identity is declared.",
                    evidence_ids=(),
                ),
                Finding(
                    id="repository_test",
                    code="task.test.command",
                    classification="verified",
                    subject_id=scan.repository.id,
                    summary="The repository test task is declared.",
                    evidence_ids=(),
                ),
            ),
            key=lambda item: (item.code, item.subject_id, item.id),
        )
    )
    result = replace(scan, findings=findings)
    index = ScanPresentation(result, Path("repository"))

    assert [
        (group.section, group.subsection) for group in index.finding_groups(scan.repository.id)
    ] == [
        ("At a glance", "Other observations"),
        ("Repository-wide workflows", "Test"),
    ]

    output = _render(result, width=160)
    assert "Other observations (1)" in output
    assert "VERIFIED The repository identity is declared." in output
    assert "Repository-wide workflows (1)" in output
    assert "Test (1)" in output
    assert "VERIFIED The repository test task is declared." in output


@pytest.mark.verifies("TST019")
def test_attention_pairs_related_unknowns_without_losing_records() -> None:
    scan = sample_result()
    component = scan.components[0]
    evidence_id = scan.evidence[1].id
    paired = Finding(
        id="paired_unknown",
        code="example.related-unknown",
        classification="unknown",
        subject_id=component.id,
        summary="The related package boundary could not be established.",
        evidence_ids=(evidence_id,),
    )
    unmatched = Finding(
        id="unmatched_unknown",
        code="manager-conflict",
        classification="unknown",
        subject_id=component.id,
        summary="The independent manager choice could not be established.",
        evidence_ids=(),
    )
    caution = Finding(
        id="explicit_caution",
        code="javascript.npm-lock-precedence",
        classification="verified",
        subject_id=component.id,
        summary="Both npm lock forms are present.",
        evidence_ids=(evidence_id,),
    )
    diagnostic = replace(
        scan.diagnostics[0],
        subject_id=component.id,
        evidence_ids=(evidence_id,),
    )
    findings = tuple(
        sorted((paired, unmatched, caution), key=lambda item: (item.code, item.subject_id, item.id))
    )
    result = replace(scan, findings=findings, diagnostics=(diagnostic,))
    presentation = ScanPresentation(result, Path("repository"))
    attention = tuple(
        group
        for group in presentation.component_groups(component.id)
        if group.section == "Needs attention"
    )

    assert [(group.subsection, len(group.records)) for group in attention] == [
        ("Problems & next steps", 2),
        ("Cautions", 1),
        ("Unknowns to confirm", 1),
    ]
    problems = presentation.attention_problems(attention[0])
    assert problems[0].diagnostic == diagnostic
    assert problems[0].related_unknowns == (paired,)
    assert presentation.attention_counts(attention) == (3, 4)
    output = _render(result, width=160)
    assert "Needs attention (3 issues; 4 records)" in output
    assert "Related context (1)" in output
    assert output.count(paired.summary) == 1
    assert output.count(unmatched.summary) == 1
    assert output.count(caution.summary) == 1


@pytest.mark.verifies("TST019")
def test_static_report_retains_high_cardinality_diagnostics_exactly() -> None:
    scan = sample_result()
    diagnostics = tuple(
        sorted(
            (
                Diagnostic(
                    id=f"diagnostic_{index}",
                    code="repeated.code",
                    subject_id=scan.components[0].id,
                    location=f"src/file_{index}.py",
                    message=f"Unique message {index}",
                    evidence_ids=(),
                )
                for index in range(50)
            ),
            key=lambda item: (item.code, item.subject_id or item.location or "", item.id),
        )
    )

    output = _render(replace(scan, diagnostics=diagnostics), width=200)

    assert ". - repeated.code (50 issues; 50 records)" in output
    for index in range(50):
        assert output.count(f"Unique message {index}\n") == 1


@pytest.mark.verifies("TST019")
def test_static_report_uses_a_diagnostic_location_when_no_subject_exists() -> None:
    scan = sample_result()
    diagnostic = replace(scan.diagnostics[0], subject_id=None, location="pyproject.toml")

    output = _render(replace(scan, diagnostics=(diagnostic,)))

    assert "pyproject.toml - example.diagnostic (1 issue; 1 record)" in output
    assert "example.diagnostic @ pyproject.toml" in output


@pytest.mark.verifies("TST033")
def test_scan_interactive_starts_explorer_before_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    scan = sample_result()
    captured: list[tuple[Path, ScanResult]] = []
    order: list[str] = []

    def scanned(path: Path, *, git_executable: Path | None = None) -> ScanResult:
        order.append("scan")
        return scan

    def explorer_started(root: Path, scan_work: object) -> None:
        order.append("explorer")
        assert callable(scan_work)
        captured.append((root, scan_work()))

    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "scan_repository", scanned)
    monkeypatch.setattr(cli, "run_scan_explorer", explorer_started)

    result = CliRunner().invoke(app, ["scan", str(repository), "--interactive"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert captured == [(repository, scan)]
    assert order == ["explorer", "scan"]


@pytest.mark.verifies("TST033")
def test_scan_interactive_surfaces_worker_failures_as_cli_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    monkeypatch.setattr(cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        cli,
        "scan_repository",
        lambda path, *, git_executable=None: (_ for _ in ()).throw(ScanError("scan failed")),
    )
    monkeypatch.setattr(cli, "run_scan_explorer", lambda root, scan_work: scan_work())

    result = CliRunner().invoke(app, ["scan", str(repository), "--interactive"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: scan failed" in result.stderr


@pytest.mark.verifies("TST033")
def test_scan_interactive_non_tty_fails_before_scanning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "scan_repository",
        lambda path, *, git_executable=None: pytest.fail("scan must not run"),
    )

    result = CliRunner().invoke(app, ["scan", ".", "--interactive"])

    assert result.exit_code == 2
    plain_stderr = Text.from_ansi(result.stderr).plain
    assert "default report or --format json" in " ".join(plain_stderr.split())


@pytest.mark.verifies("TST033")
def test_interactive_terminal_check_requires_both_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stream:
        def __init__(self, terminal: bool) -> None:
            self.terminal = terminal

        def isatty(self) -> bool:
            return self.terminal

    monkeypatch.setattr(sys, "stdin", Stream(True))
    monkeypatch.setattr(sys, "stdout", Stream(True))
    assert cli._is_interactive_terminal()
    monkeypatch.setattr(sys, "stdout", Stream(False))
    assert not cli._is_interactive_terminal()
    monkeypatch.setattr(sys, "stdin", Stream(False))
    assert not cli._is_interactive_terminal()


@pytest.mark.verifies("TST019", "TST018")
def test_scan_json_stdout_is_exactly_the_canonical_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    scan = sample_result()
    monkeypatch.setattr(cli, "scan_repository", lambda path, *, git_executable=None: scan)

    result = CliRunner().invoke(app, ["scan", str(repository), "--format", "json"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout_bytes == dump_scan_json(scan)
    assert load_scan_json(result.stdout_bytes) == scan
    assert json.loads(result.stdout) == json.loads(dump_scan_json(scan))


@pytest.mark.verifies("TST019")
def test_scan_json_returns_partial_document_for_invalid_gitignore(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text("!\n", encoding="utf-8")
    (repository / "Cargo.toml").write_text('[package]\nname = "example"\n', encoding="utf-8")

    result = CliRunner().invoke(app, ["scan", str(repository), "--format", "json"])

    assert result.exit_code == 0
    assert result.stderr == ""
    scan = load_scan_json(result.stdout_bytes)
    assert scan.completion == "partial"
    assert {(item.scope, item.reason) for item in scan.skipped_scopes} >= {
        (".gitignore", "invalid_gitignore")
    }
    assert "inspection.invalid-gitignore" in {item.code for item in scan.diagnostics}


@pytest.mark.verifies("TST019")
def test_scan_operational_failure_uses_stderr_and_exit_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(path: Path, *, git_executable: Path | None = None) -> object:
        raise ScanError("cannot inspect repository")

    monkeypatch.setattr(cli, "scan_repository", fail)

    result = CliRunner().invoke(app, ["scan", ".", "--format", "json"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: cannot inspect repository" in result.stderr


@pytest.mark.verifies("TST019", "TST033")
@pytest.mark.parametrize(
    "arguments",
    [
        ["scan", ".", "--format", "yaml"],
        ["scan", "--bad"],
        ["scan", ".", "--format", "json", "--interactive"],
        ["scan", ".", "--verbose"],
    ],
)
def test_scan_usage_failures_remain_exit_two(arguments: list[str]) -> None:
    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 2
