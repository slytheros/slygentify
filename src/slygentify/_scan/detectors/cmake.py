"""Static CMake declarations through the bounded repository capability."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath

from slygentify._scan.contracts import (
    ComponentCandidate,
    DetectionContext,
    DetectionResult,
    DiagnosticCandidate,
    EvidenceCandidate,
    FindingCandidate,
    RelationshipCandidate,
    RepositoryView,
)
from slygentify._scan.detectors._ci import (
    contains_literal_credential,
    expression_only,
    inspect_commands,
)
from slygentify._scan.detectors._cmake_syntax import CMakeSyntaxError, Command, commands
from slygentify._scan.detectors._support import evidence_key, pointer, quoted
from slygentify._scan.paths import nearest_ancestor, parent, safe_member
from slygentify.traceability import implements

_PROJECT_OPTIONS = {"VERSION", "COMPAT_VERSION", "SPDX_LICENSE", "DESCRIPTION", "HOMEPAGE_URL"}
_STANDARD = {
    "CMAKE_C_STANDARD": "C",
    "CMAKE_CXX_STANDARD": "CXX",
    "C_STANDARD": "C",
    "CXX_STANDARD": "CXX",
}
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}(?:\.\.\.[0-9]+(?:\.[0-9]+){0,3})?")
_CONTROL = frozenset({"if", "foreach", "while", "function", "macro"})


def _project_languages(call: Command) -> tuple[str, ...] | None:
    """Only explicit language arguments, never CMake's implicit defaults."""
    args = [arg.value for arg in call.arguments]
    args = args[1:]
    languages: list[str] = []
    options: set[str] = set()
    index = 0
    while index < len(args):
        option = args[index]
        if option in _PROJECT_OPTIONS or option == "LANGUAGES":
            if option in options:
                return None
            options.add(option)
            if option == "LANGUAGES":
                index += 1
                continue
            if index + 1 == len(args) or args[index + 1] in _PROJECT_OPTIONS | {"LANGUAGES"}:
                return None
            if option in {"VERSION", "COMPAT_VERSION"} and not re.fullmatch(
                r"[0-9]+(?:\.[0-9]+){0,3}", args[index + 1]
            ):
                return None
            index += 2
        else:
            languages.append(args[index])
            index += 1
    if "COMPAT_VERSION" in options and "VERSION" not in options:
        return None
    return tuple(languages)


def _unique_object(data: bytes) -> dict[str, object]:
    def unicode_scalars(value: object) -> None:
        if isinstance(value, str):
            value.encode("utf-8")
        elif isinstance(value, dict):
            for key, item in value.items():
                unicode_scalars(key)
                unicode_scalars(item)
        elif isinstance(value, list):
            for item in value:
                unicode_scalars(item)

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    if not isinstance(result, dict):
        raise ValueError("not an object")
    unicode_scalars(result)
    return result


def _subdirectory_arguments(args: list[str]) -> bool:
    """Accept only source, optional binary directory, and the two literal flags."""
    flags = {"EXCLUDE_FROM_ALL", "SYSTEM"}
    remaining = args[1:]
    if remaining and remaining[0] not in flags:
        remaining = remaining[1:]
    return all(arg in flags for arg in remaining) and len(remaining) == len(set(remaining))


@implements("REQ057")
def detect_cmake(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    """Retain declarations, not effective configuration, installed tools or build success."""
    evidence: list[EvidenceCandidate] = []
    findings: list[FindingCandidate] = []
    diagnostics: list[DiagnosticCandidate] = []
    components: list[ComponentCandidate] = []
    relationships: list[RelationshipCandidate] = []
    parsed: dict[str, tuple[Command, ...]] = {}
    projects: set[str] = set()
    qualified: dict[str, list[tuple[str, str, str | None, str]]] = {}
    manifest_paths: list[str] = []
    auxiliary_paths: list[str] = []
    for candidate in view.path_candidates():
        if view.checkpoint():
            break
        if candidate.name == "CMakeLists.txt":
            manifest_paths.append(candidate.path)
        elif candidate.name in {"CMakePresets.json", ".clang-format", ".clang-tidy"}:
            auxiliary_paths.append(candidate.path)

    def emit(
        code: str,
        path: str,
        locator: str,
        subject: str | None,
        summary: str,
        *,
        unknown: bool = False,
        scope: tuple[str, ...] = (),
    ) -> tuple[str, str, str | None, str]:
        contextual = tuple(part for part in scope if part in _CONTROL)
        if contextual:
            summary += f" Source is conditional or deferred ({', '.join(contextual)}); it may not take effect."
        item = EvidenceCandidate(
            "cmake-declaration",
            path,
            locator,
            "A static CMake source declaration is present.",
            "bounded literal-only inspection",
            "cmake.inspect.v1",
            f"{code}:{locator}:{hashlib.sha256(summary.encode()).hexdigest()}",
        )
        evidence.append(item)
        key = evidence_key(item)
        findings.append(
            FindingCandidate(
                "cmake." + code, "unknown" if unknown else "verified", subject, summary, (key,)
            )
        )
        return key

    def issue(
        code: str, path: str, message: str, *, partial: bool = False, subject: str | None = None
    ) -> None:
        diagnostics.append(
            DiagnosticCandidate(
                "cmake." + code,
                path,
                problem=message,
                effect="The affected declaration remains unresolved; no configuration or commands were executed",
                recovery="inspect the source manually or use a supported literal declaration",
                partial=partial,
                subject_path=subject,
                disposition="problem" if code.startswith("invalid") else "limitation",
            )
        )

    for path in manifest_paths:
        if view.checkpoint():
            break
        data = view.read_bytes(path)
        if data is None:
            continue
        try:
            calls = commands(data.decode("utf-8"), view.checkpoint)
        except (UnicodeError, CMakeSyntaxError):
            issue(
                "invalid-syntax",
                path,
                "CMake source has malformed or unsupported syntax",
                partial=True,
            )
            continue
        parsed[path] = calls
        for call in calls:
            if (
                call.name == "project"
                and call.arguments
                and call.arguments[0].literal
                and not (_CONTROL & set(call.scope))
            ):
                projects.add(parent(path))

    # Ordinary build directories belong to the nearest independently established component.
    roots = frozenset(projects)
    owners = roots | context.component_paths
    references: list[tuple[str, str, tuple[str, str, str | None, str]]] = []
    for path, calls in parsed.items():
        subject = nearest_ancestor(parent(path), owners)
        for call in calls:
            if view.checkpoint():
                break
            args = [arg.value for arg in call.arguments]
            name = call.name
            if name not in {
                "project",
                "cmake_minimum_required",
                "set",
                "set_property",
                "set_target_properties",
                "target_compile_features",
                "find_package",
                "include",
                "enable_testing",
                "add_test",
                "add_subdirectory",
            }:
                continue
            if name == "set" and (not args or args[0] not in _STANDARD):
                continue
            if name in {"set_property", "set_target_properties"} and not any(
                value in _STANDARD for value in args
            ):
                continue
            if (
                name == "include"
                and args
                and args[0] not in {"CTest", "GoogleTest", "Catch"}
                and all(arg.literal for arg in call.arguments)
            ):
                continue
            # Key/value credentials must be withheld even without assignment syntax.
            if any(contains_literal_credential(value) for value in args) or any(
                contains_literal_credential(f"{key}=withheld") for key in args[:-1]
            ):
                emit(
                    "declaration.redacted",
                    path,
                    call.locator,
                    subject,
                    "A declaration's values were withheld because they resemble credentials.",
                    unknown=True,
                    scope=call.scope,
                )
                continue
            if any(
                value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value) for value in args
            ):
                emit(
                    "declaration.unresolved",
                    path,
                    call.locator,
                    subject,
                    "A declaration contains an absolute or host-specific value; its values were withheld.",
                    unknown=True,
                    scope=call.scope,
                )
                issue(
                    "unresolved-subdirectory"
                    if name == "add_subdirectory"
                    else "unsafe-declaration",
                    path,
                    "A host-specific declaration value was withheld",
                    partial=name == "add_subdirectory",
                    subject=subject,
                )
                continue
            dynamic = not all(arg.literal for arg in call.arguments)
            if name == "find_package" and args and call.arguments[0].literal:
                request = [arg.value if arg.literal else "<unresolved>" for arg in call.arguments]
                limitation = (
                    " dynamic argument values were withheld and remain unresolved;"
                    if dynamic
                    else ""
                )
                emit(
                    "dependency.request",
                    path,
                    call.locator,
                    subject,
                    f"Source declares dependency request find_package({quoted(request)});{limitation} installation and resolution are unknown.",
                    scope=call.scope,
                )
                if args[0] in {"GTest", "Catch2"}:
                    emit(
                        "tool.declaration",
                        path,
                        call.locator,
                        subject,
                        f"Source explicitly requests test framework {quoted(args[0])}.",
                        scope=call.scope,
                    )
                if not dynamic:
                    continue
            if dynamic:
                if name == "project" and args and call.arguments[0].literal:
                    emit(
                        "identity.declaration",
                        path,
                        call.locator,
                        subject,
                        f"CMake declares project {quoted(args[0])}; other arguments remain unresolved.",
                        scope=call.scope,
                    )
                emit(
                    "declaration.dynamic",
                    path,
                    call.locator,
                    subject,
                    f"A dynamic {name} declaration was not evaluated.",
                    unknown=True,
                    scope=call.scope,
                )
                issue(
                    "dynamic-declaration",
                    path,
                    f"A {name} declaration uses dynamic or unsupported arguments",
                    subject=subject,
                )
                continue
            key = None
            is_language = False
            if name == "project" and args:
                key = emit(
                    "identity.declaration",
                    path,
                    call.locator,
                    subject,
                    f"CMake declares project {quoted(args[0])}.",
                    scope=call.scope,
                )
                languages = _project_languages(call)
                if languages is None:
                    emit(
                        "declaration.unresolved",
                        path,
                        call.locator,
                        subject,
                        "Project options have unsupported or malformed arguments; explicit languages remain unresolved.",
                        unknown=True,
                        scope=call.scope,
                    )
                    issue(
                        "unsupported-declaration",
                        path,
                        "A project declaration has unsupported option arguments",
                        subject=subject,
                    )
                elif languages:
                    key = emit(
                        "language.declaration",
                        path,
                        call.locator,
                        subject,
                        f"Project explicitly declares languages {quoted(languages)}; these are declarations, not effective configuration.",
                        scope=call.scope,
                    )
                    is_language = bool({"C", "CXX"} & set(languages))
            elif (
                name == "cmake_minimum_required"
                and len(args) >= 2
                and args[0] == "VERSION"
                and _VERSION.fullmatch(args[1])
            ):
                emit(
                    "version.declaration",
                    path,
                    call.locator,
                    subject,
                    f"CMake minimum/version-policy range is declared as {quoted(args[1])}.",
                    scope=call.scope,
                )
            elif (
                name == "set"
                and len(args) >= 2
                and re.fullmatch(r"[0-9]+", args[1])
                and (
                    args[2:] in ([], ["PARENT_SCOPE"])
                    or (
                        len(args) in {5, 6}
                        and args[2] == "CACHE"
                        and args[3] in {"BOOL", "FILEPATH", "PATH", "STRING", "INTERNAL"}
                        and (len(args) == 5 or args[5] == "FORCE")
                    )
                )
            ):
                key = emit(
                    "standard.declaration",
                    path,
                    call.locator,
                    subject,
                    f"Source variable {args[0]} declares {quoted(args[1])} in directory {quoted(parent(path))}; no effective or project-wide standard is inferred.",
                    scope=call.scope,
                )
                is_language = True
            elif name in {"set_property", "set_target_properties"}:
                marker = "PROPERTY" if name == "set_property" else "PROPERTIES"
                if marker in args and (name == "set_target_properties" or args[:1] == ["TARGET"]):
                    split = args.index(marker)
                    targets = args[1:split] if name == "set_property" else args[:split]
                    props = args[split + 1 :]
                    if name == "set_property" and any(
                        value in {"APPEND", "APPEND_STRING"} for value in targets
                    ):
                        issue(
                            "unsupported-standard",
                            path,
                            "An appended target standard remains unresolved",
                            subject=subject,
                        )
                    elif targets and len(props) % 2 == 0:
                        pairs = zip(props[::2], props[1::2], strict=True)
                        for prop, value in (
                            pairs if name == "set_target_properties" or len(props) == 2 else ()
                        ):
                            if prop in {"C_STANDARD", "CXX_STANDARD"} and value.isdecimal():
                                key = emit(
                                    "standard.declaration",
                                    path,
                                    call.locator + ":" + prop,
                                    subject,
                                    f"Targets {quoted(targets)} declare {prop} {quoted(value)}; no effective standard is inferred.",
                                    scope=call.scope,
                                )
                                is_language = True
                if key is None:
                    emit(
                        "standard.unresolved",
                        path,
                        call.locator,
                        subject,
                        "A target-standard declaration has unsupported literal arguments.",
                        unknown=True,
                        scope=call.scope,
                    )
                    issue(
                        "unsupported-standard",
                        path,
                        "A target-standard declaration remains unresolved",
                        subject=subject,
                    )
            elif name == "target_compile_features" and len(args) >= 3:
                visibility = ""
                for feature in args[1:]:
                    if feature in {"PUBLIC", "PRIVATE", "INTERFACE"}:
                        visibility = feature
                    elif visibility and re.fullmatch(r"(?:c|cxx)_std_[0-9]+", feature):
                        key = emit(
                            "standard.declaration",
                            path,
                            call.locator + ":" + feature,
                            subject,
                            f"Target {quoted(args[0])} declares compile feature {quoted(feature)} with visibility {quoted(visibility)}; no effective standard is inferred.",
                            scope=call.scope,
                        )
                        is_language = True
                if key is None:
                    emit(
                        "standard.unresolved",
                        path,
                        call.locator,
                        subject,
                        "No supported literal language-standard compile feature was recognized.",
                        unknown=True,
                        scope=call.scope,
                    )
            elif name in {"include", "enable_testing", "add_test"}:
                if name != "include" or (args and args[0] in {"CTest", "GoogleTest", "Catch"}):
                    tool = args[0] if name == "include" else "CTest"
                    emit(
                        "tool.declaration",
                        path,
                        call.locator,
                        subject,
                        f"Source declares testing/tool evidence {quoted(tool)} through {name}; tests were not executed.",
                        scope=call.scope,
                    )
                else:
                    emit(
                        "tool.unresolved",
                        path,
                        call.locator,
                        subject,
                        "An include declaration has no supported literal tool selection.",
                        unknown=True,
                        scope=call.scope,
                    )
            elif name == "add_subdirectory" and args and _subdirectory_arguments(args):
                key = emit(
                    "subdirectory.declaration",
                    path,
                    call.locator,
                    subject,
                    f"Source declares build subdirectory {quoted(args[0])}; this declaration alone establishes no component.",
                    scope=call.scope,
                )
                target = safe_member(parent(path), args[0])
                expected = "CMakeLists.txt" if target == "." else f"{target}/CMakeLists.txt"
                if target is None or expected not in parsed:
                    issue(
                        "unresolved-subdirectory",
                        path,
                        "A build subdirectory is missing, excluded, unsafe, escaping, or unreadable",
                        partial=True,
                        subject=subject,
                    )
                elif not (_CONTROL & set(call.scope)):
                    references.append((parent(path), target, key))
            else:
                emit(
                    "declaration.unresolved",
                    path,
                    call.locator,
                    subject,
                    f"An unsupported or malformed {name} declaration remains unresolved.",
                    unknown=True,
                    scope=call.scope,
                )
                issue(
                    "unsupported-declaration",
                    path,
                    f"A {name} declaration has unsupported arguments",
                    subject=subject,
                )
            if (
                is_language
                and key is not None
                and subject is not None
                and not (_CONTROL & set(call.scope))
            ):
                qualified.setdefault(subject, []).append(key)

    # Safe members cannot ascend: the only possible retained cycle is a self-reference.
    for source, target, key in references:
        if source == target:
            issue(
                "subdirectory-cycle",
                "CMakeLists.txt" if source == "." else f"{source}/CMakeLists.txt",
                "A cyclic subdirectory relationship was omitted",
                partial=True,
                subject=nearest_ancestor(source, owners),
            )
            continue
        owner = nearest_ancestor(source, owners)
        if owner is not None and target in roots and owner != target:
            relationships.append(
                RelationshipCandidate("cmake-subdirectory", owner, target, "verified", (key,))
            )
    for root, keys in sorted(qualified.items()):
        components.append(ComponentCandidate(root, "project", tuple(keys), "cmake"))

    for path in auxiliary_paths:
        if view.checkpoint():
            break
        name = PurePosixPath(path).name
        subject = nearest_ancestor(parent(path), owners)
        if name.startswith(".clang-"):
            if subject is not None and view.read_bytes(path) is not None:
                emit(
                    "tool.configuration",
                    path,
                    "file",
                    subject,
                    f"Configuration for {name[1:]} is present; installation and use are unknown.",
                )
            continue
        data = view.read_bytes(path)
        if data is None:
            continue
        try:
            document = _unique_object(data)
        except (UnicodeError, ValueError, RecursionError):
            issue(
                "invalid-presets",
                path,
                "Presets must be unique-key UTF-8 JSON objects containing Unicode scalar strings",
                partial=True,
                subject=subject,
            )
            continue
        version = document.get("version")
        if type(version) is not int or not 1 <= version <= 12:
            emit(
                "preset.unsupported",
                path,
                "/version",
                subject,
                "The preset format version is unsupported.",
                unknown=True,
            )
            issue(
                "unsupported-presets-version",
                path,
                "The preset format version is outside supported versions 1–12",
                subject=subject,
            )
            continue
        if "include" in document:
            if version < 4:
                issue(
                    "invalid-presets",
                    path,
                    "include requires preset version 4 or newer",
                    partial=True,
                    subject=subject,
                )
            emit(
                "preset.unresolved",
                path,
                "/include",
                subject,
                "Preset includes were not expanded.",
                unknown=True,
            )
            issue("unresolved-presets", path, "Preset includes remain unresolved", subject=subject)
        if "packagePresets" in document:
            emit(
                "preset.unsupported",
                path,
                "/packagePresets",
                subject,
                "Package presets are outside stage 1 inspection.",
                unknown=True,
            )
        for kind, minimum in (("configure", 1), ("build", 2), ("test", 2), ("workflow", 6)):
            field = kind + "Presets"
            if field not in document:
                continue
            values = document[field]
            if version < minimum or not isinstance(values, list):
                issue(
                    "invalid-presets",
                    path,
                    f"{field} is unavailable in this format version or is not an array",
                    partial=True,
                    subject=subject,
                )
                continue
            seen_names: set[str] = set()
            for index, preset in enumerate(values):
                if view.checkpoint():
                    break
                locator = pointer(field, index)
                if (
                    not isinstance(preset, dict)
                    or not isinstance(preset.get("name"), str)
                    or not preset["name"]
                    or preset["name"] in seen_names
                    or type(preset.get("hidden", False)) is not bool
                ):
                    issue(
                        "invalid-presets",
                        path,
                        "A preset has invalid fields or duplicates a name within its kind",
                        partial=True,
                        subject=subject,
                    )
                    continue
                seen_names.add(preset["name"])
                if contains_literal_credential(preset["name"]):
                    emit(
                        "preset.unresolved",
                        path,
                        locator + "/name",
                        subject,
                        "A preset name resembling credentials was withheld.",
                        unknown=True,
                    )
                    continue
                emit(
                    "preset.declaration",
                    path,
                    locator + "/name",
                    subject,
                    f"A {'hidden ' if preset.get('hidden') else ''}{kind} preset named {quoted(preset['name'])} is declared; availability and execution are unverified.",
                )
                for construct in ("inherits", "condition"):
                    if construct in preset:
                        if construct == "condition" and version < 3:
                            issue(
                                "invalid-presets",
                                path,
                                "condition requires preset version 3 or newer",
                                partial=True,
                                subject=subject,
                            )
                        emit(
                            "preset.unresolved",
                            path,
                            locator + "/" + construct,
                            subject,
                            f"Preset {construct} remains unresolved.",
                            unknown=True,
                        )
                        issue(
                            "unresolved-presets",
                            path,
                            f"Preset {construct} was not evaluated",
                            subject=subject,
                        )
                if kind != "configure":
                    continue
                for selection in ("generator", "toolchainFile"):
                    if selection not in preset:
                        continue
                    value = preset[selection]
                    if selection == "toolchainFile" and version < 3:
                        issue(
                            "invalid-presets",
                            path,
                            "toolchainFile requires preset version 3 or newer",
                            partial=True,
                            subject=subject,
                        )
                    elif not isinstance(value, str) or not value:
                        issue(
                            "invalid-presets",
                            path,
                            "A preset selection must be a nonempty string",
                            partial=True,
                            subject=subject,
                        )
                    elif (
                        "$" in value
                        or value.startswith(("/", "\\"))
                        or re.match(r"^[A-Za-z]:", value)
                        or contains_literal_credential(value)
                        or (
                            selection == "toolchainFile"
                            and safe_member(parent(path), value) is None
                        )
                    ):
                        emit(
                            "preset.unresolved",
                            path,
                            locator + "/" + selection,
                            subject,
                            f"Preset {selection} is dynamic, sensitive, or not a safe relative selection; its value was withheld.",
                            unknown=True,
                        )
                        issue(
                            "unresolved-presets",
                            path,
                            f"Preset {selection} remains unresolved",
                            subject=subject,
                        )
                    else:
                        emit(
                            "preset.selection",
                            path,
                            locator + "/" + selection,
                            subject,
                            f"Preset directly declares {selection} {quoted(value)}; effective selection is unknown.",
                        )

    if roots:

        def ci_issue(code: str, path: str, partial: bool) -> None:
            problems = {
                "invalid-ci-workflow": "CI workflow content is not supported static YAML",
                "ci-scope-unresolved": "A CI command has an invalid, dynamic, or external checkout/working-directory scope",
                "ci-include-cycle": "A GitLab local include refers back to an active ancestor",
                "ci-include-depth": "GitLab local include nesting exceeds the supported depth of 16",
                "invalid-ci-include": "A GitLab local include is missing, unsafe, excluded, or escaping",
                "external-ci-include": "An external or dynamic GitLab include was not fetched",
                "external-ci-step": "A reusable GitLab run step was not fetched or evaluated",
            }
            issue(
                code,
                path,
                problems[code],
                partial=partial,
            )

        for command in inspect_commands(view, ci_issue):
            subject = nearest_ancestor(command.directory, roots | context.component_paths)
            if subject not in roots:
                continue
            redacted = contains_literal_credential(command.value)
            dynamic = expression_only(command.value)
            summary = (
                "A CI command's credential-shaped values were withheld."
                if redacted
                else "A dynamic CI command expression was not evaluated."
                if dynamic
                else f"CI declares command {quoted(command.value)}; it was not executed or verified as a preferred workflow."
            )
            emit(
                "ci.command",
                command.path,
                command.locator,
                subject,
                summary,
                unknown=redacted or dynamic,
            )
    return DetectionResult(
        tuple(evidence),
        tuple(components),
        tuple(findings),
        tuple(diagnostics),
        tuple(relationships),
    )
