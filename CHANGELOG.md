# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-05-03

### Added

- **Core**: Static knowledge graph built from Python and TypeScript/JS
  source files using NetworkX. No LLM calls, no network access.
- **CLI commands**: `relic init`, `relic index`, `relic query`, `relic search`,
  `relic stats`, `relic watch`, `relic coverage`, `relic audit`,
  `relic benchmark`.
- **MCP server**: Four tools — `relic_query`, `relic_search`, `relic_reindex`,
  `relic_stats` — served over stdio for agent integration.
- **TOON output**: Token-Oriented Object Notation for compact, LLM-friendly
  graph serialization (300-1,200 tokens vs 5,000-40,000 for raw file reads).
- **Agent config**: `relic --init <agent>` writes instructions and MCP
  registration for Claude Code, Cursor, Copilot, and Codex.
- **Filesystem watcher**: `relic watch` auto-rebuilds the index on file
  changes with configurable debounce.
- **Coverage report**: `relic coverage` shows indexed vs skipped files with
  skip reasons (no parser, too large, symlink).
- **Token audit**: `relic audit` measures relic's own token footprint
  (instructions + MCP descriptions + sample query).
- **Benchmark**: `relic benchmark <file>` compares manual file reads vs
  relic-assisted TOON context.
- **CLI theme**: Nord-inspired colour palette with `⬢` brand mark, styled
  output via Rich.
- **Self-update**: `relic --update` pulls latest from GitHub and reinstalls.
- **Token budget tests**: Regression tests enforce instruction (≤ 800 tokens)
  and MCP description (≤ 500 tokens) budgets.
- **Cache stability tests**: MCP `list_tools()` output is byte-identical
  across calls with no dynamic content leakage.

[unreleased]: https://github.com/Swanand58/relic/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Swanand58/relic/releases/tag/v0.1.0
