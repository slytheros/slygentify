"""Tests for the fail-closed post-1.0 release checklist."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import cast

import pytest

from tools import measure_acceptance, release_checklist


@pytest.fixture
def inputs(tmp_path: Path) -> release_checklist.Inputs:
    formal = tmp_path / "formal"
    supplemental = tmp_path / "supplemental"
    formal.mkdir()
    supplemental.mkdir()
    return release_checklist.Inputs(
        "1.2.3",
        "v1.2.3",
        "a" * 40,
        None,
        formal,
        supplemental,
        None,
        22,
    )


@pytest.mark.verifies("TST057")
def test_dry_run_reports_effects_without_writing(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: tmp_path / "repository")
    monkeypatch.setattr(
        release_checklist, "_verify_frozen_checkout", lambda *_args: {"status": "passed"}
    )
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
    monkeypatch.setattr(
        release_checklist, "_verify_frozen_checkout", lambda *_args: {"status": "passed"}
    )

    preflight = release_checklist.run_checklist(
        inputs, tmp_path / "evidence", "preflight", dry_run=True, allow_network=False
    )
    package_writes = release_checklist._phase_writes("package")  # noqa: SLF001

    preflight_writes = cast(
        tuple[str, ...], cast(dict[str, object], preflight["effects"])["writes"]
    )
    assert "repository/.coverage" in preflight_writes
    assert "evidence/package-dist/" not in package_writes
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
            "promotion": None,
        },
    )
    packet = release_checklist._gate_packet(  # noqa: SLF001
        inputs, "formal-corpus", digest, evidence / "resume-context.json"
    )
    release_checklist._write(evidence / "formal-corpus-review-packet.json", packet)  # noqa: SLF001

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "comments": [
                    {
                        "author": {"login": release_checklist.RELEASE_MAINTAINER},
                        "body": release_checklist._gate_comment(  # noqa: SLF001
                            "semantic-corpus", str(packet["packet_digest"])
                        ),
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-01-01T00:00:00Z",
                        "includesCreatedEdit": False,
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
            "promotion": None,
        },
    )
    packet = release_checklist._gate_packet(  # noqa: SLF001
        inputs, "formal-corpus", digest, evidence / "resume-context.json"
    )
    release_checklist._write(evidence / "formal-corpus-review-packet.json", packet)  # noqa: SLF001

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "comments": [
                    {
                        "author": {"login": "untrusted"},
                        "body": release_checklist._gate_comment("semantic-corpus", digest),  # noqa: SLF001
                        "createdAt": "2026-01-01T00:00:00Z",
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
def test_evidence_writer_refuses_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("untouched", encoding="utf-8")
    link = tmp_path / "state.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(release_checklist.ChecklistError, match="symlinked"):
        release_checklist._write(link, {"value": "new"})  # noqa: SLF001
    assert target.read_text(encoding="utf-8") == "untouched"


@pytest.mark.verifies("TST057")
def test_evidence_writer_preserves_existing_state_when_replacement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "release-checklist-state.json"
    state.write_text("original", encoding="utf-8")

    def fail_replace(*_args: object) -> None:
        raise OSError("full")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="full"):
        release_checklist._write(state, {"replacement": True})  # noqa: SLF001
    assert state.read_text(encoding="utf-8") == "original"


@pytest.mark.verifies("TST057")
def test_predecessor_validation_refuses_symlinked_phase_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    target = tmp_path / "preflight.json"
    record: dict[str, object] = {"artifacts": {}}
    target.write_bytes(release_checklist._canonical(record))  # noqa: SLF001
    phase = evidence / "preflight.json"
    try:
        phase.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    state = {
        "phases": {"preflight": {"digest": release_checklist._digest(record), "artifacts": {}}}
    }  # noqa: SLF001

    with pytest.raises(release_checklist.ChecklistError, match="stale or missing"):
        release_checklist._require_predecessors(state, "formal-corpus", evidence)  # noqa: SLF001


@pytest.mark.verifies("TST057")
def test_artifact_publisher_refuses_a_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("source", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    target = tmp_path / "target.json"
    target.write_text("untouched", encoding="utf-8")
    link = evidence / "formal-report.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(release_checklist.ChecklistError, match="symlinked"):
        release_checklist._publish_artifact(source, evidence, "formal-report.json")  # noqa: SLF001
    assert target.read_text(encoding="utf-8") == "untouched"


@pytest.mark.verifies("TST057")
def test_path_identity_rejects_missing_or_non_directory_roots(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    file = tmp_path / "file"
    file.write_text("not a corpus", encoding="utf-8")

    for root in (missing, file):
        with pytest.raises(release_checklist.ChecklistError, match="existing directory"):
            release_checklist._path_identity(root)  # noqa: SLF001


@pytest.mark.verifies("TST057")
def test_path_identity_excludes_mutable_git_metadata(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "source.txt"
    source.write_text("stable", encoding="utf-8")
    git_metadata = corpus / ".git"
    git_metadata.mkdir()
    fetch_head = git_metadata / "FETCH_HEAD"
    fetch_head.write_text("first", encoding="utf-8")

    initial = release_checklist._path_identity(corpus)  # noqa: SLF001
    fetch_head.write_text("second", encoding="utf-8")

    assert release_checklist._path_identity(corpus) == initial  # noqa: SLF001


@pytest.mark.verifies("TST057")
def test_path_identity_includes_link_entries_without_reading_their_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not become corpus input", encoding="utf-8")
    link = corpus / "linked-secret.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    read_bytes = Path.read_bytes

    def no_target_read(candidate: Path) -> bytes:
        if candidate == outside:
            pytest.fail("corpus identity must not read a linked target")
        return read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", no_target_read)

    with_link = release_checklist._path_identity(corpus)  # noqa: SLF001
    link.unlink()

    assert release_checklist._path_identity(corpus) != with_link  # noqa: SLF001


@pytest.mark.verifies("TST057")
def test_path_identity_does_not_read_sensitive_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    secret = corpus / ".env"
    secret.write_text("SECRET=do-not-read", encoding="utf-8")
    read_bytes = Path.read_bytes

    def no_secret_read(candidate: Path) -> bytes:
        if candidate == secret:
            pytest.fail("corpus identity must not read sensitive content")
        return read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", no_secret_read)
    initial = release_checklist._path_identity(corpus)  # noqa: SLF001
    secret.unlink()

    assert release_checklist._path_identity(corpus) != initial  # noqa: SLF001


@pytest.mark.verifies("TST057")
def test_dry_run_validates_checkout_preconditions(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    monkeypatch.setattr(
        release_checklist,
        "_verify_frozen_checkout",
        lambda *_args: (_ for _ in ()).throw(release_checklist.ChecklistError("checkout is dirty")),
    )

    with pytest.raises(release_checklist.ChecklistError, match="checkout is dirty"):
        release_checklist.run_checklist(
            inputs, tmp_path / "evidence", "preflight", dry_run=True, allow_network=False
        )


@pytest.mark.verifies("TST057")
def test_successor_phases_revalidate_prior_human_gates(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = tmp_path / "evidence"
    verified: list[str] = []
    monkeypatch.setattr(
        release_checklist,
        "verify_human_gate",
        lambda _inputs, _evidence, phase: verified.append(phase),
    )

    release_checklist._revalidate_gate_approvals(  # noqa: SLF001
        inputs, evidence, "package", allow_network=True
    )

    assert verified == ["formal-corpus", "initialization-review", "promotion-gate"]
    with pytest.raises(release_checklist.ChecklistError, match="--allow-network"):
        release_checklist._revalidate_gate_approvals(  # noqa: SLF001
            inputs, evidence, "package", allow_network=False
        )


@pytest.mark.verifies("TST057")
def test_dry_run_does_not_revalidate_prior_human_gates(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    promoted = release_checklist.Inputs(
        inputs.version,
        inputs.tag,
        inputs.freeze_commit,
        123,
        inputs.formal_root,
        inputs.supplemental_root,
        inputs.composed_root,
        inputs.github_issue,
        "b" * 40,
        37,
    )
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    monkeypatch.setattr(release_checklist, "_require_predecessors", lambda *_args: None)
    monkeypatch.setattr(release_checklist, "_validate_promotion_binding", lambda *_args: None)
    monkeypatch.setattr(release_checklist, "_phase_commands", lambda *_args: [])
    monkeypatch.setattr(release_checklist, "_verify_promoted_checkout", lambda *_args: {})
    monkeypatch.setattr(
        release_checklist,
        "_revalidate_gate_approvals",
        lambda *_args, **_kwargs: pytest.fail("dry-run must not revalidate a remote gate"),
    )

    assert (
        release_checklist.run_checklist(
            promoted, tmp_path / "evidence", "package", dry_run=True, allow_network=True
        )["status"]
        == "planned"
    )


@pytest.mark.verifies("TST057")
def test_supplemental_measurement_rejects_linked_checkout(tmp_path: Path) -> None:
    root = tmp_path / "supplemental"
    root.mkdir()
    external = tmp_path / "external-checkout"
    (external / ".git").mkdir(parents=True)
    linked = root / "linked-checkout"
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")

    with pytest.raises(measure_acceptance.CorpusError, match="not a direct corpus directory"):
        measure_acceptance._supplemental_measurement(root)  # noqa: SLF001


@pytest.mark.verifies("TST057")
def test_release_git_checks_clear_inherited_git_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    class Completed:
        returncode = 0
        stdout = "verified\n"
        stderr = ""

    def fake_run(_command: list[str], **kwargs: object) -> Completed:
        captured.update(cast(dict[str, str], kwargs["env"]))
        return Completed()

    monkeypatch.setenv("GIT_DIR", "outside")
    monkeypatch.setenv("GIT_WORK_TREE", "outside")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "include.path=outside")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert release_checklist._git(tmp_path, "rev-parse", "HEAD") == "verified"  # noqa: SLF001
    assert "GIT_DIR" not in captured
    assert "GIT_WORK_TREE" not in captured
    assert "GIT_CONFIG_PARAMETERS" not in captured
    assert captured["GIT_CONFIG_GLOBAL"] == os.devnull
    assert captured["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.mark.verifies("TST057")
def test_formal_snapshot_preserves_links_without_reading_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be copied", encoding="utf-8")
    link = checkout / "linked-secret.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    monkeypatch.setattr(measure_acceptance, "_git", lambda *_args: "")
    hooks_directory = tmp_path / "disabled-hooks"
    hooks_directory.mkdir()

    snapshot = measure_acceptance._snapshot(  # noqa: SLF001
        checkout, "a" * 40, tmp_path / "snapshot", hooks_directory
    )
    copied_link = snapshot / "linked-secret.txt"

    assert copied_link.is_symlink()
    assert os.readlink(copied_link) == os.readlink(link)
    assert not (snapshot / "outside-secret.txt").exists()


@pytest.mark.verifies("TST057")
def test_human_gate_rejects_embedded_approval_record(
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
            "promotion": None,
        },
    )
    packet = release_checklist._gate_packet(  # noqa: SLF001
        inputs, "formal-corpus", digest, evidence / "resume-context.json"
    )
    release_checklist._write(evidence / "formal-corpus-review-packet.json", packet)  # noqa: SLF001

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "comments": [
                    {
                        "author": {"login": release_checklist.RELEASE_MAINTAINER},
                        "body": "quoted "
                        + release_checklist._gate_comment("semantic-corpus", digest),
                        "createdAt": "2026-01-01T00:00:00Z",
                    }
                ]
            }
        )

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: Completed())
    with pytest.raises(release_checklist.ChecklistError, match="does not contain"):
        release_checklist.verify_human_gate(inputs, evidence, "formal-corpus")


@pytest.mark.verifies("TST057")
def test_human_gate_rejects_an_edited_approval_record(
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
            "promotion": None,
        },
    )
    packet = release_checklist._gate_packet(  # noqa: SLF001
        inputs, "formal-corpus", digest, evidence / "resume-context.json"
    )
    release_checklist._write(evidence / "formal-corpus-review-packet.json", packet)  # noqa: SLF001

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "comments": [
                    {
                        "author": {"login": release_checklist.RELEASE_MAINTAINER},
                        "body": release_checklist._gate_comment("semantic-corpus", digest),
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-01-01T00:00:01Z",
                    }
                ]
            }
        )

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: Completed())
    with pytest.raises(release_checklist.ChecklistError, match="does not contain"):
        release_checklist.verify_human_gate(inputs, evidence, "formal-corpus")


@pytest.mark.verifies("TST057")
def test_gate_packet_quotes_a_resume_context_path(
    inputs: release_checklist.Inputs, tmp_path: Path
) -> None:
    context = tmp_path / "evidence with spaces" / ".release-checklist-resume.json"
    packet = release_checklist._gate_packet(inputs, "formal-corpus", "a" * 64, context)  # noqa: SLF001

    assert shlex.split(str(packet["resume"])) == [
        "python",
        "-m",
        "tools.release_checklist",
        "--resume",
        "--resume-context",
        str(context),
        "--phase",
        "initialization-review",
        "--allow-network",
    ]


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
def test_preflight_propagates_offline_uv_controls(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    environments: list[dict[str, str] | None] = []
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    monkeypatch.setattr(release_checklist, "_phase_commands", lambda *_args: [["pre-commit"]])
    monkeypatch.setattr(
        release_checklist, "_verify_frozen_checkout", lambda *_args: {"status": "passed"}
    )

    def capture_environment(
        *_args: object, environment: dict[str, str] | None, **_kwargs: object
    ) -> dict[str, object]:
        environments.append(environment)
        return {"status": "passed"}

    monkeypatch.setattr(release_checklist, "_run_command", capture_environment)

    release_checklist.run_checklist(
        inputs, tmp_path / "evidence", "preflight", dry_run=False, allow_network=False
    )

    assert environments == [{"UV_OFFLINE": "1", "UV_NO_SYNC": "1"}]


@pytest.mark.verifies("TST057")
@pytest.mark.parametrize(
    "phase", ["verify-gitflow", "verify-testpypi", "verify-pypi", "verify-github-release"]
)
def test_network_verification_never_executes_display_only_command_plans(
    inputs: release_checklist.Inputs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    bound = release_checklist.Inputs(
        inputs.version,
        inputs.tag,
        inputs.freeze_commit,
        123,
        inputs.formal_root,
        inputs.supplemental_root,
        inputs.composed_root,
        inputs.github_issue,
        "b" * 40,
        37,
    )
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: repository)
    monkeypatch.setattr(release_checklist, "_require_predecessors", lambda *_args: None)
    monkeypatch.setattr(
        release_checklist, "_revalidate_gate_approvals", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        release_checklist,
        "_load_state",
        lambda *_args: {"phases": {}, "promotion": release_checklist._promotion_binding(bound)},  # noqa: SLF001
    )
    monkeypatch.setattr(release_checklist, "_phase_commands", lambda *_args: [["PLACEHOLDER"]])
    monkeypatch.setattr(
        release_checklist,
        "_run_command",
        lambda *_args, **_kwargs: pytest.fail("display-only plan must not execute"),
    )
    monkeypatch.setattr(release_checklist, "_verify_gitflow", lambda *_args: {"status": "passed"})
    monkeypatch.setattr(
        release_checklist, "_verify_hosted_phase", lambda *_args: {"status": "passed"}
    )

    assert (
        release_checklist.run_checklist(
            bound, tmp_path / "evidence", phase, dry_run=False, allow_network=True
        )["status"]
        == "passed"
    )


@pytest.mark.verifies("TST057")
def test_gitflow_requires_a_reviewed_develop_to_main_merge(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    promoted = release_checklist.Inputs(
        inputs.version,
        inputs.tag,
        inputs.freeze_commit,
        123,
        inputs.formal_root,
        inputs.supplemental_root,
        inputs.composed_root,
        inputs.github_issue,
        "b" * 40,
        37,
    )

    def fake_git(_root: Path, *arguments: str) -> str:
        if arguments == ("status", "--porcelain", "--untracked-files=all"):
            return ""
        if arguments == ("show", "-s", "--format=%ct", "b" * 40):
            return "123"
        if arguments == ("cat-file", "-t", f"refs/tags/{promoted.tag}"):
            return "tag"
        if arguments == ("rev-list", "-n", "1", promoted.tag):
            return "b" * 40
        if arguments in {
            ("rev-parse", f"{promoted.freeze_commit}^{{tree}}"),
            ("rev-parse", f"{'b' * 40}^{{tree}}"),
        }:
            return "d" * 40
        raise AssertionError(arguments)

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        result = Completed()
        if command[:3] == ["gh", "api", "graphql"]:
            result.stdout = json.dumps(
                {
                    "data": {
                        "repository": {
                            "object": {
                                "associatedPullRequests": {
                                    "nodes": [
                                        {
                                            "number": 37,
                                            "mergedAt": "2026-01-01T00:00:00Z",
                                            "baseRefName": "main",
                                            "headRefName": "release/1.2.3",
                                            "mergeCommit": {"oid": "b" * 40},
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            )
        elif any("/git/ref/heads/" in value for value in command):
            result.returncode = 1
            result.stdout = "HTTP/2 404 Not Found\n"
        elif any("/git/ref/tags/" in value for value in command):
            result.stdout = json.dumps({"object": {"type": "tag", "sha": "e" * 40}})
        elif any("/git/tags/" in value for value in command):
            result.stdout = json.dumps({"object": {"type": "commit", "sha": "b" * 40}})
        elif any("pulls?state=closed" in value for value in command):
            result.stdout = json.dumps(
                [
                    {
                        "merged_at": "2026-01-01T00:00:01Z",
                        "merge_commit_sha": "c" * 40,
                    }
                ]
            )
        elif command[:2] == ["gh", "api"]:
            result.stdout = json.dumps({"behind_by": 0})
        return result

    monkeypatch.setattr(release_checklist, "_git", fake_git)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        release_checklist._verify_gitflow(  # noqa: SLF001
            tmp_path, promoted, "2025-12-31T00:00:00Z"
        )["promotion_pull_request"]
        == 37
    )


@pytest.mark.verifies("TST057")
def test_package_phase_follows_promotion_verification() -> None:
    assert release_checklist.PHASES.index("verify-gitflow") < release_checklist.PHASES.index(
        "package"
    )


@pytest.mark.verifies("TST057")
def test_promotion_binding_derives_the_build_epoch(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_checklist, "_repository_root", lambda: tmp_path / "repository")
    monkeypatch.setattr(release_checklist, "_git", lambda *_args: "123")

    bound = release_checklist._bind_promotion(inputs, "b" * 40, 37)  # noqa: SLF001

    assert bound.promotion_commit == "b" * 40
    assert bound.promotion_pull_request == 37
    assert bound.source_date_epoch == 123
    assert bound.initial_public() == inputs.initial_public()


@pytest.mark.verifies("TST057")
def test_promotion_binding_cannot_change_after_gitflow_verification(
    inputs: release_checklist.Inputs,
) -> None:
    bound = release_checklist.Inputs(
        inputs.version,
        inputs.tag,
        inputs.freeze_commit,
        123,
        inputs.formal_root,
        inputs.supplemental_root,
        inputs.composed_root,
        inputs.github_issue,
        "b" * 40,
        37,
    )
    state = {"promotion": release_checklist._promotion_binding(bound)}  # noqa: SLF001
    replacement = release_checklist.Inputs(
        inputs.version,
        inputs.tag,
        inputs.freeze_commit,
        456,
        inputs.formal_root,
        inputs.supplemental_root,
        inputs.composed_root,
        inputs.github_issue,
        "c" * 40,
        38,
    )

    with pytest.raises(release_checklist.ChecklistError, match="does not match"):
        release_checklist._validate_promotion_binding(state, replacement, "package")  # noqa: SLF001


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
def test_testpypi_verification_requires_post_gate_publication_job(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Completed:
        returncode = 0
        stdout = ""

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        result = Completed()
        if command[1:3] == ["run", "list"]:
            result.stdout = json.dumps(
                [
                    {
                        "databaseId": 1,
                        "url": "https://example.invalid/runs/1",
                        "headSha": "b" * 40,
                        "headBranch": inputs.tag,
                        "status": "completed",
                        "conclusion": "success",
                        "createdAt": "2026-01-01T00:00:01Z",
                    }
                ]
            )
        else:
            result.stdout = json.dumps(
                {"jobs": [{"name": "Verify only", "startedAt": "2026-01-01T00:00:01Z"}]}
            )
        return result

    monkeypatch.setattr(release_checklist, "_git", lambda *_args: "b" * 40)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(release_checklist.ChecklistError, match="publication job"):
        release_checklist._verify_hosted_phase(  # noqa: SLF001
            tmp_path, inputs, "verify-testpypi", "2026-01-01T00:00:00Z"
        )


@pytest.mark.verifies("TST057")
def test_testpypi_verification_accepts_an_older_qualifying_publication(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Completed:
        returncode = 0
        stdout = ""

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        result = Completed()
        if command[1:3] == ["run", "list"]:
            result.stdout = json.dumps(
                [
                    {
                        "databaseId": 2,
                        "url": "https://example.invalid/runs/2",
                        "headSha": "b" * 40,
                        "headBranch": inputs.tag,
                        "status": "completed",
                        "conclusion": "success",
                        "createdAt": "2026-01-01T00:00:02Z",
                    },
                    {
                        "databaseId": 1,
                        "url": "https://example.invalid/runs/1",
                        "headSha": "b" * 40,
                        "headBranch": inputs.tag,
                        "status": "completed",
                        "conclusion": "success",
                        "createdAt": "2026-01-01T00:00:01Z",
                    },
                ]
            )
        elif command[3] == "1":
            result.stdout = json.dumps(
                {
                    "jobs": [
                        {
                            "name": "Publish approved files to TestPyPI",
                            "startedAt": "2026-01-01T00:00:01Z",
                        }
                    ]
                }
            )
        else:
            result.stdout = json.dumps({"jobs": [{"name": "Verify only"}]})
        return result

    monkeypatch.setattr(release_checklist, "_git", lambda *_args: "b" * 40)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = release_checklist._verify_hosted_phase(  # noqa: SLF001
        tmp_path, inputs, "verify-testpypi", "2026-01-01T00:00:00Z"
    )

    assert result["status"] == "passed"
    assert result["workflow_run_id"] == 1
    assert result["workflow_run_url"] == "https://example.invalid/runs/1"


@pytest.mark.verifies("TST057")
def test_pypi_verification_accepts_a_verify_only_recovery_run(
    inputs: release_checklist.Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Completed:
        returncode = 0
        stdout = ""

    def fake_run(command: list[str], **_kwargs: object) -> Completed:
        result = Completed()
        if command[1:3] == ["run", "list"]:
            result.stdout = json.dumps(
                [
                    {
                        "databaseId": 2,
                        "url": "https://example.invalid/runs/2",
                        "headSha": "b" * 40,
                        "headBranch": inputs.tag,
                        "status": "completed",
                        "conclusion": "success",
                        "createdAt": "2026-01-01T00:00:02Z",
                    },
                    {
                        "databaseId": 1,
                        "url": "https://example.invalid/runs/1",
                        "headSha": "b" * 40,
                        "headBranch": inputs.tag,
                        "status": "completed",
                        "conclusion": "failure",
                        "createdAt": "2026-01-01T00:00:01Z",
                    },
                ]
            )
        elif command[3] == "1":
            result.stdout = json.dumps(
                {
                    "jobs": [
                        {
                            "name": "Publish approved files to PyPI",
                            "startedAt": "2026-01-01T00:00:01Z",
                            "conclusion": "success",
                        }
                    ]
                }
            )
        else:
            result.stdout = json.dumps(
                {
                    "jobs": [
                        {
                            "name": "Verify PyPI hashes and provenance",
                            "conclusion": "success",
                        }
                    ]
                }
            )
        return result

    monkeypatch.setattr(release_checklist, "_git", lambda *_args: "b" * 40)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = release_checklist._verify_hosted_phase(  # noqa: SLF001
        tmp_path, inputs, "verify-pypi", "2026-01-01T00:00:00Z"
    )

    assert result["workflow_run_id"] == 2
    assert result["publication_run_id"] == 1


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
                "--formal-root",
                str(tmp_path / "formal"),
                "--supplemental-root",
                str(tmp_path / "supplemental"),
                "--evidence-directory",
                str(tmp_path / "evidence"),
                "--composed-root",
                str(tmp_path / "composed"),
                "--github-issue",
                "22",
                "--phase",
                "formal-corpus",
                "--verify-gate",
                "--dry-run",
            ]
        )
