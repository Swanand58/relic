# relic

CLI tool that stores and loads codebase knowledge graphs per subproject — so your AI coding agent starts every session with full context, not a cold read.

Works with any AI coding agent: Claude Code, GitHub Copilot, Codex, Cursor, etc.

---

## Install

Requires [uv](https://docs.astral.sh/uv/).

### From GitHub (any machine, no PyPI needed)

```bash
uv tool install git+https://github.com/Swanand58/relic
```

### Upgrade

```bash
uv tool upgrade relic
```

### Local dev install (editable — picks up changes live)

```bash
# from inside the cloned repo
uv tool install --editable .
```

---

## Setup

1. In your project root, create `relic.yaml`:

```yaml
subprojects:
  payments:
    path: ./payments
    description: "Payment processing service"
  api:
    path: ./api
    description: "FastAPI package with endpoints, services and models"
  pipeline:
    path: ./pipeline
    description: "Cloud data pipeline jobs"
```

2. Generate knowledge graphs (your active AI agent does the writing):

```bash
relic --refresh payments   # emits a generation prompt → agent writes .knowledge/payments/graph.md
relic --refresh            # same for all subprojects
```

3. Load a graph into your clipboard before starting a session:

```bash
relic payments             # copies session prompt to clipboard
```

Paste into your AI session. The agent reads the graph and starts with full context.

---

## Usage

```bash
relic payments             # load payments graph → clipboard
relic payments api         # load multiple graphs into one prompt → clipboard
relic --list               # list all subprojects defined in relic.yaml
relic --stale              # check which graphs are out of date
relic --refresh            # emit generation prompts for all subprojects
relic --refresh payments   # emit generation prompt for one subproject
relic update               # pull latest from GitHub and reinstall
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

**Step 1 — create `relic.yaml` in the project root**

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

**Step 2 — generate knowledge graphs (do this once, redo after big changes)**

Run this inside your AI coding session (Claude Code, Copilot, etc.):

```bash
relic --refresh
```

relic prints a structured prompt for each subproject. Your agent reads it, analyses the code, and writes:

```
backend/
└── .knowledge/
    ├── payments/graph.md
    ├── auth/graph.md
    └── notifications/graph.md
```

**Step 3 — start a new session on the payments bug**

```bash
relic payments
```

Prompt copied to clipboard. Paste into your AI session. The agent reads the graph and immediately knows:
- What Stripe webhooks exist and where they're handled
- Which functions process refunds
- What `payments` calls in `notifications` after a charge succeeds

**Step 4 — working across two subprojects at once**

```bash
relic payments notifications
```

Both graphs merged into one prompt. Agent understands the full flow end-to-end.

**Step 5 — check if graphs are stale after a sprint**

```bash
relic --stale
```

```
┌─────────────────────────────────────────────────────┐
│ Staleness Check                                     │
├──────────────────┬───────┬────────────────────────────┤
│ payments         │  yes  │ Commit 2024-01-15 newer... │
│ auth             │  no   │ Graph is up to date        │
│ notifications    │  yes  │ graph.md does not exist    │
└──────────────────┴───────┴────────────────────────────┘
```

Refresh only what's stale:

```bash
relic --refresh payments notifications
```

---

## How it works

**Generating graphs (`--refresh`):**

`relic --refresh payments` walks the `./payments` directory, collects all source files, and prints a structured prompt to stdout. Your active AI coding agent reads this prompt, analyses the code, and writes `.knowledge/payments/graph.md`. No separate LLM API key or account needed — relic uses the agent you are already working with.

**Loading graphs:**

`relic payments` reads `.knowledge/payments/graph.md`, wraps it in a session opener prompt, and copies it to your clipboard. Paste it into any new AI session for instant full-project context.

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

relic writes `.knowledge/` and reads `relic.yaml` in whatever directory you run it from. If you do not want these committed to your project repo (e.g. a work codebase), add them to that project's `.gitignore`:

```
.knowledge/
relic.yaml
```

Graphs stay local to your machine. Your project repo is untouched. This is the recommended setup for shared or work codebases where you want relic context privately, without pushing it upstream.

If you own the repo and want graphs committed (so teammates benefit too), simply omit these lines from `.gitignore` and commit `.knowledge/` alongside code changes.

---

## Security

relic reads your source files and builds prompts from them. These protections are built in:

**Path traversal prevention**

Subproject paths in `relic.yaml` are resolved and checked against the project root (the directory where you run relic). Any path that escapes the project root — e.g. `path: /etc` or `path: ../../secrets` — is rejected with an error before any files are read.

**Symlink blocking**

relic skips all symlinks when walking subproject directories. A symlink inside your codebase pointing outside the project cannot be used to read arbitrary files off your machine.

**CLI argument sanitisation**

Subproject names passed on the command line (e.g. `relic payments`) are validated against `[a-zA-Z0-9_-]` only. Path traversal attempts like `relic ../../etc` are rejected immediately.

**Prompt injection defence**

Every file's content is wrapped in explicit delimiters and the generation prompt instructs the agent to treat all file content as data, not as instructions. This reduces the risk of malicious strings inside your source files hijacking the agent's behaviour during graph generation.

**File collection limits**

relic skips files over 100 KB and caps collection at 500 files per subproject. This prevents runaway prompt sizes from unusually large or deeply nested directories.

**What relic does NOT do**

- It does not send your code anywhere itself — no API calls, no telemetry.
- The generation prompt goes to stdout only. Your AI agent (Claude Code, Copilot, etc.) reads it locally.
- `relic update` runs `uv tool install --reinstall` against the hardcoded GitHub URL only — no user-supplied URLs are ever passed to a shell.
