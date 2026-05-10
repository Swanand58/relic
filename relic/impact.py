"""Impact analysis — transitive caller closure and shortest-path queries.

Provides:
- compute_impact(G, target): all files/symbols transitively affected if target changes
- compute_path(G, source, dest): shortest dependency path between two nodes
- render_impact_toon / render_path_toon / render_communities_toon: TOON output
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import networkx as nx

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Impact (transitive caller closure)
# ---------------------------------------------------------------------------

_INBOUND_EDGE_TYPES = {"imports", "uses", "calls", "extends", "tested_by", "tests"}


def _resolve_node(G: nx.DiGraph, target: str) -> str | None:
    """Resolve a target string to a graph node ID.

    Accepts:
      - exact node ID (file path or symbol@path)
      - bare symbol name (resolves to first match)
    """
    if target in G.nodes:
        return target
    # Try as symbol name
    for n, d in G.nodes(data=True):
        if d.get("ntype") == "symbol" and d.get("name") == target:
            return n
    return None


def compute_impact(G: nx.DiGraph, target: str, max_depth: int = 0) -> dict[str, Any] | None:
    """Compute transitive impact of changing *target*.

    Walks the reversed graph (dependents → dependencies) collecting all nodes
    that transitively depend on *target* via import/use/call/extend edges.

    Returns a dict with:
        node          : resolved node ID
        affected_files: sorted list of affected file paths
        affected_symbols: sorted list of affected symbol IDs
        depth_map     : {node_id: hop_distance}
    Returns None if target not found.
    """
    node = _resolve_node(G, target)
    if node is None:
        return None

    # Build a reversed subgraph of dependency edges only
    rev = nx.DiGraph()
    for u, v, d in G.edges(data=True):
        if d.get("etype") in _INBOUND_EDGE_TYPES:
            rev.add_edge(v, u, **d)

    if max_depth > 0:
        reachable_set = nx.single_source_shortest_path_length(rev, node, cutoff=max_depth)
    else:
        reachable_set = nx.single_source_shortest_path_length(rev, node)

    depth_map: dict[str, int] = dict(reachable_set)
    depth_map.pop(node, None)  # exclude self

    affected_files = sorted(
        {n for n in depth_map if G.nodes[n].get("ntype") == "file"} if G.has_node(node) else set()
    )
    affected_symbols = sorted(n for n in depth_map if G.nodes.get(n, {}).get("ntype") == "symbol")

    return {
        "node": node,
        "affected_files": affected_files,
        "affected_symbols": affected_symbols,
        "depth_map": depth_map,
    }


# ---------------------------------------------------------------------------
# Path (shortest dependency path)
# ---------------------------------------------------------------------------


def compute_path(G: nx.DiGraph, source: str, dest: str) -> dict[str, Any] | None:
    """Find shortest dependency path from *source* to *dest*.

    Returns a dict with:
        nodes : list of node IDs in path order
        edges : list of (from, to, etype, evidence) tuples
    Returns None if either node missing or no path exists.
    """
    src = _resolve_node(G, source)
    dst = _resolve_node(G, dest)
    if src is None or dst is None:
        return None

    # Build edge-type-filtered graph (dependency edges only)
    dep_graph = nx.DiGraph()
    for u, v, d in G.edges(data=True):
        if d.get("etype") in _INBOUND_EDGE_TYPES:
            dep_graph.add_edge(u, v, **d)

    try:
        path_nodes = nx.shortest_path(dep_graph, src, dst)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None

    edges = []
    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        d = dep_graph.edges.get((u, v), {})
        edges.append((u, v, d.get("etype", ""), d.get("evidence", "")))

    return {"nodes": path_nodes, "edges": edges}


# ---------------------------------------------------------------------------
# TOON renderers
# ---------------------------------------------------------------------------


def render_impact_toon(target: str, result: dict[str, Any]) -> str:
    from relic.toon import ToonWriter

    w = ToonWriter()
    w.kv("impact_target", target).blank()

    depth_map = result["depth_map"]

    if result["affected_files"]:
        file_rows = [[f, depth_map.get(f, 0)] for f in result["affected_files"]]
        w.table("affected_files", ["path", "hops"], file_rows).blank()
    else:
        w.kv("affected_files", 0).blank()

    if result["affected_symbols"]:
        sym_rows = [[s, depth_map.get(s, 0)] for s in result["affected_symbols"]]
        w.table("affected_symbols", ["symbol", "hops"], sym_rows).blank()

    return w.build().strip()


def render_path_toon(source: str, dest: str, result: dict[str, Any]) -> str:
    from relic.toon import ToonWriter

    w = ToonWriter()
    w.kv("path_from", source)
    w.kv("path_to", dest).blank()

    node_rows = [[i, n] for i, n in enumerate(result["nodes"])]
    w.table("nodes", ["hop", "id"], node_rows).blank()

    if result["edges"]:
        edge_rows = [[u, v, et, ev] for u, v, et, ev in result["edges"]]
        w.table("edges", ["from", "to", "type", "evidence"], edge_rows).blank()

    return w.build().strip()


def render_communities_toon(communities: dict[int, list[str]], limit: int = 20) -> str:
    from relic.toon import ToonWriter

    w = ToonWriter()
    w.kv("total_communities", len(communities)).blank()

    # Sort by community size descending
    sorted_cids = sorted(communities, key=lambda c: len(communities[c]), reverse=True)
    shown = sorted_cids[:limit]

    for cid in shown:
        members = communities[cid]
        w.table(
            f"community_{cid}",
            ["path"],
            [[m] for m in members],
        ).blank()

    if len(sorted_cids) > limit:
        w.comment(f"... and {len(sorted_cids) - limit} more communities (use --limit to adjust)")

    return w.build().strip()
