"""MCP server — exposes relic knowledge graph as a native tool for coding agents.

Agents call relic_query directly instead of running a shell command.
Start with: relic mcp  (stdio transport, standard MCP protocol)

Configure in Claude Code (.claude/settings.json):
    "mcpServers": {
        "relic": { "command": "relic", "args": ["mcp"] }
    }
"""

import asyncio
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from relic.indexer import load_graph
from relic.toon import subgraph_to_toon

KNOWLEDGE_DIR = Path(".knowledge")

server = Server("relic")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="relic_query",
            description=(
                "Query the relic knowledge graph for a file path or symbol name. "
                "Returns a TOON context block: the file's imports, exported symbols, "
                "and neighbouring files up to `depth` hops. "
                "Call this before reading or editing any file to get precise, "
                "token-efficient dependency context."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "File path (e.g. src/core/PageDocument.ts) or symbol name (e.g. PageDocument)",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "BFS traversal depth — number of hops from the target node (default 2)",
                        "default": 2,
                    },
                },
                "required": ["target"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    if name != "relic_query":
        raise ValueError(f"Unknown tool: {name}")

    args = arguments or {}
    target: str = args.get("target", "")
    depth: int = int(args.get("depth", 2))

    if not target:
        return [TextContent(type="text", text="Error: target is required.")]

    try:
        G = load_graph(KNOWLEDGE_DIR)
    except FileNotFoundError:
        return [TextContent(
            type="text",
            text="Error: no index found. Run `relic index` in the project root first.",
        )]

    # Normalise path
    target_norm = target.lstrip("./")
    node_id = None
    for candidate in [target, target_norm, str(Path(target))]:
        if candidate in G.nodes:
            node_id = candidate
            break

    if node_id is None:
        for n, d in G.nodes(data=True):
            if d.get("ntype") == "symbol" and d.get("name") == target:
                node_id = n
                break

    if node_id is None:
        return [TextContent(
            type="text",
            text=f"Not found: '{target}' not in index. Run `relic index` to rebuild.",
        )]

    # BFS traversal
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

    return [TextContent(type="text", text=toon)]


def run() -> None:
    """Entry point: start the MCP stdio server."""
    async def _serve() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(_serve())
