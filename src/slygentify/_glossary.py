"""Shared user-facing definitions for scan presentation terms."""

from __future__ import annotations

from dataclasses import dataclass

from slygentify.traceability import implements


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    """One concise term and its contextual explanation."""

    key: str
    label: str
    short: str
    detail: str


GLOSSARY: tuple[GlossaryEntry, ...] = (
    GlossaryEntry(
        "verified",
        "VERIFIED",
        "Directly supported",
        "Directly supported by repository content that Slygentify inspected.",
    ),
    GlossaryEntry(
        "inferred",
        "INFERRED",
        "Derived from inspected sources",
        "Derived deterministically from inspected sources, but not stated directly.",
    ),
    GlossaryEntry(
        "recommended",
        "RECOMMENDED",
        "A suggested next step",
        "A suggested improvement or next step, not a claim about current repository state.",
    ),
    GlossaryEntry(
        "unknown",
        "UNKNOWN",
        "Not established",
        "The available safe inspection did not establish the answer.",
    ),
    GlossaryEntry(
        "complete",
        "Complete scan",
        "Finished within current support",
        "Inspection finished within current ecosystem support and resource limits; it does not mean every repository fact is known.",
    ),
    GlossaryEntry(
        "partial",
        "Partial scan",
        "Some safe inspection was incomplete",
        "The scan succeeded, but one or more scopes were skipped, limited, or could not be inspected safely.",
    ),
    GlossaryEntry(
        "component",
        "Component",
        "A development unit",
        "An evidence-backed development unit such as a package, application, or workspace member.",
    ),
    GlossaryEntry(
        "relationship",
        "Relationship",
        "A connection between components",
        "A directed, classified connection such as containment or workspace membership.",
    ),
    GlossaryEntry(
        "finding",
        "Finding",
        "A classified conclusion",
        "A verified, inferred, recommended, or unknown conclusion about the repository.",
    ),
    GlossaryEntry(
        "diagnostic",
        "Diagnostic",
        "A condition encountered while inspecting",
        "A diagnostic is a problem, a trustworthy limitation, or a noteworthy notice. Its "
        "disposition is independent of claim classification, completion, and exit status.",
    ),
    GlossaryEntry(
        "skipped-scope",
        "Inspection boundary",
        "An area not fully inspected",
        "A repository area omitted because of a safety rule, unsupported input, or resource limit.",
    ),
    GlossaryEntry(
        "source",
        "Source & provenance",
        "Why Slygentify made a claim",
        "An inspected location and observation supporting a claim. A locator identifies the relevant part within that location.",
    ),
)

_BY_KEY = {entry.key: entry for entry in GLOSSARY}


def glossary_entry(key: str) -> GlossaryEntry:
    """Return one shared definition by its stable presentation key."""
    return _BY_KEY[key]


@implements("REQ019")
def compact_claim_guide() -> str:
    """Return the compact claim legend used by non-interactive output."""
    return " | ".join(
        f"{glossary_entry(key).label} = {glossary_entry(key).short.casefold()}"
        for key in ("verified", "inferred", "recommended", "unknown")
    )


@implements("REQ033")
def glossary_text() -> str:
    """Return the complete glossary for the interactive help overlay."""
    lines = ["Slygentify terms", ""]
    lines.extend(f"{entry.label} — {entry.detail}" for entry in GLOSSARY)
    lines.extend(["", "Press Escape, ?, or q to close."])
    return "\n".join(lines)
