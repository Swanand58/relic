"""Audit — measure relic's own token footprint in the agent context.

Inspired by the public reverse-engineering of Claude Code overhead patterns:
73% of tokens go to invisible chrome (CLAUDE.md bloat, MCP tool schemas,
hooks, skills) before the agent reads a single user message. Relic's whole
pitch is "save tokens" — this command lets the user verify relic isn't
itself part of that overhead.

Three numbers matter:
1. Instructions block — written by `relic --init` to CLAUDE.md /
   .cursorrules / etc. Loaded every turn.
2. MCP tool schemas — sent in the system prompt of every turn the agent
   has the relic MCP server attached.
3. Typical relic_query — what each query call costs once.

(1) + (2) is the "baseline tax" — what relic costs you per turn whether or
not you actually call it. Should stay well under 1,500 tokens.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from relic import style
from relic.agent_config import (
    RELIC_EXAMPLE_PLACEHOLDER,
    RELIC_INSTRUCTIONS,
    _pick_example_file,
)
from relic.benchmark import _read_file, _tokens
from relic.indexer import load_graph
from relic.mcp_server import list_tools
from relic.toon import subgraph_to_toon

# Healthy ranges for relic's own tax. Tuned so a typical agent has at
# least 80 % of its system-prompt budget left after relic registers.
TAX_HEALTHY = 1500    # ✓ green
TAX_WARN = 2000       # ⚠ yellow above this
# Above TAX_WARN → ✗ red. Means relic is contributing meaningfully to the
# kind of overhead the article called out — time to trim.

# Default depth used when sampling a typical relic_query response. Matches
# the default in the MCP tool itself.
SAMPLE_DEPTH = 2


def _instruction_tokens() -> int:
    """Estimate the rendered instruction block.

    `RELIC_EXAMPLE_PLACEHOLDER` is substituted at init time with a real
    project path. We count using the placeholder's length, which is a
    conservative upper bound — real paths are typically shorter.
    """
    return _tokens(RELIC_INSTRUCTIONS)


def _mcp_tool_tokens() -> tuple[int, list[dict]]:
    """Token cost of all four MCP tool definitions (description + schema).

    Returns (total, per_tool_breakdown) where per_tool_breakdown is a list
    of {name, description_tokens, schema_tokens, total} dicts.
    """
    tools = asyncio.run(list_tools())
    breakdown: list[dict] = []
    total = 0
    for t in tools:
        desc_tok = _tokens(t.description or "")
        schema_tok = _tokens(str(t.inputSchema))
        sub_total = desc_tok + schema_tok
        breakdown.append({
            "name": t.name,
            "description_tokens": desc_tok,
            "schema_tokens": schema_tok,
            "total": sub_total,
        })
        total += sub_total
    return total, breakdown


def _sample_query(project_root: Path, knowledge_dir: Path) -> dict | None:
    """Run a representative `relic_query` against the user's actual graph.

    Picks the same file `relic --init` would picks for its example
    (most-connected non-barrel) so the audit reflects what an agent would
    actually see on this project. Returns None when no index exists yet —
    the user will be told to run `relic index` first.
    """
    try:
        G = load_graph(knowledge_dir)
    except FileNotFoundError:
        return None

    sample_path = _pick_example_file(project_root)
    if sample_path == RELIC_EXAMPLE_PLACEHOLDER or sample_path not in G.nodes:
        # The placeholder helper falls back to "src/<your-file>" when the
        # graph is empty. Audit needs a real node — bail.
        return None

    neighbours = {sample_path}
    frontier = {sample_path}
    for _ in range(SAMPLE_DEPTH):
        nf: set[str] = set()
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
        focus_path=sample_path,
        file_nodes=file_nodes,
        symbol_nodes=symbol_nodes,
        import_edges=import_edges,
        define_edges=define_edges,
        extends_edges=extends_edges,
    )

    direct_imports = [v for u, v in import_edges if u == sample_path]
    focus_content = _read_file(project_root / sample_path)
    focus_tokens = _tokens(focus_content)
    manual_total = focus_tokens
    for imp in direct_imports:
        manual_total += _tokens(_read_file(project_root / imp))

    toon_tokens = _tokens(toon)
    with_relic = toon_tokens + focus_tokens
    savings = manual_total - with_relic

    return {
        "sample_path": sample_path,
        "depth": SAMPLE_DEPTH,
        "toon_tokens": toon_tokens,
        "focus_tokens": focus_tokens,
        "with_relic_tokens": with_relic,
        "manual_baseline": manual_total,
        "savings": savings,
        "savings_pct": round(savings / manual_total * 100) if manual_total else 0,
        "files_replaced": len(direct_imports),
    }


def compute_audit(project_root: Path, knowledge_dir: Path) -> dict:
    """Single source of truth for `relic audit` output. Pure data."""
    instr_tokens = _instruction_tokens()
    mcp_tokens, mcp_breakdown = _mcp_tool_tokens()
    baseline_tax = instr_tokens + mcp_tokens

    if baseline_tax <= TAX_HEALTHY:
        verdict = "healthy"
    elif baseline_tax <= TAX_WARN:
        verdict = "warn"
    else:
        verdict = "over"

    return {
        "instruction_tokens": instr_tokens,
        "mcp_tokens": mcp_tokens,
        "mcp_breakdown": mcp_breakdown,
        "baseline_tax": baseline_tax,
        "verdict": verdict,
        "thresholds": {"healthy": TAX_HEALTHY, "warn": TAX_WARN},
        "sample_query": _sample_query(project_root, knowledge_dir),
    }


# ---------------------------------------------------------------------------
# Renderer — themed, kv-aligned, no tables.
# ---------------------------------------------------------------------------

def render_audit(audit: dict, console) -> None:
    console.print(style.header("audit"))
    console.print()
    console.print(style.dim("   relic's footprint in your agent context"))
    console.print()

    kw = 22
    console.print(style.kv(
        "instructions block", f"~{audit['instruction_tokens']:,} tokens",
        key_width=kw,
    ))
    console.print(f"   {' ' * kw}[{style.DIM}]CLAUDE.md / .cursorrules / AGENTS.md[/]")

    console.print(style.kv(
        "mcp tool schemas", f"~{audit['mcp_tokens']:,} tokens",
        key_width=kw,
    ))
    console.print(f"   {' ' * kw}[{style.DIM}]4 tools, every turn[/]")

    console.print(f"   [{style.DIM}]{'─' * (kw + 14)}[/]")
    console.print(style.kv(
        "baseline tax / turn", f"~{audit['baseline_tax']:,} tokens",
        key_width=kw,
    ))

    sample = audit["sample_query"]
    if sample is not None:
        console.print()
        console.print(style.dim(
            f"   sample query  ·  {sample['sample_path']}  ·  depth={sample['depth']}"
        ))
        console.print()
        console.print(style.kv(
            "relic_query response", f"~{sample['with_relic_tokens']:,} tokens",
            key_width=kw,
        ))
        console.print(style.kv(
            "manual baseline", f"~{sample['manual_baseline']:,} tokens",
            key_width=kw,
        ))
        console.print(style.kv(
            "net savings", f"~{sample['savings']:,} tokens "
                           f"({sample['savings_pct']}%)",
            key_width=kw,
        ))
    else:
        console.print()
        console.print(style.dim(
            "   sample query: not available — run `relic index` first."
        ))

    console.print()
    _print_verdict(audit, console)


def _print_verdict(audit: dict, console) -> None:
    """One-line summary with the brand glyph in the threshold's color."""
    tax = audit["baseline_tax"]
    th = audit["thresholds"]
    verdict = audit["verdict"]

    if verdict == "healthy":
        console.print(style.success(
            f"baseline tax under {th['healthy']:,} tokens — within healthy range"
        ))
    elif verdict == "warn":
        console.print(style.warn(
            f"baseline tax {tax:,} tokens — above the {th['healthy']:,} healthy "
            f"target. Trim agent instructions or disable unused MCP tools."
        ))
    else:
        console.print(style.error(
            f"baseline tax {tax:,} tokens — above the {th['warn']:,} warn line. "
            f"Relic is contributing to context overhead. File an issue."
        ))
