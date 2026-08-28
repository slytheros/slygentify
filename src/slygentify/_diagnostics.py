"""Shared safe diagnostic structure and human-readable rendering."""

from __future__ import annotations

from dataclasses import dataclass

from slygentify.models import DiagnosticDisposition


def _sentence(value: str) -> str:
    """Normalize one concise diagnostic sentence."""

    normalized = " ".join(value.split()).rstrip(".")
    if not normalized:
        raise ValueError("diagnostic text must not be empty")
    return f"{normalized}."


def compose_message(problem: str, effect: str, recovery: str | None = None) -> str:
    """Return the compatible single-message representation of a diagnostic."""

    message = f"{_sentence(problem)} {_sentence(effect)}"
    if recovery is not None:
        message = f"{message} Next: {_sentence(recovery)}"
    return message


@dataclass(frozen=True, slots=True)
class DiagnosticDetail:
    """Safe, command-neutral content for a diagnostic."""

    code: str
    target: str
    problem: str
    effect: str
    category: str | None = None
    recovery: str | None = None
    safety_rationale: str | None = None
    disposition: DiagnosticDisposition = "problem"

    def __post_init__(self) -> None:
        required: tuple[tuple[str, str], ...] = (
            ("code", self.code),
            ("target", self.target),
            ("problem", self.problem),
            ("effect", self.effect),
        )
        for name, value in required:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"diagnostic {name} must not be empty")
        optional: tuple[tuple[str, str | None], ...] = (
            ("category", self.category),
            ("recovery", self.recovery),
            ("safety_rationale", self.safety_rationale),
        )
        for optional_name, optional_value in optional:
            if optional_value is not None and (
                not isinstance(optional_value, str) or not optional_value.strip()
            ):
                raise ValueError(f"diagnostic {optional_name} must not be empty when present")
        if self.disposition not in {"problem", "limitation", "notice"}:
            raise ValueError("diagnostic disposition is not supported")

    @property
    def message(self) -> str:
        return compose_message(self.problem, self.effect, self.recovery)


def render_diagnostic(detail: DiagnosticDetail, severity: str) -> str:
    """Render a plain-text diagnostic without host or untrusted content."""

    lines = [f"{severity} [{detail.code}] {detail.target}"]
    lines.append(f"Disposition: {detail.disposition.title()}")
    if detail.category is not None:
        lines.append(f"Category: {detail.category}")
    lines.extend(
        (f"Description: {_sentence(detail.problem)}", f"Effect: {_sentence(detail.effect)}")
    )
    if detail.safety_rationale is not None:
        lines.append(f"Why no automatic repair: {_sentence(detail.safety_rationale)}")
    if detail.recovery is not None:
        lines.append(f"Next: {_sentence(detail.recovery)}")
    return "\n".join(lines)
