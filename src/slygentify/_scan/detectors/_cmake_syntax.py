"""Literal-only CMake syntax inspection; no CMake evaluation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from slygentify.traceability import implements

_BRACKET = re.compile(r"\[(=*)\[")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


class _ParseStopped(Exception):
    """The shared inspection deadline stopped a lexical operation."""


class CMakeSyntaxError(ValueError):
    """Malformed or unsupported command syntax."""


@dataclass(frozen=True, slots=True)
class Argument:
    value: str
    literal: bool


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    arguments: tuple[Argument, ...]
    line: int
    scope: tuple[str, ...]

    @property
    def locator(self) -> str:
        return f"line:{self.line}:{self.name}"


@implements("REQ057")
def commands(text: str, checkpoint: Callable[[], bool]) -> tuple[Command, ...]:
    """Tokenize supported calls, retaining conditional and deferred source context."""
    position = 0
    line = 1
    length = len(text)
    result: list[Command] = []
    scope: list[str] = []
    next_checkpoint = 4096

    def advance(end: int) -> None:
        nonlocal position, line, next_checkpoint
        line += text.count("\n", position, end)
        position = end
        if position >= next_checkpoint:
            next_checkpoint = position + 4096
            if checkpoint():
                raise _ParseStopped

    def bracket() -> str | None:
        match = _BRACKET.match(text, position)
        if match is None:
            return None
        start = position + len(match[0])
        closing = "]" + match[1] + "]"
        end = text.find(closing, start)
        if end < 0:
            raise CMakeSyntaxError("unterminated bracket")
        value = text[start:end]
        advance(end + len(closing))
        return value

    def whitespace() -> None:
        while position < length:
            if text[position].isspace():
                advance(position + 1)
            elif text[position] == "#":
                advance(position + 1)
                if bracket() is None:
                    end = text.find("\n", position)
                    advance(length if end < 0 else end)
            else:
                break

    try:
        while position < length:
            if checkpoint():
                break
            whitespace()
            if position == length:
                break
            match = _IDENTIFIER.match(text, position)
            if match is None:
                raise CMakeSyntaxError("expected command")
            name = match[0].casefold()
            command_line = line
            advance(position + len(match[0]))
            whitespace()
            if position == length or text[position] != "(":
                raise CMakeSyntaxError("expected opening parenthesis")
            advance(position + 1)
            arguments: list[Argument] = []
            depth = 0
            while True:
                if checkpoint():
                    return tuple(result)
                whitespace()
                if position == length:
                    raise CMakeSyntaxError("unterminated command")
                char = text[position]
                if char == ")" and depth == 0:
                    advance(position + 1)
                    break
                if char in "()":
                    depth += 1 if char == "(" else -1
                    arguments.append(Argument(char, False))
                    advance(position + 1)
                    continue
                value = bracket()
                if value is not None:
                    arguments.append(Argument(value.removeprefix("\n"), True))
                    continue
                quoted = char == '"'
                if quoted:
                    advance(position + 1)
                parts: list[str] = []
                literal = True
                while position < length:
                    char = text[position]
                    if quoted and char == '"':
                        advance(position + 1)
                        break
                    if not quoted and (char.isspace() or char in "()#"):
                        break
                    if char == "\\":
                        advance(position + 1)
                        if position == length:
                            raise CMakeSyntaxError("unterminated escape")
                        char = text[position]
                        if char != "\n":
                            parts.append({"n": "\n", "t": "\t", "r": "\r"}.get(char, char))
                    else:
                        if char == "$" or (not quoted and char in ';"'):
                            literal = False
                        parts.append(char)
                    advance(position + 1)
                else:
                    if quoted:
                        raise CMakeSyntaxError("unterminated quote")
                arguments.append(Argument("".join(parts), literal))
            if name in {"endif", "endforeach", "endwhile", "endfunction", "endmacro", "endblock"}:
                expected = name[3:]
                if not scope or scope[-1] != expected:
                    raise CMakeSyntaxError("unbalanced control block")
                scope.pop()
            elif name in {"else", "elseif"}:
                if not scope or scope[-1] != "if":
                    raise CMakeSyntaxError("unbalanced conditional branch")
            result.append(Command(name, tuple(arguments), command_line, tuple(scope)))
            if name in {"if", "foreach", "while", "function", "macro", "block"}:
                scope.append(name)
        if scope and not checkpoint():
            raise CMakeSyntaxError("unterminated control block")
    except _ParseStopped:
        return tuple(result)
    return tuple(result)
