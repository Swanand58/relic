"""Tests for MCP server handlers — disambiguation, search delegation, errors.

These hit the handler functions directly (not the async stdio loop). The
handlers read from a `.knowledge/index.pkl` resolved relative to cwd, so
tests use the `tmp_project` fixture (which chdir's into a real indexed tree).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from relic.mcp_server import (
    _handle_query,
    _handle_search,
    _resolve_node,
    list_tools,
)


# ---------------------------------------------------------------------------
# _resolve_node — file path single-match, symbol multi-match
# ---------------------------------------------------------------------------

class TestResolveNode:
    def test_file_path_returns_single_match(self, sample_graph):
        result = _resolve_node(sample_graph, "payments/processor.py")
        assert result == ["payments/processor.py"]

    def test_file_path_with_dot_slash_prefix(self, sample_graph):
        result = _resolve_node(sample_graph, "./payments/processor.py")
        assert result == ["payments/processor.py"]

    def test_unknown_returns_empty_list(self, sample_graph):
        assert _resolve_node(sample_graph, "ghost/file.py") == []

    def test_unique_symbol_returns_one_match(self, sample_graph):
        result = _resolve_node(sample_graph, "PaymentProcessor")
        assert len(result) == 1
        assert "PaymentProcessor@" in result[0]

    def test_ambiguous_symbol_returns_all_matches(self, sample_graph):
        result = _resolve_node(sample_graph, "process")
        assert len(result) == 2
        assert all("process@" in m for m in result)

    def test_file_match_takes_precedence_over_symbol(self, sample_graph):
        # if a file path and a symbol name both happened to match,
        # the file path wins (returned alone).
        # `payments/processor.py` is only a file node, so this just
        # exercises the precedence rule.
        result = _resolve_node(sample_graph, "payments/processor.py")
        assert result == ["payments/processor.py"]


# ---------------------------------------------------------------------------
# _handle_query — disambiguation path
# ---------------------------------------------------------------------------

class TestHandleQuery:
    def test_missing_target_returns_error(self, tmp_project: Path):
        out = _handle_query({})
        assert "Error: target is required" in out[0].text

    def test_no_index_returns_error(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # tmp_path has no .knowledge/
        out = _handle_query({"target": "anything"})
        assert "no index found" in out[0].text.lower()

    def test_unknown_target_returns_not_found(self, tmp_project: Path):
        out = _handle_query({"target": "ghost_symbol_xyz"})
        assert "Not found" in out[0].text

    def test_typo_target_includes_did_you_mean(self, tmp_project: Path):
        # `payment_processor` is a snake_case typo for `PaymentProcessor`
        out = _handle_query({"target": "payment_processor"})
        text = out[0].text
        assert "Not found" in text
        assert "Did you mean?" in text
        assert "PaymentProcessor" in text

    def test_unrecoverable_typo_omits_did_you_mean(self, tmp_project: Path):
        out = _handle_query({"target": "qqqqqqq_unrelated"})
        text = out[0].text
        assert "Not found" in text
        assert "Did you mean?" not in text

    def test_unique_symbol_returns_toon_subgraph(self, tmp_project: Path):
        # `PaymentProcessor` is unique in the indexed project
        out = _handle_query({"target": "PaymentProcessor"})
        text = out[0].text
        assert "focus:" in text
        assert "ambiguous" not in text

    def test_ambiguous_symbol_returns_candidate_list(self, tmp_project: Path):
        # Two `process` definitions in the indexed project.
        out = _handle_query({"target": "process"})
        text = out[0].text
        assert "ambiguous: 'process' matches 2 symbols" in text
        assert "candidates[2]{name,type,file,line}:" in text

    def test_file_path_query_returns_subgraph(self, tmp_project: Path):
        out = _handle_query({"target": "src/processor.py"})
        text = out[0].text
        assert "focus: src/processor.py" in text

    def test_depth_argument_respected(self, tmp_project: Path):
        # depth=0 returns just the focus node, no neighbors.
        out = _handle_query({"target": "src/processor.py", "depth": 0})
        text = out[0].text
        assert "focus: src/processor.py" in text
        assert "neighbors[" not in text


# ---------------------------------------------------------------------------
# _handle_search — delegates to search_graph
# ---------------------------------------------------------------------------

class TestHandleSearch:
    def test_missing_query_returns_error(self, tmp_project: Path):
        out = _handle_search({})
        assert "Error: query is required" in out[0].text

    def test_no_index_returns_error(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out = _handle_search({"query": "anything"})
        assert "no index found" in out[0].text.lower()

    def test_known_symbol_returns_toon(self, tmp_project: Path):
        out = _handle_search({"query": "PaymentProcessor"})
        text = out[0].text
        assert "search:" in text
        assert "PaymentProcessor" in text

    def test_unknown_query_returns_no_results(self, tmp_project: Path):
        out = _handle_search({"query": "definitely_not_present_xyz"})
        assert "No results" in out[0].text

    def test_invalid_subproject_returns_error_with_available(self, tmp_project: Path):
        out = _handle_search({"query": "foo", "subproject": "ghost"})
        text = out[0].text
        assert "no such subproject 'ghost'" in text
        # the indexed project's only subproject is `app`
        assert "app" in text

    def test_valid_subproject_proceeds(self, tmp_project: Path):
        out = _handle_search({"query": "process", "subproject": "app"})
        assert "no such subproject" not in out[0].text


# ---------------------------------------------------------------------------
# Tool registry — descriptions must stay imperative / specific
# ---------------------------------------------------------------------------

class TestToolDescriptions:
    """Pin the imperative wording of each MCP tool description.

    These are read by the agent at session start and influence whether it
    actually calls the tool. They must stay imperative and call out the
    `when` not just the `what`.
    """

    def _tools_by_name(self):
        tools = asyncio.run(list_tools())
        return {t.name: t for t in tools}

    def test_all_four_tools_registered(self):
        tools = self._tools_by_name()
        assert set(tools.keys()) == {
            "relic_query", "relic_search", "relic_reindex", "relic_stats"
        }

    def test_query_describes_disambiguation(self):
        tools = self._tools_by_name()
        # disambiguation behavior must be discoverable from the description
        assert "ambiguous" in tools["relic_query"].description.lower()

    def test_search_describes_ranking(self):
        tools = self._tools_by_name()
        desc = tools["relic_search"].description.lower()
        assert "ranked" in desc or "rank" in desc

    def test_reindex_warns_about_staleness(self):
        tools = self._tools_by_name()
        desc = tools["relic_reindex"].description.lower()
        # the cost of skipping reindex must be spelled out
        assert "stale" in desc or "wrong" in desc

    def test_stats_calls_for_followup_action(self):
        tools = self._tools_by_name()
        desc = tools["relic_stats"].description.lower()
        # description should chain to relic_reindex when the index is old
        assert "relic_reindex" in desc


# ---------------------------------------------------------------------------
# Cache stability — list_tools() output must be byte-identical across calls
# and across cwds. Anthropic's prompt cache (and any other LLM cache layer)
# invalidates on a single byte changing in the system prompt. If our tool
# definitions silently embedded a timestamp, project name, or counter, every
# turn would miss the cache and pay full price for ~360 tokens.
# ---------------------------------------------------------------------------

class TestToolDefinitionStability:
    def _serialize(self):
        tools = asyncio.run(list_tools())
        # Serialise the parts an LLM would actually see: name, description,
        # and inputSchema. Reduces noise from non-payload Tool fields.
        return [
            (t.name, t.description, str(t.inputSchema))
            for t in tools
        ]

    def test_two_calls_produce_identical_output(self):
        first = self._serialize()
        second = self._serialize()
        assert first == second

    def test_cwd_does_not_leak_into_definitions(self, tmp_path: Path, monkeypatch):
        # Move into an empty tmp dir, snapshot the tools, move into the
        # real project, snapshot again. They must match — anything that
        # changes here would silently invalidate the LLM's prompt cache
        # for every relic-enabled session.
        monkeypatch.chdir(tmp_path)
        from_empty = self._serialize()
        repo_root = Path(__file__).resolve().parent.parent
        monkeypatch.chdir(repo_root)
        from_repo = self._serialize()
        assert from_empty == from_repo

    def test_no_dynamic_content_in_descriptions(self):
        # Belt-and-braces: scan each description for known leaky patterns.
        import re
        suspicious = re.compile(
            r"(\d{4}-\d{2}-\d{2})|"   # timestamp
            r"(/Users/|/home/|C:\\)|"  # absolute path
            r"\b\d{2}:\d{2}:\d{2}\b"   # clock time
        )
        tools = asyncio.run(list_tools())
        for t in tools:
            assert not suspicious.search(t.description or ""), (
                f"{t.name} description contains dynamic content — breaks prompt cache."
            )
