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
| v0.5.0 | Phase 7.5 (workflow / save passes) | Fewer round-trips per task |
| v0.6.0 | Phase 8 features (see below) | Intelligence |

---

## Phase 7.5 — Save the passes (agent workflow)

Goal: collapse the typical 3–4 MCP round-trip pattern (`stats` → `search` → `query` → open
file) into a **single `relic_query` call**. Driven by real agent feedback:

- Agents ran `relic_stats` on every task to check freshness — pure overhead.
- Agents that called `relic_reindex` on a large monorepo timed out every time
  (full rebuild blocks the MCP request beyond the client's 30–60 s budget).
- `relic_search` returned just names + paths, forcing an immediate `relic_query`
  follow-up to get any usable context.
- `relic_query` answered "what connects to what" but not "where does X happen",
  so agents still had to grep + read files for logic-based tasks.

Phase 7.5 fixes the surface without adding new MCP tools — every change either removes
a tool, removes a round-trip, or enriches an existing response.

### 7.5a — Remove `relic_stats` from the agent loop

`relic_stats` answers one question agents actually care about: *is the index fresh?*
That belongs in **every** response, not in a dedicated tool call.

- **Embed an `index{...}` header on every MCP response** (`relic_query`, `relic_search`,
  `relic_reindex`, `relic_diff`). Carries: `age_s`, `stale` (bool — derived from on-disk
  mtimes vs. stored index mtime), `files_changed` count.
- **Deprecate `relic_stats` as an MCP tool.** Keep `relic stats` CLI for humans.
- Agent instructions updated: "Never call stats; freshness is in every response."
- Net effect: ~1 wasted call per task eliminated.

### 7.5b — Remove `relic watch`

`relic watch` exists to keep the index fresh in the background. Once the agent itself
can reindex cheaply (7.5d), the watcher is redundant — and it adds a separate process
the user has to remember to start.

- **Delete `relic watch` command and `relic/watcher.py`.**
- Remove `watchdog` from `pyproject.toml` dependencies.
- Remove related tests (`tests/test_watcher.py`).
- Update README / agent rules to drop watcher references.
- The agent owns freshness: it sees `stale=true` in the header → it calls
  `relic_reindex` → incremental update finishes in < 1 s → it continues.

### 7.5c — Enrich `relic_search` so most tasks need only one call

Today `relic_search` returns name + path. Agents almost always follow up with
`relic_query` to get the signature and neighbors. Fold that context into the search hit.

- Each search hit gains: `signature`, one-line `docstring`, `neighbor_count`,
  `defining_file` (if symbol).
- Cost: ~30 tokens per hit, capped by `limit`.
- Net effect: the `search → query` chain collapses to one call for the common case.

### 7.5d — Incremental reindex (the timeout fix)

Full `build_graph` is the only path today. It re-parses every file every time. On a
large monorepo this blows past MCP client timeouts. No background jobs, no daemons —
just **don't do work that doesn't need doing**.

- **Stamp every file node with its `mtime` at index time.** Stored in a sidecar
  `.knowledge/mtimes.json` (cheap to read without unpickling the full graph).
- **`relic_reindex` becomes incremental by default**:
  - Walk the tree, collect current mtimes (one stat per file — cheap).
  - For each file: if `current_mtime <= stored_mtime`, **skip parsing entirely**.
  - Reparse only changed/new files. Drop nodes for deleted files.
  - Re-resolve cross-file edges (`uses`, `calls`, `extends`) for: touched files +
    files that import them. Leave the rest of the graph alone.
- **`stale=true` in the response header is computed the same way** — a single mtime
  sweep tells both the agent (via header) and the reindexer (when invoked) what's dirty.
- **Full rebuild stays available** as `relic index --full` (CLI) and as a
  `mode="full"` arg on `relic_reindex` for the rare case the schema bumps or the index
  gets corrupted. First-ever index on a fresh repo also goes through this path.
- Concurrency: write to a temp file then atomic rename, so a half-finished reindex
  can't corrupt the index for a parallel `relic_query`.
- Net effect: a typical reindex on a 50k-file monorepo with a handful of changed files
  drops from tens of seconds to sub-second. No MCP timeout. No background jobs needed.

### 7.5e — `content=` on `relic_query` (folded later in 7.5)

Last leg of "kill the read-the-file pass". Optional `content` parameter on
`relic_query`: scoped string/regex match inside the focused file/symbol body, returned
as `lineno: matching line` rows. Plus first-class indexing of decorators (e.g.
`@app.route("/login")`) and one-line docstrings as symbol attributes, so they appear in
the query response without bloating the default output.

(Detailed design after 7.5a–d ship — wanted to record the direction.)

### Round-trip ledger (target state)

| Wasted call today | What kills it | Phase |
|---|---|---|
| `relic_stats` | Freshness header on every response | 7.5a |
| Background `relic watch` process | Incremental reindex makes it pointless | 7.5b |
| `relic_search` → `relic_query` | Search returns sig + docstring + neighbor count | 7.5c |
| `relic_reindex` timeout / retry storm | Mtime-based incremental reindex | 7.5d |
| `relic_query` → open file → grep | `content=` param + decorator/docstring index | 7.5e |

Steady-state agent workflow: **one `relic_query` per question** in the common case.

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
