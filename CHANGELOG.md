# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (Phase 8 — Semantic Index)

- **Symbol intent**: first line of docstring or leading comment per symbol stored
  as an `intent` field (max 80 chars). Visible in `relic_query` exports table
  (`exports[N]{name,type,line,signature,intent}`) and `relic_search` results.
- **Decorator index**: decorator names and literal arguments indexed per symbol
  (max 5). Surfaces in `relic_query` as a `decorators[N]{symbol,decorator,args}`
  table. Decorator-matched search results show `via=decorator:<name>`.
- **String literal index**: string constants ≥ 8 chars inside function bodies
  indexed in an inverted lookup (max 20 per symbol, max 200 chars each). Search
  by quoting the query: `relic_search '"payment failed"'`. Results in
  `literal_matches[N]{value,symbol,file,line}` table.
- **Cost header**: every MCP response now emits a second header line
  `cost{response_tokens,focus_file_tokens}`. Agents use `focus_file_tokens` to
  apply the SKIP rule without an extra roundtrip.
- **Tiered agent rules**: MUST/SHOULD/SKIP tiers replace the flat MUST list.
  `relic_query` is skippable for small isolated files (< 200 tokens, 0 callers).
  `relic_query <symbol>` demoted from MUST to SHOULD.
- **`relic audit --usage`**: shows per-tool MCP call counts from
  `.knowledge/usage.json`, written by the MCP server after each call.
- **`relic_query include_intent` parameter**: opt-in `include_intent=false` drops
  the `intent` column from exports (saves ~10% tokens on very large graphs).
- **Tree-sitter semantic fields**: Go, Rust, and Java parsers now extract `intent`
  (leading `///` / `//` / Javadoc comment) and decorators / `#[attrs]` /
  `@Annotations` alongside symbol definitions.
- **Rich markup stripping**: string literal indexer strips `[bold red]...[/bold red]`
  markup before storing, preventing terminal control sequences from leaking into
  search results.

### Added (earlier — incremental reindex + freshness header)

- **Incremental reindex**: `relic_reindex` now stat-sweeps the project tree and
  reparses only files whose mtime changed since the last index. Sub-second on
  large monorepos. Backed by a new `.knowledge/mtimes.json` sidecar (atomic
  writes).
- **Freshness header**: every MCP response is prefixed with
  `index{age_s,stale,files_changed}`. Agents read it to decide when to call
  `relic_reindex`. Cached for 2 s so back-to-back calls in the same turn pay
  the stat-sweep cost only once.

### Changed

- **`relic_reindex` MCP tool** is now incremental-only. If no index exists yet
  the call returns a clear error asking the user to run `relic index` once
  manually — the MCP server intentionally does not perform full cold-start
  rebuilds (they would blow past client request timeouts on large repos).

### Removed

- **`relic_stats` MCP tool**: removed. Freshness rides on every response
  header instead. The `relic stats` CLI command is unchanged for human use.
- **`relic watch` CLI command + `relic/watcher.py`**: removed. With incremental
  reindex and the freshness header, the agent owns staleness — a separate
  background watcher process (and the `watchdog` dependency) is no longer
  needed.

## [0.2.3] - 2026-05-05

### Fixed

- `relic --version` now reads version dynamically from package metadata instead of a hardcoded string.
- `relic --update` uv fallback uses `uv tool upgrade` instead of invalid `uv tool install --upgrade`.

## [0.2.2] - 2026-05-05

### Fixed

- Added `readme` and `license` fields to pyproject.toml so PyPI renders the project page correctly.

## [0.2.1] - 2026-05-05

### Changed

- Package renamed to `relic-graph` for PyPI distribution.
- `relic --update` now upgrades from PyPI instead of GitHub.
- Dependency floors bumped: networkx >=3.6.1, mcp >=1.27.0, ruff >=0.15.12.

## [0.2.0] - 2026-05-04

### Added

- **Function signatures**: Symbol nodes carry full signatures extracted from
  Python AST and TypeScript regex. Agents see parameter types, return types,
  and arity without reading source files.
- **Test file mapping**: Convention-based `tested_by`/`tests` edges
  (`test_foo.py`, `foo.test.ts`, `__tests__/`). Agents stop grepping for
  test files — the graph already knows.
- **Python class inheritance**: `extends` edges from `ast.ClassDef.bases`.
- **Batch query**: `relic_query "A B C"` returns one merged TOON instead of
  three separate calls. Cuts round-trips and header overhead.
- **Symbol-scoped query**: `relic_query Class.method` resolves dotted notation
  to the specific symbol, producing a smaller TOON than querying the whole file.
- **Blast radius / callers**: `uses` edges track `from X import Y` at symbol
  level. TOON output includes a `callers` section showing which files reference
  each exported symbol.
- **`relic diff`**: Compares on-disk source state against the last indexed graph.
  Shows new files, deleted files, and changed symbols so agents know whether to
  reindex.
- **Smart update**: `relic --update` now installs the latest GitHub release tag
  instead of tracking `main`.

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

[unreleased]: https://github.com/Swanand58/relic/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Swanand58/relic/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Swanand58/relic/releases/tag/v0.1.0
