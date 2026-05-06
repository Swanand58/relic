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

---

## Phase 7 — Scaling Roadmap

Goal: compete with Graphify on language coverage and graph richness while keeping Relic's core advantage (zero-LLM, on-demand queries, token efficiency).

### Critical Bug Fix — Discovery for single-app projects

**Problem:** `relic init` on a Next.js/React/Django project only picks up directories in
a hardcoded `SOURCE_DIRS` set (`src`, `lib`, `app`, etc.). Directories like `components/`,
`atoms/`, `utils/`, `hooks/` are missed entirely.

**Root cause:** `relic/discovery.py` was designed for monorepos and multi-package projects,
not single-app projects where all code lives under the project root.

**Fix:** When the project root itself has a manifest file and is not a monorepo, treat
the root as a single subproject (`path: ./`). Additionally, rethink the indexer so that
code files are never silently skipped — subproject labels are a nice-to-have for
structure, but every source file must be indexed regardless.

See "Subproject architecture rethink" section below for the full design.

---

### 7a — Foundation (language infra + calls edges)

**7a.1: tree-sitter integration for multi-language support**

Currently `relic/indexer.py` uses Python `ast` for Python and regex for TS/JS. To scale
to 20+ languages, add tree-sitter as an optional dependency.

- Add `tree-sitter` + grammar packages as optional deps in `pyproject.toml`
- Create `relic/parsers/` package with a base interface and per-language modules
- Keep existing ast/regex parsers as zero-dep defaults
- First batch: Go, Rust, Java, C/C++, Ruby

**7a.2: `calls` edges via AST/tree-sitter**

Walk function bodies to find call expressions. For each call site, resolve the callee
to a known symbol node and create a `calls` edge (symbol -> symbol). Only deterministic
EXTRACTED calls — no INFERRED guessing.

- Python: `ast.Call` nodes, resolve `Name` and `Attribute` to known symbols
- TS/JS: regex for `identifier(` inside function bodies, match against known exports
- Tree-sitter languages: walk `call_expression` nodes

---

### 7b — Intelligence + Languages batch 2

**7b.1: Community detection (lightweight, no LLM)**

Use NetworkX built-in `louvain_communities` to assign a `community` attribute to each
file node. Surface in TOON output. New CLI command: `relic communities`.

**7b.2: Languages batch 2** — C#, Kotlin, Scala, PHP, Swift, Lua

**7b.3: `relic viz`**

Generate interactive HTML graph (D3.js or vis.js, single file, zero Python deps).
`relic viz` opens in default browser.

---

### 7c — Polish + remaining languages

**7c.1: Languages batch 3** — Zig, Elixir, Objective-C, Julia, SQL, Fortran

**7c.2: `relic explain <symbol>`** — signature, callers, callees, community, test files

**7c.3: `relic path <A> <B>`** — shortest dependency path between two symbols/files

**7c.4: Graph diff improvements** — calls edges, community shifts, impact radius

---

### Release plan

| Release | Contains | Languages |
|---------|----------|-----------|
| v0.3.0 | Discovery fix + calls edges | Python, TS/JS (existing) |
| v0.4.0 | tree-sitter infra + batch 1 | + Go, Rust, Java, C/C++, Ruby |
| v0.5.0 | Communities + viz + batch 2 | + C#, Kotlin, Scala, PHP, Swift, Lua |
| v0.6.0 | explain + path + batch 3 | + Zig, Elixir, ObjC, Julia, SQL, Fortran |

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
