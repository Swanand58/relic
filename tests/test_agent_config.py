"""Tests for relic.agent_config — example-file picker and instruction regressions.

`_pick_example_file` is the only piece of behavioral logic in the module.
The rest of the module writes files; we cover that with one end-to-end
init test that confirms the placeholder is substituted.

The imperative-wording regression tests pin the contract we shipped in
Phase 3 — if someone removes the MUST/SHOULD rules or the decision tree
by accident, these fail loudly.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import networkx as nx
import pytest

from relic.agent_config import (
    AGENTS,
    RELIC_BLOCK_END,
    RELIC_BLOCK_START,
    RELIC_EXAMPLE_PLACEHOLDER,
    RELIC_INSTRUCTIONS,
    _pick_example_file,
    init_agent,
)

# ---------------------------------------------------------------------------
# RELIC_INSTRUCTIONS — wording regression
# ---------------------------------------------------------------------------


class TestInstructionsContent:
    """Pin the imperative wording shipped in Phase 3.

    These tests are deliberately brittle — they fail if someone softens the
    MUST/SHOULD rules or removes the decision tree. That's the point.
    """

    def test_must_rules_present(self):
        assert "MUST call `relic_query <path>`" in RELIC_INSTRUCTIONS
        assert "MUST call `relic_query <symbol>`" in RELIC_INSTRUCTIONS
        assert "MUST call `relic_reindex`" in RELIC_INSTRUCTIONS

    def test_should_rules_present(self):
        assert "SHOULD call `relic_search" in RELIC_INSTRUCTIONS
        assert "SHOULD call `relic_stats`" in RELIC_INSTRUCTIONS

    def test_decision_tree_section_present(self):
        assert "### Decision tree" in RELIC_INSTRUCTIONS

    def test_example_placeholder_present(self):
        # init_agent depends on this token being substitutable.
        assert RELIC_EXAMPLE_PLACEHOLDER in RELIC_INSTRUCTIONS

    def test_no_descriptive_legacy_wording(self):
        # The Phase 2 block opened with this phrasing — fail if it returns.
        assert "Use the MCP tools below" not in RELIC_INSTRUCTIONS


# ---------------------------------------------------------------------------
# _pick_example_file — fallback chain
# ---------------------------------------------------------------------------


class TestPickExampleFile:
    def test_picks_most_connected_non_barrel(self, tmp_path: Path):
        G = nx.DiGraph()
        # `core.py` is connected to two other files, `util.py` to one.
        G.add_node("src/core.py", ntype="file", path="src/core.py", language="python", subproject="app")
        G.add_node("src/util.py", ntype="file", path="src/util.py", language="python", subproject="app")
        G.add_node("src/leaf.py", ntype="file", path="src/leaf.py", language="python", subproject="app")
        G.add_edge("src/util.py", "src/core.py", etype="imports")
        G.add_edge("src/leaf.py", "src/core.py", etype="imports")

        knowledge_dir = tmp_path / ".knowledge"
        knowledge_dir.mkdir()
        with (knowledge_dir / "index.pkl").open("wb") as f:
            pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)

        assert _pick_example_file(tmp_path) == "src/core.py"

    def test_skips_barrel_files(self, tmp_path: Path):
        G = nx.DiGraph()
        # __init__.py is the most-connected, but it's a barrel — must be skipped.
        G.add_node("src/__init__.py", ntype="file", path="src/__init__.py", language="python", subproject="app")
        G.add_node("src/real.py", ntype="file", path="src/real.py", language="python", subproject="app")
        G.add_node("src/other.py", ntype="file", path="src/other.py", language="python", subproject="app")
        G.add_edge("src/__init__.py", "src/real.py", etype="imports")
        G.add_edge("src/__init__.py", "src/other.py", etype="imports")
        G.add_edge("src/other.py", "src/real.py", etype="imports")

        knowledge_dir = tmp_path / ".knowledge"
        knowledge_dir.mkdir()
        with (knowledge_dir / "index.pkl").open("wb") as f:
            pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)

        assert _pick_example_file(tmp_path) == "src/real.py"

    def test_falls_back_to_subproject_when_no_index(self, tmp_path: Path):
        (tmp_path / "relic.yaml").write_text(
            "subprojects:\n  app:\n    path: ./mysrc\n    description: 'x'\n",
            encoding="utf-8",
        )
        result = _pick_example_file(tmp_path)
        assert result.startswith("mysrc/")
        assert "<your-file>" in result

    def test_default_when_nothing_available(self, tmp_path: Path):
        # No index, no relic.yaml — falls back to the generic placeholder.
        assert _pick_example_file(tmp_path) == "src/<your-file>"

    def test_index_with_no_files_falls_through(self, tmp_path: Path):
        G = nx.DiGraph()  # empty graph, no file nodes
        knowledge_dir = tmp_path / ".knowledge"
        knowledge_dir.mkdir()
        with (knowledge_dir / "index.pkl").open("wb") as f:
            pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
        (tmp_path / "relic.yaml").write_text(
            "subprojects:\n  app:\n    path: ./src\n    description: 'x'\n",
            encoding="utf-8",
        )
        # graph has nothing usable — should drop into the yaml fallback
        result = _pick_example_file(tmp_path)
        assert "<your-file>" in result


# ---------------------------------------------------------------------------
# init_agent — end-to-end placeholder substitution
# ---------------------------------------------------------------------------


class TestInitAgent:
    def test_writes_block_with_substituted_example(self, tmp_project: Path):
        init_agent("claude", tmp_project)

        claude_md = (tmp_project / "CLAUDE.md").read_text(encoding="utf-8")
        assert RELIC_BLOCK_START in claude_md
        assert RELIC_BLOCK_END in claude_md
        # placeholder must NOT appear verbatim — it should be substituted
        assert RELIC_EXAMPLE_PLACEHOLDER not in claude_md
        # substituted example points at a real file from the indexed project
        assert "relic_query src/" in claude_md

    def test_mcp_config_registered(self, tmp_project: Path):
        import json

        init_agent("claude", tmp_project)
        cfg = json.loads((tmp_project / ".claude/settings.json").read_text(encoding="utf-8"))
        assert cfg["mcpServers"]["relic"]["command"] == "relic"

    @pytest.mark.parametrize("agent_key", list(AGENTS.keys()))
    def test_each_agent_writes_its_instruction_file(self, tmp_project: Path, agent_key: str):
        init_agent(agent_key, tmp_project)
        target = tmp_project / AGENTS[agent_key]["path"]
        content = target.read_text(encoding="utf-8")
        assert RELIC_BLOCK_START in content
        assert "MUST call" in content  # imperative wording survived per-agent
