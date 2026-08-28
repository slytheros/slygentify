"""Command-line interface for Slygentify."""

import hashlib
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console

from slygentify import (
    DoctorInputError,
    DoctorOperationalError,
    ScanError,
    ScanResult,
    doctor_repository,
    dump_doctor_json,
    dump_scan_json,
    dump_scan_projection_json,
    map_repository,
    scan_repository,
)
from slygentify._diagnostics import DiagnosticDetail, render_diagnostic
from slygentify._doctor_presentation import render_doctor_report
from slygentify._explorer import run_scan_explorer
from slygentify._generation import _render_paste_snippet
from slygentify._presentation import render_scan_report
from slygentify._provenance import load_state_json
from slygentify._repository import AGENTS_FILENAME
from slygentify.initialization import (
    InitializationDiagnostic,
    InitializationError,
    apply_initialization,
    find_git_root,
    plan_initialization,
)
from slygentify.traceability import implements

app = typer.Typer(
    add_completion=False,
    help="Make repositories easier and safer for coding agents to operate.",
    no_args_is_help=True,
)


@app.callback()
def cli() -> None:
    """Operate Slygentify's repository-local commands."""


class _ScanFormat(StrEnum):
    text = "text"
    json = "json"


class _DoctorFormat(StrEnum):
    text = "text"
    json = "json"


class _MapSection(StrEnum):
    orientation = "orientation"
    workflows = "workflows"
    architecture = "architecture"
    automation = "automation"
    boundaries = "boundaries"


def _is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _map_limit(value: str) -> int | Literal["unlimited"]:
    if value == "unlimited":
        return "unlimited"
    try:
        parsed = int(value)
    except ValueError:
        raise typer.BadParameter("must be a positive integer or 'unlimited'") from None
    if parsed <= 0 or str(parsed) != value:
        raise typer.BadParameter("must be a positive integer or 'unlimited'")
    return parsed


def _render_initialization_error(error: InitializationError) -> None:
    """Render every initialization failure with one recovery and exact changed paths."""

    typer.echo(render_diagnostic(error.diagnostic, "Error"), err=True)
    if error.changed_locations:
        typer.echo(f"Changed: {', '.join(error.changed_locations)}", err=True)


def _render_initialization_diagnostic(
    diagnostic: InitializationDiagnostic, severity: str = "Error"
) -> None:
    """Render a structured initialization refusal without duplicating recovery text."""

    detail = DiagnosticDetail(
        diagnostic.code,
        diagnostic.target,
        diagnostic.problem or diagnostic.message,
        diagnostic.effect or "Initialization did not apply the requested repository change.",
        diagnostic.category,
        diagnostic.recovery,
        diagnostic.safety_rationale,
        disposition=diagnostic.disposition,
    )
    typer.echo(render_diagnostic(detail, severity), err=True)


def _requires_manual_paste(ownership: str, replace_requested: bool) -> bool:
    """Return whether a safe existing artifact needs user-directed incorporation."""
    return not replace_requested and ownership in {"unmanaged", "human-edited"}


def _render_manual_paste(markdown: str) -> None:
    """Print paste guidance without presenting existing user content as an error."""
    typer.echo("Existing AGENTS.md was preserved. Paste this section into that file:")
    typer.echo("\n--- Paste into AGENTS.md ---")
    typer.echo(_render_paste_snippet(markdown), nl=False)


def _render_state_summary(data: bytes) -> None:
    """Render stable provenance-review facts without dumping all evidence by default."""
    state = load_state_json(data)
    typer.echo("--- provenance summary ---")
    typer.echo(
        f"state-v{state.schema_version}: {len(data)} bytes; sha256: "
        f"{hashlib.sha256(data).hexdigest()}"
    )
    typer.echo(
        f"inputs: {len(state.inputs)}; completion: {state.completion}; "
        f"skipped scopes: {len(state.skipped_scopes)}"
    )


def _render_lifecycle_next() -> None:
    typer.echo(
        "Next: run read-only 'slygentify doctor .' after structural, tooling, or workflow "
        "changes, or in existing CI."
    )


@app.command("init")
@implements("REQ003", "REQ004", "REQ040", "REQ044", "REQ053")
def init_command(
    path: Annotated[
        Path,
        typer.Argument(help="A directory inside the Git repository to initialize."),
    ] = Path("."),
    *,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the exact proposed artifacts without writing them."),
    ] = False,
    replace: Annotated[
        bool,
        typer.Option("--replace", help="Explicitly replace an existing regular AGENTS.md."),
    ] = False,
    adopt: Annotated[
        bool,
        typer.Option(
            "--adopt", help="Append and manage a visible Slygentify section in unmanaged AGENTS.md."
        ),
    ] = False,
    show_state: Annotated[
        bool,
        typer.Option(
            "--show-state", help="With --dry-run, print the exact proposed provenance JSON."
        ),
    ] = False,
) -> None:
    """Create or safely regenerate evidence-backed AGENTS.md guidance."""
    try:
        if show_state and not dry_run:
            raise typer.BadParameter("--show-state requires --dry-run")
        if adopt and replace:
            raise typer.BadParameter("--adopt cannot be combined with --replace")
        plan = plan_initialization(path, replace=replace, adopt=adopt)
    except InitializationError as error:
        _render_initialization_error(error)
        raise typer.Exit(code=1) from None
    for warning in plan.warnings:
        typer.echo(
            render_diagnostic(
                DiagnosticDetail(
                    "initialization.relaxed-limits",
                    "slygentify.toml",
                    warning,
                    "Initialization used explicitly expanded generated-guidance limits.",
                    recovery="Review the expanded limits and restore the defaults if they were unintended.",
                    safety_rationale="Slygentify does not silently change an explicit repository configuration.",
                    disposition="notice",
                ),
                "Warning",
            ),
            err=True,
        )
    manual_paste = not adopt and _requires_manual_paste(plan.ownership, replace)
    if manual_paste:
        for diagnostic in plan.diagnostics:
            _render_initialization_diagnostic(diagnostic, severity="Notice")

    if dry_run:
        typer.echo(f"Ownership: {plan.ownership}")
        typer.echo(f"AGENTS.md: {plan.agents_action}")
        typer.echo(f".slygentify/state.json: {plan.state_action}")
        if plan.managed_section is None:
            typer.echo("\n--- AGENTS.md ---")
            typer.echo(plan.agents_markdown, nl=False)
        else:
            typer.echo("\n--- Slygentify bootstrap guidance ---")
            typer.echo(plan.managed_section, nl=False)
        if not plan.can_apply and not manual_paste:
            for diagnostic in plan.diagnostics:
                _render_initialization_diagnostic(diagnostic)
            raise typer.Exit(code=1)
        _render_state_summary(plan.state_json)
        if show_state:
            typer.echo("--- .slygentify/state.json ---")
            typer.echo(plan.state_json.decode("utf-8"), nl=False)
        if manual_paste:
            raise typer.Exit(code=4)
        return
    if manual_paste:
        _render_manual_paste(plan.agents_markdown)
        raise typer.Exit(code=4)
    if not plan.can_apply:
        for diagnostic in plan.diagnostics:
            _render_initialization_diagnostic(diagnostic)
        raise typer.Exit(code=1)
    if replace and plan.agents_action == "replace":
        typer.echo(
            render_diagnostic(
                DiagnosticDetail(
                    "initialization.replace-without-backup",
                    AGENTS_FILENAME,
                    "The explicit replacement will discard the existing regular AGENTS.md.",
                    "Slygentify will not create a backup or merge the existing guidance.",
                    recovery=None,
                    safety_rationale="The caller explicitly authorized replacement, but Slygentify cannot decide which human guidance should be retained.",
                    disposition="notice",
                ),
                "Warning",
            ),
            err=True,
        )
    try:
        result = apply_initialization(plan)
    except InitializationError as error:
        _render_initialization_error(error)
        raise typer.Exit(code=1) from None
    if not result.changed_locations:
        typer.echo("No changes.")
    elif result.ownership == "recoverable-state":
        typer.echo("Repaired .slygentify/state.json")
    elif result.agents_action == "create":
        typer.echo("Created AGENTS.md and .slygentify/state.json")
    elif adopt:
        typer.echo("Adopted Slygentify bootstrap guidance and .slygentify/state.json")
    else:
        typer.echo("Regenerated AGENTS.md and .slygentify/state.json")
    _render_lifecycle_next()


@app.command("scan")
@implements("REQ019", "REQ033")
def scan_command(
    path: Annotated[
        Path,
        typer.Argument(help="A directory inside the Git repository to inspect."),
    ] = Path("."),
    *,
    output_format: Annotated[
        _ScanFormat,
        typer.Option("--format", help="Choose human-readable text or canonical JSON."),
    ] = _ScanFormat.text,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", help="Explore the scan in a full-screen terminal UI."),
    ] = False,
    git_executable: Annotated[
        Path | None,
        typer.Option(
            "--git-executable",
            help="Trusted code: not sandboxed; arbitrary effects are possible.",
        ),
    ] = None,
) -> None:
    """Inspect a repository locally without changing it."""
    if interactive and output_format is _ScanFormat.json:
        raise typer.BadParameter("--interactive is only available with --format text")
    if interactive and not _is_interactive_terminal():
        raise typer.BadParameter(
            "--interactive requires an interactive input and output terminal; "
            "use the default report or --format json"
        )
    try:
        if interactive:
            root = find_git_root(path).resolve(strict=True)

            def scan() -> ScanResult:
                result = scan_repository(path, git_executable=git_executable)
                return result

            run_scan_explorer(
                root,
                scan,
            )
            return
        result = scan_repository(path, git_executable=git_executable)
        if output_format is _ScanFormat.json:
            sys.stdout.buffer.write(dump_scan_json(result))
            sys.stdout.buffer.flush()
            return
        root = find_git_root(path).resolve(strict=True)
    except (ScanError, InitializationError, OSError) as error:
        detail = (
            error.diagnostic
            if hasattr(error, "diagnostic")
            else DiagnosticDetail(
                "scan.operation-failed",
                ".",
                "Slygentify could not safely inspect the selected repository.",
                "Slygentify did not emit a scan result and did not modify repository files.",
                recovery="Correct the selected input or environment condition, then rerun scan.",
                disposition="problem",
            )
        )
        typer.echo(render_diagnostic(detail, "Error"), err=True)
        raise typer.Exit(code=1) from None
    render_scan_report(
        result,
        root,
        Console(file=sys.stdout, force_terminal=None, highlight=False, markup=False),
    )


@app.command("doctor")
@implements("REQ049")
def doctor_command(
    path: Annotated[
        Path,
        typer.Argument(help="A directory inside the Git repository to assess."),
    ] = Path("."),
    *,
    output_format: Annotated[
        _DoctorFormat,
        typer.Option("--format", help="Choose concise text or canonical JSON."),
    ] = _DoctorFormat.text,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Include complete evidence and skipped-scope detail."),
    ] = False,
    git_executable: Annotated[
        Path | None,
        typer.Option(
            "--git-executable",
            help="Trusted code: not sandboxed; arbitrary effects are possible.",
        ),
    ] = None,
) -> None:
    """Assess managed repository knowledge without changing or executing it."""

    if verbose and output_format is _DoctorFormat.json:
        raise typer.BadParameter("--verbose is only available with --format text")
    try:
        result = doctor_repository(path, git_executable=git_executable)
    except DoctorInputError as error:
        typer.echo(render_diagnostic(error.diagnostic, "Error"), err=True)
        raise typer.Exit(code=2) from None
    except DoctorOperationalError as error:
        typer.echo(render_diagnostic(error.diagnostic, "Error"), err=True)
        raise typer.Exit(code=3) from None

    has_actionable_diagnostic = any(
        item.severity in {"warning", "error"} for item in result.diagnostics
    )
    if output_format is _DoctorFormat.json:
        try:
            sys.stdout.buffer.write(dump_doctor_json(result))
            sys.stdout.buffer.flush()
        except (DoctorInputError, DoctorOperationalError) as error:
            typer.echo(f"Error: doctor result serialization failed: {error}", err=True)
            raise typer.Exit(code=3) from None
    else:
        try:
            root = find_git_root(path).resolve(strict=True)
            render_doctor_report(
                result,
                root,
                Console(file=sys.stdout, force_terminal=None, highlight=False, markup=False),
                verbose=verbose,
            )
        except (InitializationError, OSError) as error:
            typer.echo(f"Error: doctor report rendering failed: {error}", err=True)
            raise typer.Exit(code=3) from None
    if has_actionable_diagnostic:
        raise typer.Exit(code=1)


@app.command("map")
@implements("REQ043")
def map_command(
    path: Annotated[
        Path,
        typer.Argument(help="A directory inside the Git repository to inspect."),
    ] = Path("."),
    *,
    scope: Annotated[
        str,
        typer.Option("--scope", help="Safe repository-relative POSIX logical path."),
    ] = ".",
    sections: Annotated[
        list[_MapSection] | None,
        typer.Option("--section", help="Projection section; repeat to select several."),
    ] = None,
    max_bytes: Annotated[
        str,
        typer.Option("--max-bytes", help="Canonical JSON bytes or 'unlimited'."),
    ] = str(8 * 1024),
    git_executable: Annotated[
        Path | None,
        typer.Option(
            "--git-executable",
            help="Trusted code: not sandboxed; arbitrary effects are possible.",
        ),
    ] = None,
) -> None:
    """Emit fresh bounded operating context for one logical repository path."""
    limit = _map_limit(max_bytes)
    try:
        projection = map_repository(
            path,
            scope=scope,
            sections=None if sections is None else tuple(item.value for item in sections),
            max_bytes=limit,
            git_executable=git_executable,
        )
        sys.stdout.buffer.write(dump_scan_projection_json(projection))
        sys.stdout.buffer.flush()
    except ScanError as error:
        detail = error.diagnostic
        if detail.code == "scan.operation-failed":
            detail = DiagnosticDetail(
                "map.operation-failed",
                detail.target,
                "Slygentify could not produce bounded context for the selected scope.",
                "Slygentify did not emit a map result and did not modify repository files.",
                recovery="Correct the selected repository or scope, then rerun map.",
                safety_rationale="Map is read-only and cannot safely change repository content to resolve an invalid scope.",
                disposition="problem",
            )
        typer.echo(render_diagnostic(detail, "Error"), err=True)
        raise typer.Exit(code=1) from None
