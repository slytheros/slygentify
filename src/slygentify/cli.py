"""Command-line interface for Slygentify."""

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
from slygentify._doctor_presentation import render_doctor_report
from slygentify._explorer import run_scan_explorer
from slygentify._presentation import render_scan_report
from slygentify.initialization import (
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


def _configuration_warning(result: object) -> None:
    diagnostics = getattr(result, "diagnostics", ())
    if any(getattr(item, "code", None) == "configuration.relaxed-limits" for item in diagnostics):
        typer.echo(
            "Warning: slygentify.toml raises or disables one or more inspection limits.",
            err=True,
        )


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

    typer.echo(f"Error [{error.code}]: {error}", err=True)
    if error.changed_locations:
        typer.echo(f"Changed: {', '.join(error.changed_locations)}", err=True)
    typer.echo(f"Next: {error.recovery}", err=True)


@app.command("init")
@implements("REQ003", "REQ004", "REQ040", "REQ044")
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
) -> None:
    """Create or safely regenerate evidence-backed AGENTS.md guidance."""
    try:
        plan = plan_initialization(path, replace=replace)
    except InitializationError as error:
        _render_initialization_error(error)
        raise typer.Exit(code=1) from None
    for warning in plan.warnings:
        typer.echo(f"Warning: {warning}", err=True)

    if dry_run:
        typer.echo(f"Ownership: {plan.ownership}")
        typer.echo(f"AGENTS.md: {plan.agents_action}")
        typer.echo(f".slygentify/state.json: {plan.state_action}")
        typer.echo("\n--- AGENTS.md ---")
        typer.echo(plan.agents_markdown, nl=False)
        typer.echo("--- .slygentify/state.json ---")
        typer.echo(plan.state_json.decode("utf-8"), nl=False)
        if not plan.can_apply:
            for diagnostic in plan.diagnostics:
                typer.echo(f"Error [{diagnostic.code}]: {diagnostic.message}", err=True)
                typer.echo(f"Next: {diagnostic.recovery}", err=True)
            raise typer.Exit(code=1)
        return
    if not plan.can_apply:
        for diagnostic in plan.diagnostics:
            typer.echo(f"Error [{diagnostic.code}]: {diagnostic.message}", err=True)
            typer.echo(f"Next: {diagnostic.recovery}", err=True)
        raise typer.Exit(code=1)
    if replace and plan.agents_action == "replace":
        typer.echo("Warning: --replace discards the existing AGENTS.md without a backup.", err=True)
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
    else:
        typer.echo("Regenerated AGENTS.md and .slygentify/state.json")


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
                _configuration_warning(result)
                return result

            run_scan_explorer(
                root,
                scan,
            )
            return
        result = scan_repository(path, git_executable=git_executable)
        _configuration_warning(result)
        if output_format is _ScanFormat.json:
            sys.stdout.buffer.write(dump_scan_json(result))
            sys.stdout.buffer.flush()
            return
        root = find_git_root(path).resolve(strict=True)
    except (ScanError, InitializationError, OSError) as error:
        typer.echo(f"Error: {error}", err=True)
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
        typer.echo(f"Error: doctor could not assess the selected input: {error}", err=True)
        typer.echo("Next: correct PATH or --git-executable, then rerun doctor.", err=True)
        raise typer.Exit(code=2) from None
    except DoctorOperationalError as error:
        typer.echo(f"Error: doctor could not produce a trustworthy result: {error}", err=True)
        typer.echo("Next: correct the reported environment or tool failure, then retry.", err=True)
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
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None
