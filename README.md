# relic

CLI tool that stores and loads codebase knowledge graphs per subproject — so your AI coding agent starts every session with full context, not a cold read.

Works with any AI coding agent: Claude Code, GitHub Copilot, OpenAI Codex, Cursor, etc.

---

## Install

Requires [uv](https://docs.astral.sh/uv/).

### From GitHub (any machine, no PyPI needed)

```bash
uv tool install git+https://github.com/Swanand58/relic
```

If `relic` is not found after install, run:

```bash
uv tool update-shell
```

Then open a new terminal tab.

### Upgrade to latest (main branch)

```bash
relic --update
```

### Local dev install (editable — picks up changes live)

```bash
# from inside the cloned repo
uv tool install --editable .
```

---

## Setup

All setup commands run **in your own terminal** — not inside the coding agent.

### Step 1 — initialise relic for your coding agent

Run once from your project root. Writes relic's instructions into your agent's config file so it automatically handles `relic --refresh` output:

```bash
relic --init claude     # Claude Code   → writes/updates CLAUDE.md
relic --init copilot    # GitHub Copilot → writes/updates .github/copilot-instructions.md
relic --init cursor     # Cursor        → writes/updates .cursorrules
relic --init codex      # OpenAI Codex  → writes/updates AGENTS.md
relic --init all        # writes all of the above
```

Re-running `--init` is safe — it updates the existing block without duplicating it.

### Step 2 — auto-generate `relic.yaml`

```bash
relic init
```

Walks your project, detects subprojects from package manifests and source directories, writes `relic.yaml`, and adds `relic.yaml` + `.knowledge/` to `.gitignore` automatically.

`relic.yaml` is personal config — gitignored by design. Each developer runs `relic init` on their own machine.

### Step 3 — generate knowledge graphs

Run in **your terminal**. Your active coding agent reads the output and writes the graph files:

```bash
relic --refresh            # generate for stale or missing graphs only
relic --refresh payments   # generate for one subproject only
relic --refresh --force    # regenerate everything regardless of staleness
```

relic checks each subproject against the latest git commit. Fresh graphs are skipped automatically. The agent analyses the code dump and writes `.knowledge/<subproject>/graph.md` plus a master `.knowledge/graph.md`.

### Step 4 — load a graph before starting a new session

```bash
relic payments             # copies session prompt to clipboard
relic payments api         # load multiple subprojects into one prompt
```

Paste into your AI session. The agent starts with full codebase context immediately.

---

## Usage

```bash
relic init                 # auto-discover subprojects, generate relic.yaml, update .gitignore
relic <name> [name ...]    # load graph(s) → clipboard
relic --list               # list all subprojects defined in relic.yaml
relic --refresh            # emit prompts for stale/missing graphs only
relic --refresh <name>     # emit prompt for one subproject
relic --refresh --force    # force regenerate all graphs regardless of staleness
relic --stale              # check which graphs are out of date
relic --init <agent>       # write relic instructions into agent config file
relic --init all           # write instructions for all supported agents
relic --update             # pull latest from GitHub main and reinstall
relic --version            # print installed version
```

---

## Example: backend (payment app)

Say your project looks like this:

```
backend/
├── payments/       # Stripe integration, charge logic
├── auth/           # JWT, user sessions
├── notifications/  # Email + push after payment events
└── ...
```

**Step 1 — run setup in your terminal**

```bash
cd backend
relic --init claude   # writes CLAUDE.md
```

**Step 2 — create `relic.yaml`**

```yaml
subprojects:
  payments:
    path: ./payments
    description: "Stripe payment processing — charges, refunds, webhook handling"
  auth:
    path: ./auth
    description: "JWT auth, user sessions, role-based access control"
  notifications:
    path: ./notifications
    description: "Email and push notification triggers after payment events"
```

**Step 3 — generate knowledge graphs**

Run in your terminal:

```bash
relic --refresh
```

relic prints a prompt for each subproject. Your agent reads it and writes:

```
backend/
└── .knowledge/
    ├── payments/graph.md
    ├── auth/graph.md
    └── notifications/graph.md
```

**Step 4 — start a new session on the payments bug**

```bash
relic payments
```

Prompt copied to clipboard. Paste into your AI session. The agent immediately knows:
- What Stripe webhooks exist and where they're handled
- Which functions process refunds
- What `payments` calls in `notifications` after a charge succeeds

**Step 5 — working across two subprojects at once**

```bash
relic payments notifications
```

Both graphs merged into one prompt. Agent understands the full flow end-to-end.

**Step 6 — check if graphs are stale after a sprint**

```bash
relic --stale
```

```
┌──────────────────┬───────┬─────────────────────────────────┐
│ payments         │  yes  │ Commit 2024-01-15 newer...      │
│ auth             │  no   │ Graph is up to date             │
│ notifications    │  yes  │ graph.md does not exist         │
└──────────────────┴───────┴─────────────────────────────────┘
```

Refresh only what's stale:

```bash
relic --refresh payments notifications
```

---

## How it works

**Generating graphs (`--refresh`):**

`relic --refresh payments` walks `./payments`, collects all source files, and prints a structured prompt to stdout. Your active AI coding agent reads this prompt, analyses the code, and writes `.knowledge/payments/graph.md`. No separate LLM API key needed — relic delegates to the agent you are already using.

**Loading graphs:**

`relic payments` reads `.knowledge/payments/graph.md`, wraps it in a session opener prompt, and copies it to your clipboard. Paste into any new AI session for instant full-project context.

---

## Knowledge graph sections

Each `graph.md` contains:

- **What is [subproject]** — 2-3 line summary
- **Entry Points** — files where execution starts
- **Key Entities** — table of important classes, functions, configs
- **API Surface** — endpoints or triggers exposed
- **Cross-Project Dependencies** — what it calls and what calls it
- **Folder Structure** — annotated directory tree
- **Confidence Briefing Anchor** — agent fills in after reading

---

## Staleness detection

`relic --stale` uses git to compare the last commit time for each subproject path against the `graph.md` modification time. If new commits exist since the last refresh, the graph is marked stale.

---

## Keeping graphs out of your project repo

relic reads `relic.yaml` and writes `.knowledge/` in whichever directory you run it from. To keep these local only (recommended for work/shared codebases), add to that project's `.gitignore`:

```
.knowledge/
relic.yaml
```

If you own the repo and want teammates to share graphs, omit these lines and commit `.knowledge/` alongside code changes.

---

## Security

relic reads your source files and builds prompts from them. These protections are built in:

**Path traversal prevention**

Subproject paths in `relic.yaml` are resolved and checked against the project root. Any path that escapes — e.g. `path: /etc` or `path: ../../secrets` — is rejected before any files are read.

**Symlink blocking**

relic skips all symlinks when walking subproject directories, preventing escape to arbitrary paths on your machine.

**CLI argument sanitisation**

Subproject names passed on the command line are validated against `[a-zA-Z0-9_-]` only. Path traversal attempts like `relic ../../etc` are rejected immediately.

**Prompt injection defence**

Every file's content is wrapped in explicit delimiters and the generation prompt instructs the agent to treat file content as data, not as instructions. This reduces the risk of malicious strings in source files hijacking agent behaviour.

**File collection limits**

relic skips files over 100 KB and caps collection at 500 files per subproject, preventing runaway prompt sizes.

**What relic does NOT do**

- No API calls, no telemetry — code never leaves your machine.
- Generation prompt goes to stdout only — your local agent reads it.
- `relic --update` passes only the hardcoded `github.com/Swanand58/relic@main` URL to uv — no user input is ever passed to a shell.
