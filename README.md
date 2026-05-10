<p align="center">
  <img src="https://raw.githubusercontent.com/Swanand58/relic/main/assets/banner.svg" alt="relic" width="800"/>
</p>

<p align="center">
  <a href="https://github.com/Swanand58/relic/actions/workflows/ci.yml"><img src="https://github.com/Swanand58/relic/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
  <a href="https://pypi.org/project/relic-graph/"><img src="https://img.shields.io/pypi/v/relic-graph" alt="PyPI"/></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+"/></a>
  <a href="https://github.com/Swanand58/relic/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"/></a>
</p>

Relic solves the cold-read problem in AI coding agents.

Every time an agent opens a file it reads that file, then the files it imports, then the files those import — just to understand what connects to what. That's 5-10 file reads before it can start on your actual task. Every read costs tokens.

Relic builds a static knowledge graph from your source code in seconds (no LLM). Before the agent touches any file, it calls `relic_query` and gets:

- What that file exports (symbol names, signatures, line numbers, intent from docstrings)
- What it imports (resolved paths, not guesses)
- What else in the codebase depends on it (callers at the symbol level)
- Which test file covers it
- Class inheritance chains
- Decorator/annotation index (search by `@pytest.mark.parametrize`, `@app.route`, etc.)
- String literal index (search by quoting: `relic_search '"payment failed"'`)
- Full blast-radius of any change (`relic impact`) — every file transitively affected
- Shortest dependency path between any two files or symbols (`relic path A B`)
- Architecture clusters — file communities discovered from the import graph (`relic communities`)

300–1200 tokens. Via MCP — works with Claude Code, Cursor, Copilot, and any MCP-compatible agent.

Relic also measures its own cost. `relic audit` shows exactly what relic adds to your agent's context (instructions block + MCP tool schemas) and proves it's a fraction of what it saves. No "trust us" — verifiable per project.

---

## How it works

```
relic init              # scan project, build knowledge graph (seconds, no LLM)
relic --init claude     # write CLAUDE.md + register MCP server in .claude/settings.json
```

Agent calls `relic_query` before touching unfamiliar code:

```
focus: src/core/PageExtension.ts

neighbors[9]{path,language}:
  src/types.ts,typescript
  src/layout/presets.ts,typescript
  src/pagination/PaginationPlugin.ts,typescript
  ...

exports[8]{name,type,line,signature,intent}:
  resolvePageSize,function,21,resolvePageSize(doc: PageDocument) -> number,Compute the effective page size for a document
  resolveMargins,function,29,resolveMargins(config: MarginConfig) -> Margins,Apply margin config to a page layout
  resolveHeader,function,38,resolveHeader(page: Page) -> HeaderBlock,Build the header block for a page
  FolioStorage,interface,67,FolioStorage,
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

Agent knows the structure — including signatures, intent, test files, and callers — before reading the code. Fewer follow-up reads. No hallucinated imports. No surprise broken callers.

---

## Install

```bash
pip install relic-graph
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install relic-graph
```

If `relic` is not found after install:

```bash
uv tool update-shell
```

Then open a new terminal tab.

### Upgrade

```bash
pip install --upgrade relic-graph
```

Or:

```bash
relic --update
```

### Local dev

```bash
uv tool install --editable . --force
```

---

## Setup

Run all setup commands **in your terminal** — not inside the agent.

### 1. Initialize and index

```bash
cd your-project
relic init
```

Scans the entire project tree, builds the knowledge graph via static analysis (no LLM), and adds `.knowledge/` to `.gitignore`. No config file required — every source file with a recognized extension is indexed automatically.

To rebuild after changes:

```bash
relic index
```

Shows a delta (what changed since last index), which directories were skipped, and how many files were excluded by `.relicignore`. After the first `relic index`, agents call `relic_reindex` themselves — it's incremental (sub-second) and the response header tells them when it's needed.

**Optional:** To exclude files from indexing, create a `.relicignore` in your project root (same syntax as `.gitignore`):

```
generated/
*.pb.py
vendor/**
```

**Optional:** If you want subproject labels (for filtering search results), create a `relic.yaml` manually:

```yaml
subprojects:
  api:
    path: ./src/api
    description: REST API
  web:
    path: ./src/web
    description: Frontend
```

### 2. Wire your agent

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

Relic exposes four tools over MCP (stdio transport). Every response is prefixed
with an `index{age_s,stale,files_changed}` freshness header and a
`cost{response_tokens,focus_file_tokens}` header so agents know when to reindex
and can decide whether to skip a query on small files.

| Tool | When to call |
|---|---|
| `relic_query` | Before editing unfamiliar code — returns imports, exports (with intent), signatures, neighbors, callers, calls, decorators, test files. Supports batch (`"A B C"`), dotted notation (`Class.method`), `include_intent` toggle. Shorthands: `"impact:TARGET"` for blast-radius, `"A->B"` for shortest path. |
| `relic_search` | When you don't know where a class/function/file lives. Quote the query (`"error message"`) to search string literals inside function bodies. |
| `relic_reindex` | When the response header reports `stale=true`, or after creating, editing, or deleting source files. Incremental, sub-second. |
| `relic_diff` | When you want a per-file breakdown of what changed before reindexing |

See [docs/MCP.md](https://github.com/Swanand58/relic/blob/main/docs/MCP.md) for full tool reference and agent setup.

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

The agent owns this. `relic_reindex` is **incremental** — it stat-sweeps the
project tree, reparses only files whose mtime changed, and finishes in well under
a second on large repos. Every MCP response carries two headers:

```
index{age_s,stale,files_changed}: 42,false,0
cost{response_tokens,focus_file_tokens}: 847,312
```

`stale=true` means call `relic_reindex`. `cost{focus_file_tokens}` lets the agent
decide whether a `relic_query` on a tiny file is worth the roundtrip — if it's
under 200 tokens the agent can just read the file directly (SKIP rule). No
background watcher process, no extra terminal tab, no separate stats tool.

If you want to rebuild manually (after a big rebase or a tooling change), run
`relic index` — same as before.

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
relic audit --usage    # show MCP tool call counts from .knowledge/usage.json
```

Shows three numbers: the instructions block written to `CLAUDE.md` / `.cursorrules` / `AGENTS.md`, the MCP tool schemas the agent loads every turn, and a sample `relic_query` against your real graph. `--usage` adds a breakdown of how many times each MCP tool was called.

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

## `relic init` vs `relic index`

| | `relic init` | `relic index` |
|---|---|---|
| **When** | First-time setup | After code changes |
| **Builds graph** | Yes | Yes |
| **Adds `.knowledge/` to `.gitignore`** | Yes | No |
| **Shows delta** | No | Yes (new/removed files, symbols, edges) |
| **Shows skip stats** | No | Yes (skipped dirs, `.relicignore` exclusions) |

Run `relic init` once per project. After that, the agent's `relic_reindex` tool keeps the graph current incrementally; only run `relic index` again if you want a manual full rebuild.

---

## Commands

```bash
relic init                         # first-time setup: build graph, configure .gitignore
relic index                        # rebuild graph, show delta + skip stats
relic query <file|symbol>          # print TOON context subgraph to stdout
relic query Class.method           # symbol-scoped query via dotted notation
relic query "fileA fileB"          # batch query — merged TOON output
relic query <file> --depth N       # adjust traversal depth (default 2)
relic search <term>                # ranked search across files and symbols
relic search '"literal text"'      # search string literals inside function bodies
relic search <term> -k symbol      # filter to symbols (or `file`, `all`)
relic search <term> -s <name>      # restrict to a subproject
relic impact <file|symbol>         # blast-radius: every file transitively affected
relic path <source> <dest>         # shortest dependency path between two nodes
relic communities                  # show file clusters from Louvain graph clustering
relic stats                        # index health: counts, last_updated, subprojects
relic diff                         # what changed since last index (new/deleted/changed)
relic coverage                     # what's indexed vs skipped, with reasons
relic coverage -v                  # list every skipped file (not just samples)
relic audit                        # measure relic's own token footprint
relic audit --usage                # show per-tool MCP call counts
relic benchmark <file>             # compare token cost of context with vs without relic
relic mcp                          # start MCP stdio server (4 tools)

relic --list                       # list subprojects (if relic.yaml exists)
relic --init <agent>               # write agent config + MCP registration
relic --init all                   # write config for all supported agents
relic --update                     # install latest GitHub release
relic --version                    # print version
```

---

## What gets indexed

| Language | Files | Symbols + Signatures | Intent | Decorators | Imports | Calls | Inheritance | Test mapping |
|---|---|---|---|---|---|---|---|---|
| Python | ✓ | classes, functions (full signatures) | ✓ (docstring) | ✓ (`@decorator`) | ✓ (ast) | ✓ (ast) | ✓ (`extends`) | ✓ (`test_foo.py`) |
| TypeScript / TSX | ✓ | classes, functions, interfaces, types | ✓ (leading comment) | ✓ (`@decorator`) | ✓ | ✓ (regex) | ✓ (`extends`) | ✓ (`foo.test.ts`) |
| JavaScript / JSX | ✓ | classes, functions | ✓ (leading comment) | ✓ (`@decorator`) | ✓ | ✓ (regex) | ✓ | ✓ |
| Go | ✓ | structs, interfaces, functions | ✓ (leading `//`) | — | ✓ | ✓ | — | — |
| Rust | ✓ | structs, enums, traits, functions | ✓ (leading `///`) | ✓ (`#[attr]`) | ✓ | ✓ | ✓ (`impl`) | — |
| Java | ✓ | classes, interfaces, methods | ✓ (Javadoc) | ✓ (`@Annotation`) | ✓ | ✓ | ✓ (`extends`) | — |
| C# | ✓ | classes, interfaces, methods | ✓ (leading `//`) | ✓ (`[Attr]`) | ✓ | — | — | — |
| Kotlin | ✓ | classes, functions | ✓ (leading `//`) | — | ✓ | — | — | — |
| Scala | ✓ | classes, traits, objects, functions | ✓ (leading `//`) | — | ✓ | — | ✓ | — |
| PHP | ✓ | classes, interfaces, functions | ✓ (leading `//`) | — | ✓ | — | — | — |
| Swift | ✓ | classes, structs, protocols, functions | ✓ (leading `//`) | — | ✓ | — | — | — |
| Markdown | ✓ | H1/H2 headings as symbols | — | — | — | — | — | — |
| OpenAPI YAML/JSON | ✓ | HTTP endpoints (`GET /path`) | ✓ (summary) | — | — | — | — | — |
| JSON Schema | ✓ | `definitions`/`$defs` entries | ✓ (description) | — | — | — | — | — |
| pyproject.toml | ✓ | package name + script entry points | ✓ (description) | — | — | — | — | — |
| package.json | ✓ | package name + npm scripts | ✓ (description) | — | — | — | — | — |

Go, Rust, Java, C#, Kotlin, Scala, PHP, and Swift support requires the optional `treesitter` extra:

```bash
pip install relic-graph[treesitter]
```

---

## Graph analysis

### Blast-radius before refactoring

```bash
relic impact src/core/payments.py
relic impact PaymentProcessor
```

Shows every file that transitively imports, uses, or calls the target — and how many hops away each is. Run this before renaming a class or changing a function signature. Agents can call the same analysis via `relic_query "impact:TARGET"`.

### Shortest path between two nodes

```bash
relic path src/api/views.py src/core/database.py
relic path UserSerializer PaymentProcessor
```

Finds the shortest chain of import/call/use edges connecting SOURCE to DEST, with each hop's edge type and evidence label (`ast`/`treesitter`/`regex`/`convention`). Useful for understanding unexpected coupling. MCP shorthand: `relic_query "src/api/views.py->src/core/database.py"`.

### Architecture communities

```bash
relic communities
relic communities --limit 10
```

Runs Louvain clustering on the file import graph and prints each community as a TOON table. Communities represent cohesive module boundaries — files that strongly import each other end up together. Useful for spotting unexpected cross-module coupling or planning a monorepo split.

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

**Path traversal prevention** — if subproject paths are defined in `relic.yaml`, they are resolved and checked against the project root. Entries like `path: /etc` or `path: ../../secrets` are rejected.

**Symlinks skipped** — the indexer ignores all symbolic links during traversal, so a malicious symlink pointing outside the project cannot pull foreign files into the graph. Same rule applies to `relic coverage` and incremental reindexes.

**File size limit** — skips files over 200 KB. Bounds per-file work and prevents a single bloated file from dominating the index.

**No filesystem writes outside the project** — relic only writes to `.knowledge/` and (when explicitly invoked) `.gitignore` and the agent config files you ask it to update.

**No external calls** — no API calls, no telemetry. Code never leaves your machine. The only network calls relic makes are during `relic --update`: one GitHub API call to find the latest release tag, then `uv tool install` to reinstall from that tag.

For vulnerability reports see [SECURITY.md](https://github.com/Swanand58/relic/blob/main/SECURITY.md).

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](https://github.com/Swanand58/relic/blob/main/CONTRIBUTING.md) for setup, conventions,
and what we will (and won't) accept.

---

## License

[MIT](https://github.com/Swanand58/relic/blob/main/LICENSE)
