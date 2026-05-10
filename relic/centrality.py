"""Graph centrality metrics — PageRank, betweenness, degree.

All metrics are computed on the file-level subgraph (symbol nodes excluded)
using only dependency edge types (imports, uses, tested_by).  This keeps the
numbers interpretable: centrality reflects file-level coupling, not internal
symbol noise.
"""

from __future__ import annotations

import networkx as nx

from relic.toon import ToonWriter

_DEP_EDGE_TYPES = {"imports", "uses", "tested_by"}


def _pagerank_pure(G: nx.DiGraph, alpha: float = 0.85, max_iter: int = 100, tol: float = 1e-6) -> dict:
    """Pure-Python PageRank power iteration — no scipy/numpy required."""
    N = G.number_of_nodes()
    if N == 0:
        return {}
    nodes = list(G.nodes())
    rank = {n: 1.0 / N for n in nodes}
    dangling = [n for n in nodes if G.out_degree(n) == 0]
    for _ in range(max_iter):
        prev = rank.copy()
        dangling_sum = alpha * sum(prev[n] for n in dangling) / N
        for n in nodes:
            rank[n] = dangling_sum + (1.0 - alpha) / N
            for nbr in G.predecessors(n):
                rank[n] += alpha * prev[nbr] / G.out_degree(nbr)
        if sum(abs(rank[n] - prev[n]) for n in nodes) < tol:
            break
    return rank


def _file_dep_graph(G: nx.DiGraph) -> nx.DiGraph:
    """Return a file-only subgraph of dependency edges."""
    file_nodes = {n for n, d in G.nodes(data=True) if d.get("ntype") == "file"}
    fg = nx.DiGraph()
    fg.add_nodes_from(file_nodes)
    for u, v, d in G.edges(data=True):
        if u in file_nodes and v in file_nodes and d.get("etype") in _DEP_EDGE_TYPES:
            fg.add_edge(u, v, **d)
    return fg


def compute_centrality(G: nx.DiGraph) -> list[dict]:
    """Compute PageRank, betweenness, in-degree, out-degree for every file node.

    Returns list of dicts sorted by PageRank descending.
    """
    fg = _file_dep_graph(G)
    if fg.number_of_nodes() == 0:
        return []

    pagerank = _pagerank_pure(fg)

    # Betweenness on undirected projection (coupling bridges, not flow direction)
    ug = fg.to_undirected()
    betweenness = nx.betweenness_centrality(ug, normalized=True)

    rows = []
    for node in fg.nodes():
        rows.append(
            {
                "path": node,
                "pagerank": round(pagerank.get(node, 0.0), 6),
                "betweenness": round(betweenness.get(node, 0.0), 6),
                "in_degree": fg.in_degree(node),
                "out_degree": fg.out_degree(node),
                "community": G.nodes[node].get("community", -1),
            }
        )

    rows.sort(key=lambda r: r["pagerank"], reverse=True)
    return rows


def render_centrality_toon(rows: list[dict], top: int = 0, sort_by: str = "pagerank") -> str:
    """Render centrality table as TOON."""
    valid_sorts = {"pagerank", "betweenness", "in_degree", "out_degree"}
    if sort_by not in valid_sorts:
        sort_by = "pagerank"

    sorted_rows = sorted(rows, key=lambda r: r[sort_by], reverse=True)
    if top > 0:
        sorted_rows = sorted_rows[:top]

    w = ToonWriter()
    w.kv("sort_by", sort_by)
    w.kv("total_files", len(rows)).blank()

    if sorted_rows:
        w.table(
            "centrality",
            ["path", "pagerank", "betweenness", "in_degree", "out_degree", "community"],
            [
                [
                    r["path"],
                    r["pagerank"],
                    r["betweenness"],
                    r["in_degree"],
                    r["out_degree"],
                    r["community"],
                ]
                for r in sorted_rows
            ],
        )

    return w.build().strip()
