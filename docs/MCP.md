# Relic MCP Server

Relic exposes a Model Context Protocol (MCP) server over stdio. Any MCP-compatible
agent can call the four relic tools natively — no shell commands, no prompting required.

```bash
relic mcp    # start the server (stdio transport)
```

Every response begins with a one-line `index{age_s,stale,files_changed}` header so
the agent always knows whether the graph is fresh — there is no separate "stats"
tool to call. See [Freshness header](#freshness-header) below.

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

Any MCP client (LangGraph, custom stdio client) can connect:

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
| `target` | string | yes | — | File path, symbol name, `Class.method`, or space-separated batch |
| `depth` | integer | no | `2` | BFS hops from target node. Use `1` for barrel/index files. |
| `exclude_tests` | boolean | no | `true` | Filter test-file symbols from `neighbor_symbols` |
| `max_neighbor_symbols` | integer | no | `30` | Cap neighbor symbols (0 = unlimited), ranked by connectivity |

**Output:** TOON block — focus file, neighboring files, exported symbols, import/caller/calls edges.

```
focus: src/payments/processor.py

neighbors[3]{path,language,subproject}:
  src/payments/models.py,python,payments
  src/payments/exceptions.py,python,payments
  src/core/database.py,python,core

exports[4]{name,type,line,signature}:
  PaymentProcessor,class,12,PaymentProcessor
  process_payment,function,45,process_payment(order: Order) -> Receipt
  validate_card,function,78,validate_card(card: Card) -> bool
  RETRY_LIMIT,variable,8,RETRY_LIMIT

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

calls[2]{caller,callee}:
  process_payment,validate_card
  process_payment,DatabaseSession

called_by[1]{caller,callee}:
  handle_checkout,process_payment
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
search: processor

file_matches[2]{path,language,exports,imported_by}:
  src/payments/processor.py,python,4,3
  src/core/preprocessor.ts,typescript,2,1

symbol_matches[3]{name,type,file,signature,callers}:
  PaymentProcessor,class,src/payments/processor.py,PaymentProcessor,5
  DataProcessor,class,src/core/preprocessor.ts,DataProcessor(BaseProcessor),2
  process_payment,function,src/payments/processor.py,process_payment(order: Order) -> Receipt,7
```

Each hit carries enough context to skip the follow-up `relic_query` call in
the common case: file results show `exports` (symbol count) and
`imported_by` (in-edges); symbol results carry the signature and `callers`
count (inbound `uses` + `calls` edges).  Signatures over 80 characters are
truncated with `…`; query the symbol directly for the full text.

**Tips:**
- Search by partial name: `"proc"` matches `processor`, `preprocess`, `ProducerConfig`
- Search by directory: `"payments/"` returns only files in the payments subproject
- Only follow up with `relic_query` when you need neighbors / full graph context

---

### `relic_reindex`

Incrementally update the knowledge graph from source.

**When to call:** when the response header reports `stale=true`, or after you
create, delete, or move source files.

**Parameters:** none.

**Output:**

```
index{age_s,stale,files_changed}: 0,false,0
reindex: incremental in 0.06s
changed: +1 new, ~2 modified, -0 deleted, =79 unchanged
files: 82
symbols: 598
edges: 1,180
```

**Notes:**
- Reparses only files whose mtime changed since the last index. Sub-second on
  large repos in steady state.
- If no index exists yet, the call returns an error asking the user to run
  `relic index` once. The MCP server never does a full cold-start build —
  that path stays in the CLI to avoid blowing past MCP request timeouts.

---

### `relic_diff`

Check what changed since the last index without a full reindex.

**When to call:** after merges or big edits to decide whether `relic_reindex` is needed.

**Parameters:** none.

**Output:**

```
diff_summary:
  new_files: 2
  deleted_files: 0
  changed_files: 3

new_files[2]{path}:
  src/payments/refunds.py
  src/payments/disputes.py

changed_symbols[4]{file,name,status}:
  src/payments/processor.py,process_refund,added
  src/payments/processor.py,validate_card,changed
  src/core/database.py,execute_query,changed
  src/core/database.py,close_pool,added
```

**Notes:**
- Lightweight — compares on-disk source against the stored graph without rebuilding
- If the diff shows changes, follow up with `relic_reindex` to update the graph

---

## Freshness header

Every MCP response is prefixed with one line:

```
index{age_s,stale,files_changed}: 42,false,0
```

| Field | Meaning |
|---|---|
| `age_s` | Seconds since `index.pkl` was last written |
| `stale` | `true` iff at least one source file has changed, been added, or deleted relative to the saved index |
| `files_changed` | Total count of differing files (added + modified + deleted) |

If no index exists yet, the header collapses to `index{indexed,stale}: false,true`
and the body explains how to bootstrap it.

The header is computed via a cheap `stat()` sweep against `.knowledge/mtimes.json`
and cached for 2 seconds, so back-to-back calls in the same agent turn pay the
cost only once. Agents should treat `stale=true` as the sole signal to call
`relic_reindex` — there is no separate "stats" tool, and proactive reindexing
when `stale=false` is wasted work.

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
| `calls` | `caller → callee` | outbound function calls from focus symbols |
| `called_by` | `caller → callee` | inbound function calls into focus symbols |

**`imported_by` is the most important table** — it tells you what breaks if you change
this file. `calls` and `called_by` go deeper — showing which *functions* call which,
so you don't need to read the caller file to understand the dependency.

---

## Workflow examples

### Before editing a file

```
→ relic_query("src/payments/processor.py")
  Read: exports, what it imports, who calls it.
  Header: stale=false → graph is current.
→ Read the file
→ Edit the file
```

Don't call `relic_reindex` proactively. The next response's header will
report `stale=true` if your edits drifted the graph; only then reindex.

### Finding where something lives

```
→ relic_search("PaymentProcessor")
  Hit carries signature + caller count + defining file.
  In most cases that's enough to keep going — no follow-up needed.
→ relic_query("src/payments/processor.py")   # only if you need neighbours
```

### Before a large refactor

```
→ relic_query("src/core/database.py", depth=3)
  Header reports stale=false → graph is current.
→ Read: wide graph context, plan the changes.
```

If the header reports `stale=true`, call `relic_reindex` once first — the
incremental update is sub-second.

---

## Troubleshooting

**`Error: no index found. Ask the user to run relic index ...`**
The graph hasn't been built yet. The MCP server intentionally refuses to do a
full cold-start build (it would blow past client timeouts on large repos). Have
the user run `relic index` once in the project root. Subsequent `relic_reindex`
calls from the agent are incremental and fast.

**`Not found: 'X'. Try relic_search or relic_reindex if recently added.`**
Node not in index. Either the file was added after the last index, or the path is wrong.
Use `relic_search` to find the correct path, or call `relic_reindex` to rebuild.

**Index is stale after editing files**
The freshness header on the next response will say `stale=true`. Call
`relic_reindex` — incremental, sub-second.
