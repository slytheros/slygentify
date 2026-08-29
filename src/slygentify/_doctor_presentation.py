"""Human-readable presentation for static doctor results."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.text import Text

from slygentify.models import DoctorDiagnostic, DoctorResult, DoctorSeverity
from slygentify.traceability import implements

_SEVERITY_ORDER: tuple[DoctorSeverity, ...] = ("error", "warning", "info")
_SEVERITY_LABELS: dict[DoctorSeverity, str] = {
    "error": "Errors",
    "warning": "Warnings",
    "info": "Information",
}
_SEVERITY_STYLES: dict[DoctorSeverity, str] = {
    "error": "bold red",
    "warning": "bold yellow",
    "info": "bold cyan",
}


def _target(diagnostic: DoctorDiagnostic) -> str:
    if diagnostic.location is not None and diagnostic.subject_id is not None:
        return f"{diagnostic.location} ({diagnostic.subject_id})"
    if diagnostic.location is not None:
        return diagnostic.location
    assert diagnostic.subject_id is not None
    return diagnostic.subject_id


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or f'{singular}s'}"


@implements("REQ049")
def render_doctor_report(
    result: DoctorResult,
    root: Path,
    console: Console,
    *,
    verbose: bool = False,
) -> None:
    """Render a concise report, with optional complete evidence detail."""

    counts = {
        severity: sum(item.severity == severity for item in result.diagnostics)
        for severity in _SEVERITY_ORDER
    }
    console.print("Doctor completed", style="bold")
    console.print(f"Repository: {root}")
    console.print(f"Status: {result.completion.title()} doctor result")
    console.print(
        "Diagnostics: "
        + ", ".join(
            (
                _count_label(counts["error"], "error"),
                _count_label(counts["warning"], "warning"),
                _count_label(counts["info"], "info", "info"),
            )
        )
    )

    if not result.diagnostics:
        console.print("No diagnostics.")
    for severity in _SEVERITY_ORDER:
        diagnostics = tuple(item for item in result.diagnostics if item.severity == severity)
        if not diagnostics:
            continue
        console.print(f"\n{_SEVERITY_LABELS[severity]} ({len(diagnostics)})", style="bold")
        for diagnostic in diagnostics:
            console.print(
                Text(
                    f"{diagnostic.severity.upper()} {diagnostic.classification.upper()} "
                    f"{diagnostic.disposition.upper()} [{diagnostic.code}] {_target(diagnostic)}",
                    style=_SEVERITY_STYLES[severity],
                )
            )
            console.print(f"  Description: {diagnostic.problem}")
            console.print(f"  Effect: {diagnostic.effect}")
            if diagnostic.category is not None:
                console.print(f"  Category: {diagnostic.category}")
            if diagnostic.safety_rationale is not None:
                console.print(f"  Why no automatic repair: {diagnostic.safety_rationale}")
            if diagnostic.remediation is not None:
                console.print(f"  Next: {diagnostic.remediation}")
            if verbose:
                references = ", ".join(diagnostic.evidence_ids) or "none"
                console.print(f"  Evidence IDs: {references}")

    if not verbose:
        return

    console.print(f"\nEvidence ({len(result.evidence)})", style="bold")
    for evidence_item in result.evidence:
        locator = f" [{evidence_item.locator}]" if evidence_item.locator is not None else ""
        console.print(
            Text(
                f"[{evidence_item.id}] {evidence_item.source_kind}: "
                f"{evidence_item.location}{locator}"
            )
        )
        console.print(f"  Observation: {evidence_item.observation}")
        if evidence_item.verification_method is not None:
            console.print(f"  Verification: {evidence_item.verification_method}")

    console.print(f"\nSkipped scopes ({len(result.skipped_scopes)})", style="bold")
    for skipped_item in result.skipped_scopes:
        details = [
            f"reason={skipped_item.reason}",
            f"omitted={skipped_item.omitted_scope}",
        ]
        if skipped_item.effective_limit is not None:
            details.append(f"limit={skipped_item.effective_limit}")
        if skipped_item.consumed is not None:
            details.append(f"consumed={skipped_item.consumed}")
        console.print(f"{skipped_item.scope}: {', '.join(details)}")
