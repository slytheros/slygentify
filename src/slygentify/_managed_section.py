"""Safe rendering and replacement of the visible Slygentify guidance section."""

from __future__ import annotations

import hashlib

from slygentify._generation import _render_paste_snippet

SECTION_BEGIN = b"<!-- slygentify:begin -->\n"
SECTION_END = b"<!-- slygentify:end -->\n"


class ManagedSectionError(ValueError):
    """A managed guidance section is missing, malformed, or changed."""


def render_managed_section(markdown: str) -> bytes:
    """Return the fixed-marker, visible Markdown section for existing guidance."""
    return SECTION_BEGIN + _render_paste_snippet(markdown).encode("utf-8") + SECTION_END


def section_digest(data: bytes) -> str:
    """Return the canonical digest recorded for one managed section."""
    return hashlib.sha256(data).hexdigest()


def extract_managed_section(data: bytes) -> bytes:
    """Return exactly one complete managed section without reading surrounding text."""
    if data.count(SECTION_BEGIN) != 1 or data.count(SECTION_END) != 1:
        raise ManagedSectionError("managed guidance markers are missing or duplicated")
    start = data.index(SECTION_BEGIN)
    try:
        end = data.index(SECTION_END, start + len(SECTION_BEGIN))
    except ValueError as error:
        raise ManagedSectionError("managed guidance markers are malformed") from error
    return data[start : end + len(SECTION_END)]


def append_managed_section(existing: bytes, section: bytes) -> bytes:
    """Append a section while changing only the separator needed to keep Markdown readable."""
    if not existing:
        return section
    separator = (
        b"" if existing.endswith(b"\n\n") else b"\n" if existing.endswith(b"\n") else b"\n\n"
    )
    return existing + separator + section


def replace_managed_section(existing: bytes, expected_sha256: str, section: bytes) -> bytes:
    """Replace only an unchanged managed section, preserving all surrounding bytes."""
    current = extract_managed_section(existing)
    if section_digest(current) != expected_sha256:
        raise ManagedSectionError("managed guidance section was changed")
    start = existing.index(current)
    return existing[:start] + section + existing[start + len(current) :]
