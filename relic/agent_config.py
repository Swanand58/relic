"""Agent config writer — injects relic instructions and MCP config for coding agents.

Each agent reads a different config file at session start. This module writes the
relic behaviour block and MCP server registration into the right files.

MCP config paths per agent:
  Claude Code  — .claude/settings.json       (mcpServers key)
  Cursor       — .cursor/mcp.json            (mcpServers key)
  Copilot      — .vscode/mcp.json            (servers key, VS Code 1.99+)
  Codex        — instructions only (no standard MCP config file)
"""

import json
from pathlib import Path

import yaml
from rich.console import Console

console = Console()

RELIC_BLOCK_START = "<!-- relic:start -->"
RELIC_BLOCK_END = "<!-- relic:end -->"

# Token replaced with a real project file path at init time.
# Using a sentinel (not str.format) so curly braces inside the TOON example
# don't have to be escaped.
RELIC_EXAMPLE_PLACEHOLDER = "__RELIC_EXAMPLE_FILE__"

RELIC_INSTRUCTIONS = """\
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
relic_query __RELIC_EXAMPLE_FILE__
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
relic query __RELIC_EXAMPLE_FILE__
relic search PaymentProcessor
relic index
```

### Knowledge graph files

- `.knowledge/index.pkl` — binary graph (gitignored, local only)
- `.knowledge/index.toon` — human-readable TOON index (gitignored, local only)
"""


_BARREL_FILES = {"__init__.py", "index.ts", "index.tsx", "index.js", "index.jsx"}


def _pick_example_file(project_root: Path) -> str:
    """Return a representative file path to drop into the agent instructions example.

    Preference order:
    1. Most-connected non-barrel file from the existing index (real demo).
    2. First subproject path from relic.yaml + a placeholder filename.
    3. A generic `src/<your-file>` placeholder.
    """
    # Lazy import to avoid a circular dep at module load time.
    from relic.indexer import load_graph

    knowledge_dir = project_root / ".knowledge"
    try:
        G = load_graph(knowledge_dir)
    except FileNotFoundError:
        G = None
    except Exception:
        G = None

    if G is not None:
        best_path: str | None = None
        best_degree = -1
        for n, d in G.nodes(data=True):
            if d.get("ntype") != "file":
                continue
            if Path(n).name in _BARREL_FILES:
                continue
            deg = G.degree(n)
            if deg > best_degree:
                best_degree = deg
                best_path = n
        if best_path:
            return best_path

    config_file = project_root / "relic.yaml"
    if config_file.exists():
        try:
            with config_file.open(encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            subprojects = cfg.get("subprojects", {})
            if subprojects:
                first = next(iter(subprojects.values()))
                base = (first.get("path", "src") or "src").lstrip("./") or "src"
                return f"{base}/<your-file>"
        except (yaml.YAMLError, OSError):
            pass

    return "src/<your-file>"


def _wrap_block(content: str) -> str:
    return f"{RELIC_BLOCK_START}\n{content}\n{RELIC_BLOCK_END}\n"


def _upsert_block(file_path: Path, content: str) -> str:
    """Insert or replace the relic block in a file. Returns 'created' or 'updated'."""
    block = _wrap_block(content)

    if not file_path.exists():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(block, encoding="utf-8")
        return "created"

    existing = file_path.read_text(encoding="utf-8")

    if RELIC_BLOCK_START in existing:
        start = existing.index(RELIC_BLOCK_START)
        end = existing.index(RELIC_BLOCK_END) + len(RELIC_BLOCK_END)
        updated = existing[:start] + block + existing[end:].lstrip("\n")
        file_path.write_text(updated, encoding="utf-8")
        return "updated"

    separator = "\n\n" if existing.strip() else ""
    file_path.write_text(existing + separator + block, encoding="utf-8")
    return "updated"


AGENTS: dict[str, dict] = {
    "claude": {
        "name": "Claude Code",
        "path": "CLAUDE.md",
        # MCP config: merged into existing settings JSON under mcpServers key
        "mcp_config": ".claude/settings.json",
        "mcp_key": "mcpServers",
        "mcp_server": {"command": "relic", "args": ["mcp"]},
    },
    "copilot": {
        "name": "GitHub Copilot",
        "path": ".github/copilot-instructions.md",
        # VS Code workspace MCP config — requires VS Code 1.99+
        "mcp_config": ".vscode/mcp.json",
        "mcp_key": "servers",
        "mcp_server": {"type": "stdio", "command": "relic", "args": ["mcp"]},
    },
    "cursor": {
        "name": "Cursor",
        "path": ".cursorrules",
        "mcp_config": ".cursor/mcp.json",
        "mcp_key": "mcpServers",
        "mcp_server": {"command": "relic", "args": ["mcp"]},
    },
    "codex": {
        "name": "OpenAI Codex",
        "path": "AGENTS.md",
        # No standard MCP config file for Codex CLI — instructions only
    },
}


def _write_mcp_config(agent_key: str, project_root: Path) -> str:
    """Write MCP server registration for the given agent.

    Merges into existing config — does not overwrite unrelated keys.
    For Claude Code, also strips stale PreToolUse hooks from older relic versions.
    Returns 'created' or 'updated'.
    """
    agent = AGENTS[agent_key]
    config_path = project_root / agent["mcp_config"]
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            config = {}
        action = "updated"
    else:
        config = {}
        action = "created"

    config.setdefault(agent["mcp_key"], {})["relic"] = agent["mcp_server"]

    # Claude Code only: strip stale relic PreToolUse hooks from older versions
    if agent_key == "claude":
        hooks = config.get("hooks", {})
        pre_tool_use = hooks.get("PreToolUse", [])
        cleaned = [
            entry
            for entry in pre_tool_use
            if not (
                isinstance(entry, dict)
                and any(isinstance(h, dict) and "relic" in h.get("command", "") for h in entry.get("hooks", []))
            )
        ]
        if cleaned != pre_tool_use:
            hooks["PreToolUse"] = cleaned
            if not cleaned:
                del hooks["PreToolUse"]
            if not hooks:
                config.pop("hooks", None)
            else:
                config["hooks"] = hooks

    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return action


def init_agent(agent_key: str, project_root: Path) -> None:
    """Write relic instructions and MCP config for a specific agent."""
    agent = AGENTS[agent_key]
    target = project_root / agent["path"]
    instructions = RELIC_INSTRUCTIONS.replace(RELIC_EXAMPLE_PLACEHOLDER, _pick_example_file(project_root))
    action = _upsert_block(target, instructions)
    console.print(f"[green]✓[/green] [bold]{agent['name']}[/bold] — {action} [dim]{target}[/dim]")

    if "mcp_config" in agent:
        mcp_action = _write_mcp_config(agent_key, project_root)
        config_path = project_root / agent["mcp_config"]
        console.print(f"[green]✓[/green] [bold]{agent['name']} MCP[/bold] — {mcp_action} [dim]{config_path}[/dim]")
        console.print("[dim]tools: relic_query, relic_search, relic_reindex, relic_stats[/dim]")


def init_all_agents(project_root: Path) -> None:
    """Write relic instructions into config files for all supported agents."""
    for key in AGENTS:
        init_agent(key, project_root)
