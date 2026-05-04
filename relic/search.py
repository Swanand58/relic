"""Shared search logic — scored substring search across the knowledge graph.

Used by both the CLI (`relic search`) and the MCP server (`relic_search` tool)
so ranking and filtering behave identically regardless of entry point.

Scoring:
    exact match (case-insensitive)  → 3
    prefix match                    → 2
    substring match                 → 1
    no match                        → 0

Ties are broken by node degree (higher first) so well-connected files and
symbols surface above isolated ones.
"""

from __future__ import annotations

from typing import Literal

import networkx as nx

from relic.toon import ToonWriter

Kind = Literal["file", "symbol", "all"]

SCORE_EXACT = 3
SCORE_PREFIX = 2
SCORE_SUBSTRING = 1


def _normalize(s: str) -> str:
    """Lowercase + strip non-alphanumeric — bridges snake_case / kebab-case / camelCase.

    Used by `suggest_close_matches` so that a typo like `payment_processor` still
    surfaces `PaymentProcessor` (both normalize to `paymentprocessor`).
    """
    return "".join(c for c in s.lower() if c.isalnum())


def suggest_close_matches(G: nx.DiGraph, target: str, limit: int = 3) -> list[str]:
    """Return up to `limit` "did you mean?" suggestions for an unresolved target.

    Scans every file and symbol node, applying `_score` against the normalized
    forms (so capitalization and underscores don't matter). Returns lines like
    "file: src/foo.py" and "symbol: PaymentProcessor (src/foo.py)" ordered by
    score then degree, capped at `limit`.

    Empty list if the target is empty or nothing scores above zero.
    """
    needle = _normalize(target)
    if not needle:
        return []

    candidates: list[tuple[int, int, str]] = []  # (score, degree, label)
    for n, d in G.nodes(data=True):
        ntype = d.get("ntype")
        if ntype == "file":
            score = _score(_normalize(n), needle)
            if score:
                candidates.append((score, G.degree(n), f"file: {n}"))
        elif ntype == "symbol":
            score = _score(_normalize(d.get("name", "")), needle)
            if score:
                candidates.append(
                    (
                        score,
                        G.degree(n),
                        f"symbol: {d.get('name', '')} ({d.get('path', '')})",
                    )
                )

    candidates.sort(key=lambda item: (-item[0], -item[1]))
    return [label for _, _, label in candidates[:limit]]


def available_subprojects(G: nx.DiGraph) -> set[str]:
    """Return the set of subproject names that appear on file nodes in the graph.

    Used by callers to validate `--subproject` arguments and surface a useful
    error instead of returning silently empty results.
    """
    return {d["subproject"] for _, d in G.nodes(data=True) if d.get("ntype") == "file" and d.get("subproject")}


def _score(haystack: str, needle: str) -> int:
    """Return 0 if no match, else a relevance score (higher = better)."""
    if haystack == needle:
        return SCORE_EXACT
    if haystack.startswith(needle):
        return SCORE_PREFIX
    if needle in haystack:
        return SCORE_SUBSTRING
    return 0


def search_graph(
    G: nx.DiGraph,
    query: str,
    kind: Kind = "all",
    subproject: str | None = None,
    limit: int = 20,
) -> tuple[list[dict], list[dict]]:
    """Run a scored substring search across file and symbol nodes.

    Returns (file_matches, symbol_matches) ordered by score DESC then node
    degree DESC, capped at `limit` per category.

    `subproject`, if provided, restricts results to nodes belonging to that
    subproject. Symbol nodes inherit their defining file's subproject.
    """
    needle = query.lower().strip()
    if not needle:
        return [], []

    file_subproject: dict[str, str] = {
        n: d.get("subproject", "") for n, d in G.nodes(data=True) if d.get("ntype") == "file"
    }

    file_hits: list[tuple[int, int, dict]] = []
    symbol_hits: list[tuple[int, int, dict]] = []

    for n, d in G.nodes(data=True):
        ntype = d.get("ntype")
        if ntype == "file" and kind in ("file", "all"):
            if subproject and d.get("subproject") != subproject:
                continue
            score = _score(n.lower(), needle)
            if score:
                file_hits.append((score, G.degree(n), d))
        elif ntype == "symbol" and kind in ("symbol", "all"):
            sym_subproject = file_subproject.get(d.get("path", ""), "")
            if subproject and sym_subproject != subproject:
                continue
            score = _score(d.get("name", "").lower(), needle)
            if score:
                symbol_hits.append((score, G.degree(n), d))

    file_hits.sort(key=lambda item: (-item[0], -item[1]))
    symbol_hits.sort(key=lambda item: (-item[0], -item[1]))

    return (
        [d for _, _, d in file_hits[:limit]],
        [d for _, _, d in symbol_hits[:limit]],
    )


def render_search_toon(
    query: str,
    file_matches: list[dict],
    symbol_matches: list[dict],
) -> str:
    """Render search results as a TOON document.

    Returns a plain "No results" string when both lists are empty so callers
    can pass it straight through to stdout or an MCP TextContent.
    """
    if not file_matches and not symbol_matches:
        return f"No results for '{query}'."

    w = ToonWriter()
    w.kv("search", query).blank()

    if file_matches:
        w.table(
            "file_matches",
            ["path", "language", "subproject"],
            [[d.get("path", ""), d.get("language", ""), d.get("subproject", "")] for d in file_matches],
        ).blank()

    if symbol_matches:
        w.table(
            "symbol_matches",
            ["name", "type", "file"],
            [[d.get("name", ""), d.get("stype", ""), d.get("path", "")] for d in symbol_matches],
        ).blank()

    return w.build().strip()
