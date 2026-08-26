"""Tests for strict root scan configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pathspec import PathSpec
from pathspec.patterns.gitignore import GitIgnorePatternError

import slygentify._configuration as configuration_module
from slygentify import ScanError, scan_repository
from slygentify._configuration import ComponentDeclaration, ConfigurationError, load_configuration


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    return root


@pytest.mark.verifies("TST035")
def test_configuration_defaults_are_absent_and_valid_configuration_is_effective(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    default = load_configuration(root)
    assert default.sha256 is None
    assert default.value("max_depth") == 256
    assert default.value("max_entries") == 1_000_000
    assert default.value("max_file_bytes") == 256 * 1024 * 1024
    assert default.value("max_total_bytes") == 16 * 1024 * 1024 * 1024
    assert default.value("max_elapsed_seconds") == 30 * 60
    assert default.value("max_open_files") == 256
    assert default.value("max_memory_bytes") == 2 * 1024 * 1024 * 1024
    assert default.max_agents_bytes == 4096
    assert default.max_component_entries == 8
    assert not default.init_relaxed

    (root / "service").mkdir()
    (root / "slygentify.toml").write_text(
        """schema_version = 1
[scan]
ignore = ["generated/**", "!generated/keep.txt"]
[[scan.components]]
path = "service"
ecosystem = "python"
kind = "application"
[scan.limits]
max_entries = "unlimited"
max_depth = 2
""",
        encoding="utf-8",
    )
    configuration = load_configuration(root)
    assert configuration.ignore[-1] == "!generated/keep.txt"
    assert configuration.components[0].path == "service"
    assert configuration.value("max_entries") is None
    assert configuration.relaxed


@pytest.mark.verifies("TST044")
def test_configuration_init_bounds_are_independent_and_report_relaxation(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    target = root / "slygentify.toml"
    target.write_text(
        """schema_version = 1
[init]
max_agents_bytes = 8192
max_component_entries = 3
""",
        encoding="utf-8",
    )

    bounded = load_configuration(root)

    assert bounded.max_agents_bytes == 8192
    assert bounded.max_component_entries == 3
    assert bounded.init_relaxed

    target.write_text(
        """schema_version = 1
[init]
max_agents_bytes = "unlimited"
max_component_entries = "unlimited"
""",
        encoding="utf-8",
    )
    unlimited = load_configuration(root)
    assert unlimited.max_agents_bytes == "unlimited"
    assert unlimited.max_component_entries == "unlimited"
    assert unlimited.init_relaxed


@pytest.mark.verifies("TST035")
def test_configuration_accepts_unlimited_for_every_limit(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    nested = root / "nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("[project]\nname = 'nested'\n", encoding="utf-8")
    limits = "\n".join(f"{name} = 'unlimited'" for name in configuration_module._LIMIT_NAMES)
    (root / "slygentify.toml").write_text(
        f"schema_version = 1\n[scan.limits]\n{limits}\n", encoding="utf-8"
    )

    configuration = load_configuration(root)

    assert all(configuration.value(name) is None for name in configuration_module._LIMIT_NAMES)
    assert scan_repository(root).completion == "complete"


@pytest.mark.verifies("TST035")
@pytest.mark.parametrize(
    "content",
    [
        "schema_version = 2\n",
        "schema_version = true\n",
        "schema_version = 1\nunknown = true\n",
        "schema_version = 1\n[scan]\nunknown = true\n",
        "schema_version = 1\n[scan.limits]\nmax_depth = 0\n",
        "schema_version = 1\n[scan.limits]\nmax_depth = true\n",
        "schema_version = 1\n[init]\nunknown = 1\n",
        "schema_version = 1\n[init]\nmax_agents_bytes = 1535\n",
        "schema_version = 1\n[init]\nmax_agents_bytes = true\n",
        "schema_version = 1\n[init]\nmax_component_entries = 0\n",
        "schema_version = 1\n[init]\nmax_component_entries = 'all'\n",
        "schema_version = 1\n[[scan.components]]\npath = '../outside'\n",
        "schema_version = 1\n[[scan.components]]\npath = 'missing'\n",
    ],
)
def test_configuration_rejects_invalid_or_unsafe_values(tmp_path: Path, content: str) -> None:
    root = _repository(tmp_path)
    (root / "slygentify.toml").write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="no repository files were changed"):
        load_configuration(root)


@pytest.mark.verifies("TST035")
def test_scan_applies_configuration_ignore_component_and_relaxed_diagnostic(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "generated").mkdir()
    (root / "generated" / "Cargo.toml").write_text("[package]\nname='hidden'\n", encoding="utf-8")
    (root / "service").mkdir()
    (root / "slygentify.toml").write_text(
        """schema_version = 1
[scan]
ignore = ["generated/**"]
[[scan.components]]
path = "service"
ecosystem = "python"
kind = "application"
[scan.limits]
max_entries = "unlimited"
""",
        encoding="utf-8",
    )

    result = scan_repository(root)

    assert [item.path for item in result.components] == ["service"]
    assert "configuration.relaxed-limits" in {item.code for item in result.diagnostics}
    assert "generated" in {item.scope for item in result.skipped_scopes}


@pytest.mark.verifies("TST035")
def test_scan_reports_configuration_errors_without_scanning(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "slygentify.toml").write_text("schema_version = 1\ninvalid = true\n", encoding="utf-8")
    with pytest.raises(ScanError, match="slygentify.toml"):
        scan_repository(root)


@pytest.mark.verifies("TST035")
def test_configuration_retains_conflicting_detected_ecosystem(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "service").mkdir()
    (root / "service" / "pyproject.toml").write_text(
        "[project]\nname = 'service'\n", encoding="utf-8"
    )
    (root / "slygentify.toml").write_text(
        "schema_version = 1\n[[scan.components]]\npath = 'service'\necosystem = 'generic'\n",
        encoding="utf-8",
    )
    result = scan_repository(root)
    assert result.components[0].ecosystem == "mixed"
    assert "configuration.component-conflict" in {item.code for item in result.diagnostics}


@pytest.mark.verifies("TST035")
def test_configuration_defensive_validation_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)
    with pytest.raises(KeyError):
        load_configuration(root).value("missing")
    for value in (None, "", "a\\b", "a\x00b", "/a", "C:a", "a/../b"):
        with pytest.raises(ConfigurationError):
            configuration_module._safe_path(value)
    with pytest.raises(ConfigurationError):
        configuration_module._identifier(" ")
    with pytest.raises(ConfigurationError):
        configuration_module._limit(False)

    target = root / "slygentify.toml"
    target.mkdir()
    with pytest.raises(ConfigurationError):
        load_configuration(root)
    target.rmdir()
    target.write_bytes(b"\xef\xbb\bfschema_version = 1\n")
    with pytest.raises(ConfigurationError):
        load_configuration(root)
    target.write_bytes(b"\xff")
    with pytest.raises(ConfigurationError):
        load_configuration(root)
    target.write_text("schema_version = 1\n[scan]\nignore = 1\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_configuration(root)
    target.write_text("schema_version = 1\n[scan]\ncomponents = 1\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_configuration(root)
    target.write_text("schema_version = 1\n[scan]\nlimits = 1\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_configuration(root)
    target.write_text("schema_version = 1\ninit = 1\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_configuration(root)

    nested = root / "nested"
    nested.mkdir()
    (nested / ".git").mkdir()
    with pytest.raises(ConfigurationError):
        configuration_module._validate_component(
            root, ComponentDeclaration("nested", None, None, "x")
        )
    monkeypatch.setattr(
        PathSpec,
        "from_lines",
        lambda *args: (_ for _ in ()).throw(GitIgnorePatternError("bad")),
    )
    target.write_text("schema_version = 1\n[scan]\nignore = ['x']\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_configuration(root)
