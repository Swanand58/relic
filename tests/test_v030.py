"""Tests for v0.3.0 — zero-config indexing."""

from __future__ import annotations

from pathlib import Path

from relic.indexer import (
    _collect_source_files,
    build_graph,
    run_index,
)


class TestZeroConfigIndexing:
    def test_index_without_yaml(self, tmp_path: Path):
        """run_index should succeed even when no relic.yaml exists."""
        (tmp_path / "main.py").write_text("def hello():\n    pass\n")
        G = run_index(tmp_path, tmp_path / ".knowledge", config_file=None)
        assert sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file") >= 1

    def test_index_with_yaml(self, tmp_path: Path):
        """run_index should still work with relic.yaml for subproject labels."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def main():\n    pass\n")
        (tmp_path / "relic.yaml").write_text("subprojects:\n  app:\n    path: ./src\n    description: app\n")
        G = run_index(tmp_path, tmp_path / ".knowledge", tmp_path / "relic.yaml")
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

        files = _collect_source_files(tmp_path)
        paths = {str(p.relative_to(tmp_path)) for p, _ in files}
        assert "components/Button.tsx" in paths
        assert "utils/helpers.ts" in paths
        assert "app.py" in paths

    def test_collect_skips_node_modules(self, tmp_path: Path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}\n")
        (tmp_path / "app.js").write_text("const x = 1;\n")

        files = _collect_source_files(tmp_path)
        paths = {str(p.relative_to(tmp_path)) for p, _ in files}
        assert "app.js" in paths
        assert "node_modules/pkg/index.js" not in paths

    def test_collect_labels_subproject_files(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("x = 1\n")
        (tmp_path / "other.py").write_text("y = 2\n")

        subprojects = {"app": {"path": "./src"}}
        files = _collect_source_files(tmp_path, subprojects)
        labels = {str(p.relative_to(tmp_path)): label for p, label in files}
        assert labels["src/app.py"] == "app"
        assert labels["other.py"] == ""

    def test_nextjs_project_indexes_all_dirs(self, tmp_path: Path):
        """The original bug: Next.js project missing components/, utils/, etc."""
        for d in ["app", "components", "atoms", "utils", "hooks", "lib"]:
            (tmp_path / d).mkdir()
            (tmp_path / d / "index.ts").write_text(f"export const {d} = true;\n")

        G = build_graph(tmp_path)
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
