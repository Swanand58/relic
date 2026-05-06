# Relic — Development Plan

## What's built and merged (main)

| Feature | Branch | Notes |
|---|---|---|
| `relic init` subcommand routing fix | `fix/init-subcommand-bypass` | Typer callback was eating "init" before Click could route it |
| `relic query` absolute path fix | `fix/query-absolute-path` | Index stores relative paths — strip PROJECT_ROOT on lookup |
| `relic mcp` command | `feat/mcp-server` | stdio MCP server exposing `relic_query` native tool |
| Remove graph.md refresh pipeline | `cleanup/remove-refresh-pipeline` | Deleted `generator.py`, `staleness.py`, removed `--refresh`/`--stale`/`--force` flags |
| TOON 38% size reduction | `feat/toon-focus-symbols` | `exports` (focus file symbols), `neighbors` (file paths), `imported_by` (inbound edges), filter edge noise |
| README rewrite | `docs/readme-rewrite` | Cold-read problem framing, current architecture, no stale refresh docs |
| `relic benchmark` command | `feat/benchmark` | Proves token reduction vs manual agent reads, shows hidden callers |
| Remove Claude Code hook injection + MCP tools | `fix/remove-hook-support` | Hooks contradict the non-invasive principle. Replaced with `relic_search`, `relic_reindex`, `relic_stats` MCP tools across all agents. Adds `docs/MCP.md`. |
| Phase 3 — ranked search, disambiguation, imperative rules | `feat/search-and-disambiguation` | Scored search (exact > prefix > substring), subproject filter, `relic search` CLI, symbol disambiguation TOON candidate list, MUST/SHOULD agent instructions with decision tree, project-specific example file injection |
| Dogfood relic on the relic repo | `chore/dogfood-relic` | Project agent configs (CLAUDE.md, .cursorrules, AGENTS.md, copilot-instructions.md) and MCP registrations. Untracks personal `relic.yaml`/`.knowledge/`. Removes stale `_index.md`/`.gitkeep`. |
| Phase 4 — `relic watch` + `relic coverage` | `feat/watch-and-coverage` | Watchdog-backed filesystem watcher with 500 ms debounce and re-entrant pending flag; coverage report classifying files into indexed / no_parser / too_large / symlink with per-subproject breakdown and `--verbose` listing. 25 new tests (137 total). README updates. |
| CLI theme overhaul | `feat/cli-theme` | Nord-inspired palette, `⬢` brand mark, styled output via Rich, `relic.style` module, 10 new tests. |
| Phase 5 — `relic audit` + token budgets | `feat/audit-and-tax` | `relic audit` command measuring relic's own token cost. Trimmed MCP descriptions to ≤ 500 tokens combined. Regression tests for instruction (≤ 800) and MCP (≤ 500) token budgets. Cache-stability tests for `list_tools()`. |
| CI/CD + OSS scaffolding | `chore/ci-and-oss-scaffolding` | GitHub Actions CI (lint + test matrix + smoke), Dependabot, CODEOWNERS, PR/issue templates, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, CHANGELOG.md. Lint baseline (line-length 120, ruff format). Windows path normalization (POSIX slashes in graph). |
| Release workflow | `chore/release-workflow` | Automated GitHub Releases on version tags. Version consistency check (tag ↔ pyproject.toml). Changelog extraction. PyPI placeholder for later. |
| Phase 6 — deeper token savings | `feat/phase6-signatures-and-tests` | Function signatures in TOON, batch query, test file mapping, symbol-scoped query (Class.method), `uses` edges for named imports, `relic diff`, auto-index test directories. |
| PyPI publishing | `chore/pypi-publish` | Package renamed to `relic-graph`. Trusted Publishers workflow. `--update` upgrades from PyPI. |
| Dynamic version | `fix/dynamic-version` | `__version__` reads from `importlib.metadata` instead of hardcoded string. |
| v0.3.0 — Zero-config indexing | `feat/v0.3.0-zero-config` | `relic init` scans entire project tree. `relic.yaml` optional. Removed `discovery.py`. |
| Enhanced `relic index` | `feat/enhance-relic-index` | `.relicignore` support, skip stats, delta summary. |
| Zero-config all commands | `fix/yaml-optional-everywhere` | `relic diff`, `relic watch`, `relic coverage`, `relic --list` no longer require `relic.yaml`. |

---

## Phase 7 — Token Efficiency + Graph Richness

Goal: reduce per-query token waste and add `calls` edges so agents read fewer files.
Prioritized by real agent usage — every feature must answer: "Does this save the agent
a file read?"

### 7a — Reduce noise

**7a.1: Filter test symbols from `neighbor_symbols`**

When querying `indexer.py` at depth 2, 120+ of 174 neighbor symbols are test functions.
These are rarely useful for implementation tasks and waste ~2,000 tokens per query.

- Add `exclude_tests` parameter to `relic_query` (default `true`)
- Filter out symbols from files where `_is_test_file()` returns true from `neighbor_symbols`
- Keep `tested_by` edges (compact and useful)
- Keep test files in `neighbors` list (file names are cheap)
- Expected savings: 40–60% reduction in depth-2 query tokens

**7a.2: `relic_diff` MCP tool (5th tool)**

Expose `compute_diff` as an MCP tool so agents can check staleness without a CLI.

- New tool: `relic_diff` — returns TOON showing new/deleted/changed files and symbols
- Agents call this after merges or when they suspect the index is stale
- Eliminates speculative `relic_reindex` calls (each reindex is ~2s wall time + re-query)

---

### 7b — Calls edges

**7b.1: `calls` edges via Python AST**

Walk `ast.Call` nodes in function bodies, resolve `Name` and `Attribute` to known symbol
nodes in the graph. Only deterministic resolutions (callee exists in graph).

- Add `calls` section to TOON output: `calls[N]{caller,callee}`
- Enhance `callers` section to include call-site granularity (which function calls which)
- Saves 1–3 file reads per task — agent knows `diff.py::_symbol_fingerprint` calls
  `_analyse_python` without reading `diff.py`

**7b.2: `calls` edges via regex for TS/JS**

Same concept using existing regex infrastructure. Match `identifier(` patterns inside
function bodies against known exported symbols.

- Lower accuracy than AST but useful for obvious cases
- Same TOON output format as Python calls

---

### 7c — Smart depth

**7c.1: Cap `neighbor_symbols` with importance ranking**

Instead of dumping all symbols at depth N, cap to a token budget (e.g., top 30 by
connectivity score). Most-connected symbols first.

- Add `max_neighbor_symbols` parameter to `relic_query` (default 30)
- Rank by: number of callers + number of imports (proxy for importance)
- Include `... and N more` indicator so agent knows it can go deeper
- Bounds worst-case output — prevents 174-symbol dumps

---

### 7d — tree-sitter integration

**7d.1: tree-sitter for multi-language support**

Currently `relic/indexer.py` uses Python `ast` for Python and regex for TS/JS. To scale
to 20+ languages, add tree-sitter as an optional dependency.

- Add `tree-sitter` + grammar packages as optional deps in `pyproject.toml`
- Create `relic/parsers/` package with a base `Parser` protocol
- Keep existing ast/regex parsers as zero-dep defaults
- First batch: Go, Rust, Java (highest demand in AI coding)

---

### Release plan

| Release | Contains | Theme |
|---------|----------|-------|
| v0.4.0 | All of Phase 7 (7a–7d) | Token efficiency + graph richness |
| v0.5.0 | Phase 8 features (see below) | Intelligence |

---

## Phase 8 — Intelligence + Remaining Languages

Features moved here from the original roadmap. Will be planned in detail after Phase 7.

### 8a — Intelligence

**8a.1: Community detection (lightweight, no LLM)**

Use NetworkX built-in `louvain_communities` to assign a `community` attribute to each
file node. Surface in TOON output. New CLI command: `relic communities`.

**8a.2: `relic explain <symbol>`** — signature, callers, callees, community, test files

**8a.3: `relic path <A> <B>`** — shortest dependency path between two symbols/files

**8a.4: Graph diff improvements** — calls edges, community shifts, impact radius

### 8b — Languages batch 2

C#, Kotlin, Scala, PHP, Swift, Lua

### 8c — Languages batch 3

Zig, Elixir, Objective-C, Julia, SQL, Fortran

### 8d — Visualization

**`relic viz`** — Generate interactive HTML graph (D3.js or vis.js, single file, zero
Python deps). `relic viz` opens in default browser.

---

## What we are NOT doing (and why)

- **PDF / docs / image extraction** — requires LLM calls, contradicts zero-cost philosophy
- **INFERRED or AMBIGUOUS edges** — Relic's graph is deterministic, no guessing
- **Semantic similarity edges** — needs embeddings/LLM, not our lane
- **Global cross-repo graphs** — nice-to-have but not a priority vs. single-repo excellence

---

## Key constraints (do not break)

- **No hook injection.** Relic must not write into another tool's hook surface (PreToolUse, PostToolUse, etc). Hooks are invasive, vendor-specific, and contradict the agent-agnostic positioning. All integration is via MCP tools or explicit CLI calls.
- **Agent-agnostic by default.** Every feature must work the same on Claude Code, Cursor, Copilot, Codex. No more Claude-only paths.
- **Token efficiency is a first-class constraint.** Every new output format must be benchmarkable against the manual-read baseline (`relic benchmark`).
- **No network egress.** No telemetry, no API calls. The only exception is `relic --update` hitting PyPI for a self-upgrade.
- **Static analysis only.** No LLM in the indexing or query path. Speed and determinism are the product.
