"""Agent config writer — injects relic instructions into coding agent config files.

Each agent has a different config file it reads at session start. This module
writes the relic behaviour block into the right file for each agent so the agent
automatically handles relic commands without manual prompting.

For Claude Code, this also writes a PreToolUse hook into .claude/settings.json
so `relic query` fires automatically before every Read/Edit, injecting TOON
context into the agent without any manual command.
"""

import json
from pathlib import Path

from rich.console import Console

console = Console()

RELIC_BLOCK_START = "<!-- relic:start -->"
RELIC_BLOCK_END = "<!-- relic:end -->"

RELIC_INSTRUCTIONS = """\
## Relic — Codebase Knowledge Graph

Relic is installed on this machine. It builds a static knowledge graph from source
code and serves precise, token-efficient context to coding agents.

The relic binary lives at `~/.local/bin/relic`. If `relic` is not found in your
shell PATH, use the full path: `~/.local/bin/relic`.

### Automatic context injection (Claude Code)

Two mechanisms are active:

1. **PreToolUse hook** — fires before every Read/Edit, runs `relic query <file>`,
   injects TOON context automatically. No manual call needed.
2. **MCP tool** — `relic_query` is available as a native tool. Call it directly
   for any file or symbol to get precise dependency context on demand.

You will see TOON context like:

```
focus: src/payments/processor.py

files[3]{path,language,subproject}:
  src/payments/processor.py,python,payments
  src/payments/models.py,python,payments
  src/core/database.py,python,core

symbols[5]{name,type,file,line}:
  PaymentProcessor,class,src/payments/processor.py,12
  ...
```

Read this TOON block before editing — it tells you what the file imports,
what it exports, and what else in the codebase depends on it.

### Commands

**Build or rebuild the knowledge graph** (run after significant codebase changes):

```bash
~/.local/bin/relic index
```

**Query context for a specific file or symbol** (the hook does this automatically):

```bash
~/.local/bin/relic query src/payments/processor.py
~/.local/bin/relic query PaymentProcessor
~/.local/bin/relic query src/payments/processor.py --depth 3
```

**Check which subproject graphs are stale:**

```bash
~/.local/bin/relic --stale
```

**Refresh graph.md knowledge files** (for agents that use the markdown format):

```bash
~/.local/bin/relic --refresh
~/.local/bin/relic --refresh payments
```

### Knowledge graph files

- Index (binary): `.knowledge/index.pkl`
- TOON index (human-readable): `.knowledge/index.toon`
- Subproject graph docs: `.knowledge/<subproject>/graph.md`

These files are local to your machine and gitignored.
"""

# Shell command injected into .claude/settings.json as a PreToolUse hook.
# Receives tool call JSON on stdin. Must output {"hookSpecificOutput":{"additionalContext":"..."}}
# so Claude Code injects the TOON block before the tool result. Silently no-ops on any error.
_HOOK_COMMAND = (
    "python3 -c \""
    "import json,sys,subprocess,pathlib;"
    "d=json.loads(sys.stdin.read());"
    "inp=d.get('tool_input',d);"
    "f=inp.get('file_path','') or inp.get('path','');"
    "r=subprocess.run([str(pathlib.Path.home()/'.local/bin/relic'),'query',f],capture_output=True,text=True) if f else None;"
    "c=r.stdout if r and r.returncode==0 and r.stdout.strip() else '';"
    "print(json.dumps({'hookSpecificOutput':{'additionalContext':c}})) if c else None"
    "\""
)


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
        # Replace existing block
        start = existing.index(RELIC_BLOCK_START)
        end = existing.index(RELIC_BLOCK_END) + len(RELIC_BLOCK_END)
        updated = existing[:start] + block + existing[end:].lstrip("\n")
        file_path.write_text(updated, encoding="utf-8")
        return "updated"

    # Append to end
    separator = "\n\n" if existing.strip() else ""
    file_path.write_text(existing + separator + block, encoding="utf-8")
    return "updated"


def _write_claude_hooks(project_root: Path) -> str:
    """Write or update the relic PreToolUse hook in .claude/settings.json.

    Merges into existing settings — does not overwrite unrelated keys.
    Returns 'created' or 'updated'.
    """
    settings_path = project_root / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            settings = {}
        action = "updated"
    else:
        settings = {}
        action = "created"

    hooks = settings.setdefault("hooks", {})
    pre_tool_use = hooks.setdefault("PreToolUse", [])

    # Remove any existing relic hook entry to avoid duplicates
    pre_tool_use[:] = [
        entry for entry in pre_tool_use
        if not (
            isinstance(entry, dict)
            and entry.get("matcher", "") in ("Read|Edit", "Read|Edit|Write")
            and any(
                isinstance(h, dict) and "relic" in h.get("command", "")
                for h in entry.get("hooks", [])
            )
        )
    ]

    pre_tool_use.append({
        "matcher": "Read|Edit",
        "hooks": [
            {
                "type": "command",
                "command": _HOOK_COMMAND,
            }
        ],
    })

    # MCP server registration
    mcp_servers = settings.setdefault("mcpServers", {})
    mcp_servers["relic"] = {
        "command": "relic",
        "args": ["mcp"],
    }

    settings_path.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return action


AGENTS = {
    "claude": {
        "name": "Claude Code",
        "path": "CLAUDE.md",
    },
    "copilot": {
        "name": "GitHub Copilot",
        "path": ".github/copilot-instructions.md",
    },
    "cursor": {
        "name": "Cursor",
        "path": ".cursorrules",
    },
    "codex": {
        "name": "OpenAI Codex",
        "path": "AGENTS.md",
    },
}


def init_agent(agent_key: str, project_root: Path) -> None:
    """Write relic instructions into the config file for a specific agent.

    For Claude Code, also writes the PreToolUse hook into .claude/settings.json.
    """
    agent = AGENTS[agent_key]
    target = project_root / agent["path"]
    action = _upsert_block(target, RELIC_INSTRUCTIONS)
    console.print(f"[green]✓[/green] [bold]{agent['name']}[/bold] — {action} [dim]{target}[/dim]")

    if agent_key == "claude":
        hook_action = _write_claude_hooks(project_root)
        settings_path = project_root / ".claude" / "settings.json"
        console.print(
            f"[green]✓[/green] [bold]Claude Code hook + MCP[/bold] — {hook_action} "
            f"[dim]{settings_path}[/dim]"
        )
        console.print("[dim]PreToolUse hook: relic query fires before every Read/Edit.[/dim]")
        console.print("[dim]MCP server: relic_query tool available as native agent tool.[/dim]")


def init_all_agents(project_root: Path) -> None:
    """Write relic instructions into config files for all supported agents."""
    for key in AGENTS:
        init_agent(key, project_root)
