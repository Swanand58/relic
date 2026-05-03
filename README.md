# relic

Relic solves the cold-read problem in AI coding agents.

Every time an agent opens a file it reads that file, then the files it imports, then the files those import — just to understand what connects to what. That's 5-10 file reads before it can start on your actual task. Every read costs tokens.

Relic builds a static knowledge graph from your source code in seconds (no LLM). Before the agent touches any file, it calls `relic_query` and gets:

- What that file exports (exact symbol names and line numbers)
- What it imports (resolved paths, not guesses)
- What else in the codebase depends on it

300–1200 tokens. Via MCP — works with Claude Code, Cursor, Copilot, and any MCP-compatible agent.

---

## How it works

```
relic init              # auto-detect subprojects → relic.yaml
relic index             # static analysis → .knowledge/index.pkl  (seconds, no LLM)
relic --init claude     # write CLAUDE.md + register MCP server in .claude/settings.json
```

Agent calls `relic_query` before touching unfamiliar code:

```
focus: src/core/PageExtension.ts

neighbors[9]{path,language,subproject}:
  src/types.ts,typescript,src
  src/layout/presets.ts,typescript,src
  src/pagination/PaginationPlugin.ts,typescript,src
  ...

exports[8]{name,type,line}:
  resolvePageSize,function,21
  resolveMargins,function,29
  resolveHeader,function,38
  FolioStorage,interface,67
  ...

imports[8]{from,to}:
  src/core/PageExtension.ts,src/types.ts
  src/core/PageExtension.ts,src/layout/presets.ts
  ...

imported_by[1]{from,to}:
  src/index.ts,src/core/PageExtension.ts
```

Agent knows the structure before reading the code. Fewer follow-up reads. No hallucinated imports. No surprise broken callers.

---

## Install

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/Swanand58/relic
```

If `relic` is not found after install:

```bash
uv tool update-shell
```

Then open a new terminal tab.

### Upgrade

```bash
relic --update
```

### Local dev

```bash
uv tool install --editable .
```

---

## Setup

Run all setup commands **in your terminal** — not inside the agent.

### 1. Discover subprojects

```bash
cd your-project
relic init
```

Walks the project, detects subprojects from package manifests and source directories, writes `relic.yaml`, adds `relic.yaml` and `.knowledge/` to `.gitignore`. Both are personal — gitignored by design.

### 2. Build the index

```bash
relic index
```

Statically analyses all source files. No LLM. Extracts files, classes, functions, imports, and inheritance. Writes `.knowledge/index.pkl` and a human-readable `.knowledge/index.toon`.

Re-run after significant codebase changes, or use `relic_reindex` from inside the agent session. To keep the index fresh automatically while you work, run `relic watch` in a separate terminal tab.

### 3. Wire your agent

```bash
relic --init claude     # Claude Code    → CLAUDE.md + .claude/settings.json
relic --init copilot    # GitHub Copilot → .github/copilot-instructions.md + .vscode/mcp.json
relic --init cursor     # Cursor         → .cursorrules + .cursor/mcp.json
relic --init codex      # OpenAI Codex   → AGENTS.md
relic --init all        # all of the above
```

Writes agent instructions and registers the relic MCP server in the right config file per agent. Re-running is safe — updates the existing block without duplicating.

---

## MCP tools

Relic exposes four tools over MCP (stdio transport):

| Tool | When to call |
|---|---|
| `relic_query` | Before editing unfamiliar code — returns imports, exports, neighbors, callers |
| `relic_search` | When you don't know where a class/function/file lives |
| `relic_reindex` | After creating, editing, or deleting source files |
| `relic_stats` | To verify the index is fresh before a large refactor |

See [docs/MCP.md](docs/MCP.md) for full tool reference and agent setup.

---

## Query context manually

```bash
relic query src/core/PageExtension.ts
relic query resolveMargins               # by symbol name
relic query src/core/PageExtension.ts --depth 3   # wider graph
```

Output is TOON (Token-Oriented Object Notation) — tabular format that declares column names once and lists values row by row. ~40% fewer tokens than equivalent JSON for the same data.

---

## Keep the index fresh

```bash
relic watch
```

Runs in the foreground in a terminal tab. Listens to OS-native filesystem events (FSEvents on macOS, inotify on Linux, ReadDirectoryChangesW on Windows — no polling) and rebuilds the index when source files change. Bursts of edits are coalesced into a single reindex via a 500 ms debounce. Press Ctrl+C to stop.

Useful when an agent forgets to call `relic_reindex` after writing files — the watcher backfills the gap. Same parser, same security posture as `relic index`: parse-only static analysis, symlinks skipped, files over 200 KB skipped, nothing executed.

## Audit coverage

```bash
relic coverage
relic coverage --verbose    # list every skipped file
```

Shows the count and identity of files that were indexed vs silently dropped, classified by reason: `no_parser` (extension not supported), `too_large` (over 200 KB), `symlink` (skipped for safety). Use this when a query unexpectedly comes back empty — it tells you whether the file is a tool limit instead of a model error.

---

## Commands

```bash
relic init                     # auto-discover subprojects, write relic.yaml
relic index                    # build knowledge graph from source (no LLM)
relic query <file|symbol>      # print TOON context subgraph to stdout
relic query <file> --depth N   # adjust traversal depth (default 2)
relic search <term>            # ranked search across files and symbols
relic search <term> -k symbol  # filter to symbols (or `file`, `all`)
relic search <term> -s <name>  # restrict to a subproject
relic stats                    # index health: counts, last_updated, subprojects
relic watch                    # rebuild index automatically on file changes
relic watch --debounce-ms 200  # tighter debounce window (default 500 ms)
relic coverage                 # what's indexed vs skipped, with reasons
relic coverage -v              # list every skipped file (not just samples)
relic mcp                      # start MCP stdio server (4 tools)

relic --list                   # list subprojects in relic.yaml
relic --init <agent>           # write agent config + MCP registration
relic --init all               # write config for all supported agents
relic --update                 # pull latest from GitHub main and reinstall
relic --version                # print version
```

---

## What gets indexed

| Language | Files | Symbols | Imports |
|---|---|---|---|
| Python | ✓ | classes, functions | ✓ (ast) |
| TypeScript / TSX | ✓ | classes, functions, interfaces, types | ✓ |
| JavaScript / JSX | ✓ | classes, functions | ✓ |
| Other | ✓ (file nodes only) | — | — |

---

## Agentic pipelines (LangGraph, custom orchestrators)

Relic slots into multi-agent workflows. One `relic index` call, every agent in the chain benefits.

Typical integration:
- **Orchestrator** — `relic_query` to identify relevant files without reading them, scope the task cheaply
- **Planning agent** — knows the dependency graph before planning, avoids plans that break callers
- **Implementation agent** — gets exact symbol names and paths, no hallucinated imports
- **Review agent** — sees `imported_by` edges, knows what to test beyond the changed file

Register once per project, all agents in the session get all four relic tools natively.

---

## Token comparison

| Approach | Context per file touch |
|---|---|
| Agent reads file + all imports manually | 5,000–40,000 tokens |
| `relic_query` (depth=1) | 300–1,200 tokens |
| `relic_query` (depth=2) | 800–3,000 tokens |

---

## Security

**Path traversal prevention** — subproject paths in `relic.yaml` are resolved and checked against the project root. Entries like `path: /etc` or `path: ../../secrets` are rejected.

**Symlink blocking** — relic skips all symlinks when walking directories.

**CLI argument sanitisation** — the query target is validated before shell execution.

**File size limits** — skips files over 200 KB, caps at 500 files per subproject.

**No external calls** — no API calls, no telemetry. Code never leaves your machine. `--update` passes only the hardcoded `github.com/Swanand58/relic@main` URL to uv.
