"""Bounded lexical declarations shared by the static Meson and Bazel adapters.

This is deliberately not a build-language interpreter. Only whole direct calls and
simple assignments are exposed; nested expressions remain opaque token sequences.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

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


class Stopped(Exception):
    """The bounded view stopped parsing or retaining candidates."""


@dataclass(frozen=True, slots=True)
class Token:
    value: str
    kind: str
    line: int
    column: int
    indentation: int = 0


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    line: int
    arguments: tuple[tuple[Token, ...], ...]
    conditional: bool = False
    binding: str | None = None
    column: int = 0

    @property
    def locator(self) -> str:
        return f"line:{self.line}:column:{self.column}:{self.name}"


def safe_text(value: str) -> bool:
    return (
        bool(value.strip())
        and len(value) <= 4096
        and value.isprintable()
        and not contains_literal_credential(value)
        and not any(marker in value for marker in ("$", "`", "@{", "{{"))
    )


def literal(tokens: tuple[Token, ...]) -> str | None:
    if len(tokens) == 1 and tokens[0].kind == "string" and safe_text(tokens[0].value):
        return tokens[0].value
    return None


def split(tokens: tuple[Token, ...]) -> tuple[tuple[Token, ...], ...]:
    parts: list[tuple[Token, ...]] = []
    start = depth = 0
    for index, token in enumerate(tokens):
        if token.kind == "symbol":
            if token.value in "([{":
                depth += 1
            elif token.value in ")]}":
                depth -= 1
            elif token.value == "," and depth == 0:
                parts.append(tokens[start:index])
                start = index + 1
    if start < len(tokens):
        parts.append(tokens[start:])
    return tuple(parts)


def arguments(
    call: Call, separator: str
) -> tuple[list[tuple[Token, ...]], dict[str, tuple[Token, ...]]]:
    positional: list[tuple[Token, ...]] = []
    keywords: dict[str, tuple[Token, ...]] = {}
    for part in call.arguments:
        if (
            len(part) >= 2
            and part[0].kind == "word"
            and part[1].kind == "symbol"
            and part[1].value == separator
        ):
            if part[0].value in keywords:
                return [], {"!duplicate": ()}
            keywords[part[0].value] = part[2:]
        else:
            positional.append(part)
    return positional, keywords


def strings(tokens: tuple[Token, ...]) -> tuple[str, ...] | None:
    single = literal(tokens)
    if single is not None:
        return (single,)
    if (
        len(tokens) >= 2
        and tokens[0].kind == tokens[-1].kind == "symbol"
        and tokens[0].value == "["
        and tokens[-1].value == "]"
    ):
        values = tuple(literal(part) for part in split(tokens[1:-1]))
        if all(value is not None for value in values):
            return tuple(value for value in values if value is not None)
    return None


@implements("REQ061")
def lex(text: str, dialect: str, view: RepositoryView) -> tuple[list[Token], bool]:
    """Tokenize strings/comments without expanding expressions or m4 quotations."""
    result: list[Token] = []
    position = 0
    line = 1
    column = 0
    stack: list[str] = []
    cases: list[str] = []
    statement_start = True
    length = len(text)
    indents = []
    for source_line in text.split("\n"):
        if view.checkpoint():
            raise Stopped
        indents.append(len(source_line) - len(source_line.lstrip(" \t\r")))

    def token(value: str, kind: str, at_line: int, at_column: int) -> Token:
        return Token(value, kind, at_line, at_column, indents[at_line - 1])

    def advance(end: int) -> None:
        nonlocal position, line, column
        chunk = text[position:end]
        count = chunk.count("\n")
        line += count
        column = len(chunk.rsplit("\n", 1)[-1]) if count else column + len(chunk)
        position = end
        if view.checkpoint():
            raise Stopped

    while position < length:
        if view.checkpoint():
            raise Stopped
        char = text[position]
        start_line, start_column = line, column
        if char in " \t\r":
            advance(position + 1)
            continue
        if char == "#" or (dialect == "m4" and re.match(r"dnl\b", text[position : position + 4])):
            end = text.find("\n", position)
            advance(length if end < 0 else end)
            continue
        if char == "\n":
            result.append(token("\n", "newline", line, column))
            if not stack or (dialect == "m4" and all(opening == "{" for opening in stack)):
                statement_start = True
            advance(position + 1)
            continue
        # Shell case arms are opaque syntax, not m4 call parentheses. Keep their
        # boundary so declarations in an arm remain conditional, then resume after
        # esac. Never inspect quoted macro bodies or evaluate a shell expression.
        shell = dialect == "m4" and all(opening == "{" for opening in stack)
        pattern = shell and bool(cases) and cases[-1] in {"pattern", "pattern-part"}
        if pattern and char == "\\" and position + 1 < length:
            result.append(token(text[position : position + 2], "opaque", line, column))
            cases[-1] = "pattern-part"
            statement_start = False
            advance(position + 2)
            continue
        if pattern and char in "()":
            result.append(token(char, "opaque", line, column))
            if char == ")":
                cases[-1] = "body"
                result.append(token(";", "symbol", line, column))
                statement_start = True
            else:
                cases[-1] = "pattern-part"
            advance(position + 1)
            continue
        if shell and cases and cases[-1] == "body":
            terminator = next(
                (value for value in (";;&", ";;", ";&") if text.startswith(value, position)), None
            )
            if terminator is not None:
                cases[-1] = "pattern"
                result.append(token(";", "symbol", line, column))
                statement_start = True
                advance(position + len(terminator))
                continue
        if char in "\"'" or (dialect == "m4" and char == "["):
            delimiter = char
            if dialect != "m4" and text.startswith(char * 3, position):
                delimiter *= 3
            end_quote = "]" if char == "[" else delimiter
            advance(position + len(delimiter))
            parts: list[str] = []
            depth = 1
            supported = True
            if dialect == "m4" and char != "[":
                supported = False
            while position < length:
                if char == "[" and text[position] == "[":
                    depth += 1
                    if depth > 64:
                        return result, True
                if text.startswith(end_quote, position):
                    depth -= 1
                    if depth == 0:
                        advance(position + len(end_quote))
                        break
                current = text[position]
                if current == "\\" and dialect != "m4":
                    advance(position + 1)
                    if position == length:
                        return result, True
                    if text[position] not in "ntr\\\"'":
                        supported = False
                    current = {"n": "\n", "t": "\t", "r": "\r"}.get(text[position], text[position])
                parts.append(current)
                advance(position + 1)
            else:
                return result, True
            value = "".join(parts)
            if dialect == "m4" and re.search(r"[\[\]]|\bm4_[A-Za-z_0-9]+", value):
                supported = False
            result.append(
                token(value, "string" if supported else "opaque", start_line, start_column)
            )
            if pattern:
                cases[-1] = "pattern-part"
            statement_start = False
            continue
        match = re.compile(r"[A-Za-z_][A-Za-z_0-9]*").match(text, position)
        if match:
            value = match[0]
            case_end = False
            if shell and statement_start and not pattern and value in {"then", "do", "else"}:
                result.append(token(value, "opaque", line, column))
                result.append(token(";", "symbol", line, column))
                advance(position + len(value))
                continue
            if shell:
                if (
                    value == "case"
                    and statement_start
                    and not pattern
                    and (not cases or cases[-1] == "body")
                ):
                    result.append(token(";", "symbol", line, column))
                    cases.append("header")
                elif value == "in" and cases and cases[-1] == "header":
                    cases[-1] = "pattern"
                elif (
                    value == "esac"
                    and statement_start
                    and cases
                    and cases[-1] in {"pattern", "body"}
                ):
                    cases.pop()
                    case_end = True
                elif pattern:
                    cases[-1] = "pattern-part"
            result.append(
                token(value, "opaque" if pattern and not case_end else "word", line, column)
            )
            statement_start = False
            advance(position + len(value))
            continue
        if char in "([{":
            stack.append(char)
            if len(stack) > 64:
                return result, True
        elif char in ")]}":
            if not stack or stack.pop() != {")": "(", "]": "[", "}": "{"}[char]:
                return result, True
        result.append(token(char, "symbol", line, column))
        if pattern:
            cases[-1] = "pattern-part"
        statement_start = shell and char in ";{|&"
        advance(position + 1)
    return result, bool(stack)


@implements("REQ061")
def declarations(
    tokens: list[Token], dialect: str, view: RepositoryView
) -> tuple[tuple[Call, ...], bool]:
    """Expose complete statements, never calls hidden inside macros or strings."""
    statements: list[tuple[Token, ...]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens:
        if view.checkpoint():
            raise Stopped
        if token.kind == "symbol":
            if token.value in "([{":
                depth += 1
            elif token.value in ")]}":
                depth -= 1
        if (
            token.kind == "newline" or (token.kind == "symbol" and token.value == ";")
        ) and depth == 0:
            if current:
                statements.append(tuple(current))
                current = []
        elif token.kind != "newline":
            current.append(token)
    if current and depth == 0:
        statements.append(tuple(current))
    result: list[Call] = []
    scopes: list[str] = []
    control_lines: set[int] = set()
    invalid = False
    for statement in statements:
        if view.checkpoint():
            raise Stopped
        first = statement[0]
        name = first.value
        if first.kind == "word" and name in {"endif", "endforeach", "fi", "done", "esac"}:
            if scopes:
                scopes.pop()
            else:
                invalid = True
            result.append(Call("!control", first.line, (), True))
            continue
        conditional = (
            first.indentation > 0 or first.line in control_lines
            if dialect == "bazel"
            else bool(scopes)
        )
        if first.kind == "word" and name in {
            "if",
            "foreach",
            "for",
            "while",
            "until",
            "case",
            "def",
        }:
            scopes.append(name)
            control_lines.add(first.line)
            binding = statement[1].value if name == "def" and len(statement) > 1 else None
            result.append(Call("!control", first.line, (), True, binding))
            continue
        binding = None
        body = statement
        if len(body) >= 3 and body[0].kind == "word" and body[1].value == "=":
            binding = body[0].value
            body = body[2:]
        if (
            len(body) >= 3
            and body[0].kind == "word"
            and body[1].value == "("
            and body[-1].value == ")"
        ):
            # The closing parenthesis must close this call, not a later expression.
            nested = 0
            closes = []
            for index, token in enumerate(body[1:], 1):
                if token.kind == "symbol" and token.value == "(":
                    nested += 1
                elif token.kind == "symbol" and token.value == ")":
                    nested -= 1
                    if nested == 0:
                        closes.append(index)
            if closes == [len(body) - 1]:
                result.append(
                    Call(
                        body[0].value,
                        first.line,
                        split(tuple(body[2:-1])),
                        conditional,
                        binding,
                        first.column,
                    )
                )
                continue
        if (
            dialect == "m4"
            and len(body) == 1
            and first.kind == "word"
            and name in {"AC_PROG_CC", "AC_PROG_CXX"}
        ):
            result.append(Call(name, first.line, (), conditional))
        else:
            result.append(Call("!opaque", first.line, (statement,), conditional, binding))
    return tuple(result), invalid or (bool(scopes) and dialect != "bazel")


@implements("REQ061")
class Inspection:
    """Candidate retention and parser accounting through the bounded capability."""

    def __init__(self, view: RepositoryView, context: DetectionContext, prefix: str) -> None:
        self.view = view
        self.context = context
        self.prefix = prefix
        self.evidence: list[EvidenceCandidate] = []
        self.components: list[ComponentCandidate] = []
        self.findings: list[FindingCandidate] = []
        self.diagnostics: list[DiagnosticCandidate] = []
        self.relationships: list[RelationshipCandidate] = []
        self.temporary = 0
        self._unsupported_groups: set[tuple[str | None, str]] = set()
        self._unsupported_sources: set[tuple[str, str | None, str]] = set()

    def keep(self, path: str, value: str) -> None:
        if self.view.checkpoint() or not self.view.reserve_memory(path, 2048 + 16 * len(value)):
            raise Stopped

    def documents(self, names: set[str], dialect: str) -> Iterator[tuple[str, tuple[Call, ...]]]:
        for candidate in self.view.path_candidates():
            try:
                if self.view.checkpoint():
                    raise Stopped
                if candidate.name not in names:
                    continue
                data = self.view.read_bytes(candidate.path)
                if data is None:
                    continue
                amount = 8192 + 256 * len(data)
                if not self.view.reserve_memory(candidate.path, amount):
                    raise Stopped
                self.temporary += amount
                try:
                    text = data.decode("utf-8")
                except UnicodeError:
                    self.unknown(
                        candidate.path,
                        "encoding",
                        None,
                        "Unsupported UTF-8 source encoding.",
                        partial=True,
                    )
                    yield candidate.path, ()
                    continue
                tokens, invalid = lex(text, dialect, self.view)
                calls, invalid_scopes = declarations(tokens, dialect, self.view)
                if invalid or invalid_scopes:
                    self.unknown(
                        candidate.path,
                        "syntax",
                        None,
                        "Malformed or excessively nested source syntax.",
                        partial=True,
                    )
                yield candidate.path, calls
            except Stopped:
                return

    def emit(
        self,
        path: str,
        locator: str,
        subject: str | None,
        code: str,
        summary: str,
        *,
        unknown: bool = False,
    ) -> EvidenceKey:
        self.keep(path, summary)
        digest = hashlib.sha256(summary.encode()).hexdigest()
        item = EvidenceCandidate(
            f"{self.prefix}-declaration",
            path,
            locator,
            f"A static {self.prefix} source declaration is present.",
            "bounded literal-only inspection",
            f"{self.prefix}.inspect.v1",
            f"{code}:{locator}:{digest}",
        )
        self.evidence.append(item)
        key = evidence_key(item)
        self.findings.append(
            FindingCandidate(
                f"{self.prefix}.{code}",
                "unknown" if unknown else "verified",
                subject,
                summary,
                (key,),
            )
        )
        return key

    def unknown(
        self,
        path: str,
        locator: str,
        subject: str | None,
        problem: str,
        *,
        partial: bool = False,
        reference: bool = False,
    ) -> None:
        key = self.emit(
            path,
            locator,
            subject,
            "declaration.unresolved",
            problem + " No build code was evaluated; affected values remain unresolved.",
            unknown=True,
        )
        self.diagnostics.append(
            DiagnosticCandidate(
                f"{self.prefix}."
                + (
                    "unresolved-reference"
                    if reference
                    else f"{'invalid' if partial else 'unsupported'}-declaration"
                ),
                path,
                problem=problem,
                effect="The affected declaration remains unresolved",
                recovery="inspect the source manually or use a supported literal declaration",
                partial=partial,
                subject_path=subject,
                evidence_keys=(key,),
                disposition="problem" if partial else "limitation",
            )
        )

    def unsupported(self, path: str, subject: str | None, problem: str) -> None:
        """Summarize a capability limit once per owner, preserving file evidence.

        The summary cites a representative source; other contributing files retain
        independent evidence and provenance inputs. Specific unresolved declarations
        continue through unknown(), with their own locators and diagnostics.
        """
        if self.view.checkpoint():
            raise Stopped
        source = (path, subject, problem)
        if source in self._unsupported_sources:
            return
        self.keep(path, problem)
        self._unsupported_sources.add(source)
        group = (subject, problem)
        digest = hashlib.sha256(problem.encode()).hexdigest()
        locator = "inspection:unsupported:" + digest[:16]
        if group not in self._unsupported_groups:
            self._unsupported_groups.add(group)
            self.unknown(path, locator, subject, problem)
        else:
            self.evidence.append(
                EvidenceCandidate(
                    f"{self.prefix}-declaration",
                    path,
                    locator,
                    "Additional source syntax is outside supported literal boundary inspection.",
                    "bounded literal-only inspection",
                    f"{self.prefix}.inspect.v1",
                    "unsupported-source:" + digest,
                )
            )

    def component(self, path: str, kind: str, key: EvidenceKey) -> None:
        self.keep(path, kind)
        self.components.append(ComponentCandidate(path, kind, (key,), self.prefix))

    def declaration(
        self, path: str, call: Call, subject: str | None, code: str, value: str
    ) -> EvidenceKey:
        suffix = (
            " Source is conditional or deferred; it may not take effect."
            if call.conditional
            else ""
        )
        return self.emit(
            path,
            call.locator + ":" + code,
            subject,
            re.sub(r"\.\d+$", "", code) + ".declaration",
            value + suffix,
        )

    def conflict(self, path: str, subject: str, keys: list[EvidenceKey]) -> None:
        if len(keys) > 1:
            self.unknown(
                path,
                "identity:conflict",
                subject,
                "Competing identity declarations coexist; no effective identity was selected.",
            )

    def owner(self, path: str) -> str | None:
        return nearest_ancestor(
            parent(path), self.context.component_paths | {c.path for c in self.components}
        )

    def member(
        self, path: str, locator: str, source: str | None, target: str | None, valid: set[str]
    ) -> None:
        if source is None or target is None or target not in valid:
            self.unknown(
                path,
                locator,
                source,
                "A declared local reference has no safe, unambiguous established target.",
                reference=source is not None and target is not None,
            )
            return
        key = self.emit(
            path,
            locator,
            source,
            "workspace.member",
            f"Local component {quoted(target)} is declared as a member; effective build inclusion is unknown.",
        )
        if source != target:
            self.keep(path, target)
            self.relationships.append(
                RelationshipCandidate("workspace-member", source, target, "verified", (key,))
            )

    def reference(self, base: str, value: str | None) -> str | None:
        if value is None or not safe_text(value) or any(char in value for char in "\\*?[]"):
            return None
        target = safe_member(base, value)
        # A reference never opens a path outside the already guarded catalog.
        return target

    def result(self) -> DetectionResult:
        self.view.release_memory(self.temporary)
        return DetectionResult(
            tuple(self.evidence),
            tuple(self.components),
            tuple(self.findings),
            tuple(self.diagnostics),
            tuple(self.relationships),
        )
