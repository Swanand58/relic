# relic

[![CI](https://github.com/Swanand58/relic/actions/workflows/ci.yml/badge.svg)](https://github.com/Swanand58/relic/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Relic solves the cold-read problem in AI coding agents.

Every time an agent opens a file it reads that file, then the files it imports, then the files those import — just to understand what connects to what. That's 5-10 file reads before it can start on your actual task. Every read costs tokens.

Relic builds a static knowledge graph from your source code in seconds (no LLM). Before the agent touches any file, it calls `relic_query` and gets:

- What that file exports (symbol names, signatures, line numbers)
- What it imports (resolved paths, not guesses)
- What else in the codebase depends on it (callers at the symbol level)
- Which test file covers it
- Class inheritance chains

300–1200 tokens. Via MCP — works with Claude Code, Cursor, Copilot, and any MCP-compatible agent.

Relic also measures its own cost. `relic audit` shows exactly what relic adds to your agent's context (instructions block + MCP tool schemas) and proves it's a fraction of what it saves. No "trust us" — verifiable per project.

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

exports[8]{name,type,line,signature}:
  resolvePageSize,function,21,resolvePageSize(doc: PageDocument) -> number
  resolveMargins,function,29,resolveMargins(config: MarginConfig) -> Margins
  resolveHeader,function,38,resolveHeader(page: Page) -> HeaderBlock
  FolioStorage,interface,67,FolioStorage
  ...

imports[8]{from,to}:
  src/core/PageExtension.ts,src/types.ts
  src/core/PageExtension.ts,src/layout/presets.ts
  ...

imported_by[1]{from,to}:
  src/index.ts,src/core/PageExtension.ts

tested_by[1]{source,test}:
  src/core/PageExtension.ts,tests/PageExtension.test.ts

callers[2]{file,symbol}:
  src/index.ts,resolvePageSize
  src/render/engine.ts,resolveMargins
```

Agent knows the structure — including signatures, test files, and callers — before reading the code. Fewer follow-up reads. No hallucinated imports. No surprise broken callers.

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
| `relic_query` | Before editing unfamiliar code — returns imports, exports, signatures, neighbors, callers, test files. Supports batch (`"A B C"`), dotted notation (`Class.method`). |
| `relic_search` | When you don't know where a class/function/file lives |
| `relic_reindex` | After creating, editing, or deleting source files |
| `relic_stats` | To verify the index is fresh before a large refactor |

See [docs/MCP.md](docs/MCP.md) for full tool reference and agent setup.

---

## Query context manually

```bash
relic query src/core/PageExtension.ts
relic query resolveMargins                         # by symbol name
relic query PageExtension.resolveMargins           # dotted notation — scoped to one symbol
relic query "src/foo.ts src/bar.ts"                # batch — merged TOON for multiple targets
relic query src/core/PageExtension.ts --depth 3    # wider graph
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

## Check for drift

```bash
relic diff
```

Compares on-disk source files against the last indexed graph. Shows new files, deleted files, and changed symbols (added or removed functions/classes). Agents use this to decide whether to call `relic_reindex` after big PR merges. Humans use it to sanity-check the index before a session.

---

## Verify relic's own cost

```bash
relic audit
```

Shows three numbers: the instructions block written to `CLAUDE.md` / `.cursorrules` / `AGENTS.md`, the MCP tool schemas the agent loads every turn, and a sample `relic_query` against your real graph.

```
⬢  audit
   instructions block     ~651 tokens   CLAUDE.md / .cursorrules / AGENTS.md
   mcp tool schemas       ~365 tokens   4 tools, every turn
   ─────────────────────────────────
   baseline tax / turn  ~1,016 tokens

   sample query · src/core/PageExtension.ts · depth=2
   relic_query response  ~5,627 tokens
   manual baseline      ~27,952 tokens
   net savings          ~22,325 tokens (80%)

✓ baseline tax under 1,500 tokens — within healthy range
```

Background: 90-day instrumentation of Claude Code sessions found that 73% of tokens go to invisible chrome (CLAUDE.md bloat, MCP schemas, hooks, skills) before the agent reads a single user message. Relic's pitch is "save tokens" — it would be dishonest if relic itself were part of the problem. CI guards keep the instructions block under 800 tokens and the MCP schemas under 500 tokens. Loosening either is a deliberate decision, not a drift.

## Benchmark token savings on a real file

```bash
relic benchmark src/core/PageExtension.ts
relic benchmark src/core/PageExtension.ts --depth 2
```

For a single target file, prints what an agent would read manually (target file + every direct import), what relic provides instead (one TOON subgraph + the target file), and the resulting savings. Also surfaces the `imported_by` callers — files an agent has no way to discover from the file alone.

---

## Commands

```bash
relic init                         # auto-discover subprojects, write relic.yaml
relic index                        # build knowledge graph from source (no LLM)
relic query <file|symbol>          # print TOON context subgraph to stdout
relic query Class.method           # symbol-scoped query via dotted notation
relic query "fileA fileB"          # batch query — merged TOON output
relic query <file> --depth N       # adjust traversal depth (default 2)
relic search <term>                # ranked search across files and symbols
relic search <term> -k symbol      # filter to symbols (or `file`, `all`)
relic search <term> -s <name>      # restrict to a subproject
relic stats                        # index health: counts, last_updated, subprojects
relic diff                         # what changed since last index (new/deleted/changed)
relic watch                        # rebuild index automatically on file changes
relic watch --debounce-ms 200      # tighter debounce window (default 500 ms)
relic coverage                     # what's indexed vs skipped, with reasons
relic coverage -v                  # list every skipped file (not just samples)
relic audit                        # measure relic's own token footprint
relic benchmark <file>             # compare token cost of context with vs without relic
relic mcp                          # start MCP stdio server (4 tools)

relic --list                       # list subprojects in relic.yaml
relic --init <agent>               # write agent config + MCP registration
relic --init all                   # write config for all supported agents
relic --update                     # install latest GitHub release
relic --version                    # print version
```

---

## What gets indexed

| Language | Files | Symbols + Signatures | Imports | Inheritance | Test mapping |
|---|---|---|---|---|---|
| Python | ✓ | classes, functions (with full signatures) | ✓ (ast) | ✓ (`extends` edges) | ✓ (`test_foo.py`) |
| TypeScript / TSX | ✓ | classes, functions, interfaces, types (with signatures) | ✓ | ✓ (`extends` edges) | ✓ (`foo.test.ts`, `foo.spec.ts`) |
| JavaScript / JSX | ✓ | classes, functions (with signatures) | ✓ | ✓ | ✓ |
| Other | ✓ (file nodes only) | — | — | — | — |

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

**Static analysis only** — relic never executes your source. Python is parsed with `ast.parse` (parse-only, no eval); TypeScript and JavaScript are matched with regex. Reading a malicious repo cannot run code through relic.

**Path traversal prevention** — subproject paths in `relic.yaml` are resolved and checked against the project root. Entries like `path: /etc` or `path: ../../secrets` are rejected.

**Symlinks skipped** — the indexer ignores all symbolic links during traversal, so a malicious symlink pointing outside the project cannot pull foreign files into the graph. Same rule applies to `relic watch` and `relic coverage`.

**File size limit** — skips files over 200 KB. Bounds per-file work and prevents a single bloated file from dominating the index.

**No filesystem writes outside the project** — relic only writes to `.knowledge/` and (when explicitly invoked) `relic.yaml`, `.gitignore`, and the agent config files you ask it to update.

**No external calls** — no API calls, no telemetry. Code never leaves your machine. The only network calls relic makes are during `relic --update`: one GitHub API call to find the latest release tag, then `uv tool install` to reinstall from that tag.

For vulnerability reports see [SECURITY.md](SECURITY.md).

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, conventions,
and what we will (and won't) accept.

---

## License

[MIT](LICENSE)
