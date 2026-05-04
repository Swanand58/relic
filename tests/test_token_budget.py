"""Token-budget regression tests.

These guard against future bloat in the two surfaces that ride along in
the agent's context every turn:

1. RELIC_INSTRUCTIONS — the block we paste into CLAUDE.md / .cursorrules
   / AGENTS.md / .github/copilot-instructions.md.
2. MCP tool definitions — the description + schema for each of the four
   relic_* tools, sent in the system prompt of every turn the agent has
   the relic MCP server registered.

Background: the public 90-day Claude Code instrumentation showed that
73% of tokens go to invisible chrome (CLAUDE.md bloat, MCP schemas, hooks,
skills) before the agent reads a single user message. Relic's pitch is
"save tokens" — it would be dishonest if relic itself were part of the
problem. These thresholds keep us honest.

Tightening a threshold is fine. Loosening one needs a deliberate reason
documented in the PR description.
"""

from __future__ import annotations

import asyncio

from relic.agent_config import RELIC_INSTRUCTIONS
from relic.benchmark import _tokens
from relic.mcp_server import list_tools

# Hard caps. Numbers we picked: at the time of writing, instructions sit
# at ~651 tokens and combined MCP tooling at ~364 tokens, so each cap has
# breathing room without being so loose that a 30 % regression slips by.
INSTRUCTION_BUDGET = 800
MCP_TOTAL_BUDGET = 500


class TestInstructionBudget:
    def test_under_budget(self):
        actual = _tokens(RELIC_INSTRUCTIONS)
        assert actual <= INSTRUCTION_BUDGET, (
            f"RELIC_INSTRUCTIONS is {actual} tokens, budget is "
            f"{INSTRUCTION_BUDGET}. Trim it or raise the budget on purpose."
        )

    def test_contains_required_sections(self):
        # Trimming must not delete the load-bearing parts. Pin the
        # section headings so future edits don't accidentally drop them.
        for marker in ("### Rules", "### Decision tree", "### Example call"):
            assert marker in RELIC_INSTRUCTIONS, f"missing section: {marker}"


class TestMcpBudget:
    def _tools(self):
        return asyncio.run(list_tools())

    def test_total_under_budget(self):
        tools = self._tools()
        total = sum(_tokens(t.description or "") + _tokens(str(t.inputSchema)) for t in tools)
        assert total <= MCP_TOTAL_BUDGET, (
            f"MCP tool definitions total {total} tokens, budget is {MCP_TOTAL_BUDGET}. Trim a description or schema."
        )

    def test_no_tool_description_exceeds_quarter_of_budget(self):
        # Catch one tool eating most of the budget rather than the bloat
        # spreading evenly. Quarter of MCP_TOTAL_BUDGET is a generous cap
        # given we ship four tools.
        per_tool_cap = MCP_TOTAL_BUDGET // 2  # 250 — generous, blocks a single doubling
        for t in self._tools():
            cost = _tokens(t.description or "") + _tokens(str(t.inputSchema))
            assert cost <= per_tool_cap, f"{t.name} alone is {cost} tokens; per-tool cap is {per_tool_cap}."

    def test_all_four_tools_present(self):
        # Budget is meaningless if we silently lose a tool. Lock the surface.
        names = {t.name for t in self._tools()}
        assert names == {"relic_query", "relic_search", "relic_reindex", "relic_stats"}
