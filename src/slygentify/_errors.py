"""Dependency-neutral exceptions for the supported scan interfaces."""

from slygentify._diagnostics import DiagnosticDetail
from slygentify.traceability import implements


@implements("REQ017")
class ScanError(Exception):
    """An operational failure prevented a repository scan."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "scan.operation-failed",
        target: str = ".",
        category: str | None = None,
        effect: str = "Slygentify did not emit a scan result and did not modify repository files.",
        recovery: str = "Correct the selected input or environment condition, then rerun scan.",
    ) -> None:
        super().__init__(message)
        self.diagnostic = DiagnosticDetail(
            code,
            target,
            "Slygentify could not safely inspect the selected repository.",
            effect,
            category,
            recovery,
            "The command cannot safely guess a repository target or alter repository content to recover.",
            disposition="problem",
        )


@implements("REQ018")
class ScanValidationError(ScanError):
    """An object or JSON document is not a valid scan result."""
