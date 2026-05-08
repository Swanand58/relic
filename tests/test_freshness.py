"""Tests for the freshness signal and the `index{...}` header (Phase 7.5a)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from relic import freshness as fr
from relic.indexer import run_index


def _bump_mtime(path: Path, *, delta_s: float = 5.0) -> None:
    new_t = path.stat().st_mtime + delta_s
    os.utime(path, (new_t, new_t))


def _write_python(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_project(root: Path) -> None:
    _write_python(root / "pkg" / "a.py", "def alpha():\n    return 1\n")
    _write_python(root / "pkg" / "b.py", "def beta():\n    return 2\n")


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with an empty freshness cache."""
    fr.invalidate()
    yield
    fr.invalidate()


# ---------------------------------------------------------------------------
# freshness() core behaviour
# ---------------------------------------------------------------------------


def test_freshness_reports_not_indexed(tmp_path: Path) -> None:
    f = fr.freshness(tmp_path, tmp_path / ".knowledge")
    assert f["indexed"] is False
    assert f["stale"] is True


def test_freshness_clean_after_index(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    f = fr.freshness(tmp_path, knowledge)
    assert f["indexed"] is True
    assert f["stale"] is False
    assert f["files_changed"] == 0
    assert f["age_s"] >= 0


def test_freshness_detects_modified_file(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    target = tmp_path / "pkg" / "a.py"
    target.write_text("def alpha():\n    return 99\n", encoding="utf-8")
    _bump_mtime(target)

    fr.invalidate()  # bypass the TTL cache for the assertion
    f = fr.freshness(tmp_path, knowledge)
    assert f["stale"] is True
    assert f["files_changed"] == 1


def test_freshness_detects_added_file(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    _write_python(tmp_path / "pkg" / "c.py", "def gamma():\n    return 3\n")

    fr.invalidate()
    f = fr.freshness(tmp_path, knowledge)
    assert f["stale"] is True
    assert f["files_changed"] == 1


def test_freshness_detects_deleted_file(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    (tmp_path / "pkg" / "a.py").unlink()

    fr.invalidate()
    f = fr.freshness(tmp_path, knowledge)
    assert f["stale"] is True
    assert f["files_changed"] == 1


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_freshness_cache_returns_same_object(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    a = fr.freshness(tmp_path, knowledge)
    b = fr.freshness(tmp_path, knowledge)
    assert a is b, "back-to-back freshness calls inside the TTL must return the cached dict"


def test_invalidate_forces_recompute(tmp_path: Path) -> None:
    _make_project(tmp_path)
    knowledge = tmp_path / ".knowledge"
    run_index(tmp_path, knowledge)

    first = fr.freshness(tmp_path, knowledge)
    fr.invalidate()
    second = fr.freshness(tmp_path, knowledge)
    assert first == second
    assert first is not second


# ---------------------------------------------------------------------------
# header() rendering
# ---------------------------------------------------------------------------


def test_header_when_indexed() -> None:
    line = fr.header({"indexed": True, "age_s": 42, "stale": False, "files_changed": 0})
    assert line == "index{age_s,stale,files_changed}: 42,false,0"


def test_header_when_stale() -> None:
    line = fr.header({"indexed": True, "age_s": 7, "stale": True, "files_changed": 3})
    assert "stale=true" not in line  # values are positional, not key=value
    assert line.endswith(": 7,true,3")


def test_header_when_not_indexed() -> None:
    line = fr.header({"indexed": False, "age_s": -1, "stale": True, "files_changed": -1})
    assert "false" in line


# ---------------------------------------------------------------------------
# MCP integration — every response carries the header
# ---------------------------------------------------------------------------


class _MCPCase:
    """Helper to drive MCP handlers in an indexed tmp project."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_project(tmp_path)
        monkeypatch.chdir(tmp_path)
        run_index(tmp_path, tmp_path / ".knowledge")
        fr.invalidate()

    @staticmethod
    def has_header(text: str) -> bool:
        return text.startswith("index{")


def test_query_response_carries_freshness_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _MCPCase(tmp_path, monkeypatch)
    from relic.mcp_server import _handle_query

    out = _handle_query({"target": "pkg/a.py"})
    assert case.has_header(out[0].text)


def test_search_response_carries_freshness_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _MCPCase(tmp_path, monkeypatch)
    from relic.mcp_server import _handle_search

    out = _handle_search({"query": "alpha"})
    assert case.has_header(out[0].text)


def test_reindex_response_carries_freshness_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _MCPCase(tmp_path, monkeypatch)
    from relic.mcp_server import _handle_reindex

    out = asyncio.run(_handle_reindex())
    assert case.has_header(out[0].text)


def test_diff_response_carries_freshness_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _MCPCase(tmp_path, monkeypatch)
    from relic.mcp_server import _handle_diff

    out = _handle_diff()
    assert case.has_header(out[0].text)


def test_query_error_response_still_carries_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even validation errors prefix the header — agents always know freshness."""
    _MCPCase(tmp_path, monkeypatch)
    from relic.mcp_server import _handle_query

    out = _handle_query({"target": ""})  # missing target
    assert out[0].text.startswith("index{")
    assert "Error" in out[0].text


def test_no_index_response_still_carries_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    fr.invalidate()
    from relic.mcp_server import _handle_query

    out = _handle_query({"target": "anything"})
    assert out[0].text.startswith("index{indexed")  # not-indexed variant
    assert "no index found" in out[0].text.lower()


# ---------------------------------------------------------------------------
# relic_stats tool is gone
# ---------------------------------------------------------------------------


def test_relic_stats_tool_no_longer_registered() -> None:
    from relic.mcp_server import list_tools

    names = {t.name for t in asyncio.run(list_tools())}
    assert "relic_stats" not in names


def test_relic_stats_dispatch_raises() -> None:
    from relic.mcp_server import call_tool

    with pytest.raises(ValueError, match="Unknown tool"):
        asyncio.run(call_tool("relic_stats", {}))
