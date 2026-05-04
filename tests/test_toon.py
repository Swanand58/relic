"""Unit tests for relic.toon — TOON serializer primitives and helpers."""

from __future__ import annotations

from relic.toon import (
    ToonWriter,
    _safe,
    candidates_to_toon,
    full_index_to_toon,
    subgraph_to_toon,
)

# ---------------------------------------------------------------------------
# _safe — primitive cell rendering
# ---------------------------------------------------------------------------


class TestSafe:
    def test_plain_value_unchanged(self):
        assert _safe("hello") == "hello"

    def test_none_becomes_empty(self):
        assert _safe(None) == ""

    def test_int_stringified(self):
        assert _safe(42) == "42"

    def test_newline_replaced_with_space(self):
        assert _safe("line\nbreak") == "line break"

    def test_carriage_return_stripped(self):
        assert _safe("a\rb") == "ab"

    def test_comma_triggers_quoting(self):
        assert _safe("a,b") == '"a,b"'

    def test_no_quoting_when_no_comma(self):
        assert _safe("path/to/file.py") == "path/to/file.py"


# ---------------------------------------------------------------------------
# ToonWriter — fluent builder
# ---------------------------------------------------------------------------


class TestToonWriter:
    def test_kv_emits_key_value(self):
        out = ToonWriter().kv("focus", "src/foo.py").build()
        assert out == "focus: src/foo.py"

    def test_table_with_rows(self):
        out = (
            ToonWriter()
            .table(
                "files",
                ["path", "lang"],
                [["a.py", "python"], ["b.ts", "typescript"]],
            )
            .build()
        )
        assert "files[2]{path,lang}:" in out
        assert "  a.py,python" in out
        assert "  b.ts,typescript" in out

    def test_empty_table_omitted(self):
        out = ToonWriter().table("files", ["path"], []).build()
        assert "files" not in out

    def test_blank_inserts_separator(self):
        out = ToonWriter().kv("a", 1).blank().kv("b", 2).build()
        assert "\n\n" in out

    def test_chaining_returns_writer(self):
        w = ToonWriter()
        assert w.kv("a", 1) is w
        assert w.table("t", ["x"], [["1"]]) is w
        assert w.blank() is w


# ---------------------------------------------------------------------------
# candidates_to_toon — disambiguation list
# ---------------------------------------------------------------------------


class TestCandidatesToToon:
    def _candidates(self):
        return [
            {"name": "process", "stype": "function", "path": "payments/processor.py", "line": 45},
            {"name": "process", "stype": "function", "path": "orders/handler.py", "line": 12},
        ]

    def test_header_includes_count(self):
        out = candidates_to_toon("process", self._candidates())
        assert "ambiguous: 'process' matches 2 symbols" in out

    def test_table_uses_expected_columns(self):
        out = candidates_to_toon("process", self._candidates())
        assert "candidates[2]{name,type,file,line}:" in out

    def test_each_candidate_rendered(self):
        out = candidates_to_toon("process", self._candidates())
        assert "process,function,payments/processor.py,45" in out
        assert "process,function,orders/handler.py,12" in out

    def test_handles_missing_line(self):
        cands = [{"name": "x", "stype": "function", "path": "a.py"}]  # no line
        out = candidates_to_toon("x", cands)
        # default line 0 surfaces
        assert ",0" in out


# ---------------------------------------------------------------------------
# subgraph_to_toon — full pre-edit context block
# ---------------------------------------------------------------------------


class TestSubgraphToToon:
    def test_basic_focus_only(self):
        out = subgraph_to_toon(
            focus_path="src/foo.py",
            file_nodes=[{"path": "src/foo.py", "language": "python", "subproject": "app"}],
            symbol_nodes=[],
            import_edges=[],
            define_edges=[],
            extends_edges=[],
        )
        assert "focus: src/foo.py" in out
        # no neighbors / exports / imports
        assert "neighbors[" not in out
        assert "exports[" not in out

    def test_neighbor_files_listed_separately(self):
        out = subgraph_to_toon(
            focus_path="src/foo.py",
            file_nodes=[
                {"path": "src/foo.py", "language": "python", "subproject": "app"},
                {"path": "src/bar.py", "language": "python", "subproject": "app"},
            ],
            symbol_nodes=[],
            import_edges=[],
            define_edges=[],
            extends_edges=[],
        )
        assert "neighbors[1]{path,language,subproject}:" in out
        assert "src/bar.py,python,app" in out
        # focus file not duplicated in neighbors
        assert "src/foo.py,python,app" not in out

    def test_exports_only_for_focus_file(self):
        out = subgraph_to_toon(
            focus_path="src/foo.py",
            file_nodes=[
                {"path": "src/foo.py", "language": "python", "subproject": "app"},
                {"path": "src/bar.py", "language": "python", "subproject": "app"},
            ],
            symbol_nodes=[
                {"name": "Foo", "stype": "class", "path": "src/foo.py", "line": 1, "signature": "Foo"},
                {"name": "Bar", "stype": "class", "path": "src/bar.py", "line": 1, "signature": "Bar"},
            ],
            import_edges=[],
            define_edges=[],
            extends_edges=[],
        )
        assert "exports[1]{name,type,line,signature}:" in out
        assert "Foo,class,1,Foo" in out
        assert "neighbor_symbols" in out

    def test_tested_by_rendered_for_focus(self):
        out = subgraph_to_toon(
            focus_path="src/foo.py",
            file_nodes=[
                {"path": "src/foo.py", "language": "python", "subproject": "app"},
                {"path": "tests/test_foo.py", "language": "python", "subproject": "app"},
            ],
            symbol_nodes=[],
            import_edges=[],
            define_edges=[],
            extends_edges=[],
            tested_by_edges=[("src/foo.py", "tests/test_foo.py")],
        )
        assert "tested_by[1]{source,test}:" in out
        assert "src/foo.py,tests/test_foo.py" in out

    def test_imports_filtered_to_focus(self):
        out = subgraph_to_toon(
            focus_path="src/foo.py",
            file_nodes=[
                {"path": "src/foo.py", "language": "python", "subproject": "app"},
                {"path": "src/bar.py", "language": "python", "subproject": "app"},
                {"path": "src/baz.py", "language": "python", "subproject": "app"},
            ],
            symbol_nodes=[],
            import_edges=[
                ("src/foo.py", "src/bar.py"),  # focus → bar
                ("src/bar.py", "src/baz.py"),  # bar → baz, NOT involving focus
            ],
            define_edges=[],
            extends_edges=[],
        )
        # only the focus-touching edge surfaces
        assert "imports[1]{from,to}:" in out
        # the bar→baz edge must not appear
        assert "src/bar.py,src/baz.py" not in out

    def test_imported_by_rendered(self):
        out = subgraph_to_toon(
            focus_path="src/foo.py",
            file_nodes=[
                {"path": "src/foo.py", "language": "python", "subproject": "app"},
                {"path": "src/caller.py", "language": "python", "subproject": "app"},
            ],
            symbol_nodes=[],
            import_edges=[("src/caller.py", "src/foo.py")],
            define_edges=[],
            extends_edges=[],
        )
        assert "imported_by[1]{from,to}:" in out
        assert "src/caller.py,src/foo.py" in out


# ---------------------------------------------------------------------------
# full_index_to_toon — sanity check, used by `relic index`
# ---------------------------------------------------------------------------


class TestFullIndexToon:
    def test_renders_files_section(self, sample_graph):
        out = full_index_to_toon(sample_graph)
        assert out.startswith("# Relic knowledge graph")
        assert "files[" in out
        assert "symbols[" in out
