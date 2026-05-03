<!-- relic:start -->
## Relic — Codebase Knowledge Graph

Relic exposes a static knowledge graph of this repository via MCP tools.
Ground every edit in real dependency information instead of guessing or
re-reading files. Typical query is 300–1,200 tokens vs 5,000–40,000 for
manual file reads.

### Rules

- MUST call `relic_query <path>` before any Edit, Write, or non-trivial Read on a file you have not already loaded in this session.
- MUST call `relic_query <symbol>` before introducing a new import or call site for a symbol whose definition you have not seen.
- MUST call `relic_reindex` after creating, deleting, or moving source files. Subsequent queries against a stale index will return wrong context.
- SHOULD call `relic_search <name>` instead of grep / file listings when you do not know where a class, function, or file lives.
- SHOULD call `relic_stats` before a large refactor to confirm the index is fresh; reindex if `last_updated` looks old.

### Decision tree

About to edit a file you have not loaded this session
    → `relic_query <path>` first, then read only the symbols you actually need.

Need to call or import a symbol whose definition you have not seen
    → `relic_query <symbol>`.
    If the response is `ambiguous: ... matches N symbols`, re-query with the full file path.

You don't know where something lives
    → `relic_search <name>` (use `kind=symbol` or `kind=file` to narrow,
      `subproject=<name>` in monorepos).

You just created, deleted, or moved a source file
    → `relic_reindex`.

You suspect the index is stale
    → `relic_stats`, then `relic_reindex` if `last_updated` is old.

### Example call

Try this on a real file in this project to see the output format:

```
relic_query relic/cli.py
```

### Reading TOON output

```
focus: src/payments/processor.py

neighbors[3]{path,language,subproject}:
  src/payments/models.py,python,payments
  src/core/database.py,python,core

exports[5]{name,type,line}:
  PaymentProcessor,class,12
  process_payment,function,45

imports[2]{from,to}:
  src/payments/processor.py,src/payments/models.py

imported_by[1]{from,to}:
  src/api/views.py,src/payments/processor.py
```

TOON tables declare column names once, then list values row-by-row.
`imports` = this file imports from; `imported_by` = other files that depend on this file.

### CLI (if MCP unavailable)

```bash
relic query relic/cli.py
relic search PaymentProcessor
relic index
```

### Knowledge graph files

- `.knowledge/index.pkl` — binary graph (gitignored, local only)
- `.knowledge/index.toon` — human-readable TOON index (gitignored, local only)

<!-- relic:end -->
