"""Release workflow, artifact, recovery, and provenance tests."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from tools import release

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _write_distributions(directory: Path, version: str = "1.2.3") -> None:
    directory.mkdir()
    wheel = directory / f"slygentify-{version}-py3-none-any.whl"
    dist_info = f"slygentify-{version}.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.4\nName: slygentify\nVersion: {version}\n",
        )
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        archive.writestr("slygentify/__init__.py", f'__version__ = "{version}"\n')
    sdist = directory / f"slygentify-{version}.tar.gz"
    metadata = f"Metadata-Version: 2.4\nName: slygentify\nVersion: {version}\n".encode()
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo(f"slygentify-{version}/PKG-INFO")
        info.size = len(metadata)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(metadata))


def _bundle(tmp_path: Path, tag: str = "v1.2.3") -> Path:
    distributions = tmp_path / "dist"
    _write_distributions(distributions, release.release_version_from_tag(tag))
    bundle = tmp_path / "bundle"
    release.prepare_release_bundle(distributions, bundle, tag)
    return bundle


@pytest.mark.verifies("TST053")
@pytest.mark.parametrize(
    ("tag", "version"),
    [("v1.0.0-rc.1", "1.0.0rc1"), ("v1.0.0", "1.0.0"), ("v1.2.3-rc.4", "1.2.3rc4")],
)
def test_release_tag_mapping_accepts_only_final_and_rc(tag: str, version: str) -> None:
    assert release.release_version_from_tag(tag) == version


@pytest.mark.verifies("TST053")
@pytest.mark.parametrize(
    "tag",
    [
        "1.2.3",
        "v01.2.3",
        "v1.02.3",
        "v1.2.03",
        "v1.2",
        "v1.2.3-rc.0",
        "v1.2.3-rc.01",
        "v1.2.3-alpha.1",
        "v1.2.3+build",
        "v1.2.3.post1",
        "v0.1.0",
    ],
)
def test_release_tag_mapping_rejects_every_other_form(tag: str) -> None:
    with pytest.raises(release.ReleaseError):
        release.release_version_from_tag(tag)


@pytest.mark.verifies("TST053")
def test_source_check_verification_requires_every_latest_protected_check() -> None:
    successful = {
        "check_runs": [
            {"name": name, "conclusion": "success"}
            for name in sorted(release.REQUIRED_SOURCE_CHECKS)
        ]
    }
    release.verify_source_checks(successful)

    failed = json.loads(json.dumps(successful))
    failed["check_runs"][0]["conclusion"] = "failure"
    with pytest.raises(release.ReleaseError, match="missing or unsuccessful"):
        release.verify_source_checks(failed)

    for invalid in (None, {"check_runs": None}, {"check_runs": [None]}):
        with pytest.raises(release.ReleaseError, match="invalid"):
            release.verify_source_checks(invalid)


@pytest.mark.verifies("TST053")
def test_release_bundle_is_canonical_and_detects_byte_or_manifest_changes(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    manifest = release.verify_release_bundle(bundle, "v1.2.3")

    assert manifest.version == "1.2.3"
    assert [item.filename for item in manifest.artifacts] == [
        "slygentify-1.2.3-py3-none-any.whl",
        "slygentify-1.2.3.tar.gz",
    ]
    assert (bundle / "SHA256SUMS").read_text(encoding="utf-8") == "".join(
        f"{item.sha256}  {item.filename}\n" for item in manifest.artifacts
    )
    document = json.loads((bundle / "release-manifest.json").read_text(encoding="utf-8"))
    assert document["repository"] == release.REPOSITORY

    artifact = bundle / "dist" / manifest.artifacts[0].filename
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    with pytest.raises(release.ReleaseError, match="inspect wheel|match the recorded"):
        release.verify_release_bundle(bundle, "v1.2.3")


@pytest.mark.verifies("TST053")
def test_distribution_inspection_rejects_inventory_metadata_and_unsafe_sdist(
    tmp_path: Path,
) -> None:
    distributions = tmp_path / "dist"
    _write_distributions(distributions)
    (distributions / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(release.ReleaseError, match="exactly"):
        release.inspect_distributions(distributions, "v1.2.3")
    (distributions / "unexpected.txt").unlink()

    wheel = distributions / "slygentify-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "slygentify-1.2.3.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: other\nVersion: 1.2.3\n",
        )
        archive.writestr("slygentify-1.2.3.dist-info/WHEEL", "Tag: py3-none-any\n")
    with pytest.raises(release.ReleaseError, match="metadata does not match"):
        release.inspect_distributions(distributions, "v1.2.3")

    _write_distributions(tmp_path / "clean")
    unsafe = tmp_path / "clean" / "slygentify-1.2.3.tar.gz"
    with tarfile.open(unsafe, "w:gz") as archive:
        info = tarfile.TarInfo("../escape")
        info.size = 0
        archive.addfile(info, io.BytesIO())
    with pytest.raises(release.ReleaseError, match="unsafe"):
        release.inspect_distributions(tmp_path / "clean", "v1.2.3")


@pytest.mark.verifies("TST053")
def test_registry_modes_admit_only_empty_partial_or_complete_matching_state(tmp_path: Path) -> None:
    manifest = release.verify_release_bundle(_bundle(tmp_path), "v1.2.3")
    local = {item.filename: item.sha256 for item in manifest.artifacts}
    first, second = local

    publish = release.classify_registry_state(manifest, {}, "publish")
    assert publish.upload == tuple(sorted(local))
    recovery = release.classify_registry_state(manifest, {first: local[first]}, "recover-partial")
    assert recovery.existing == (first,)
    assert recovery.upload == (second,)
    verify = release.classify_registry_state(manifest, local, "verify-only")
    assert verify.upload == ()

    for remote, mode, message in (
        (local, "publish", "empty"),
        ({}, "recover-partial", "exactly one"),
        ({first: local[first]}, "verify-only", "both"),
        ({"unexpected.whl": "0" * 64}, "publish", "unsafe"),
        ({first: "0" * 64}, "recover-partial", "unsafe"),
    ):
        with pytest.raises(release.ReleaseError, match=message):
            release.classify_registry_state(
                manifest,
                remote,
                release._mode(mode),  # noqa: SLF001
            )


@pytest.mark.verifies("TST053")
def test_registry_staging_copies_only_the_missing_verified_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    manifest = release.verify_release_bundle(bundle, "v1.2.3")
    existing = manifest.artifacts[0]
    monkeypatch.setattr(
        release,
        "_read_registry_files",
        lambda _root, _manifest: {existing.filename: existing.sha256},
    )

    destination = tmp_path / "publish"
    plan = release.stage_registry_upload(
        bundle, destination, "https://example.invalid", "recover-partial", "v1.2.3"
    )

    assert [path.name for path in destination.iterdir()] == list(plan.upload)
    assert plan.upload == (manifest.artifacts[1].filename,)


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload

    def __iter__(self) -> Any:
        return iter(self.payload.splitlines())


@pytest.mark.verifies("TST053")
def test_registry_query_and_provenance_are_bounded_and_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    manifest = release.verify_release_bundle(bundle, "v1.2.3")
    urls = [
        {
            "filename": item.filename,
            "digests": {"sha256": item.sha256},
            "yanked": False,
        }
        for item in manifest.artifacts
    ]
    requests: list[str] = []

    def open_request(request: Any, timeout: int) -> _Response:
        assert timeout == 30
        requests.append(request.full_url)
        return _Response(
            {"urls": urls} if "/pypi/" in request.full_url else {"attestation_bundles": []}
        )

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    provenance = tmp_path / "provenance"
    release.fetch_registry_provenance(bundle, provenance, "https://index.example", "v1.2.3")

    assert requests[0] == "https://index.example/pypi/slygentify/1.2.3/json"
    assert len(list(provenance.iterdir())) == 2
    assert all("/integrity/slygentify/1.2.3/" in url for url in requests[1:])

    def not_found(_request: Any, timeout: int) -> _Response:
        raise urllib.error.HTTPError("url", 404, "missing", Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", not_found)
    assert release._read_registry_files("https://index.example", manifest) == {}  # noqa: SLF001


@pytest.mark.verifies("TST053")
def test_smoke_install_uses_separate_isolated_runs_for_each_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    commands: list[list[str]] = []

    def run(command: list[str], check: bool) -> None:
        assert check is True
        commands.append(command)

    monkeypatch.setattr(subprocess, "run", run)
    release.smoke_install_bundle(bundle, "v1.2.3")

    assert len(commands) == 4
    assert all(command[:4] == ["uv", "run", "--isolated", "--no-project"] for command in commands)
    assert [command[-2:] for command in commands] == [
        ["-c", "import slygentify; assert slygentify.__version__ == '1.2.3'"],
        ["slygentify", "--help"],
        ["-c", "import slygentify; assert slygentify.__version__ == '1.2.3'"],
        ["slygentify", "--help"],
    ]


@pytest.mark.verifies("TST053")
@pytest.mark.parametrize("installer", ["uv-tool", "pipx"])
def test_published_tool_install_is_exact_isolated_and_executable(
    installer: release.ToolInstaller,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle(tmp_path, "v1.0.0-rc.1")
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(command: list[str], *, check: bool, env: dict[str, str]) -> None:
        assert check is True
        calls.append((command, env))
        if len(calls) == 1:
            home_variable = "UV_TOOL_DIR" if installer == "uv-tool" else "PIPX_HOME"
            metadata = (
                Path(env[home_variable])
                / "environment"
                / "slygentify-1.0.0rc1.dist-info"
                / "METADATA"
            )
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                "Metadata-Version: 2.4\nName: slygentify\nVersion: 1.0.0rc1\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name, *, path: str(Path(path.split(os.pathsep)[0]) / name),
    )

    release.smoke_install_published_tool(
        bundle,
        "v1.0.0-rc.1",
        installer,
        "https://test.pypi.org",
        "https://pypi.org",
    )

    assert len(calls) == 2
    install, environment = calls[0]
    assert install[-1] == "slygentify==1.0.0rc1"
    assert environment["PATH"].split(os.pathsep)[0]
    for name in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "UV_INDEX_URL"):
        assert name not in environment
    if installer == "uv-tool":
        assert install[:3] == ["uv", "tool", "install"]
        assert install[install.index("--index") + 1] == "https://test.pypi.org/simple"
        assert install[install.index("--default-index") + 1] == "https://pypi.org/simple"
        assert environment["UV_TOOL_BIN_DIR"] == environment["PATH"].split(os.pathsep)[0]
        assert environment["UV_CACHE_DIR"].endswith("cache")
    else:
        assert install[:4] == [sys.executable, "-m", "pipx", "install"]
        assert install[install.index("--backend") + 1] == "pip"
        assert install[install.index("--index-url") + 1] == "https://test.pypi.org/simple"
        assert install[install.index("--pip-args") + 1] == (
            "--isolated --extra-index-url https://pypi.org/simple"
        )
        assert environment["PIPX_BIN_DIR"] == environment["PATH"].split(os.pathsep)[0]
        assert environment["PIPX_MAN_DIR"].endswith("man")
        assert environment["PIP_CACHE_DIR"].endswith("cache")
    assert calls[1][0][-1] == "--help"


@pytest.mark.verifies("TST053")
def test_published_tool_install_fails_closed_on_missing_or_wrong_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path, "v1.0.0-rc.1")

    def install_wrong_version(_command: list[str], *, check: bool, env: dict[str, str]) -> None:
        assert check is True
        metadata = (
            Path(env["UV_TOOL_DIR"]) / "environment" / "slygentify-1.0.0.dist-info" / "METADATA"
        )
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            "Metadata-Version: 2.4\nName: slygentify\nVersion: 1.0.0\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(subprocess, "run", install_wrong_version)
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    with pytest.raises(release.ReleaseError, match="does not match release version"):
        release.smoke_install_published_tool(bundle, "v1.0.0-rc.1", "uv-tool", "https://pypi.org")

    def install_without_executable(
        _command: list[str], *, check: bool, env: dict[str, str]
    ) -> None:
        assert check is True
        metadata = (
            Path(env["UV_TOOL_DIR"]) / "environment" / "slygentify-1.0.0rc1.dist-info" / "METADATA"
        )
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            "Metadata-Version: 2.4\nName: slygentify\nVersion: 1.0.0rc1\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(subprocess, "run", install_without_executable)
    with pytest.raises(release.ReleaseError, match="did not expose"):
        release.smoke_install_published_tool(bundle, "v1.0.0-rc.1", "uv-tool", "https://pypi.org")


@pytest.mark.verifies("TST053")
@pytest.mark.parametrize("failure_call", [1, 2])
def test_published_tool_install_propagates_subprocess_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    bundle = _bundle(tmp_path, "v1.0.0-rc.1")
    calls = 0

    def fail(command: list[str], *, check: bool, env: dict[str, str]) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise subprocess.CalledProcessError(1, command)
        metadata = (
            Path(env["PIPX_HOME"]) / "environment" / "slygentify-1.0.0rc1.dist-info" / "METADATA"
        )
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            "Metadata-Version: 2.4\nName: slygentify\nVersion: 1.0.0rc1\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: "slygentify")
    with pytest.raises(subprocess.CalledProcessError):
        release.smoke_install_published_tool(bundle, "v1.0.0-rc.1", "pipx", "https://pypi.org")


def _workflow(name: str) -> tuple[str, dict[str, Any]]:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    return text, yaml.load(text, Loader=yaml.BaseLoader)


@pytest.mark.verifies("TST053")
def test_release_workflows_are_distinct_human_gated_and_least_privilege() -> None:
    production_text, production = _workflow("release.yml")
    test_text, testpypi = _workflow("release-testpypi.yml")

    assert set(production["on"]) == {"push", "workflow_dispatch"}
    assert production["on"]["push"]["tags"] == ["v*"]
    assert production["on"]["workflow_dispatch"]["inputs"]["mode"]["options"] == [
        "recover-partial",
        "verify-only",
    ]
    assert set(testpypi["on"]) == {"workflow_dispatch"}
    assert testpypi["on"]["workflow_dispatch"]["inputs"]["mode"]["options"] == [
        "publish",
        "recover-partial",
        "verify-only",
    ]
    assert production["permissions"] == {"contents": "read"}
    assert testpypi["permissions"] == {"contents": "read"}

    for document, environment in ((production, "pypi"), (testpypi, "testpypi")):
        publish = document["jobs"]["publish"]
        assert publish["environment"]["name"] == environment
        assert publish["permissions"] == {"id-token": "write"}
        assert len(publish["steps"]) == 2
        assert publish["needs"] == ["build", "preflight"]
        assert set(document["jobs"]["preflight"]["needs"]) == {
            "build",
            "attest",
            "install-artifacts",
        }
        matrix = document["jobs"]["install-artifacts"]["strategy"]["matrix"]
        assert matrix == {
            "os": ["ubuntu-24.04", "windows-2025", "macos-15"],
            "python-version": ["3.11", "3.12", "3.13", "3.14"],
        }
        published_job = "install-pypi" if environment == "pypi" else "install-testpypi"
        published_matrix = document["jobs"][published_job]["strategy"]["matrix"]
        assert published_matrix == {
            "os": ["ubuntu-24.04", "windows-2025", "macos-15"],
            "python-version": ["3.11", "3.12", "3.13", "3.14"],
            "installer": ["uv-tool", "pipx"],
        }
        assert document["jobs"][published_job]["needs"] == [
            "build",
            "verify-pypi" if environment == "pypi" else "verify-testpypi",
        ]

    for text in (production_text, test_text):
        assert "verify-source-checks check-runs.json" in text
        assert "python -m tools.release smoke-tool" in text
        assert '--installer "${{ matrix.installer }}"' in text
        assert "--no-install-project" not in text
        assert "pull_request_target" not in text
        assert "secrets." not in text
        assert "gh release" not in text
        assert "git tag" not in text
        for line in text.splitlines():
            if "uses:" in line:
                reference = line.split("uses:", maxsplit=1)[1].strip().split()[0]
                assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference)

    for document in (production, testpypi):
        for job in document["jobs"].values():
            steps = job.get("steps", [])
            if any("python -m tools.release" in step.get("run", "") for step in steps):
                installs = [
                    step.get("run")
                    for step in steps
                    if step.get("run", "").startswith("uv sync --locked")
                ]
                assert installs == ["uv sync --locked --all-groups"]

    assert "environment:\n      name: pypi" in production_text
    assert "repository-url: https://test.pypi.org/legacy/" in test_text
