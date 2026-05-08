"""Tests for compute_stats — backs the `relic stats` CLI command.

The matching `relic_stats` MCP tool was removed in Phase 7.5a; agents read
freshness from the per-response `index{...}` header instead.
"""

from __future__ import annotations

from pathlib import Path

from relic.indexer import compute_stats


class TestComputeStats:
    def test_counts_files_and_symbols(self, sample_graph, tmp_path: Path):
        stats = compute_stats(sample_graph, tmp_path)
        assert stats["files"] == 4
        assert stats["symbols"] == 4

    def test_counts_edges(self, sample_graph, tmp_path: Path):
        stats = compute_stats(sample_graph, tmp_path)
        # 4 defines + 3 imports = 7
        assert stats["edges"] == 7

    def test_edges_by_type(self, sample_graph, tmp_path: Path):
        stats = compute_stats(sample_graph, tmp_path)
        assert stats["edges_by_type"]["defines"] == 4
        assert stats["edges_by_type"]["imports"] == 3

    def test_subprojects_sorted_unique(self, sample_graph, tmp_path: Path):
        stats = compute_stats(sample_graph, tmp_path)
        assert stats["subprojects"] == ["api", "orders", "payments"]

    def test_last_updated_unknown_when_no_index(self, sample_graph, tmp_path: Path):
        stats = compute_stats(sample_graph, tmp_path)
        assert stats["last_updated"] == "unknown"

    def test_last_updated_set_when_index_exists(self, sample_graph, tmp_path: Path):
        knowledge_dir = tmp_path / ".knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "index.pkl").write_bytes(b"placeholder")
        stats = compute_stats(sample_graph, knowledge_dir)
        assert stats["last_updated"] != "unknown"
        # crude format check — YYYY-MM-DD HH:MM:SS
        assert len(stats["last_updated"]) == 19
