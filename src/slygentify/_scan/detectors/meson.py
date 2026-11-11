"""Literal Meson project boundaries, languages and local composition."""

from __future__ import annotations

from slygentify._scan.contracts import (
    DetectionContext,
    DetectionResult,
    EvidenceKey,
    RepositoryView,
)
from slygentify._scan.detectors._static_syntax import (
    Inspection,
    Stopped,
    arguments,
    literal,
    strings,
)
from slygentify._scan.detectors._support import quoted
from slygentify._scan.paths import nearest_ancestor, parent
from slygentify.traceability import implements


@implements("REQ061")
def detect_meson(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    state = Inspection(view, context, "meson")
    try:
        documents = []
        projects: dict[str, str | None] = {}
        for path, calls in state.documents({"meson.build"}, "meson"):
            documents.append((path, calls))
            keys: list[EvidenceKey] = []
            for call in calls:
                if call.name != "project":
                    continue
                positional, keywords = arguments(call, ":")
                name = literal(positional[0]) if positional else None
                if name is None or "!duplicate" in keywords:
                    state.unknown(
                        path,
                        call.locator,
                        None,
                        "Project identity requires a supported literal name and unique fields.",
                    )
                    continue
                root = parent(path)
                key = state.declaration(
                    path,
                    call,
                    root if not call.conditional else None,
                    "identity",
                    f"Meson project name {quoted(name)} is declared.",
                )
                if not call.conditional:
                    keys.append(key)
                    state.component(root, "project", key)
                    directory = (
                        literal(keywords["subproject_dir"])
                        if "subproject_dir" in keywords
                        else "subprojects"
                    )
                    projects[root] = state.reference(root, directory)
            state.conflict(path, parent(path), keys)
            if len(keys) > 1:
                projects[parent(path)] = None
        roots = set(projects)
        available = {path for path, _ in documents}
        for path, calls in documents:
            owner = nearest_ancestor(parent(path), roots)
            for call in calls:
                positional, keywords = arguments(call, ":")
                if call.name in {"project", "add_languages"}:
                    language_parts = positional[1:] if call.name == "project" else positional
                    for index, part in enumerate(language_parts):
                        values = strings(part)
                        if values is None:
                            state.unknown(
                                path,
                                call.locator + f":language:{index}",
                                owner,
                                "Computed language declarations remain unresolved.",
                            )
                        else:
                            state.declaration(
                                path,
                                call,
                                owner,
                                f"language.{index}",
                                f"Meson languages {quoted(values)} are declared; compiler availability and effective use are unknown.",
                            )
                    if "version" in keywords and call.name == "project":
                        version = literal(keywords["version"])
                        if version is None:
                            state.unknown(
                                path,
                                call.locator + ":version",
                                owner,
                                "Computed project version remains unresolved.",
                            )
                        else:
                            state.declaration(
                                path,
                                call,
                                owner,
                                "version",
                                f"Meson project version {quoted(version)} is declared.",
                            )
                    if (
                        "subproject_dir" in keywords
                        and state.reference(parent(path), literal(keywords["subproject_dir"]))
                        is None
                    ):
                        state.unknown(
                            path,
                            call.locator + ":subproject_dir",
                            owner,
                            "Computed subproject directory remains unresolved.",
                        )
                elif call.name in {"subdir", "subproject"}:
                    value = literal(positional[0]) if len(positional) == 1 else None
                    base = parent(path) if call.name == "subdir" else projects.get(owner or "")
                    target = state.reference(base, value) if base is not None else None
                    if call.conditional:
                        state.unknown(
                            path,
                            call.locator,
                            owner,
                            "Conditional local composition is declared; inclusion remains unresolved.",
                        )
                    elif call.name == "subproject":
                        state.member(path, call.locator, owner, target, roots)
                    elif target is not None and f"{target}/meson.build" in available:
                        state.declaration(
                            path,
                            call,
                            owner,
                            "composition",
                            f"Meson subdirectory {quoted(target)} is declared; it does not establish a project boundary.",
                        )
                    else:
                        state.unknown(
                            path,
                            call.locator,
                            owner,
                            "A subdirectory reference has no safe catalogued Meson fragment.",
                            reference=target is not None,
                        )
                else:
                    state.unknown(
                        path,
                        call.locator,
                        owner,
                        "Additional Meson syntax remains outside literal boundary inspection.",
                    )
    except Stopped:
        pass
    return state.result()
