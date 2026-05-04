"""Tests for relic diff — detecting changes since last index."""

from __future__ import annotations

from pathlib import Path

from relic.diff import compute_diff, diff_to_toon
from relic.indexer import run_index


def _setup_project(tmp_path: Path) -> Path:
    """Create a minimal project, index it, return root."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("class Foo:\n    pass\n\ndef bar():\n    pass\n")
    (src / "utils.py").write_text("def helper():\n    return 1\n")
    (tmp_path / "relic.yaml").write_text("subprojects:\n  app:\n    path: ./src\n    description: app\n")
    run_index(tmp_path, tmp_path / ".knowledge", tmp_path / "relic.yaml")
    return tmp_path


class TestNoDiff:
    def test_up_to_date(self, tmp_path: Path):
        root = _setup_project(tmp_path)
        result = compute_diff(root, root / ".knowledge", root / "relic.yaml")
        assert not result["stale"]
        assert result["new_files"] == []
        assert result["deleted_files"] == []
        assert result["changed_files"] == []


class TestNewFiles:
    def test_new_file_detected(self, tmp_path: Path):
        root = _setup_project(tmp_path)
        (root / "src" / "new_module.py").write_text("def new_func():\n    pass\n")
        result = compute_diff(root, root / ".knowledge", root / "relic.yaml")
        assert result["stale"]
        assert "src/new_module.py" in result["new_files"]


class TestDeletedFiles:
    def test_deleted_file_detected(self, tmp_path: Path):
        root = _setup_project(tmp_path)
        (root / "src" / "utils.py").unlink()
        result = compute_diff(root, root / ".knowledge", root / "relic.yaml")
        assert result["stale"]
        assert "src/utils.py" in result["deleted_files"]


class TestChangedSymbols:
    def test_added_symbol_detected(self, tmp_path: Path):
        root = _setup_project(tmp_path)
        (root / "src" / "foo.py").write_text("class Foo:\n    pass\n\ndef bar():\n    pass\n\ndef baz():\n    pass\n")
        result = compute_diff(root, root / ".knowledge", root / "relic.yaml")
        assert result["stale"]
        assert len(result["changed_files"]) == 1
        ch = result["changed_files"][0]
        assert ch["path"] == "src/foo.py"
        assert "baz:function" in ch["added_symbols"]

    def test_removed_symbol_detected(self, tmp_path: Path):
        root = _setup_project(tmp_path)
        (root / "src" / "foo.py").write_text("class Foo:\n    pass\n")
        result = compute_diff(root, root / ".knowledge", root / "relic.yaml")
        assert result["stale"]
        ch = result["changed_files"][0]
        assert "bar:function" in ch["removed_symbols"]


class TestDiffToon:
    def test_up_to_date_toon(self):
        result = {"stale": False, "new_files": [], "deleted_files": [], "changed_files": []}
        out = diff_to_toon(result)
        assert "up-to-date" in out

    def test_stale_toon(self):
        result = {
            "stale": True,
            "new_files": ["src/new.py"],
            "deleted_files": ["src/old.py"],
            "changed_files": [{"path": "src/foo.py", "added_symbols": ["baz:function"], "removed_symbols": []}],
        }
        out = diff_to_toon(result)
        assert "stale" in out
        assert "new_files" in out
        assert "deleted_files" in out
        assert "changed_files" in out
        assert "src/new.py" in out
        assert "src/old.py" in out
