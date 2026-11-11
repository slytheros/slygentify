"""Bazel repository/package markers and explicit C/C++ rule declarations."""

from __future__ import annotations

from slygentify._scan.contracts import DetectionContext, DetectionResult, RepositoryView
from slygentify._scan.detectors._static_syntax import Inspection, Stopped, arguments, literal
from slygentify._scan.detectors._support import quoted
from slygentify._scan.paths import nearest_ancestor, parent
from slygentify.traceability import implements

_ROOTS = {"MODULE.bazel", "REPO.bazel", "WORKSPACE", "WORKSPACE.bazel"}
_BUILDS = {"BUILD", "BUILD.bazel"}
_RULES = {"cc_binary", "cc_library", "cc_test", "cc_import", "cc_shared_library"}


@implements("REQ061")
def detect_bazel(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    state = Inspection(view, context, "bazel")
    try:
        documents = []
        catalog_roots = {c.parent for c in view.path_candidates() if c.name in _ROOTS}
        files: dict[str, set[str]] = {}
        for path, calls in state.documents(_ROOTS | _BUILDS, "bazel"):
            documents.append((path, calls))
            root = parent(path)
            name = path.rsplit("/", 1)[-1]
            files.setdefault(root, set()).add(name)
            key = state.emit(
                path,
                "file:boundary",
                root,
                "component.evidence",
                f"A Bazel {'repository root marker' if name in _ROOTS else 'package BUILD file'} is present; it does not establish a compiled language or target.",
            )
            state.component(root, "workspace" if root in catalog_roots else "package", key)
        root_paths = {parent(path) for path, _ in documents if path.rsplit("/", 1)[-1] in _ROOTS}
        package_paths = {
            parent(path) for path, _ in documents if path.rsplit("/", 1)[-1] in _BUILDS
        }
        ambiguous = set()
        for root, names in files.items():
            if (
                {"BUILD", "BUILD.bazel"} <= names
                or {"MODULE.bazel", "REPO.bazel"} <= names
                or {"WORKSPACE", "WORKSPACE.bazel"} <= names
            ):
                ambiguous.add(root)
                path = next(path for path, _ in documents if parent(path) == root)
                state.unknown(
                    path,
                    "file:conflict",
                    root,
                    "Competing Bazel boundary files coexist; no effective file was selected.",
                )
        for package in sorted(package_paths):
            package_owner = nearest_ancestor(package, catalog_roots)
            path = next(
                path
                for path, _ in documents
                if parent(path) == package and path.rsplit("/", 1)[-1] in _BUILDS
            )
            if package in ambiguous or package_owner in ambiguous:
                state.unknown(
                    path,
                    "package:membership",
                    package,
                    "Ambiguous Bazel boundary markers prevent package membership resolution.",
                )
            elif package_owner not in root_paths:
                state.unknown(
                    path,
                    "package:membership",
                    package,
                    "No catalogued Bazel repository root owns this package.",
                )
            else:
                state.member(path, "package:membership", package_owner, package, package_paths)
        for path, calls in documents:
            root = parent(path)
            name = path.rsplit("/", 1)[-1]
            bindings: dict[str, str | None] = {}
            counts: dict[str, int] = {}
            for call in calls:
                if call.binding is not None:
                    counts[call.binding] = counts.get(call.binding, 0) + 1
                elif call.name == "!opaque":
                    tokens = call.arguments[0]
                    assignment = next(
                        (
                            i
                            for i, token in enumerate(tokens)
                            if token.value == "=" and token.kind == "symbol"
                        ),
                        None,
                    )
                    if assignment is not None:
                        for token in tokens[:assignment]:
                            if token.kind == "word":
                                counts[token.value] = counts.get(token.value, 0) + 1
                if call.name != "load":
                    continue
                positional, keywords = arguments(call, "=")
                label = literal(positional[0]) if positional else None
                imported = [(literal(part), literal(part)) for part in positional[1:]]
                imported += [(alias, literal(value)) for alias, value in keywords.items()]
                for alias, rule in imported:
                    if alias is None:
                        continue
                    counts[alias] = counts.get(alias, 0) + 1
                    standard = (
                        label == "@rules_cc//cc:defs.bzl" or label == f"@rules_cc//cc:{rule}.bzl"
                    )
                    bindings[alias] = (
                        rule if standard and rule in _RULES and not call.conditional else None
                    )
                state.unsupported(
                    path,
                    root,
                    "Bazel load source and implementation remain unresolved; literal standard rules_cc imports identify source spelling only.",
                )
            identities = []
            for call in calls:
                positional, keywords = arguments(call, "=")
                if call.name in {"module", "workspace"} and name in _ROOTS:
                    value = literal(keywords.get("name", ()))
                    if value is None or call.conditional or counts.get(call.name, 0):
                        state.unknown(
                            path,
                            call.locator,
                            root,
                            "Bazel module/workspace identity requires an unshadowed direct literal declaration.",
                        )
                    else:
                        identities.append(
                            state.declaration(
                                path,
                                call,
                                root,
                                "identity",
                                f"Bazel {call.name} name {quoted(value)} is declared.",
                            )
                        )
                        if "version" in keywords:
                            version = literal(keywords["version"])
                            if version is None:
                                state.unknown(
                                    path,
                                    call.locator + ":version",
                                    root,
                                    "Computed Bazel version remains unresolved.",
                                )
                            else:
                                state.declaration(
                                    path,
                                    call,
                                    root,
                                    "version",
                                    f"Bazel module version {quoted(version)} is declared.",
                                )
                elif call.name == "load":
                    continue
                elif name in _BUILDS and (call.name in _RULES or call.name in bindings):
                    rule = bindings.get(call.name, call.name)
                    count = counts.get(call.name, 0)
                    shadowed = count > (1 if call.name in bindings else 0)
                    target = literal(keywords.get("name", ()))
                    if (
                        rule is None
                        or shadowed
                        or call.binding is not None
                        or call.conditional
                        or target is None
                        or positional
                    ):
                        state.unknown(
                            path,
                            call.locator,
                            root,
                            "A shadowed, computed or deferred C/C++ rule call remains unresolved.",
                        )
                    else:
                        import_description = (
                            " through a literal standard rules_cc import"
                            if call.name in bindings
                            else " through a legacy rule name"
                        )
                        state.declaration(
                            path,
                            call,
                            root,
                            "language",
                            f"Bazel C/C++ rule {quoted(rule)} with target name {quoted(target)} is declared{import_description}; implementation, source types and compilation are unknown.",
                        )
                else:
                    state.unsupported(
                        path,
                        root,
                        "Additional Bazel expressions, macros and external modules remain unevaluated.",
                    )
            state.conflict(path, root, identities)
    except Stopped:
        pass
    return state.result()
