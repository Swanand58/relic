"""Tests for incremental_index and the mtime sidecar (Phase 7.5d)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from relic.indexer import (
    incremental_index,
    load_graph,
    load_mtimes,
    run_index,
    save_mtimes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bump_mtime(path: Path, *, delta_s: float = 5.0) -> None:
    """Force the on-disk mtime forward by *delta_s* seconds.

    Touch alone is unreliable on filesystems with second-resolution mtimes,
    so we set the timestamp explicitly.
    """
    new_t = path.stat().st_mtime + delta_s
    os.utime(path, (new_t, new_t))


def _write_python(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_project(root: Path) -> None:
    _write_python(
        root / "pkg" / "a.py",
        "def alpha():\n    return 1\n",
    )
    _write_python(
        root / "pkg" / "b.py",
        "from pkg.a import alpha\n\ndef beta():\n    return alpha()\n",
    )


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------


def test_run_index_writes_mtimes_sidecar(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"

    run_index(tmp_path, knowledge)

    sidecar = knowledge / "mtimes.json"
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert "pkg/a.py" in data
    assert "pkg/b.py" in data
    assert all(isinstance(v, (int, float)) for v in data.values())


def test_save_mtimes_is_atomic(tmp_path: Path) -> None:
    knowledge = tmp_path / ".knowledge"
    save_mtimes(knowledge, {"a.py": 1.0, "b.py": 2.0})
    # No tmp file should remain after a successful write
    assert not (knowledge / "mtimes.json.tmp").exists()
    assert load_mtimes(knowledge) == {"a.py": 1.0, "b.py": 2.0}


def test_load_mtimes_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_mtimes(tmp_path / ".knowledge") == {}


# ---------------------------------------------------------------------------
# incremental_index — preconditions
# ---------------------------------------------------------------------------


def test_incremental_refuses_without_existing_index(tmp_path: Path) -> None:
    _make_project(tmp_path)
    with pytest.raises(FileNotFoundError):
        incremental_index(tmp_path, tmp_path / ".knowledge")


# ---------------------------------------------------------------------------
# incremental_index — change detection
# ---------------------------------------------------------------------------


def test_incremental_skips_unchanged_files(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    _, summary = incremental_index(tmp_path, knowledge)
    assert summary["added"] == 0
    assert summary["modified"] == 0
    assert summary["deleted"] == 0
    assert summary["unchanged"] >= 2  # both pkg files


def test_incremental_picks_up_new_file(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    _write_python(tmp_path / "pkg" / "c.py", "def gamma():\n    return 3\n")

    G, summary = incremental_index(tmp_path, knowledge)
    assert summary["added"] == 1
    assert summary["modified"] == 0
    assert summary["deleted"] == 0
    assert "pkg/c.py" in G.nodes
    assert "gamma@pkg/c.py" in G.nodes


def test_incremental_picks_up_modified_file(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)
    G_before = load_graph(knowledge)
    assert "alpha@pkg/a.py" in G_before.nodes
    assert "renamed@pkg/a.py" not in G_before.nodes

    target = tmp_path / "pkg" / "a.py"
    target.write_text("def renamed():\n    return 1\n", encoding="utf-8")
    _bump_mtime(target)

    G, summary = incremental_index(tmp_path, knowledge)
    assert summary["modified"] == 1
    assert summary["added"] == 0
    assert "renamed@pkg/a.py" in G.nodes
    assert "alpha@pkg/a.py" not in G.nodes


def test_incremental_handles_deleted_file(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    (tmp_path / "pkg" / "a.py").unlink()

    G, summary = incremental_index(tmp_path, knowledge)
    assert summary["deleted"] == 1
    assert "pkg/a.py" not in G.nodes
    assert "alpha@pkg/a.py" not in G.nodes


# ---------------------------------------------------------------------------
# incremental_index — cross-file edges remain consistent
# ---------------------------------------------------------------------------


def test_incremental_re_resolves_uses_edge_after_modification(tmp_path: Path) -> None:
    """When a.py renames its export, b.py's `uses` edge must point to the new symbol."""
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    G_before = load_graph(knowledge)
    assert G_before.has_edge("pkg/b.py", "alpha@pkg/a.py")

    a_py = tmp_path / "pkg" / "a.py"
    a_py.write_text("def alpha():\n    return 99\n", encoding="utf-8")
    _bump_mtime(a_py)

    b_py = tmp_path / "pkg" / "b.py"
    b_py.write_text("from pkg.a import alpha\n\ndef beta():\n    return alpha() + 1\n", encoding="utf-8")
    _bump_mtime(b_py)

    G, summary = incremental_index(tmp_path, knowledge)
    assert summary["modified"] == 2
    assert G.has_edge("pkg/b.py", "alpha@pkg/a.py"), "uses edge must be re-resolved against current symbols"


def test_incremental_drops_uses_edge_when_target_symbol_disappears(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    a_py = tmp_path / "pkg" / "a.py"
    a_py.write_text("def renamed():\n    return 1\n", encoding="utf-8")
    _bump_mtime(a_py)

    G, _ = incremental_index(tmp_path, knowledge)
    assert not G.has_edge("pkg/b.py", "alpha@pkg/a.py")


# ---------------------------------------------------------------------------
# MCP handler integration
# ---------------------------------------------------------------------------


def test_mcp_reindex_refuses_without_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    from relic.mcp_server import _handle_reindex

    result = asyncio.run(_handle_reindex())
    assert len(result) == 1
    assert "no index found" in result[0].text.lower()
    assert "relic index" in result[0].text


def test_mcp_reindex_runs_incrementally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    run_index(tmp_path, tmp_path / ".knowledge")

    from relic.mcp_server import _handle_reindex

    result = asyncio.run(_handle_reindex())
    text = result[0].text
    assert "incremental" in text
    assert "0 new" in text
    assert "0 modified" in text
