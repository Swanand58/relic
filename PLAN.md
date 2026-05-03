# Relic — Development Plan

## What's built and merged (main)

| Feature | Branch | Notes |
|---|---|---|
| `relic init` subcommand routing fix | `fix/init-subcommand-bypass` | Typer callback was eating "init" before Click could route it |
| `relic query` absolute path fix | `fix/query-absolute-path` | Hook passes absolute paths; index stores relative — strip PROJECT_ROOT |
| PreToolUse hook + MCP config | `feat/claude-hooks` | Writes hook + mcpServers to `.claude/settings.json` |
| `relic mcp` command | `feat/mcp-server` | stdio MCP server exposing `relic_query` native tool |
| Hook depth=1 | `perf/hook-depth-1` | depth=2 on barrel files loads entire subproject; depth=1 is enough for pre-edit context |
| Remove graph.md refresh pipeline | `cleanup/remove-refresh-pipeline` | Deleted `generator.py`, `staleness.py`, removed `--refresh`/`--stale`/`--force` flags |
| TOON 38% size reduction | `feat/toon-focus-symbols` | `exports` (focus file symbols), `neighbors` (file paths), `imported_by` (inbound edges), filter edge noise |
| README rewrite | `docs/readme-rewrite` | Cold-read problem framing, current architecture, no stale refresh docs |
| `relic benchmark` command | `feat/benchmark` | Proves token reduction vs manual agent reads, shows hidden callers |

---

## Phase 3 — not started

### High priority

**`relic watch`**
- Filesystem watcher (watchdog or similar). Rebuilds index on source file changes.
- Run once in a terminal tab: `relic watch`
- Agent-agnostic — works for Claude Code, Copilot, Cursor, Codex. No hook needed.
- Solves the stale index problem passively.

**`relic search <term>`**
- Fuzzy symbol/file search across entire graph.
- Returns matching files and symbols in TOON format.
- Useful for exploration — agent calls this when it doesn't know where something lives.
- Works for all agents (direct CLI call or via MCP tool).

**MCP `relic_reindex` tool**
- Add to existing MCP server alongside `relic_query`.
- Any MCP-compatible agent calls this after writing files to keep index fresh.
- Active alternative to watch mode — agent-driven rather than passive.
- Agent-agnostic (not Claude Code only).

**`relic query --json`**
- Structured JSON output for pipeline integration.
- Useful for blueprint, LangGraph, orchestrators that need machine-readable graph data.
- Same BFS traversal as current query, different serializer.

### Medium priority

**`relic coverage`**
- Shows % of codebase indexed, which files were skipped and why (size limit, no parser, etc).
- Useful for debugging why certain files aren't in the graph.

**`relic diff`**
- Compares current source state against last index snapshot.
- Shows: new files, deleted files, changed symbols (new/removed functions/classes).
- Useful after big PR merges to know whether to re-run `relic index`.

---

## Key constraints (do not break)

- Hooks (PreToolUse/PostToolUse) are **Claude Code only**. Do not pitch hook-based features as agent-agnostic.
- All agent-agnostic features must work via: watch mode (passive) or MCP tools (active).
- relic is for ALL agents — Claude Code, Copilot, Cursor, Codex — not just Claude.
- Token efficiency is a first-class constraint. Every new output format must be benchmarkable.
