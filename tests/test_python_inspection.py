"""Behavioral tests for the approved bounded Python evidence matrix."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

import slygentify._scan.detectors.python as private_scan
import slygentify._scan.normalization as normalization
from slygentify import Finding, ScanResult, dump_scan_json, scan_repository
from slygentify._scan import kernel
from slygentify._scan.contracts import (
    DetectionContext,
    DetectionResult,
    FindingCandidate,
    PathCandidate,
)
from slygentify._scan.detectors._support import (
    StaticStructureError,
    evidence_key,
    strict_yaml_document,
)
from slygentify._scan.paths import path_metadata


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    return root


def _write(root: Path, path: str, content: str = "") -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _codes(result: ScanResult, collection: str) -> set[str]:
    items = result.findings if collection == "findings" else result.diagnostics
    return {item.code for item in items}


def _summaries(result: ScanResult, code: str) -> set[str]:
    return {item.summary for item in result.findings if item.code == code}


@pytest.mark.verifies("TST020", "TST021", "TST022", "TST024")
def test_pep621_uv_workspace_and_supported_evidence(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "pyproject.toml",
        """
[project]
name = "root-project"
requires-python = ">=3.11"
dependencies = ["FastAPI>=0.1", "pytest", "SQLAlchemy"]
optional-dependencies.docs = ["Flask", "Django"]
scripts.root = "root:main"
gui-scripts.gui = "root:gui"
dynamic = ["version"]

[dependency-groups]
dev = ["ruff", "pytest-cov", "pre-commit"]

[tool.uv]
[tool.uv.workspace]
members = ["packages/*"]
exclude = ["packages/excluded"]

[tool.pytest.ini_options]
addopts = "-q"
[tool.ruff]
[tool.black]
[tool.mypy]
[tool.pyright]
[tool.coverage.run]
[tool.tox]
""".strip(),
    )
    _write(
        root,
        "packages/member/pyproject.toml",
        '[project]\nname = "member"\ndependencies = ["alembic"]\n',
    )
    _write(root, "packages/excluded/pyproject.toml", '[project]\nname = "excluded"\n')
    _write(root, "uv.lock")
    _write(root, ".python-version", "3.12\n")
    _write(root, "requirements-dev.txt", "mypy>=1\npyright\n-c constraints.txt\n")
    _write(root, "constraints.txt", "pycodestyle\n")
    _write(root, "src/conftest.py", "raise RuntimeError('must not execute')\n")
    _write(root, ".pre-commit-config.yaml", "repos: []\n")
    _write(root, "pyrightconfig.json", "{}")
    _write(root, "alembic.ini", "[alembic]\n")
    _write(root, "setup.py", "raise RuntimeError('must not execute')\n")

    result = scan_repository(root)

    assert {component.path: component.ecosystem for component in result.components} == {
        ".": "python",
        "packages/excluded": "python",
        "packages/member": "python",
    }
    assert {
        "python.component.verified",
        "python.dependency.declaration",
        "python.entry-point.declaration",
        "python.framework.declaration",
        "python.manager.evidence",
        "python.metadata.declaration",
        "python.runtime.declaration",
        "python.tool.evidence",
        "python.workspace.member",
    } <= _codes(result, "findings")
    assert {"fastapi", "flask", "django", "sqlalchemy", "alembic"} <= {
        summary.rsplit(" ", 1)[-1].rstrip(".")
        for summary in _summaries(result, "python.framework.declaration")
    }
    assert "python.dynamic-metadata-unknown" in _codes(result, "diagnostics")
    assert "python.manager-conflict" not in _codes(result, "diagnostics")
    assert "python.runtime-conflict" not in _codes(result, "diagnostics")
    assert b"root:main" not in dump_scan_json(result)
    assert b"must not execute" not in dump_scan_json(result)


@pytest.mark.verifies("TST021", "TST024")
def test_runtime_conflict_requires_demonstrable_incompatibility(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "pyproject.toml",
        '[project]\nname = "example"\nrequires-python = ">=3.11,<3.15"\n',
    )
    _write(root, ".python-version", "3.15\n")

    result = scan_repository(root)

    diagnostic = next(item for item in result.diagnostics if item.code == "python.runtime-conflict")
    assert diagnostic.subject_id == result.components[0].id
    assert diagnostic.location == "."
    assert diagnostic.message == (
        'Python runtime declarations conflict for component ".": '
        '.python-version [line:1] selects "3.15"; pyproject.toml '
        '[project.requires-python] declares the supported range ">=3.11,<3.15". '
        "At least one selected version does not satisfy a declared supported range. "
        "Next: choose a selected Python version within the supported range, or update "
        "the range if support for that version is intentional."
    )
    referenced = {item.id: item for item in result.evidence}
    assert {
        (referenced[evidence_id].location, referenced[evidence_id].locator)
        for evidence_id in diagnostic.evidence_ids
    } == {
        (".python-version", "line:1"),
        ("pyproject.toml", "project.requires-python"),
    }


@pytest.mark.verifies("TST021", "TST024")
@pytest.mark.parametrize(
    ("constraint", "selection"),
    [("not-a-range", "3.14"), (">=3.11", "not-a-version")],
)
def test_runtime_compatibility_does_not_guess_for_unparseable_values(
    tmp_path: Path, constraint: str, selection: str
) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "pyproject.toml",
        f'[project]\nname = "example"\nrequires-python = "{constraint}"\n',
    )
    _write(root, ".python-version", f"{selection}\n")

    result = scan_repository(root)

    assert "python.runtime-conflict" not in _codes(result, "diagnostics")


@pytest.mark.verifies("TST021", "TST024")
def test_manager_conflict_requires_competing_lock_families(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "example"\n')
    _write(root, "uv.lock")
    _write(root, "poetry.lock")
    _write(root, "requirements-dev.txt", "pytest\n")

    result = scan_repository(root)

    diagnostic = next(item for item in result.diagnostics if item.code == "python.manager-conflict")
    assert diagnostic.location == "."
    assert "poetry: poetry.lock" in diagnostic.message
    assert "uv: uv.lock" in diagnostic.message
    assert "No manager preference was selected" in diagnostic.message
    assert "Next:" in diagnostic.message
    referenced = {item.id: item for item in result.evidence}
    assert {referenced[item].location for item in diagnostic.evidence_ids} == {
        "poetry.lock",
        "uv.lock",
    }


@pytest.mark.verifies("TST020", "TST021", "TST024")
def test_requirements_inline_comments_are_not_part_of_pep508_values(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "example"\n')
    _write(
        root,
        "requirements.txt",
        "audioop-lts>=0.2.1 ; python_version >= '3.13'  # compatibility\n"
        "itsdangerous  # Flask dependency\n"
        'demo; implementation_name == "cpython # literal"\n',
    )

    result = scan_repository(root)

    assert "python.invalid-requirement" not in _codes(result, "diagnostics")
    assert {
        "audioop-lts",
        "itsdangerous",
        "demo",
    } <= {summary.split(" ")[2] for summary in _summaries(result, "python.dependency.declaration")}


@pytest.mark.verifies("TST020")
def test_requirement_comment_scanner_preserves_escaped_quoted_hashes() -> None:
    value = 'demo; extra == "a\\"#b"  # trailing comment'
    assert private_scan._strip_requirement_comment(value) == 'demo; extra == "a\\"#b"'


@pytest.mark.verifies("TST020", "TST021", "TST022", "TST024")
def test_templates_generated_requirements_and_constraints_remain_narrow(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "pyproject.toml",
        '[project]\nname = "source"\ndependencies = ["flask"]\n',
    )
    _write(
        root,
        "requirements.txt",
        "# This file is autogenerated by pip-compile\nwerkzeug==3\n",
    )
    _write(root, "constraints-dev.txt", "pytest==8\n")
    _write(
        root,
        "{{cookiecutter.project}}/pyproject.toml",
        '[project]\nname = "{{ cookiecutter.project }}"\n',
    )
    _write(root, "{{cookiecutter.project}}/uv.lock")
    _write(root, "{{cookiecutter.project}}/tests/conftest.py")
    _write(root, "{{cookiecutter.project}}/.pre-commit-config.yaml", "repos: []\n")
    _write(
        root,
        "template-content/pyproject.toml",
        '[project]\nname = "{{ cookiecutter.project }}"\n',
    )
    _write(
        root,
        "{{cookiecutter.legacy}}/setup.cfg",
        "[metadata]\nname = {{ cookiecutter.legacy }}\n[options]\n",
    )

    result = scan_repository(root)

    assert [(item.path, item.ecosystem) for item in result.components] == [(".", "python")]
    assert "python.template-manifest.unknown" in _codes(result, "findings")
    assert "python.template-manifest-unknown" in _codes(result, "diagnostics")
    assert "python.invalid-manifest" not in _codes(result, "diagnostics")
    assert "python.unbound-evidence" not in _codes(result, "diagnostics")
    dependency_locations = {
        item.location for item in result.evidence if item.source_kind == "dependency"
    }
    assert dependency_locations == {"pyproject.toml"}
    manager_locations = {item.location for item in result.evidence if item.source_kind == "manager"}
    assert {"requirements.txt", "constraints-dev.txt"} <= manager_locations


@pytest.mark.verifies("TST020", "TST021")
def test_pip_compile_header_detection_does_not_accept_negated_prose(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "source"\n')
    _write(root, "requirements.txt", "# This file is not autogenerated by pip-compile\nflask\n")

    result = scan_repository(root)

    assert any(
        item.source_kind == "dependency" and item.location == "requirements.txt"
        for item in result.evidence
    )


@pytest.mark.verifies("TST021")
def test_uv_workspace_table_qualifies_a_workspace_only_root(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[tool.uv.workspace]\nmembers = ["backend"]\n')
    _write(root, "backend/pyproject.toml", '[project]\nname = "backend"\n')
    _write(root, "uv.lock")

    result = scan_repository(root)

    assert {(item.path, item.kind) for item in result.components} == {
        (".", "workspace"),
        ("backend", "package"),
    }
    assert "python.workspace.member" in _codes(result, "findings")
    assert "python.affiliation.unknown" not in _codes(result, "findings")
    assert "python.unbound-evidence" not in _codes(result, "diagnostics")


@pytest.mark.verifies("TST021", "TST024")
def test_invalid_workspace_only_table_does_not_qualify_a_component(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", "[tool.uv.workspace]\nmembers = 42\n")
    _write(root, "uv.lock")

    result = scan_repository(root)

    assert result.components == ()
    assert result.completion == "partial"
    assert "python.invalid-workspace" in _codes(result, "diagnostics")
    assert "python.unbound-evidence" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST020", "TST021", "TST022")
def test_poetry_legacy_and_ini_tool_evidence(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "pyproject.toml",
        """
[tool.poetry]
name = "legacy"
[tool.poetry.dependencies]
python = "^3.11"
Flask = "*"
[tool.poetry.group.dev.dependencies]
flake8 = "*"
[tool.poetry.scripts]
legacy = "legacy:main"
[tool.black]
""".strip(),
    )
    _write(root, "poetry.lock")
    _write(
        root,
        "setup.cfg",
        "[metadata]\nname = legacy\n[options]\npackages = find:\n[tool:pytest]\n[flake8]\n[pycodestyle]\n[mypy]\n",
    )
    _write(root, "tox.ini", "[pytest]\n[flake8]\n[pycodestyle]\n[mypy]\n")
    _write(root, ".flake8", "[flake8]\n")
    for name, body in {
        "pytest.toml": "[pytest]\n",
        ".pytest.toml": "[pytest]\n",
        "pytest.ini": "[pytest]\n",
        "ruff.toml": "line-length = 88\n",
        ".ruff.toml": "line-length = 88\n",
        "mypy.ini": "[mypy]\n",
        ".mypy.ini": "[mypy]\n",
        ".coveragerc": "[run]\n",
        "tox.toml": "[tool.tox]\n",
    }.items():
        _write(root, name, body)

    result = scan_repository(root)

    assert result.components[0].ecosystem == "python"
    tool_summaries = _summaries(result, "python.tool.evidence")
    assert any("black" in summary for summary in tool_summaries)
    assert any("flake8" in summary for summary in tool_summaries)
    assert any("pycodestyle" in summary for summary in tool_summaries)
    assert any("tox" in summary for summary in tool_summaries)
    assert any(
        "Legacy Poetry entry point" in summary
        for summary in _summaries(result, "python.entry-point.declaration")
    )
    assert "python.tool-configuration-conflict" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST022", "TST024")
def test_tox_ini_is_evidence_but_unbound_examples_do_not_conflict(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(root, "tox.ini", "[tox]\nenvlist = py\n[testenv]\ncommands = pytest\n")
    _write(root, "docs/examples/.flake8", "[flake8]\n")
    _write(root, "docs/examples/setup.cfg", "[flake8]\n")

    result = scan_repository(root)

    assert any(
        item.location == "tox.ini" and item.locator == "tox/testenv" for item in result.evidence
    )
    assert not any(
        item.code == "python.tool-configuration-conflict" and item.location == "docs/examples"
        for item in result.diagnostics
    )


@pytest.mark.verifies("TST020", "TST021", "TST024")
def test_unknown_candidates_tool_only_and_unbound_manager(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "legacy/setup.py", "import definitely_not_imported\n")
    _write(root, "tool-only/pyproject.toml", "[tool.ruff]\nline-length = 90\n")
    _write(root, "orphan/uv.lock")
    _write(root, "orphan/poetry.lock")
    _write(root, "unbound/conftest.py")

    result = scan_repository(root)

    assert {(item.path, item.ecosystem, item.kind) for item in result.components} == {
        ("legacy", "generic", "package-candidate")
    }
    assert "python.component.candidate" in _codes(result, "findings")
    assert "python.affiliation.unknown" in _codes(result, "findings")
    assert {"python.dynamic-manifest-unknown", "python.unbound-evidence"} <= _codes(
        result, "diagnostics"
    )


@pytest.mark.verifies("TST020", "TST021", "TST022", "TST024")
def test_malformed_python_sources_are_partial(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "bad/pyproject.toml", "[project\n")
    _write(root, "ini/setup.cfg", "[metadata\n")
    _write(
        root,
        "valid/pyproject.toml",
        '[project]\nname = "valid"\ndependencies = ["not a req ???"]\n',
    )
    (root / "valid" / ".python-version").write_bytes(b"\xff")
    (root / "valid" / "requirements.txt").write_bytes(b"\xff")
    _write(root, "valid/pyrightconfig.json", "{")
    _write(root, "valid/.pre-commit-config.yaml", "!unsafe value\n")

    result = scan_repository(root)

    assert result.completion == "partial"
    assert {
        "python.invalid-configuration",
        "python.invalid-manifest",
        "python.invalid-requirement",
        "python.invalid-requirements",
        "python.invalid-runtime-file",
        "python.unsupported-configuration",
    } <= _codes(result, "diagnostics")
    unsupported = next(
        item for item in result.diagnostics if item.code == "python.unsupported-configuration"
    )
    assert unsupported.location == "valid/.pre-commit-config.yaml"
    assert "unsupported YAML structure" in unsupported.message
    assert "Next:" in unsupported.message


@pytest.mark.verifies("TST021", "TST024")
@pytest.mark.parametrize(
    ("members", "code"),
    [
        ('"../outside"', "python.invalid-workspace-member"),
        ('"missing/*"', "python.missing-workspace-member"),
        ("42", "python.invalid-workspace"),
    ],
)
def test_uv_workspace_invalid_and_missing_members(tmp_path: Path, members: str, code: str) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "pyproject.toml",
        f'[project]\nname = "root"\n[tool.uv.workspace]\nmembers = [{members}]\n',
    )
    result = scan_repository(root)
    assert result.completion == "partial"
    assert code in _codes(result, "diagnostics")


@pytest.mark.verifies("TST023", "TST024")
def test_gitea_and_github_ci_commands_runtime_and_redaction(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    workflow = """
jobs:
  tests:
    strategy:
      matrix:
        python-version: ["3.11", "3.12", {invalid: value}]
    steps:
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
      - run: uv run pytest
      - run: TOKEN=literal-secret deploy
      - run: TOKEN=${RUNTIME_TOKEN} deploy
      - run: TOKEN=%RUNTIME_TOKEN% deploy
      - run: uv pip install --index-url https://user:${INDEX_TOKEN}@example.invalid/simple
      - run: python -c 'import os; print(os.getenv("TWITTER_ACCESS_TOKEN"))'
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ inputs.python }}
""".strip()
    _write(root, ".gitea/workflows/tests.yml", workflow)
    _write(root, ".github/workflows/tests.yaml", workflow.replace("uv run pytest", "ruff check ."))

    result = scan_repository(root)
    data = dump_scan_json(result)

    assert b"uv run pytest" in data
    assert b"ruff check ." in data
    assert b"literal-secret" not in data
    assert b"TOKEN=${RUNTIME_TOKEN} deploy" in data
    assert b"TOKEN=%RUNTIME_TOKEN% deploy" in data
    assert b"https://user:${INDEX_TOKEN}@example.invalid/simple" in data
    assert b"TWITTER_ACCESS_TOKEN" in data
    assert "python.sensitive-command-redacted" in _codes(result, "diagnostics")
    redaction = next(
        item for item in result.diagnostics if item.code == "python.sensitive-command-redacted"
    )
    assert "may be sensitive" in redaction.message
    assert "non-sensitive test data" in redaction.message
    assert "if it is sensitive" in redaction.message
    assert "python.dynamic-ci-runtime-unknown" in _codes(result, "diagnostics")
    assert {"3.11", "3.12"} <= {
        summary.split('"')[1]
        for summary in _summaries(result, "python.runtime.declaration")
        if '"' in summary
    }


@pytest.mark.verifies("TST021", "TST023", "TST024")
def test_static_object_and_include_matrices_have_exact_runtime_locators(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\nrequires-python = ">=3.10"\n')
    _write(
        root,
        ".github/workflows/tests.yml",
        """
jobs:
  direct:
    strategy:
      matrix:
        python-version: ["3.14"]
        exclude:
          - python-version: "3.14"
        include:
          - python-version: "3.10"
    steps:
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
  nested:
    strategy:
      matrix:
        config:
          - python: "3.11"
          - python: "3.12"
    steps:
      - uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.config.python }}
""".strip(),
    )

    result = scan_repository(root)

    runtime_evidence = {
        (item.location, item.locator)
        for item in result.evidence
        if item.source_kind == "runtime" and item.location.endswith("tests.yml")
    }
    assert runtime_evidence == {
        (".github/workflows/tests.yml", "/jobs/direct/strategy/matrix/include/0/python-version"),
        (".github/workflows/tests.yml", "/jobs/nested/strategy/matrix/config/0/python"),
        (".github/workflows/tests.yml", "/jobs/nested/strategy/matrix/config/1/python"),
    }
    assert "python.dynamic-ci-runtime-unknown" not in _codes(result, "diagnostics")
    assert "python.runtime-conflict" not in _codes(result, "diagnostics")


@pytest.mark.verifies("TST023", "TST024")
def test_static_matrix_helpers_reject_unsupported_shapes() -> None:
    assert private_scan._nested_value({"config": {}}, ("config", "python")) is None
    assert private_scan._mapping_matches({"python": "3.14"}, {"python": "3.14"})
    assert not private_scan._mapping_matches("3.14", {"python": "3.14"})
    assert private_scan._matrix_values([], ("python",), ()) == ()
    assert private_scan._matrix_values({}, (), ()) == ()
    assert private_scan._matrix_values({"python": "dynamic"}, ("python",), ()) == ()
    assert (
        private_scan._matrix_values(
            {
                "config": ["not-an-object", {"python": True}],
                "include": ["not-an-object", {"python": True}, {"other": "3.14"}],
            },
            ("config", "python"),
            ("jobs", "test", "strategy", "matrix"),
        )
        == ()
    )
    assert (
        private_scan._matrix_values(
            {"python": "dynamic", "include": "dynamic", "exclude": "dynamic"},
            ("python",),
            (),
        )
        == ()
    )


@pytest.mark.verifies("TST023", "TST024")
def test_expression_only_commands_and_checkout_ownership_are_not_overstated(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(root, "packages/api/pyproject.toml", '[project]\nname = "api"\n')
    _write(
        root,
        ".github/workflows/tests.yml",
        """
jobs:
  external:
    steps:
      - uses: actions/checkout@v6
        with:
          repository: external/project
      - uses: actions/checkout@v6
        with:
          path: project-under-test
      - run: external-command
      - working-directory: project-under-test
        run: ${{ matrix.command }}
      - working-directory: project-under-test/packages/api
        run: echo ${{ matrix.value }}
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"
""".strip(),
    )

    result = scan_repository(root)
    payload = dump_scan_json(result)

    assert b"external-command" not in payload
    assert b"matrix.command" not in payload
    assert b"echo ${{ matrix.value }}" in payload
    assert "python.ci.command.dynamic" in _codes(result, "findings")
    assert "python.dynamic-ci-command-unknown" in _codes(result, "diagnostics")
    commands = [item for item in result.findings if item.code == "python.ci.command"]
    api = next(item for item in result.components if item.path == "packages/api")
    assert [item.subject_id for item in commands] == [api.id]
    assert "python.runtime.declaration" not in _codes(result, "findings")


@pytest.mark.verifies("TST023", "TST024")
def test_mixed_expression_command_ending_in_braces_remains_literal(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(
        root,
        ".github/workflows/expression.yml",
        """jobs:
  test:
    steps:
      - run: '${{ matrix.command }} && echo "}}"'
""",
    )

    result = scan_repository(root)

    assert "python.ci.command.dynamic" not in _codes(result, "findings")
    assert any("matrix.command" in item.summary for item in result.findings)


@pytest.mark.verifies("TST023", "TST024")
def test_checkout_ownership_rejects_invalid_and_ambiguous_directories(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(
        root,
        ".github/workflows/ownership.yml",
        """
jobs:
  root:
    steps:
      - uses: actions/checkout@v6
      - run: root-command
      - working-directory: ${{ matrix.directory }}
        run: expression-directory-command
  nested-only:
    steps:
      - uses: actions/checkout@v6
        with:
          path: nested
      - run: outside-nested-command
      - working-directory: nested
        run: nested-root-command
  invalid-checkout:
    steps:
      - uses: actions/checkout@v6
        with:
          path: invalid\\path
      - run: invalid-checkout-command
  ambiguous:
    steps:
      - uses: actions/checkout@v6
        with:
          repository: ${{ inputs.repository }}
      - run: ambiguous-command
""".strip(),
    )

    result = scan_repository(root)
    payload = dump_scan_json(result)

    assert b"root-command" in payload
    assert b"nested-root-command" in payload
    for omitted in (
        b"expression-directory-command",
        b"outside-nested-command",
        b"invalid-checkout-command",
        b"ambiguous-command",
    ):
        assert omitted not in payload


@pytest.mark.verifies("TST020", "TST023", "TST024")
def test_generic_manifest_prevents_python_promotion_and_ci_attribution(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "go.mod", "module example.invalid/polyglot\n")
    _write(
        root,
        "pyproject.toml",
        '[project]\nname = "build-support"\ndependencies = ["ruff"]\n',
    )
    _write(
        root,
        ".github/workflows/tests.yml",
        "jobs:\n  tests:\n    steps:\n      - run: go test ./...\n",
    )

    result = scan_repository(root)

    assert [(item.path, item.ecosystem, item.ecosystems) for item in result.components] == [
        (".", "mixed", ("generic", "python"))
    ]
    component_evidence = {
        item.location for item in result.evidence if item.id in result.components[0].evidence_ids
    }
    assert {"go.mod", "pyproject.toml"} <= component_evidence
    assert "python.component.verified" not in _codes(result, "findings")
    assert "python.ci.command" not in _codes(result, "findings")
    assert "python.metadata.declaration" in _codes(result, "findings")


@pytest.mark.verifies("TST023", "TST024")
def test_ci_working_directory_attribution_and_unattributed_commands(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "packages/api/pyproject.toml", '[project]\nname = "api"\n')
    _write(
        root,
        ".github/workflows/tests.yml",
        """
jobs:
  tests:
    defaults:
      run:
        working-directory: packages/api
    steps:
      - run: pytest
      - working-directory: nowhere
        run: ignored-command
""".strip(),
    )
    result = scan_repository(root)
    payload = dump_scan_json(result)
    assert b"pytest" in payload
    assert b"ignored-command" not in payload


@pytest.mark.verifies("TST023", "TST024")
def test_gitlab_local_external_missing_and_cycle_includes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(
        root,
        ".gitlab-ci.yml",
        """
include:
  - local: .gitlab/jobs.yml
  - project: other/project
  - local: missing.yml
before_script: echo before
test:
  script:
    - pytest
  after_script: echo after
""".strip(),
    )
    _write(
        root,
        ".gitlab/jobs.yml",
        "include: {local: .gitlab-ci.yml}\njob:\n  run:\n    - run: ruff check .\n",
    )

    result = scan_repository(root)
    payload = dump_scan_json(result)

    assert b"echo before" in payload
    assert b"pytest" in payload
    assert b"ruff check ." in payload
    assert {
        "python.ci-include-cycle",
        "python.external-ci-include",
        "python.invalid-ci-include",
    } <= _codes(result, "diagnostics")
    assert "python.ci.external-include" in _codes(result, "findings")


@pytest.mark.verifies("TST023", "TST024")
def test_invalid_ci_yaml_is_partial(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(root, ".gitea/workflows/bad.yml", "jobs: [")
    _write(root, ".gitlab-ci.yml", "!unsafe value")
    result = scan_repository(root)
    assert result.completion == "partial"
    assert "python.invalid-ci-workflow" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST023", "TST024")
@pytest.mark.parametrize(
    "document",
    [
        "value: &anchor [one]\ncopy: *anchor\n",
        "value: !!python/object:builtins.object {}\n",
        "? [one, two]\n: value\n",
        "key: one\nkey: two\n",
        "value: .inf\n",
        "value: 0x10\n",
        "value: null\n",
    ],
)
def test_restricted_yaml_parser_boundaries(document: str) -> None:
    if document.startswith(("value: .inf", "value: 0x", "value: null")):
        assert isinstance(strict_yaml_document(document.encode()), dict)
    else:
        with pytest.raises((StaticStructureError, yaml.YAMLError)):
            strict_yaml_document(document.encode())


@pytest.mark.verifies("TST024")
def test_yaml_parser_empty_and_depth_bounds() -> None:
    assert strict_yaml_document(b"") == {}
    deep = "value: " + "[" * 34 + "x" + "]" * 34
    with pytest.raises(StaticStructureError):
        strict_yaml_document(deep.encode())


@pytest.mark.verifies("TST024")
def test_repository_view_lazy_read_limits_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    entry_path = root / "pyproject.toml"
    metadata = entry_path.stat()
    entry = kernel._Entry(
        "pyproject.toml",
        False,
        True,
        False,
        False,
        metadata.st_size,
        kernel._identity(metadata),
    )

    def view_for(**limits: Any) -> kernel._RepositoryView:
        inspection = kernel._Inspection(
            {},
            (),
            (),
            False,
            {"pyproject.toml": entry},
            root,
            kernel._Limits(**limits),
            0,
            time.monotonic(),
        )
        return kernel._RepositoryView(inspection)

    view = view_for()
    assert view.read_bytes("missing") is None
    first = view.read_bytes("pyproject.toml")
    assert first == view.read_bytes("pyproject.toml")

    assert view_for(max_total_bytes=1).read_bytes("pyproject.toml") is None
    assert view_for(max_memory_bytes=1).read_bytes("pyproject.toml") is None
    timed = view_for(max_elapsed_seconds=0.000001)
    timed._started = 0.0
    assert timed.read_bytes("pyproject.toml") is None

    monkeypatch.setattr(kernel, "_read_file", lambda *args: (_ for _ in ()).throw(OSError()))
    assert view_for().read_bytes("pyproject.toml") is None


@pytest.mark.verifies("TST024")
def test_catalogued_relevant_file_buffer_respects_shared_memory_limit(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "Cargo.toml", "[package]\n" + "x" * 100)
    inspection = kernel._inspect(
        root,
        limits=kernel._Limits(max_memory_bytes=32),
    )
    assert inspection.partial
    assert "max_memory_bytes" in {item.reason for item in inspection.skipped}


@pytest.mark.verifies("TST020", "TST024")
def test_python_scan_is_deterministic_and_json_safe(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "demo"\ndependencies = ["FastAPI"]\n')
    first = scan_repository(root)
    second = scan_repository(root)
    assert first == second
    assert dump_scan_json(first) == dump_scan_json(second)
    assert json.loads(dump_scan_json(first))["components"][0]["ecosystem"] == "python"


@pytest.mark.verifies("TST024")
def test_no_target_import_process_environment_network_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "demo"\n')
    _write(root, "setup.py", "raise RuntimeError('executed')\n")
    before = {path.relative_to(root) for path in root.rglob("*")}
    monkeypatch.setattr(os, "getenv", lambda *args: pytest.fail("environment read"))
    result = scan_repository(root)
    after = {path.relative_to(root) for path in root.rglob("*")}
    assert result.components[0].ecosystem == "python"
    assert before == after


@pytest.mark.verifies("TST020", "TST021", "TST022", "TST023", "TST024")
def test_unreadable_detector_inputs_are_ignored_after_view_diagnostic() -> None:
    class UnreadableView:
        def paths(self) -> tuple[str, ...]:
            return (
                "pyproject.toml",
                "setup.cfg",
                "requirements.txt",
                ".python-version",
                "pyrightconfig.json",
                ".gitea/workflows/test.yml",
            )

        def read_bytes(self, path: str) -> None:
            return None

        def path_candidates(self) -> tuple[PathCandidate, ...]:
            return tuple(PathCandidate(path, *path_metadata(path)) for path in self.paths())

        def direct_children(self, path: str) -> tuple[str, ...]:
            return ()

        def checkpoint(self) -> bool:
            return False

    detected = private_scan.detect_python(UnreadableView(), DetectionContext())  # type: ignore[arg-type]
    assert detected == DetectionResult()


@pytest.mark.verifies("TST020", "TST021", "TST022", "TST023")
def test_supported_parsers_reject_wrong_static_shapes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(
        root,
        "pyproject.toml",
        """
[build-system]
requires = []
[project]
dynamic = [1]
dependencies = [1]
optional-dependencies.bad = [1]
optional-dependencies.wrong = 42
[dependency-groups]
bad = [1]
wrong = 42
[tool.poetry.dependencies]
python = { version = "3.12" }
[tool.poetry.group.bad]
value = true
[tool.uv.workspace]
members = ["member"]
exclude = "bad"
""".strip(),
    )
    _write(
        root,
        "poetry-shapes/pyproject.toml",
        "[tool.poetry]\nname='x'\ndependencies='bad'\ngroup='bad'\n",
    )
    _write(root, "member/setup.cfg", "[metadata]\nname=x\n[options]\n")
    _write(root, "pyrightconfig.json", "[]")
    _write(root, ".pre-commit-config.yaml", "- item\n")
    _write(root, "orphan/conftest.py")
    _write(root, ".gitea/workflows/shapes.yml", "jobs: []\n")
    _write(
        root,
        ".github/workflows/shapes.yml",
        "jobs:\n  scalar: value\n  no-steps: {}\n  steps:\n    steps: [value]\n",
    )
    result = scan_repository(root)
    assert result.completion == "partial"
    assert "python.invalid-configuration" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST023", "TST024")
def test_gitlab_include_depth_and_non_mapping_documents(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(root, ".gitlab-ci.yml", "include: {local: chain/0.yml}\n")
    for index in range(18):
        next_path = f"chain/{index + 1}.yml"
        _write(root, f"chain/{index}.yml", f"include: {{local: {next_path}}}\n")
    _write(root, "chain/18.yml", "[]\n")
    result = scan_repository(root)
    assert result.completion == "partial"
    assert "python.ci-include-depth" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST021", "TST022", "TST023", "TST024")
def test_selective_read_failures_remain_partial_detector_inputs() -> None:
    pyproject = b'[project]\nname = "root"\n'
    gitlab = b"include:\n  - {local: included.yml}\n  - {}\njob:\n  script:\n    - {}\n"

    class SelectiveView:
        def paths(self) -> tuple[str, ...]:
            return (
                "pyproject.toml",
                ".python-version",
                "requirements.txt",
                ".gitlab-ci.yml",
                "included.yml",
            )

        def read_bytes(self, path: str) -> bytes | None:
            if path == "pyproject.toml":
                return pyproject
            if path == ".gitlab-ci.yml":
                return gitlab
            return None

        def path_candidates(self) -> tuple[PathCandidate, ...]:
            return tuple(PathCandidate(path, *path_metadata(path)) for path in self.paths())

        def direct_children(self, path: str) -> tuple[str, ...]:
            return ()

        def checkpoint(self) -> bool:
            return False

    detected = private_scan.detect_python(SelectiveView(), DetectionContext())  # type: ignore[arg-type]
    assert detected.components[0].ecosystem == "python"
    assert detected.relationships == ()


@pytest.mark.verifies("TST023")
def test_gitlab_non_mapping_local_include_is_invalid(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(root, ".gitlab-ci.yml", "include: {local: included.yml}\n")
    _write(root, "included.yml", "[]\n")
    result = scan_repository(root)
    assert result.completion == "partial"
    assert "python.invalid-ci-workflow" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST023")
def test_workflow_shape_and_literal_runtime_variants(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "pyproject.toml", '[project]\nname = "root"\n')
    _write(root, ".gitea/workflows/list.yml", "[]\n")
    _write(
        root,
        ".github/workflows/runtime.yml",
        """
jobs:
  runtime:
    steps:
      - uses: actions/setup-python@v6
        with: {python-version: "3.13"}
      - working-directory: 42
        run: pytest
""".strip(),
    )
    result = scan_repository(root)
    assert 'Python interpreter selection "3.13" is declared.' in _summaries(
        result, "python.runtime.declaration"
    )
    assert "python.invalid-ci-workflow" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST024")
def test_normalized_python_finding_memory_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    evidence = private_scan._python_evidence("manifest", "pyproject.toml", "project", "x", "x")
    finding = FindingCandidate(
        "python.test", "verified", None, "finding", (evidence_key(evidence),)
    )
    monkeypatch.setattr(
        normalization,
        "BUILTIN_DETECTORS",
        (lambda view, context: DetectionResult(evidence=(evidence,), findings=(finding,)),),
    )
    monkeypatch.setattr(
        normalization,
        "_record_size",
        lambda value: 100 if isinstance(value, Finding) else 1,
    )
    result = normalization._normalize(
        root,
        kernel._Inspection({}, (), (), False),
        memory_limit=10,
    )
    assert result.completion == "partial"
    assert "inspection.max-memory-bytes" in _codes(result, "diagnostics")
