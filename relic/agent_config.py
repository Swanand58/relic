"""Agent config writer — injects relic instructions into coding agent config files.

Each agent has a different config file it reads at session start. This module
writes the relic behaviour block into the right file for each agent so the agent
automatically handles `relic --refresh` output without manual prompting.
"""

from pathlib import Path

from rich.console import Console

console = Console()

RELIC_BLOCK_START = "<!-- relic:start -->"
RELIC_BLOCK_END = "<!-- relic:end -->"

RELIC_INSTRUCTIONS = """\
## Relic — Codebase Knowledge Graph Tool

Relic is installed in this project. It manages `.knowledge/<subproject>/graph.md` files
that give you instant codebase context at the start of every session.

### When the user runs `relic --refresh` (or `! relic --refresh`)

1. The command prints a structured generation prompt to stdout for each subproject.
2. Read the full output carefully.
3. For each subproject prompt, analyse the code dump provided and write the knowledge
   graph markdown to the exact file path specified in the prompt (`output_path`).
4. Follow the template structure in the prompt exactly — fill every section, no placeholders.
5. Confirm to the user which graph.md files were written.

### When the user runs `relic <subproject>`

The session prompt is copied to clipboard automatically. Nothing for you to do —
the user will paste it into a new session.

### When the user runs `relic --stale`

The command prints a staleness table. If any subproject is stale, suggest running
`relic --refresh <subproject>` to regenerate it.

### graph.md files

- Stored in `.knowledge/<subproject>/graph.md`
- Write them using your file writing capability to the exact path relic specifies
- These are the source of truth for project context — keep them accurate
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
    """Write relic instructions into the config file for a specific agent."""
    agent = AGENTS[agent_key]
    target = project_root / agent["path"]
    action = _upsert_block(target, RELIC_INSTRUCTIONS)
    console.print(f"[green]✓[/green] [bold]{agent['name']}[/bold] — {action} [dim]{target}[/dim]")


def init_all_agents(project_root: Path) -> None:
    """Write relic instructions into config files for all supported agents."""
    for key in AGENTS:
        init_agent(key, project_root)
