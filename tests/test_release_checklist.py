"""Tests for the fail-closed post-1.0 release checklist."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from tools import release_checklist


@pytest.fixture
def inputs(tmp_path: Path) -> release_checklist.Inputs:
    return release_checklist.Inputs(
        "1.2.3",
        "v1.2.3",
        "a" * 40,
        1,
        tmp_path / "formal",
        tmp_path / "supplemental",
        None,
        22,
    )


@pytest.mark.verifies("TST057")
def test_dry_run_reports_effects_without_writing(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: tmp_path / "repository")
    result = release_checklist.run_checklist(
        inputs, tmp_path / "evidence", "preflight", dry_run=True, allow_network=False
    )

    assert result["status"] == "planned"
    effects = cast(dict[str, object], result["effects"])
    assert effects["network"] is False
    assert not (tmp_path / "evidence").exists()


@pytest.mark.verifies("TST057")
def test_dry_run_reports_phase_and_command_writes(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: tmp_path / "repository")

    preflight = release_checklist.run_checklist(
        inputs, tmp_path / "evidence", "preflight", dry_run=True, allow_network=False
    )
    package_writes = release_checklist._phase_writes("package")  # noqa: SLF001

    preflight_writes = cast(
        tuple[str, ...], cast(dict[str, object], preflight["effects"])["writes"]
    )
    assert "repository/.coverage" in preflight_writes
    assert "evidence/package-dist/" in package_writes
    assert "evidence/package-bundle/release-manifest.json" in package_writes


@pytest.mark.verifies("TST057")
def test_phase_rejects_skipped_prerequisite(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: tmp_path / "repository")
    with pytest.raises(release_checklist.ChecklistError, match="cannot skip unmet prerequisites"):
        release_checklist.run_checklist(
            inputs, tmp_path / "evidence", "package", dry_run=True, allow_network=False
        )


@pytest.mark.verifies("TST057")
def test_external_evidence_and_state_must_match_immutable_inputs(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    with pytest.raises(release_checklist.ChecklistError, match="outside"):
        release_checklist.run_checklist(
            inputs, repository / "evidence", "preflight", dry_run=True, allow_network=False
        )

    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "release-checklist-state.json").write_text(
        json.dumps({"schema_version": 1, "inputs": {"wrong": True}, "phases": {}}), encoding="utf-8"
    )
    with pytest.raises(release_checklist.ChecklistError, match="immutable release inputs"):
        release_checklist.run_checklist(
            inputs, evidence, "preflight", dry_run=True, allow_network=False
        )


@pytest.mark.verifies("TST057")
def test_completed_phase_writes_canonical_evidence_and_gate_packet(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    monkeypatch.setattr(release_checklist, "_phase_commands", lambda *_args: [])
    monkeypatch.setattr(
        release_checklist, "_verify_frozen_checkout", lambda *_args: {"status": "passed"}
    )
    evidence = tmp_path / "evidence"

    result = release_checklist.run_checklist(
        inputs, evidence, "preflight", dry_run=False, allow_network=False
    )

    assert result["status"] == "passed"
    assert (evidence / "preflight.json").read_bytes().endswith(b"\n")
    state = json.loads((evidence / "release-checklist-state.json").read_text(encoding="utf-8"))
    assert state["phases"]["preflight"]["digest"] == result["digest"]


@pytest.mark.verifies("TST057")
def test_human_gate_reads_issue_without_writing(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    digest = "b" * 64
    release_checklist._write(  # noqa: SLF001
        evidence / "release-checklist-state.json",
        {
            "schema_version": 1,
            "inputs": inputs.public(),
            "phases": {"formal-corpus": {"digest": digest}},
        },
    )

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "comments": [
                    {
                        "author": {"login": release_checklist.RELEASE_MAINTAINER},
                        "body": release_checklist._gate_comment("semantic-corpus", digest),  # noqa: SLF001
                    }
                ]
            }
        )

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: Completed())
    result = release_checklist.verify_human_gate(inputs, evidence, "formal-corpus")

    assert result == {"phase": "formal-corpus", "gate": "semantic-corpus", "status": "approved"}
    state = json.loads((evidence / "release-checklist-state.json").read_text(encoding="utf-8"))
    assert state["phases"]["formal-corpus"]["gate_verified"] is True


@pytest.mark.verifies("TST057")
def test_human_gate_rejects_an_untrusted_commenter(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    digest = "b" * 64
    release_checklist._write(  # noqa: SLF001
        evidence / "release-checklist-state.json",
        {
            "schema_version": 1,
            "inputs": inputs.public(),
            "phases": {"formal-corpus": {"digest": digest}},
        },
    )

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "comments": [
                    {
                        "author": {"login": "untrusted"},
                        "body": release_checklist._gate_comment("semantic-corpus", digest),  # noqa: SLF001
                    }
                ]
            }
        )

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: Completed())
    with pytest.raises(release_checklist.ChecklistError, match="does not contain"):
        release_checklist.verify_human_gate(inputs, evidence, "formal-corpus")


@pytest.mark.verifies("TST057")
def test_composed_corpus_is_protected_from_evidence_writes(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    composed = tmp_path / "composed"
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    protected = release_checklist.Inputs(
        inputs.version,
        inputs.tag,
        inputs.freeze_commit,
        inputs.source_date_epoch,
        inputs.formal_root,
        inputs.supplemental_root,
        composed,
        inputs.github_issue,
    )
    with pytest.raises(release_checklist.ChecklistError, match="outside"):
        release_checklist.run_checklist(
            protected, composed / "evidence", "preflight", dry_run=True, allow_network=False
        )


@pytest.mark.verifies("TST057")
def test_evidence_directory_cannot_contain_a_corpus_root(tmp_path: Path) -> None:
    with pytest.raises(release_checklist.ChecklistError, match="outside"):
        release_checklist._safe_external(tmp_path, (tmp_path / "corpus",))  # noqa: SLF001


@pytest.mark.verifies("TST057")
def test_private_resume_context_preserves_paths_outside_portable_state(
    inputs: release_checklist.Inputs, tmp_path: Path
) -> None:
    evidence = tmp_path / "evidence"
    context = release_checklist._write_resume_context(evidence, inputs)  # noqa: SLF001

    assert release_checklist._load_resume_context(context) == inputs  # noqa: SLF001
    assert json.loads(context.read_text(encoding="utf-8"))["formal_root"] == str(inputs.formal_root)
    assert str(inputs.formal_root) not in json.dumps(inputs.public())


@pytest.mark.verifies("TST057")
def test_scaling_and_network_phases_fail_closed_before_effects(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: tmp_path / "repository")
    with pytest.raises(release_checklist.ChecklistError, match="requires --allow-network"):
        release_checklist.run_checklist(
            inputs, tmp_path / "evidence", "verify-testpypi", dry_run=True, allow_network=False
        )
    scaling = release_checklist.Inputs(
        inputs.version,
        inputs.tag,
        inputs.freeze_commit,
        inputs.source_date_epoch,
        inputs.formal_root,
        inputs.supplemental_root,
        None,
        inputs.github_issue,
    )
    with pytest.raises(release_checklist.ChecklistError, match="cannot skip unmet prerequisites"):
        release_checklist.run_checklist(
            scaling, tmp_path / "evidence", "scaling", dry_run=True, allow_network=False
        )


@pytest.mark.verifies("TST057")
def test_preflight_does_not_require_a_release_tag(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    monkeypatch.setattr(release_checklist, "_phase_commands", lambda *_args: [])
    monkeypatch.setattr(
        release_checklist,
        "_verify_frozen_checkout",
        lambda *_args: {"status": "passed"},
    )

    assert (
        release_checklist.run_checklist(
            inputs, tmp_path / "evidence", "preflight", dry_run=False, allow_network=False
        )["status"]
        == "passed"
    )


@pytest.mark.verifies("TST057")
def test_rerun_compares_staged_evidence_before_replacing_it(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    monkeypatch.setattr(release_checklist, "_phase_commands", lambda *_args: [])
    monkeypatch.setattr(
        release_checklist, "_verify_frozen_checkout", lambda *_args: {"status": "passed"}
    )
    evidence = tmp_path / "evidence"
    first = release_checklist.run_checklist(
        inputs, evidence, "preflight", dry_run=False, allow_network=False
    )
    original = (evidence / "preflight.json").read_bytes()
    second = release_checklist.run_checklist(
        inputs, evidence, "preflight", dry_run=False, allow_network=False
    )

    assert second["digest"] == first["digest"]
    assert (evidence / "preflight.json").read_bytes() == original


@pytest.mark.verifies("TST057")
def test_hosted_verification_requires_matching_tag_ref(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Completed:
        returncode = 0
        stdout = json.dumps(
            [
                {
                    "headSha": "b" * 40,
                    "headBranch": "develop",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        )

    monkeypatch.setattr(release_checklist, "_git", lambda *_args: "b" * 40)
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: Completed())
    with pytest.raises(release_checklist.ChecklistError, match="immutable tag"):
        release_checklist._verify_hosted_phase(  # noqa: SLF001
            tmp_path, inputs, "verify-testpypi", "2026-01-01T00:00:00Z"
        )


@pytest.mark.verifies("TST057")
def test_direct_gate_dry_run_is_side_effect_free(tmp_path: Path) -> None:
    with pytest.raises(release_checklist.ChecklistError, match="cannot be combined"):
        release_checklist.main(
            [
                "--version",
                "1.2.3",
                "--tag",
                "v1.2.3",
                "--freeze-commit",
                "a" * 40,
                "--source-date-epoch",
                "1",
                "--formal-root",
                str(tmp_path / "formal"),
                "--supplemental-root",
                str(tmp_path / "supplemental"),
                "--evidence-directory",
                str(tmp_path / "evidence"),
                "--phase",
                "formal-corpus",
                "--verify-gate",
                "--dry-run",
            ]
        )
