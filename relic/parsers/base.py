"""Base parser protocol and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class AnalysisResult:
    """Unified return type for all language parsers."""

    imports: list[str] = field(default_factory=list)
    symbols: list[dict] = field(default_factory=list)
    imported_names: list[tuple[str, str]] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)


@runtime_checkable
class Parser(Protocol):
    """Interface that every language parser must implement."""

    lang: str

    def analyse(self, source: str, rel_path: str, project_root: "Path") -> AnalysisResult: ...  # noqa: F821


_REGISTRY: dict[str, Parser] = {}


def register(parser: Parser) -> None:
    """Register a parser for a language."""
    _REGISTRY[parser.lang] = parser


def get_parser(lang: str) -> Parser | None:
    """Return the registered parser for *lang*, or ``None``."""
    return _REGISTRY.get(lang)


def _load_treesitter_parsers() -> None:
    """Try to import and register tree-sitter parsers. No-op if not installed."""
    try:
        from relic.parsers.treesitter import register_all

        register_all()
    except ImportError:
        pass


_load_treesitter_parsers()
