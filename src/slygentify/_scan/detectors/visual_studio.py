"""Static C++ project/solution and Make recognition, without build evaluation."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from contextlib import suppress

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
from slygentify._scan.detectors._ci import contains_literal_credential
from slygentify._scan.detectors._support import evidence_key, quoted
from slygentify._scan.paths import nearest_ancestor, parent, safe_member
from slygentify.traceability import implements

_MAKEFILES = frozenset({"Makefile", "makefile", "GNUmakefile"})
_NAMESPACE = "http://schemas.microsoft.com/developer/msbuild/2003"
_FIELDS = {
    "ProjectName": "identity",
    "PlatformToolset": "toolset",
    "LanguageStandard": "standard",
    "LanguageStandard_C": "standard",
}
_PROJECT = re.compile(
    r'^Project\("\{([0-9A-Fa-f-]{36})\}"\)\s*=\s*"([^"\r\n]*)",\s*"([^"\r\n]*)",\s*"\{[0-9A-Fa-f-]{36}\}"\s*$'
)
_FOLDERS = {"66a26720-8fb5-11d2-aa7e-00c04f688dde", "2150e333-8fdc-42a3-9474-1a3956d46de8"}


class _Stopped(Exception):
    """The repository view already recorded a resource boundary."""


class _Tree(ET.TreeBuilder):
    def __init__(self, checkpoint: Callable[[], bool]) -> None:
        super().__init__()
        self.depth = 0
        self.checkpoint = checkpoint

    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise ValueError("DTD declarations are not supported")

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        if self.checkpoint():
            raise _Stopped
        self.depth += 1
        if self.depth > 64:
            raise ValueError("XML nesting exceeds supported depth")
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        self.depth -= 1
        return super().end(tag)


def _xml(data: bytes, checkpoint: Callable[[], bool]) -> ET.Element:
    parser = ET.XMLParser(target=_Tree(checkpoint))
    for offset in range(0, len(data), 4096):
        if checkpoint():
            raise _Stopped
        parser.feed(data[offset : offset + 4096])
    return parser.close()


def _walk(root: ET.Element, namespace: str = "") -> Iterator[tuple[ET.Element, str, bool, str]]:
    pending = [(root, "/" + root.tag.rsplit("}", 1)[-1] + "[1]", False, "")]
    while pending:
        node, locator, conditional, owner = pending.pop()
        name = node.tag.removeprefix(namespace)
        conditional = (
            conditional
            or "Condition" in node.attrib
            or name in {"Choose", "When", "Otherwise", "Target"}
        )
        yield node, locator, conditional, owner
        counts: dict[str, int] = {}
        children = []
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1]
            counts[tag] = counts.get(tag, 0) + 1
            children.append((child, f"{locator}/{tag}[{counts[tag]}]", conditional, name))
        pending.extend(reversed(children))


def _literal(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 4096
        and value.isprintable()
        and not any(c in value for c in "$%{};`")
        and not contains_literal_credential(value)
    )


def _member(base: str, value: str) -> str | None:
    if not _literal(value):
        return None
    value = value.replace("\\", "/")
    if value.startswith("/") or ":" in value:
        return None
    parts = [] if base == "." else base.split("/")
    for part in value.split("/"):
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part not in {"", "."}:
            parts.append(part)
    return safe_member(".", "/".join(parts))


@implements("REQ059")
def detect_msbuild_make(view: RepositoryView, context: DetectionContext) -> DetectionResult:
    """Retain source declarations and safe directory relationships, never effective builds."""
    evidence: list[EvidenceCandidate] = []
    findings: list[FindingCandidate] = []
    diagnostics: list[DiagnosticCandidate] = []
    components: list[ComponentCandidate] = []
    relationships: list[RelationshipCandidate] = []
    projects: set[str] = set()
    project_roots: set[str] = set()
    project_spellings: dict[str, str | None] = {}
    case_lookup_complete = True
    case_lookup_memory = 0
    roots = set(context.component_paths)
    path = ""
    subject: str | None = None
    family = "msbuild"

    def charge(*values: str) -> None:
        if not view.reserve_memory(path, 4096 + 16 * sum(map(len, values))):
            raise _Stopped

    def emit(code: str, locator: str, summary: str, *, unknown: bool = False) -> EvidenceKey:
        charge(code, locator, summary, path)
        item = EvidenceCandidate(
            family + "-declaration",
            path,
            locator,
            "A static build-system source observation is present.",
            "bounded non-evaluating inspection",
            family + ".inspect.v1",
            code + ":" + locator + ":" + hashlib.sha256(summary.encode()).hexdigest(),
        )
        evidence.append(item)
        key = evidence_key(item)
        findings.append(
            FindingCandidate(
                family + "." + code, "unknown" if unknown else "verified", subject, summary, (key,)
            )
        )
        return key

    def issue(
        code: str, problem: str, *, partial: bool = False, keys: tuple[EvidenceKey, ...] = ()
    ) -> None:
        charge(code, problem)
        diagnostics.append(
            DiagnosticCandidate(
                family + "." + code,
                path,
                problem=problem,
                effect="Available source evidence is retained; effective configuration remains unknown",
                recovery="inspect the source manually or supply a supported safe literal declaration",
                partial=partial,
                subject_path=subject,
                evidence_keys=keys,
                disposition="problem"
                if partial or code == "declaration-conflict"
                else "limitation",
            )
        )

    def unresolved(locator: str, problem: str) -> None:
        emit(
            "declaration.unresolved",
            locator,
            problem + "; values were not evaluated or followed.",
            unknown=True,
        )
        issue("unsupported-declaration", problem)

    def project(document: ET.Element) -> None:
        if document.tag not in {"Project", "{" + _NAMESPACE + "}Project"}:
            raise ValueError("not an MSBuild project")
        namespace = "{" + _NAMESPACE + "}" if document.tag.startswith("{") else ""
        key = emit(
            "project.evidence",
            "/Project[1]",
            "A C++ MSBuild project boundary is declared; build success and effective configuration are unknown.",
        )
        components.append(ComponentCandidate(parent(path), "project", (key,), "msbuild"))
        if parent(path) in project_roots:
            issue(
                "project-attribution-unresolved",
                "Multiple C++ project files share this directory; file-level ownership remains unresolved",
            )
        projects.add(path)
        project_roots.add(parent(path))
        roots.add(parent(path))
        seen: dict[tuple[str, bool], tuple[str, EvidenceKey]] = {}
        for node, locator, conditional, owner in _walk(document, namespace):
            if view.checkpoint():
                raise _Stopped
            name = node.tag.removeprefix(namespace)
            if name in {"Import", "ImportGroup", "Choose", "Target", "UsingTask"}:
                unresolved(
                    locator,
                    "MSBuild imports, control flow and executable extensions remain unresolved",
                )
            if name not in _FIELDS or owner not in {"PropertyGroup", "ClCompile"}:
                continue
            value = (node.text or "").strip()
            if len(node) or not _literal(value):
                unresolved(
                    locator, "A supported MSBuild declaration is dynamic, malformed or sensitive"
                )
                continue
            summary = f"{name} {quoted(value)} is declared in this project file; effective selection is unknown."
            if conditional:
                summary += " Source is conditional or deferred and may not take effect."
            key = emit(_FIELDS[name] + ".declaration", locator, summary)
            # Conditional scopes are not compared: different configurations are legitimate.
            identity = (name, conditional)
            if not conditional and identity in seen and seen[identity][0] != value:
                issue(
                    "declaration-conflict",
                    "Different unconditional declarations coexist in one project file; no winner was selected",
                    keys=(seen[identity][1], key),
                )
            seen[identity] = (value, key)

    def solution_entries(data: bytes) -> list[tuple[str, str]]:
        if path.lower().endswith(".slnx"):
            document = _xml(data, view.checkpoint)
            if document.tag != "Solution":
                raise ValueError("not an XML solution")
            entries = []
            for node, locator, conditional, owner in _walk(document):
                if view.checkpoint():
                    raise _Stopped
                if node.tag == "Project" and owner in {"Solution", "Folder"}:
                    if conditional:
                        unresolved(locator, "Conditional solution membership remains unresolved")
                    else:
                        entries.append((node.get("Path", ""), locator + "/@Path"))
                elif node.tag not in {"Solution", "Folder", "Project"}:
                    unresolved(locator, "Additional XML solution configuration remains unresolved")
            return entries
        text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
        lines = text.splitlines()
        first = 0
        while first < len(lines) and not lines[first].strip():
            if view.checkpoint():
                raise _Stopped
            first += 1
        if first == len(lines) or not lines[first].startswith(
            "Microsoft Visual Studio Solution File, Format Version "
        ):
            raise ValueError("not a classic solution")
        entries = []
        opened = False
        for number, raw in enumerate(lines[first + 1 :], first + 2):
            if view.checkpoint():
                raise _Stopped
            line = raw.strip()
            if line.startswith("Project("):
                match = _PROJECT.fullmatch(line)
                if opened or match is None:
                    raise ValueError("malformed project entry")
                opened = True
                if match[1].lower() not in _FOLDERS:
                    entries.append((match[3], f"line:{number}"))
            elif line == "EndProject":
                if not opened:
                    raise ValueError("unmatched EndProject")
                opened = False
        if opened:
            raise ValueError("unterminated project entry")
        return entries

    candidates = view.path_candidates()
    # Project boundaries precede solutions and Make ownership, regardless of lexical order.
    for phase in ("project", "solution", "make"):
        for candidate in candidates:
            if view.checkpoint():
                break
            name = candidate.name
            selected = (
                name.lower().endswith(".vcxproj")
                if phase == "project"
                else name.lower().endswith((".sln", ".slnx"))
                if phase == "solution"
                else name in _MAKEFILES
            )
            if not selected:
                continue
            path = candidate.path
            family = "make" if phase == "make" else "msbuild"
            subject = parent(path) if phase == "project" else nearest_ancestor(parent(path), roots)
            if phase == "project":
                # Include unreadable/invalid catalogued projects in collision detection.
                # A partial index cannot establish that a spelling is unique.
                index_memory = 4096 + 32 * len(path)
                if not view.reserve_memory(path, index_memory):
                    case_lookup_complete = False
                    continue
                case_lookup_memory += index_memory
                folded = path.casefold()
                project_spellings[folded] = path if folded not in project_spellings else None
            data = view.read_bytes(path)
            if data is None:
                continue
            parser_memory = 4096 + 256 * len(data)
            if not view.reserve_memory(path, parser_memory):
                continue
            try:
                if phase == "project":
                    project(_xml(data, view.checkpoint))
                elif phase == "solution":
                    entries = solution_entries(data)
                    members: list[tuple[str, EvidenceKey]] = []
                    for value, locator in entries:
                        if view.checkpoint():
                            raise _Stopped
                        target = _member(parent(path), value)
                        declared_target = target
                        if target is not None and target not in projects and case_lookup_complete:
                            folded = target.casefold()
                            if folded in project_spellings:
                                resolved = project_spellings[folded]
                                if resolved is None:
                                    unresolved(
                                        locator,
                                        "A solution project path has ambiguous case-insensitive matches; no target was selected",
                                    )
                                    continue
                                target = resolved
                        if target is None:
                            unresolved(locator, "A solution project path is unsafe or dynamic")
                        elif not target.lower().endswith(".vcxproj"):
                            unresolved(
                                locator,
                                "A non-C++ solution project is outside supported inspection",
                            )
                        elif target not in projects:
                            unresolved(
                                locator,
                                "A referenced C++ project is unavailable or lacks valid project evidence",
                            )
                        else:
                            subject = parent(path)
                            summary = f"Literal solution membership of {quoted(target)} is declared; build ordering and activation are unknown."
                            if target != declared_target:
                                summary = f"Solution reference {quoted(value)} matches the unique catalogued C++ project {quoted(target)} ignoring case; build ordering and activation are unknown."
                            key = emit("solution.member", locator, summary)
                            members.append((parent(target), key))
                    if members:
                        subject = parent(path)
                        key = emit(
                            "solution.evidence",
                            "file",
                            "A solution declares supported C++ project membership.",
                        )
                        components.append(
                            ComponentCandidate(subject, "workspace", (key,), "msbuild")
                        )
                        roots.add(subject)
                        for target_root, member_key in members:
                            if target_root != subject:
                                relationships.append(
                                    RelationshipCandidate(
                                        "workspace-member",
                                        subject,
                                        target_root,
                                        "verified",
                                        (key, member_key),
                                    )
                                )
                    else:
                        unresolved(
                            "file",
                            "No validated C++ solution membership establishes a workspace boundary",
                        )
                else:
                    emit(
                        "tool.evidence",
                        "file",
                        "A Makefile is present; Make dialect, language, targets and preferred commands are unknown.",
                    )
                    unresolved(
                        "file",
                        "Make variables, includes, computed targets and recipes are outside static recognition",
                    )
                    if subject is None:
                        issue(
                            "ownership-unresolved",
                            "No established component owns this Makefile; evidence remains repository-scoped",
                        )
            except _Stopped:
                continue
            except (ET.ParseError, ValueError, UnicodeError, LookupError):
                with suppress(_Stopped):
                    issue(
                        "invalid-manifest",
                        "Build-system evidence is malformed, has unsupported encoding, excessive nesting or forbidden XML declarations",
                        partial=True,
                    )
            finally:
                view.release_memory(parser_memory)
    project_spellings.clear()
    view.release_memory(case_lookup_memory)
    return DetectionResult(
        tuple(evidence),
        tuple(components),
        tuple(findings),
        tuple(diagnostics),
        tuple(relationships),
    )
