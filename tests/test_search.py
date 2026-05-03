"""Unit tests for relic.search — scoring, filtering, ranking, TOON rendering."""

from __future__ import annotations

import pytest

from relic.search import (
    SCORE_EXACT,
    SCORE_PREFIX,
    SCORE_SUBSTRING,
    _normalize,
    _score,
    available_subprojects,
    render_search_toon,
    search_graph,
    suggest_close_matches,
)


# ---------------------------------------------------------------------------
# _score primitive
# ---------------------------------------------------------------------------

class TestScore:
    def test_exact_match_wins(self):
        assert _score("payment", "payment") == SCORE_EXACT

    def test_prefix_match(self):
        assert _score("paymentprocessor", "payment") == SCORE_PREFIX

    def test_substring_match(self):
        assert _score("recurringpaymentservice", "payment") == SCORE_SUBSTRING

    def test_no_match(self):
        assert _score("orders", "payment") == 0

    def test_score_ordering(self):
        assert SCORE_EXACT > SCORE_PREFIX > SCORE_SUBSTRING > 0


# ---------------------------------------------------------------------------
# search_graph — file/symbol matching, ranking, filtering
# ---------------------------------------------------------------------------

class TestSearchGraph:
    def test_empty_query_returns_empty(self, sample_graph):
        files, symbols = search_graph(sample_graph, "")
        assert files == []
        assert symbols == []

    def test_whitespace_query_returns_empty(self, sample_graph):
        files, symbols = search_graph(sample_graph, "   ")
        assert files == []
        assert symbols == []

    def test_exact_symbol_match(self, sample_graph):
        _, symbols = search_graph(sample_graph, "PaymentProcessor")
        names = [s["name"] for s in symbols]
        assert "PaymentProcessor" in names

    def test_substring_finds_partials(self, sample_graph):
        _, symbols = search_graph(sample_graph, "payment")
        # case-insensitive — should hit PaymentProcessor
        assert any(s["name"] == "PaymentProcessor" for s in symbols)

    def test_kind_file_excludes_symbols(self, sample_graph):
        files, symbols = search_graph(sample_graph, "process", kind="file")
        assert symbols == []
        # processor.py contains "process" substring
        assert any("processor" in f["path"] for f in files)

    def test_kind_symbol_excludes_files(self, sample_graph):
        files, symbols = search_graph(sample_graph, "process", kind="symbol")
        assert files == []
        assert len(symbols) >= 1

    def test_kind_all_returns_both(self, sample_graph):
        files, symbols = search_graph(sample_graph, "process", kind="all")
        assert files
        assert symbols

    def test_subproject_filter_files(self, sample_graph):
        files, _ = search_graph(sample_graph, "processor", subproject="payments")
        assert files
        assert all(f["subproject"] == "payments" for f in files)

    def test_subproject_filter_symbols(self, sample_graph):
        _, symbols = search_graph(sample_graph, "process", subproject="orders")
        # orders/handler.py defines `process` — its inherited subproject is `orders`
        assert symbols
        assert all(s["path"].startswith("orders/") for s in symbols)

    def test_subproject_filter_excludes_other(self, sample_graph):
        # `Order` class only exists in payments/models.py — filtering by `orders`
        # subproject must not match it (despite the substring `Order`)
        _, symbols = search_graph(sample_graph, "Order", subproject="orders")
        assert symbols == []

    def test_unknown_subproject_returns_empty(self, sample_graph):
        files, symbols = search_graph(sample_graph, "process", subproject="ghost")
        assert files == []
        assert symbols == []

    def test_ranking_exact_before_prefix_before_substring(self, sample_graph):
        # `process` matches symbols `process` (exact) twice and `PaymentProcessor` (substring).
        # The two exact-match `process` symbols must appear before PaymentProcessor.
        _, symbols = search_graph(sample_graph, "process", kind="symbol")
        names = [s["name"] for s in symbols]
        assert names.index("process") < names.index("PaymentProcessor")

    def test_limit_caps_results_per_category(self, sample_graph):
        files, symbols = search_graph(sample_graph, "p", kind="all", limit=1)
        assert len(files) <= 1
        assert len(symbols) <= 1

    def test_returns_node_data_dicts(self, sample_graph):
        files, _ = search_graph(sample_graph, "processor")
        assert files
        # callers rely on these keys
        for d in files:
            assert "path" in d
            assert "language" in d
            assert "subproject" in d


# ---------------------------------------------------------------------------
# render_search_toon — output formatting
# ---------------------------------------------------------------------------

class TestRenderSearchToon:
    def test_no_results_returns_plain_string(self):
        out = render_search_toon("ghost", [], [])
        assert out == "No results for 'ghost'."

    def test_renders_search_header(self, sample_graph):
        files, symbols = search_graph(sample_graph, "processor")
        out = render_search_toon("processor", files, symbols)
        assert out.startswith("search: processor")

    def test_includes_file_matches_table(self, sample_graph):
        files, _ = search_graph(sample_graph, "processor", kind="file")
        out = render_search_toon("processor", files, [])
        assert "file_matches[" in out
        assert "{path,language,subproject}" in out

    def test_includes_symbol_matches_table(self, sample_graph):
        _, symbols = search_graph(sample_graph, "process", kind="symbol")
        out = render_search_toon("process", [], symbols)
        assert "symbol_matches[" in out
        assert "{name,type,file}" in out

    def test_omits_empty_section(self, sample_graph):
        # only file matches → no symbol_matches section
        files, _ = search_graph(sample_graph, "processor", kind="file")
        out = render_search_toon("processor", files, [])
        assert "symbol_matches" not in out


# ---------------------------------------------------------------------------
# available_subprojects — used for input validation
# ---------------------------------------------------------------------------

class TestAvailableSubprojects:
    def test_returns_unique_subprojects(self, sample_graph):
        result = available_subprojects(sample_graph)
        assert result == {"payments", "orders", "api"}

    def test_skips_symbols(self, sample_graph):
        # symbols don't carry a subproject attr — must not leak through
        result = available_subprojects(sample_graph)
        assert all(isinstance(s, str) and s for s in result)

    def test_empty_graph_returns_empty_set(self):
        import networkx as nx
        assert available_subprojects(nx.DiGraph()) == set()


# ---------------------------------------------------------------------------
# _normalize — bridges snake_case / kebab-case / camelCase
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercases(self):
        assert _normalize("PaymentProcessor") == "paymentprocessor"

    def test_strips_underscores(self):
        assert _normalize("payment_processor") == "paymentprocessor"

    def test_strips_path_separators(self):
        assert _normalize("src/payments/processor.py") == "srcpaymentsprocessorpy"

    def test_strips_kebab_case(self):
        assert _normalize("payment-processor") == "paymentprocessor"

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_normalization_bridges_case_styles(self):
        assert _normalize("payment_processor") == _normalize("PaymentProcessor")


# ---------------------------------------------------------------------------
# suggest_close_matches — "did you mean?" for unresolved query targets
# ---------------------------------------------------------------------------

class TestSuggestCloseMatches:
    def test_empty_target_returns_empty(self, sample_graph):
        assert suggest_close_matches(sample_graph, "") == []

    def test_finds_typo_via_normalization(self, sample_graph):
        # snake_case typo of PaymentProcessor → still found
        suggestions = suggest_close_matches(sample_graph, "payment_processor")
        assert any("PaymentProcessor" in s for s in suggestions)

    def test_finds_partial_file_path(self, sample_graph):
        # user typed a partial path
        suggestions = suggest_close_matches(sample_graph, "processor.py")
        assert any("payments/processor.py" in s for s in suggestions)

    def test_labels_distinguish_files_and_symbols(self, sample_graph):
        suggestions = suggest_close_matches(sample_graph, "process")
        # files use `file:` prefix, symbols use `symbol:` prefix
        assert any(s.startswith("file:") for s in suggestions) or \
               any(s.startswith("symbol:") for s in suggestions)
        for s in suggestions:
            assert s.startswith(("file:", "symbol:"))

    def test_respects_limit(self, sample_graph):
        suggestions = suggest_close_matches(sample_graph, "p", limit=2)
        assert len(suggestions) <= 2

    def test_no_matches_returns_empty(self, sample_graph):
        assert suggest_close_matches(sample_graph, "xyzabc_nonexistent") == []

    def test_includes_file_path_for_symbols(self, sample_graph):
        suggestions = suggest_close_matches(sample_graph, "PaymentProcessor")
        # symbol suggestions carry the defining file in parens
        sym_suggestions = [s for s in suggestions if s.startswith("symbol:")]
        assert sym_suggestions
        assert any("(payments/processor.py)" in s for s in sym_suggestions)
