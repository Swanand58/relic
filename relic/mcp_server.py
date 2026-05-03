"""MCP server — exposes relic knowledge graph as native tools for coding agents.

Four tools:
  relic_query   — dependency context for a file or symbol (call before editing)
  relic_search  — search symbols/files across the graph by name
  relic_reindex — rebuild the knowledge graph after writing files
  relic_stats   — index health: file count, symbol count, last updated

Start with: relic mcp  (stdio transport, standard MCP protocol)

Configure in any MCP-compatible agent (.claude/settings.json, cursor settings, etc.):
    "mcpServers": {
        "relic": { "command": "relic", "args": ["mcp"] }
    }
"""

import asyncio
import datetime
import time
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from relic.indexer import load_graph, run_index
from relic.search import render_search_toon, search_graph
from relic.toon import candidates_to_toon, subgraph_to_toon

KNOWLEDGE_DIR = Path(".knowledge")
CONFIG_FILE = Path("relic.yaml")

server = Server("relic")


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
    for candidate in [target, target_norm, str(Path(target))]:
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


def _to_toon(subgraph, focus_path: str) -> str:
    file_nodes = [d for _, d in subgraph.nodes(data=True) if d.get("ntype") == "file"]
    symbol_nodes = [d for _, d in subgraph.nodes(data=True) if d.get("ntype") == "symbol"]
    import_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "imports"]
    define_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "defines"]
    extends_edges = [(u, v) for u, v, d in subgraph.edges(data=True) if d.get("etype") == "extends"]
    return subgraph_to_toon(
        focus_path=focus_path,
        file_nodes=file_nodes,
        symbol_nodes=symbol_nodes,
        import_edges=import_edges,
        define_edges=define_edges,
        extends_edges=extends_edges,
    )


def _load_or_error(knowledge_dir: Path):
    """Load graph or return an error string. Returns (G, None) or (None, error_str)."""
    try:
        return load_graph(knowledge_dir), None
    except FileNotFoundError:
        return None, "Error: no index found. Call relic_reindex first."


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="relic_query",
            description=(
                "Get dependency context for a file or symbol before editing it. "
                "Returns TOON: the file's imports, exported symbols, neighboring files, "
                "and what imports this file (callers), up to `depth` hops. "
                "Call this at the start of any edit session for an unfamiliar file — "
                "replaces manual file reads with a ~10x token-efficient summary."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "File path (e.g. src/payments/processor.py) "
                            "or symbol name (e.g. PaymentProcessor)"
                        ),
                    },
                    "depth": {
                        "type": "integer",
                        "description": (
                            "BFS hops from target node (default 2). "
                            "Use depth=1 for barrel/index files to avoid loading the entire subproject."
                        ),
                        "default": 2,
                    },
                },
                "required": ["target"],
            },
        ),
        Tool(
            name="relic_search",
            description=(
                "Search for files and symbols across the knowledge graph by name. "
                "Use this when you don't know where something lives — "
                "pass a class name, function name, or partial file path. "
                "Results are ranked: exact > prefix > substring, with well-connected "
                "nodes surfacing first on ties. Returns TOON."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search term — partial file path or symbol name "
                            "(case-insensitive)"
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "description": "Filter results: 'file', 'symbol', or 'all' (default 'all')",
                        "enum": ["file", "symbol", "all"],
                        "default": "all",
                    },
                    "subproject": {
                        "type": "string",
                        "description": (
                            "Restrict results to a single subproject (as named in "
                            "relic.yaml). Useful in monorepos to avoid cross-subproject noise."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results per category (default 20)",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="relic_reindex",
            description=(
                "Rebuild the relic knowledge graph from source code. "
                "Call this after creating, editing, or deleting files to keep the index fresh. "
                "Takes a few seconds. Returns updated file/symbol/edge counts."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="relic_stats",
            description=(
                "Check knowledge graph health: files indexed, symbols, edges, "
                "last updated timestamp, and subprojects covered. "
                "Call this to verify the index is fresh before a large refactor."
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
    if name == "relic_stats":
        return _handle_stats()
    raise ValueError(f"Unknown tool: {name}")


def _handle_query(args: dict) -> list[TextContent]:
    target: str = args.get("target", "")
    depth: int = int(args.get("depth", 2))

    if not target:
        return [TextContent(type="text", text="Error: target is required.")]

    G, err = _load_or_error(KNOWLEDGE_DIR)
    if err:
        return [TextContent(type="text", text=err)]

    matches = _resolve_node(G, target)
    if not matches:
        return [TextContent(
            type="text",
            text=f"Not found: '{target}'. Try relic_search to locate it, or relic_reindex if recently added.",
        )]

    if len(matches) > 1:
        candidates = [G.nodes[n] for n in matches]
        return [TextContent(type="text", text=candidates_to_toon(target, candidates))]

    node_id = matches[0]
    subgraph = _bfs_subgraph(G, node_id, depth)
    return [TextContent(type="text", text=_to_toon(subgraph, node_id))]


def _handle_search(args: dict) -> list[TextContent]:
    query: str = args.get("query", "")
    kind: str = args.get("kind", "all")
    subproject = args.get("subproject") or None
    limit: int = int(args.get("limit", 20))

    if not query:
        return [TextContent(type="text", text="Error: query is required.")]

    G, err = _load_or_error(KNOWLEDGE_DIR)
    if err:
        return [TextContent(type="text", text=err)]

    file_matches, symbol_matches = search_graph(
        G, query, kind=kind, subproject=subproject, limit=limit  # type: ignore[arg-type]
    )
    return [TextContent(type="text", text=render_search_toon(query, file_matches, symbol_matches))]


async def _handle_reindex() -> list[TextContent]:
    try:
        t0 = time.monotonic()
        G = await asyncio.to_thread(run_index, Path("."), KNOWLEDGE_DIR, CONFIG_FILE)
        elapsed = time.monotonic() - t0
    except FileNotFoundError as exc:
        return [TextContent(type="text", text=f"Error: {exc}. Run `relic init` in the project root first.")]
    except Exception as exc:
        return [TextContent(type="text", text=f"Reindex failed: {exc}")]

    file_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file")
    symbol_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "symbol")
    edge_count = G.number_of_edges()

    return [TextContent(
        type="text",
        text=(
            f"reindex: done in {elapsed:.1f}s\n"
            f"files: {file_count}\n"
            f"symbols: {symbol_count}\n"
            f"edges: {edge_count}"
        ),
    )]


def _handle_stats() -> list[TextContent]:
    G, err = _load_or_error(KNOWLEDGE_DIR)
    if err:
        return [TextContent(type="text", text=err)]

    index_path = KNOWLEDGE_DIR / "index.pkl"
    mtime = index_path.stat().st_mtime if index_path.exists() else None
    last_updated = (
        datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        if mtime else "unknown"
    )

    file_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file")
    symbol_count = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "symbol")
    edge_count = G.number_of_edges()

    subprojects: set[str] = set()
    for _, d in G.nodes(data=True):
        sp = d.get("subproject", "")
        if sp:
            subprojects.add(sp)

    edge_types: dict[str, int] = {}
    for _, _, d in G.edges(data=True):
        et = d.get("etype", "unknown")
        edge_types[et] = edge_types.get(et, 0) + 1

    lines = [
        f"last_updated: {last_updated}",
        f"files: {file_count}",
        f"symbols: {symbol_count}",
        f"edges: {edge_count}",
    ]
    for et, count in sorted(edge_types.items()):
        lines.append(f"  {et}: {count}")
    if subprojects:
        lines.append(f"subprojects: {', '.join(sorted(subprojects))}")

    return [TextContent(type="text", text="\n".join(lines))]


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
