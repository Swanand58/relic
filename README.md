# relic

CLI tool that builds a static knowledge graph from your codebase and automatically injects precise, token-efficient context into your AI coding agent before every file read or edit — no manual commands, no cold reads, no hallucinations.

Works with any AI coding agent: Claude Code, GitHub Copilot, OpenAI Codex, Cursor, etc.

---

## How it works

```
relic index                    # scan codebase → .knowledge/index.pkl  (seconds, no LLM)
relic --init claude            # write CLAUDE.md + PreToolUse hook
```

From that point, every time your agent reads or edits a file, the hook fires automatically:

```
relic query src/payments/processor.py
```

The agent receives a TOON context block before touching the file:

```
focus: src/payments/processor.py

files[3]{path,language,subproject}:
  src/payments/processor.py,python,payments
  src/payments/models.py,python,payments
  src/core/database.py,python,core

symbols[5]{name,type,file,line}:
  PaymentProcessor,class,src/payments/processor.py,12
  process_charge,function,src/payments/processor.py,34
  handle_refund,function,src/payments/processor.py,67

imports[2]{from,to}:
  src/payments/processor.py,src/payments/models.py
  src/payments/processor.py,src/core/database.py
```

300–800 tokens per query. No LLM needed to build the index. No cold reads. No hallucinated imports.

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

### Upgrade to latest

```bash
relic --update
```

### Local dev install

```bash
uv tool install --editable .
```

---

## Setup

Run all setup commands **in your own terminal** — not inside the coding agent.

### Step 1 — auto-discover subprojects

```bash
cd your-project
relic init
```

Walks the project, detects subprojects from package manifests and source directories, writes `relic.yaml`, and adds `relic.yaml` and `.knowledge/` to `.gitignore`. Takes a second.

`relic.yaml` is personal config — gitignored by design. Each developer runs `relic init` on their own machine.

### Step 2 — build the knowledge graph

```bash
relic index
```

Statically analyses all source files. No LLM. Extracts files, classes, functions, imports, and inheritance relationships. Writes `.knowledge/index.pkl`.

Run this again after significant codebase changes.

### Step 3 — wire up your coding agent

```bash
relic --init claude     # Claude Code   → CLAUDE.md + .claude/settings.json hook
relic --init copilot    # GitHub Copilot → .github/copilot-instructions.md
relic --init cursor     # Cursor        → .cursorrules
relic --init codex      # OpenAI Codex  → AGENTS.md
relic --init all        # all of the above
```

For Claude Code, this also installs a **PreToolUse hook** into `.claude/settings.json`. The hook runs `relic query <file>` automatically before every Read or Edit — no manual commands needed.

Re-running `--init` is safe — it updates the existing block without duplicating it.

---

## That's it — start coding

With the hook active, your agent automatically gets context before touching any file. No paste, no prompt, no warm-up.

To query context manually (or for non-hook agents):

```bash
relic query src/payments/processor.py
relic query PaymentProcessor            # by symbol name
relic query src/payments/processor.py --depth 3   # wider neighbourhood
```

---

## Full command reference

```bash
relic init                     # auto-discover subprojects, write relic.yaml, update .gitignore
relic index                    # build knowledge graph from source (no LLM)
relic query <file|symbol>      # print TOON context subgraph to stdout
relic query <file> --depth N   # adjust BFS traversal depth (default 2)

relic --list                   # list subprojects defined in relic.yaml
relic --stale                  # check which graph.md docs are out of date
relic --refresh                # emit prompts for stale/missing graph.md docs only
relic --refresh <name>         # emit prompt for one subproject
relic --refresh --force        # force regenerate all graph.md docs

relic --init <agent>           # write relic instructions into agent config file
relic --init all               # write instructions for all supported agents
relic --update                 # pull latest from GitHub main and reinstall
relic --version                # print installed version
```

---

## Example: backend (payment app)

```
backend/
├── payments/       # Stripe integration, charge logic
├── auth/           # JWT, user sessions
└── notifications/  # Email + push after payment events
```

**Setup (once per machine):**

```bash
cd backend
relic init              # discovers payments, auth, notifications → relic.yaml
relic index             # builds graph in ~2 seconds
relic --init claude     # wires CLAUDE.md + hook
```

**Start a new session — zero manual context loading:**

Open Claude Code. The hook fires before the first Read. Agent immediately knows:

- `PaymentProcessor` is in `payments/processor.py:12`
- It imports `ChargeModel` from `payments/models.py`
- `notifications/sender.py` imports `PaymentProcessor`
- None of this required reading or summarising any file

**Working on a cross-subproject bug:**

```bash
relic query payments/processor.py --depth 3
```

Prints a wider TOON subgraph. Pipe it into the agent or paste it directly.

**After a big refactor — rebuild the graph:**

```bash
relic index
```

Done in seconds. No LLM needed.

---

## Graph.md knowledge docs (optional)

For agents and workflows that prefer rich markdown documentation alongside the static graph, relic can generate LLM-written `graph.md` files per subproject.

Run in your terminal — your active agent reads the output and writes the files:

```bash
relic --refresh            # generate for stale or missing docs only
relic --refresh payments   # generate for one subproject
relic --refresh --force    # regenerate everything
```

Your agent writes:

```
.knowledge/
├── payments/graph.md
├── auth/graph.md
├── notifications/graph.md
└── graph.md              # master index
```

Each `graph.md` contains: subproject summary, entry points, key entities, API surface, cross-project dependencies, folder structure, and a confidence self-assessment.

Check staleness:

```bash
relic --stale
```

```
┌───────────────┬───────┬──────────────────────────────────┐
│ payments      │  yes  │ Commit 2024-01-15 newer than doc │
│ auth          │  no   │ Graph is up to date              │
│ notifications │  yes  │ graph.md does not exist          │
└───────────────┴───────┴──────────────────────────────────┘
```

---

## Token efficiency

| Approach | Tokens per context load |
|---|---|
| Full graph.md paste | ~35,000 |
| `relic query` (TOON, depth=2) | 300–800 |
| `relic query` (depth=3) | 800–2,000 |

The static graph never makes LLM calls. Build it once, query it thousands of times.

---

## What gets indexed

| Language | Files | Symbols | Imports |
|---|---|---|---|
| Python | ✓ | classes, functions | ✓ (ast) |
| TypeScript / TSX | ✓ | classes, functions, interfaces, types | ✓ |
| JavaScript / JSX | ✓ | classes, functions | ✓ |
| Other | ✓ (file nodes only) | — | — |

---

## Keeping graphs out of your repo

`relic init` automatically adds `relic.yaml` and `.knowledge/` to the project's `.gitignore`. These are local to each developer's machine.

If you want teammates to share the same index, remove those lines from `.gitignore` and commit `.knowledge/` alongside code changes.

---

## Security

**Path traversal prevention** — subproject paths in `relic.yaml` are resolved and checked against the project root. Entries like `path: /etc` or `path: ../../secrets` are rejected before any files are read.

**Symlink blocking** — relic skips all symlinks when walking directories, preventing escape outside the subproject.

**CLI argument sanitisation** — subproject names are validated against `[a-zA-Z0-9_-]` only. Attempts like `relic ../../etc` are rejected immediately.

**Prompt injection defence** — file content in `--refresh` prompts is wrapped in explicit delimiters and the agent is instructed to treat it as data, not instructions.

**File size limits** — relic skips files over 200 KB (indexer) / 100 KB (refresh prompts) and caps at 500 files per subproject.

**No external calls** — no API calls, no telemetry, no network access. Code never leaves your machine. The `--update` command passes only the hardcoded `github.com/Swanand58/relic@main` URL to uv — no user input reaches the shell.
