"""Circular dependency detection on the file-level dependency subgraph."""

from __future__ import annotations

import networkx as nx

from relic.toon import ToonWriter

_DEP_EDGE_TYPES = {"imports", "uses", "tested_by"}


def _file_dep_graph(G: nx.DiGraph) -> nx.DiGraph:
    file_nodes = {n for n, d in G.nodes(data=True) if d.get("ntype") == "file"}
    fg = nx.DiGraph()
    fg.add_nodes_from(file_nodes)
    for u, v, d in G.edges(data=True):
        if u in file_nodes and v in file_nodes and d.get("etype") in _DEP_EDGE_TYPES:
            fg.add_edge(u, v, **d)
    return fg


def compute_cycles(G: nx.DiGraph, min_len: int = 2) -> list[list[str]]:
    """Return all simple cycles in the file dependency graph, shortest first."""
    fg = _file_dep_graph(G)
    cycles = [c for c in nx.simple_cycles(fg) if len(c) >= min_len]
    cycles.sort(key=len)
    return cycles


def render_cycles_toon(cycles: list[list[str]], limit: int = 20) -> str:
    w = ToonWriter()
    w.kv("total_cycles", len(cycles))
    if not cycles:
        w.blank().raw("no circular dependencies found")
        return w.build().strip()

    shown = cycles[:limit] if limit > 0 else cycles
    w.kv("shown", len(shown)).blank()

    rows = []
    for i, cycle in enumerate(shown, 1):
        chain = " → ".join(cycle) + f" → {cycle[0]}"
        rows.append([i, len(cycle), chain])

    w.table("cycles", ["#", "length", "chain"], rows)
    return w.build().strip()
