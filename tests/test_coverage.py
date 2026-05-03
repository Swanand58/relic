"""Tests for relic.coverage — exposes what the indexer silently drops."""

from __future__ import annotations

import os
import sys
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from relic.coverage import compute_coverage, render_coverage
from relic.indexer import MAX_FILE_BYTES


def _make_subproject(tmp_path: Path) -> tuple[Path, dict]:
    """Create a subproject directory tree with one of each classification."""
    src = tmp_path / "src"
    src.mkdir()

    (src / "good.py").write_text("def f(): pass\n", encoding="utf-8")
    (src / "also_good.ts").write_text("export const x = 1;\n", encoding="utf-8")

    (src / "README.md").write_text("# docs\n", encoding="utf-8")
    (src / "config.yaml").write_text("k: v\n", encoding="utf-8")

    big = src / "huge.py"
    big.write_bytes(b"# pad\n" * (MAX_FILE_BYTES // 6 + 100))

    nested = src / "nested"
    nested.mkdir()
    (nested / "inner.py").write_text("def g(): pass\n", encoding="utf-8")

    skipdir = src / "node_modules"
    skipdir.mkdir()
    (skipdir / "vendored.js").write_text("module.exports = {};\n", encoding="utf-8")

    subprojects = {"app": {"path": "./src", "description": "app source"}}
    return tmp_path, subprojects


class TestComputeCoverage:
    def test_indexed_files_include_python_and_ts(self, tmp_path: Path):
        root, subprojects = _make_subproject(tmp_path)
        cov = compute_coverage(root, subprojects)
        indexed = cov["subprojects"]["app"]["indexed"]
        assert any(p.endswith("good.py") for p in indexed)
        assert any(p.endswith("also_good.ts") for p in indexed)
        assert any(p.endswith(os.path.join("nested", "inner.py")) for p in indexed)

    def test_no_parser_bucket_collects_md_and_yaml(self, tmp_path: Path):
        root, subprojects = _make_subproject(tmp_path)
        cov = compute_coverage(root, subprojects)
        no_parser = cov["subprojects"]["app"]["skipped"]["no_parser"]
        assert any(p.endswith("README.md") for p in no_parser)
        assert any(p.endswith("config.yaml") for p in no_parser)

    def test_too_large_bucket_includes_size(self, tmp_path: Path):
        root, subprojects = _make_subproject(tmp_path)
        cov = compute_coverage(root, subprojects)
        too_large = cov["subprojects"]["app"]["skipped"]["too_large"]
        assert len(too_large) == 1
        path, size = too_large[0]
        assert path.endswith("huge.py")
        assert size > MAX_FILE_BYTES

    def test_skipdir_files_are_not_reported(self, tmp_path: Path):
        # Files inside SKIP_DIRS (node_modules) should be invisible — those
        # exclusions are intentional, not actionable for the user.
        root, subprojects = _make_subproject(tmp_path)
        cov = compute_coverage(root, subprojects)
        flat = (
            cov["subprojects"]["app"]["indexed"]
            + cov["subprojects"]["app"]["skipped"]["no_parser"]
            + [p for p, _ in cov["subprojects"]["app"]["skipped"]["too_large"]]
            + cov["subprojects"]["app"]["skipped"]["symlink"]
        )
        assert not any("node_modules" in p for p in flat)

    @pytest.mark.skipif(sys.platform == "win32", reason="symlink perms on CI Windows")
    def test_symlink_bucket(self, tmp_path: Path):
        root, subprojects = _make_subproject(tmp_path)
        target = root / "src" / "good.py"
        link = root / "src" / "linked.py"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation not permitted")
        cov = compute_coverage(root, subprojects)
        symlinks = cov["subprojects"]["app"]["skipped"]["symlink"]
        assert any(p.endswith("linked.py") for p in symlinks)

    def test_missing_subproject_path_marked(self, tmp_path: Path):
        cov = compute_coverage(
            tmp_path, {"ghost": {"path": "./does/not/exist", "description": ""}}
        )
        assert cov["subprojects"]["ghost"]["missing"] is True
        assert cov["subprojects"]["ghost"]["indexed"] == []

    def test_totals_sum_correctly(self, tmp_path: Path):
        root, subprojects = _make_subproject(tmp_path)
        cov = compute_coverage(root, subprojects)
        totals = cov["totals"]
        # 3 indexed (good.py, also_good.ts, nested/inner.py)
        assert totals["indexed"] == 3
        # 2 no_parser (README.md, config.yaml)
        assert totals["no_parser"] == 2
        # 1 too_large (huge.py)
        assert totals["too_large"] == 1


class TestRenderCoverage:
    def _capture(self, coverage: dict, verbose: bool = False) -> str:
        buf = StringIO()
        console = Console(file=buf, force_terminal=False, width=120)
        render_coverage(coverage, console, verbose=verbose)
        return buf.getvalue()

    def test_renders_summary_metrics(self, tmp_path: Path):
        root, subprojects = _make_subproject(tmp_path)
        cov = compute_coverage(root, subprojects)
        output = self._capture(cov)
        assert "Files indexed" in output
        assert "Skipped (no parser)" in output
        assert "Skipped" in output and "KB" in output

    def test_renders_subproject_block(self, tmp_path: Path):
        root, subprojects = _make_subproject(tmp_path)
        cov = compute_coverage(root, subprojects)
        output = self._capture(cov)
        assert "app" in output
        assert "indexed" in output

    def test_missing_subproject_renders_warning(self, tmp_path: Path):
        cov = compute_coverage(tmp_path, {"ghost": {"path": "./nope", "description": ""}})
        output = self._capture(cov)
        assert "ghost" in output
        assert "missing" in output.lower()

    def test_verbose_lists_all_skipped_files(self, tmp_path: Path):
        # Build many no_parser files to exceed the example limit
        src = tmp_path / "src"
        src.mkdir()
        for i in range(12):
            (src / f"doc_{i}.md").write_text("x", encoding="utf-8")
        cov = compute_coverage(
            tmp_path, {"app": {"path": "./src", "description": ""}}
        )
        non_verbose = self._capture(cov, verbose=False)
        verbose = self._capture(cov, verbose=True)
        assert "more (use --verbose)" in non_verbose
        assert "more (use --verbose)" not in verbose
        for i in range(12):
            assert f"doc_{i}.md" in verbose
