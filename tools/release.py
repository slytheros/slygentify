"""Deterministic, fail-closed helpers for the human-gated release workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn, cast

from packaging.version import Version

from slygentify.traceability import implements

PROJECT_NAME = "slygentify"
REPOSITORY = "https://github.com/slytheros/slygentify"
TAG_PATTERN = re.compile(
    r"^v(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-rc\.(?P<rc>[1-9][0-9]*))?$"
)
RegistryMode = Literal["publish", "recover-partial", "verify-only"]
REQUIRED_SOURCE_CHECKS = frozenset(
    {
        "Code quality",
        "CodeQL (Python)",
        "Dependency vulnerabilities",
        "Package",
        *{
            f"Tests ({system}, Python {python})"
            for system in ("ubuntu-24.04", "windows-2025", "macos-15")
            for python in ("3.11", "3.12", "3.13", "3.14")
        },
    }
)


class ReleaseError(ValueError):
    """Raised when release evidence is incomplete, inconsistent, or unsafe."""


@dataclass(frozen=True, slots=True)
class Artifact:
    """One immutable distribution artifact."""

    filename: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """The exact release identity and its two immutable files."""

    tag: str
    version: str
    artifacts: tuple[Artifact, ...]


@dataclass(frozen=True, slots=True)
class RegistryPlan:
    """A fail-closed decision about which files, if any, may be uploaded."""

    mode: RegistryMode
    upload: tuple[str, ...]
    existing: tuple[str, ...]


def _fail(message: str) -> NoReturn:
    raise ReleaseError(message)


@implements("REQ052")
def release_version_from_tag(tag: str) -> str:
    """Map one permitted SemVer tag to the exact PEP 440 package version."""
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        _fail("release tag must be vX.Y.Z or vX.Y.Z-rc.N with canonical integers")
    base = ".".join(match.group(name) for name in ("major", "minor", "patch"))
    rc = match.group("rc")
    version = Version(base if rc is None else f"{base}rc{rc}")
    if version < Version("1.0.0rc1"):
        _fail("the first genuine publication must be version 1.0.0rc1 or newer")
    return str(version)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_fields(raw: bytes, source: str) -> tuple[str, str]:
    message = BytesParser(policy=default).parsebytes(raw)
    name = message.get("Name")
    version = message.get("Version")
    if not isinstance(name, str) or not isinstance(version, str):
        _fail(f"{source} does not contain string Name and Version metadata")
    return name, version


def _expected_filenames(version: str) -> tuple[str, str]:
    return (
        f"{PROJECT_NAME}-{version}-py3-none-any.whl",
        f"{PROJECT_NAME}-{version}.tar.gz",
    )


@implements("REQ052")
def verify_source_checks(document: object) -> None:
    """Require the latest protected source checks to have succeeded for the tag commit."""
    if not isinstance(document, dict) or not isinstance(document.get("check_runs"), list):
        _fail("GitHub returned an invalid check-runs document")
    conclusions: dict[str, str | None] = {}
    for item in document["check_runs"]:
        if not isinstance(item, dict):
            _fail("GitHub returned an invalid check-run record")
        name, conclusion = item.get("name"), item.get("conclusion")
        if not isinstance(name, str) or (
            conclusion is not None and not isinstance(conclusion, str)
        ):
            _fail("GitHub check-run name or conclusion is invalid")
        conclusions[name] = conclusion
    unsuccessful = sorted(
        name for name in REQUIRED_SOURCE_CHECKS if conclusions.get(name) != "success"
    )
    if unsuccessful:
        _fail(f"protected source checks are missing or unsuccessful: {unsuccessful!r}")


def _validate_wheel(path: Path, version: str) -> None:
    metadata_name = f"{PROJECT_NAME}-{version}.dist-info/METADATA"
    wheel_name = f"{PROJECT_NAME}-{version}.dist-info/WHEEL"
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if metadata_name not in names or wheel_name not in names:
                _fail(f"{path.name} is missing canonical wheel metadata")
            name, embedded_version = _metadata_fields(
                archive.read(metadata_name), f"{path.name} METADATA"
            )
            wheel_metadata = archive.read(wheel_name).decode("utf-8")
    except (KeyError, UnicodeDecodeError, zipfile.BadZipFile) as error:
        raise ReleaseError(f"unable to inspect wheel {path.name}: {error}") from error
    if name != PROJECT_NAME or embedded_version != version:
        _fail(f"{path.name} metadata does not match {PROJECT_NAME} {version}")
    if "Tag: py3-none-any\n" not in wheel_metadata.replace("\r\n", "\n"):
        _fail(f"{path.name} is not a py3-none-any wheel")


def _validate_sdist(path: Path, version: str) -> None:
    prefix = f"{PROJECT_NAME}-{version}"
    metadata_name = f"{prefix}/PKG-INFO"
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or ".." in pure.parts or pure.parts[:1] != (prefix,):
                    _fail(f"{path.name} contains unsafe or unexpected member {member.name!r}")
            metadata = archive.extractfile(metadata_name)
            if metadata is None:
                _fail(f"{path.name} is missing PKG-INFO")
            name, embedded_version = _metadata_fields(metadata.read(), f"{path.name} PKG-INFO")
    except (KeyError, tarfile.TarError) as error:
        raise ReleaseError(f"unable to inspect source distribution {path.name}: {error}") from error
    if name != PROJECT_NAME or embedded_version != version:
        _fail(f"{path.name} metadata does not match {PROJECT_NAME} {version}")


@implements("REQ052")
def inspect_distributions(directory: Path, tag: str) -> ReleaseManifest:
    """Validate and digest the exact wheel and sdist for ``tag``."""
    version = release_version_from_tag(tag)
    expected = _expected_filenames(version)
    files = sorted(path for path in directory.iterdir() if path.is_file())
    if tuple(path.name for path in files) != expected:
        _fail(f"distribution directory must contain exactly {expected!r}")
    _validate_wheel(files[0], version)
    _validate_sdist(files[1], version)
    artifacts = tuple(Artifact(path.name, _sha256(path), path.stat().st_size) for path in files)
    return ReleaseManifest(tag, version, artifacts)


def _manifest_document(manifest: ReleaseManifest) -> dict[str, object]:
    return {
        "schema_version": 1,
        "project": PROJECT_NAME,
        "repository": REPOSITORY,
        "tag": manifest.tag,
        "version": manifest.version,
        "artifacts": [
            {"filename": item.filename, "sha256": item.sha256, "size": item.size}
            for item in manifest.artifacts
        ],
    }


@implements("REQ052")
def prepare_release_bundle(distributions: Path, bundle: Path, tag: str) -> ReleaseManifest:
    """Create a reviewable bundle containing only verified release evidence."""
    manifest = inspect_distributions(distributions, tag)
    if bundle.exists():
        _fail(f"release bundle target already exists: {bundle}")
    artifact_directory = bundle / "dist"
    artifact_directory.mkdir(parents=True)
    for artifact in manifest.artifacts:
        shutil.copyfile(distributions / artifact.filename, artifact_directory / artifact.filename)
    checksums = "".join(
        f"{artifact.sha256}  {artifact.filename}\n" for artifact in manifest.artifacts
    )
    (bundle / "SHA256SUMS").write_text(checksums, encoding="utf-8", newline="\n")
    document = json.dumps(
        _manifest_document(manifest), ensure_ascii=False, indent=2, sort_keys=True
    )
    (bundle / "release-manifest.json").write_text(document + "\n", encoding="utf-8", newline="\n")
    return manifest


def _load_manifest(bundle: Path) -> ReleaseManifest:
    try:
        raw = json.loads((bundle / "release-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(f"unable to load release manifest: {error}") from error
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "project",
        "repository",
        "tag",
        "version",
        "artifacts",
    }:
        _fail("release manifest has unexpected fields")
    if raw["schema_version"] != 1 or raw["project"] != PROJECT_NAME:
        _fail("release manifest has an unsupported identity")
    if raw["repository"] != REPOSITORY:
        _fail("release manifest repository does not match the trusted publisher")
    if not isinstance(raw["tag"], str) or not isinstance(raw["version"], str):
        _fail("release manifest tag and version must be strings")
    version = release_version_from_tag(raw["tag"])
    if raw["version"] != version:
        _fail("release manifest tag and version do not match")
    raw_artifacts = raw["artifacts"]
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != 2:
        _fail("release manifest must contain exactly two artifacts")
    artifacts: list[Artifact] = []
    for item in raw_artifacts:
        if not isinstance(item, dict) or set(item) != {"filename", "sha256", "size"}:
            _fail("release manifest artifact has unexpected fields")
        filename, digest, size = item["filename"], item["sha256"], item["size"]
        if (
            not isinstance(filename, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size <= 0
        ):
            _fail("release manifest artifact values are invalid")
        artifacts.append(Artifact(filename, digest, size))
    if tuple(item.filename for item in artifacts) != _expected_filenames(version):
        _fail("release manifest artifact filenames do not match its version")
    return ReleaseManifest(raw["tag"], version, tuple(artifacts))


@implements("REQ052")
def verify_release_bundle(bundle: Path, tag: str) -> ReleaseManifest:
    """Revalidate every byte and metadata field in a prepared release bundle."""
    manifest = _load_manifest(bundle)
    if manifest.tag != tag:
        _fail(f"release bundle tag {manifest.tag!r} does not match {tag!r}")
    actual = inspect_distributions(bundle / "dist", tag)
    if actual != manifest:
        _fail("release bundle artifacts do not match the recorded manifest")
    expected_checksums = "".join(
        f"{artifact.sha256}  {artifact.filename}\n" for artifact in manifest.artifacts
    )
    try:
        recorded = (bundle / "SHA256SUMS").read_text(encoding="utf-8")
    except OSError as error:
        raise ReleaseError(f"unable to read SHA256SUMS: {error}") from error
    if recorded != expected_checksums:
        _fail("SHA256SUMS does not match the release manifest")
    return manifest


def _read_registry_files(index_root: str, manifest: ReleaseManifest) -> dict[str, str]:
    url = f"{index_root.rstrip('/')}/pypi/{PROJECT_NAME}/{manifest.version}/json"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            document = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise ReleaseError(f"registry query failed with HTTP {error.code}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(f"registry query failed: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("urls"), list):
        _fail("registry returned an invalid release document")
    remote: dict[str, str] = {}
    for item in document["urls"]:
        if not isinstance(item, dict):
            _fail("registry returned an invalid file record")
        filename = item.get("filename")
        digests = item.get("digests")
        if (
            not isinstance(filename, str)
            or not isinstance(digests, dict)
            or not isinstance(digests.get("sha256"), str)
            or item.get("yanked") is not False
        ):
            _fail("registry file record is incomplete or yanked")
        if filename in remote:
            _fail(f"registry returned duplicate filename {filename!r}")
        remote[filename] = cast(str, digests["sha256"])
    return remote


@implements("REQ052")
def classify_registry_state(
    manifest: ReleaseManifest, remote: dict[str, str], mode: RegistryMode
) -> RegistryPlan:
    """Decide which exact files a selected publication mode may upload."""
    local = {item.filename: item.sha256 for item in manifest.artifacts}
    unexpected = sorted(set(remote) - set(local))
    mismatched = sorted(name for name in set(remote) & set(local) if remote[name] != local[name])
    if unexpected or mismatched:
        _fail(
            f"registry state is unsafe: unexpected={unexpected!r}, digest_mismatches={mismatched!r}"
        )
    existing = tuple(sorted(remote))
    missing = tuple(sorted(set(local) - set(remote)))
    if mode == "publish" and existing:
        _fail("normal publication requires an empty registry release")
    if mode == "recover-partial" and (len(existing) != 1 or len(missing) != 1):
        _fail("partial recovery requires exactly one matching existing file")
    if mode == "verify-only" and missing:
        _fail("verification-only requires both matching files to exist")
    upload = missing if mode != "verify-only" else ()
    return RegistryPlan(mode, upload, existing)


@implements("REQ052")
def stage_registry_upload(
    bundle: Path, destination: Path, index_root: str, mode: RegistryMode, tag: str
) -> RegistryPlan:
    """Stage only files that the selected fail-closed registry mode permits."""
    manifest = verify_release_bundle(bundle, tag)
    plan = classify_registry_state(manifest, _read_registry_files(index_root, manifest), mode)
    if destination.exists():
        _fail(f"registry staging target already exists: {destination}")
    destination.mkdir(parents=True)
    for filename in plan.upload:
        shutil.copyfile(bundle / "dist" / filename, destination / filename)
    return plan


@implements("REQ052")
def fetch_registry_provenance(bundle: Path, destination: Path, index_root: str, tag: str) -> None:
    """Download the exact PyPI provenance objects for later cryptographic verification."""
    manifest = verify_release_bundle(bundle, tag)
    remote = _read_registry_files(index_root, manifest)
    classify_registry_state(manifest, remote, "verify-only")
    if destination.exists():
        _fail(f"provenance target already exists: {destination}")
    destination.mkdir(parents=True)
    for artifact in manifest.artifacts:
        url = (
            f"{index_root.rstrip('/')}/integrity/{PROJECT_NAME}/{manifest.version}/"
            f"{artifact.filename}/provenance"
        )
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.pypi.integrity.v1+json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = response.read()
            json.loads(payload)
        except (OSError, json.JSONDecodeError) as error:
            raise ReleaseError(
                f"unable to retrieve provenance for {artifact.filename}: {error}"
            ) from error
        (destination / f"{artifact.filename}.provenance").write_bytes(payload)


@implements("REQ052")
def smoke_install_bundle(bundle: Path, tag: str) -> None:
    """Install and execute each exact distribution in a separate isolated environment."""
    manifest = verify_release_bundle(bundle, tag)
    for artifact in manifest.artifacts:
        path = (bundle / "dist" / artifact.filename).resolve()
        subprocess.run(
            [
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--refresh",
                "--with",
                str(path),
                "python",
                "-c",
                (f"import slygentify; assert slygentify.__version__ == {manifest.version!r}"),
            ],
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "run",
                "--isolated",
                "--no-project",
                "--refresh",
                "--with",
                str(path),
                "slygentify",
                "--help",
            ],
            check=True,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    version = subparsers.add_parser("version")
    version.add_argument("tag")

    source_checks = subparsers.add_parser("verify-source-checks")
    source_checks.add_argument("document", type=Path)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--tag", required=True)
    prepare.add_argument("--dist", type=Path, required=True)
    prepare.add_argument("--bundle", type=Path, required=True)

    verify = subparsers.add_parser("verify-bundle")
    verify.add_argument("--tag", required=True)
    verify.add_argument("--bundle", type=Path, required=True)

    stage = subparsers.add_parser("stage")
    stage.add_argument("--tag", required=True)
    stage.add_argument("--bundle", type=Path, required=True)
    stage.add_argument("--destination", type=Path, required=True)
    stage.add_argument("--index-root", required=True)
    stage.add_argument(
        "--mode", choices=("publish", "recover-partial", "verify-only"), required=True
    )

    provenance = subparsers.add_parser("fetch-provenance")
    provenance.add_argument("--tag", required=True)
    provenance.add_argument("--bundle", type=Path, required=True)
    provenance.add_argument("--destination", type=Path, required=True)
    provenance.add_argument("--index-root", required=True)

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--tag", required=True)
    smoke.add_argument("--bundle", type=Path, required=True)
    return parser


def _mode(value: str) -> RegistryMode:
    if value not in {"publish", "recover-partial", "verify-only"}:
        _fail(f"unsupported registry mode {value!r}")
    return cast(RegistryMode, value)


@implements("REQ052")
def main(argv: list[str] | None = None) -> int:
    """Run one deterministic release-helper operation."""
    arguments = _parser().parse_args(argv)
    if arguments.command == "version":
        print(release_version_from_tag(arguments.tag))
    elif arguments.command == "verify-source-checks":
        try:
            document = json.loads(arguments.document.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ReleaseError(f"unable to load check runs: {error}") from error
        verify_source_checks(document)
    elif arguments.command == "prepare":
        manifest = prepare_release_bundle(arguments.dist, arguments.bundle, arguments.tag)
        print(json.dumps(_manifest_document(manifest), sort_keys=True))
    elif arguments.command == "verify-bundle":
        manifest = verify_release_bundle(arguments.bundle, arguments.tag)
        print(json.dumps(_manifest_document(manifest), sort_keys=True))
    elif arguments.command == "stage":
        plan = stage_registry_upload(
            arguments.bundle,
            arguments.destination,
            arguments.index_root,
            _mode(arguments.mode),
            arguments.tag,
        )
        print(json.dumps({"existing": plan.existing, "mode": plan.mode, "upload": plan.upload}))
    elif arguments.command == "fetch-provenance":
        fetch_registry_provenance(
            arguments.bundle, arguments.destination, arguments.index_root, arguments.tag
        )
    elif arguments.command == "smoke":
        smoke_install_bundle(arguments.bundle, arguments.tag)
    else:  # pragma: no cover - argparse guarantees one known command
        _fail(f"unsupported command {arguments.command!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
