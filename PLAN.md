# Relic — Development Plan

## What's built and merged (main)

| Feature | Branch | Notes |
|---|---|---|
| `relic init` subcommand routing fix | `fix/init-subcommand-bypass` | Typer callback was eating "init" before Click could route it |
| `relic query` absolute path fix | `fix/query-absolute-path` | Index stores relative paths — strip PROJECT_ROOT on lookup |
| `relic mcp` command | `feat/mcp-server` | stdio MCP server exposing `relic_query` native tool |
| Remove graph.md refresh pipeline | `cleanup/remove-refresh-pipeline` | Deleted `generator.py`, `staleness.py`, removed `--refresh`/`--stale`/`--force` flags |
| TOON 38% size reduction | `feat/toon-focus-symbols` | `exports` (focus file symbols), `neighbors` (file paths), `imported_by` (inbound edges), filter edge noise |
| README rewrite | `docs/readme-rewrite` | Cold-read problem framing, current architecture, no stale refresh docs |
| `relic benchmark` command | `feat/benchmark` | Proves token reduction vs manual agent reads, shows hidden callers |
| Remove Claude Code hook injection + MCP tools | `fix/remove-hook-support` | Hooks contradict the non-invasive principle. Replaced with `relic_search`, `relic_reindex`, `relic_stats` MCP tools across all agents. Adds `docs/MCP.md`. |
| Phase 3 — ranked search, disambiguation, imperative rules | `feat/search-and-disambiguation` | Scored search (exact > prefix > substring), subproject filter, `relic search` CLI, symbol disambiguation TOON candidate list, MUST/SHOULD agent instructions with decision tree, project-specific example file injection |
| Dogfood relic on the relic repo | `chore/dogfood-relic` | Project agent configs (CLAUDE.md, .cursorrules, AGENTS.md, copilot-instructions.md) and MCP registrations. Untracks personal `relic.yaml`/`.knowledge/`. Removes stale `_index.md`/`.gitkeep`. |
| Phase 4 — `relic watch` + `relic coverage` | `feat/watch-and-coverage` | Watchdog-backed filesystem watcher with 500 ms debounce and re-entrant pending flag; coverage report classifying files into indexed / no_parser / too_large / symlink with per-subproject breakdown and `--verbose` listing. 25 new tests (137 total). README updates. |
| CLI theme overhaul | `feat/cli-theme` | Nord-inspired palette, `⬢` brand mark, styled output via Rich, `relic.style` module, 10 new tests. |
| Phase 5 — `relic audit` + token budgets | `feat/audit-and-tax` | `relic audit` command measuring relic's own token cost. Trimmed MCP descriptions to ≤ 500 tokens combined. Regression tests for instruction (≤ 800) and MCP (≤ 500) token budgets. Cache-stability tests for `list_tools()`. |
| CI/CD + OSS scaffolding | `chore/ci-and-oss-scaffolding` | GitHub Actions CI (lint + test matrix + smoke), Dependabot, CODEOWNERS, PR/issue templates, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, CHANGELOG.md. Lint baseline (line-length 120, ruff format). Windows path normalization (POSIX slashes in graph). |
| Release workflow | `chore/release-workflow` | Automated GitHub Releases on version tags. Version consistency check (tag ↔ pyproject.toml). Changelog extraction. PyPI placeholder for later. |
| Phase 6 — deeper token savings | `feat/phase6-signatures-and-tests` | Function signatures in TOON, batch query, test file mapping, symbol-scoped query (Class.method), `uses` edges for named imports, `relic diff`, auto-index test directories. |
| PyPI publishing | `chore/pypi-publish` | Package renamed to `relic-graph`. Trusted Publishers workflow. `--update` upgrades from PyPI. |
| Dynamic version | `fix/dynamic-version` | `__version__` reads from `importlib.metadata` instead of hardcoded string. |
| v0.3.0 — Zero-config indexing | `feat/v0.3.0-zero-config` | `relic init` scans entire project tree. `relic.yaml` optional. Removed `discovery.py`. |
| Enhanced `relic index` | `feat/enhance-relic-index` | `.relicignore` support, skip stats, delta summary. |
| Zero-config all commands | `fix/yaml-optional-everywhere` | `relic diff`, `relic watch`, `relic coverage`, `relic --list` no longer require `relic.yaml`. |
| Phase 8 — Semantic Index (incl. 7d tree-sitter) | `feat/phase8-semantic-index` | `intent` (docstrings/leading comments) on every symbol for Python/TS/Go/Rust/Java. `decorators` (literal args only, max 5) on Python/TS/Go/Rust/Java symbols. String literal index (≥8 chars, max 20/symbol) + quoted `relic_search '"..."'`. `cost{response_tokens,focus_file_tokens}` header on every MCP response. Tiered MUST/SHOULD/SKIP rules in RELIC_INSTRUCTIONS. `relic audit --usage`. 50 new tests (311 total). |

---

## Phase 7 — Token Efficiency + Graph Richness

Goal: reduce per-query token waste and add `calls` edges so agents read fewer files.
Prioritized by real agent usage — every feature must answer: "Does this save the agent
a file read?"

### 7a — Reduce noise

**7a.1: Filter test symbols from `neighbor_symbols`**

When querying `indexer.py` at depth 2, 120+ of 174 neighbor symbols are test functions.
These are rarely useful for implementation tasks and waste ~2,000 tokens per query.

- Add `exclude_tests` parameter to `relic_query` (default `true`)
- Filter out symbols from files where `_is_test_file()` returns true from `neighbor_symbols`
- Keep `tested_by` edges (compact and useful)
- Keep test files in `neighbors` list (file names are cheap)
- Expected savings: 40–60% reduction in depth-2 query tokens

**7a.2: `relic_diff` MCP tool (5th tool)**

Expose `compute_diff` as an MCP tool so agents can check staleness without a CLI.

- New tool: `relic_diff` — returns TOON showing new/deleted/changed files and symbols
- Agents call this after merges or when they suspect the index is stale
- Eliminates speculative `relic_reindex` calls (each reindex is ~2s wall time + re-query)

---

### 7b — Calls edges

**7b.1: `calls` edges via Python AST**

Walk `ast.Call` nodes in function bodies, resolve `Name` and `Attribute` to known symbol
nodes in the graph. Only deterministic resolutions (callee exists in graph).

- Add `calls` section to TOON output: `calls[N]{caller,callee}`
- Enhance `callers` section to include call-site granularity (which function calls which)
- Saves 1–3 file reads per task — agent knows `diff.py::_symbol_fingerprint` calls
  `_analyse_python` without reading `diff.py`

**7b.2: `calls` edges via regex for TS/JS**

Same concept using existing regex infrastructure. Match `identifier(` patterns inside
function bodies against known exported symbols.

- Lower accuracy than AST but useful for obvious cases
- Same TOON output format as Python calls

---

### 7c — Smart depth

**7c.1: Cap `neighbor_symbols` with importance ranking**

Instead of dumping all symbols at depth N, cap to a token budget (e.g., top 30 by
connectivity score). Most-connected symbols first.

- Add `max_neighbor_symbols` parameter to `relic_query` (default 30)
- Rank by: number of callers + number of imports (proxy for importance)
- Include `... and N more` indicator so agent knows it can go deeper
- Bounds worst-case output — prevents 174-symbol dumps

---

### 7d — tree-sitter integration

**7d.1: tree-sitter for multi-language support**

Currently `relic/indexer.py` uses Python `ast` for Python and regex for TS/JS. To scale
to 20+ languages, add tree-sitter as an optional dependency.

- Add `tree-sitter` + grammar packages as optional deps in `pyproject.toml`
- Create `relic/parsers/` package with a base `Parser` protocol
- Keep existing ast/regex parsers as zero-dep defaults
- First batch: Go, Rust, Java (highest demand in AI coding)

---

### Release plan

| Release | Contains | Theme |
|---------|----------|-------|
| v0.4.0 | All of Phase 7 (7a–7d) | Token efficiency + graph richness |
| v0.5.0 | Phase 7.5 (workflow / save passes) | Fewer round-trips per task |
| v0.6.0 | Phase 8 (semantic index) | Agent skips file reads |
| v0.7.0+ | Phase 9 (intelligence + remaining languages) | Breadth + advanced graph features |

---

## Phase 7.5 — Save the passes (agent workflow)

Goal: collapse the typical 3–4 MCP round-trip pattern (`stats` → `search` → `query` → open
file) into a **single `relic_query` call**. Driven by real agent feedback:

- Agents ran `relic_stats` on every task to check freshness — pure overhead.
- Agents that called `relic_reindex` on a large monorepo timed out every time
  (full rebuild blocks the MCP request beyond the client's 30–60 s budget).
- `relic_search` returned just names + paths, forcing an immediate `relic_query`
  follow-up to get any usable context.
- `relic_query` answered "what connects to what" but not "where does X happen",
  so agents still had to grep + read files for logic-based tasks.

Phase 7.5 fixes the surface without adding new MCP tools — every change either removes
a tool, removes a round-trip, or enriches an existing response.

### 7.5a — Remove `relic_stats` from the agent loop

`relic_stats` answers one question agents actually care about: *is the index fresh?*
That belongs in **every** response, not in a dedicated tool call.

- **Embed an `index{...}` header on every MCP response** (`relic_query`, `relic_search`,
  `relic_reindex`, `relic_diff`). Carries: `age_s`, `stale` (bool — derived from on-disk
  mtimes vs. stored index mtime), `files_changed` count.
- **Deprecate `relic_stats` as an MCP tool.** Keep `relic stats` CLI for humans.
- Agent instructions updated: "Never call stats; freshness is in every response."
- Net effect: ~1 wasted call per task eliminated.

### 7.5b — Remove `relic watch`

`relic watch` exists to keep the index fresh in the background. Once the agent itself
can reindex cheaply (7.5d), the watcher is redundant — and it adds a separate process
the user has to remember to start.

- **Delete `relic watch` command and `relic/watcher.py`.**
- Remove `watchdog` from `pyproject.toml` dependencies.
- Remove related tests (`tests/test_watcher.py`).
- Update README / agent rules to drop watcher references.
- The agent owns freshness: it sees `stale=true` in the header → it calls
  `relic_reindex` → incremental update finishes in < 1 s → it continues.

### 7.5c — Enrich `relic_search` so most tasks need only one call

Today `relic_search` returns name + path. Agents almost always follow up with
`relic_query` to get the signature and neighbors. Fold that context into the search hit.

- Each search hit gains: `signature`, one-line `docstring`, `neighbor_count`,
  `defining_file` (if symbol).
- Cost: ~30 tokens per hit, capped by `limit`.
- Net effect: the `search → query` chain collapses to one call for the common case.

### 7.5d — Incremental reindex (the timeout fix)

Full `build_graph` is the only path today. It re-parses every file every time. On a
large monorepo this blows past MCP client timeouts. No background jobs, no daemons —
just **don't do work that doesn't need doing**.

- **Stamp every file node with its `mtime` at index time.** Stored in a sidecar
  `.knowledge/mtimes.json` (cheap to read without unpickling the full graph).
- **`relic_reindex` becomes incremental by default**:
  - Walk the tree, collect current mtimes (one stat per file — cheap).
  - For each file: if `current_mtime <= stored_mtime`, **skip parsing entirely**.
  - Reparse only changed/new files. Drop nodes for deleted files.
  - Re-resolve cross-file edges (`uses`, `calls`, `extends`) for: touched files +
    files that import them. Leave the rest of the graph alone.
- **`stale=true` in the response header is computed the same way** — a single mtime
  sweep tells both the agent (via header) and the reindexer (when invoked) what's dirty.
- **Full rebuild stays available** as `relic index --full` (CLI) and as a
  `mode="full"` arg on `relic_reindex` for the rare case the schema bumps or the index
  gets corrupted. First-ever index on a fresh repo also goes through this path.
- Concurrency: write to a temp file then atomic rename, so a half-finished reindex
  can't corrupt the index for a parallel `relic_query`.
- Net effect: a typical reindex on a 50k-file monorepo with a handful of changed files
  drops from tens of seconds to sub-second. No MCP timeout. No background jobs needed.

### Round-trip ledger (target state)

| Wasted call today | What kills it | Phase |
|---|---|---|
| `relic_stats` | Freshness header on every response | 7.5a |
| Background `relic watch` process | Incremental reindex makes it pointless | 7.5b |
| `relic_search` → `relic_query` | Search returns sig + docstring + neighbor count | 7.5c |
| `relic_reindex` timeout / retry storm | Mtime-based incremental reindex | 7.5d |
| `relic_query` → open file → grep | Semantic index (Phase 8) | 8 |

Steady-state agent workflow: **one `relic_query` per question** in the common case.

(`7.5e` — `content=` parameter and decorator/docstring indexing — has been folded
into Phase 8, where it belongs alongside the broader semantic-index work.)

---

## Phase 8 — Semantic Index

Goal: collapse the three remaining "logic discovery" failure modes into single relic
calls, so the agent stops grepping and stops reading whole files just to figure out
*what code does*. Driven by the same agent feedback as Phase 7.5:

- `relic_query` tells me **what connects to what**, but not **what it does**. So I
  read the file anyway just to see whether `process_payment` validates, charges, or
  also sends an email. One docstring would have answered it.
- "Where is the login route defined?" still requires `grep "@app.route"` + opening
  matches to find the right path. The decorator + literal arg is sitting right there
  in the AST — relic just doesn't index it.
- "Where does this error message come from?" forces a grep across the repo, then a
  read to figure out the enclosing function. String literals scoped to their symbol
  would answer this in one call.

Phase 8 stays inside the existing constraints: no LLM, no new MCP tools, all signals
extracted by static analysis, token budgets enforced. Old Phase 8 (Louvain, `relic
explain`/`path`, more languages, `relic viz`) moves to **Phase 9**.

### 8a — Docstrings / leading comments per symbol (intent surface)

Single biggest win-per-byte. *Every* query benefits, on every symbol.

**What gets indexed**

- Python: `ast.get_docstring(node)` for class / function / async function. **First
  line only.**
- TS/JS: regex for the `/** … */` JSDoc block or contiguous `//` lines directly
  preceding a `function` / `class` / `const` declaration. First line of stripped
  comment text.
- Tree-sitter languages (Go, Rust, Java): same first-line rule via the parser's
  comment node (`//`, `///`, `/* */`, doc-comment grammar nodes).

**Where it lives**

- New `intent` attribute on every symbol node. String, max **80 chars**, `…`-truncated.
  Empty string when there is no docstring/leading comment.

**Where it surfaces**

- `relic_query`: `exports[]{name,type,line,signature,intent}` — column added to the
  existing exports table. **Default on.**
- `relic_search`: `symbol_matches[]{name,type,file,signature,callers,intent}` — adds
  `intent` to the row introduced in 7.5c.
- Off via `include_intent=false` on `relic_query` for token-sensitive callers.

**Token budget**

- ≤ 80 chars × ~30 symbols default cap → ~600 chars per query → ~150 added tokens.
  < 5% of a typical query response. Net win as soon as it kills one file read.

**Tests**

- Python `def f(): """Compute X."""` → `intent == "Compute X."`.
- Python no docstring → `intent == ""`.
- TS JSDoc block → first stripped line surfaces.
- Truncation at 80 chars produces a trailing `…`.
- `include_intent=false` removes the column from `exports`.
- Token-budget regression tests still pass.

### 8b — Decorator / annotation literal index

Cheap, targeted, kills `grep "@app.route"`-style searches across all web/CLI/ORM/
job-queue codebases.

**What gets indexed**

- Python: every `@decorator` on a function/class. Capture `name` (dotted) and
  *literal* args only (string / number / bool / `None`). **Drop non-literal args**
  silently — we never want to index `@retry(BACKOFF_MS)` because the value isn't
  knowable statically.
- TS/JS: regex for `@decorator(...)` immediately preceding `class`/`function`/method.
  Same literal-only rule.
- Tree-sitter languages (Java annotations, Rust attributes, Go directive comments
  where applicable): walk annotation nodes, extract literal args.

**Where it lives**

- New `decorators` attribute on symbol nodes: list of `{name, args}` dicts. Hard cap
  of **5 entries per symbol** (most-recently-applied first when truncated).

**Where it surfaces**

- `relic_query`: new optional TOON section `decorators[N]{symbol,decorator,args}` —
  emitted **only when** at least one focus-file symbol has decorators. Zero cost on
  files that use none.
- `relic_search`: search query is matched against decorator names AND literal arg
  values. `relic_search "/login"` finds `@app.route("/login")`. The hit row gains a
  short `via` annotation indicating the match came from a decorator.

**Token budget**

- Most symbols have no decorators. Files with heavy decorator usage (Flask, Typer,
  Pydantic) gain ~5–15 tokens per decorated symbol. Negligible vs. file reads
  killed.

**Tests**

- `@app.route("/login")` → `{"name":"app.route","args":["/login"]}`.
- `@cached` → `{"name":"cached","args":[]}`.
- `@retry(BACKOFF_MS)` → `{"name":"retry","args":[]}` (non-literal silently dropped).
- TS `@Component({selector: 'app-foo'})` → name + literal `selector` value captured.
- `relic_search "/login"` returns the decorated symbol with `via: @app.route`.
- Per-symbol cap of 5 enforced.

### 8c — Symbol-scoped string literal index

Targets the "where does this error message come from?" failure mode.

**What gets indexed**

- All string literals **≥ 8 characters** inside function/method/class bodies. Stored
  as `{value, line, symbol_id}` per literal.
- Hard caps: max **20 literals per symbol** (longest first when truncated); never
  store > **200 chars** per literal value (truncate with `…`).
- **Skip** docstring strings — they're already captured by 8a.
- For f-strings / template literals, capture only the **constant prefix** (everything
  before the first interpolation). Avoids storing partial junk.

**Where it lives**

- Per-symbol `literals` attribute on symbol nodes (not surfaced in `relic_query`
  output by default — too noisy).
- Aggregate inverted index `string_literals: {value → [symbol_ids]}` stored on the
  graph, used by quoted search.

**Where it surfaces**

- `relic_search` recognizes a **quoted** query as a literal search:
  `relic_search '"rate limit exceeded"'` → returns matching symbols with file +
  line + signature + a 30-char snippet around the match.
- TOON section: `literal_matches[N]{value,symbol,file,line}`. Bounded by `limit`
  (default 20).
- Unquoted searches keep behaving as today (name-based ranking).

**Token budget**

- Aggregate index lives in the pickle, **not in any response**.
- Per-search response is bounded by `limit`. ~25 tokens per hit × 20 = ~500 tokens
  worst case.

**Tests**

- `raise ValueError("rate limit exceeded")` → indexed under enclosing function.
- Quoted search returns enclosing symbol + line.
- Unquoted search behaves as in 7.5c.
- Literals < 8 chars not indexed.
- Literals > 200 chars truncated.
- f-string prefix captured; interpolation parts dropped.
- Per-symbol cap of 20 enforced.

### 8d — Productivity instrumentation

So we can prove (or disprove) that 8a–8c actually help, instead of guessing.

**Cost hints in every MCP response**

Extend the freshness header from 7.5a into a two-line preface:

```
index{age_s,stale,files_changed}: 0,false,0
cost{response_tokens,focus_file_tokens}: 412,1830
```

`focus_file_tokens` is a rough estimate from the on-disk size of the focus file
(bytes ÷ 4). Lets the agent compare "what relic gave me" against "what reading the
file would cost", so it can throttle itself honestly. Adds ~10 tokens per response.

**Tiered rules in `RELIC_INSTRUCTIONS`**

Replace the blanket MUST with a context-aware one:

```
- MUST call `relic_query <path>` before edits when:
    (a) the file has > 5 callers (high blast radius), OR
    (b) the file is > 1500 tokens (cheaper than reading), OR
    (c) you have not loaded the file in this session and the task is non-trivial.
- SHOULD call `relic_query <symbol>` when introducing a new import or call site.
- SHOULD skip `relic_query` for known small files where you'd read the whole file
  anyway.
```

This explicitly removes the "spend 300 tokens to confirm a 200-token file" anti-
pattern the user flagged.

**`relic audit --usage`**

New flag on the existing audit command. Reads an in-memory MCP call counter (server
tracks `query_count`, `search_count`, `responses_under_200_tokens`, average size,
distribution of stale headers) and reports a per-session summary. We can't observe
post-relic file reads in an agent-agnostic way, so we measure what we *can* and
explicitly flag the rest as unknown.

**Token budget**

- Cost line: ~10 tokens per response.
- Tiered rules might *shrink* `RELIC_INSTRUCTIONS` net.
- `--usage` is CLI-only; zero MCP cost.

### Order of work inside Phase 8

1. **8d first** (instrumentation). Cheap. Gives us measurement before we add signal.
2. **8a** (docstrings). Highest value-per-byte.
3. **8b** (decorators). Cheap, additive.
4. **8c** (string literals). Largest payoff for "where does this come from" tasks.

### What we are explicitly *not* doing in Phase 8

- **Effects fingerprint** ("does this hit the network/DB/filesystem?"). Doing it
  shallowly is misleading; doing it deeply requires whole-graph call closure and is
  easy to ship wrong. Defer to Phase 9.
- **Raises index** as a standalone feature. If it falls out cheaply during 8a's AST
  walk, fine — otherwise Phase 9.
- **Type-flow analysis.** Out of scope.
- **Anything requiring runtime data.** Out of scope.

---

## Phase 9 — Coding-Task Intelligence

Goal: make relic answer the three questions every agent asks before touching code —
"what breaks if I change this?", "how does A connect to B?", "where is this endpoint
defined?" — without reading a single file.

Relic's differentiator vs. graphify: deterministic edges + symbol-level precision =
trustworthy blast-radius analysis. Graphify explores concepts; relic does pre-flight
for coding tasks.

Explicitly cut from this phase (covered by linters, or computable from existing TOON):
inline hints, cycle detection, unused symbol detection, change safety score.

### 9a — Impact radius (`relic impact`)

The single biggest agent pain point not covered by existing tools or graphify.

**Problem it solves**

`relic_query validate_card` shows direct callers (1 file). But `checkout_handler` is
called by `billing_worker` which is called by `stripe_webhook`. If I change
`validate_card`'s signature, 3 files break — relic only shows me 1 today.

**What it does**

`relic impact <symbol|file>` walks the graph transitively via `imported_by` and `uses`
edges (NetworkX `nx.descendants` on reversed graph). Returns:

```
impact: validate_card

direct_callers[1]{file,symbol,line}:
  src/api/views.py,checkout_handler,112

transitive_callers[2]{file,symbol,hops}:
  src/workers/billing.py,process_batch,2
  src/api/webhooks.py,stripe_callback,3

tests_affected[1]{file}:
  tests/test_payments.py

total_files_affected: 3
```

**Also exposed via MCP** as `relic_query "impact:validate_card"` — no new tool, agent
uses existing `relic_query` with an `impact:` prefix target.

**Token budget:** bounded by `limit` param (default 20 per section). Worst-case ~300
tokens for a high-connectivity symbol.

### 9b — Path query (`relic path`)

**Problem it solves**

Agent planning a refactor needs to know the dependency chain between two components.
"How does the webhook handler end up calling the database?" Today: grep + read files.

**What it does**

`relic path <A> <B>` returns shortest dependency path via `nx.shortest_path`. Works
for both files and symbols. Also available as `relic_query "A->B"` for MCP callers.

```
path: src/api/webhooks.py → src/core/database.py
hops[4]{from,to,edge_type,evidence}:
  src/api/webhooks.py,src/payments/processor.py,imports,ast
  src/payments/processor.py,process_payment,defines,ast
  process_payment,DatabaseSession,calls,ast
  src/payments/processor.py,src/core/database.py,imports,ast
```

Includes `evidence` on each hop (see 9d) so agent knows which hops are exact vs.
approximated.

### 9c — Community detection (`relic communities`)

**Problem it solves**

Subproject labels in `relic.yaml` reflect directory structure, not actual coupling.
Two files in different directories that heavily import each other belong to the same
functional module — community detection reveals this without manual config.

**What it does**

`networkx.community.louvain_communities` on the import graph. Assigns `community`
integer to every file node. No new dependency — NetworkX already ships Louvain.

Surfaced in:
- `relic_query`: `community: 3 (12 files)` line in focus file header
- `relic communities`: table of community → member files, ranked by size
- `relic impact`: flags when transitive callers span multiple communities (higher risk)
- `relic_search`: `-c <id>` filter to scope results to one community

Agent value: "this change is community-isolated → low cascade risk" vs "cross-community
→ check transitive callers carefully."

### 9d — Edge evidence labels

**Problem it solves**

Relic's TS/JS regex call detection is approximate — it matches `identifier(` patterns
but can't resolve overloaded names. Agents planning refactors need to know which edges
to trust unconditionally vs. treat as likely-but-unverified.

**What gets tagged**

Every edge gains an `evidence` attribute at index time:
- `ast` — Python AST (exact)
- `treesitter` — tree-sitter parse for Go/Rust/Java (exact)
- `regex` — TS/JS/other regex match (approximate)
- `convention` — test mapping by filename convention

**Where it surfaces**

- `relic_query`: `imports[N]{from,to,evidence}`, `calls[N]{caller,callee,evidence}`
- `relic path`: per-hop evidence (see 9b example above)
- `relic impact`: transitive callers via `regex` edges flagged as `~approximate`

Not INFERRED or AMBIGUOUS edges — no new guessing, just labelling the analysis method.

### 9e — Static doc indexing

**Problem it solves**

"Where is the `/login` endpoint defined?" still requires grep even with Phase 8
decorator search — if the framework uses a router object (`router.add_route(...)`) the
decorator isn't on the function. OpenAPI specs and config files are authoritative
sources relic currently ignores.

**Supported formats (static parsing only, zero LLM)**

| Format | Extraction | Indexed as |
|---|---|---|
| `openapi.yaml` / `swagger.yaml` | HTTP paths, operationId, summary | Symbol nodes `stype=endpoint`; summary as `intent` |
| `*.json` (JSON Schema) | `$id`, top-level property names, `description` | Symbol nodes `stype=schema` |
| `pyproject.toml` / `package.json` | Package name, scripts, deps | File node; script names as symbols |
| `*.md` / `*.rst` | H1–H3 headings, internal links | File node; first heading as `intent` |

**Where it surfaces**

- `relic_query openapi.yaml` → exports table with endpoint symbols
- `relic_search "/login"` → matches endpoint symbol, intent = `POST /login — Authenticate user`
- `relic_search "migrate"` → matches `package.json` script `db:migrate`

PDF, image, video — permanently out of scope (require LLM).

### 9f — Languages batch 2

C#, Kotlin, Scala, PHP, Swift — all via tree-sitter (optional dep already ships).

### Release plan

| Release | Contains |
|---------|----------|
| v0.6.0 | All of Phase 9 (9a–9f) |

---

## Phase 10 — Breadth + Visualization

Features from the original Phase 9 roadmap that need Phase 9 foundations first, or
are lower priority vs. agent utility.

### 10a — Effects fingerprint

Transitive call-graph closure to answer "does X hit the DB/network/filesystem?".
Deferred from Phase 8 and Phase 9 — doing it shallowly (direct calls only) is
misleading; needs full call closure from Phase 9 to be correct.

### 10b — Languages batch 3

Zig, Elixir, Objective-C, Julia, SQL, Fortran.

### 10c — `relic viz`

Interactive HTML graph (D3.js or vis.js, single file, zero Python deps at runtime).
Nodes colored by community, filterable by subproject. `relic viz` opens in default
browser. Lower agent utility than 9a–9c; high human utility for onboarding.

---

## What we are NOT doing (and why)

- **PDF / image / video extraction** — requires LLM calls, contradicts zero-cost philosophy
- **INFERRED or AMBIGUOUS edges** — relic's graph is deterministic. `evidence` (9d) labels analysis method, not guesses
- **Semantic similarity edges** — needs embeddings/LLM, not our lane
- **Natural language graph queries** — the agent's job; relic provides structured data
- **URL ingestion (papers, tweets)** — non-code, LLM-dependent
- **Wiki / Obsidian export** — TOON is more token-efficient for agents
- **Global cross-repo graphs** — single-repo excellence first
- **Inline agent hints in TOON** — computable from existing output; adds tokens without new data
- **`relic cycles`** — linters (ruff, mypy) catch circular imports; not relic's job
- **`relic unused`** — linters cover dead code detection better
- **Change safety score** — agents compute from `imported_by` count + `tested_by` already in TOON

---

## Key constraints (do not break)

- **No hook injection.** Relic must not write into another tool's hook surface (PreToolUse, PostToolUse, etc). Hooks are invasive, vendor-specific, and contradict the agent-agnostic positioning. All integration is via MCP tools or explicit CLI calls.
- **Agent-agnostic by default.** Every feature must work the same on Claude Code, Cursor, Copilot, Codex. No more Claude-only paths.
- **Token efficiency is a first-class constraint.** Every new output format must be benchmarkable against the manual-read baseline (`relic benchmark`).
- **No network egress.** No telemetry, no API calls. The only exception is `relic --update` hitting PyPI for a self-upgrade.
- **Static analysis only.** No LLM in the indexing or query path. Speed and determinism are the product.
