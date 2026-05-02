"""TOON serializer — converts NetworkX subgraphs to Token-Oriented Object Notation.

TOON uses a tabular layout for uniform arrays (declare field names once, list
values row by row) so LLMs parse it in far fewer tokens than JSON.

We implement the subset needed for knowledge graph output:
    - Key-value block:  key: value
    - Tabular array:    name[N]{col1,col2,...}:\n  v1,v2,...\n  v1,v2,...

Reference: https://toonformat.dev
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

def _safe(value: Any) -> str:
    """Render a primitive value safe for a TOON cell (no commas or newlines)."""
    s = str(value) if value is not None else ""
    # Replace commas and newlines that would break tabular parsing
    s = s.replace("\n", " ").replace("\r", "")
    # Only quote if contains comma
    if "," in s:
        s = f'"{s}"'
    return s


# ---------------------------------------------------------------------------
# TOON writer
# ---------------------------------------------------------------------------

class ToonWriter:
    """Builds a TOON document section by section."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def kv(self, key: str, value: Any) -> "ToonWriter":
        """Append a key: value line."""
        self._parts.append(f"{key}: {_safe(value)}")
        return self

    def table(self, name: str, fields: list[str], rows: list[list[Any]]) -> "ToonWriter":
        """Append a tabular array block.

        name[N]{field1,field2,...}:
          v1,v2,...
          v1,v2,...
        """
        if not rows:
            return self
        header = f"{name}[{len(rows)}]{{{','.join(fields)}}}:"
        self._parts.append(header)
        for row in rows:
            self._parts.append("  " + ",".join(_safe(v) for v in row))
        return self

    def blank(self) -> "ToonWriter":
        self._parts.append("")
        return self

    def comment(self, text: str) -> "ToonWriter":
        self._parts.append(f"# {text}")
        return self

    def build(self) -> str:
        return "\n".join(self._parts)


# ---------------------------------------------------------------------------
# Knowledge graph → TOON
# ---------------------------------------------------------------------------

def subgraph_to_toon(
    focus_path: str,
    file_nodes: list[dict],
    symbol_nodes: list[dict],
    import_edges: list[tuple[str, str]],
    define_edges: list[tuple[str, str]],
    extends_edges: list[tuple[str, str]],
) -> str:
    """Render a knowledge graph subgraph as a TOON document.

    All lists contain only the nodes/edges relevant to the queried file
    (already filtered by the caller to the 2-hop neighbourhood).
    """
    w = ToonWriter()

    w.kv("focus", focus_path).blank()

    if file_nodes:
        w.table(
            "files",
            ["path", "language", "subproject"],
            [[f["path"], f["language"], f["subproject"]] for f in file_nodes],
        ).blank()

    if symbol_nodes:
        w.table(
            "symbols",
            ["name", "type", "file", "line"],
            [[s["name"], s["stype"], s["path"], s["line"]] for s in symbol_nodes],
        ).blank()

    if import_edges:
        w.table(
            "imports",
            ["from", "to"],
            [[a, b] for a, b in import_edges],
        ).blank()

    if extends_edges:
        w.table(
            "extends",
            ["child", "parent"],
            [[a, b] for a, b in extends_edges],
        ).blank()

    return w.build().strip()


def full_index_to_toon(G: "nx.DiGraph") -> str:  # type: ignore[name-defined]
    """Render the entire graph as a TOON document (human-readable index)."""
    import networkx as nx

    w = ToonWriter()
    w.comment("Relic knowledge graph — full index").blank()

    file_rows = [
        [d["path"], d["language"], d["subproject"]]
        for _, d in sorted(G.nodes(data=True))
        if d.get("ntype") == "file"
    ]
    if file_rows:
        w.table("files", ["path", "language", "subproject"], file_rows).blank()

    sym_rows = [
        [d["name"], d["stype"], d["path"], d["line"]]
        for _, d in sorted(G.nodes(data=True))
        if d.get("ntype") == "symbol"
    ]
    if sym_rows:
        w.table("symbols", ["name", "type", "file", "line"], sym_rows).blank()

    import_rows = [
        [u, v]
        for u, v, d in sorted(G.edges(data=True))
        if d.get("etype") == "imports"
    ]
    if import_rows:
        w.table("imports", ["from", "to"], import_rows).blank()

    extends_rows = [
        [u, v]
        for u, v, d in sorted(G.edges(data=True))
        if d.get("etype") == "extends"
    ]
    if extends_rows:
        w.table("extends", ["child", "parent"], extends_rows).blank()

    return w.build().strip()
