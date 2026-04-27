"""Loader — reads graph.md files and builds a session prompt for AI agents."""

from pathlib import Path

import pyperclip
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

SESSION_OPENER = """\
You are working on this codebase. Read the following knowledge graph(s) carefully.

After reading, provide:
1. A confidence score (1-10) for each subproject
2. Any gaps or uncertainties in your understanding
3. Confirm you are ready to work

---

{graphs}
"""


def read_graph(subproject_name: str, knowledge_dir: Path) -> str:
    """Read the graph.md for a subproject and return its content.

    Raises FileNotFoundError if the graph does not exist.
    """
    graph_path = knowledge_dir / subproject_name / "graph.md"
    if not graph_path.exists():
        raise FileNotFoundError(
            f"No graph found for '{subproject_name}' at {graph_path}. "
            f"Run `relic --refresh {subproject_name}` to generate it."
        )
    return graph_path.read_text(encoding="utf-8")


def build_prompt(subproject_names: list[str], knowledge_dir: Path) -> str:
    """Build the full session prompt by loading all requested subproject graphs.

    Returns the assembled prompt string.
    Raises FileNotFoundError if any graph is missing.
    """
    sections = []
    for name in subproject_names:
        content = read_graph(name, knowledge_dir)
        sections.append(f"<!-- SUBPROJECT: {name} -->\n\n{content}")

    combined = "\n\n---\n\n".join(sections)
    return SESSION_OPENER.format(graphs=combined)


def load_and_copy(subproject_names: list[str], knowledge_dir: Path) -> None:
    """Load graphs for the given subprojects, build the session prompt, and copy to clipboard.

    Prints a rich confirmation panel on success.
    """
    try:
        prompt = build_prompt(subproject_names, knowledge_dir)
    except FileNotFoundError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(1)

    pyperclip.copy(prompt)

    names_str = ", ".join(subproject_names)
    char_count = len(prompt)

    console.print(
        Panel(
            Text.assemble(
                ("Prompt copied to clipboard\n\n", "bold green"),
                ("Subprojects: ", "bold"),
                (names_str + "\n", "cyan"),
                ("Characters:  ", "bold"),
                (f"{char_count:,}", "cyan"),
            ),
            title="[bold]Relic[/bold]",
            border_style="green",
        )
    )
