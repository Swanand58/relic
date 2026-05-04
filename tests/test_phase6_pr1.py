"""Tests for Phase 6 PR 1 — signatures, test mapping, Python inheritance."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from relic.indexer import (
    _add_test_mapping,
    _analyse_python,
    _analyse_typescript,
    _is_test_file,
    _source_candidate_names,
    _test_candidate_names,
)
from relic.toon import full_index_to_toon, subgraph_to_toon

# ---------------------------------------------------------------------------
# Python signature extraction
# ---------------------------------------------------------------------------


class TestPythonSignatures:
    def _syms(self, source: str) -> list[dict]:
        _, symbols, _ = _analyse_python(source, "mod.py", Path("/fake"))
        return symbols

    def test_simple_function(self):
        syms = self._syms("def greet(name: str) -> str:\n    return name\n")
        assert len(syms) == 1
        assert "greet(name: str)" in syms[0]["signature"]
        assert "-> str" in syms[0]["signature"]

    def test_no_annotations(self):
        syms = self._syms("def hello(x, y):\n    pass\n")
        assert "hello(x, y)" in syms[0]["signature"]

    def test_class_no_bases(self):
        syms = self._syms("class Foo:\n    pass\n")
        assert syms[0]["signature"] == "Foo"

    def test_class_with_bases(self):
        syms = self._syms("class Child(Base, Mixin):\n    pass\n")
        sig = syms[0]["signature"]
        assert "Child(Base, Mixin)" == sig

    def test_async_function(self):
        syms = self._syms("async def fetch(url: str) -> bytes:\n    pass\n")
        assert "fetch(url: str)" in syms[0]["signature"]
        assert "-> bytes" in syms[0]["signature"]

    def test_extends_edge_info_present(self):
        syms = self._syms("class Dog(Animal):\n    pass\n")
        assert syms[0].get("extends") == "Animal"

    def test_no_extends_for_plain_class(self):
        syms = self._syms("class Solo:\n    pass\n")
        assert "extends" not in syms[0]


# ---------------------------------------------------------------------------
# TypeScript signature extraction
# ---------------------------------------------------------------------------


class TestTypeScriptSignatures:
    def _syms(self, source: str) -> list[dict]:
        _, symbols, _ = _analyse_typescript(source, "mod.ts", Path("/fake"))
        return symbols

    def test_function_declaration(self):
        syms = self._syms("function greet(name: string): string {\n}\n")
        assert len(syms) == 1
        assert "greet(name: string)" in syms[0]["signature"]

    def test_exported_function(self):
        syms = self._syms("export function process(items: Item[]): void {\n}\n")
        assert "process(items: Item[])" in syms[0]["signature"]

    def test_arrow_function(self):
        syms = self._syms("const add = (a: number, b: number): number => a + b;\n")
        assert len(syms) == 1
        assert "add" in syms[0]["signature"]

    def test_class_extends(self):
        syms = self._syms("class Dog extends Animal {\n}\n")
        assert syms[0]["signature"] == "Dog(Animal)"
        assert syms[0].get("extends") == "Animal"

    def test_interface(self):
        syms = self._syms("interface Config {\n  timeout: number;\n}\n")
        assert syms[0]["signature"] == "Config"

    def test_type_alias(self):
        syms = self._syms("type ID = string;\n")
        assert syms[0]["signature"] == "ID"


# ---------------------------------------------------------------------------
# Test file detection + candidate generation
# ---------------------------------------------------------------------------


class TestTestFileDetection:
    def test_test_prefix(self):
        assert _is_test_file("tests/test_foo.py")

    def test_test_suffix(self):
        assert _is_test_file("src/foo.test.ts")

    def test_spec_suffix(self):
        assert _is_test_file("components/Button.spec.tsx")

    def test_dunder_tests_dir(self):
        assert _is_test_file("src/__tests__/foo.ts")

    def test_normal_file_not_test(self):
        assert not _is_test_file("src/processor.py")

    def test_candidate_names_python(self):
        names = _test_candidate_names("src/processor.py")
        assert "src/test_processor.py" in names

    def test_candidate_names_ts(self):
        names = _test_candidate_names("src/utils.ts")
        assert "src/utils.test.ts" in names

    def test_source_from_test_prefix(self):
        names = _source_candidate_names("tests/test_foo.py")
        assert "tests/foo.py" in names

    def test_source_from_test_suffix(self):
        names = _source_candidate_names("src/foo.test.ts")
        assert "src/foo.ts" in names


# ---------------------------------------------------------------------------
# Test mapping in graph
# ---------------------------------------------------------------------------


class TestTestMapping:
    def test_tested_by_edges_added(self):
        G = nx.DiGraph()
        G.add_node("src/foo.py", ntype="file", path="src/foo.py", language="python", subproject="")
        G.add_node("src/test_foo.py", ntype="file", path="src/test_foo.py", language="python", subproject="")
        _add_test_mapping(G)
        edges = [(u, v, d["etype"]) for u, v, d in G.edges(data=True)]
        assert ("src/foo.py", "src/test_foo.py", "tested_by") in edges
        assert ("src/test_foo.py", "src/foo.py", "tests") in edges

    def test_no_false_positive(self):
        G = nx.DiGraph()
        G.add_node("src/foo.py", ntype="file", path="src/foo.py", language="python", subproject="")
        G.add_node("src/bar.py", ntype="file", path="src/bar.py", language="python", subproject="")
        _add_test_mapping(G)
        assert len(list(G.edges())) == 0

    def test_tests_dir_convention(self):
        G = nx.DiGraph()
        G.add_node("src/foo.py", ntype="file", path="src/foo.py", language="python", subproject="")
        G.add_node("tests/test_foo.py", ntype="file", path="tests/test_foo.py", language="python", subproject="")
        _add_test_mapping(G)
        tested_by = [(u, v) for u, v, d in G.edges(data=True) if d["etype"] == "tested_by"]
        assert ("src/foo.py", "tests/test_foo.py") in tested_by

    def test_ts_test_suffix(self):
        G = nx.DiGraph()
        G.add_node("src/utils.ts", ntype="file", path="src/utils.ts", language="typescript", subproject="")
        G.add_node("src/utils.test.ts", ntype="file", path="src/utils.test.ts", language="typescript", subproject="")
        _add_test_mapping(G)
        tested_by = [(u, v) for u, v, d in G.edges(data=True) if d["etype"] == "tested_by"]
        assert ("src/utils.ts", "src/utils.test.ts") in tested_by


# ---------------------------------------------------------------------------
# Python class inheritance (extends edges)
# ---------------------------------------------------------------------------


class TestPythonInheritance:
    def test_extends_edge_in_graph(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "base.py").write_text("class Animal:\n    pass\n")
        (src / "dog.py").write_text("from src.base import Animal\n\nclass Dog(Animal):\n    pass\n")
        (tmp_path / "relic.yaml").write_text("subprojects:\n  app:\n    path: ./src\n    description: app\n")

        from relic.indexer import run_index

        run_index(tmp_path, tmp_path / ".knowledge", tmp_path / "relic.yaml")
        import pickle

        G = pickle.loads((tmp_path / ".knowledge" / "index.pkl").read_bytes())

        extends = [(u, v) for u, v, d in G.edges(data=True) if d.get("etype") == "extends"]
        child_names = [u.split("@")[0] for u, _ in extends]
        assert "Dog" in child_names


# ---------------------------------------------------------------------------
# Signatures in TOON output
# ---------------------------------------------------------------------------


class TestSignaturesInToon:
    def test_exports_table_has_signature_column(self):
        out = subgraph_to_toon(
            focus_path="src/foo.py",
            file_nodes=[{"path": "src/foo.py", "language": "python", "subproject": ""}],
            symbol_nodes=[
                {
                    "name": "greet",
                    "stype": "function",
                    "path": "src/foo.py",
                    "line": 1,
                    "signature": "greet(name: str) -> str",
                }
            ],
            import_edges=[],
            define_edges=[],
            extends_edges=[],
        )
        assert "signature" in out
        assert "greet(name: str) -> str" in out

    def test_full_index_toon_has_signature_column(self):
        G = nx.DiGraph()
        G.add_node("f.py", ntype="file", path="f.py", language="python", subproject="")
        G.add_node(
            "hello@f.py",
            ntype="symbol",
            name="hello",
            stype="function",
            path="f.py",
            line=1,
            signature="hello(x: int) -> int",
        )
        G.add_edge("f.py", "hello@f.py", etype="defines")
        out = full_index_to_toon(G)
        assert "signature" in out
        assert "hello(x: int) -> int" in out

    def test_tested_by_in_full_index_toon(self):
        G = nx.DiGraph()
        G.add_node("src/a.py", ntype="file", path="src/a.py", language="python", subproject="")
        G.add_node("tests/test_a.py", ntype="file", path="tests/test_a.py", language="python", subproject="")
        G.add_edge("src/a.py", "tests/test_a.py", etype="tested_by")
        out = full_index_to_toon(G)
        assert "tested_by" in out
        assert "src/a.py" in out
        assert "tests/test_a.py" in out


# ---------------------------------------------------------------------------
# Uses edges (symbol-level import tracking)
# ---------------------------------------------------------------------------


class TestUsesEdges:
    def test_python_from_import_creates_uses_edge(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "models.py").write_text("class Order:\n    pass\n")
        (src / "processor.py").write_text("from src.models import Order\n\ndef run(o: Order):\n    pass\n")
        (tmp_path / "relic.yaml").write_text("subprojects:\n  app:\n    path: ./src\n    description: app\n")

        from relic.indexer import run_index

        run_index(tmp_path, tmp_path / ".knowledge", tmp_path / "relic.yaml")
        import pickle

        G = pickle.loads((tmp_path / ".knowledge" / "index.pkl").read_bytes())
        uses = [(u, v) for u, v, d in G.edges(data=True) if d.get("etype") == "uses"]
        assert any("processor.py" in u and "Order@" in v for u, v in uses)

    def test_uses_edge_not_created_for_star_import(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "models.py").write_text("class Order:\n    pass\n")
        (src / "processor.py").write_text("from src.models import *\n")
        (tmp_path / "relic.yaml").write_text("subprojects:\n  app:\n    path: ./src\n    description: app\n")

        from relic.indexer import run_index

        run_index(tmp_path, tmp_path / ".knowledge", tmp_path / "relic.yaml")
        import pickle

        G = pickle.loads((tmp_path / ".knowledge" / "index.pkl").read_bytes())
        uses = [(u, v) for u, v, d in G.edges(data=True) if d.get("etype") == "uses"]
        assert len(uses) == 0


# ---------------------------------------------------------------------------
# Callers in TOON output (blast radius)
# ---------------------------------------------------------------------------


class TestCallersInToon:
    def test_callers_section_rendered(self):
        out = subgraph_to_toon(
            focus_path="src/models.py",
            file_nodes=[
                {"path": "src/models.py", "language": "python", "subproject": ""},
                {"path": "src/processor.py", "language": "python", "subproject": ""},
            ],
            symbol_nodes=[
                {"name": "Order", "stype": "class", "path": "src/models.py", "line": 1, "signature": "Order"},
            ],
            import_edges=[],
            define_edges=[],
            extends_edges=[],
            uses_edges=[("src/processor.py", "Order@src/models.py")],
        )
        assert "callers" in out
        assert "src/processor.py" in out
        assert "Order" in out

    def test_no_callers_when_no_uses(self):
        out = subgraph_to_toon(
            focus_path="src/models.py",
            file_nodes=[{"path": "src/models.py", "language": "python", "subproject": ""}],
            symbol_nodes=[
                {"name": "Order", "stype": "class", "path": "src/models.py", "line": 1, "signature": "Order"},
            ],
            import_edges=[],
            define_edges=[],
            extends_edges=[],
            uses_edges=[],
        )
        assert "callers" not in out


class TestImportedNamesPython:
    def test_from_import_returns_names(self, tmp_path: Path):
        (tmp_path / "models.py").write_text("class Order:\n    pass\nclass Receipt:\n    pass\n")
        (tmp_path / "proc.py").write_text("from models import Order, Receipt\n")
        source = (tmp_path / "proc.py").read_text()
        _, _, imported = _analyse_python(source, "proc.py", tmp_path)
        names = [n for _, n in imported]
        assert "Order" in names
        assert "Receipt" in names

    def test_star_import_excluded(self, tmp_path: Path):
        (tmp_path / "models.py").write_text("class Order:\n    pass\n")
        (tmp_path / "proc.py").write_text("from models import *\n")
        source = (tmp_path / "proc.py").read_text()
        _, _, imported = _analyse_python(source, "proc.py", tmp_path)
        assert len(imported) == 0
