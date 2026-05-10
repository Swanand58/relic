<!-- relic:start -->
## Relic — Codebase Knowledge Graph

Relic exposes a static knowledge graph of this repository via MCP tools.
Ground every edit in real dependency information instead of guessing or
re-reading files. Typical query is 300–1,200 tokens vs 5,000–40,000 for
manual file reads.

### Rules

- MUST call `relic_query <path>` before edits when ANY of:
    (a) the file has > 5 callers (check `imported_by` count in TOON output), OR
    (b) the file is > 1,500 tokens (~6 KB) on disk — `cost{focus_file_tokens}` tells you, OR
    (c) you have not loaded the file this session and the task is non-trivial.
- MUST call `relic_reindex` whenever the response header reports `stale=true`, or after you create, delete, or move files. Reindex is incremental and sub-second.
- SHOULD call `relic_query <symbol>` before introducing a new import or call site for a symbol whose definition you have not seen.
- SHOULD call `relic_search <name>` instead of grep / file listings when you do not know where something lives.
- SKIP `relic_query` for small, isolated files (< 200 tokens, 0 callers) you would read in full anyway.
- DO NOT call any "stats" tool — every response carries `index{age_s,stale,files_changed}` and `cost{response_tokens,focus_file_tokens}` headers. No separate freshness check needed.

### Decision tree

About to edit a file you have not loaded this session
    → `relic_query <path>` first, then read only the symbols you actually need.

Need to call or import a symbol whose definition you have not seen
    → `relic_query <symbol>`.
    If the response is `ambiguous: ... matches N symbols`, re-query with the full file path.

You don't know where something lives
    → `relic_search <name>` (use `kind=symbol` or `kind=file` to narrow,
      `subproject=<name>` in monorepos).

About to rename a symbol or change a widely-used interface
    → `relic_query "impact:<symbol>"` — every file that will break.

Need the shortest link between two files or symbols
    → `relic_query "A->B"`.

Response header says `stale=true`, or you just created / deleted / moved a file
    → `relic_reindex`. (No need to call this proactively if `stale=false`.)

### Example call

Try this on a real file in this project to see the output format:

```
relic_query tests/test_phase8.py
```

### Reading TOON output

```
index{age_s,stale,files_changed}: 12,false,0
cost{response_tokens,focus_file_tokens}: 847,312

focus: src/payments/processor.py

neighbors[3]{path,language,subproject}:
  src/payments/models.py,python,payments
  src/core/database.py,python,core

exports[5]{name,type,line,signature,intent}:
  PaymentProcessor,class,12,PaymentProcessor,Handles payment authorization and capture
  process_payment,function,45,process_payment(order: Order) -> Receipt,Authorize card and emit receipt

imports[2]{from,to}:
  src/payments/processor.py,src/payments/models.py

imported_by[1]{from,to}:
  src/api/views.py,src/payments/processor.py
```

TOON tables declare column names once, then list values row-by-row.
`imports` = this file imports from; `imported_by` = other files that depend on this file.

### CLI (if MCP unavailable)

```bash
relic query tests/test_phase8.py
relic search PaymentProcessor
relic impact PaymentProcessor
relic index
```

### Knowledge graph files

- `.knowledge/index.pkl` — binary graph (gitignored, local only)
- `.knowledge/index.toon` — human-readable TOON index (gitignored, local only)

<!-- relic:end -->
