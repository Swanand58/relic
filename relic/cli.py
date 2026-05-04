"""Relic CLI — entry point for all commands.

Visual style is centralised in `relic.style`: any header, success/error glyph,
table layout, or spinner pulse comes from there. Touch `style.py` to retheme
the whole tool.
"""

import subprocess
from pathlib import Path
from typing import Optional

import typer
import yaml

from relic import __version__, style
from relic.agent_config import AGENTS, init_agent, init_all_agents
from relic.audit import compute_audit, render_audit
from relic.benchmark import run_benchmark
from relic.coverage import compute_coverage, render_coverage
from relic.diff import compute_diff, diff_to_toon
from relic.discovery import discover_subprojects
from relic.indexer import compute_stats, load_graph, run_index
from relic.mcp_server import run as run_mcp
from relic.search import (
    available_subprojects,
    render_search_toon,
    search_graph,
    suggest_close_matches,
)
from relic.style import console, err_console
from relic.toon import candidates_to_toon, full_index_to_toon, subgraph_to_toon
from relic.watcher import run_watch

app = typer.Typer(
    name="relic",
    help="Codebase knowledge management — build and query a static knowledge graph.",
    add_completion=False,
    no_args_is_help=True,
)

CONFIG_FILE = Path("relic.yaml")
KNOWLEDGE_DIR = Path(".knowledge")
PROJECT_ROOT = Path.cwd()


def _load_config() -> dict:
    """Read and parse relic.yaml from the current working directory.

    Exits with an error message if the file is missing or malformed.
    """
    if not CONFIG_FILE.exists():
        console.print(style.error(f"{CONFIG_FILE} not found. Create one in the project root to use relic."))
        raise SystemExit(1)
    try:
        with CONFIG_FILE.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        console.print(style.error(f"failed to parse {CONFIG_FILE}: {exc}"))
        raise SystemExit(1)

    if not cfg or "subprojects" not in cfg:
        console.print(style.error(f"{CONFIG_FILE} must contain a 'subprojects' key."))
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
        console.print(style.dim(f"   added to .gitignore: {', '.join(additions)}"))


@app.command(name="init")
def project_init() -> None:
    """Auto-discover subprojects and generate relic.yaml. Adds relic entries to .gitignore."""
    if CONFIG_FILE.exists():
        console.print(style.warn(f"{CONFIG_FILE} already exists — delete it first to re-initialise."))
        raise SystemExit(1)

    console.print(style.header("init"))
    console.print()

    with style.make_spinner("discovering subprojects…") as spinner:
        spinner.add_task("", total=None)
        subprojects = discover_subprojects(PROJECT_ROOT)

    if not subprojects:
        console.print(style.error("no subprojects found. Create relic.yaml manually and define your subprojects."))
        raise SystemExit(1)

    config = {"subprojects": subprojects}
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    table = style.make_table()
    table.add_column("name", style=f"bold {style.SECONDARY}")
    table.add_column("path")
    table.add_column("description", style=style.DIM)
    for name, info in subprojects.items():
        table.add_row(name, info["path"], info["description"])
    console.print(table)

    _add_to_gitignore(PROJECT_ROOT, ["relic.yaml", ".knowledge/"])

    console.print()
    console.print(style.success(f"[bold]{CONFIG_FILE}[/] created with {len(subprojects)} subproject(s)"))
    console.print(style.dim("   relic.yaml and .knowledge/ are personal — gitignored."))
    console.print()
    console.print(style.dim("next:"))
    arrow = style.ARROW
    sec = style.SECONDARY
    console.print(f"   {arrow}  [bold {sec}]relic --init claude[/]   set up Claude Code")
    console.print(f"   {arrow}  [bold {sec}]relic index[/]            build the knowledge graph")


@app.command(name="index")
def index_cmd() -> None:
    """Build the knowledge graph by statically analysing all subproject source files.

    No LLM involved. Writes .knowledge/index.pkl (NetworkX graph).
    Run this after relic init, and again whenever the codebase changes significantly.
    """
    console.print(style.header("index"))
    console.print()

    try:
        with style.make_spinner("indexing codebase…") as spinner:
            spinner.add_task("", total=None)
            G = run_index(PROJECT_ROOT, KNOWLEDGE_DIR, CONFIG_FILE)
    except (FileNotFoundError, ValueError) as exc:
        console.print(style.error(str(exc)))
        raise SystemExit(1)

    file_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file")
    symbol_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "symbol")
    edge_count = G.number_of_edges()

    console.print(style.kv("files", file_count))
    console.print(style.kv("symbols", symbol_count))
    console.print(style.kv("edges", edge_count))
    console.print()

    toon_path = KNOWLEDGE_DIR / "index.toon"
    toon_path.write_text(full_index_to_toon(G), encoding="utf-8")

    console.print(style.success(f"saved   {style.dim(str(KNOWLEDGE_DIR / 'index.pkl'))}"))
    console.print(style.success(f"toon    {style.dim(str(toon_path))}"))


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
        console.print(style.error(str(exc)))
        raise SystemExit(1)

    target_norm = target.lstrip("./")
    path_candidates = [target, target_norm, str(Path(target))]
    abs_target = Path(target)
    if abs_target.is_absolute():
        try:
            from pathlib import PurePosixPath

            path_candidates.append(PurePosixPath(abs_target.relative_to(PROJECT_ROOT)).as_posix())
        except ValueError:
            pass

    node_id = None
    for candidate in path_candidates:
        if candidate in G.nodes:
            node_id = candidate
            break

    if node_id is None:
        symbol_matches = [n for n, d in G.nodes(data=True) if d.get("ntype") == "symbol" and d.get("name") == target]
        if not symbol_matches:
            console.print(style.error(f"not found: '{target}'"))
            suggestions = suggest_close_matches(G, target)
            if suggestions:
                console.print(style.dim("   did you mean?"))
                for s in suggestions:
                    console.print(f"      [bold {style.SECONDARY}]{s}[/]")
            console.print(
                style.dim("   try `relic search <name>` to explore, or `relic index` if the file was added recently.")
            )
            raise SystemExit(1)
        if len(symbol_matches) > 1:
            cand_data = [G.nodes[n] for n in symbol_matches]
            print(candidates_to_toon(target, cand_data))
            err_console.print(
                style.dim(
                    f"ambiguous: '{target}' matches {len(symbol_matches)} symbols — "
                    f"re-run with the file path to scope the query"
                )
            )
            return
        node_id = symbol_matches[0]

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

    print(toon)

    err_console.print(
        style.dim(f"query: {node_id} {style.DOT} {len(file_nodes)} files, {len(symbol_nodes)} symbols, depth={depth}")
    )


@app.command(name="search")
def search_cmd(
    query: str = typer.Argument(..., help="Search term — file path or symbol name."),
    kind: str = typer.Option("all", "--kind", "-k", help="Filter results: file, symbol, or all."),
    subproject: Optional[str] = typer.Option(
        None,
        "--subproject",
        "-s",
        help="Restrict results to a single subproject (as named in relic.yaml).",
    ),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results per category."),
) -> None:
    """Search the knowledge graph for files and symbols by name.

    Results are ranked: exact > prefix > substring, with well-connected nodes
    surfacing first on ties. Output is TOON, same format as the relic_search
    MCP tool.
    """
    if kind not in ("file", "symbol", "all"):
        console.print(style.error(f"invalid --kind: {kind!r}. choose file, symbol, or all."))
        raise SystemExit(1)

    try:
        G = load_graph(KNOWLEDGE_DIR)
    except FileNotFoundError as exc:
        console.print(style.error(str(exc)))
        raise SystemExit(1)

    if subproject:
        available = available_subprojects(G)
        if subproject not in available:
            avail_str = ", ".join(sorted(available)) or "(none indexed)"
            console.print(style.error(f"no such subproject {subproject!r}. available: {avail_str}."))
            raise SystemExit(1)

    file_matches, symbol_matches = search_graph(
        G,
        query,
        kind=kind,
        subproject=subproject,
        limit=limit,  # type: ignore[arg-type]
    )

    print(render_search_toon(query, file_matches, symbol_matches))

    suffix = f" {style.DOT} subproject={subproject}" if subproject else ""
    err_console.print(
        style.dim(f"search: '{query}' {style.DOT} {len(file_matches)} file(s), {len(symbol_matches)} symbol(s){suffix}")
    )


@app.command(name="stats")
def stats_cmd() -> None:
    """Print health metrics for the knowledge graph.

    Shares logic with the `relic_stats` MCP tool — same numbers, different
    display. Use this to confirm the index is fresh before a large refactor.
    """
    try:
        G = load_graph(KNOWLEDGE_DIR)
    except FileNotFoundError as exc:
        console.print(style.error(str(exc)))
        raise SystemExit(1)

    stats = compute_stats(G, KNOWLEDGE_DIR)

    console.print(style.header("stats"))
    console.print()
    console.print(style.kv("last_updated", stats["last_updated"]))
    console.print(style.kv("files", stats["files"]))
    console.print(style.kv("symbols", stats["symbols"]))
    console.print(style.kv("edges", stats["edges"]))
    for et, count in sorted(stats["edges_by_type"].items()):
        console.print(style.kv(f"  {et}", count))
    if stats["subprojects"]:
        console.print(style.kv("subprojects", ", ".join(stats["subprojects"])))


@app.command(name="watch")
def watch_cmd(
    debounce_ms: int = typer.Option(500, "--debounce-ms", help="Coalesce filesystem events within this window."),
) -> None:
    """Watch source files and rebuild the index on every change.

    Run this in a terminal tab while you work. Useful when an agent forgets
    to call relic_reindex — the index stays current automatically. Uses
    OS-native filesystem events (FSEvents/inotify/ReadDirectoryChangesW),
    not polling. Press Ctrl+C to stop.
    """
    if not CONFIG_FILE.exists():
        console.print(style.error(f"{CONFIG_FILE} not found. run `relic init` first."))
        raise SystemExit(1)
    if not (KNOWLEDGE_DIR / "index.pkl").exists():
        console.print(style.error("no index found. run `relic index` once before `relic watch`."))
        raise SystemExit(1)

    try:
        run_watch(
            PROJECT_ROOT,
            KNOWLEDGE_DIR,
            CONFIG_FILE,
            debounce_seconds=max(0.05, debounce_ms / 1000),
        )
    except (FileNotFoundError, ValueError) as exc:
        console.print(style.error(str(exc)))
        raise SystemExit(1)


@app.command(name="coverage")
def coverage_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List every skipped file, not just samples."),
) -> None:
    """Show what relic indexed and what it skipped, with reasons.

    Without this, files dropped silently because of size limits, missing
    parsers, or symlink rules look like model errors instead of tool limits.
    Use it after `relic index` (or `relic watch`) to audit coverage.
    """
    cfg = _load_config()
    coverage = compute_coverage(PROJECT_ROOT, cfg["subprojects"])
    render_coverage(coverage, console, verbose=verbose)


@app.command(name="mcp")
def mcp_cmd() -> None:
    """Start the relic MCP server (stdio transport).

    Exposes four tools: relic_query, relic_search, relic_reindex, relic_stats.
    Works with any MCP-compatible agent (Claude Code, Cursor, Copilot, Codex).
    Configure in agent settings:

        "mcpServers": { "relic": { "command": "relic", "args": ["mcp"] } }
    """
    run_mcp()


@app.command(name="audit")
def audit_cmd() -> None:
    """Measure relic's own token footprint in the agent context.

    Shows the instruction block written to CLAUDE.md / .cursorrules,
    the MCP tool schemas the agent loads every turn, and a sample
    relic_query against your real graph. Use this to verify relic isn't
    itself part of the 73% overhead problem documented for AI coding
    agents — the baseline tax should stay well under 1,500 tokens.
    """
    audit = compute_audit(PROJECT_ROOT, KNOWLEDGE_DIR)
    render_audit(audit, console)


@app.command(name="benchmark")
def benchmark_cmd(
    target: str = typer.Argument(..., help="File path to benchmark.", metavar="FILE"),
    depth: int = typer.Option(1, "--depth", "-d", help="Graph traversal depth (default 1)."),
) -> None:
    """Compare token cost of agent context with vs without relic.

    Shows files an agent would read manually, tokens saved by TOON injection,
    and hidden callers it would miss entirely. Run this to prove relic's value.
    """
    run_benchmark(target, PROJECT_ROOT, KNOWLEDGE_DIR, depth=depth)


@app.command(name="diff")
def diff_cmd() -> None:
    """Show what changed since the last index.

    Compares on-disk source files against the indexed graph to surface
    new files, deleted files, and changed symbols. Helps agents decide
    whether to call relic_reindex.
    """
    if not KNOWLEDGE_DIR.exists():
        err_console.print(style.error("no index found — run `relic index` first"))
        raise SystemExit(1)

    result = compute_diff(PROJECT_ROOT, KNOWLEDGE_DIR, CONFIG_FILE)
    if not result["stale"]:
        console.print(style.success("index is up-to-date — no changes detected"))
        return

    console.print(style.header("diff"))
    toon = diff_to_toon(result)
    console.print(toon)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    list_all: bool = typer.Option(False, "--list", "-l", help="List all defined subprojects."),
    init: Optional[str] = typer.Option(
        None,
        "--init",
        "-i",
        help=(f"Write relic instructions into agent config file. Pass agent name ({', '.join(AGENTS)}) or 'all'."),
        metavar="AGENT",
    ),
    update: bool = typer.Option(False, "--update", "-u", help="Pull latest release from GitHub and reinstall."),
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit."),
) -> None:
    """Relic — build and query a static knowledge graph for AI coding agents."""
    if version:
        console.print(style.banner(__version__))
        return

    if init is not None:
        if init == "all":
            init_all_agents(PROJECT_ROOT)
        elif init in AGENTS:
            init_agent(init, PROJECT_ROOT)
        else:
            console.print(style.error(f"unknown agent: '{init}' — choose from {', '.join(AGENTS)} or 'all'"))
            raise SystemExit(1)
        return

    if update:
        console.print(style.header("update"))
        console.print(style.dim("   fetching latest release tag…\n"))

        tag_result = subprocess.run(
            ["gh", "api", "repos/Swanand58/relic/releases/latest", "--jq", ".tag_name"],
            capture_output=True,
            text=True,
        )
        tag = tag_result.stdout.strip() if tag_result.returncode == 0 and tag_result.stdout.strip() else None

        if tag:
            console.print(style.dim(f"   installing {tag}…\n"))
            ref = tag
        else:
            console.print(style.dim("   no release found, falling back to main…\n"))
            ref = "main"

        result = subprocess.run(
            [
                "uv",
                "tool",
                "install",
                "--reinstall",
                f"git+https://github.com/Swanand58/relic@{ref}",
            ],
            text=True,
        )
        if result.returncode != 0:
            console.print(style.error("update failed — is uv installed and on PATH?"))
            raise SystemExit(result.returncode)
        console.print(style.success(f"relic updated to {ref}"))
        return

    if ctx.invoked_subcommand is not None:
        return

    cfg = _load_config()
    all_subprojects: dict = cfg["subprojects"]

    if list_all:
        table = style.make_table(title="subprojects")
        table.add_column("name", style=f"bold {style.SECONDARY}")
        table.add_column("path")
        table.add_column("description", style=style.DIM)
        for name, info in all_subprojects.items():
            table.add_row(name, info.get("path", ""), info.get("description", ""))
        console.print(table)
        return

    console.print(ctx.get_help())
