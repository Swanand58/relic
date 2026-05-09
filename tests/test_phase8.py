"""Phase 8 tests — semantic index: intent, decorators, literals, cost header, tiered rules."""

from __future__ import annotations

import ast
from pathlib import Path

import networkx as nx
import pytest

from relic.indexer import (
    _analyse_python,
    _analyse_typescript,
    _apply_file_data,
    _build_literal_index,
    _extract_python_decorators,
    _extract_python_intent,
    _extract_python_literals,
    _extract_ts_decorators,
    _extract_ts_intent,
)
from relic.search import _is_literal_query, _search_literals, render_search_toon, search_graph
from relic.toon import subgraph_to_toon


# ---------------------------------------------------------------------------
# 8a — Python intent (docstrings)
# ---------------------------------------------------------------------------


class TestPythonIntent:
    def test_function_with_docstring(self):
        tree = ast.parse('def foo():\n    """Compute X."""\n    pass')
        node = tree.body[0]
        assert _extract_python_intent(node) == "Compute X."

    def test_class_with_docstring(self):
        tree = ast.parse('class Foo:\n    """Payment processor."""\n    pass')
        node = tree.body[0]
        assert _extract_python_intent(node) == "Payment processor."

    def test_no_docstring_returns_empty(self):
        tree = ast.parse("def foo():\n    pass")
        node = tree.body[0]
        assert _extract_python_intent(node) == ""

    def test_multiline_docstring_first_line_only(self):
        tree = ast.parse('def foo():\n    """First line.\n    Second line.\n    """\n    pass')
        node = tree.body[0]
        assert _extract_python_intent(node) == "First line."

    def test_long_docstring_truncated_at_80(self):
        long = "X" * 90
        tree = ast.parse(f'def foo():\n    """{long}"""\n    pass')
        node = tree.body[0]
        result = _extract_python_intent(node)
        assert result.endswith("…")
        assert len(result) == 80

    def test_intent_in_analyse_python(self, tmp_path: Path):
        src = '"""Module."""\n\ndef compute(x: int) -> int:\n    """Compute the result."""\n    return x * 2\n'
        imports, symbols, _, _ = _analyse_python(src, "src/foo.py", tmp_path)
        fn = next(s for s in symbols if s["name"] == "compute")
        assert fn["intent"] == "Compute the result."

    def test_no_docstring_intent_in_analyse_python(self, tmp_path: Path):
        src = "def foo():\n    return 1\n"
        _, symbols, _, _ = _analyse_python(src, "src/foo.py", tmp_path)
        assert symbols[0]["intent"] == ""


# ---------------------------------------------------------------------------
# 8a — TypeScript intent (JSDoc / // comments)
# ---------------------------------------------------------------------------


class TestTypeScriptIntent:
    def test_single_line_comment_before_function(self, tmp_path: Path):
        src = "// Process payment\nfunction processPayment() {}\n"
        _, symbols, _, _ = _analyse_typescript(src, "src/pay.ts", tmp_path)
        assert symbols
        sym = next(s for s in symbols if s["name"] == "processPayment")
        assert sym["intent"] == "Process payment"

    def test_jsdoc_block_before_function(self, tmp_path: Path):
        src = "/**\n * Handle the order.\n * @param id order id\n */\nfunction handleOrder() {}\n"
        _, symbols, _, _ = _analyse_typescript(src, "src/order.ts", tmp_path)
        sym = next(s for s in symbols if s["name"] == "handleOrder")
        assert sym["intent"] == "Handle the order."

    def test_no_comment_returns_empty(self, tmp_path: Path):
        src = "function bareFunction() {}\n"
        _, symbols, _, _ = _analyse_typescript(src, "src/bare.ts", tmp_path)
        sym = next(s for s in symbols if s["name"] == "bareFunction")
        assert sym["intent"] == ""

    def test_extract_ts_intent_single_line(self):
        lines = ["// Return the sum", "function sum() {}"]
        assert _extract_ts_intent(lines, 2) == "Return the sum"

    def test_extract_ts_intent_empty_when_no_comment(self):
        lines = ["const x = 1;", "function foo() {}"]
        assert _extract_ts_intent(lines, 2) == ""


# ---------------------------------------------------------------------------
# 8a — intent surfaces in TOON exports table
# ---------------------------------------------------------------------------


class TestIntentInToon:
    def _make_graph(self, intent: str = "Compute payment.") -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node("src/pay.py", ntype="file", path="src/pay.py", language="python", subproject="")
        G.add_node(
            "compute@src/pay.py",
            ntype="symbol", name="compute", stype="function",
            path="src/pay.py", line=1, signature="compute() -> int",
            intent=intent, decorators=[], literals=[],
        )
        G.add_edge("src/pay.py", "compute@src/pay.py", etype="defines")
        return G

    def test_intent_column_in_exports(self):
        G = self._make_graph("Compute payment.")
        sg = G.subgraph(G.nodes)
        file_nodes = [d for _, d in sg.nodes(data=True) if d.get("ntype") == "file"]
        sym_nodes = [d for _, d in sg.nodes(data=True) if d.get("ntype") == "symbol"]
        out = subgraph_to_toon("src/pay.py", file_nodes, sym_nodes, [], [], [], include_intent=True)
        assert "intent" in out
        assert "Compute payment." in out

    def test_no_intent_when_include_intent_false(self):
        G = self._make_graph("Compute payment.")
        sg = G.subgraph(G.nodes)
        file_nodes = [d for _, d in sg.nodes(data=True) if d.get("ntype") == "file"]
        sym_nodes = [d for _, d in sg.nodes(data=True) if d.get("ntype") == "symbol"]
        out = subgraph_to_toon("src/pay.py", file_nodes, sym_nodes, [], [], [], include_intent=False)
        assert "Compute payment." not in out
        assert "{name,type,line,signature}" in out

    def test_intent_in_search_results(self):
        G = nx.DiGraph()
        G.add_node("src/pay.py", ntype="file", path="src/pay.py", language="python", subproject="")
        G.add_node(
            "compute@src/pay.py",
            ntype="symbol", name="compute", stype="function",
            path="src/pay.py", line=1, signature="compute()",
            intent="Compute payment.", decorators=[], literals=[],
        )
        G.add_edge("src/pay.py", "compute@src/pay.py", etype="defines")
        _, symbols, _ = search_graph(G, "compute")
        assert symbols
        assert symbols[0].get("intent") == "Compute payment."


# ---------------------------------------------------------------------------
# 8b — Python decorators
# ---------------------------------------------------------------------------


class TestPythonDecorators:
    def test_simple_decorator(self):
        tree = ast.parse("@cached\ndef foo():\n    pass")
        node = tree.body[0]
        decs = _extract_python_decorators(node)
        assert decs == [{"name": "cached", "args": []}]

    def test_decorator_with_literal_arg(self):
        tree = ast.parse('@app.route("/login")\ndef login():\n    pass')
        node = tree.body[0]
        decs = _extract_python_decorators(node)
        assert decs == [{"name": "app.route", "args": ["/login"]}]

    def test_non_literal_arg_dropped(self):
        tree = ast.parse("@retry(BACKOFF_MS)\ndef foo():\n    pass")
        node = tree.body[0]
        decs = _extract_python_decorators(node)
        assert decs == [{"name": "retry", "args": []}]

    def test_no_decorator_returns_empty(self):
        tree = ast.parse("def foo():\n    pass")
        node = tree.body[0]
        assert _extract_python_decorators(node) == []

    def test_max_5_decorators(self):
        src = "\n".join([f"@dec{i}" for i in range(7)]) + "\ndef foo():\n    pass"
        tree = ast.parse(src)
        node = tree.body[0]
        decs = _extract_python_decorators(node)
        assert len(decs) == 5

    def test_most_recently_applied_first(self):
        tree = ast.parse("@first\n@second\ndef foo():\n    pass")
        node = tree.body[0]
        decs = _extract_python_decorators(node)
        assert decs[0]["name"] == "second"
        assert decs[1]["name"] == "first"

    def test_decorator_in_analyse_python(self, tmp_path: Path):
        src = '@app.route("/login")\ndef login():\n    pass\n'
        _, symbols, _, _ = _analyse_python(src, "src/views.py", tmp_path)
        sym = next(s for s in symbols if s["name"] == "login")
        assert sym["decorators"] == [{"name": "app.route", "args": ["/login"]}]


# ---------------------------------------------------------------------------
# 8b — TypeScript decorators
# ---------------------------------------------------------------------------


class TestTypeScriptDecorators:
    def test_extract_ts_decorator_simple(self):
        lines = ["@Component", "class AppComponent {}"]
        decs = _extract_ts_decorators(lines, 2)
        assert decs == [{"name": "Component", "args": []}]

    def test_extract_ts_decorator_with_string_arg(self):
        lines = ['@Injectable("singleton")', "class MyService {}"]
        decs = _extract_ts_decorators(lines, 2)
        assert decs == [{"name": "Injectable", "args": ["singleton"]}]

    def test_no_decorator_returns_empty(self):
        lines = ["class Foo {}"]
        assert _extract_ts_decorators(lines, 1) == []

    def test_ts_decorator_in_analyse(self, tmp_path: Path):
        src = '@Component\nclass AppRoot {}\n'
        _, symbols, _, _ = _analyse_typescript(src, "src/app.ts", tmp_path)
        sym = next(s for s in symbols if s["name"] == "AppRoot")
        assert sym["decorators"] == [{"name": "Component", "args": []}]


# ---------------------------------------------------------------------------
# 8b — decorators section in TOON
# ---------------------------------------------------------------------------


class TestDecoratorsToon:
    def test_decorators_section_emitted_when_present(self):
        file_nodes = [{"path": "src/views.py", "language": "python", "subproject": ""}]
        sym_nodes = [{
            "name": "login", "stype": "function", "path": "src/views.py", "line": 1,
            "signature": "login()", "intent": "", "decorators": [{"name": "app.route", "args": ["/login"]}],
        }]
        out = subgraph_to_toon("src/views.py", file_nodes, sym_nodes, [], [], [], include_intent=True)
        assert "decorators[" in out
        assert "app.route" in out
        assert "/login" in out

    def test_no_decorators_section_when_none(self):
        file_nodes = [{"path": "src/views.py", "language": "python", "subproject": ""}]
        sym_nodes = [{
            "name": "login", "stype": "function", "path": "src/views.py", "line": 1,
            "signature": "login()", "intent": "", "decorators": [],
        }]
        out = subgraph_to_toon("src/views.py", file_nodes, sym_nodes, [], [], [], include_intent=True)
        assert "decorators[" not in out

    def test_decorator_search_via_annotation(self):
        G = nx.DiGraph()
        G.add_node("src/views.py", ntype="file", path="src/views.py", language="python", subproject="")
        G.add_node(
            "login@src/views.py",
            ntype="symbol", name="login", stype="function",
            path="src/views.py", line=1, signature="login()",
            intent="", decorators=[{"name": "app.route", "args": ["/login"]}], literals=[],
        )
        G.add_edge("src/views.py", "login@src/views.py", etype="defines")
        _, symbols, _ = search_graph(G, "/login")
        assert symbols
        assert symbols[0]["name"] == "login"
        assert "via" in symbols[0]
        assert "/login" in symbols[0]["via"]


# ---------------------------------------------------------------------------
# 8c — Python string literals
# ---------------------------------------------------------------------------


class TestPythonLiterals:
    def test_literal_in_function_indexed(self, tmp_path: Path):
        src = 'def foo():\n    raise ValueError("rate limit exceeded")\n'
        _, symbols, _, _ = _analyse_python(src, "src/foo.py", tmp_path)
        sym = symbols[0]
        assert any("rate limit exceeded" in lit["value"] for lit in sym["literals"])

    def test_short_literal_not_indexed(self, tmp_path: Path):
        src = 'def foo():\n    x = "hi"\n'
        _, symbols, _, _ = _analyse_python(src, "src/foo.py", tmp_path)
        sym = symbols[0]
        assert all(len(lit["value"]) >= 8 for lit in sym["literals"])

    def test_docstring_not_duplicated(self, tmp_path: Path):
        src = 'def foo():\n    """This is the docstring."""\n    pass\n'
        _, symbols, _, _ = _analyse_python(src, "src/foo.py", tmp_path)
        sym = symbols[0]
        assert not any("This is the docstring." in lit["value"] for lit in sym["literals"])

    def test_long_literal_truncated(self, tmp_path: Path):
        long = "x" * 300
        src = f'def foo():\n    x = "{long}"\n'
        _, symbols, _, _ = _analyse_python(src, "src/foo.py", tmp_path)
        sym = symbols[0]
        if sym["literals"]:
            assert all(len(lit["value"]) <= 201 for lit in sym["literals"])

    def test_max_20_literals_per_symbol(self, tmp_path: Path):
        lines = [f'    x{i} = "literal_value_{i:03d}"' for i in range(30)]
        src = "def foo():\n" + "\n".join(lines) + "\n"
        _, symbols, _, _ = _analyse_python(src, "src/foo.py", tmp_path)
        sym = symbols[0]
        assert len(sym["literals"]) <= 20

    def test_extract_python_literals_basic(self):
        tree = ast.parse('def foo():\n    raise ValueError("rate limit exceeded")\n')
        node = tree.body[0]
        lits = _extract_python_literals(node, "")
        assert any("rate limit exceeded" in lit["value"] for lit in lits)


# ---------------------------------------------------------------------------
# 8c — Literal inverted index + quoted search
# ---------------------------------------------------------------------------


class TestLiteralIndex:
    def _make_graph_with_literal(self) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node("src/errors.py", ntype="file", path="src/errors.py", language="python", subproject="")
        G.add_node(
            "raise_error@src/errors.py",
            ntype="symbol", name="raise_error", stype="function",
            path="src/errors.py", line=1, signature="raise_error()",
            intent="", decorators=[],
            literals=[{"value": "rate limit exceeded", "line": 5}],
        )
        G.add_edge("src/errors.py", "raise_error@src/errors.py", etype="defines")
        _build_literal_index(G)
        return G

    def test_literal_index_built(self):
        G = self._make_graph_with_literal()
        assert "string_literals" in G.graph
        assert any("rate limit exceeded" in k for k in G.graph["string_literals"])

    def test_is_literal_query_detection(self):
        assert _is_literal_query('"rate limit exceeded"')
        assert _is_literal_query('"x"')  # valid literal query, just won't match anything indexed
        assert not _is_literal_query("rate limit exceeded")
        assert not _is_literal_query("")
        assert not _is_literal_query('""')  # empty quoted string not valid (len == 2)

    def test_quoted_search_returns_literal_matches(self):
        G = self._make_graph_with_literal()
        _, _, lm = search_graph(G, '"rate limit exceeded"')
        assert lm
        assert lm[0]["symbol"] == "raise_error"
        assert lm[0]["file"] == "src/errors.py"

    def test_quoted_search_renders_literal_matches_toon(self):
        G = self._make_graph_with_literal()
        _, _, lm = search_graph(G, '"rate limit exceeded"')
        out = render_search_toon('"rate limit exceeded"', [], [], lm)
        assert "literal_matches[" in out
        assert "raise_error" in out

    def test_unquoted_search_unaffected(self):
        G = self._make_graph_with_literal()
        files, symbols, lm = search_graph(G, "raise_error")
        assert lm == []
        assert symbols

    def test_literal_index_built_by_build_graph(self, tmp_path: Path):
        src = 'def report_error():\n    raise ValueError("rate limit exceeded here")\n'
        (tmp_path / "errors.py").write_text(src, encoding="utf-8")
        from relic.indexer import build_graph
        G, _ = build_graph(tmp_path)
        assert "string_literals" in G.graph


# ---------------------------------------------------------------------------
# 8d — cost header in MCP responses
# ---------------------------------------------------------------------------


class TestCostHeader:
    def test_cost_header_present_in_mcp_response(self, tmp_project: Path):
        from relic.mcp_server import _handle_query
        result = _handle_query({"target": "src/processor.py", "depth": 1})
        text = result[0].text
        assert "cost{response_tokens,focus_file_tokens}:" in text

    def test_cost_header_after_freshness_header(self, tmp_project: Path):
        from relic.mcp_server import _handle_query
        result = _handle_query({"target": "src/processor.py", "depth": 1})
        text = result[0].text
        lines = text.splitlines()
        index_line = next(i for i, l in enumerate(lines) if l.startswith("index{"))
        cost_line = next(i for i, l in enumerate(lines) if l.startswith("cost{"))
        assert cost_line == index_line + 1

    def test_response_tokens_positive(self, tmp_project: Path):
        from relic.mcp_server import _handle_query
        result = _handle_query({"target": "src/processor.py", "depth": 1})
        text = result[0].text
        cost_line = next(l for l in text.splitlines() if l.startswith("cost{"))
        vals = cost_line.split(": ", 1)[1].split(",")
        response_tokens = int(vals[0])
        assert response_tokens > 0


# ---------------------------------------------------------------------------
# 8d — tiered MUST/SHOULD rules in instructions
# ---------------------------------------------------------------------------


class TestTieredRules:
    def test_tiered_must_has_criteria_a_b_c(self):
        from relic.agent_config import RELIC_INSTRUCTIONS
        assert "(a)" in RELIC_INSTRUCTIONS
        assert "(b)" in RELIC_INSTRUCTIONS
        assert "(c)" in RELIC_INSTRUCTIONS

    def test_cost_header_mentioned_in_rules(self):
        from relic.agent_config import RELIC_INSTRUCTIONS
        assert "cost{" in RELIC_INSTRUCTIONS

    def test_skip_rule_present(self):
        from relic.agent_config import RELIC_INSTRUCTIONS
        assert "SKIP" in RELIC_INSTRUCTIONS

    def test_should_query_symbol_present(self):
        from relic.agent_config import RELIC_INSTRUCTIONS
        assert "SHOULD call `relic_query <symbol>`" in RELIC_INSTRUCTIONS


# ---------------------------------------------------------------------------
# 8d — audit --usage (compute_usage_audit)
# ---------------------------------------------------------------------------


class TestAuditUsage:
    def test_compute_usage_audit_none_when_missing(self, tmp_path: Path):
        from relic.audit import compute_usage_audit
        assert compute_usage_audit(tmp_path) is None

    def test_compute_usage_audit_reads_json(self, tmp_path: Path):
        import json
        from relic.audit import compute_usage_audit
        data = {"query_count": 5, "search_count": 2}
        (tmp_path / "usage.json").write_text(json.dumps(data), encoding="utf-8")
        result = compute_usage_audit(tmp_path)
        assert result is not None
        assert result["query_count"] == 5
