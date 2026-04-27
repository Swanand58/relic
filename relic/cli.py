"""Relic CLI — entry point for all commands."""

import subprocess
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from relic import __version__
from relic.generator import emit_refresh_all, emit_refresh_prompt
from relic.loader import load_and_copy
from relic.staleness import check_all_staleness, is_stale

app = typer.Typer(
    name="relic",
    help="Codebase knowledge management — load and refresh graph.md knowledge files.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()

# Paths resolved relative to the working directory where relic is invoked.
CONFIG_FILE = Path("relic.yaml")
KNOWLEDGE_DIR = Path(".knowledge")


def _load_config() -> dict:
    """Read and parse relic.yaml from the current working directory.

    Exits with an error message if the file is missing or malformed.
    """
    if not CONFIG_FILE.exists():
        console.print(
            f"[bold red]Error:[/bold red] {CONFIG_FILE} not found. "
            "Create one in the project root to use relic."
        )
        raise SystemExit(1)
    try:
        with CONFIG_FILE.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        console.print(f"[bold red]Error parsing {CONFIG_FILE}:[/bold red] {exc}")
        raise SystemExit(1)

    if not cfg or "subprojects" not in cfg:
        console.print(f"[bold red]Error:[/bold red] {CONFIG_FILE} must contain a 'subprojects' key.")
        raise SystemExit(1)

    return cfg


def _validate_names(names: tuple[str, ...], subprojects: dict) -> None:
    """Exit with an error if any name is not defined in relic.yaml."""
    unknown = [n for n in names if n not in subprojects]
    if unknown:
        console.print(
            f"[bold red]Unknown subproject(s):[/bold red] {', '.join(unknown)}\n"
            f"Defined: {', '.join(subprojects.keys())}"
        )
        raise SystemExit(1)


@app.command(name="update")
def update() -> None:
    """Pull the latest version of relic from GitHub and reinstall."""
    console.print("[bold cyan]Updating relic…[/bold cyan]")
    result = subprocess.run(
        [
            "uv", "tool", "install",
            "--reinstall",
            "git+https://github.com/Swanand58/relic",
        ],
        text=True,
    )
    if result.returncode != 0:
        console.print("[bold red]Update failed.[/bold red] Is uv installed and on PATH?")
        raise SystemExit(result.returncode)
    console.print("[bold green]relic updated.[/bold green]")


@app.command(name="load", hidden=True)
def _load_placeholder() -> None:  # pragma: no cover
    """Internal placeholder — not used directly."""
    pass


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    subprojects_arg: Optional[list[str]] = typer.Argument(
        None,
        help="One or more subproject names to load into a session prompt.",
        metavar="SUBPROJECT...",
    ),
    list_all: bool = typer.Option(False, "--list", "-l", help="List all defined subprojects."),
    refresh: Optional[list[str]] = typer.Option(
        None,
        "--refresh",
        "-r",
        help="Emit a graph.md generation prompt to stdout for the active coding agent to execute. Pass subproject name(s) or omit for all.",
        metavar="NAME",
    ),
    stale: bool = typer.Option(False, "--stale", "-s", help="Check which graphs are stale."),
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit."),
) -> None:
    """Relic — load knowledge graphs into your clipboard for AI coding sessions."""
    if version:
        console.print(f"relic {__version__}")
        return

    cfg = _load_config()
    all_subprojects: dict = cfg["subprojects"]

    # --list
    if list_all:
        table = Table(title="Subprojects", show_lines=True)
        table.add_column("Name", style="bold cyan")
        table.add_column("Path")
        table.add_column("Description")
        for name, info in all_subprojects.items():
            table.add_row(name, info.get("path", ""), info.get("description", ""))
        console.print(table)
        return

    # --stale
    if stale:
        results = check_all_staleness(all_subprojects, KNOWLEDGE_DIR)
        table = Table(title="Staleness Check", show_lines=True)
        table.add_column("Subproject", style="bold")
        table.add_column("Stale", justify="center")
        table.add_column("Reason")
        for r in results:
            stale_label = "[red]yes[/red]" if r["stale"] else "[green]no[/green]"
            table.add_row(r["name"], stale_label, r["reason"])
        console.print(table)
        return

    # --refresh [NAME ...]
    if refresh is not None:
        if refresh:
            _validate_names(tuple(refresh), all_subprojects)
            for name in refresh:
                emit_refresh_prompt(name, all_subprojects[name], KNOWLEDGE_DIR)
        else:
            emit_refresh_all(all_subprojects, KNOWLEDGE_DIR)
        return

    # positional: relic payments [api ...]
    if subprojects_arg:
        _validate_names(tuple(subprojects_arg), all_subprojects)
        load_and_copy(list(subprojects_arg), KNOWLEDGE_DIR)
        return

    # no args — typer prints help automatically via no_args_is_help=True
    console.print(ctx.get_help())
