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

## In flight (this branch, not yet merged)

| Feature | Branch | Notes |
|---|---|---|
| Remove Claude Code hook injection | `fix/remove-hook-support` | Hooks contradict relic's principle of being non-invasive and agent-agnostic — replaced entirely with MCP tools across all agents |
| `relic_search` MCP tool | `fix/remove-hook-support` | Substring match over file paths and symbol names. Linear graph scan, no ranking — basic version only |
| `relic_reindex` MCP tool | `fix/remove-hook-support` | Agent-driven freshness — rebuild index from inside the session |
| `relic_stats` MCP tool | `fix/remove-hook-support` | Index health: file/symbol/edge counts, last_updated, subprojects, edge type breakdown |
| `docs/MCP.md` | `fix/remove-hook-support` | Full tool reference and per-agent setup |

---

## Phase 3 — search, disambiguation, instructions

Single bundled PR. No new dependencies, no new file formats, no telemetry.
Goal: tighten the navigation layer so agents find the right thing the first
time and actually call the tools when they should.

**`relic_search` ranking + scope filter**
- Score: exact > prefix > substring (case-insensitive), tie-break by node degree so well-connected files surface first.
- Add `subproject` argument so monorepo searches stay scoped. Symbol nodes inherit their defining file's subproject.
- Output shape unchanged — same TOON tables.

**`relic search <term>` CLI subcommand**
- Shell parity for `relic_search`. Same scoring and filtering logic, lives in `relic/search.py` so CLI and MCP share one source of truth.
- Flags: `--kind`, `--subproject`, `--limit`. Prints TOON to stdout, status to stderr (matches `relic query`).

**Symbol disambiguation in `relic_query`**
- Today `_resolve_node` returns the first symbol that matches a name and silently drops the rest — confidently wrong context when names collide.
- Return a TOON candidate list (`name,type,file,line`) when 2+ symbols match. Agent re-queries with the full file path. Single-match behaviour unchanged.
- Mirror in CLI `query` so the two paths stay consistent.

**Sharper `RELIC_INSTRUCTIONS`**
- Imperative MUST/SHOULD rules instead of descriptive prose.
- Decision tree replaces the numbered workflow — every branch ends in a tool call.
- `init_agent` substitutes the most-connected file from the index (or a `relic.yaml` subproject path as fallback) into the example block so the very first sample call points at a real file in the user's project.

---

## Phase 4 — still on the radar (priority: medium)

**`relic watch`**
- Filesystem watcher (watchdog). Rebuilds index on source file changes.
- Run once in a terminal tab: `relic watch`.
- Passive, agent-agnostic alternative to `relic_reindex` — useful when the agent forgets to call the tool.

**`relic coverage`**
- Shows % of codebase indexed, which files were skipped and why (size limit, no parser, binary, etc).
- Without this, silent incompleteness looks like model error, not tool limit.

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
