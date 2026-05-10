"""MCP server — exposes relic knowledge graph as native tools for coding agents.

Four tools:
  relic_query   — dependency context for a file or symbol (call before editing)
  relic_search  — search symbols/files across the graph by name
  relic_reindex — incremental rebuild after writing files
  relic_diff    — what changed since last index (new/deleted/changed files)

Every response is prefixed with an `index{age_s,stale,files_changed}` header
so agents always know whether to call `relic_reindex` — there is no separate
"stats" tool to call.

Start with: relic mcp  (stdio transport, standard MCP protocol)

Configure in any MCP-compatible agent (.claude/settings.json, cursor settings, etc.):
    "mcpServers": {
        "relic": { "command": "relic", "args": ["mcp"] }
    }
"""

import asyncio
import json
import threading
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from relic import freshness as _freshness
from relic.diff import compute_diff, diff_to_toon
from relic.indexer import incremental_index, load_graph
from relic.search import (
    available_subprojects,
    render_search_toon,
    search_graph,
    suggest_close_matches,
)
from relic.toon import candidates_to_toon, subgraph_to_toon

KNOWLEDGE_DIR = Path(".knowledge")
CONFIG_FILE = Path("relic.yaml")
USAGE_FILE = KNOWLEDGE_DIR / "usage.json"

server = Server("relic")

# ---------------------------------------------------------------------------
# Usage tracking (in-process, persisted to .knowledge/usage.json)
# ---------------------------------------------------------------------------

_usage_lock = threading.Lock()
_USAGE_KEYS = (
    "query_count",
    "search_count",
    "reindex_count",
    "diff_count",
    "total_response_tokens",
    "responses_under_200_tokens",
)


def _read_usage() -> dict:
    try:
        if USAGE_FILE.exists():
            return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {k: 0 for k in _USAGE_KEYS}


def _write_usage(data: dict) -> None:
    try:
        KNOWLEDGE_DIR.mkdir(exist_ok=True)
        USAGE_FILE.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def _track(call_type: str, response_tokens: int) -> None:
    with _usage_lock:
        data = _read_usage()
        count_key = f"{call_type}_count"
        if count_key in data:
            data[count_key] = data.get(count_key, 0) + 1
        data["total_response_tokens"] = data.get("total_response_tokens", 0) + response_tokens
        if response_tokens < 200:
            data["responses_under_200_tokens"] = data.get("responses_under_200_tokens", 0) + 1
        _write_usage(data)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_node(G, target: str) -> list[str]:
    """Find node IDs for a file path or symbol name.

    File-path resolution returns at most one match (paths are unique node IDs).
    Symbol-name resolution returns every matching symbol so callers can render
    a disambiguation list when more than one definition exists.

    Returns [] when nothing matches.
    """
    target_norm = target.lstrip("./")
    posix_norm = target_norm.replace("\\", "/")
    for candidate in [target, target_norm, posix_norm]:
        if candidate in G.nodes:
            return [candidate]
    matches: list[str] = []
    for n, d in G.nodes(data=True):
        if d.get("ntype") == "symbol" and d.get("name") == target:
            matches.append(n)
    return matches


def _bfs_subgraph(G, node_id: str, depth: int):
    """BFS traversal in both directions — returns subgraph within `depth` hops."""
    visited = {node_id}
    frontier = {node_id}
    for _ in range(depth):
        next_frontier = set()
        for n in frontier:
            next_frontier.update(G.predecessors(n))
            next_frontier.update(G.successors(n))
        next_frontier -= visited
        visited.update(next_frontier)
        frontier = next_frontier
    return G.subgraph(visited)


def _to_toon(
    subgraph,
    focus_path: str,
    *,
    exclude_tests: bool = False,
    max_neighbor_symbols: int = 0,
    include_intent: bool = True,
) -> str:
    file_nodes = [d for _, d in subgraph.nodes(data=True) if d.get("ntype") == "file"]
    symbol_nodes = [d for _, d in subgraph.nodes(data=True) if d.get("ntype") == "symbol"]
    import_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "imports"]
    define_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "defines"]
    extends_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "extends"]
    tested_by_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "tested_by"]
    uses_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "uses"]
    calls_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "calls"]
    return subgraph_to_toon(
        focus_path=focus_path,
        file_nodes=file_nodes,
        symbol_nodes=symbol_nodes,
        import_edges=import_edges,
        define_edges=define_edges,
        extends_edges=extends_edges,
        tested_by_edges=tested_by_edges,
        uses_edges=uses_edges,
        calls_edges=calls_edges,
        exclude_tests=exclude_tests,
        max_neighbor_symbols=max_neighbor_symbols,
        include_intent=include_intent,
    )


def _load_or_error(knowledge_dir: Path):
    """Load graph or return an error string. Returns (G, None) or (None, error_str)."""
    try:
        return load_graph(knowledge_dir), None
    except FileNotFoundError:
        return None, (
            "Error: no index found. Ask the user to run `relic index` "
            "in the project root once; subsequent reindexes are incremental."
        )


def _config_path() -> Path | None:
    return CONFIG_FILE if CONFIG_FILE.exists() else None


def _freshness_header() -> str:
    """Compute the current freshness header (cheap; cached for 2s)."""
    f = _freshness.freshness(Path("."), KNOWLEDGE_DIR, _config_path())
    return _freshness.header(f)


def _wrap(text: str, focus_file_tokens: int = 0, call_type: str = "query") -> list[TextContent]:
    """Prefix freshness + cost headers onto a response and box it as TextContent."""
    response_tokens = max(1, len(text) // 4)
    cost_line = f"cost{{response_tokens,focus_file_tokens}}: {response_tokens},{focus_file_tokens}"
    full_text = f"{_freshness_header()}\n{cost_line}\n{text}"
    _track(call_type, response_tokens)
    return [TextContent(type="text", text=full_text)]


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="relic_query",
            description=(
                "TOON dependency context for a file or symbol: imports, exports, "
                "signatures, neighbors, callers (up to `depth` hops). Call before "
                "editing any unfamiliar file. Ambiguous symbol → TOON candidates "
                "list; re-query with full file path. Supports space-separated "
                "targets for batch query and Class.method dotted notation. "
                "Shorthands: 'impact:TARGET' for blast-radius, 'A->B' for shortest path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "File path, symbol name, Class.method, or space-separated targets.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "BFS hops (default 2; use 1 for barrel files).",
                        "default": 2,
                    },
                    "exclude_tests": {
                        "type": "boolean",
                        "description": "Drop test symbols from neighbor_symbols (default true).",
                        "default": True,
                    },
                    "max_neighbor_symbols": {
                        "type": "integer",
                        "description": "Cap neighbor_symbols to top N by connectivity (default 30, 0 = unlimited).",
                        "default": 30,
                    },
                    "include_intent": {
                        "type": "boolean",
                        "description": "Include docstring intent and decorators in exports (default true).",
                        "default": True,
                    },
                },
                "required": ["target"],
            },
        ),
        Tool(
            name="relic_search",
            description=(
                "Ranked search for files and symbols by name. Use when you don't "
                "know where something lives. Order: exact > prefix > substring, "
                "well-connected nodes first on ties. Returns TOON."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "File path or symbol name (case-insensitive).",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Filter: file, symbol, or all.",
                        "enum": ["file", "symbol", "all"],
                        "default": "all",
                    },
                    "subproject": {
                        "type": "string",
                        "description": "Restrict to one subproject (if configured in relic.yaml).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results per category.",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="relic_reindex",
            description=(
                "Incremental rebuild — reparses only files whose mtime changed "
                "since the last index. Call when the response header reports "
                "`stale=true`. Sub-second on large repos. If no index exists, "
                "the user must run `relic index` once first."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="relic_diff",
            description=(
                "Detail of what changed since last index: per-file new / deleted "
                "/ modified, with symbol-level changes. Use when the header says "
                "stale and you want to know what before reindexing."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    if name == "relic_query":
        return _handle_query(args)
    if name == "relic_search":
        return _handle_search(args)
    if name == "relic_reindex":
        return await _handle_reindex()
    if name == "relic_diff":
        return _handle_diff()
    raise ValueError(f"Unknown tool: {name}")


def _resolve_dotted(G, target: str) -> list[str]:
    """Resolve dotted notation like ``ClassName.method`` to a symbol node.

    Returns matching node IDs, or [] if no match.
    """
    if "." not in target:
        return []
    parts = target.split(".", 1)
    parent_name, child_name = parts[0], parts[1]

    for n, d in G.nodes(data=True):
        if d.get("ntype") != "symbol" or d.get("name") != child_name:
            continue
        file_path = d.get("path", "")
        parent_matches = [
            pn
            for pn, pd in G.nodes(data=True)
            if pd.get("ntype") == "symbol" and pd.get("name") == parent_name and pd.get("path") == file_path
        ]
        if parent_matches:
            return [n]

    for n, d in G.nodes(data=True):
        if d.get("ntype") != "symbol" or d.get("name") != child_name:
            continue
        if parent_name in d.get("path", ""):
            return [n]

    return []


def _focus_file_tokens(G, node_id: str) -> int:
    """Estimate on-disk token cost of the focus file (bytes ÷ 4)."""
    try:
        node_data = G.nodes.get(node_id, {})
        focus_path = node_data.get("path", node_id) if node_data.get("ntype") == "symbol" else node_id
        p = Path(focus_path)
        if not p.is_absolute():
            p = Path(".") / p
        if p.exists():
            return max(1, p.stat().st_size // 4)
    except Exception:
        pass
    return 0


def _handle_query(args: dict) -> list[TextContent]:
    target: str = args.get("target", "")
    depth: int = int(args.get("depth", 2))
    exclude_tests: bool = args.get("exclude_tests", True)
    max_neighbor_symbols: int = int(args.get("max_neighbor_symbols", 30))
    include_intent: bool = args.get("include_intent", True)

    if not target:
        return _wrap("Error: target is required.", call_type="query")

    # Shorthand: "impact:TARGET" → blast-radius analysis
    if target.startswith("impact:"):
        inner = target[len("impact:"):].strip()
        G, err = _load_or_error(KNOWLEDGE_DIR)
        if err:
            return _wrap(err, call_type="query")
        from relic.impact import compute_impact, render_impact_toon

        result = compute_impact(G, inner, max_depth=depth)
        if result is None:
            return _wrap(f"Not found: '{inner}'. Use relic_search to explore.", call_type="query")
        return _wrap(render_impact_toon(inner, result), call_type="query")

    # Shorthand: "A->B" → shortest path
    if "->" in target and not target.startswith("-"):
        parts = target.split("->", 1)
        src, dst = parts[0].strip(), parts[1].strip()
        G, err = _load_or_error(KNOWLEDGE_DIR)
        if err:
            return _wrap(err, call_type="query")
        from relic.impact import compute_path, render_path_toon

        result = compute_path(G, src, dst)
        if result is None:
            return _wrap(f"No path found between '{src}' and '{dst}'.", call_type="query")
        return _wrap(render_path_toon(src, dst, result), call_type="query")

    G, err = _load_or_error(KNOWLEDGE_DIR)
    if err:
        return _wrap(err, call_type="query")

    targets = target.split() if " " in target else [target]
    focus_tokens = _focus_file_tokens(G, targets[0])

    if len(targets) == 1:
        return _wrap(
            _query_single(
                G,
                targets[0],
                depth,
                exclude_tests=exclude_tests,
                max_neighbor_symbols=max_neighbor_symbols,
                include_intent=include_intent,
            ),
            focus_file_tokens=focus_tokens,
            call_type="query",
        )

    sections = [
        _query_single(
            G,
            t,
            depth,
            exclude_tests=exclude_tests,
            max_neighbor_symbols=max_neighbor_symbols,
            include_intent=include_intent,
        )
        for t in targets
    ]
    return _wrap("\n\n---\n\n".join(sections), focus_file_tokens=focus_tokens, call_type="query")


def _query_single(
    G,
    target: str,
    depth: int,
    *,
    exclude_tests: bool = False,
    max_neighbor_symbols: int = 0,
    include_intent: bool = True,
) -> str:
    """Resolve and render a single target as a TOON string (no header)."""
    matches = _resolve_dotted(G, target)
    if not matches:
        matches = _resolve_node(G, target)

    if not matches:
        suggestions = suggest_close_matches(G, target)
        msg = f"Not found: '{target}'."
        if suggestions:
            msg += "\n\nDid you mean?\n  " + "\n  ".join(suggestions)
        msg += "\n\nUse relic_search to explore, or relic_reindex if the file was added recently."
        return msg

    if len(matches) > 1:
        candidates = [G.nodes[n] for n in matches]
        return candidates_to_toon(target, candidates)

    node_id = matches[0]
    subgraph = _bfs_subgraph(G, node_id, depth)

    node_data = G.nodes[node_id]
    focus_path = node_data.get("path", node_id) if node_data.get("ntype") == "symbol" else node_id

    return _to_toon(
        subgraph,
        focus_path,
        exclude_tests=exclude_tests,
        max_neighbor_symbols=max_neighbor_symbols,
        include_intent=include_intent,
    )


def _handle_search(args: dict) -> list[TextContent]:
    query: str = args.get("query", "")
    kind: str = args.get("kind", "all")
    subproject = args.get("subproject") or None
    limit: int = int(args.get("limit", 20))

    if not query:
        return _wrap("Error: query is required.", call_type="search")

    G, err = _load_or_error(KNOWLEDGE_DIR)
    if err:
        return _wrap(err, call_type="search")

    if subproject:
        available = available_subprojects(G)
        if subproject not in available:
            avail_str = ", ".join(sorted(available)) or "(none indexed)"
            return _wrap(f"Error: no such subproject '{subproject}'. Available: {avail_str}.", call_type="search")

    file_matches, symbol_matches, literal_matches = search_graph(
        G,
        query,
        kind=kind,
        subproject=subproject,
        limit=limit,  # type: ignore[arg-type]
    )
    return _wrap(render_search_toon(query, file_matches, symbol_matches, literal_matches), call_type="search")


async def _handle_reindex() -> list[TextContent]:
    config = _config_path()
    try:
        G, summary = await asyncio.to_thread(incremental_index, Path("."), KNOWLEDGE_DIR, config)
    except FileNotFoundError:
        return _wrap(
            "Error: no index found. Ask the user to run `relic index` "
            "in the project root once; subsequent reindexes are incremental.",
            call_type="reindex",
        )
    except Exception as exc:
        return _wrap(f"Reindex failed: {exc}", call_type="reindex")

    # Reindex changed the on-disk state — drop the freshness cache so the
    # header in this response (and the next call's) reflects reality.
    _freshness.invalidate()

    file_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file")
    symbol_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "symbol")
    edge_count = G.number_of_edges()

    body = (
        f"reindex: incremental in {summary['elapsed_s']:.2f}s\n"
        f"changed: +{summary['added']} new, ~{summary['modified']} modified, "
        f"-{summary['deleted']} deleted, ={summary['unchanged']} unchanged\n"
        f"files: {file_count}\nsymbols: {symbol_count}\nedges: {edge_count}"
    )
    return _wrap(body, call_type="reindex")


def _handle_diff() -> list[TextContent]:
    if not KNOWLEDGE_DIR.exists():
        return _wrap(
            "Error: no index found. Ask the user to run `relic index` "
            "in the project root once; subsequent reindexes are incremental.",
            call_type="diff",
        )
    try:
        result = compute_diff(Path("."), KNOWLEDGE_DIR, _config_path())
    except Exception as exc:
        return _wrap(f"Error computing diff: {exc}", call_type="diff")

    if not result["stale"]:
        return _wrap("status: up-to-date — no changes since last index", call_type="diff")
    return _wrap(diff_to_toon(result), call_type="diff")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Start the MCP stdio server."""

    async def _serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_serve())
