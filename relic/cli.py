"""Relic CLI — entry point for all commands."""

import subprocess
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from relic import __version__
from relic.agent_config import AGENTS, init_agent, init_all_agents
from relic.discovery import discover_subprojects
from relic.indexer import load_graph, run_index
from relic.mcp_server import run as run_mcp
from relic.toon import full_index_to_toon, subgraph_to_toon

app = typer.Typer(
    name="relic",
    help="Codebase knowledge management — build and query a static knowledge graph.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)

# Paths resolved relative to the working directory where relic is invoked.
CONFIG_FILE = Path("relic.yaml")
KNOWLEDGE_DIR = Path(".knowledge")
PROJECT_ROOT = Path.cwd()


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



def _add_to_gitignore(project_root: Path, entries: list[str]) -> None:
    """Append entries to .gitignore if not already present."""
    gitignore = project_root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    additions = [e for e in entries if e not in existing]
    if additions:
        block = "\n# relic\n" + "\n".join(additions) + "\n"
        with gitignore.open("a", encoding="utf-8") as f:
            f.write(block)
        console.print(f"[dim]Added to .gitignore: {', '.join(additions)}[/dim]")


@app.command(name="init")
def project_init() -> None:
    """Auto-discover subprojects and generate relic.yaml. Adds relic entries to .gitignore."""
    if CONFIG_FILE.exists():
        console.print(
            f"[yellow]relic.yaml already exists.[/yellow] Delete it first to re-initialise."
        )
        raise SystemExit(1)

    console.print("[bold cyan]Discovering subprojects…[/bold cyan]")
    subprojects = discover_subprojects(PROJECT_ROOT)

    if not subprojects:
        console.print(
            "[bold red]No subprojects found.[/bold red] "
            "Create relic.yaml manually and define your subprojects."
        )
        raise SystemExit(1)

    # Write relic.yaml
    config = {"subprojects": subprojects}
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Show what was found
    table = Table(title="Discovered Subprojects", show_lines=True)
    table.add_column("Name", style="bold cyan")
    table.add_column("Path")
    table.add_column("Description")
    for name, info in subprojects.items():
        table.add_row(name, info["path"], info["description"])
    console.print(table)

    # Gitignore relic artifacts — relic.yaml is personal config, not project config
    _add_to_gitignore(PROJECT_ROOT, ["relic.yaml", ".knowledge/"])

    console.print(f"\n[green]✓[/green] [bold]relic.yaml[/bold] created with {len(subprojects)} subproject(s).")
    console.print("[dim]relic.yaml and .knowledge/ added to .gitignore — these are local to your machine.[/dim]")
    console.print("\nNext steps:")
    console.print("  [cyan]relic --init claude[/cyan]   — set up Claude Code integration")
    console.print("  [cyan]relic index[/cyan]            — build knowledge graph from source")


@app.command(name="index")
def index_cmd() -> None:
    """Build the knowledge graph by statically analysing all subproject source files.

    No LLM involved. Writes .knowledge/index.pkl (NetworkX graph).
    Run this after relic init, and again whenever the codebase changes significantly.
    """
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]Indexing codebase…[/bold cyan]"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("", total=None)
            G = run_index(PROJECT_ROOT, KNOWLEDGE_DIR, CONFIG_FILE)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1)

    file_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file")
    symbol_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "symbol")
    edge_count = G.number_of_edges()

    table = Table(title="Index Summary", show_lines=True)
    table.add_column("Metric")
    table.add_column("Count", justify="right", style="cyan")
    table.add_row("Files indexed", str(file_count))
    table.add_row("Symbols extracted", str(symbol_count))
    table.add_row("Edges (imports/defines/extends)", str(edge_count))
    console.print(table)
    console.print(f"\n[green]✓[/green] Index saved to [dim]{KNOWLEDGE_DIR / 'index.pkl'}[/dim]")

    # Also write full TOON index for human inspection
    toon_path = KNOWLEDGE_DIR / "index.toon"
    toon_path.write_text(full_index_to_toon(G), encoding="utf-8")
    console.print(f"[green]✓[/green] TOON index saved to [dim]{toon_path}[/dim]")


@app.command(name="query")
def query_cmd(
    target: str = typer.Argument(..., help="File path or symbol name to query.", metavar="FILE_OR_SYMBOL"),
    depth: int = typer.Option(2, "--depth", "-d", help="Graph traversal depth (hops)."),
) -> None:
    """Query the knowledge graph for a file or symbol and print a TOON context subgraph.

    Coding agents run this before editing a file to get precise, token-efficient context.
    Output goes to stdout — agents read it directly from the bash tool result.
    """
    try:
        G = load_graph(KNOWLEDGE_DIR)
    except FileNotFoundError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1)

    # Normalise path — try multiple forms to match node IDs.
    # Hooks pass absolute paths; index stores relative paths from project root.
    target_norm = target.lstrip("./")
    candidates = [target, target_norm, str(Path(target))]
    abs_target = Path(target)
    if abs_target.is_absolute():
        try:
            candidates.append(str(abs_target.relative_to(PROJECT_ROOT)))
        except ValueError:
            pass

    node_id = None
    for candidate in candidates:
        if candidate in G.nodes:
            node_id = candidate
            break

    # If not found as file, try as symbol name
    if node_id is None:
        for n, d in G.nodes(data=True):
            if d.get("ntype") == "symbol" and d.get("name") == target:
                node_id = n
                break

    if node_id is None:
        console.print(f"[bold red]Not found:[/bold red] '{target}' not in index. Run `relic index` first.")
        raise SystemExit(1)

    # BFS traversal up to `depth` hops (both directions)
    neighbours = {node_id}
    frontier = {node_id}
    for _ in range(depth):
        next_frontier = set()
        for n in frontier:
            next_frontier.update(G.predecessors(n))
            next_frontier.update(G.successors(n))
        next_frontier -= neighbours
        neighbours.update(next_frontier)
        frontier = next_frontier

    subgraph = G.subgraph(neighbours)

    # Collect typed lists
    file_nodes = [d for _, d in subgraph.nodes(data=True) if d.get("ntype") == "file"]
    symbol_nodes = [d for _, d in subgraph.nodes(data=True) if d.get("ntype") == "symbol"]
    import_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "imports"]
    define_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "defines"]
    extends_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "extends"]

    toon = subgraph_to_toon(
        focus_path=node_id,
        file_nodes=file_nodes,
        symbol_nodes=symbol_nodes,
        import_edges=import_edges,
        define_edges=define_edges,
        extends_edges=extends_edges,
    )

    # Stdout — agent reads this directly
    print(toon)

    # Status — goes to stderr so it doesn't pollute stdout
    err_console.print(
        f"[dim]query: {node_id} | {len(file_nodes)} files, {len(symbol_nodes)} symbols, depth={depth}[/dim]"
    )


@app.command(name="mcp")
def mcp_cmd() -> None:
    """Start the relic MCP server (stdio transport).

    Exposes relic_query as a native tool so agents call it directly
    instead of running a shell command. Configure in .claude/settings.json:

        "mcpServers": { "relic": { "command": "relic", "args": ["mcp"] } }
    """
    run_mcp()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    list_all: bool = typer.Option(False, "--list", "-l", help="List all defined subprojects."),
    init: Optional[str] = typer.Option(None, "--init", "-i", help=f"Write relic instructions into agent config file. Pass agent name ({', '.join(AGENTS)}) or 'all'.", metavar="AGENT"),
    update: bool = typer.Option(False, "--update", "-u", help="Pull latest from GitHub (main branch) and reinstall."),
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit."),
) -> None:
    """Relic — build and query a static knowledge graph for AI coding agents."""
    if version:
        console.print(f"relic {__version__}")
        return

    if init is not None:
        if init == "all":
            init_all_agents(PROJECT_ROOT)
        elif init in AGENTS:
            init_agent(init, PROJECT_ROOT)
        else:
            console.print(
                f"[bold red]Unknown agent:[/bold red] '{init}'\n"
                f"Choose from: {', '.join(AGENTS)} or 'all'"
            )
            raise SystemExit(1)
        return

    if update:
        console.print("[bold cyan]Updating relic from main…[/bold cyan]")
        result = subprocess.run(
            [
                "uv", "tool", "install",
                "--reinstall",
                "git+https://github.com/Swanand58/relic@main",
            ],
            text=True,
        )
        if result.returncode != 0:
            console.print("[bold red]Update failed.[/bold red] Is uv installed and on PATH?")
            raise SystemExit(result.returncode)
        console.print("[bold green]relic updated.[/bold green]")
        return

    # Subcommands (init, index, query, mcp) are routed by Click before this callback.
    if ctx.invoked_subcommand is not None:
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

    # no args — typer prints help automatically via no_args_is_help=True
    console.print(ctx.get_help())
