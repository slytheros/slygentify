"""Bounded static vcpkg and Conan declarations; no dependency resolution."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from typing import cast

from slygentify._scan.contracts import (
    DetectionContext,
    DetectionResult,
    DiagnosticCandidate,
    EvidenceCandidate,
    EvidenceKey,
    FindingCandidate,
    RepositoryView,
)
from slygentify._scan.detectors._ci import contains_literal_credential
from slygentify._scan.detectors._support import evidence_key, pointer, quoted
from slygentify._scan.paths import nearest_ancestor
from slygentify.traceability import implements

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"[^#\x00-\x1f\x7f$`{}\\/]+(?:#[0-9]+)?")
_PLATFORM = re.compile(r"[A-Za-z0-9_ !&|(),]+")
_SEMVER_NUMBER = r"(?:0|[1-9][0-9]*)"
_SEMVER_PRERELEASE = r"(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
_OVERRIDE_VERSIONS = {
    "version-semver": re.compile(
        rf"{_SEMVER_NUMBER}\.{_SEMVER_NUMBER}\.{_SEMVER_NUMBER}"
        rf"(?:-{_SEMVER_PRERELEASE}(?:\.{_SEMVER_PRERELEASE})*)?"
        r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?(?:#[0-9]+)?"
    ),
    "version-date": re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:\.[0-9]+)*(?:#[0-9]+)?"),
}
_REFERENCE = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_.+-]*/"
    r"(?:[A-Za-z0-9_][A-Za-z0-9_.+*-]*|\[[A-Za-z0-9_.+*<>=!| &~,^-]+\])"
    r"(?:@[A-Za-z0-9_][A-Za-z0-9_.+-]*/[A-Za-z0-9_][A-Za-z0-9_.+-]*)?"
    r"(?:#[A-Za-z0-9_][A-Za-z0-9_.+-]*)?"
)
_SECTIONS = frozenset({"requires", "tool_requires", "test_requires", "build_requires"})


class _CandidateMemoryBoundary(Exception):
    """The view recorded a resource boundary before allocating another candidate."""


def _document(data: bytes, checkpoint: Callable[[], bool]) -> dict[str, object]:
    """Reject duplicate keys, nonfinite numbers and non-scalar Unicode; cap depth."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise ValueError("nonfinite JSON")

    result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise ValueError("expected object")
    pending: list[tuple[object, int]] = [(result, 0)]
    while pending:
        if checkpoint():
            raise TimeoutError("inspection interrupted")
        value, depth = pending.pop()
        if depth > 32:
            raise ValueError("JSON nesting exceeds 32")
        if isinstance(value, str):
            value.encode("utf-8")
        elif isinstance(value, dict):
            for key, item in value.items():
                pending.extend(((key, depth + 1), (item, depth + 1)))
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite JSON")
    return result


def _literal(value: object, pattern: re.Pattern[str]) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.isprintable()
        and len(value) <= 4096
        and bool(pattern.fullmatch(value))
        and not contains_literal_credential(value)
    )


def _platform(value: object) -> bool:
    """Validate bounded expression syntax without interpreting identifiers or activation."""
    if not _literal(value, _PLATFORM):
        return False
    assert isinstance(value, str)
    depth = 0
    operand = True
    for match in re.finditer(r"[A-Za-z0-9_]+|[!&|(),]", value):
        token = match.group()
        if operand:
            if token in {"!", "not"}:
                continue
            if token == "(":
                depth += 1
                if depth > 32:
                    return False
            elif token in {"&", "|", ",", ")", "and", "or"}:
                return False
            else:
                operand = False
        elif token == ")":
            if depth == 0:
                return False
            depth -= 1
        elif token in {"&", "|", ",", "and"}:
            operand = True
        else:
            return False
    return not operand and depth == 0


@implements("REQ058")
def detect_cpp_dependencies(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    """Attach declarations to established owners without creating component boundaries."""
    evidence: list[EvidenceCandidate] = []
    findings: list[FindingCandidate] = []
    diagnostics: list[DiagnosticCandidate] = []
    seen: dict[tuple[str, str | None, str, str], tuple[str, EvidenceKey]] = {}
    manager = ""
    path = ""
    subject: str | None = None

    def retain(*values: str) -> None:
        # Keep candidate objects, strings, keys, list slots, and conflict indexes
        # charged while normalization also retains the detector result. This is
        # separate from the temporary parser reservation, which can be released.
        if not view.reserve_memory(path, 4096 + 16 * sum(map(len, values))):
            raise _CandidateMemoryBoundary

    def emit(code: str, locator: str, summary: str, *, unknown: bool = False) -> EvidenceKey:
        retain(manager, path, subject or "", code, locator, summary)
        item = EvidenceCandidate(
            manager + "-declaration",
            path,
            locator,
            "A dependency-manager source declaration is present.",
            "non-following metadata inspection"
            if path.endswith("conanfile.py")
            else "bounded static inspection",
            "cpp-dependencies.inspect.v1",
            f"{manager}.{code}:{locator}:{hashlib.sha256(summary.encode()).hexdigest()}",
        )
        evidence.append(item)
        key = evidence_key(item)
        findings.append(
            FindingCandidate(
                manager + "." + code, "unknown" if unknown else "verified", subject, summary, (key,)
            )
        )
        return key

    def issue(
        code: str, problem: str, *, partial: bool = False, keys: tuple[EvidenceKey, ...] = ()
    ) -> None:
        retain(manager, path, subject or "", code, problem)
        diagnostics.append(
            DiagnosticCandidate(
                manager + "." + code,
                path,
                problem=problem,
                effect="Available declarations are retained; dependencies and activation remain unresolved",
                recovery="inspect the source manually or use supported literal declarations",
                partial=partial,
                subject_path=subject,
                evidence_keys=keys,
                disposition="problem" if partial or code == "dependency-conflict" else "limitation",
            )
        )

    def invalid(locator: str) -> None:
        emit(
            "dependency.unresolved",
            locator,
            "A supported declaration is malformed, unsafe, dynamic, or sensitive; its value was withheld.",
            unknown=True,
        )
        issue(
            "invalid-declaration",
            "A supported dependency declaration has invalid fields or unsafe values",
            partial=True,
        )

    def conflict(name: str, scope: str, summary: str, key: EvidenceKey) -> None:
        identity = (manager, path, scope, name)
        previous = seen.get(identity)
        if previous is not None and previous[0] != summary:
            issue(
                "dependency-conflict",
                "Different declarations for the same dependency and source scope coexist; no winner was selected",
                keys=(previous[1], key),
            )
        else:
            seen[identity] = (summary, key)

    def feature_values(values: object, locator: str) -> list[object]:
        assert isinstance(values, list)
        safe: list[object] = []
        for index, value in enumerate(values):
            if view.checkpoint():
                break
            if _literal(value, _NAME):
                safe.append(value)
            elif (
                isinstance(value, dict)
                and _literal(value.get("name"), _NAME)
                and ("platform" not in value or _platform(value["platform"]))
                and not (
                    set(value)
                    - {"name", "platform"}
                    - {key for key in value if key.startswith("$")}
                )
            ):
                safe.append(
                    {
                        key: item
                        for key, item in sorted(value.items())
                        if key in {"name", "platform"}
                    }
                )
            else:
                invalid(locator + pointer(index))
        return safe

    def dependencies(values: object, locator: str, scope: str) -> None:
        if not isinstance(values, list):
            invalid(locator)
            return
        for index, value in enumerate(values):
            if view.checkpoint():
                break
            location = locator + pointer(index)
            declaration = {"name": value} if isinstance(value, str) else value
            if not isinstance(declaration, dict) or not _literal(declaration.get("name"), _NAME):
                invalid(location)
                continue
            safe: dict[str, object] = {"name": declaration["name"]}
            valid = True
            dynamic = False
            for field, item in declaration.items():
                if field == "name":
                    continue
                if field in {"default-features", "host"}:
                    accepted = type(item) is bool
                elif field == "features":
                    accepted = isinstance(item, list)
                    if accepted:
                        item = feature_values(item, location + "/features")
                elif field == "version>=":
                    accepted = _literal(item, _VERSION)
                elif field == "platform":
                    accepted = _platform(item)
                elif field.startswith("$"):
                    continue
                else:
                    emit(
                        "dependency.unresolved",
                        location,
                        "Additional dependency fields are outside supported static inspection.",
                        unknown=True,
                    )
                    issue(
                        "unsupported-declaration", "Additional dependency fields remain unresolved"
                    )
                    continue
                if accepted:
                    safe[field] = item
                elif isinstance(item, str) and any(
                    marker in item for marker in ("${", "{{", "$ENV", "%(")
                ):
                    emit(
                        "dependency.unresolved",
                        location + pointer(field),
                        "A dynamic dependency value was withheld; the declaration remains unresolved.",
                        unknown=True,
                    )
                    issue("dynamic-declaration", "A dependency value requires dynamic evaluation")
                    dynamic = True
                else:
                    valid = False
            if not valid:
                invalid(location)
                continue
            safe = dict(sorted(safe.items()))
            summary = f"Supported vcpkg dependency fields {quoted(safe)} are declared in {scope}; platform and feature activation and resolved versions are unknown."
            key = emit("dependency.declaration", location, summary)
            if not dynamic:
                conflict(
                    str(safe["name"]),
                    scope
                    + ":"
                    + str(safe.get("platform", ""))
                    + ":host="
                    + str(safe.get("host", False))
                    + ":features="
                    + quoted(sorted(cast(list[object], safe.get("features", [])), key=quoted))
                    + ":defaults="
                    + str(safe.get("default-features", True)),
                    quoted(safe.get("version>=", "unconstrained")),
                    key,
                )

    def vcpkg(document: dict[str, object]) -> None:
        if "supports" in document:
            value = document["supports"]
            if _platform(value):
                emit(
                    "dependency.supports",
                    "/supports",
                    f"Manifest supports expression {quoted(value)} is declared; it was not evaluated.",
                )
            else:
                invalid("/supports")
        if "dependencies" in document:
            dependencies(document["dependencies"], "/dependencies", "manifest")
        if "default-features" in document:
            value = document["default-features"]
            if isinstance(value, list):
                value = feature_values(value, "/default-features")
                emit(
                    "dependency.default-features",
                    "/default-features",
                    f"Supported manifest default-feature entries {quoted(value)} are declared; effective activation is unknown.",
                )
            else:
                invalid("/default-features")
        if "features" in document:
            features = document["features"]
            if not isinstance(features, dict):
                invalid("/features")
            else:
                for name, feature in features.items():
                    if view.checkpoint():
                        break
                    # Untrusted keys must not leak into JSON Pointer evidence either.
                    if not _literal(name, _NAME):
                        invalid("/features")
                        continue
                    location = pointer("features", name)
                    if not isinstance(feature, dict):
                        invalid(location)
                        continue
                    description = feature.get("description")
                    if not (
                        isinstance(description, str)
                        or (
                            isinstance(description, list)
                            and all(isinstance(item, str) for item in description)
                        )
                    ):
                        invalid(location + "/description")
                    emit(
                        "dependency.feature",
                        location,
                        f"Named feature {quoted(name)} is declared; effective activation is unknown.",
                    )
                    if "dependencies" in feature:
                        dependencies(
                            feature["dependencies"],
                            location + "/dependencies",
                            "feature "
                            + quoted(name)
                            + " supports="
                            + (
                                quoted(feature["supports"])
                                if _platform(feature.get("supports"))
                                else "unknown"
                            ),
                        )
                    if "supports" in feature:
                        if _platform(feature["supports"]):
                            emit(
                                "dependency.supports",
                                location + "/supports",
                                f"Feature {quoted(name)} supports expression {quoted(feature['supports'])} is declared; it was not evaluated.",
                            )
                        else:
                            invalid(location + "/supports")
        if "overrides" in document:
            overrides = document["overrides"]
            if not isinstance(overrides, list):
                invalid("/overrides")
                return
            for index, override in enumerate(overrides):
                if view.checkpoint():
                    break
                location = pointer("overrides", index)
                if not isinstance(override, dict) or not _literal(override.get("name"), _NAME):
                    invalid(location)
                    continue
                override = {
                    key: item for key, item in sorted(override.items()) if not key.startswith("$")
                }
                versions = [
                    key
                    for key in override
                    if key in {"version", "version-semver", "version-date", "version-string"}
                ]
                if (
                    len(versions) == 1
                    and isinstance(override[versions[0]], str)
                    and any(
                        marker in str(override[versions[0]])
                        for marker in ("${", "{{", "$ENV", "%(")
                    )
                ):
                    emit(
                        "dependency.unresolved",
                        location,
                        "A dynamic version override was withheld; its value remains unresolved.",
                        unknown=True,
                    )
                    issue("dynamic-declaration", "A version override requires dynamic evaluation")
                    continue
                if (
                    len(versions) != 1
                    or not _literal(
                        override[versions[0]], _OVERRIDE_VERSIONS.get(versions[0], _VERSION)
                    )
                    or (
                        "port-version" in override
                        and (
                            type(override["port-version"]) is not int
                            or override["port-version"] < 0
                        )
                    )
                    or set(override) - {"name", "port-version", *versions}
                ):
                    invalid(location)
                    continue
                summary = f"vcpkg version override {quoted(override)} is declared; dependency resolution was not performed."
                key = emit("dependency.override", location, summary)
                conflict(str(override["name"]), "overrides", summary, key)

    def conan(data: bytes) -> None:
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            invalid("file")
            return
        section = ""
        sections: set[str] = set()
        for number, raw in enumerate(text.splitlines(), 1):
            if view.checkpoint():
                break
            value = raw.strip()
            if not value or value.startswith(("#", ";")):
                continue
            locator = f"section:{section}:line:{number}"
            if value.startswith("[") and value.endswith("]"):
                candidate = value[1:-1]
                if len(candidate) > 128 or not re.fullmatch(r"[A-Za-z_]+", candidate):
                    invalid(f"line:{number}")
                    section = ""
                    continue
                if candidate in sections:
                    issue("invalid-declaration", "A Conan text section is repeated", partial=True)
                sections.add(candidate)
                section = candidate
                if section not in _SECTIONS:
                    emit(
                        "dependency.unresolved",
                        f"section:{section}:line:{number}",
                        "A Conan text section is outside supported dependency inspection.",
                        unknown=True,
                    )
                    issue("unsupported-section", "A Conan text section remains unresolved")
                continue
            if section not in _SECTIONS:
                if not section:
                    invalid(locator)
                continue
            if not _literal(value, _REFERENCE):
                emit(
                    "dependency.unresolved",
                    locator,
                    "A Conan requirement is dynamic, malformed, or sensitive; its value was withheld.",
                    unknown=True,
                )
                dynamic = any(marker in value for marker in ("${", "{{", "$ENV", "%("))
                issue(
                    "dynamic-declaration" if dynamic else "invalid-declaration",
                    "A Conan requirement is not a supported literal reference",
                    partial=not dynamic,
                )
                continue
            legacy = " (legacy declaration)" if section == "build_requires" else ""
            summary = f"Conan {section}{legacy} dependency {quoted(value)} is declared; resolution was not performed."
            key = emit("dependency.declaration", locator, summary)
            conflict(value.split("/", 1)[0], section, summary, key)

    for candidate in view.path_candidates():
        if view.checkpoint():
            break
        if candidate.name not in {"vcpkg.json", "conanfile.txt", "conanfile.py"}:
            continue
        path = candidate.path
        manager = "vcpkg" if candidate.name == "vcpkg.json" else "conan"
        subject = nearest_ancestor(candidate.parent, context.component_paths)
        data = b"" if candidate.name == "conanfile.py" else view.read_bytes(path)
        if data is None:
            continue
        try:
            emit(
                "manager.evidence",
                "file",
                f"A {manager} manifest/recipe is present; installation and effective configuration are unknown.",
            )
            if subject is None:
                emit(
                    "manager.unresolved",
                    "file",
                    "No established component owns this manifest; declarations remain repository-scoped.",
                    unknown=True,
                )
                issue(
                    "ownership-unresolved",
                    "The dependency manifest has no established component owner",
                )
            if candidate.name == "conanfile.py":
                emit(
                    "dependency.unresolved",
                    "file",
                    "A Conan Python recipe is present; Python contents were not parsed, imported, or executed and dependencies remain unknown.",
                    unknown=True,
                )
                issue("dynamic-recipe", "Python recipe dependency contents were not inspected")
            else:
                # Reserve before decoding JSON or Conan text. The allowance covers
                # Unicode, JSON containers/scalars, pairs, validation work lists,
                # and the complete Conan line list. Retained candidates are separate.
                parser_memory = 4096 + 256 * len(data)
                if not view.reserve_memory(path, parser_memory):
                    continue
                try:
                    if candidate.name == "conanfile.txt":
                        conan(data)
                        continue
                    try:
                        document = _document(data, view.checkpoint)
                    except TimeoutError:
                        continue
                    except (ValueError, UnicodeError, RecursionError):
                        invalid("file")
                        continue
                    try:
                        vcpkg(document)
                    finally:
                        del document
                finally:
                    view.release_memory(parser_memory)
        except _CandidateMemoryBoundary:
            continue
    return DetectionResult(
        evidence=tuple(evidence), findings=tuple(findings), diagnostics=tuple(diagnostics)
    )
