"""Dependency-neutral exceptions for the supported scan interfaces."""

from slygentify.traceability import implements


@implements("REQ017")
class ScanError(Exception):
    """An operational failure prevented a repository scan."""


@implements("REQ018")
class ScanValidationError(ScanError):
    """An object or JSON document is not a valid scan result."""
