"""Autoconf package declarations and literal Automake source/composition evidence."""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import PurePosixPath

from slygentify._scan.contracts import (
    DetectionContext,
    DetectionResult,
    EvidenceKey,
    RepositoryView,
)
from slygentify._scan.detectors._static_syntax import Call, Inspection, Stopped, literal, safe_text
from slygentify._scan.detectors._support import quoted
from slygentify._scan.paths import parent
from slygentify.traceability import implements


@implements("REQ061")
def detect_autotools(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    state = Inspection(view, context, "autotools")
    try:
        documents = []
        for path, calls in state.documents({"configure.ac"}, "m4"):
            documents.append((path, calls))
            keys: list[EvidenceKey] = []
            for call in calls:
                if call.name != "AC_INIT":
                    continue
                name = literal(call.arguments[0]) if call.arguments else None
                if name is None:
                    state.unknown(
                        path,
                        call.locator,
                        None,
                        "Package identity requires a literal AC_INIT name.",
                    )
                    continue
                key = state.declaration(
                    path,
                    call,
                    None if call.conditional else parent(path),
                    "identity",
                    f"Autoconf package name {quoted(name)} is declared.",
                )
                if not call.conditional:
                    state.component(parent(path), "package", key)
                    keys.append(key)
            state.conflict(path, parent(path), keys)
        roots = {c.path for c in state.components}
        for path, calls in documents:
            owner = state.owner(path)
            for call in calls:
                if call.name == "AC_INIT":
                    if len(call.arguments) > 1:
                        version = literal(call.arguments[1])
                        if version is None:
                            state.unknown(
                                path,
                                call.locator + ":version",
                                owner,
                                "Computed package version remains unresolved.",
                            )
                        else:
                            state.declaration(
                                path,
                                call,
                                owner,
                                "version",
                                f"Autoconf package version {quoted(version)} is declared.",
                            )
                elif call.name in {"AC_PROG_CC", "AC_PROG_CXX"}:
                    language = "C" if call.name == "AC_PROG_CC" else "C++"
                    state.declaration(
                        path,
                        call,
                        owner,
                        "language",
                        f"Autoconf {call.name} declares a {language} compiler check; compiler availability and compilation are unknown.",
                    )
                elif call.name == "AC_CONFIG_SUBDIRS":
                    values = literal(call.arguments[0]) if len(call.arguments) == 1 else None
                    if values is None or call.conditional:
                        state.unknown(
                            path,
                            call.locator,
                            owner,
                            "Computed or conditional subpackage membership remains unresolved.",
                        )
                    else:
                        for index, value in enumerate(values.split()):
                            state.member(
                                path,
                                call.locator + f":{index}",
                                owner,
                                state.reference(parent(path), value),
                                roots,
                            )
                else:
                    state.unsupported(
                        path,
                        owner,
                        "Autoconf macro bodies, includes and shell logic remain unevaluated.",
                    )
    except Stopped:
        pass
    return state.result()


@implements("REQ061")
def detect_automake(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    """Attribute fragments only after all independent boundaries are established."""
    state = Inspection(view, context, "autotools")
    with suppress(Stopped):
        _automake(state, set(context.component_paths))
    return state.result()


@implements("REQ061")
def _automake(state: Inspection, roots: set[str]) -> None:
    for candidate in state.view.path_candidates():
        if state.view.checkpoint():
            raise Stopped
        if candidate.name != "Makefile.am":
            continue
        data = state.view.read_bytes(candidate.path)
        if data is None:
            continue
        amount = 8192 + 256 * len(data)
        if not state.view.reserve_memory(candidate.path, amount):
            raise Stopped
        state.temporary += amount
        path = candidate.path
        owner = state.owner(path)
        if owner is None:
            state.unknown(
                path,
                "ownership",
                None,
                "Automake evidence has no independently established component owner.",
            )
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            state.unknown(
                path, "encoding", owner, "Unsupported UTF-8 Automake source encoding.", partial=True
            )
            continue
        conditional = 0
        logical = ""
        start = 1
        recipe_continuation = False
        for number, raw in enumerate(text.splitlines(), 1):
            if state.view.checkpoint():
                raise Stopped
            if recipe_continuation or (not logical and raw.startswith("\t")):
                state.unsupported(
                    path,
                    owner,
                    "Automake includes, recipes and unsupported syntax remain unresolved.",
                )
                recipe_continuation = raw.rstrip().endswith("\\")
                continue
            line = raw.split("#", 1)[0].rstrip()
            if not logical:
                start = number
            logical += line.removesuffix("\\") + " "
            if line.endswith("\\"):
                continue
            line = logical.strip()
            logical = ""
            if not line:
                continue
            if line.startswith("if "):
                conditional += 1
                continue
            if line in {"else", "endif"}:
                if conditional == 0:
                    state.unknown(
                        path,
                        f"line:{start}:conditional",
                        owner,
                        "An Automake conditional terminator has no opening condition.",
                        partial=True,
                    )
                if line == "endif":
                    conditional = max(0, conditional - 1)
                continue
            match = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)\s*(\+=|:=|\?=|=)\s*(.*)", line)
            call = Call("Makefile.am", start, (), bool(conditional))
            if match is None:
                state.unsupported(
                    path,
                    owner,
                    "Automake includes, recipes and unsupported syntax remain unresolved.",
                )
                continue
            name, operator, value = match.groups()
            if name != "SUBDIRS" and not name.endswith("_SOURCES"):
                continue
            if not safe_text(value) or "@" in value:
                state.unknown(
                    path,
                    call.locator,
                    owner,
                    "Computed, empty or sensitive Automake assignment values were withheld.",
                )
                continue
            for index, item in enumerate(value.split()):
                target = state.reference(parent(path), item)
                if target is None:
                    state.unknown(
                        path,
                        call.locator + f":{index}",
                        owner,
                        "An unsafe Automake reference was withheld.",
                    )
                elif name == "SUBDIRS":
                    if target in roots and not call.conditional:
                        state.member(path, call.locator + f":{index}", owner, target, roots)
                    elif any(
                        c.path == f"{target}/Makefile.am" for c in state.view.path_candidates()
                    ):
                        state.declaration(
                            path,
                            call,
                            owner,
                            f"composition.{index}",
                            f"Automake SUBDIRS {operator} declares fragment {quoted(target)}; it does not establish a component.",
                        )
                    else:
                        state.unknown(
                            path,
                            call.locator + f":{index}",
                            owner,
                            "An Automake subdirectory has no safe catalogued fragment or package.",
                            reference=True,
                        )
                else:
                    extension = PurePosixPath(item).suffix
                    if extension in {".c", ".C", ".cc", ".cpp", ".cxx", ".c++"}:
                        language = "C" if extension == ".c" else "C++"
                        state.declaration(
                            path,
                            call,
                            owner,
                            f"language.{index}",
                            f"Automake {name} {operator} declares {language} source suffix {quoted(extension)}; extra/distribution-only entries and activation are not evaluated, and source files were not read.",
                        )
        if logical or conditional:
            state.unknown(
                path,
                "syntax",
                owner,
                "Unterminated Automake continuation or conditional.",
                partial=True,
            )
