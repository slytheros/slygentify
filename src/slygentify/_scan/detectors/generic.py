"""Private bounded generic repository evidence detection."""

from __future__ import annotations

import fnmatch
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import cast

from slygentify._scan.contracts import (
    ComponentCandidate as _ComponentCandidate,
)
from slygentify._scan.contracts import (
    DetectionContext,
    DetectionResult,
    RepositoryView,
)
from slygentify._scan.contracts import (
    DiagnosticCandidate as _DiagnosticCandidate,
)
from slygentify._scan.contracts import (
    EvidenceCandidate as _EvidenceCandidate,
)
from slygentify._scan.contracts import (
    FindingCandidate as _FindingCandidate,
)
from slygentify._scan.contracts import (
    RelationshipCandidate as _RelationshipCandidate,
)
from slygentify._scan.detectors._support import decode as _decode
from slygentify._scan.detectors._support import evidence_key as _evidence_key
from slygentify._scan.paths import parent as _parent
from slygentify._scan.paths import safe_member as _safe_member
from slygentify.traceability import implements

_RELEVANT_NAMES = frozenset({"Cargo.toml", "CMakeLists.txt", "go.mod", "go.work", "pom.xml"})
_ENGINEERING_SUFFIXES = (".kicad_pcb", ".kicad_pro", ".kicad_sch")


def _generic_relevant(name: str) -> bool:
    return name in _RELEVANT_NAMES or name.endswith(_ENGINEERING_SUFFIXES)


def _evidence(path: str, locator: str, observation: str, key: str) -> _EvidenceCandidate:
    return _EvidenceCandidate(
        "manifest", path, locator, observation, "strict bounded parse", "generic.manifest", key
    )


def _cargo(
    path: str, data: bytes, available: frozenset[str]
) -> tuple[list[_EvidenceCandidate], list[_ComponentCandidate], list[_DiagnosticCandidate]]:
    evidence: list[_EvidenceCandidate] = []
    components: list[_ComponentCandidate] = []
    diagnostics: list[_DiagnosticCandidate] = []
    try:
        document = tomllib.loads(_decode(data))
    except (UnicodeError, tomllib.TOMLDecodeError):
        return (
            evidence,
            components,
            [
                _DiagnosticCandidate(
                    "inspection.invalid-manifest",
                    path,
                    "Cargo manifest is invalid.",
                    True,
                    disposition="problem",
                )
            ],
        )
    root = _parent(path)
    keys: list[tuple[str, str, str | None, str]] = []
    if isinstance(document.get("package"), dict):
        item = _evidence(
            path, "package", "Cargo manifest declares a package boundary.", "cargo-package"
        )
        evidence.append(item)
        keys.append(_evidence_key(item))
    workspace = document.get("workspace")
    if isinstance(workspace, dict):
        item = _evidence(
            path, "workspace", "Cargo manifest declares a workspace boundary.", "cargo-workspace"
        )
        evidence.append(item)
        keys.append(_evidence_key(item))
        members = workspace.get("members", [])
        if not isinstance(members, list) or any(not isinstance(member, str) for member in members):
            diagnostics.append(
                _DiagnosticCandidate(
                    "inspection.invalid-workspace",
                    path,
                    "Cargo workspace members are invalid.",
                    True,
                    disposition="problem",
                )
            )
        else:
            cargo_paths = {
                item[: -len("/Cargo.toml")] if item != "Cargo.toml" else "."
                for item in available
                if item.endswith("Cargo.toml")
            }
            for member in members:
                resolved = _safe_member(root, member)
                if resolved is None:
                    diagnostics.append(
                        _DiagnosticCandidate(
                            "inspection.invalid-workspace-member",
                            path,
                            "Cargo workspace member escapes the repository.",
                            True,
                            disposition="problem",
                        )
                    )
                elif not any(fnmatch.fnmatchcase(candidate, resolved) for candidate in cargo_paths):
                    diagnostics.append(
                        _DiagnosticCandidate(
                            "inspection.missing-workspace-member",
                            path,
                            "Cargo workspace member has no safe manifest.",
                            True,
                            disposition="problem",
                        )
                    )
    if keys:
        components.append(
            _ComponentCandidate(
                root, "workspace" if isinstance(workspace, dict) else "package", tuple(keys)
            )
        )
    return evidence, components, diagnostics


def _go(
    path: str, data: bytes, available: frozenset[str]
) -> tuple[list[_EvidenceCandidate], list[_ComponentCandidate], list[_DiagnosticCandidate]]:
    evidence: list[_EvidenceCandidate] = []
    components: list[_ComponentCandidate] = []
    diagnostics: list[_DiagnosticCandidate] = []
    try:
        text = _decode(data)
    except UnicodeError:
        return (
            evidence,
            components,
            [
                _DiagnosticCandidate(
                    "inspection.invalid-manifest",
                    path,
                    "Go manifest is not UTF-8.",
                    True,
                    disposition="problem",
                )
            ],
        )
    root = _parent(path)
    if PurePosixPath(path).name == "go.mod":
        if any(line.strip().startswith("module ") for line in text.splitlines()):
            item = _evidence(path, "module", "Go manifest declares a module boundary.", "go-module")
            evidence.append(item)
            components.append(_ComponentCandidate(root, "package", (_evidence_key(item),)))
        else:
            diagnostics.append(
                _DiagnosticCandidate(
                    "inspection.invalid-manifest",
                    path,
                    "Go module declaration is missing.",
                    True,
                    disposition="problem",
                )
            )
        return evidence, components, diagnostics

    item = _evidence(path, "use", "Go work file declares a workspace boundary.", "go-workspace")
    members: list[str] = []
    in_use = False
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if line == "use (":
            in_use = True
        elif in_use and line == ")":
            in_use = False
        elif line.startswith("use "):
            members.append(line[4:].strip())
        elif in_use and line:
            members.append(line)
    if not members:
        return (
            evidence,
            components,
            [
                _DiagnosticCandidate(
                    "inspection.invalid-manifest",
                    path,
                    "Go workspace has no use entries.",
                    True,
                    disposition="problem",
                )
            ],
        )
    evidence.append(item)
    components.append(_ComponentCandidate(root, "workspace", (_evidence_key(item),)))
    for member in members:
        resolved = _safe_member(root, member)
        expected = "go.mod" if resolved == "." else f"{resolved}/go.mod" if resolved else ""
        if resolved is None:
            diagnostics.append(
                _DiagnosticCandidate(
                    "inspection.invalid-workspace-member",
                    path,
                    "Go workspace member escapes the repository.",
                    True,
                    disposition="problem",
                )
            )
        elif expected not in available:
            diagnostics.append(
                _DiagnosticCandidate(
                    "inspection.missing-workspace-member",
                    path,
                    "Go workspace member has no safe manifest.",
                    True,
                    disposition="problem",
                )
            )
    return evidence, components, diagnostics


def _maven(
    path: str, data: bytes, available: frozenset[str]
) -> tuple[list[_EvidenceCandidate], list[_ComponentCandidate], list[_DiagnosticCandidate]]:
    evidence: list[_EvidenceCandidate] = []
    components: list[_ComponentCandidate] = []
    diagnostics: list[_DiagnosticCandidate] = []
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        return (
            evidence,
            components,
            [
                _DiagnosticCandidate(
                    "inspection.unsafe-xml",
                    path,
                    "Maven manifest contains a prohibited declaration.",
                    True,
                    disposition="problem",
                )
            ],
        )
    try:
        root_element = ET.fromstring(_decode(data))
    except (UnicodeError, ET.ParseError):
        return (
            evidence,
            components,
            [
                _DiagnosticCandidate(
                    "inspection.invalid-manifest",
                    path,
                    "Maven manifest is invalid.",
                    True,
                    disposition="problem",
                )
            ],
        )
    if root_element.tag.rsplit("}", 1)[-1] != "project":
        return (
            evidence,
            components,
            [
                _DiagnosticCandidate(
                    "inspection.invalid-manifest",
                    path,
                    "Maven manifest root is not project.",
                    True,
                    disposition="problem",
                )
            ],
        )
    root = _parent(path)
    modules = [
        element.text.strip()
        for element in root_element.iter()
        if element.tag.rsplit("}", 1)[-1] == "module" and element.text
    ]
    item = _evidence(
        path, "project", "Maven manifest declares a project boundary.", "maven-project"
    )
    evidence.append(item)
    components.append(
        _ComponentCandidate(root, "workspace" if modules else "package", (_evidence_key(item),))
    )
    for member in modules:
        resolved = _safe_member(root, member)
        expected = "pom.xml" if resolved == "." else f"{resolved}/pom.xml" if resolved else ""
        if resolved is None:
            diagnostics.append(
                _DiagnosticCandidate(
                    "inspection.invalid-workspace-member",
                    path,
                    "Maven module escapes the repository.",
                    True,
                    disposition="problem",
                )
            )
        elif expected not in available:
            diagnostics.append(
                _DiagnosticCandidate(
                    "inspection.missing-workspace-member",
                    path,
                    "Maven module has no safe manifest.",
                    True,
                    disposition="problem",
                )
            )
    return evidence, components, diagnostics


def _cmake(
    path: str, data: bytes, available: frozenset[str]
) -> tuple[list[_EvidenceCandidate], list[_ComponentCandidate], list[_DiagnosticCandidate]]:
    del available
    try:
        text = _decode(data)
    except UnicodeError:
        return (
            [],
            [],
            [
                _DiagnosticCandidate(
                    "inspection.invalid-manifest",
                    path,
                    "CMake project evidence is not UTF-8. Next: encode the file as UTF-8.",
                    True,
                    disposition="problem",
                )
            ],
        )
    project = re.search(r"(?im)^\s*project\s*\(\s*[^)$\s][^)]*\)", text)
    component = re.search(r"(?im)^\s*idf_component_register\s*\(", text)
    if project is None and component is None:
        return (
            [],
            [],
            [
                _DiagnosticCandidate(
                    "composition.ambiguous-boundary",
                    path,
                    "CMakeLists.txt has no supported static project or ESP-IDF component marker. "
                    "Slygentify did not establish a component boundary from this file. "
                    "Next: add a supported marker, or declare "
                    f'[[scan.components]] with path = "{_parent(path)}" in the root '
                    "slygentify.toml.",
                    False,
                    disposition="limitation",
                )
            ],
        )
    locator = "project" if project is not None else "idf_component_register"
    observation = (
        "A static CMake project declaration establishes an unsupported project boundary."
        if project is not None
        else "A static ESP-IDF component declaration establishes an unsupported boundary."
    )
    item = _EvidenceCandidate(
        "unsupported-manifest",
        path,
        locator,
        observation,
        "static bounded text inspection",
        "generic.cmake",
        f"cmake:{_parent(path)}:{locator}",
    )
    return [item], [_ComponentCandidate(_parent(path), "project", (_evidence_key(item),))], []


def _unique_json_object(data: bytes) -> dict[str, object]:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    value = json.loads(_decode(data), object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("document is not an object")
    return cast(dict[str, object], value)


def _kicad_project(
    path: str, data: bytes, available: frozenset[str]
) -> tuple[list[_EvidenceCandidate], list[_ComponentCandidate], list[_DiagnosticCandidate]]:
    del available
    try:
        _unique_json_object(data)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return (
            [],
            [],
            [
                _DiagnosticCandidate(
                    "inspection.invalid-manifest",
                    path,
                    "KiCad project evidence is not a unique-key UTF-8 JSON object. "
                    "Next: correct the project file or declare the intended boundary explicitly.",
                    True,
                    disposition="problem",
                )
            ],
        )
    item = _EvidenceCandidate(
        "unsupported-manifest",
        path,
        None,
        "A valid KiCad project file establishes an unsupported engineering-project boundary.",
        "strict bounded JSON parse",
        "generic.kicad",
        f"kicad-project:{_parent(path)}:{PurePosixPath(path).name}",
    )
    return (
        [item],
        [_ComponentCandidate(_parent(path), "engineering-project", (_evidence_key(item),))],
        [],
    )


def _kicad_artifact(path: str) -> _EvidenceCandidate:
    return _EvidenceCandidate(
        "engineering-artifact",
        path,
        None,
        "A KiCad engineering artifact is present; it does not establish a boundary alone.",
        "file identity inspection",
        "generic.kicad",
        f"kicad-artifact:{path}",
    )


def _generic_workspace_relationships(
    path: str,
    data: bytes,
    available: frozenset[str],
    evidence: list[_EvidenceCandidate],
) -> tuple[_RelationshipCandidate, ...]:
    root = _parent(path)
    workspace_keys = tuple(
        _evidence_key(item) for item in evidence if item.locator in {"workspace", "use", "project"}
    )
    if not workspace_keys:
        return ()
    targets: set[str] = set()
    name = PurePosixPath(path).name
    try:
        if name == "Cargo.toml":
            document = tomllib.loads(_decode(data))
            workspace = document.get("workspace")
            members = workspace.get("members", []) if isinstance(workspace, dict) else []
            cargo_roots = {
                item[: -len("/Cargo.toml")] if item != "Cargo.toml" else "."
                for item in available
                if item.endswith("Cargo.toml")
            }
            for member in members if isinstance(members, list) else []:
                if not isinstance(member, str):
                    continue
                resolved = _safe_member(root, member)
                if resolved is not None:
                    targets.update(
                        candidate
                        for candidate in cargo_roots
                        if candidate != root and fnmatch.fnmatchcase(candidate, resolved)
                    )
        elif name == "go.work":
            go_members: list[str] = []
            in_use = False
            for raw_line in _decode(data).splitlines():
                line = raw_line.split("//", 1)[0].strip()
                if line == "use (":
                    in_use = True
                elif in_use and line == ")":
                    in_use = False
                elif line.startswith("use "):
                    go_members.append(line[4:].strip())
                elif in_use and line:
                    go_members.append(line)
            for member in go_members:
                resolved = _safe_member(root, member)
                expected = "go.mod" if resolved == "." else f"{resolved}/go.mod"
                if resolved is not None and expected in available and resolved != root:
                    targets.add(resolved)
        elif name == "pom.xml":
            document_root = ET.fromstring(_decode(data))
            for element in document_root.iter():
                if element.tag.rsplit("}", 1)[-1] != "module" or not element.text:
                    continue
                resolved = _safe_member(root, element.text.strip())
                expected = "pom.xml" if resolved == "." else f"{resolved}/pom.xml"
                if resolved is not None and expected in available and resolved != root:
                    targets.add(resolved)
    except (UnicodeError, tomllib.TOMLDecodeError, ET.ParseError):
        return ()
    return tuple(
        _RelationshipCandidate("workspace-member", root, target, "verified", workspace_keys)
        for target in sorted(targets)
    )


@implements("REQ016", "REQ031", "REQ032", "REQ041")
def detect_generic(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    """Detect generic repository evidence through the bounded view."""

    del context
    evidence: list[_EvidenceCandidate] = []
    components: list[_ComponentCandidate] = []
    findings: list[_FindingCandidate] = []
    diagnostics: list[_DiagnosticCandidate] = []
    relationships: list[_RelationshipCandidate] = []
    engineering_artifacts: list[_EvidenceCandidate] = []
    kicad_project_roots: set[str] = set()
    available = frozenset(view.paths())
    for path in view.paths():
        if view.checkpoint():
            break
        name = PurePosixPath(path).name
        if not _generic_relevant(name):
            continue
        if name.endswith((".kicad_pcb", ".kicad_sch")):
            item = _kicad_artifact(path)
            evidence.append(item)
            engineering_artifacts.append(item)
            continue
        parser = (
            _cargo
            if name == "Cargo.toml"
            else _go
            if name in {"go.mod", "go.work"}
            else _maven
            if name == "pom.xml"
            else _cmake
            if name == "CMakeLists.txt"
            else _kicad_project
        )
        data = view.read_bytes(path)
        if data is None:
            continue
        parsed_evidence, parsed_components, parsed_diagnostics = parser(path, data, available)
        evidence.extend(parsed_evidence)
        components.extend(parsed_components)
        diagnostics.extend(parsed_diagnostics)
        if name.endswith(".kicad_pro") and parsed_components:
            kicad_project_roots.add(_parent(path))
        relationships.extend(
            _generic_workspace_relationships(path, data, available, parsed_evidence)
        )
    artifact_keys: dict[str, list[tuple[str, str, str | None, str]]] = {}
    for item in engineering_artifacts:
        if view.checkpoint():
            break
        artifact_keys.setdefault(_parent(item.location), []).append(_evidence_key(item))
        if _parent(item.location) not in kicad_project_roots:
            findings.append(
                _FindingCandidate(
                    "generic.engineering-boundary.unknown",
                    "unknown",
                    None,
                    f"Engineering artifact {item.location} has no verified component boundary.",
                    (_evidence_key(item),),
                )
            )
            diagnostics.append(
                _DiagnosticCandidate(
                    "composition.ambiguous-boundary",
                    item.location,
                    "A KiCad artifact has no valid sibling .kicad_pro boundary. "
                    "Slygentify retained the artifact evidence without assigning a component. "
                    "Next: add or repair the project file, or declare [[scan.components]] with "
                    f'path = "{_parent(item.location)}" in the root slygentify.toml.',
                    False,
                    evidence_keys=(_evidence_key(item),),
                    disposition="limitation",
                )
            )
    composed_components: list[_ComponentCandidate] = []
    for component_candidate in components:
        if view.checkpoint():
            break
        composed_components.append(
            _ComponentCandidate(
                component_candidate.path,
                component_candidate.kind,
                (
                    *component_candidate.evidence_keys,
                    *artifact_keys.get(component_candidate.path, ()),
                ),
                component_candidate.ecosystem,
            )
            if component_candidate.path in kicad_project_roots
            else component_candidate
        )
    return DetectionResult(
        evidence=tuple(evidence),
        components=tuple(composed_components),
        findings=tuple(findings),
        diagnostics=tuple(diagnostics),
        relationships=tuple(relationships),
    )
