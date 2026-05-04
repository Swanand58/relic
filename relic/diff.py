"""Diff engine — compares current source state against the last indexed graph.

Tells agents (and humans) what changed since the last ``relic index``:
  - New files not yet in the graph
  - Deleted files still in the graph but gone from disk
  - Modified files whose symbols differ from the indexed snapshot

This lets agents decide whether to call ``relic_reindex`` before querying.
"""

from __future__ import annotations

from pathlib import Path

from relic.indexer import (
    LANGUAGE_MAP,
    _analyse_python,
    _analyse_typescript,
    _collect_source_files,
    _posix_rel,
    load_graph,
)
from relic.toon import ToonWriter


def _symbol_fingerprint(source: str, rel_path: str, project_root: Path, lang: str) -> set[str]:
    """Return a set of ``name:stype`` strings for symbols in a source file."""
    if lang == "python":
        _, symbols, _ = _analyse_python(source, rel_path, project_root)
    elif lang in ("typescript", "javascript"):
        _, symbols, _ = _analyse_typescript(source, rel_path, project_root)
    else:
        return set()
    return {f"{s['name']}:{s['stype']}" for s in symbols}


def compute_diff(
    project_root: Path,
    knowledge_dir: Path,
    config_path: Path,
) -> dict:
    """Compare on-disk source files against the indexed graph.

    Returns a dict with:
        new_files     — list of paths not in the graph
        deleted_files — list of paths in the graph but missing from disk
        changed_files — list of dicts {path, added_symbols, removed_symbols}
        stale         — bool, True if any of the above is non-empty
    """
    import yaml

    G = load_graph(knowledge_dir)

    with config_path.open() as f:
        config = yaml.safe_load(f) or {}
    subprojects = config.get("subprojects", {})

    disk_files = _collect_source_files(project_root, subprojects)
    disk_rels = {_posix_rel(p, project_root) for p, _ in disk_files}
    graph_files = {n for n, d in G.nodes(data=True) if d.get("ntype") == "file"}

    new_files = sorted(disk_rels - graph_files)
    deleted_files = sorted(graph_files - disk_rels)

    changed_files: list[dict] = []
    for abs_path, _sp in disk_files:
        rel = _posix_rel(abs_path, project_root)
        if rel not in graph_files:
            continue

        lang = LANGUAGE_MAP.get(abs_path.suffix, "other")
        if lang == "other":
            continue

        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            continue

        disk_syms = _symbol_fingerprint(source, rel, project_root, lang)

        graph_syms: set[str] = set()
        for succ in G.successors(rel):
            d = G.nodes[succ]
            if d.get("ntype") == "symbol":
                graph_syms.add(f"{d['name']}:{d['stype']}")

        added = sorted(disk_syms - graph_syms)
        removed = sorted(graph_syms - disk_syms)
        if added or removed:
            changed_files.append({"path": rel, "added_symbols": added, "removed_symbols": removed})

    return {
        "new_files": new_files,
        "deleted_files": deleted_files,
        "changed_files": changed_files,
        "stale": bool(new_files or deleted_files or changed_files),
    }


def diff_to_toon(result: dict) -> str:
    """Render diff result as TOON."""
    w = ToonWriter()

    if not result["stale"]:
        w.kv("status", "up-to-date")
        return w.build()

    w.kv("status", "stale — run relic_reindex").blank()

    if result["new_files"]:
        w.table("new_files", ["path"], [[f] for f in result["new_files"]]).blank()

    if result["deleted_files"]:
        w.table("deleted_files", ["path"], [[f] for f in result["deleted_files"]]).blank()

    if result["changed_files"]:
        rows = []
        for ch in result["changed_files"]:
            added = " ".join(ch["added_symbols"]) or "-"
            removed = " ".join(ch["removed_symbols"]) or "-"
            rows.append([ch["path"], added, removed])
        w.table("changed_files", ["path", "added", "removed"], rows).blank()

    return w.build().strip()
