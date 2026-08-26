"""Behavioral tests for bounded JavaScript and TypeScript inspection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import slygentify._scan.detectors.javascript as javascript
from slygentify import dump_scan_json, load_scan_json, scan_repository
from slygentify._scan.contracts import DetectionContext, PathCandidate
from slygentify._scan.detectors._support import StaticStructureError, strict_yaml_document
from slygentify._scan.paths import safe_member
from slygentify.cli import app
from tests.scan_views import InMemoryDetectorView


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    return repository


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _codes(result: Any, attribute: str) -> list[str]:
    return [item.code for item in getattr(result, attribute)]


@pytest.mark.verifies("TST025", "TST026", "TST027", "TST028", "TST029")
def test_full_npm_typescript_tool_framework_and_ci_evidence(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(
        repository / "package.json",
        {
            "name": "Example-App",
            "private": True,
            "type": "module",
            "dependencies": {"express": "^5", "Fastify": "^5", "vue": "^3"},
            "devDependencies": {
                "typescript": "^6",
                "eslint": "^10",
                "prettier": "^4",
                "jest": "^30",
                "vitest": "^4",
                "@playwright/test": "^2",
            },
            "peerDependencies": {"left-pad": "1"},
            "optionalDependencies": {"optional-package": "1"},
            "scripts": {"test": "vitest run", "lint": "eslint ."},
            "bin": {"example": "bin/example.js"},
            "engines": {"node": ">=20 <23", "npm": ">=10"},
            "packageManager": "npm@11.0.0",
            "devEngines": {"packageManager": {"name": "npm", "version": "11"}},
            "workspaces": ["packages/*"],
            "eslintConfig": {},
            "prettier": {},
            "jest": {},
        },
    )
    _write_json(repository / "packages" / "web" / "package.json", {"name": "@demo/web"})
    _write_json(
        repository / "tsconfig.json",
        {"extends": "./tsconfig.base.json", "references": [{"path": "packages/web"}]},
    )
    _write_json(repository / "tsconfig.base.json", {"compilerOptions": {}})
    _write_json(repository / "packages" / "web" / "tsconfig.json", {})
    for name in (
        "package-lock.json",
        "npm-shrinkwrap.json",
        "eslint.config.js",
        ".prettierrc.json",
        "jest.config.ts",
        "vitest.config.ts",
        "playwright.config.ts",
    ):
        (repository / name).write_text("{}", encoding="utf-8")
    (repository / ".nvmrc").write_text("22.1.0\n", encoding="utf-8")
    workflow = repository / ".github" / "workflows" / "test.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """jobs:
  test:
    strategy:
      matrix:
        node: ["20.1.0", "22.1.0"]
        include:
          - node: "21.2.0"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node }}
      - run: npm test
""",
        encoding="utf-8",
    )

    result = scan_repository(repository)

    assert result.completion == "complete"
    assert {item.path: (item.ecosystem, item.kind) for item in result.components} == {
        ".": ("javascript", "workspace"),
        "packages/web": ("javascript", "package"),
    }
    finding_codes = _codes(result, "findings")
    assert "javascript.component.verified" in finding_codes
    assert "javascript.package.metadata" in finding_codes
    assert finding_codes.count("javascript.framework.declaration") == 3
    assert "javascript.script.declaration" in finding_codes
    assert "javascript.bin.declaration" in finding_codes
    assert "javascript.workspace.member" in finding_codes
    assert "javascript.typescript.reference" in finding_codes
    assert "javascript.npm-lock-precedence" in finding_codes
    assert "javascript.ci.command" in finding_codes
    runtime_findings = [
        item for item in result.findings if item.code == "javascript.runtime.declaration"
    ]
    assert len(runtime_findings) == 6
    assert any(
        item.location == "package.json" and item.locator == "/dependencies/Fastify"
        for item in result.evidence
    )
    assert any(
        item.location.endswith("test.yml") and item.locator == "/jobs/test/strategy/matrix/node/0"
        for item in result.evidence
    )
    assert "javascript.npm-lock-coexistence" in _codes(result, "diagnostics")
    assert "javascript.runtime-conflict" not in _codes(result, "diagnostics")


@pytest.mark.verifies("TST025", "TST029")
@pytest.mark.parametrize(
    ("content", "unknown"),
    [
        ("{", True),
        ('{"name":"one","name":"two"}', True),
        ("[]", True),
        ("{}", False),
    ],
)
def test_package_boundary_requires_a_valid_unique_key_object(
    tmp_path: Path, content: str, unknown: bool
) -> None:
    repository = _repository(tmp_path)
    (repository / "package.json").write_text(content, encoding="utf-8")

    result = scan_repository(repository)

    assert (not result.components) is unknown
    assert ("javascript.invalid-manifest" in _codes(result, "diagnostics")) is unknown
    assert (result.completion == "partial") is unknown


@pytest.mark.verifies("TST025", "TST026", "TST029")
def test_invalid_package_fields_are_partial_and_commands_are_redacted(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(
        repository / "package.json",
        {
            "name": 3,
            "private": "yes",
            "type": [],
            "dependencies": [],
            "devDependencies": {"Bad Name": 7},
            "scripts": {"leak": "TOKEN=literal npm test", "bad": 7},
            "bin": {"bad": 7},
            "engines": {"node": 20},
            "packageManager": "bun@1",
            "devEngines": {"packageManager": [{"name": "bad"}]},
            "workspaces": "packages/*",
        },
    )

    result = scan_repository(repository)

    assert result.completion == "partial"
    codes = _codes(result, "diagnostics")
    assert codes.count("javascript.invalid-metadata") == 3
    assert "javascript.invalid-dependencies" in codes
    assert "javascript.invalid-dependency" in codes
    assert "javascript.invalid-script" in codes
    assert "javascript.invalid-bin" in codes
    assert "javascript.invalid-runtime" in codes
    assert codes.count("javascript.invalid-manager-selection") == 2
    assert "javascript.invalid-workspace" in codes
    assert "javascript.sensitive-command-redacted" in codes
    document = dump_scan_json(result).decode()
    assert "TOKEN=literal" not in document
    assert "confirmed secret" not in document


@pytest.mark.verifies("TST025")
def test_string_and_invalid_scalar_bin_and_scripts_shapes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(
        repository / "package.json",
        {"name": "demo", "scripts": [], "bin": "cli.js", "engines": []},
    )

    result = scan_repository(repository)

    assert "javascript.bin.declaration" in _codes(result, "findings")
    assert "javascript.invalid-scripts" in _codes(result, "diagnostics")
    assert "javascript.invalid-runtime" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST026", "TST029")
def test_pnpm_yarn_manager_workspace_conflicts_and_exclusions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {"private": True})
    _write_json(repository / "packages" / "kept" / "package.json", {})
    _write_json(repository / "packages" / "excluded" / "package.json", {})
    (repository / "pnpm-workspace.yaml").write_text(
        "packages:\n  - packages/*\n  - '!packages/excluded'\n", encoding="utf-8"
    )
    (repository / "pnpm-lock.yaml").write_text("lockfileVersion: '9'\n", encoding="utf-8")
    (repository / "yarn.lock").write_text("", encoding="utf-8")
    (repository / ".yarnrc.yml").write_text("nodeLinker: node-modules\n", encoding="utf-8")

    result = scan_repository(repository)

    members = {
        item.subject_id for item in result.findings if item.code == "javascript.workspace.member"
    }
    ids = {item.path: item.id for item in result.components}
    assert members == {ids["packages/kept"]}
    assert "javascript.manager-conflict" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST026")
def test_workspace_patterns_report_invalid_missing_and_overlap(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(
        repository / "package.json",
        {"workspaces": ["../outside", "missing/*", "packages/*"]},
    )
    _write_json(
        repository / "groups" / "package.json",
        {"workspaces": ["../packages/*", "nested/*"]},
    )
    _write_json(repository / "packages" / "member" / "package.json", {})
    _write_json(repository / "groups" / "nested" / "member" / "package.json", {})
    _write_json(
        repository / "groups" / "nested" / "package.json",
        {"workspaces": ["member"]},
    )

    result = scan_repository(repository)

    codes = _codes(result, "diagnostics")
    assert "javascript.invalid-workspace-pattern" in codes
    assert "javascript.missing-workspace-member" in codes
    assert "javascript.overlapping-workspace-membership" in codes


@pytest.mark.verifies("TST026")
def test_invalid_pnpm_and_yarn_configuration_is_partial(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    (repository / "pnpm-workspace.yaml").write_text("packages: wrong\n", encoding="utf-8")
    (repository / ".yarnrc.yml").write_text("a: &a [*a]\n", encoding="utf-8")

    result = scan_repository(repository)

    assert result.completion == "partial"
    assert "javascript.invalid-workspace" in _codes(result, "diagnostics")
    assert "javascript.invalid-manager-configuration" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST026")
@pytest.mark.parametrize(
    ("constraint", "selection", "expected"),
    [
        (">=20 <21", "22.0.0", "javascript.runtime-conflict"),
        ("workspace:*", "22", "javascript.runtime-compatibility-unknown"),
        ("^20.0.0", "20.5.0", None),
        ("~20.2.0", "20.3.0", "javascript.runtime-conflict"),
    ],
)
def test_runtime_conflicts_are_role_aware(
    tmp_path: Path, constraint: str, selection: str, expected: str | None
) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {"engines": {"node": constraint}})
    (repository / ".node-version").write_text(selection, encoding="utf-8")

    result = scan_repository(repository)

    codes = _codes(result, "diagnostics")
    if expected is None:
        assert "javascript.runtime-conflict" not in codes
        assert "javascript.runtime-compatibility-unknown" not in codes
    else:
        assert expected in codes


@pytest.mark.verifies("TST026")
def test_invalid_runtime_selection_file_is_partial(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    (repository / ".nvmrc").write_text("20\n21\n", encoding="utf-8")

    result = scan_repository(repository)

    assert result.completion == "partial"
    assert "javascript.invalid-runtime" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST027", "TST029")
def test_typescript_non_strict_and_unresolved_references_remain_unknown(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    (repository / "tsconfig.json").write_text("{// comment\n}\n", encoding="utf-8")
    _write_json(repository / "tsconfig.build.json", {"extends": "../outside"})
    (repository / "eslint.config.js").write_text(
        "raise RuntimeError('must not run')", encoding="utf-8"
    )

    result = scan_repository(repository)

    assert "javascript.typescript-content-unknown" in _codes(result, "diagnostics")
    assert "javascript.unresolved-typescript-reference" in _codes(result, "diagnostics")
    assert "javascript.typescript.configuration-content" in _codes(result, "findings")
    assert "javascript.tool.evidence" in _codes(result, "findings")


@pytest.mark.verifies("TST027")
def test_typescript_project_references_resolve_and_aggregate_by_cause(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    _write_json(
        repository / "tsconfig.json",
        {
            "references": [
                {"path": "projects/directory"},
                {"path": "configs/explicit.json"},
                {"path": "missing-one"},
                {"path": "missing-two"},
                {"path": "../outside"},
                {"path": "/outside"},
                {"path": "projects/malformed"},
                {"path": "configs/not-an-object.json"},
            ]
        },
    )
    _write_json(repository / "projects" / "directory" / "tsconfig.json", {})
    _write_json(repository / "configs" / "explicit.json", {})
    _write_json(repository / "configs" / "not-an-object.json", [])
    malformed = repository / "projects" / "malformed" / "tsconfig.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("{", encoding="utf-8")

    result = scan_repository(repository)

    references = [
        item for item in result.findings if item.code == "javascript.typescript.reference"
    ]
    assert [item.classification for item in references].count("verified") == 2
    assert [item.classification for item in references].count("unknown") == 6
    diagnostics = [
        item
        for item in result.diagnostics
        if item.code == "javascript.unresolved-typescript-reference"
    ]
    assert len(diagnostics) == 3
    assert {item.location for item in diagnostics} == {"."}
    assert {item.subject_id for item in diagnostics} == {result.components[0].id}
    locators_by_diagnostic = {
        frozenset(
            evidence.locator
            for evidence in result.evidence
            if evidence.id in diagnostic.evidence_ids
        )
        for diagnostic in diagnostics
    }
    assert locators_by_diagnostic == {
        frozenset({"/references/2/path", "/references/3/path"}),
        frozenset({"/references/4/path", "/references/5/path"}),
        frozenset({"/references/6/path", "/references/7/path"}),
    }
    assert any("unavailable as a safe regular file" in item.message for item in diagnostics)
    assert any("unsafe or escapes the repository root" in item.message for item in diagnostics)
    assert any("not a strict unique-key JSON object" in item.message for item in diagnostics)
    document = dump_scan_json(result)
    assert document == dump_scan_json(scan_repository(repository))
    assert load_scan_json(document) == result


@pytest.mark.verifies("TST027")
def test_typescript_unreadable_catalogued_target_remains_unavailable() -> None:
    class UnreadableView:
        def paths(self) -> tuple[str, ...]:
            return ("configs/unreadable.json",)

        def read_bytes(self, path: str) -> bytes | None:
            assert path == "configs/unreadable.json"
            return None

        def path_candidates(self) -> tuple[PathCandidate, ...]:
            return ()

        def direct_children(self, parent: str) -> tuple[PathCandidate, ...]:
            return ()

        def checkpoint(self) -> bool:
            return False

    assert javascript._resolve_typescript_reference(
        UnreadableView(),
        frozenset({"configs/unreadable.json"}),
        ".",
        "configs/unreadable.json",
        project_reference=True,
    ) == (False, "unavailable-or-nonregular-target")


@pytest.mark.verifies("TST027")
def test_multiple_tool_locations_are_preserved_as_a_conflict(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {"eslintConfig": {}})
    (repository / "eslint.config.js").write_text("export default []", encoding="utf-8")

    result = scan_repository(repository)

    assert "javascript.tool-configuration-conflict" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST028", "TST029")
def test_ci_dynamic_external_local_include_and_redaction(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    github = repository / ".gitea" / "workflows" / "test.yaml"
    github.parent.mkdir(parents=True)
    github.write_text(
        """jobs:
  test:
    defaults:
      run:
        working-directory: .
    steps:
      - uses: actions/checkout@v4
      - run: ${{ matrix.command }}
      - run: PASSWORD=literal npm test
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ inputs.node }}
""",
        encoding="utf-8",
    )
    (repository / ".gitlab-ci.yml").write_text(
        """include:
  - local: ci/local.yml
  - remote: https://example.invalid/ci.yml
  - ""
  - []
before_script: npm ci
test:
  script:
    - npm test
    - run: npm run lint
    - other: ignored
""",
        encoding="utf-8",
    )
    local = repository / "ci" / "local.yml"
    local.parent.mkdir()
    local.write_text("include:\n  - local: .gitlab-ci.yml\nlocal:\n  run: npm run local\n")

    result = scan_repository(repository)

    codes = _codes(result, "diagnostics")
    assert "javascript.dynamic-ci-command-unknown" in codes
    assert "javascript.dynamic-ci-runtime-unknown" in codes
    assert "javascript.sensitive-command-redacted" in codes
    assert "javascript.external-ci-include" in codes
    assert "javascript.ci-include-cycle" in codes
    assert "PASSWORD=literal" not in dump_scan_json(result).decode()


@pytest.mark.verifies("TST028")
def test_invalid_ci_and_include_targets_are_partial(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    workflow = repository / ".github" / "workflows" / "bad.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("jobs: [", encoding="utf-8")
    (repository / ".gitlab-ci.yml").write_text(
        "include:\n  - local: ../outside.yml\n", encoding="utf-8"
    )

    result = scan_repository(repository)

    assert result.completion == "partial"
    codes = _codes(result, "diagnostics")
    assert "javascript.invalid-ci-workflow" in codes
    assert "javascript.invalid-ci-include" in codes


@pytest.mark.verifies("TST028")
def test_checkout_ownership_prevents_external_command_attribution(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    workflow = repository / ".github" / "workflows" / "external.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """jobs:
  test:
    steps:
      - uses: actions/checkout@v4
        with:
          repository: someone/else
      - run: npm test
""",
        encoding="utf-8",
    )

    result = scan_repository(repository)

    assert "javascript.ci.command" not in _codes(result, "findings")


@pytest.mark.verifies("TST029")
def test_unsupported_tooling_and_same_root_generic_component_are_honest(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    (repository / "Cargo.toml").write_text('[package]\nname = "mixed"\n', encoding="utf-8")
    (repository / "bun.lockb").write_bytes(b"unsupported")

    result = scan_repository(repository)

    assert [(item.path, item.ecosystem, item.ecosystems) for item in result.components] == [
        (".", "mixed", ("generic", "javascript"))
    ]
    assert "javascript.unsupported-tooling.unknown" in _codes(result, "findings")
    assert "javascript.unsupported-tooling" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST029", "TST025", "TST026", "TST027", "TST028")
def test_javascript_scan_is_deterministic_and_cli_reports_support(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {"name": "demo"})

    first = scan_repository(repository)
    second = scan_repository(repository)
    invocation = CliRunner().invoke(app, ["scan", str(repository)])

    assert dump_scan_json(first) == dump_scan_json(second)
    assert invocation.exit_code == 0
    assert "JavaScript/TypeScript" in invocation.stdout
    assert "inspection is not available yet" not in invocation.stdout


@pytest.mark.verifies("TST029")
def test_javascript_helpers_reject_unsupported_shapes() -> None:
    assert javascript._package_name("@Scope/Package") == "@scope/package"
    assert javascript._package_name("bad name") is None
    assert javascript._manager_selection(7) is None
    assert javascript._manager_selection("npm") == ("npm", None)
    assert javascript._manager_selection("unknown@1") is None
    assert safe_member(".", "../outside") is None
    assert safe_member(".", "C:/outside") is None
    assert javascript._safe_workspace_pattern("packages/{a,b}") is None
    assert javascript._safe_workspace_pattern("") is None
    assert javascript._safe_workspace_pattern("/absolute") is None
    assert javascript._safe_workspace_pattern("C:/absolute") is None
    assert javascript._safe_workspace_pattern("packages/(group)") is None
    assert javascript._workspace_match("packages/a", "packages/*")
    assert not javascript._workspace_match("packages/a/nested", "packages/*")
    assert javascript._workspace_match("packages/a/nested", "packages/**")
    assert javascript._version("latest") is None
    assert javascript._range_contains("latest", ">=20") is None
    assert javascript._range_contains("20.0.0", "") is None
    assert javascript._range_contains("20.0.0", "20.x")
    assert javascript._range_contains("20.1.0", "20.1")
    assert javascript._range_contains("20.0.0", "<=20.0.0")
    assert javascript._range_contains("20.0.0", ">19")
    assert javascript._range_contains("20.0.0", "=20.0.0")
    assert javascript._range_contains("20.0.0", "20")
    assert javascript._matrix_values([], ("node",), ()) == ()
    assert javascript._matrix_values({}, (), ()) == ()
    assert javascript._matrix_values(
        {"node": [20, "${{ dynamic }}", "22"], "include": [7, {"node": "23"}]},
        ("node",),
        ("matrix",),
    ) == (
        ("22", "/matrix/node/2"),
        ("23", "/matrix/include/1/node"),
    )
    assert javascript._matrix_values({"node": []}, ("node",), ()) == ()
    assert javascript._matrix_values({"node": "22", "include": []}, ("node",), ()) == ()
    assert not javascript._contains_literal_credential("TOKEN=${TOKEN} npm test")
    assert javascript._contains_literal_credential("https://user:literal@example.invalid/x")


@pytest.mark.verifies("TST029")
def test_restricted_yaml_parser_boundaries() -> None:
    assert strict_yaml_document(b"") == {}
    with pytest.raises(StaticStructureError):
        strict_yaml_document(b"a: 1\na: 2\n")
    with pytest.raises(StaticStructureError):
        strict_yaml_document(b"a: &a [*a]\n")
    deep = "value"
    for _ in range(34):
        deep = f"[{deep}]"
    with pytest.raises(StaticStructureError):
        strict_yaml_document(deep.encode())


@pytest.mark.verifies("TST025", "TST026", "TST027")
def test_additional_static_shapes_are_reported(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(
        repository / "package.json",
        {
            "bin": {"unsafe": "https://user:password@example.invalid/bin.js"},
            "workspaces": {"packages": []},
            "devEngines": {"packageManager": "yarn@4"},
        },
    )
    (repository / "pnpm-workspace.yaml").write_text("[]\n", encoding="utf-8")
    (repository / ".nvmrc").write_bytes(b"\xff")
    _write_json(repository / "child" / "package.json", {"bin": 7})
    _write_json(repository / "tsconfig.json", [])
    _write_json(repository / "tsconfig.refs.json", {"references": [7, {"path": 7}]})

    result = scan_repository(repository)

    codes = _codes(result, "diagnostics")
    assert "javascript.unsafe-bin-target" in codes
    assert "javascript.invalid-bin" in codes
    assert "password@example.invalid" not in dump_scan_json(result).decode()
    assert "javascript.invalid-workspace" in codes
    assert "javascript.invalid-runtime" in codes
    assert "javascript.typescript-content-unknown" in codes


@pytest.mark.verifies("TST028")
def test_workflow_static_shape_and_ownership_variants(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    workflows = repository / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "list.yml").write_text("[]\n", encoding="utf-8")
    (workflows / "jobs-scalar.yml").write_text("jobs: no\n", encoding="utf-8")
    (workflows / "steps.yml").write_text(
        """jobs:
  ignored: scalar
  no_steps:
    steps: scalar
  variants:
    steps:
      - scalar
      - uses: actions/checkout@v4
        with:
          path: external
      - run: npm test
        working-directory: missing
      - uses: actions/setup-node@v4
        with:
          node-version: "22.1.0"
        working-directory: external
  local:
    steps:
      - run: npm run local
      - run: npm run dynamic-directory
        working-directory: ${{ matrix.directory }}
  dynamic_checkout:
    steps:
      - uses: actions/checkout@v4
        with:
          path: ${{ inputs.path }}
      - run: npm test
""",
        encoding="utf-8",
    )

    result = scan_repository(repository)

    assert "javascript.invalid-ci-workflow" in _codes(result, "diagnostics")
    runtime = [item for item in result.findings if item.code == "javascript.runtime.declaration"]
    assert len(runtime) == 1
    commands = [item for item in result.findings if item.code == "javascript.ci.command"]
    assert len(commands) == 1


@pytest.mark.verifies("TST028")
@pytest.mark.parametrize("content", ["[", "[]\n"])
def test_invalid_gitlab_documents_are_partial(tmp_path: Path, content: str) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    (repository / ".gitlab-ci.yml").write_text(content, encoding="utf-8")

    result = scan_repository(repository)

    assert result.completion == "partial"
    assert "javascript.invalid-ci-workflow" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST028")
def test_gitlab_include_depth_is_bounded(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_json(repository / "package.json", {})
    for index in range(18):
        path = repository / (".gitlab-ci.yml" if index == 0 else f"ci/{index}.yml")
        path.parent.mkdir(parents=True, exist_ok=True)
        next_path = f"ci/{index + 1}.yml"
        path.write_text(f"include:\n  - local: {next_path}\n", encoding="utf-8")

    result = scan_repository(repository)

    assert "javascript.ci-include-depth" in _codes(result, "diagnostics")


@pytest.mark.verifies("TST029")
def test_detector_tolerates_selective_read_failures() -> None:
    result = javascript.detect_javascript(
        InMemoryDetectorView(
            {"package.json": b"{}"},
            paths=(
                "package.json",
                ".yarnrc.yml",
                "pnpm-workspace.yaml",
                ".nvmrc",
                "tsconfig.json",
                ".github/workflows/test.yml",
                ".gitlab-ci.yml",
            ),
        ),
        DetectionContext(),
    )

    assert len(result.components) == 1
    assert result.diagnostics == ()
    assert result.relationships == ()


@pytest.mark.verifies("TST029")
def test_detector_ignores_unreadable_catalogued_files() -> None:
    result = javascript.detect_javascript(
        InMemoryDetectorView({"package.json": None}), DetectionContext()
    )

    assert result.evidence == ()
    assert result.components == ()
    assert result.findings == ()
    assert result.diagnostics == ()
    assert result.relationships == ()
