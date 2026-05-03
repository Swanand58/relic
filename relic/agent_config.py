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

from rich.console import Console

console = Console()

RELIC_BLOCK_START = "<!-- relic:start -->"
RELIC_BLOCK_END = "<!-- relic:end -->"

RELIC_INSTRUCTIONS = """\
## Relic — Codebase Knowledge Graph

Relic builds a static knowledge graph from source code. Use the MCP tools below
to get precise, token-efficient context before editing code — ~300–1,200 tokens
vs 5,000–40,000 for manual file reads.

### MCP Tools

**`relic_query`** — Get dependency context for a file or symbol.
Call before editing unfamiliar code. Returns imports, exports, neighbors, and
callers (files that import this file) in TOON format.

**`relic_search`** — Search for files and symbols by name.
Call when you don't know where a class, function, or file lives.

**`relic_reindex`** — Rebuild the knowledge graph after writing files.
Call after creating, editing, or deleting source files.

**`relic_stats`** — Check index health (files indexed, last updated).
Call to verify the index is fresh before a large refactor.

### Workflow

1. Starting work on unfamiliar code → `relic_query <file_or_symbol>`
2. Don't know where something lives → `relic_search <name>`
3. After writing or deleting files → `relic_reindex`
4. Index may be stale → `relic_stats`, then `relic_reindex` if needed

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
~/.local/bin/relic query src/payments/processor.py
~/.local/bin/relic query PaymentProcessor
~/.local/bin/relic index
```

### Knowledge graph files

- `.knowledge/index.pkl` — binary graph (gitignored, local only)
- `.knowledge/index.toon` — human-readable TOON index (gitignored, local only)
"""


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
            entry for entry in pre_tool_use
            if not (
                isinstance(entry, dict)
                and any(
                    isinstance(h, dict) and "relic" in h.get("command", "")
                    for h in entry.get("hooks", [])
                )
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
    action = _upsert_block(target, RELIC_INSTRUCTIONS)
    console.print(f"[green]✓[/green] [bold]{agent['name']}[/bold] — {action} [dim]{target}[/dim]")

    if "mcp_config" in agent:
        mcp_action = _write_mcp_config(agent_key, project_root)
        config_path = project_root / agent["mcp_config"]
        console.print(
            f"[green]✓[/green] [bold]{agent['name']} MCP[/bold] — {mcp_action} "
            f"[dim]{config_path}[/dim]"
        )
        console.print("[dim]tools: relic_query, relic_search, relic_reindex, relic_stats[/dim]")


def init_all_agents(project_root: Path) -> None:
    """Write relic instructions into config files for all supported agents."""
    for key in AGENTS:
        init_agent(key, project_root)
