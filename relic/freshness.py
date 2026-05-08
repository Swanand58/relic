"""Cheap, cached freshness signal for the knowledge graph.

Computes whether the on-disk source tree has drifted from the saved index
without ever loading the pickled graph.  Powers the ``index{...}`` header
prefixed to every MCP response so agents never have to call a separate
"is the index stale?" tool.

A short TTL cache keeps the cost amortised across rapid back-to-back
queries from the same agent turn.  Cost: one ``stat()`` per source file,
filtered by the same skip / ignore rules the indexer uses.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from relic.indexer import (
    _collect_source_files,
    _load_subprojects,
    _posix_rel,
    _safe_mtime,
    load_mtimes,
)

CACHE_TTL_SECONDS = 2.0

_lock = threading.Lock()
_cache: dict | None = None
_cache_t: float = 0.0
_cache_key: tuple | None = None


def _key(project_root: Path, knowledge_dir: Path, config_file: Path | None) -> tuple:
    return (
        str(project_root.resolve()),
        str(knowledge_dir.resolve()),
        str(config_file.resolve()) if config_file else "",
    )


def freshness(
    project_root: Path,
    knowledge_dir: Path,
    config_file: Path | None = None,
) -> dict:
    """Return ``{indexed, age_s, stale, files_changed}`` for the current index.

    ``indexed`` is False when no index file exists; the other fields are then
    sentinel values.  Otherwise:

      * ``age_s``         seconds since ``index.pkl`` was last written
      * ``stale``         True iff at least one source file is added,
                          modified, or deleted relative to the sidecar
      * ``files_changed`` total count of differing files

    Result is cached for ``CACHE_TTL_SECONDS`` seconds (keyed on the resolved
    project / knowledge / config paths).  Call :func:`invalidate` after a
    reindex so the next call recomputes immediately.
    """
    global _cache, _cache_t, _cache_key

    now = time.monotonic()
    key = _key(project_root, knowledge_dir, config_file)

    with _lock:
        if _cache is not None and _cache_key == key and (now - _cache_t) < CACHE_TTL_SECONDS:
            return _cache

    result = _compute(project_root, knowledge_dir, config_file)

    with _lock:
        _cache = result
        _cache_t = now
        _cache_key = key

    return result


def _compute(project_root: Path, knowledge_dir: Path, config_file: Path | None) -> dict:
    index_path = knowledge_dir / "index.pkl"
    if not index_path.exists():
        return {"indexed": False, "age_s": -1, "stale": True, "files_changed": -1}

    age_s = max(0, int(time.time() - index_path.stat().st_mtime))
    old_mtimes = load_mtimes(knowledge_dir)
    if not old_mtimes:
        # Index exists but no sidecar (older format) — don't claim stale.
        return {"indexed": True, "age_s": age_s, "stale": False, "files_changed": 0}

    try:
        subprojects = _load_subprojects(config_file)
        files, _ = _collect_source_files(project_root, subprojects)
    except Exception:
        # Never let the freshness sweep crash the actual response.
        return {"indexed": True, "age_s": age_s, "stale": False, "files_changed": 0}

    current = {_posix_rel(p, project_root): _safe_mtime(p) for p, _ in files}
    old_paths = set(old_mtimes.keys())
    new_paths = set(current.keys())
    added = new_paths - old_paths
    deleted = old_paths - new_paths
    modified = sum(1 for r in (new_paths & old_paths) if current[r] != old_mtimes.get(r, 0.0))
    files_changed = len(added) + len(deleted) + modified
    return {
        "indexed": True,
        "age_s": age_s,
        "stale": files_changed > 0,
        "files_changed": files_changed,
    }


def invalidate() -> None:
    """Reset the freshness cache.  Call after reindex so the next call is fresh."""
    global _cache, _cache_t, _cache_key
    with _lock:
        _cache = None
        _cache_t = 0.0
        _cache_key = None


def header(f: dict) -> str:
    """Render the single-line TOON freshness header for a freshness dict."""
    if not f.get("indexed", True):
        return "index{indexed,stale}: false,true"
    return f"index{{age_s,stale,files_changed}}: {f['age_s']},{str(f['stale']).lower()},{f['files_changed']}"
