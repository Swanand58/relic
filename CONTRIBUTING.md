# Contributing to relic

Thanks for being here. Relic stays small and opinionated on purpose. Before
you write code, please read this whole file — it'll save us both time.

## Project philosophy

- **Static analysis only.** Relic never calls an LLM, never makes network
  requests at runtime, never collects telemetry. Any change that breaks
  this contract will be rejected.
- **Token cost is a feature.** Every `relic_query` payload is measured in
  tokens. New output formats, fields, or instructions need a justification
  in token spent vs information gained. See `relic audit` and
  `tests/test_token_budget.py` for the budgets we enforce.
- **Determinism over cleverness.** Same repo + same command must produce
  byte-identical output. No timestamps, no PIDs, no random ordering in
  serialized graph data.
- **One way to do things.** If a feature can already be expressed by an
  existing command + a flag, prefer the flag.

## Getting set up

You need:

- Python 3.11+ (CI also runs 3.12 and 3.13)
- [uv](https://docs.astral.sh/uv/) for dependency management
- A POSIX shell or PowerShell

```bash
git clone https://github.com/Swanand58/relic.git
cd relic
uv sync --all-extras --group dev
uv run relic --version
```

Dogfood relic on itself — `relic.yaml` is committed and points at `./relic`,
so you can run any command from the repo root:

```bash
uv run relic index
uv run relic query relic/cli.py
uv run relic audit
```

## Development loop

```bash
# 1. Lint
uv run ruff check relic tests
uv run ruff format relic tests

# 2. Test
uv run pytest

# 3. Smoke
uv run relic index && uv run relic stats
```

CI runs all three on every PR across Linux / macOS / Windows × Python
3.11 / 3.12 / 3.13. Match what CI does locally and you'll have a green
pipeline first try.

## Making changes

1. **Open an issue first** for anything bigger than a typo or a small bug
   fix. We'll agree on scope before you spend time writing code.
2. **Branch from `main`.** Use a short, descriptive name:
   `feat/coverage-csv-export`, `fix/watcher-windows-paths`,
   `chore/bump-rich`.
3. **Keep PRs small.** A PR should do one thing. If you find yourself
   touching unrelated files, split into two PRs.
4. **Add tests.** New behaviour without tests will not be merged. Tests
   live in `tests/` and use `pytest`. The fixtures in `tests/conftest.py`
   handle most graph and project setup.
5. **Update docs.** If you change user-visible behaviour, update
   `README.md`. Add an entry to `CHANGELOG.md` under `## [Unreleased]`.
6. **Respect token budgets.** If you touch `RELIC_INSTRUCTIONS` or any
   MCP tool description, run `uv run relic audit` and confirm the
   regression tests still pass:

   ```bash
   uv run pytest tests/test_token_budget.py
   ```

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org):

```
feat(coverage): add csv export
fix(watcher): debounce flush on shutdown
docs(readme): clarify init flow
chore(deps): bump watchdog to 5.0
test(audit): cover empty graph
```

PR titles follow the same format. We squash-merge, so the PR title becomes
the commit on `main`.

## Code style

- Ruff (lint + format) is the only style authority. No bikeshedding.
- 120 char line limit (ruff-enforced).
- Type hints on every public function and method.
- Docstrings on modules, public classes, and public functions. Skip
  docstrings on trivial helpers.
- Avoid comments that narrate what the code does. Comments should explain
  non-obvious *why* — trade-offs, constraints, surprises.

## What we won't accept

- Anything that adds a network call at runtime.
- Anything that adds an LLM dependency or token cost.
- Telemetry, analytics, or "anonymous" usage reporting of any kind.
- Vendored binaries or generated bundles.
- New top-level commands that duplicate something `--flag` could do.

## Reporting a security issue

Don't open a public issue. See [SECURITY.md](SECURITY.md) for the private
disclosure flow.

## Code of conduct

By participating, you agree to follow our
[Code of Conduct](CODE_OF_CONDUCT.md).
