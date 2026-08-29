"""Repository-level quality and requirements checks."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest


@pytest.mark.verifies("TST052")
def test_distribution_artifacts_are_contained_and_preserve_runtime_contract(tmp_path: Path) -> None:
    repository_root = Path(__file__).parents[1]
    outputs = (tmp_path / "first", tmp_path / "second")
    environment = os.environ | {"SOURCE_DATE_EPOCH": "1700000000"}
    for output in outputs:
        result = subprocess.run(
            [
                "uv",
                "build",
                "--offline",
                "--no-build-isolation",
                "--no-sources",
                "--no-create-gitignore",
                "--out-dir",
                str(output),
            ],
            cwd=repository_root,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    output = outputs[0]
    first_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(outputs[0].iterdir())
    }
    second_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(outputs[1].iterdir())
    }
    assert first_hashes == second_hashes

    sdist = output / "slygentify-1.0.0rc1.tar.gz"
    wheel = output / "slygentify-1.0.0rc1-py3-none-any.whl"
    prefix = "slygentify-1.0.0rc1/"
    source_files = sorted(
        path.relative_to(repository_root).as_posix()
        for path in (repository_root / "src" / "slygentify").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    expected_sdist = {
        ".gitignore",
        "CHANGELOG.md",
        "LICENSE",
        "PKG-INFO",
        "README.md",
        "pyproject.toml",
        *source_files,
    }
    with tarfile.open(sdist, "r:gz") as archive:
        members = {member.name.removeprefix(prefix) for member in archive.getmembers()}
    assert members == expected_sdist

    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        metadata = archive.read("slygentify-1.0.0rc1.dist-info/METADATA").decode("utf-8")
        entry_points = archive.read("slygentify-1.0.0rc1.dist-info/entry_points.txt").decode(
            "utf-8"
        )
    assert "slygentify/_acceptance.py" not in members
    assert "slygentify/_initialization_acceptance.py" not in members
    assert "slygentify/schemas/scan-v1.schema.json" in members
    assert "Version: 1.0.0rc1" in metadata
    assert "License-Expression: Apache-2.0" in metadata
    assert "slygentify = slygentify.cli:app" in entry_points


def _write_minimal_doorstop_tree(repository: Path, child_item: str) -> None:
    requirement_directory = repository / "requirements"
    test_directory = requirement_directory / "tests"
    test_directory.mkdir(parents=True)
    (requirement_directory / ".doorstop.yml").write_text(
        "settings:\n  digits: 3\n  itemformat: yaml\n  prefix: REQ\n  sep: ''\n",
        encoding="utf-8",
    )
    (test_directory / ".doorstop.yml").write_text(
        "settings:\n  digits: 3\n  itemformat: yaml\n  parent: REQ\n  prefix: TST\n  sep: ''\n",
        encoding="utf-8",
    )
    (requirement_directory / "REQ001.yml").write_text(
        "active: true\nderived: false\nlevel: 1.1\nlinks: []\n"
        "normative: true\nreviewed: null\ntext: A valid requirement.\n",
        encoding="utf-8",
    )
    (test_directory / "TST001.yml").write_text(child_item, encoding="utf-8")


@pytest.mark.verifies("TST007")
def test_doorstop_requirements_are_valid() -> None:
    repository_root = Path(__file__).parents[1]

    result = subprocess.run(
        ["doorstop"],
        cwd=repository_root,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.verifies("TST007")
@pytest.mark.parametrize(
    "invalid_item",
    [
        (
            "active: true\nderived: false\nlevel: 1.1\nlinks:\n- REQ999: null\n"
            "normative: true\nreviewed: null\ntext: Broken parent link.\n"
        ),
        (
            "active: true\nderived: false\nlevel: 1.1\nlinks:\n- REQ001: null\n"
            "normative: true\nreferences:\n- path: missing.py\n  type: file\n"
            "reviewed: null\ntext: Broken external reference.\n"
        ),
    ],
)
def test_doorstop_rejects_broken_links_and_references(tmp_path: Path, invalid_item: str) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        capture_output=True,
        check=True,
        text=True,
    )
    _write_minimal_doorstop_tree(tmp_path, invalid_item)

    result = subprocess.run(
        ["doorstop"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0


@pytest.mark.verifies("TST008")
def test_pytest_enforces_full_branch_coverage() -> None:
    configuration = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert '"--cov=slygentify"' in configuration
    assert '"--cov-branch"' in configuration
    assert '"--cov-fail-under=100"' in configuration
    assert "fail_under = 100" in configuration


@pytest.mark.verifies("TST009")
def test_contribution_control_plane_is_complete_and_portable() -> None:
    repository_root = Path(__file__).parents[1]
    template_directory = repository_root / ".github" / "ISSUE_TEMPLATE"
    expected_templates = {
        "decision.md": ("Decision", "Kind/Decision", ["## Decision requested", "## Dependencies"]),
        "research.md": (
            "Research",
            "Kind/Research",
            ["## Research question", "## Evidence sources"],
        ),
        "feature.md": ("Feature", "Kind/Feature", ["## User outcome", "## Effects and security"]),
        "bug.md": ("Bug", "Kind/Bug", ["## Observed behavior", "## Expected behavior"]),
    }

    for filename, (name, label, headings) in expected_templates.items():
        content = (template_directory / filename).read_text(encoding="utf-8")
        assert content.startswith("---\n")
        frontmatter, body = content[4:].split("\n---\n", maxsplit=1)
        assert f"name: {name}" in frontmatter
        assert "about:" in frontmatter
        assert 'title: ""' in frontmatter
        assert f'labels: ["{label}"]' in frontmatter
        for heading in headings:
            assert heading in body

    chooser = (template_directory / "config.yml").read_text(encoding="utf-8")
    assert "blank_issues_enabled: true" in chooser

    contributing = (repository_root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for required_text in (
        "## Requirements and traceability",
        "### Agent Definition of Ready",
        "### Agent Definition of Done",
        "uv run pre-commit run --all-files",
        "Agents do not approve decisions",
    ):
        assert required_text in contributing

    pull_request = (repository_root / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
        encoding="utf-8"
    )
    for required_heading in (
        "## Requirements and traceability",
        "## Validation",
        "## Effects",
        "## Security and compatibility",
        "## Documentation and decisions",
        "## Definition of Ready/Done",
    ):
        assert required_heading in pull_request

    adr_directory = repository_root / "docs" / "adr"
    adr_index = (adr_directory / "README.md").read_text(encoding="utf-8")
    numbered_adrs = sorted(path.name for path in adr_directory.glob("[0-9][0-9][0-9][0-9]-*.md"))
    assert numbered_adrs
    for filename in numbered_adrs:
        assert f"({filename})" in adr_index

    adr_template = (adr_directory / "template.md").read_text(encoding="utf-8")
    for required_heading in (
        "## Status",
        "## Context",
        "## Decision",
        "## Consequences",
        "## Alternatives considered",
        "## Approval record",
    ):
        assert required_heading in adr_template


@pytest.mark.verifies("TST009")
def test_root_agent_guide_is_a_bounded_high_signal_router() -> None:
    repository_root = Path(__file__).parents[1]
    guide_path = repository_root / "AGENTS.md"
    guide = guide_path.read_text(encoding="utf-8")
    normalized_guide = " ".join(guide.split())

    assert len(guide_path.read_bytes()) <= 6 * 1024
    for required_text in (
        "implements `init`, `scan`",
        "`map`, and static `doctor`",
        "Sandboxed command verification",
        "CONTRIBUTING.md",
        "@implements",
        "@pytest.mark.verifies",
        "uv sync --locked --all-groups",
        "uv run pre-commit run --all-files",
        "configured forge connector",
        "CodeGraph",
        "`rg` and direct file inspection",
        "do not approve or merge pull requests",
    ):
        assert required_text in normalized_guide
    for stale_text in (
        "active milestone",
        "A possible future package structure",
        "Component overrides are not loaded",
        "future scan API",
    ):
        assert stale_text.casefold() not in guide.casefold()


@pytest.mark.verifies("TST009")
def test_tracked_markdown_relative_links_resolve() -> None:
    repository_root = Path(__file__).parents[1]
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.md"],
        cwd=repository_root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.splitlines()
    link_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    broken: list[str] = []

    for relative_name in tracked:
        markdown = repository_root / relative_name
        for raw_target in link_pattern.findall(markdown.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
            if not target or target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target):
                continue
            local_target = target.split("#", maxsplit=1)[0]
            if not (markdown.parent / local_target).resolve().exists():
                broken.append(f"{relative_name}: {target}")

    assert not broken, "broken relative Markdown links:\n" + "\n".join(broken)


@pytest.mark.verifies("TST009")
def test_documentation_matches_supported_commands_and_current_capabilities() -> None:
    repository_root = Path(__file__).parents[1]
    readme = (repository_root / "README.md").read_text(encoding="utf-8")
    documentation = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((repository_root / "docs").glob("*.md"))
    )

    for command in (
        "slygentify init",
        "slygentify scan",
        "slygentify map",
        "slygentify doctor",
    ):
        assert command in readme
    assert "Doctor does not execute discovered validation commands" in readme
    for stale_claim in (
        "scan does not yet load `slygentify.toml`",
        "component configuration is not yet loaded",
        "public-contribution CI is already implemented",
        "C:\\Users\\kourk",
    ):
        assert stale_claim.casefold() not in documentation.casefold()

    codegraph_ignore = repository_root / ".codegraph" / ".gitignore"
    assert codegraph_ignore.read_text(encoding="utf-8").endswith("*\n!.gitignore\n")
    tracked_ignore = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".codegraph/.gitignore"],
        cwd=repository_root,
        capture_output=True,
        check=False,
        text=True,
    )
    assert tracked_ignore.returncode == 0
