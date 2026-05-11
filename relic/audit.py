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
import json
from pathlib import Path

from relic import style
from relic.agent_config import RELIC_INSTRUCTIONS
from relic.benchmark import _read_file, _tokens
from relic.indexer import load_graph
from relic.mcp_server import list_tools
from relic.toon import subgraph_to_toon

# Healthy ranges for relic's own tax. Tuned so a typical agent has at
# least 80 % of its system-prompt budget left after relic registers.
TAX_HEALTHY = 1500  # ✓ green
TAX_WARN = 2000  # ⚠ yellow above this
# Above TAX_WARN → ✗ red. Means relic is contributing meaningfully to the
# kind of overhead the article called out — time to trim.

# Depth=1 matches the most common relic_query usage and aligns the TOON
# token count with the manual baseline (focus file + direct imports only).
# Depth=2 explodes on hub files and makes the comparison unfair.
SAMPLE_DEPTH = 1


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
        breakdown.append(
            {
                "name": t.name,
                "description_tokens": desc_tok,
                "schema_tokens": schema_tok,
                "total": sub_total,
            }
        )
        total += sub_total
    return total, breakdown


def _query_one(G, project_root: Path, node_id: str) -> dict | None:
    """Compute relic vs manual token cost for a single file node."""
    neighbours = {node_id}
    frontier = {node_id}
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
        focus_path=node_id,
        file_nodes=file_nodes,
        symbol_nodes=symbol_nodes,
        import_edges=import_edges,
        define_edges=define_edges,
        extends_edges=extends_edges,
    )

    direct_imports = [v for u, v in import_edges if u == node_id]
    focus_content = _read_file(project_root / node_id)
    focus_tokens = _tokens(focus_content)
    manual_total = focus_tokens
    for imp in direct_imports:
        manual_total += _tokens(_read_file(project_root / imp))

    toon_tokens = _tokens(toon)
    with_relic = toon_tokens + focus_tokens
    savings = manual_total - with_relic

    return {
        "path": node_id,
        "toon_tokens": toon_tokens,
        "focus_tokens": focus_tokens,
        "with_relic_tokens": with_relic,
        "manual_baseline": manual_total,
        "savings": savings,
        "savings_pct": round(savings / manual_total * 100) if manual_total else 0,
        "files_replaced": len(direct_imports),
    }


def _sample_query(project_root: Path, knowledge_dir: Path, n_samples: int = 5) -> dict | None:
    """Sample top-N files by import count, compute avg savings across all of them."""
    try:
        G = load_graph(knowledge_dir)
    except FileNotFoundError:
        return None

    # Only sample real source-code files — doc/config files have no import
    # relationships so their TOON savings are meaningless or misleading.
    _SOURCE_LANGS = {
        "python",
        "typescript",
        "javascript",
        "go",
        "rust",
        "java",
        "c#",
        "kotlin",
        "scala",
        "php",
        "swift",
    }
    _TEST_PATTERNS = ("test_", "_test.", "/test/", "/tests/", "/spec/", "_spec.")

    def _is_test(path: str) -> bool:
        return any(pat in path for pat in _TEST_PATTERNS)

    source_nodes = [
        n
        for n, d in G.nodes(data=True)
        if d.get("ntype") == "file"
        and (project_root / n).exists()
        and not _is_test(n)
        and d.get("language", "").lower() in _SOURCE_LANGS
    ]
    # Fall back to all non-test files if language metadata is missing
    file_nodes = source_nodes or [
        n for n, d in G.nodes(data=True) if d.get("ntype") == "file" and (project_root / n).exists() and not _is_test(n)
    ]
    if not file_nodes:
        return None

    # Count only import-type out-edges (not defines edges) — G.out_degree()
    # includes file→symbol defines edges which inflate scores for files with
    # many symbols but few imports.
    import_outdegree: dict[str, int] = {n: 0 for n in file_nodes}
    for u, v, d in G.edges(data=True):
        if d.get("etype") == "imports" and u in import_outdegree:
            import_outdegree[u] += 1

    # Sample by import out-degree. Files that import many things benefit most
    # from relic — agent would need to read all those imports manually.
    # Skip files where manual baseline < 500 tokens (too small, noisy %).
    MIN_MANUAL_TOKENS = 500
    by_outdegree = sorted(
        [n for n in file_nodes if import_outdegree[n] > 0],
        key=lambda n: import_outdegree[n],
        reverse=True,
    )
    # Fall back to all source nodes if nothing has import edges (e.g. tiny codebases)
    if not by_outdegree:
        by_outdegree = file_nodes

    # Spread evenly: high / mid / low importers for a representative mix.
    # Prefer files whose on-disk content is >= MIN_MANUAL_TOKENS — too-small
    # files produce noisy savings percentages. Fall back without the filter
    # if the codebase is tiny and everything is under the threshold.
    total = len(by_outdegree)
    candidates: list[str] = []
    step = max(1, total // (n_samples * 2))
    seen: set[str] = set()
    for i in range(0, total, step):
        if len(candidates) >= n_samples:
            break
        node = by_outdegree[i]
        if node not in seen:
            if _tokens(_read_file(project_root / node)) >= MIN_MANUAL_TOKENS:
                candidates.append(node)
            seen.add(node)

    # Fallback: if token-size filter removed everything, use top out-degree nodes
    if not candidates:
        candidates = by_outdegree[:n_samples]

    results = [r for c in candidates if (r := _query_one(G, project_root, c)) is not None]
    if not results:
        return None

    total_manual = sum(r["manual_baseline"] for r in results)
    total_with_relic = sum(r["with_relic_tokens"] for r in results)
    total_savings = total_manual - total_with_relic

    return {
        "depth": SAMPLE_DEPTH,
        "samples": results,
        "n_samples": len(results),
        "total_manual": total_manual,
        "total_with_relic": total_with_relic,
        "total_savings": total_savings,
        "savings_pct": round(total_savings / total_manual * 100) if total_manual else 0,
        # keep compat fields for render_audit
        "sample_path": results[0]["path"],
        "toon_tokens": results[0]["toon_tokens"],
        "focus_tokens": results[0]["focus_tokens"],
        "with_relic_tokens": total_with_relic,
        "manual_baseline": total_manual,
        "savings": total_savings,
        "files_replaced": sum(r["files_replaced"] for r in results),
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
    console.print(
        style.kv(
            "instructions block",
            f"~{audit['instruction_tokens']:,} tokens",
            key_width=kw,
        )
    )
    console.print(f"   {' ' * kw}[{style.DIM}]CLAUDE.md / .cursorrules / AGENTS.md[/]")

    console.print(
        style.kv(
            "mcp tool schemas",
            f"~{audit['mcp_tokens']:,} tokens",
            key_width=kw,
        )
    )
    console.print(f"   {' ' * kw}[{style.DIM}]4 tools, every turn[/]")

    console.print(f"   [{style.DIM}]{'─' * (kw + 14)}[/]")
    console.print(
        style.kv(
            "baseline tax / turn",
            f"~{audit['baseline_tax']:,} tokens",
            key_width=kw,
        )
    )

    sample = audit["sample_query"]
    if sample is not None:
        n = sample.get("n_samples", 1)
        console.print()
        console.print(style.dim(f"   sample queries  ·  {n} files  ·  depth={sample['depth']}"))
        console.print()

        for r in sample.get("samples", []):
            pct = r["savings_pct"]
            sign = "+" if pct >= 0 else ""
            color = "green" if pct > 0 else "red"
            console.print(
                f"   [{style.DIM}]{r['path']:<45}[/]  "
                f"manual ~{r['manual_baseline']:>5,}  relic ~{r['with_relic_tokens']:>5,}  "
                f"[{color}]{sign}{pct}%[/]"
            )

        console.print()
        console.print(f"   [{style.DIM}]{'─' * (kw + 30)}[/]")
        console.print(
            style.kv(
                "total manual baseline",
                f"~{sample['total_manual']:,} tokens  ({n} files)",
                key_width=kw,
            )
        )
        console.print(
            style.kv(
                "total with relic",
                f"~{sample['total_with_relic']:,} tokens",
                key_width=kw,
            )
        )
        pct = sample["savings_pct"]
        sign = "+" if pct >= 0 else ""
        color = "green" if pct > 0 else "yellow"
        savings_str = f"[{color}]~{sample['total_savings']:,} tokens ({sign}{pct}%)[/]"
        if pct <= 0:
            savings_str += f"  [{style.DIM}]← hub files pull large subgraphs; value is in caller graph[/]"
        console.print(
            style.kv(
                "net savings",
                savings_str,
                key_width=kw,
            )
        )
    else:
        console.print()
        console.print(style.dim("   sample query: not available — run `relic index` first."))

    console.print()
    _print_verdict(audit, console)


def compute_usage_audit(knowledge_dir: Path) -> dict | None:
    """Read MCP usage stats from .knowledge/usage.json. None if missing."""
    usage_file = knowledge_dir / "usage.json"
    if not usage_file.exists():
        return None
    try:
        return json.loads(usage_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def render_usage_audit(usage: dict | None, console) -> None:
    """Print MCP usage stats."""
    console.print(style.header("audit --usage"))
    console.print()
    if usage is None:
        console.print(style.dim("   no usage data — run relic via MCP first"))
        console.print()
        return
    kw = 28
    console.print(style.kv("queries", str(usage.get("query_count", 0)), key_width=kw))
    console.print(style.kv("searches", str(usage.get("search_count", 0)), key_width=kw))
    console.print(style.kv("reindexes", str(usage.get("reindex_count", 0)), key_width=kw))
    console.print(style.kv("diffs", str(usage.get("diff_count", 0)), key_width=kw))
    total = usage.get("total_response_tokens", 0)
    console.print(style.kv("total response tokens", f"~{total:,}", key_width=kw))
    tiny = usage.get("responses_under_200_tokens", 0)
    console.print(style.kv("tiny responses (< 200 tok)", str(tiny), key_width=kw))
    console.print()


def _print_verdict(audit: dict, console) -> None:
    """One-line summary with the brand glyph in the threshold's color."""
    tax = audit["baseline_tax"]
    th = audit["thresholds"]
    verdict = audit["verdict"]

    if verdict == "healthy":
        console.print(style.success(f"baseline tax under {th['healthy']:,} tokens — within healthy range"))
    elif verdict == "warn":
        console.print(
            style.warn(
                f"baseline tax {tax:,} tokens — above the {th['healthy']:,} healthy "
                f"target. Trim agent instructions or disable unused MCP tools."
            )
        )
    else:
        console.print(
            style.error(
                f"baseline tax {tax:,} tokens — above the {th['warn']:,} warn line. "
                f"Relic is contributing to context overhead. File an issue."
            )
        )
