"""Tests for relic.audit — measures relic's own context-window footprint."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from relic.audit import (
    SAMPLE_DEPTH,
    TAX_HEALTHY,
    TAX_WARN,
    compute_audit,
    render_audit,
)


class TestComputeAudit:
    def test_returns_expected_keys(self, tmp_path: Path):
        audit = compute_audit(tmp_path, tmp_path / ".knowledge")
        for key in (
            "instruction_tokens",
            "mcp_tokens",
            "mcp_breakdown",
            "baseline_tax",
            "verdict",
            "thresholds",
            "sample_query",
        ):
            assert key in audit

    def test_baseline_tax_equals_sum(self, tmp_path: Path):
        audit = compute_audit(tmp_path, tmp_path / ".knowledge")
        assert audit["baseline_tax"] == audit["instruction_tokens"] + audit["mcp_tokens"]

    def test_thresholds_match_module_constants(self, tmp_path: Path):
        audit = compute_audit(tmp_path, tmp_path / ".knowledge")
        assert audit["thresholds"]["healthy"] == TAX_HEALTHY
        assert audit["thresholds"]["warn"] == TAX_WARN

    def test_verdict_healthy_below_threshold(self, tmp_path: Path):
        # Relic must keep its own house in order: with the current instruction
        # and MCP definitions, baseline_tax must always be in the healthy band.
        # Locking this guards against future bloat creeping back in.
        audit = compute_audit(tmp_path, tmp_path / ".knowledge")
        assert audit["verdict"] == "healthy"

    def test_mcp_breakdown_has_all_four_tools(self, tmp_path: Path):
        audit = compute_audit(tmp_path, tmp_path / ".knowledge")
        names = {t["name"] for t in audit["mcp_breakdown"]}
        assert names == {"relic_query", "relic_search", "relic_reindex", "relic_stats"}

    def test_mcp_breakdown_each_entry_shape(self, tmp_path: Path):
        audit = compute_audit(tmp_path, tmp_path / ".knowledge")
        for entry in audit["mcp_breakdown"]:
            assert {"name", "description_tokens", "schema_tokens", "total"} <= entry.keys()
            assert entry["total"] == entry["description_tokens"] + entry["schema_tokens"]

    def test_no_graph_means_no_sample_query(self, tmp_path: Path):
        # Fresh project with no .knowledge/ — sample_query must be None,
        # not raise.
        audit = compute_audit(tmp_path, tmp_path / ".knowledge")
        assert audit["sample_query"] is None

    def test_with_graph_sample_query_is_populated(self, tmp_project: Path):
        audit = compute_audit(tmp_project, tmp_project / ".knowledge")
        sample = audit["sample_query"]
        assert sample is not None
        for key in (
            "sample_path",
            "depth",
            "toon_tokens",
            "focus_tokens",
            "with_relic_tokens",
            "manual_baseline",
            "savings",
            "savings_pct",
            "files_replaced",
        ):
            assert key in sample
        assert sample["depth"] == SAMPLE_DEPTH
        assert sample["with_relic_tokens"] > 0
        # Note: on a trivial 3-file fixture, TOON metadata can exceed the
        # raw file content, so savings may be negative. That's a real,
        # honest output — the audit must not pretend otherwise. Just
        # assert the math is internally consistent.
        assert sample["manual_baseline"] >= 0
        assert sample["savings"] == sample["manual_baseline"] - sample["with_relic_tokens"]


class TestRenderAudit:
    def _capture(self, project_root: Path, knowledge_dir: Path) -> str:
        audit = compute_audit(project_root, knowledge_dir)
        buf = StringIO()
        render_audit(audit, Console(file=buf, force_terminal=False, width=120))
        return buf.getvalue()

    def test_renders_all_baseline_metrics(self, tmp_path: Path):
        out = self._capture(tmp_path, tmp_path / ".knowledge")
        assert "instructions block" in out
        assert "mcp tool schemas" in out
        assert "baseline tax" in out

    def test_renders_no_graph_hint(self, tmp_path: Path):
        out = self._capture(tmp_path, tmp_path / ".knowledge")
        assert "relic index" in out

    def test_renders_sample_query_when_indexed(self, tmp_project: Path):
        out = self._capture(tmp_project, tmp_project / ".knowledge")
        assert "sample query" in out
        assert "relic_query response" in out
        assert "manual baseline" in out
        assert "net savings" in out

    def test_renders_healthy_verdict(self, tmp_path: Path):
        out = self._capture(tmp_path, tmp_path / ".knowledge")
        assert "healthy" in out.lower()
