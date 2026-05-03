"""Visual theme for the relic CLI — single source of truth for palette,
glyphs, spinner, banner, and Rich helpers.

Design principles
-----------------
- One brand mark, used sparingly. ⬢ (black hexagon) opens major sections;
  every other line is plain. The mark only feels like a brand if it's not
  on every line.
- Calm grey-blue palette, Nord-inspired. Numbers and names are bright,
  scaffolding (paths, captions, separators) is dim. The eye lands on data,
  not chrome.
- Animations only where there's actual waiting. A 2-frame hexagon pulse
  on the indexing/init spinners; nothing pulses on idle commands.
- All output is also legible without color (NO_COLOR, piped, CI). Glyphs
  carry meaning on their own; color is reinforcement, not the message.
"""

from __future__ import annotations

from rich import box
from rich._spinners import SPINNERS
from rich.console import Console
from rich.table import Table

# ---------------------------------------------------------------------------
# Palette — Nord-inspired. Hex codes so truecolor terminals render exactly.
# ---------------------------------------------------------------------------

PRIMARY = "#88C0D0"     # frost blue   - brand mark, headers, spinner
DEEP = "#5E81AC"        # deeper blue  - special tokens, links
SECONDARY = "#81A1C1"   # steel        - secondary emphasis, item bullets
FG = "#D8DEE9"          # snow         - default foreground for data
DIM = "#697283"         # cool slate   - paths, captions, separators
SUCCESS = "#A3BE8C"     # aurora green
WARN = "#EBCB8B"        # aurora gold
ERROR = "#BF616A"       # aurora red

# ---------------------------------------------------------------------------
# Glyphs — single-char, monospace-safe. Avoid emoji (renders inconsistently).
# ---------------------------------------------------------------------------

MARK = "⬢"              # the brand — solid hexagon
MARK_HOLLOW = "⬡"       # counterpart — hollow hexagon (used in spinner + banner)
ARROW = "›"             # item bullet
DOT = "·"               # inline separator
CHECK = "✓"             # success
CROSS = "✗"             # failure
WARN_GLYPH = "⚠"        # warning

# ---------------------------------------------------------------------------
# Custom spinner — slow 2-frame hexagon pulse. ~600 ms cycle reads as a
# heartbeat rather than a strobe. Registered into Rich's spinner registry
# so SpinnerColumn(spinner_name="relic") picks it up.
# ---------------------------------------------------------------------------

SPINNER_NAME = "relic"

SPINNERS[SPINNER_NAME] = {
    "interval": 600,
    "frames": [MARK_HOLLOW, MARK],
}

# ---------------------------------------------------------------------------
# Pre-styled consoles. `highlight=False` stops Rich from auto-coloring
# numbers and paths in unexpected ways — we color explicitly via helpers.
# ---------------------------------------------------------------------------

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


# ---------------------------------------------------------------------------
# Helpers — compose Rich markup strings. Pure functions so they're easy to
# unit-test and to reason about.
# ---------------------------------------------------------------------------

def header(text: str) -> str:
    """Section opener: brand mark + label. Use once per command."""
    return f"[bold {PRIMARY}]{MARK}[/]  [bold {FG}]{text}[/]"


def success(text: str) -> str:
    """Successful operation. Goes after the work, not before."""
    return f"[{SUCCESS}]{CHECK}[/]  {text}"


def error(text: str) -> str:
    """Failed operation. Bright enough to interrupt scanning."""
    return f"[bold {ERROR}]{CROSS}[/]  {text}"


def warn(text: str) -> str:
    """Soft warning — something non-fatal worth noticing."""
    return f"[{WARN}]{WARN_GLYPH}[/]  {text}"


def info(text: str) -> str:
    """Neutral progress / status line. Less weight than `success`."""
    return f"[{SECONDARY}]{ARROW}[/]  {text}"


def dim(text: str) -> str:
    """Caption / scaffolding text. The eye should glide over this."""
    return f"[{DIM}]{text}[/]"


def kv(key: str, value: str | int, *, indent: int = 3, key_width: int = 16) -> str:
    """Key-value row used in place of one-column tables.

    Padding the key to a fixed width gives a clean visual column without
    the borders of a real table. Bump `key_width` for blocks with longer
    labels (e.g. the coverage report, where keys reach ~20 chars).
    """
    pad = " " * indent
    return f"{pad}[{DIM}]{key:<{key_width}}[/] [bold {FG}]{value}[/]"


def divider(width: int = 60) -> str:
    """A faint horizontal rule. Use sparingly between major output blocks."""
    return f"[{DIM}]{'─' * width}[/]"


# ---------------------------------------------------------------------------
# Banner — a 3-line graph hint shown by `relic --version`. Keep it small;
# CLIs that announce themselves loudly age badly.
# ---------------------------------------------------------------------------

def banner(version: str, tagline: str = "codebase knowledge graph") -> str:
    p = PRIMARY
    d = DIM
    f = FG
    return (
        f"\n  [bold {p}]{MARK}[/][{d}]──[/][{d}]{MARK_HOLLOW}[/]    "
        f"[bold {f}]relic[/]  [{d}]{version}[/]\n"
        f"  [{d}]│[/]  [{d}]│[/]    "
        f"[{d}]{tagline}[/]\n"
        f"  [{d}]{MARK_HOLLOW}[/][{d}]──[/][bold {p}]{MARK}[/]\n"
    )


# ---------------------------------------------------------------------------
# Table factory — clean, no inner borders, just a header underline. Used
# only for genuinely tabular data (multiple columns). Single-column data
# should use `kv()` instead.
# ---------------------------------------------------------------------------

def make_table(title: str | None = None, *, caption: str | None = None) -> Table:
    return Table(
        title=f"[bold {PRIMARY}]{title}[/]" if title else None,
        title_justify="left",
        caption=f"[{DIM}]{caption}[/]" if caption else None,
        caption_justify="left",
        show_header=True,
        show_lines=False,
        header_style=f"bold {DIM}",
        border_style=DIM,
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        padding=(0, 2),
    )


# ---------------------------------------------------------------------------
# Spinner factory — returns a Progress configured with the relic pulse.
# Use as a context manager around blocking work.
# ---------------------------------------------------------------------------

def make_spinner(label: str):
    """Return a Rich Progress with the relic hexagon pulse + a single
    description column. `transient=True` removes the line on completion
    so the next `success()` call replaces it cleanly.
    """
    from rich.progress import Progress, SpinnerColumn, TextColumn

    return Progress(
        SpinnerColumn(spinner_name=SPINNER_NAME, style=PRIMARY),
        TextColumn(f"[{FG}]{label}[/]"),
        console=console,
        transient=True,
    )
