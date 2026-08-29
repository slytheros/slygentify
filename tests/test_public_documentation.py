"""Public documentation, examples, and package metadata checks."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

import slygentify
from slygentify import (
    doctor_repository,
    dump_doctor_json,
    dump_scan_json,
    dump_scan_projection_json,
    load_doctor_json,
    load_scan_json,
    load_scan_projection_json,
    map_repository,
    scan_repository,
)
from slygentify._configuration import load_configuration

ROOT = Path(__file__).parents[1]
DOCS = ROOT / "docs"


class _DocumentationLinkParser(HTMLParser):
    """Collect anchors and local references from a generated HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: set[str] = set()
        self.links: list[str] = []
        self.resources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        for name in ("id", "name"):
            if value := attributes.get(name):
                self.anchors.add(value)
        if tag == "a" and (href := attributes.get("href")) is not None:
            self.links.append(href)
        elif tag == "link" and (href := attributes.get("href")) is not None:
            self.resources.append(href)
        elif tag in {"img", "script"} and (src := attributes.get("src")) is not None:
            self.resources.append(src)


@pytest.mark.verifies("TST051")
def test_public_package_metadata_and_documentation_dependencies_are_exact() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata = project["project"]

    assert metadata["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"] == {"path": "src/slygentify/_version.py"}
    assert slygentify.__version__ == "1.0.0rc1"
    assert metadata["license"] == "Apache-2.0"
    assert metadata["license-files"] == ["LICENSE"]
    assert metadata["urls"] == {
        "Homepage": "https://github.com/slytheros/slygentify",
        "Source": "https://github.com/slytheros/slygentify",
        "Documentation": "https://github.com/slytheros/slygentify/tree/develop/docs",
        "Issues": "https://github.com/slytheros/slygentify/issues",
        "Changelog": "https://github.com/slytheros/slygentify/blob/develop/CHANGELOG.md",
    }
    assert {
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Topic :: Software Development",
        "Topic :: Software Development :: Documentation",
    } <= set(metadata["classifiers"])
    assert not any(item.startswith("License ::") for item in metadata["classifiers"])
    assert project["dependency-groups"]["docs"] == [
        "mkdocs>=1.6.1,<2",
        "mkdocstrings-python>=2.0.5,<3",
    ]
    configuration = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    assert "favicon: assets/logo.png" in configuration
    assert "custom_dir: overrides" in configuration
    assert "stylesheets/slygentify.css" in configuration
    assert (DOCS / "assets" / "logo.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "assets/logo.png" in (ROOT / "overrides" / "main.html").read_text(encoding="utf-8")
    stylesheet = (DOCS / "stylesheets" / "slygentify.css").read_text(encoding="utf-8")
    for color in ("#071d3a", "#08b8b2", "#006a70", "#f6f9fc"):
        assert color in stylesheet


@pytest.mark.verifies("TST051")
def test_generated_api_reference_tracks_exact_public_exports() -> None:
    reference = (DOCS / "api.md").read_text(encoding="utf-8")
    generated = reference.split("## Generated public reference", maxsplit=1)[1]
    documented = set(re.findall(r"^        - ([A-Za-z][A-Za-z0-9_]*)$", generated, re.MULTILINE))

    assert documented == set(slygentify.__all__)
    assert not any(name.startswith("_") for name in documented)
    assert "allow_inspection: false" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")


@pytest.mark.verifies("TST051")
def test_packaged_schemas_and_documented_examples_are_valid() -> None:
    schema_directory = ROOT / "src" / "slygentify" / "schemas"
    schema_paths = sorted(schema_directory.glob("*.schema.json"))
    expected = {
        "doctor-v1.schema.json",
        "scan-projection-v1.schema.json",
        "scan-v1.schema.json",
        "state-v1.schema.json",
        "state-v2.schema.json",
    }
    assert {path.name for path in schema_paths} == expected

    schema_reference = (DOCS / "schemas.md").read_text(encoding="utf-8")
    schemas: dict[str, object] = {}
    for path in schema_paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schemas[path.name] = schema
        assert path.name in schema_reference

    examples = DOCS / "examples"
    assert load_scan_json((examples / "scan.json").read_bytes()).schema_version == 1
    assert load_scan_projection_json((examples / "map.json").read_bytes()).schema_version == 1
    assert load_doctor_json((examples / "doctor.json").read_bytes()).schema_version == 1
    state = json.loads((examples / "state.json").read_text(encoding="utf-8"))
    Draft202012Validator(schemas["state-v2.schema.json"]).validate(state)

    representative_scan = load_scan_json((examples / "representative-scan.json").read_bytes())
    representative_map = load_scan_projection_json(
        (examples / "representative-map.json").read_bytes()
    )
    representative_doctor = load_doctor_json((examples / "representative-doctor.json").read_bytes())
    assert representative_scan.completion == "partial"
    assert {component.path for component in representative_scan.components} >= {
        ".",
        "examples/demo",
        "packages/web",
    }
    assert any(
        finding.code == "composition.auxiliary-component" and finding.classification == "inferred"
        for finding in representative_scan.findings
    )
    assert {diagnostic.code for diagnostic in representative_scan.diagnostics} == {
        "javascript.invalid-manifest"
    }
    assert representative_map.source_completion == "partial"
    assert representative_map.scope.matched_component_path == "packages/web"
    assert any(
        diagnostic.code == "doctor.inspection.partial" and diagnostic.severity == "warning"
        for diagnostic in representative_doctor.diagnostics
    )


@pytest.mark.verifies("TST051")
def test_representative_examples_match_public_api_output(tmp_path: Path) -> None:
    source = DOCS / "examples" / "tutorial-repository"
    repository = tmp_path / "tutorial-repository"
    shutil.copytree(source, repository)
    (repository / ".git").mkdir()

    scan = scan_repository(repository)
    projection = map_repository(
        repository,
        scope="packages/web/src/app.ts",
        sections=("orientation", "workflows", "architecture", "boundaries"),
        max_bytes="unlimited",
    )
    doctor = doctor_repository(repository)

    assert dump_scan_json(scan) == _fixture_bytes(DOCS / "examples" / "representative-scan.json")
    assert dump_scan_projection_json(projection) == _fixture_bytes(
        DOCS / "examples" / "representative-map.json"
    )
    assert dump_doctor_json(doctor) == _fixture_bytes(
        DOCS / "examples" / "representative-doctor.json"
    )


def _fixture_bytes(path: Path) -> bytes:
    """Return text fixtures with platform newline normalization for byte assertions."""
    with path.open("r", encoding="utf-8", newline=None) as fixture:
        return fixture.read().encode("utf-8")


@pytest.mark.verifies("TST051")
def test_task_guides_compile_python_and_state_supported_output_modes() -> None:
    guides = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted((DOCS / "guides").glob("*.md"))
    }
    assert set(guides) == {"doctor", "init", "map", "scan", "troubleshooting"}
    assert "Init has no JSON output mode" in guides["init"]
    assert "Map has no text output mode" in guides["map"]
    assert "--format json" in guides["scan"]
    assert "--format json" in guides["doctor"]
    assert "never executes discovered" in guides["doctor"]
    assert "### PowerShell" in guides["doctor"]
    assert "## Git tracked-path discovery is unavailable" in guides["troubleshooting"]
    assert "Do not weaken containment" in guides["troubleshooting"]

    blocks = re.compile(r"```python\n(.*?)```", re.DOTALL)
    compiled = 0
    tutorial = DOCS / "tutorials" / "first-repository.md"
    api_topics = sorted((DOCS / "api").glob("*.md"))
    assert {path.stem for path in api_topics} == {"doctor", "initialization", "map", "scan"}
    for path in [*sorted((DOCS / "guides").glob("*.md")), tutorial, DOCS / "api.md", *api_topics]:
        for index, source in enumerate(blocks.findall(path.read_text(encoding="utf-8"))):
            compile(source, f"{path}:{index}", "exec")
            compiled += 1
    assert compiled >= 9

    tutorial_text = tutorial.read_text(encoding="utf-8")
    assert "## Path A: inspect your own Git repository" in tutorial_text
    assert "## Path B: use the deterministic tutorial fixture" in tutorial_text
    for command in ("slygentify scan", "slygentify map", "slygentify init", "slygentify doctor"):
        assert command in tutorial_text
    for guide in guides.values():
        assert "## Next steps" in guide
    navigation = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for page in (
        "tutorials/first-repository.md",
        "guides/troubleshooting.md",
        "api/scan.md",
        "api/doctor.md",
        "api/map.md",
        "api/initialization.md",
    ):
        assert page in navigation


@pytest.mark.verifies("TST051")
def test_documented_resource_defaults_match_effective_configuration(tmp_path: Path) -> None:
    configuration = load_configuration(tmp_path)
    reference = (DOCS / "configuration-and-provenance.md").read_text(encoding="utf-8")
    for limit in configuration.limits:
        assert f"| `{limit.name}` | {limit.default:,} |" in reference

    accounting = (DOCS / "inspection-accounting.md").read_text(encoding="utf-8")
    assert "configuration-and-provenance.md#scanlimits" in accounting
    assert "at most 10 seconds" in accounting
    assert "64 KiB" in accounting
    for stale_claim in ("depth 64", "100,000 examined entries", "60-second scan deadline"):
        assert stale_claim not in accounting


@pytest.mark.verifies("TST051")
def test_public_documentation_site_builds_strictly_without_private_history(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    result = subprocess.run(
        ["mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    assert (site / "index.html").is_file()
    assert (site / "api.html").is_file()
    assert (site / "api" / "scan.html").is_file()
    assert (site / "guides" / "troubleshooting.html").is_file()
    assert not (site / "adr").exists()
    assert not (site / "acceptance").exists()

    public_output = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(site.rglob("*.html"))
    )
    private_forge_host = "sly" + "server"
    assert private_forge_host not in public_output.casefold()
    assert "http://" + private_forge_host not in public_output.casefold()
    assert not re.search(r"\bM\d+-[A-Z]\d+\b", public_output)
    assert not re.search(r"(?:Gitea )?(?:issue|pull request|PR) #\d+", public_output, re.IGNORECASE)
    api_output = (site / "api.html").read_text(encoding="utf-8")
    for name in slygentify.__all__:
        assert name in api_output

    assert "use_directory_urls: false" in (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    pages: dict[Path, _DocumentationLinkParser] = {}
    for page in sorted(site.rglob("*.html")):
        # MkDocs' server-only fallback has root-relative asset and navigation URLs.
        # It is not part of the locally browsable documentation page set.
        if page.name == "404.html":
            continue
        parser = _DocumentationLinkParser()
        parser.feed(page.read_text(encoding="utf-8"))
        pages[page.resolve()] = parser

    site_root = site.resolve()
    for page, parser in pages.items():
        for href in [*parser.links, *parser.resources]:
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc:
                continue
            assert not parsed.path.startswith("/"), f"{page}: non-portable root URL {href!r}"
            target = page if not parsed.path else (page.parent / unquote(parsed.path)).resolve()
            try:
                target.relative_to(site_root)
            except ValueError:
                pytest.fail(f"{page}: link escapes the generated site: {href!r}")
            assert target.is_file(), f"{page}: missing link target {href!r}"
            if parsed.fragment and target.suffix == ".html":
                assert parsed.fragment in pages[target].anchors, (
                    f"{page}: missing fragment {parsed.fragment!r} in {href!r}"
                )
