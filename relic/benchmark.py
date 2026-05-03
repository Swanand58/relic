"""Benchmark — compares token cost of agent context with vs without relic.

Shows exactly what an agent would need to read without relic, what relic
provides instead, and what information relic surfaces that manual file
reads cannot provide at all (e.g. imported_by edges).

Token approximation: characters / 4 (standard GPT/Claude estimate for
code, accurate to ±15%). No API calls — entirely local.
"""

from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from relic.indexer import load_graph
from relic.toon import subgraph_to_toon

console = Console()


def _tokens(text: str) -> int:
    """Rough token estimate: characters / 4."""
    return max(1, len(text) // 4)


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_benchmark(target: str, project_root: Path, knowledge_dir: Path, depth: int = 1) -> None:
    """Run the benchmark and print results to the console."""

    # ── Load graph ──────────────────────────────────────────────────────────
    try:
        G = load_graph(knowledge_dir)
    except FileNotFoundError:
        console.print("[bold red]Error:[/bold red] No index found. Run `relic index` first.")
        raise SystemExit(1)

    # ── Resolve node ID ──────────────────────────────────────────────────────
    target_norm = target.lstrip("./")
    abs_target = Path(target)
    candidates = [target, target_norm, str(Path(target))]
    if abs_target.is_absolute():
        try:
            candidates.append(str(abs_target.relative_to(project_root)))
        except ValueError:
            pass

    node_id = None
    for c in candidates:
        if c in G.nodes and G.nodes[c].get("ntype") == "file":
            node_id = c
            break

    if node_id is None:
        console.print(f"[bold red]Not found:[/bold red] '{target}' not in index.")
        raise SystemExit(1)

    # ── Build subgraph ───────────────────────────────────────────────────────
    t0 = time.perf_counter()
    neighbours = {node_id}
    frontier = {node_id}
    for _ in range(depth):
        nf = set()
        for n in frontier:
            nf.update(G.predecessors(n))
            nf.update(G.successors(n))
        nf -= neighbours
        neighbours.update(nf)
        frontier = nf

    sg = G.subgraph(neighbours)
    file_nodes = [d for _, d in sg.nodes(data=True) if d.get("ntype") == "file"]
    symbol_nodes = [d for _, d in sg.nodes(data=True) if d.get("ntype") == "symbol"]
    import_edges = [(u, v) for u, v, d in sg.edges(data=True) if d.get("etype") == "imports"]
    define_edges = [(u, v) for u, v, d in sg.edges(data=True) if d.get("etype") == "defines"]
    extends_edges = [(u, v) for u, v, d in sg.edges(data=True) if d.get("etype") == "extends"]

    toon = subgraph_to_toon(
        focus_path=node_id,
        file_nodes=file_nodes,
        symbol_nodes=symbol_nodes,
        import_edges=import_edges,
        define_edges=define_edges,
        extends_edges=extends_edges,
    )
    relic_query_ms = (time.perf_counter() - t0) * 1000

    # ── What files would an agent read manually? ─────────────────────────────
    # Direct imports of the focus file (what it depends on)
    direct_imports = [v for u, v in import_edges if u == node_id]
    # Files that import the focus file (callers — agent has NO way to know without relic)
    imported_by = [u for u, v in import_edges if v == node_id]

    manual_files: list[tuple[str, str, int]] = []  # (path, reason, tokens)

    # Focus file itself
    focus_abs = project_root / node_id
    focus_content = _read_file(focus_abs)
    focus_tokens = _tokens(focus_content)
    manual_files.append((node_id, "target file", focus_tokens))

    # Direct imports — agent would read these to understand what's available
    for imp in sorted(direct_imports):
        abs_p = project_root / imp
        content = _read_file(abs_p)
        tok = _tokens(content)
        manual_files.append((imp, "direct import", tok))

    manual_total_tokens = sum(t for _, _, t in manual_files)

    # ── TOON token cost ──────────────────────────────────────────────────────
    toon_tokens = _tokens(toon)
    # Agent still reads the focus file — relic doesn't replace that
    with_relic_tokens = toon_tokens + focus_tokens
    savings = manual_total_tokens - with_relic_tokens
    pct = round((savings / manual_total_tokens) * 100) if manual_total_tokens > 0 else 0

    # ── Output ───────────────────────────────────────────────────────────────
    console.print()
    console.print(Rule(f"[bold]Relic Benchmark[/bold] — {node_id}", style="cyan"))
    console.print()

    # WITHOUT RELIC
    console.print("[bold red]WITHOUT RELIC[/bold red] — agent reads files manually")
    console.print()

    t_without = Table(show_header=True, header_style="bold", show_lines=False, box=None, padding=(0, 2))
    t_without.add_column("File", style="dim")
    t_without.add_column("Reason")
    t_without.add_column("~Tokens", justify="right", style="yellow")
    for path, reason, tok in manual_files:
        t_without.add_row(path, reason, f"{tok:,}")
    t_without.add_row("", "", "")
    t_without.add_row("[bold]Total[/bold]", f"[bold]{len(manual_files)} files[/bold]", f"[bold yellow]{manual_total_tokens:,}[/bold yellow]")
    console.print(t_without)
    console.print()

    if imported_by:
        console.print("[bold red]Still unknown[/bold red] without relic (agent cannot discover these):")
        for f in imported_by:
            console.print(f"  [red]✗[/red] [dim]{f}[/dim] imports this file — [italic]breaking change risk[/italic]")
        console.print()

    # WITH RELIC
    console.print("[bold green]WITH RELIC[/bold green] — TOON context injected automatically")
    console.print()

    t_with = Table(show_header=True, header_style="bold", show_lines=False, box=None, padding=(0, 2))
    t_with.add_column("Component")
    t_with.add_column("Detail", style="dim")
    t_with.add_column("~Tokens", justify="right", style="green")
    t_with.add_row("TOON context (auto-injected)", f"depth={depth}, {len(file_nodes)} files, {len(symbol_nodes)} symbols", f"{toon_tokens:,}")
    t_with.add_row("Focus file read", node_id, f"{focus_tokens:,}")
    t_with.add_row("", "", "")
    t_with.add_row("[bold]Total[/bold]", "", f"[bold green]{with_relic_tokens:,}[/bold green]")
    console.print(t_with)
    console.print()

    if imported_by:
        console.print("[bold green]Known from graph[/bold green] (zero extra reads):")
        for f in imported_by:
            console.print(f"  [green]✓[/green] [dim]{f}[/dim] imports this file")
        console.print()

    # Summary
    console.print(Rule(style="dim"))
    summary = Table(show_header=False, show_lines=False, box=None, padding=(0, 2))
    summary.add_column("Metric", style="bold")
    summary.add_column("Value")
    summary.add_row("Token reduction", f"[bold green]{savings:,} tokens saved ({pct}% fewer)[/bold green]")
    summary.add_row("Files eliminated", f"[green]{len(direct_imports)} import reads skipped[/green]")
    summary.add_row("Hidden callers", f"[green]{len(imported_by)} imported_by edge(s) — only relic knows[/green]")
    summary.add_row("Query time", f"[dim]{relic_query_ms:.1f}ms[/dim]")
    summary.add_row("Token estimate", "[dim]~4 chars/token (standard code estimate)[/dim]")
    console.print(summary)
    console.print()
