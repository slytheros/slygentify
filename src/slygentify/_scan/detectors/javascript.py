"""Private bounded JavaScript and TypeScript repository inspection."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import cast

import yaml  # type: ignore[import-untyped]

from slygentify._scan.contracts import (
    ComponentCandidate,
    DetectionContext,
    DetectionResult,
    DiagnosticCandidate,
    EvidenceCandidate,
    EvidenceKey,
    FindingCandidate,
    RelationshipCandidate,
    RepositoryView,
)
from slygentify._scan.detectors._support import StaticStructureError as _StaticStructureError
from slygentify._scan.detectors._support import evidence_key as _key
from slygentify._scan.detectors._support import pointer as _pointer
from slygentify._scan.detectors._support import quoted as _quoted
from slygentify._scan.detectors._support import strict_yaml_document as _yaml_document
from slygentify._scan.paths import descendant_paths as _descendant_paths
from slygentify._scan.paths import nearest_ancestor as _nearest_ancestor
from slygentify._scan.paths import safe_member as _safe_member
from slygentify.models import DiagnosticDisposition
from slygentify.traceability import implements

_RULE_ID = "javascript.inspect.v1"
_DEPENDENCY_FIELDS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)
_TOOLS = {
    "typescript": "typescript",
    "eslint": "eslint",
    "prettier": "prettier",
    "jest": "jest",
    "vitest": "vitest",
    "@playwright/test": "playwright",
}
_FRAMEWORKS = frozenset({"express", "fastify", "vue"})
_LOCK_NAMES = {
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
}
_UNSUPPORTED_NAMES = frozenset(
    {
        "bun.lock",
        "bun.lockb",
        "deno.json",
        "deno.jsonc",
        "lerna.json",
        "nx.json",
        "rush.json",
        "turbo.json",
    }
)
_PACKAGE_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"[A-Za-z0-9_]*(?:token|password|passwd|secret|api[_-]?key)[A-Za-z0-9_]*"
    r"\s*=\s*(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s;&|]+)"
)
_CREDENTIAL_URL = re.compile(r"(?i)https?://[^/\s:@]+:(?P<value>[^/\s@]+)@")
_VERSION = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+][0-9A-Za-z.-]+)?$")
_COMPARATOR = re.compile(r"^(>=|<=|>|<|=|\^|~)?v?(\d+)(?:\.(\d+|x|X|\*))?(?:\.(\d+|x|X|\*))?$")


class _DuplicateJsonKey(ValueError):
    pass


def _evidence(
    source_kind: str,
    path: str,
    locator: str | None,
    observation: str,
    semantic_key: str,
) -> EvidenceCandidate:
    return EvidenceCandidate(
        source_kind,
        path,
        locator,
        observation,
        "strict bounded static inspection",
        _RULE_ID,
        semantic_key,
    )


def _json_document(data: bytes) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in values:
            if name in result:
                raise _DuplicateJsonKey(name)
            result[name] = value
        return result

    return json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=pairs)


def _contains_literal_credential(command: str) -> bool:
    for match in _CREDENTIAL_ASSIGNMENT.finditer(command):
        value = match.group("value").strip("\"'")
        if value and not any(token in value for token in ("${", "{{", "%", "$")):
            return True
    return any(
        match.group("value") and "$" not in match.group("value")
        for match in _CREDENTIAL_URL.finditer(command)
    )


def _resolve_typescript_reference(
    view: RepositoryView,
    paths: frozenset[str],
    root: str,
    target: str,
    *,
    project_reference: bool,
) -> tuple[bool, str]:
    """Resolve one static TypeScript reference without expanding detector capability."""

    target_with_suffix = target if PurePosixPath(target).suffix else f"{target}.json"
    candidates = [_safe_member(root, target_with_suffix)]
    if project_reference:
        candidates.append(_safe_member(root, f"{target}/tsconfig.json"))
    safe_candidates = tuple(dict.fromkeys(candidate for candidate in candidates if candidate))
    if not safe_candidates:
        return False, "unsafe-or-escaping-path"

    for candidate in safe_candidates:
        if candidate not in paths:
            continue
        data = view.read_bytes(candidate)
        if data is None:
            return False, "unavailable-or-nonregular-target"
        try:
            document = _json_document(data)
            if not isinstance(document, dict):
                raise ValueError("TypeScript configuration is not an object")
        except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
            return False, "non-strict-or-malformed-target"
        return True, ""
    return False, "unavailable-or-nonregular-target"


def _safe_workspace_pattern(value: str) -> str | None:
    pattern = value[1:] if value.startswith("!") else value
    pattern = pattern.replace("\\", "/").strip()
    if not pattern or pattern.startswith("/") or ":" in pattern:
        return None
    pattern = pattern.rstrip("/")
    if any(part in {"", ".", ".."} for part in PurePosixPath(pattern).parts):
        return None
    if any(token in pattern for token in ("{", "}", "(", ")")):
        return None
    return ("!" if value.startswith("!") else "") + pattern


def _workspace_match(path: str, pattern: str) -> bool:
    path_parts = PurePosixPath(path).parts
    pattern_parts = PurePosixPath(pattern).parts

    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        if pattern_parts[pattern_index] == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], pattern_parts[pattern_index])
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)


def _package_name(value: str) -> str | None:
    normalized = value.strip().lower()
    return normalized if _PACKAGE_NAME.fullmatch(normalized) else None


def _manager_selection(value: object) -> tuple[str, str | None] | None:
    if not isinstance(value, str):
        return None
    name, separator, version = value.strip().partition("@")
    normalized = name.lower()
    if normalized not in {"npm", "pnpm", "yarn"}:
        return None
    return normalized, version if separator and version else None


def _version(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(value.strip())
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _comparator_contains(version: tuple[int, int, int], token: str) -> bool | None:
    match = _COMPARATOR.fullmatch(token)
    if match is None:
        return None
    operator, major, minor, patch = match.groups()
    if minor in {"x", "X", "*"} or patch in {"x", "X", "*"}:
        expected = (int(major), int(minor) if minor and minor.isdigit() else 0, 0)
        precision = 1 if minor in {None, "x", "X", "*"} else 2
        return version[:precision] == expected[:precision]
    expected = (int(major), int(minor or 0), int(patch or 0))
    if operator == ">=":
        return version >= expected
    if operator == "<=":
        return version <= expected
    if operator == ">":
        return version > expected
    if operator == "<":
        return version < expected
    if operator == "^":
        upper = (expected[0] + 1, 0, 0) if expected[0] else (0, expected[1] + 1, 0)
        return expected <= version < upper
    if operator == "~":
        return expected <= version < (expected[0], expected[1] + 1, 0)
    if operator == "=" or patch is not None:
        return version == expected
    if minor is not None:
        return version[:2] == expected[:2]
    return version[0] == expected[0]


def _range_contains(value: str, constraint: str) -> bool | None:
    version = _version(value)
    if version is None:
        return None
    outcomes: list[bool] = []
    for alternative in constraint.split("||"):
        tokens = alternative.strip().replace(",", " ").split()
        if not tokens:
            return None
        checks = [_comparator_contains(version, token) for token in tokens]
        if any(check is None for check in checks):
            return None
        outcomes.append(all(cast(bool, check) for check in checks))
    return any(outcomes)


def _expression_only(value: str) -> bool:
    return re.fullmatch(r"\s*\$\{\{.*\}\}\s*", value, flags=re.DOTALL) is not None


def _matrix_reference(value: str) -> tuple[str, ...] | None:
    match = re.fullmatch(r"\s*\$\{\{\s*matrix\.([A-Za-z0-9_.-]+)\s*\}\}\s*", value)
    return tuple(match.group(1).split(".")) if match else None


def _matrix_values(
    matrix: object, reference: tuple[str, ...], locator: tuple[object, ...]
) -> tuple[tuple[str, str], ...]:
    if not isinstance(matrix, dict) or not reference:
        return ()
    direct = matrix.get(reference[0])
    results: list[tuple[str, str]] = []
    if len(reference) == 1 and isinstance(direct, list):
        for index, value in enumerate(direct):
            if isinstance(value, str) and "${{" not in value:
                results.append((value, _pointer(*locator, reference[0], index)))
    includes = matrix.get("include")
    if isinstance(includes, list):
        for index, item in enumerate(includes):
            current: object = item
            for name in reference:
                current = current.get(name) if isinstance(current, dict) else None
            if isinstance(current, str) and "${{" not in current:
                results.append((current, _pointer(*locator, "include", index, *reference)))
    return tuple(results)


@implements("REQ025", "REQ026", "REQ027", "REQ028", "REQ029", "REQ041")
def detect_javascript(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    """Detect JavaScript and TypeScript evidence through the bounded view."""

    generic_component_paths = context.generic_component_paths
    """Inspect only the approved static JavaScript and TypeScript evidence matrix."""

    evidence: list[EvidenceCandidate] = []
    components: list[ComponentCandidate] = []
    findings: list[FindingCandidate] = []
    diagnostics: list[DiagnosticCandidate] = []
    relationships: list[RelationshipCandidate] = []
    paths = view.paths()
    path_set = frozenset(paths)
    path_candidates = view.path_candidates()
    packages: dict[str, tuple[str, dict[str, object]]] = {}
    component_keys: dict[str, list[EvidenceKey]] = {}
    component_kinds: dict[str, str] = {}
    manager_families: dict[str, dict[str, list[EvidenceKey]]] = {}
    runtimes: dict[str, list[tuple[str, str, str, str, EvidenceKey]]] = {}
    workspace_patterns: dict[str, list[tuple[str, str, EvidenceKey]]] = {}
    workspace_memberships: dict[str, set[str]] = {}
    tool_locations: dict[str, dict[str, list[EvidenceKey]]] = {}
    unresolved_typescript_references: dict[tuple[str, str], list[EvidenceKey]] = {}

    def add_evidence(item: EvidenceCandidate) -> EvidenceKey:
        evidence.append(item)
        return _key(item)

    def add_finding(
        code: str,
        classification: str,
        root: str | None,
        summary: str,
        keys: tuple[EvidenceKey, ...],
    ) -> None:
        findings.append(FindingCandidate(code, classification, root, summary, keys))

    def add_diagnostic(
        code: str,
        location: str,
        message: str | None,
        *,
        disposition: DiagnosticDisposition,
        partial: bool,
        root: str | None = None,
        keys: tuple[EvidenceKey, ...] = (),
        problem: str | None = None,
        effect: str | None = None,
        recovery: str | None = None,
    ) -> None:
        if problem is None:
            assert message is not None
            candidate = DiagnosticCandidate(
                code,
                location,
                message,
                partial,
                root,
                keys,
                disposition=disposition,
            )
        else:
            assert message is None and effect is not None
            candidate = DiagnosticCandidate(
                code,
                location,
                partial=partial,
                subject_path=root,
                evidence_keys=keys,
                problem=problem,
                effect=effect,
                recovery=recovery,
                disposition=disposition,
            )
        diagnostics.append(candidate)

    def record_tool(root: str, name: str, path: str, locator: str | None, strength: str) -> None:
        item = _evidence(
            "tool-configuration",
            path,
            locator,
            f"{name} {strength} evidence is present.",
            f"tool:{root}:{name}:{path}:{locator or ''}:{strength}",
        )
        key = add_evidence(item)
        if strength != "direct-dependency":
            tool_locations.setdefault(root, {}).setdefault(name, []).append(key)
        add_finding(
            "javascript.tool.evidence",
            "verified",
            root,
            f"{name} has {strength} evidence at {path}.",
            (key,),
        )

    def record_manager(
        root: str,
        family: str,
        path: str,
        locator: str | None,
        observation: str,
    ) -> EvidenceKey:
        key = add_evidence(
            _evidence(
                "manager",
                path,
                locator,
                observation,
                f"manager:{root}:{family}:{path}:{locator or ''}",
            )
        )
        manager_families.setdefault(root, {}).setdefault(family, []).append(key)
        add_finding(
            "javascript.manager.evidence",
            "verified",
            root,
            f"{family} manager evidence is present for {_quoted(root)}.",
            (key,),
        )
        return key

    def record_runtime(root: str, value: str, path: str, locator: str, role: str) -> EvidenceKey:
        key = add_evidence(
            _evidence(
                "runtime",
                path,
                locator,
                "A Node.js or npm runtime declaration is present.",
                f"runtime:{root}:{role}:{path}:{locator}:{value}",
            )
        )
        runtimes.setdefault(root, []).append((value, path, locator, role, key))
        add_finding(
            "javascript.runtime.declaration",
            "verified",
            root,
            f"{role.replace('-', ' ').capitalize()} {_quoted(value)} is declared.",
            (key,),
        )
        return key

    for candidate in path_candidates:
        if view.checkpoint():
            break
        path = candidate.path
        if candidate.name != "package.json":
            continue
        data = view.read_bytes(path)
        if data is None:
            continue
        root = candidate.parent
        try:
            document = _json_document(data)
            if not isinstance(document, dict):
                raise ValueError("package document is not an object")
        except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
            key = add_evidence(
                _evidence(
                    "manifest",
                    path,
                    None,
                    "A package.json file is present but its content could not be verified.",
                    f"invalid-manifest:{root}",
                )
            )
            add_finding(
                "javascript.component.unknown",
                "unknown",
                None,
                f"A JavaScript package candidate at {root} could not be verified.",
                (key,),
            )
            add_diagnostic(
                "javascript.invalid-manifest",
                path,
                None,
                disposition="problem",
                partial=True,
                keys=(key,),
                problem=f"JavaScript manifest {path} is not a unique-key UTF-8 JSON object",
                effect=f"The package boundary and declarations from {path} were omitted",
                recovery=(
                    "correct its JSON syntax, encoding, duplicate keys, or top-level shape, or "
                    "intentionally exclude the file if it is outside the intended scan scope"
                ),
            )
            continue
        package = cast(dict[str, object], document)
        packages[root] = (path, package)
        manifest_key = add_evidence(
            _evidence(
                "manifest",
                path,
                None,
                "A valid package.json declares a JavaScript or TypeScript package boundary.",
                f"manifest:{root}",
            )
        )
        component_keys[root] = [manifest_key]
        component_kinds[root] = "package"
        add_finding(
            "javascript.component.verified",
            "verified",
            root,
            f"A JavaScript or TypeScript package is declared at {_quoted(root)}.",
            (manifest_key,),
        )

    for root, (path, package) in sorted(packages.items()):
        if view.checkpoint():
            break
        for field_name, expected in (("name", str), ("private", bool), ("type", str)):
            if field_name not in package:
                continue
            value = package[field_name]
            locator = _pointer(field_name)
            if not isinstance(value, expected):
                add_diagnostic(
                    "javascript.invalid-metadata",
                    path,
                    f"package.json field {locator} has an unsupported type. "
                    "Next: use the documented package.json scalar type.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
                continue
            key = add_evidence(
                _evidence(
                    "package-metadata",
                    path,
                    locator,
                    f"JavaScript package field {field_name} is declared.",
                    f"metadata:{root}:{field_name}:{_quoted(value)}",
                )
            )
            add_finding(
                "javascript.package.metadata",
                "verified",
                root,
                f"Package field {field_name} is declared as {_quoted(value)}.",
                (key,),
            )

        for field_name in _DEPENDENCY_FIELDS:
            raw_dependencies = package.get(field_name)
            if raw_dependencies is None:
                continue
            if not isinstance(raw_dependencies, dict):
                add_diagnostic(
                    "javascript.invalid-dependencies",
                    path,
                    f"package.json field {_pointer(field_name)} is not an object. "
                    "Next: declare package names as object keys with string specifications.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
                continue
            for raw_name, specification in sorted(raw_dependencies.items()):
                locator = _pointer(field_name, raw_name)
                name = _package_name(raw_name) if isinstance(raw_name, str) else None
                if name is None or not isinstance(specification, str):
                    add_diagnostic(
                        "javascript.invalid-dependency",
                        path,
                        f"Dependency at {path} [{locator}] is not a supported npm declaration. "
                        "Next: use a valid npm package name and string specification.",
                        disposition="problem",
                        partial=True,
                        root=root,
                    )
                    continue
                key = add_evidence(
                    _evidence(
                        "dependency",
                        path,
                        locator,
                        f"Direct JavaScript dependency {name} is declared in {field_name}.",
                        f"dependency:{root}:{field_name}:{name}",
                    )
                )
                add_finding(
                    "javascript.dependency.declaration",
                    "verified",
                    root,
                    f"Direct dependency {name} is declared in {field_name}.",
                    (key,),
                )
                if name in _TOOLS:
                    record_tool(root, _TOOLS[name], path, locator, "direct-dependency")
                if name in _FRAMEWORKS:
                    add_finding(
                        "javascript.framework.declaration",
                        "verified",
                        root,
                        f"The component directly declares {name}.",
                        (key,),
                    )

        scripts = package.get("scripts")
        if scripts is not None:
            if not isinstance(scripts, dict):
                add_diagnostic(
                    "javascript.invalid-scripts",
                    path,
                    "package.json scripts is not an object. Next: use string-valued script entries.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
            else:
                for name, command in sorted(scripts.items()):
                    locator = _pointer("scripts", name)
                    if not isinstance(name, str) or not isinstance(command, str):
                        add_diagnostic(
                            "javascript.invalid-script",
                            path,
                            f"Script at {path} [{locator}] is not a string declaration.",
                            disposition="problem",
                            partial=True,
                            root=root,
                        )
                        continue
                    key = add_evidence(
                        _evidence(
                            "declared-command",
                            path,
                            locator,
                            "A package script command is declared.",
                            f"script:{root}:{name}:{hashlib.sha256(command.encode()).hexdigest()}",
                        )
                    )
                    if _contains_literal_credential(command):
                        add_finding(
                            "javascript.script.declaration",
                            "unknown",
                            root,
                            f"Script {name} is declared, but its command text was withheld.",
                            (key,),
                        )
                        add_diagnostic(
                            "javascript.sensitive-command-redacted",
                            path,
                            f"A script at {path} [{locator}] contains credential-shaped literal "
                            "text, so its value was withheld. Next: review whether the text is "
                            "intentional test data; if sensitive, replace it with an authorized "
                            "secret reference.",
                            disposition="notice",
                            partial=False,
                            root=root,
                            keys=(key,),
                        )
                    else:
                        add_finding(
                            "javascript.script.declaration",
                            "verified",
                            root,
                            f"Package script {name} declares command {_quoted(command)}.",
                            (key,),
                        )

        bins = package.get("bin")
        bin_values: list[tuple[str, str, str]] = []
        if isinstance(bins, str):
            bin_values.append((cast(str, package.get("name", "package")), bins, _pointer("bin")))
        elif isinstance(bins, dict):
            for name, target in sorted(bins.items()):
                if isinstance(name, str) and isinstance(target, str):
                    bin_values.append((name, target, _pointer("bin", name)))
                else:
                    add_diagnostic(
                        "javascript.invalid-bin",
                        path,
                        "A package bin entry is not a string mapping. "
                        "Next: use a string or string-valued object.",
                        disposition="problem",
                        partial=True,
                        root=root,
                    )
        elif bins is not None:
            add_diagnostic(
                "javascript.invalid-bin",
                path,
                "package.json bin has an unsupported type. Next: use a string or object.",
                disposition="problem",
                partial=True,
                root=root,
            )
        for name, target, locator in bin_values:
            key = add_evidence(
                _evidence(
                    "entry-point",
                    path,
                    locator,
                    f"Package executable entry point {name} is declared.",
                    f"bin:{root}:{name}:{target}",
                )
            )
            if _safe_member(root, target) is None:
                add_finding(
                    "javascript.bin.declaration",
                    "unknown",
                    root,
                    f"Package executable {name} has an unsafe or unsupported target.",
                    (key,),
                )
                add_diagnostic(
                    "javascript.unsafe-bin-target",
                    path,
                    f"Package bin target at {path} [{locator}] is not a safe in-root relative "
                    "path, so its value was withheld. Next: use a package-relative file path or "
                    "inspect the declaration manually.",
                    disposition="problem",
                    partial=False,
                    root=root,
                    keys=(key,),
                )
            else:
                add_finding(
                    "javascript.bin.declaration",
                    "verified",
                    root,
                    f"Package executable {name} targets {_quoted(target)}.",
                    (key,),
                )

        engines = package.get("engines")
        if engines is not None and not isinstance(engines, dict):
            add_diagnostic(
                "javascript.invalid-runtime",
                path,
                "package.json engines is not an object. Next: use string-valued engine ranges.",
                disposition="problem",
                partial=True,
                root=root,
            )
        elif isinstance(engines, dict):
            for name in ("node", "npm"):
                value = engines.get(name)
                if value is None:
                    continue
                if not isinstance(value, str):
                    add_diagnostic(
                        "javascript.invalid-runtime",
                        path,
                        f"Engine declaration {_pointer('engines', name)} is not a string.",
                        disposition="problem",
                        partial=True,
                        root=root,
                    )
                    continue
                record_runtime(root, value, path, _pointer("engines", name), f"{name}-constraint")

        selection = _manager_selection(package.get("packageManager"))
        if package.get("packageManager") is not None:
            if selection is None:
                add_diagnostic(
                    "javascript.invalid-manager-selection",
                    path,
                    "packageManager does not identify npm, pnpm, or Yarn. "
                    "Next: use a supported Corepack manager declaration.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
            else:
                record_manager(
                    root,
                    selection[0],
                    path,
                    _pointer("packageManager"),
                    "A Corepack packageManager selection is declared.",
                )
        dev_engines = package.get("devEngines")
        dev_manager = dev_engines.get("packageManager") if isinstance(dev_engines, dict) else None
        manager_selections = dev_manager if isinstance(dev_manager, list) else [dev_manager]
        for index, value in enumerate(manager_selections):
            if value is None:
                continue
            manager_name = value.get("name") if isinstance(value, dict) else value
            version_value = value.get("version") if isinstance(value, dict) else None
            selected = _manager_selection(
                f"{manager_name}@{version_value}" if version_value else manager_name
            )
            locator = (
                _pointer("devEngines", "packageManager", index)
                if isinstance(dev_manager, list)
                else _pointer("devEngines", "packageManager")
            )
            if selected is None:
                add_diagnostic(
                    "javascript.invalid-manager-selection",
                    path,
                    f"Manager selection at {path} [{locator}] is unsupported.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
            else:
                record_manager(
                    root,
                    selected[0],
                    path,
                    locator,
                    "A devEngines package-manager selection is declared.",
                )

        raw_workspaces = package.get("workspaces")
        if isinstance(raw_workspaces, dict):
            raw_workspaces = raw_workspaces.get("packages")
        if raw_workspaces is not None:
            if not isinstance(raw_workspaces, list) or not all(
                isinstance(value, str) for value in raw_workspaces
            ):
                add_diagnostic(
                    "javascript.invalid-workspace",
                    path,
                    "package.json workspaces must be a list of literal package patterns.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
            else:
                workspace_key = add_evidence(
                    _evidence(
                        "workspace",
                        path,
                        _pointer("workspaces"),
                        "A package workspace declaration is present.",
                        f"workspace:{root}:package-json",
                    )
                )
                component_keys[root].append(workspace_key)
                component_kinds[root] = "workspace"
                add_finding(
                    "javascript.workspace.declaration",
                    "verified",
                    root,
                    f"A package workspace is declared at {_quoted(root)}.",
                    (workspace_key,),
                )
                for index, value in enumerate(cast(list[str], raw_workspaces)):
                    workspace_patterns.setdefault(root, []).append(
                        (value, _pointer("workspaces", index), workspace_key)
                    )

        for field_name, tool_name in (
            ("eslintConfig", "eslint"),
            ("prettier", "prettier"),
            ("jest", "jest"),
        ):
            if field_name in package:
                record_tool(root, tool_name, path, _pointer(field_name), "package-field")

    for candidate in path_candidates:
        if view.checkpoint():
            break
        path = candidate.path
        name = candidate.name
        root = candidate.parent
        if root not in packages:
            continue
        if name in _LOCK_NAMES:
            record_manager(
                root,
                _LOCK_NAMES[name],
                path,
                None,
                f"A {name} lock declaration is present.",
            )
        elif name == ".yarnrc.yml":
            data = view.read_bytes(path)
            if data is None:
                continue
            try:
                _yaml_document(data)
            except (UnicodeError, yaml.YAMLError, _StaticStructureError):
                add_diagnostic(
                    "javascript.invalid-manager-configuration",
                    path,
                    "Yarn configuration could not be parsed safely. "
                    "Next: correct its UTF-8 YAML syntax or unsupported structure.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
                continue
            record_manager(root, "yarn", path, None, "A Yarn configuration file is present.")
        elif name == "pnpm-workspace.yaml":
            data = view.read_bytes(path)
            if data is None:
                continue
            try:
                document = _yaml_document(data)
                if not isinstance(document, dict):
                    raise _StaticStructureError("workspace document is not a mapping")
                raw_patterns = document.get("packages")
                if not isinstance(raw_patterns, list) or not all(
                    isinstance(value, str) for value in raw_patterns
                ):
                    raise _StaticStructureError("packages must be a list of strings")
            except (UnicodeError, yaml.YAMLError, _StaticStructureError):
                add_diagnostic(
                    "javascript.invalid-workspace",
                    path,
                    "pnpm workspace configuration is not supported static YAML. "
                    "Next: use a UTF-8 YAML packages list without aliases or custom tags.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
                continue
            workspace_key = record_manager(
                root,
                "pnpm",
                path,
                _pointer("packages"),
                "A pnpm workspace declaration is present.",
            )
            component_keys[root].append(workspace_key)
            component_kinds[root] = "workspace"
            add_finding(
                "javascript.workspace.declaration",
                "verified",
                root,
                f"A pnpm workspace is declared at {_quoted(root)}.",
                (workspace_key,),
            )
            for index, value in enumerate(cast(list[str], raw_patterns)):
                workspace_patterns.setdefault(root, []).append(
                    (value, _pointer("packages", index), workspace_key)
                )
        elif name in {".nvmrc", ".node-version"}:
            data = view.read_bytes(path)
            if data is None:
                continue
            try:
                value = data.decode("utf-8", errors="strict").strip()
            except UnicodeError:
                value = ""
            if not value or "\n" in value or "\r" in value:
                add_diagnostic(
                    "javascript.invalid-runtime",
                    path,
                    f"Node selection file {path} does not contain one UTF-8 value. "
                    "Next: record one literal Node version or alias.",
                    disposition="problem",
                    partial=True,
                    root=root,
                )
            else:
                record_runtime(root, value, path, "line:1", "node-selection")
        elif name in _UNSUPPORTED_NAMES:
            key = add_evidence(
                _evidence(
                    "unsupported-tooling",
                    path,
                    None,
                    f"Unsupported JavaScript tooling file {name} is present.",
                    f"unsupported:{root}:{name}",
                )
            )
            add_finding(
                "javascript.unsupported-tooling.unknown",
                "unknown",
                root,
                f"{name} is outside the public 1.0 JavaScript inspection matrix.",
                (key,),
            )
            add_diagnostic(
                "javascript.unsupported-tooling",
                path,
                f"{name} was not interpreted. Next: inspect it manually when its semantics are needed.",
                disposition="limitation",
                partial=False,
                root=root,
                keys=(key,),
            )

    for root in sorted(packages):
        if view.checkpoint():
            break
        for candidate in view.direct_children(root):
            if view.checkpoint():
                break
            path = candidate.path
            name = candidate.name
            config_tool_name: str | None = None
            if (
                name == ".eslintrc"
                or name.startswith(".eslintrc.")
                or name.startswith("eslint.config.")
            ):
                config_tool_name = "eslint"
            elif name.startswith(".prettierrc") or name.startswith("prettier.config."):
                config_tool_name = "prettier"
            elif name.startswith("jest.config."):
                config_tool_name = "jest"
            elif name.startswith("vitest.config."):
                config_tool_name = "vitest"
            elif name.startswith("playwright.config."):
                config_tool_name = "playwright"
            if config_tool_name is not None:
                record_tool(root, config_tool_name, path, None, "configuration-location")

            if not (
                name == "tsconfig.json" or (name.startswith("tsconfig.") and name.endswith(".json"))
            ):
                continue
            record_tool(root, "typescript", path, None, "configuration-location")
            data = view.read_bytes(path)
            if data is None:
                continue
            try:
                document = _json_document(data)
                if not isinstance(document, dict):
                    raise ValueError("TypeScript configuration is not an object")
            except (UnicodeError, json.JSONDecodeError, _DuplicateJsonKey, ValueError):
                key = add_evidence(
                    _evidence(
                        "tool-configuration",
                        path,
                        None,
                        "TypeScript configuration content could not be inspected as strict JSON.",
                        f"typescript-content-unknown:{path}",
                    )
                )
                add_finding(
                    "javascript.typescript.configuration-content",
                    "unknown",
                    root,
                    "TypeScript configuration content remains unknown; its location is verified.",
                    (key,),
                )
                add_diagnostic(
                    "javascript.typescript-content-unknown",
                    path,
                    f"TypeScript configuration {path} is not strict unique-key JSON. "
                    "It may use JSON-with-comments or be malformed, so it was not expanded. "
                    "Next: inspect it manually when extends or project references are needed.",
                    disposition="limitation",
                    partial=True,
                    root=root,
                    keys=(key,),
                )
                continue
            typed_document = cast(dict[str, object], document)
            references: list[tuple[str, str, bool]] = []
            extends = typed_document.get("extends")
            if isinstance(extends, str):
                references.append((extends, _pointer("extends"), False))
            project_references = typed_document.get("references")
            if isinstance(project_references, list):
                for index, reference in enumerate(project_references):
                    target = reference.get("path") if isinstance(reference, dict) else None
                    if isinstance(target, str):
                        references.append((target, _pointer("references", index, "path"), True))
            for target, locator, project_reference in references:
                source_key = add_evidence(
                    _evidence(
                        "typescript-reference",
                        path,
                        locator,
                        "A TypeScript configuration reference is declared.",
                        f"typescript-reference:{path}:{locator}:{target}",
                    )
                )
                reference_resolved, cause = _resolve_typescript_reference(
                    view,
                    path_set,
                    root,
                    target,
                    project_reference=project_reference,
                )
                if reference_resolved:
                    add_finding(
                        "javascript.typescript.reference",
                        "verified",
                        root,
                        f"TypeScript configuration declares safe local reference "
                        f"{_quoted(target)}.",
                        (source_key,),
                    )
                else:
                    add_finding(
                        "javascript.typescript.reference",
                        "unknown",
                        root,
                        "A TypeScript reference could not be resolved safely within the repository.",
                        (source_key,),
                    )
                    unresolved_typescript_references.setdefault((root, cause), []).append(
                        source_key
                    )

    for (diagnostic_root, cause), unresolved_keys in sorted(
        unresolved_typescript_references.items()
    ):
        cause_message = {
            "unsafe-or-escaping-path": "unsafe or escapes the repository root",
            "unavailable-or-nonregular-target": "is unavailable as a safe regular file",
            "non-strict-or-malformed-target": "is not a strict unique-key JSON object",
        }[cause]
        count = len(unresolved_keys)
        add_diagnostic(
            "javascript.unresolved-typescript-reference",
            diagnostic_root,
            f"{count} TypeScript reference{'s' if count != 1 else ''} in component "
            f"{_quoted(diagnostic_root)} {cause_message}. Next: verify the targets manually or use "
            "strict safe in-root JSON configuration files.",
            disposition="problem",
            partial=False,
            root=diagnostic_root,
            keys=tuple(sorted(unresolved_keys)),
        )

    valid_roots = frozenset(packages)
    sorted_roots = tuple(sorted(valid_roots))
    for workspace_root, declarations in sorted(workspace_patterns.items()):
        if view.checkpoint():
            break
        included: set[str] = set()
        excluded: set[str] = set()
        for raw_pattern, locator, declaration_key in declarations:
            if view.checkpoint():
                break
            safe_pattern = _safe_workspace_pattern(raw_pattern)
            if safe_pattern is None:
                add_diagnostic(
                    "javascript.invalid-workspace-pattern",
                    declaration_key[1],
                    f"Workspace pattern at {declaration_key[1]} [{locator}] is unsafe or uses "
                    "unsupported dynamic syntax. Next: use an in-root literal glob pattern.",
                    disposition="problem",
                    partial=True,
                    root=workspace_root,
                    keys=(declaration_key,),
                )
                continue
            negative = safe_pattern.startswith("!")
            pattern = safe_pattern[1:] if negative else safe_pattern
            matches: set[str] = set()
            for package_root in _descendant_paths(workspace_root, sorted_roots):
                if view.checkpoint():
                    break
                if package_root == workspace_root:
                    continue
                if workspace_root == ".":
                    relative = package_root
                elif package_root.startswith(f"{workspace_root}/"):
                    relative = package_root[len(workspace_root) + 1 :]
                else:
                    continue
                if _workspace_match(relative, pattern):
                    matches.add(package_root)
            if negative:
                excluded.update(matches)
            elif not matches:
                add_diagnostic(
                    "javascript.missing-workspace-member",
                    declaration_key[1],
                    f"Workspace pattern at {declaration_key[1]} [{locator}] matched no valid "
                    "package.json member. Next: correct the pattern or add the missing manifest.",
                    disposition="problem",
                    partial=True,
                    root=workspace_root,
                    keys=(declaration_key,),
                )
            else:
                included.update(matches)
        for member in sorted(included - excluded):
            declaration_keys = tuple(dict.fromkeys(item[2] for item in declarations))
            component_keys[member].extend(declaration_keys)
            workspace_memberships.setdefault(member, set()).add(workspace_root)
            relationships.append(
                RelationshipCandidate(
                    "workspace-member",
                    workspace_root,
                    member,
                    "verified",
                    declaration_keys,
                )
            )
            add_finding(
                "javascript.workspace.member",
                "verified",
                member,
                f"Package {_quoted(member)} is a declared member of workspace {_quoted(workspace_root)}.",
                declaration_keys,
            )

    for member, roots in sorted(workspace_memberships.items()):
        if len(roots) > 1:
            keys = tuple(
                key for root in sorted(roots) for _, _, key in workspace_patterns.get(root, [])
            )
            add_diagnostic(
                "javascript.overlapping-workspace-membership",
                member,
                f"Package {_quoted(member)} matches multiple workspace roots: "
                f"{', '.join(_quoted(root) for root in sorted(roots))}. No owner was selected. "
                "Next: add exclusions or narrow the workspace patterns.",
                disposition="problem",
                partial=False,
                root=member,
                keys=tuple(dict.fromkeys(keys)),
            )

    component_roots = frozenset(root for root in packages if root not in generic_component_paths)

    def component_for(directory: str) -> str | None:
        return _nearest_ancestor(directory, component_roots)

    def workflow_directory(value: object) -> str | None:
        if not isinstance(value, str) or "${{" in value or "\\" in value:
            return None
        stripped = value.strip()
        if stripped in {"", ".", "./"}:
            return "."
        return _safe_member(".", stripped[2:] if stripped.startswith("./") else stripped)

    def owned_directory(
        directory: object, ownership: dict[str, bool | None], checkout_seen: bool
    ) -> str | None:
        normalized = workflow_directory(directory)
        if normalized is None:
            return None
        if not checkout_seen:
            return normalized
        candidates = [
            path
            for path in ownership
            if path == "." or normalized == path or normalized.startswith(f"{path}/")
        ]
        if not candidates:
            return None
        owner = max(candidates, key=len)
        if ownership[owner] is not True:
            return None
        if owner == ".":
            return normalized
        remainder = normalized[len(owner) :].lstrip("/")
        return remainder or "."

    def emit_command(path: str, locator: str, command: str, subject: str) -> None:
        key = add_evidence(
            _evidence(
                "ci-command",
                path,
                locator,
                "A literal CI command is declared.",
                f"ci-command:{path}:{locator}:{hashlib.sha256(command.encode()).hexdigest()}",
            )
        )
        if _contains_literal_credential(command):
            add_finding(
                "javascript.ci.command",
                "unknown",
                subject,
                "A CI command is declared, but its text was withheld because it resembles credential material.",
                (key,),
            )
            add_diagnostic(
                "javascript.sensitive-command-redacted",
                path,
                f"A CI command at {path} [{locator}] contains credential-shaped literal text, "
                "so its value was withheld. Next: review whether it is intentional test data; "
                "if sensitive, replace it with an authorized CI secret reference.",
                disposition="notice",
                partial=False,
                root=subject,
                keys=(key,),
            )
        else:
            add_finding(
                "javascript.ci.command",
                "verified",
                subject,
                f"CI declares command {_quoted(command)}.",
                (key,),
            )

    def emit_dynamic_command(path: str, locator: str, subject: str) -> None:
        key = add_evidence(
            _evidence(
                "ci-command",
                path,
                locator,
                "A dynamic CI command expression is declared.",
                f"dynamic-ci-command:{path}:{locator}",
            )
        )
        add_finding(
            "javascript.ci.command.dynamic",
            "unknown",
            subject,
            "A dynamic CI command expression was not evaluated.",
            (key,),
        )
        add_diagnostic(
            "javascript.dynamic-ci-command-unknown",
            path,
            f"A CI run value at {path} [{locator}] consists only of a workflow expression. "
            "Next: inspect the expression inputs when the concrete command is needed.",
            disposition="limitation",
            partial=False,
            root=subject,
            keys=(key,),
        )

    for path in paths:
        if view.checkpoint():
            break
        if not (
            (path.startswith(".gitea/workflows/") or path.startswith(".github/workflows/"))
            and path.endswith((".yml", ".yaml"))
        ):
            continue
        data = view.read_bytes(path)
        if data is None:
            continue
        try:
            workflow = _yaml_document(data)
            if not isinstance(workflow, dict):
                raise _StaticStructureError("workflow is not a mapping")
        except (UnicodeError, yaml.YAMLError, _StaticStructureError):
            add_diagnostic(
                "javascript.invalid-ci-workflow",
                path,
                "CI workflow content is not supported static YAML. "
                "Next: correct its syntax or unsupported structure.",
                disposition="problem",
                partial=True,
            )
            continue
        jobs = workflow.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_name, job_value in jobs.items():
            if not isinstance(job_name, str) or not isinstance(job_value, dict):
                continue
            job = cast(dict[str, object], job_value)
            strategy = job.get("strategy")
            matrix = strategy.get("matrix") if isinstance(strategy, dict) else {}
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            defaults = job.get("defaults")
            run_defaults = defaults.get("run") if isinstance(defaults, dict) else {}
            job_directory = (
                run_defaults.get("working-directory", ".")
                if isinstance(run_defaults, dict)
                else "."
            )
            ownership: dict[str, bool | None] = {}
            checkout_seen = False
            for index, step_value in enumerate(steps):
                if not isinstance(step_value, dict):
                    continue
                step = cast(dict[str, object], step_value)
                uses = step.get("uses")
                with_values = step.get("with")
                if isinstance(uses, str) and uses.startswith("actions/checkout@"):
                    checkout_seen = True
                    checkout_path = (
                        with_values.get("path", ".") if isinstance(with_values, dict) else "."
                    )
                    normalized_checkout = workflow_directory(checkout_path)
                    repository = (
                        with_values.get("repository") if isinstance(with_values, dict) else None
                    )
                    if normalized_checkout is not None:
                        ownership[normalized_checkout] = (
                            True
                            if repository is None
                            else False
                            if isinstance(repository, str) and "${{" not in repository
                            else None
                        )
                directory = step.get("working-directory", job_directory)
                repository_directory = owned_directory(directory, ownership, checkout_seen)
                subject = (
                    component_for(repository_directory)
                    if repository_directory is not None
                    else None
                )
                command = step.get("run")
                if isinstance(command, str) and subject is not None:
                    locator = _pointer("jobs", job_name, "steps", index, "run")
                    if _expression_only(command):
                        emit_dynamic_command(path, locator, subject)
                    else:
                        emit_command(path, locator, command, subject)
                version_value = (
                    with_values.get("node-version") if isinstance(with_values, dict) else None
                )
                if (
                    isinstance(uses, str)
                    and uses.startswith("actions/setup-node@")
                    and isinstance(version_value, str)
                    and subject is not None
                ):
                    reference = _matrix_reference(version_value)
                    resolved = (
                        _matrix_values(
                            matrix,
                            reference,
                            ("jobs", job_name, "strategy", "matrix"),
                        )
                        if reference is not None
                        else ()
                    )
                    if resolved:
                        for value, locator in resolved:
                            record_runtime(subject, value, path, locator, "node-selection")
                    elif "${{" not in version_value:
                        record_runtime(
                            subject,
                            version_value,
                            path,
                            _pointer("jobs", job_name, "steps", index, "with", "node-version"),
                            "node-selection",
                        )
                    else:
                        locator = _pointer("jobs", job_name, "steps", index, "with", "node-version")
                        key = add_evidence(
                            _evidence(
                                "runtime",
                                path,
                                locator,
                                "A dynamic setup-node runtime expression is declared.",
                                f"dynamic-ci-runtime:{path}:{locator}",
                            )
                        )
                        add_finding(
                            "javascript.runtime.dynamic",
                            "unknown",
                            subject,
                            "A dynamic setup-node runtime expression was not evaluated.",
                            (key,),
                        )
                        add_diagnostic(
                            "javascript.dynamic-ci-runtime-unknown",
                            path,
                            f"A dynamic or unsupported setup-node expression at {path} [{locator}] "
                            "was not evaluated. Next: use a literal version, a direct static matrix "
                            "property, or inspect the expression manually.",
                            disposition="limitation",
                            partial=False,
                            root=subject,
                            keys=(key,),
                        )

    if ".gitlab-ci.yml" in path_set and component_for(".") is not None:
        visited: set[str] = set()

        def visit_gitlab(path: str, depth: int) -> None:
            if path in visited:
                add_diagnostic(
                    "javascript.ci-include-cycle",
                    path,
                    "A GitLab local include cycle was not expanded. "
                    "Next: remove the cycle or inspect the repeated include manually.",
                    disposition="problem",
                    partial=False,
                    root=".",
                )
                return
            if depth > 16:
                add_diagnostic(
                    "javascript.ci-include-depth",
                    path,
                    "GitLab local include nesting exceeded the supported bound. "
                    "Next: reduce the include depth or inspect the remaining files manually.",
                    disposition="limitation",
                    partial=True,
                    root=".",
                )
                return
            visited.add(path)
            data = view.read_bytes(path)
            if data is None:
                return
            try:
                document = _yaml_document(data)
                if not isinstance(document, dict):
                    raise _StaticStructureError("GitLab document is not a mapping")
            except (UnicodeError, yaml.YAMLError, _StaticStructureError):
                add_diagnostic(
                    "javascript.invalid-ci-workflow",
                    path,
                    "GitLab CI content is not supported static YAML. "
                    "Next: correct its syntax or unsupported structure.",
                    disposition="problem",
                    partial=True,
                    root=".",
                )
                return
            includes = document.get("include", [])
            include_values = includes if isinstance(includes, list) else [includes]
            for index, include in enumerate(include_values):
                local = (
                    include
                    if isinstance(include, str)
                    else include.get("local")
                    if isinstance(include, dict)
                    else None
                )
                if isinstance(local, str) and "*" not in local and "$" not in local:
                    normalized = local.lstrip("/")
                    if _safe_member(".", normalized) is None or normalized not in path_set:
                        add_diagnostic(
                            "javascript.invalid-ci-include",
                            path,
                            f"GitLab local include at {path} [{_pointer('include', index)}] is "
                            "missing or escapes the repository. Next: use an existing in-root file.",
                            disposition="problem",
                            partial=True,
                            root=".",
                        )
                    else:
                        visit_gitlab(normalized, depth + 1)
                elif include:
                    key = add_evidence(
                        _evidence(
                            "ci-include",
                            path,
                            _pointer("include", index),
                            "An external or dynamic GitLab include remains unexpanded.",
                            f"external-include:{path}:{index}",
                        )
                    )
                    add_finding(
                        "javascript.ci.external-include",
                        "unknown",
                        ".",
                        "An external or dynamic GitLab include was not expanded.",
                        (key,),
                    )
                    add_diagnostic(
                        "javascript.external-ci-include",
                        path,
                        "External, component, project, template, and dynamic includes are not "
                        "fetched. Next: inspect the external source separately if needed.",
                        disposition="limitation",
                        partial=False,
                        root=".",
                        keys=(key,),
                    )
            reserved = {
                "include",
                "stages",
                "variables",
                "workflow",
                "default",
                "image",
                "services",
                "before_script",
                "after_script",
                "cache",
                "pages",
                "interruptible",
            }

            def commands(value: object, locator: tuple[object, ...]) -> None:
                values = value if isinstance(value, list) else [value]
                for index, command in enumerate(values):
                    if isinstance(command, str):
                        emit_command(path, _pointer(*locator, index), command, ".")
                    elif isinstance(command, dict) and isinstance(command.get("run"), str):
                        emit_command(
                            path,
                            _pointer(*locator, index, "run"),
                            cast(str, command["run"]),
                            ".",
                        )

            for field_name in ("before_script", "after_script"):
                if field_name in document:
                    commands(document[field_name], (field_name,))
            for job_name, job in document.items():
                if job_name in reserved or job_name.startswith(".") or not isinstance(job, dict):
                    continue
                for field_name in ("before_script", "script", "after_script", "run"):
                    if field_name in job:
                        commands(job[field_name], (job_name, field_name))

        visit_gitlab(".gitlab-ci.yml", 0)

    for root, families in sorted(manager_families.items()):
        if len(families) > 1:
            conflict_keys = tuple(key for _, keys in sorted(families.items()) for key in keys)
            details = "; ".join(
                f"{family}: {', '.join(sorted(key[1] for key in keys))}"
                for family, keys in sorted(families.items())
            )
            add_diagnostic(
                "javascript.manager-conflict",
                root,
                f"Competing JavaScript package-manager families are present for component "
                f"{_quoted(root)}: {details}. No manager preference was selected. Next: keep the "
                "families only if coexistence is intentional; otherwise remove or regenerate stale "
                "locks and align explicit manager selections.",
                disposition="problem",
                partial=False,
                root=root,
                keys=conflict_keys,
            )
        npm_paths = {key[1] for key in families.get("npm", [])}
        if {
            f"{'' if root == '.' else f'{root}/'}package-lock.json",
            f"{'' if root == '.' else f'{root}/'}npm-shrinkwrap.json",
        } <= npm_paths:
            keys = tuple(
                key
                for key in families["npm"]
                if PurePosixPath(key[1]).name in {"package-lock.json", "npm-shrinkwrap.json"}
            )
            add_finding(
                "javascript.npm-lock-precedence",
                "verified",
                root,
                "Both npm root lock files are present; npm documents npm-shrinkwrap.json as "
                "taking precedence for publication and installation semantics.",
                keys,
            )
            add_diagnostic(
                "javascript.npm-lock-coexistence",
                root,
                "Both package-lock.json and npm-shrinkwrap.json are present. Their documented "
                "precedence was retained without selecting npm as the preferred workflow. "
                "Next: confirm that keeping both files is intentional.",
                disposition="notice",
                partial=False,
                root=root,
                keys=keys,
            )

    for root, runtime_declarations in sorted(runtimes.items()):
        runtime_constraints = [
            item for item in runtime_declarations if item[3] == "node-constraint"
        ]
        runtime_selections = [item for item in runtime_declarations if item[3] == "node-selection"]
        for runtime_selection in runtime_selections:
            for runtime_constraint in runtime_constraints:
                compatibility = _range_contains(runtime_selection[0], runtime_constraint[0])
                runtime_keys = (runtime_selection[4], runtime_constraint[4])
                if compatibility is False:
                    add_diagnostic(
                        "javascript.runtime-conflict",
                        root,
                        f"Node runtime declarations conflict for component {_quoted(root)}: "
                        f"{runtime_selection[1]} [{runtime_selection[2]}] selects "
                        f"{_quoted(runtime_selection[0])}, while {runtime_constraint[1]} "
                        f"[{runtime_constraint[2]}] declares supported range "
                        f"{_quoted(runtime_constraint[0])}. The exact selection is outside the supported "
                        "range. Next: select a compatible Node version or intentionally update the "
                        "engines.node range.",
                        disposition="problem",
                        partial=False,
                        root=root,
                        keys=runtime_keys,
                    )
                elif compatibility is None:
                    add_diagnostic(
                        "javascript.runtime-compatibility-unknown",
                        root,
                        f"Compatibility between Node selection {_quoted(runtime_selection[0])} at "
                        f"{runtime_selection[1]} [{runtime_selection[2]}] and range "
                        f"{_quoted(runtime_constraint[0])} at {runtime_constraint[1]} "
                        f"[{runtime_constraint[2]}] could not be determined without broader "
                        "package-manager semantics. Both declarations were retained. Next: compare "
                        "them with the project's selected package manager.",
                        disposition="limitation",
                        partial=False,
                        root=root,
                        keys=runtime_keys,
                    )

    for root, by_tool in sorted(tool_locations.items()):
        for tool_name, tool_keys in sorted(by_tool.items()):
            locations = {key[1] for key in tool_keys}
            if len(locations) > 1:
                add_diagnostic(
                    "javascript.tool-configuration-conflict",
                    root,
                    f"Multiple {tool_name} evidence locations are present for component "
                    f"{_quoted(root)}; none was selected. Next: confirm their scopes or remove "
                    "obsolete configuration.",
                    disposition="problem",
                    partial=False,
                    root=root,
                    keys=tuple(tool_keys),
                )

    for root in sorted(packages):
        if view.checkpoint():
            break
        components.append(
            ComponentCandidate(
                root,
                component_kinds[root],
                tuple(dict.fromkeys(component_keys[root])),
                "javascript",
            )
        )

    return DetectionResult(
        evidence=tuple(evidence),
        components=tuple(components),
        findings=tuple(findings),
        diagnostics=tuple(diagnostics),
        relationships=tuple(relationships),
    )
