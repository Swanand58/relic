"""TOON serializer — converts NetworkX subgraphs to Token-Oriented Object Notation.

TOON uses a tabular layout for uniform arrays (declare field names once, list
values row by row) so LLMs parse it in far fewer tokens than JSON.

We implement the subset needed for knowledge graph output:
    - Key-value block:  key: value
    - Tabular array:    name[N]{col1,col2,...}:\n  v1,v2,...\n  v1,v2,...

Reference: https://toonformat.dev
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import networkx as nx

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
    tested_by_edges: list[tuple[str, str]] | None = None,
    uses_edges: list[tuple[str, str]] | None = None,
    calls_edges: list[tuple[str, str]] | None = None,
    exclude_tests: bool = False,
    max_neighbor_symbols: int = 0,
    include_intent: bool = True,
) -> str:
    """Render a knowledge graph subgraph as a TOON document.

    Symbols are split into two sections for token efficiency:
    - exports: full detail (name, type, line, signature) for the focus file
    - neighbor_symbols: name, type, file, signature for other files

    When *exclude_tests* is True, symbols from test files are dropped from
    ``neighbor_symbols`` (saves 40–60% tokens at depth 2).  Test files still
    appear in ``neighbors`` and ``tested_by`` edges are preserved.
    """
    from relic.indexer import _is_test_file

    w = ToonWriter()

    w.kv("focus", focus_path).blank()

    neighbor_files = [f for f in file_nodes if f["path"] != focus_path]
    if neighbor_files:
        w.table(
            "neighbors",
            ["path", "language"],
            [[f["path"], f["language"]] for f in neighbor_files],
        ).blank()

    focus_symbols = [s for s in symbol_nodes if s["path"] == focus_path]
    if focus_symbols:
        if include_intent:
            w.table(
                "exports",
                ["name", "type", "line", "signature", "intent"],
                [
                    [s["name"], s["stype"], s["line"], s.get("signature", ""), s.get("intent", "")]
                    for s in focus_symbols
                ],
            ).blank()
        else:
            w.table(
                "exports",
                ["name", "type", "line", "signature"],
                [[s["name"], s["stype"], s["line"], s.get("signature", "")] for s in focus_symbols],
            ).blank()

    if include_intent:
        dec_rows: list[list] = []
        for s in focus_symbols:
            for dec in s.get("decorators", []):
                args_repr = str(dec.get("args", []))
                dec_rows.append([s["name"], dec["name"], args_repr])
        if dec_rows:
            w.table("decorators", ["symbol", "decorator", "args"], dec_rows).blank()

    neighbor_symbols = [s for s in symbol_nodes if s["path"] != focus_path]
    if exclude_tests:
        neighbor_symbols = [s for s in neighbor_symbols if not _is_test_file(s["path"])]
    truncated_count = 0
    if max_neighbor_symbols > 0 and len(neighbor_symbols) > max_neighbor_symbols:
        all_edges = list(import_edges) + list(define_edges) + list(extends_edges)
        all_edges += list(tested_by_edges or []) + list(uses_edges or []) + list(calls_edges or [])
        edge_refs: dict[str, int] = {}
        for u, v in all_edges:
            edge_refs[u] = edge_refs.get(u, 0) + 1
            edge_refs[v] = edge_refs.get(v, 0) + 1
        neighbor_symbols.sort(
            key=lambda s: edge_refs.get(f"{s['name']}@{s['path']}", 0),
            reverse=True,
        )
        truncated_count = len(neighbor_symbols) - max_neighbor_symbols
        neighbor_symbols = neighbor_symbols[:max_neighbor_symbols]
    if neighbor_symbols:
        w.table(
            "neighbor_symbols",
            ["name", "type", "file", "signature"],
            [[s["name"], s["stype"], s["path"], s.get("signature", "")] for s in neighbor_symbols],
        )
        if truncated_count > 0:
            w.comment(f"... and {truncated_count} more (use --depth 1 or exclude_tests=false to adjust)")
        w.blank()

    focus_imports = [(a, b) for a, b in import_edges if a == focus_path]
    focus_imported_by = [(a, b) for a, b in import_edges if b == focus_path]

    if focus_imports:
        w.table(
            "imports",
            ["from", "to"],
            [[a, b] for a, b in focus_imports],
        ).blank()

    if focus_imported_by:
        w.table(
            "imported_by",
            ["from", "to"],
            [[a, b] for a, b in focus_imported_by],
        ).blank()

    if tested_by_edges:
        focus_tested_by = [(a, b) for a, b in tested_by_edges if a == focus_path]
        if focus_tested_by:
            w.table(
                "tested_by",
                ["source", "test"],
                [[a, b] for a, b in focus_tested_by],
            ).blank()

    focus_extends = [(a, b) for a, b in extends_edges if a == focus_path or b == focus_path]
    if focus_extends:
        w.table(
            "extends",
            ["child", "parent"],
            [[a, b] for a, b in focus_extends],
        ).blank()

    # Callers — which files use symbols defined in the focus file
    if uses_edges:
        focus_symbol_ids = {f"{s['name']}@{focus_path}" for s in focus_symbols}
        callers = [(a, b) for a, b in uses_edges if b in focus_symbol_ids and a != focus_path]
        if callers:
            w.table(
                "callers",
                ["file", "symbol"],
                [[a, b.split("@")[0]] for a, b in callers],
            ).blank()

    if calls_edges:
        focus_symbol_ids = {f"{s['name']}@{focus_path}" for s in focus_symbols}
        # Outbound: focus file's symbols calling other symbols
        outbound = [(a, b) for a, b in calls_edges if a in focus_symbol_ids]
        if outbound:
            w.table(
                "calls",
                ["caller", "callee"],
                [[a.split("@")[0], b.split("@")[0]] for a, b in outbound],
            ).blank()
        # Inbound: other symbols calling into focus file's symbols
        inbound = [(a, b) for a, b in calls_edges if b in focus_symbol_ids and a not in focus_symbol_ids]
        if inbound:
            w.table(
                "called_by",
                ["caller", "callee"],
                [[a.split("@")[0], b.split("@")[0]] for a, b in inbound],
            ).blank()

    return w.build().strip()


def candidates_to_toon(target: str, candidates: list[dict]) -> str:
    """Render a TOON list of symbol candidates when a name matches multiple symbols.

    Used by `relic_query` / `relic query` to surface every definition of an
    ambiguous symbol so the agent can re-query with the full file path.

    Each candidate dict is expected to carry: name, stype, path, line.
    """
    w = ToonWriter()
    w.kv("ambiguous", f"'{target}' matches {len(candidates)} symbols").blank()
    w.table(
        "candidates",
        ["name", "type", "file", "line"],
        [[d.get("name", ""), d.get("stype", ""), d.get("path", ""), d.get("line", 0)] for d in candidates],
    )
    return w.build().strip()


def full_index_to_toon(G: nx.DiGraph) -> str:
    """Render the entire graph as a TOON document (human-readable index)."""

    w = ToonWriter()
    w.comment("Relic knowledge graph — full index").blank()

    file_rows = [[d["path"], d["language"]] for _, d in sorted(G.nodes(data=True)) if d.get("ntype") == "file"]
    if file_rows:
        w.table("files", ["path", "language"], file_rows).blank()

    sym_rows = [
        [d["name"], d["stype"], d["path"], d["line"], d.get("signature", ""), d.get("intent", "")]
        for _, d in sorted(G.nodes(data=True))
        if d.get("ntype") == "symbol"
    ]
    if sym_rows:
        w.table("symbols", ["name", "type", "file", "line", "signature", "intent"], sym_rows).blank()

    import_rows = [[u, v] for u, v, d in sorted(G.edges(data=True)) if d.get("etype") == "imports"]
    if import_rows:
        w.table("imports", ["from", "to"], import_rows).blank()

    extends_rows = [[u, v] for u, v, d in sorted(G.edges(data=True)) if d.get("etype") == "extends"]
    if extends_rows:
        w.table("extends", ["child", "parent"], extends_rows).blank()

    uses_rows = [[u, v] for u, v, d in sorted(G.edges(data=True)) if d.get("etype") == "uses"]
    if uses_rows:
        w.table("uses", ["file", "symbol"], uses_rows).blank()

    calls_rows = [[u, v] for u, v, d in sorted(G.edges(data=True)) if d.get("etype") == "calls"]
    if calls_rows:
        w.table("calls", ["caller", "callee"], calls_rows).blank()

    tested_by_rows = [[u, v] for u, v, d in sorted(G.edges(data=True)) if d.get("etype") == "tested_by"]
    if tested_by_rows:
        w.table("tested_by", ["source", "test"], tested_by_rows).blank()

    return w.build().strip()
