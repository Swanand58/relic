"""Parser infrastructure for multi-language static analysis.

Provides a ``Parser`` protocol and a registry that selects the best available
parser for each language.  When ``tree-sitter-language-pack`` is installed,
tree-sitter based parsers are used for Go, Rust, Java, and other languages.
Otherwise, only the built-in Python ``ast`` and TS/JS regex parsers are
available (zero external dependencies).

Install tree-sitter support::

    pip install relic-graph[treesitter]
"""

from relic.parsers.base import AnalysisResult, Parser, get_parser

__all__ = ["AnalysisResult", "Parser", "get_parser"]
