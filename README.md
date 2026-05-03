# relic

Relic solves the cold-read problem in AI coding agents.

Every time an agent opens a file it reads that file, then the files it imports, then the files those import — just to understand what connects to what. That's 5-10 file reads before it can start on your actual task. Every read costs tokens.

Relic builds a static knowledge graph from your source code in seconds (no LLM). Before the agent touches any file, it automatically receives:

- What that file exports (exact symbol names and line numbers)
- What it imports (actual resolved paths, not guesses)
- What else in the codebase depends on it

300–1200 tokens. Injected automatically via a Claude Code hook. Zero workflow change.

---

## How it works

```
relic init              # auto-detect subprojects → relic.yaml
relic index             # static analysis → .knowledge/index.pkl  (seconds, no LLM)
relic --init claude     # write CLAUDE.md + PreToolUse hook + MCP server config
```

From that point, every Read or Edit fires automatically:

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

Re-run after significant codebase changes.

### 3. Wire your agent

```bash
relic --init claude     # Claude Code   → CLAUDE.md + .claude/settings.json
relic --init copilot    # GitHub Copilot → .github/copilot-instructions.md
relic --init cursor     # Cursor        → .cursorrules
relic --init codex      # OpenAI Codex  → AGENTS.md
relic --init all        # all of the above
```

For Claude Code this also installs a **PreToolUse hook** and registers the **relic MCP server**. The hook runs `relic query <file>` before every Read and Edit — no manual commands needed.

Re-running `--init` is safe — it updates the existing block without duplicating it.

---

## Query context manually

The hook handles this automatically, but you can also query directly:

```bash
relic query src/core/PageExtension.ts
relic query resolveMargins               # by symbol name
relic query src/core/PageExtension.ts --depth 3   # wider graph
```

Output is TOON (Token-Oriented Object Notation) — tabular format that declares column names once and lists values row by row. ~40% fewer tokens than equivalent JSON for the same data.

---

## Commands

```bash
relic init                     # auto-discover subprojects, write relic.yaml
relic index                    # build knowledge graph from source (no LLM)
relic query <file|symbol>      # print TOON context subgraph to stdout
relic query <file> --depth N   # adjust traversal depth (default 2)
relic mcp                      # start MCP stdio server (relic_query tool)

relic --list                   # list subprojects in relic.yaml
relic --init <agent>           # write agent config + hook
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

## MCP server

Relic exposes `relic_query` as a native MCP tool. For Claude Code, `relic --init claude` registers it automatically. For other MCP-compatible agents:

```json
"mcpServers": {
  "relic": { "command": "relic", "args": ["mcp"] }
}
```

Agents call `relic_query` directly instead of running a shell command.

---

## Agentic pipelines (Blueprint, LangGraph, custom orchestrators)

Relic is designed to slot into multi-agent workflows. One `relic index` call, every agent in the chain benefits.

Typical integration:
- **Orchestrator** — `relic query` to identify relevant files without reading them, scope the task cheaply
- **Planning agent** — knows the dependency graph before planning, avoids plans that break callers
- **Implementation agent** — gets exact symbol names and paths, no hallucinated imports
- **Review agent** — sees `imported_by` edges, knows what to test beyond the changed file

The MCP server is the cleanest integration point — register once, all agents in the session get `relic_query` as a native tool.

---

## Token comparison

| Approach | Context per file touch |
|---|---|
| Agent reads file + all imports manually | 5,000–40,000 tokens |
| `relic query` (depth=1, hook default) | 300–1,200 tokens |
| `relic query` (depth=2) | 800–3,000 tokens |

---

## Security

**Path traversal prevention** — subproject paths in `relic.yaml` are resolved and checked against the project root. Entries like `path: /etc` or `path: ../../secrets` are rejected.

**Symlink blocking** — relic skips all symlinks when walking directories.

**CLI argument sanitisation** — the query target is validated before shell execution.

**File size limits** — skips files over 200 KB, caps at 500 files per subproject.

**No external calls** — no API calls, no telemetry. Code never leaves your machine. `--update` passes only the hardcoded `github.com/Swanand58/relic@main` URL to uv.
