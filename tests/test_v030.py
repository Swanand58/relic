"""Tests for v0.3.0 — zero-config indexing, .relicignore, skip stats."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from relic.indexer import (
    _collect_source_files,
    _load_relicignore,
    _matches_ignore,
    build_graph,
    run_index,
)


def _posix_paths(tmp_path: Path, files: list[tuple[Path, str]]) -> dict[str, str]:
    """Convert collected files to {posix_relative_path: label} for cross-platform asserts."""
    return {PurePosixPath(p.relative_to(tmp_path)).as_posix(): label for p, label in files}


class TestZeroConfigIndexing:
    def test_index_without_yaml(self, tmp_path: Path):
        """run_index should succeed even when no relic.yaml exists."""
        (tmp_path / "main.py").write_text("def hello():\n    pass\n")
        G, skip_stats = run_index(tmp_path, tmp_path / ".knowledge", config_file=None)
        assert sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file") >= 1
        assert isinstance(skip_stats, dict)

    def test_index_with_yaml(self, tmp_path: Path):
        """run_index should still work with relic.yaml for subproject labels."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def main():\n    pass\n")
        (tmp_path / "relic.yaml").write_text("subprojects:\n  app:\n    path: ./src\n    description: app\n")
        G, _ = run_index(tmp_path, tmp_path / ".knowledge", tmp_path / "relic.yaml")
        file_nodes = {n: d for n, d in G.nodes(data=True) if d.get("ntype") == "file"}
        assert "src/app.py" in file_nodes
        assert file_nodes["src/app.py"]["subproject"] == "app"

    def test_collect_walks_entire_tree(self, tmp_path: Path):
        """_collect_source_files walks the entire project root, not just declared paths."""
        (tmp_path / "components").mkdir()
        (tmp_path / "utils").mkdir()
        (tmp_path / "components" / "Button.tsx").write_text("export const Button = () => {}\n")
        (tmp_path / "utils" / "helpers.ts").write_text("export function format() {}\n")
        (tmp_path / "app.py").write_text("print('hi')\n")

        files, _ = _collect_source_files(tmp_path)
        paths = _posix_paths(tmp_path, files)
        assert "components/Button.tsx" in paths
        assert "utils/helpers.ts" in paths
        assert "app.py" in paths

    def test_collect_skips_node_modules(self, tmp_path: Path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}\n")
        (tmp_path / "app.js").write_text("const x = 1;\n")

        files, skip_stats = _collect_source_files(tmp_path)
        paths = _posix_paths(tmp_path, files)
        assert "app.js" in paths
        assert "node_modules/pkg/index.js" not in paths
        assert "node_modules" in skip_stats["skipped_dirs"]

    def test_collect_labels_subproject_files(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("x = 1\n")
        (tmp_path / "other.py").write_text("y = 2\n")

        subprojects = {"app": {"path": "./src"}}
        files, _ = _collect_source_files(tmp_path, subprojects)
        labels = _posix_paths(tmp_path, files)
        assert labels["src/app.py"] == "app"
        assert labels["other.py"] == ""

    def test_nextjs_project_indexes_all_dirs(self, tmp_path: Path):
        """The original bug: Next.js project missing components/, utils/, etc."""
        for d in ["app", "components", "atoms", "utils", "hooks", "lib"]:
            (tmp_path / d).mkdir()
            (tmp_path / d / "index.ts").write_text(f"export const {d} = true;\n")

        G, _ = build_graph(tmp_path)
        file_nodes = {n for n, d in G.nodes(data=True) if d.get("ntype") == "file"}
        for d in ["app", "components", "atoms", "utils", "hooks", "lib"]:
            assert f"{d}/index.ts" in file_nodes, f"missed {d}/index.ts"

    def test_toon_output_no_subproject_column(self, tmp_path: Path):
        """TOON output should not include a subproject column."""
        from relic.toon import subgraph_to_toon

        out = subgraph_to_toon(
            focus_path="src/foo.py",
            file_nodes=[
                {"path": "src/foo.py", "language": "python"},
                {"path": "src/bar.py", "language": "python"},
            ],
            symbol_nodes=[],
            import_edges=[("src/foo.py", "src/bar.py")],
            define_edges=[],
            extends_edges=[],
        )
        assert "subproject" not in out
        assert "{path,language}" in out


# ---------------------------------------------------------------------------
# .relicignore
# ---------------------------------------------------------------------------


class TestRelicignore:
    def test_load_patterns(self, tmp_path: Path):
        (tmp_path / ".relicignore").write_text("generated/\n*.pb.py\n# comment\n\nvendor/**\n")
        patterns = _load_relicignore(tmp_path)
        assert patterns == ["generated/", "*.pb.py", "vendor/**"]

    def test_load_missing_file(self, tmp_path: Path):
        assert _load_relicignore(tmp_path) == []

    def test_matches_glob(self):
        assert _matches_ignore("src/foo.pb.py", ["*.pb.py"])
        assert not _matches_ignore("src/foo.py", ["*.pb.py"])

    def test_matches_directory_pattern(self):
        assert _matches_ignore("generated/models.py", ["generated/"])
        assert not _matches_ignore("src/models.py", ["generated/"])

    def test_relicignore_excludes_files(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "generated").mkdir()
        (tmp_path / "generated" / "schema.py").write_text("y = 2\n")
        (tmp_path / ".relicignore").write_text("generated/\n")

        files, skip_stats = _collect_source_files(tmp_path)
        paths = _posix_paths(tmp_path, files)
        assert "app.py" in paths
        assert "generated/schema.py" not in paths
        assert skip_stats["ignored_count"] == 1

    def test_relicignore_glob_pattern(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "foo.pb.py").write_text("y = 2\n")
        (tmp_path / ".relicignore").write_text("*.pb.py\n")

        files, skip_stats = _collect_source_files(tmp_path)
        paths = _posix_paths(tmp_path, files)
        assert "app.py" in paths
        assert "foo.pb.py" not in paths
        assert skip_stats["ignored_count"] == 1


# ---------------------------------------------------------------------------
# Skip stats
# ---------------------------------------------------------------------------


class TestSkipStats:
    def test_skipped_dirs_tracked(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("x = 1\n")
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}\n")
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "site.py").write_text("pass\n")

        _, skip_stats = _collect_source_files(tmp_path)
        assert "node_modules" in skip_stats["skipped_dirs"]
        assert ".venv" in skip_stats["skipped_dirs"]

    def test_no_skipped_dirs_when_clean(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("x = 1\n")
        _, skip_stats = _collect_source_files(tmp_path)
        assert len(skip_stats["skipped_dirs"]) == 0
        assert skip_stats["ignored_count"] == 0
