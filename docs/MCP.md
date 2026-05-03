# Relic MCP Server

Relic exposes a Model Context Protocol (MCP) server over stdio. Any MCP-compatible
agent can call the four relic tools natively — no shell commands, no prompting required.

```bash
relic mcp    # start the server (stdio transport)
```

---

## Setup per agent

Run `relic --init <agent>` to write instructions and register the MCP server automatically.
Or register manually using the config snippets below.

### Claude Code

`relic --init claude` writes this into `.claude/settings.json`:

```json
{
  "mcpServers": {
    "relic": { "command": "relic", "args": ["mcp"] }
  }
}
```

### Cursor

`relic --init cursor` writes this into `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "relic": { "command": "relic", "args": ["mcp"] }
  }
}
```

### GitHub Copilot (VS Code)

Requires VS Code 1.99+. `relic --init copilot` writes this into `.vscode/mcp.json`:

```json
{
  "servers": {
    "relic": { "type": "stdio", "command": "relic", "args": ["mcp"] }
  }
}
```

### OpenAI Codex

Codex CLI does not have a standard MCP config file. `relic --init codex` writes
instructions into `AGENTS.md` — the agent uses the CLI tools documented there.

### Custom agents / orchestrators

Any MCP client (LangGraph, Blueprint, custom stdio client) can connect:

```python
import subprocess
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server = StdioServerParameters(command="relic", args=["mcp"])
async with stdio_client(server) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("relic_query", {"target": "src/core/processor.py"})
```

---

## Tools

### `relic_query`

Get dependency context for a file or symbol before editing it.

**When to call:** at the start of any edit session for unfamiliar code.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `target` | string | yes | — | File path (`src/payments/processor.py`) or symbol name (`PaymentProcessor`) |
| `depth` | integer | no | `2` | BFS hops from target node. Use `1` for barrel/index files. |

**Output:** TOON block — focus file, neighboring files, exported symbols, import edges, caller edges.

```
focus: src/payments/processor.py

neighbors[3]{path,language,subproject}:
  src/payments/models.py,python,payments
  src/payments/exceptions.py,python,payments
  src/core/database.py,python,core

exports[4]{name,type,line}:
  PaymentProcessor,class,12
  process_payment,function,45
  validate_card,function,78
  RETRY_LIMIT,variable,8

neighbor_symbols[6]{name,type,file}:
  Payment,class,src/payments/models.py
  PaymentStatus,type,src/payments/models.py
  DatabaseSession,class,src/core/database.py

imports[3]{from,to}:
  src/payments/processor.py,src/payments/models.py
  src/payments/processor.py,src/payments/exceptions.py
  src/payments/processor.py,src/core/database.py

imported_by[2]{from,to}:
  src/api/views.py,src/payments/processor.py
  src/workers/billing.py,src/payments/processor.py
```

**Depth guidance:**

| Depth | Use case | Token range |
|---|---|---|
| `1` | Barrel/index files, quick caller check | 300–1,200 |
| `2` (default) | Normal files — see two hops of context | 800–3,000 |
| `3+` | Deep refactors — wide graph, higher token cost | 2,000–8,000 |

---

### `relic_search`

Search for files and symbols across the knowledge graph by name.

**When to call:** when you don't know where a class, function, or file lives.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | string | yes | — | Partial file path or symbol name (case-insensitive substring) |
| `kind` | string | no | `"all"` | Filter: `"file"`, `"symbol"`, or `"all"` |
| `limit` | integer | no | `20` | Max results per category |

**Output:**

```
search: "processor"

file_matches[2]{path,language,subproject}:
  src/payments/processor.py,python,payments
  src/core/preprocessor.ts,typescript,core

symbol_matches[3]{name,type,file}:
  PaymentProcessor,class,src/payments/processor.py
  DataProcessor,class,src/core/preprocessor.ts
  process_payment,function,src/payments/processor.py
```

**Tips:**
- Search by partial name: `"proc"` matches `processor`, `preprocess`, `ProducerConfig`
- Search by directory: `"payments/"` returns only files in the payments subproject
- After finding the file, call `relic_query` on it for full context

---

### `relic_reindex`

Rebuild the knowledge graph from source code.

**When to call:** after creating, editing, or deleting source files.

**Parameters:** none.

**Output:**

```
reindex: done in 2.3s
files: 84
symbols: 612
edges: 1,204
```

**Notes:**
- Runs synchronously — takes 1–10 seconds depending on codebase size
- Safe to call multiple times — rewrites the index in place
- Equivalent to running `relic index` in the terminal

---

### `relic_stats`

Check knowledge graph health.

**When to call:** to verify the index is fresh before a large refactor, or when
`relic_query` returns unexpected results.

**Parameters:** none.

**Output:**

```
last_updated: 2026-05-03 14:22:01
files: 84
symbols: 612
edges: 1,204
  defines: 612
  imports: 571
  extends: 21
subprojects: core, payments, workers
```

---

## Reading TOON output

TOON (Token-Oriented Object Notation) is a tabular format. Column names are declared
once per table; values are listed row by row. ~40% fewer tokens than equivalent JSON.

**Table syntax:**

```
tableName[rowCount]{col1,col2,col3}:
  val1,val2,val3
  val1,val2,val3
```

**Edge tables:**

| Table | Direction | Meaning |
|---|---|---|
| `imports` | `from → to` | focus file imports from these files |
| `imported_by` | `from → to` | these files import the focus file (callers) |
| `extends` | `child → parent` | inheritance relationships |

**`imported_by` is the most important table** — it tells you what breaks if you change
this file. Agents miss this entirely with manual reads.

---

## Workflow examples

### Before editing a file

```
→ relic_query("src/payments/processor.py")
  Read: exports, what it imports, who calls it
→ Read the file
→ Edit the file
→ relic_reindex()   ← keep index fresh
```

### Finding where something lives

```
→ relic_search("PaymentProcessor")
  Read: file_matches, symbol_matches
→ relic_query("src/payments/processor.py")
  Read: full context before editing
```

### Before a large refactor

```
→ relic_stats()
  Check: last_updated — is index fresh?
→ relic_reindex() if stale
→ relic_query("src/core/database.py", depth=3)
  Read: wide graph context
```

---

## Troubleshooting

**`Error: no index found. Call relic_reindex first.`**
Index hasn't been built. Call `relic_reindex` or run `relic index` in the terminal.

**`Not found: 'X'. Try relic_search or relic_reindex if recently added.`**
Node not in index. Either the file was added after the last index, or the path is wrong.
Use `relic_search` to find the correct path, or call `relic_reindex` to rebuild.

**`relic_reindex` fails with `FileNotFoundError`**
`relic.yaml` not found. Run `relic init` in the project root first.

**Index is stale after editing files**
Call `relic_reindex` after any file write/delete to keep the graph accurate.
For passive refresh, watch mode is on the roadmap.
