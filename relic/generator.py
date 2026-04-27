"""Generator — builds a graph.md generation prompt for the active coding agent to execute.

No LLM calls are made here. The prompt is printed to stdout so the agent that
invoked `relic --refresh` can read it, analyse the code, and write the output file.
"""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()

MAX_FILE_BYTES = 100_000
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", "dist", "build"}
SKIP_EXTENSIONS = {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin", ".lock"}

GENERATION_PROMPT = """\
You are acting as a senior software architect analysing a codebase subproject.

## Your task

Read every file in the code dump below. Then write a knowledge graph markdown file
to the path:

    {output_path}

The file must follow this exact structure (fill in every section — no placeholders):

---
# Knowledge Graph: {name}

**Generated:** <today's date, UTC>
**Git Hash:** <current HEAD short hash, or "unknown">
**Path:** {subproject_path}

---

## What is {name}

<2-3 sentences describing what this subproject does and why it exists.>

---

## Entry Points

- `<file>` — <what execution/import starts here>

---

## Key Entities

| Name | Type | File | Purpose |
|------|------|------|---------|
| `<ClassName>` | class | `<file>` | <one sentence> |

---

## API Surface

- **<METHOD>** `<path or trigger>` — <description>

_(If none, write: No HTTP endpoints exposed.)_

---

## Cross-Project Dependencies

**Calls:**
- `<target>` — <reason>

**Called by:**
- `<source>` — <reason>

---

## Folder Structure

```
<annotated directory tree>
```

---

## Confidence Briefing Anchor

> _Agent: Fill in after reading — "I understand [X]. I am less confident about [Y]. Ready: yes/no."_
---

## Subproject description

{description}

## Code dump

{code_dump}
"""


def _collect_files(root: Path) -> list[tuple[str, str]]:
    """Walk root and return list of (relative_path, content) tuples.

    Skips binary files, oversized files, and ignored directories.
    """
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix in SKIP_EXTENSIONS:
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, PermissionError):
            continue
        rel = str(p.relative_to(root))
        files.append((rel, content))
    return files


def _build_code_dump(files: list[tuple[str, str]]) -> str:
    """Format collected files into a readable code dump."""
    parts = []
    for rel, content in files:
        parts.append(f"### {rel}\n```\n{content}\n```")
    return "\n\n".join(parts)


def build_refresh_prompt(
    name: str,
    cfg: dict,
    knowledge_dir: Path,
) -> str:
    """Build the generation prompt string for one subproject.

    Returns the prompt. Does not write anything — the calling agent does.
    """
    subproject_path = Path(cfg["path"]).resolve()
    description = cfg.get("description", "")
    output_path = (knowledge_dir / name / "graph.md").resolve()

    files = _collect_files(subproject_path)
    code_dump = _build_code_dump(files) if files else "_No readable source files found._"

    return GENERATION_PROMPT.format(
        name=name,
        description=description,
        subproject_path=subproject_path,
        output_path=output_path,
        code_dump=code_dump,
    )


def emit_refresh_prompt(name: str, cfg: dict, knowledge_dir: Path) -> None:
    """Print the generation prompt for one subproject to stdout.

    The active coding agent reads this output and writes graph.md.
    """
    out_dir = knowledge_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_refresh_prompt(name, cfg, knowledge_dir)

    # Use plain print so the agent can capture raw stdout cleanly.
    print(prompt)

    console.print(
        Panel(
            f"[bold cyan]{name}[/bold cyan] prompt emitted.\n"
            f"Agent: write output to [dim]{out_dir / 'graph.md'}[/dim]",
            title="[bold]Relic — refresh[/bold]",
            border_style="cyan",
        ),
        err=True,  # status goes to stderr so stdout stays clean for agent capture
    )


def emit_refresh_all(subprojects: dict, knowledge_dir: Path) -> None:
    """Emit generation prompts for every subproject, separated by a clear divider."""
    for name, cfg in subprojects.items():
        print(f"\n{'=' * 80}")
        print(f"# SUBPROJECT: {name}")
        print(f"{'=' * 80}\n")
        emit_refresh_prompt(name, cfg, knowledge_dir)
