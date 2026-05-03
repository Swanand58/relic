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

## In flight (this branch, not yet merged)

| Feature | Branch | Notes |
|---|---|---|
| Phase 4 — `relic watch` + `relic coverage` | `feat/watch-and-coverage` | Watchdog-backed filesystem watcher with 500 ms debounce and re-entrant pending flag; coverage report classifying files into indexed / no_parser / too_large / symlink with per-subproject breakdown and `--verbose` listing. 25 new tests (137 total). README updates. |

---

## Phase 5 — still on the radar (priority: medium)

**`relic diff`**
- Compares current source state against last index snapshot.
- Shows: new files, deleted files, changed symbols (new/removed functions/classes).
- Useful after big PR merges to know whether to re-run `relic index`.

---

## Key constraints (do not break)

- **No hook injection.** Relic must not write into another tool's hook surface (PreToolUse, PostToolUse, etc). Hooks are invasive, vendor-specific, and contradict the agent-agnostic positioning. All integration is via MCP tools or explicit CLI calls.
- **Agent-agnostic by default.** Every feature must work the same on Claude Code, Cursor, Copilot, Codex. No more Claude-only paths.
- **Token efficiency is a first-class constraint.** Every new output format must be benchmarkable against the manual-read baseline (`relic benchmark`).
- **No network egress.** No telemetry, no API calls. The only exception is `relic --update` hitting GitHub for a self-reinstall.
- **Static analysis only.** No LLM in the indexing or query path. Speed and determinism are the product.
