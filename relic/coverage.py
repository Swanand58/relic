"""Coverage report — surface what relic indexes vs silently skips.

Without this, a file that was too large or had no parser would just disappear
from query results, making missing context look like a model error instead of
a tool limit.

The walker mirrors `indexer._collect_source_files` exactly (same SKIP_DIRS,
same MAX_FILE_BYTES, same LANGUAGE_MAP) but classifies each file into one of
four buckets instead of silently dropping the skipped ones:

    indexed       — would be picked up by `relic index`
    no_parser     — extension not in LANGUAGE_MAP (.md, .json, .toml, …)
    too_large     — exceeds MAX_FILE_BYTES (200 KB)
    symlink       — symbolic link (skipped for path-traversal safety)
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from relic import style
from relic.indexer import LANGUAGE_MAP, MAX_FILE_BYTES, SKIP_DIRS

# Show this many examples per skip reason in the rendered report.
_EXAMPLE_LIMIT = 5


def _classify_tree(root: Path, project_root: Path) -> dict:
    """Walk *root* and classify each file into indexed/skipped buckets."""
    entry: dict = {
        "missing": False,
        "indexed": [],
        "skipped": {"no_parser": [], "too_large": [], "symlink": []},
    }
    if not root.exists():
        entry["missing"] = True
        return entry

    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue

        if p.is_symlink():
            entry["skipped"]["symlink"].append(_safe_rel(p, project_root))
            continue

        if not p.is_file():
            continue

        rel = _safe_rel(p, project_root)

        if p.suffix not in LANGUAGE_MAP:
            entry["skipped"]["no_parser"].append(rel)
            continue

        try:
            size = p.stat().st_size
        except OSError:
            continue

        if size > MAX_FILE_BYTES:
            entry["skipped"]["too_large"].append((rel, size))
            continue

        entry["indexed"].append(rel)

    return entry


def compute_coverage(project_root: Path, subprojects: dict | None = None) -> dict:
    """Classify every file as indexed or skipped, with reasons.

    When *subprojects* is provided (from relic.yaml), each subproject is
    reported separately.  When ``None`` or empty (zero-config mode), the
    entire project tree is walked as a single ``"(project)"`` entry.

    Returns a dict shaped::

        {
          "subprojects": { <name>: { "missing", "indexed", "skipped" }, ... },
          "totals": { "indexed", "no_parser", "too_large", "symlink" },
        }

    Files inside SKIP_DIRS (.git, node_modules, build, etc) are not surfaced —
    those exclusions are intentional and not actionable for the user.
    """
    report: dict[str, dict] = {}
    totals = {"indexed": 0, "no_parser": 0, "too_large": 0, "symlink": 0}

    if subprojects:
        for name, cfg in subprojects.items():
            sub_path = (project_root / cfg["path"]).resolve()
            entry = _classify_tree(sub_path, project_root)
            report[name] = entry
    else:
        entry = _classify_tree(project_root, project_root)
        report["(project)"] = entry

    for entry in report.values():
        if entry["missing"]:
            continue
        totals["indexed"] += len(entry["indexed"])
        totals["no_parser"] += len(entry["skipped"]["no_parser"])
        totals["too_large"] += len(entry["skipped"]["too_large"])
        totals["symlink"] += len(entry["skipped"]["symlink"])

    return {"subprojects": report, "totals": totals}


def _safe_rel(path: Path, root: Path) -> str:
    """Return POSIX-style path relative to root, falling back to absolute if outside."""
    try:
        from pathlib import PurePosixPath

        return PurePosixPath(path.relative_to(root)).as_posix()
    except ValueError:
        return str(path)


def render_coverage(coverage: dict, console: Console, verbose: bool = False) -> None:
    """Render the coverage report to a Rich console.

    With verbose=False (default), each skip bucket shows up to _EXAMPLE_LIMIT
    sample paths. With verbose=True, every skipped file is listed — useful for
    auditing why a specific file is missing from the index.
    """
    totals = coverage["totals"]
    total_seen = sum(totals.values())
    coverage_pct = (totals["indexed"] / total_seen * 100) if total_seen else 100.0

    console.print(style.header("coverage"))
    console.print()
    kw = 22  # widest key here is "Skipped (no parser)" at 19 chars
    console.print(style.kv("Files indexed", totals["indexed"], key_width=kw))
    console.print(style.kv("Skipped (no parser)", totals["no_parser"], key_width=kw))
    console.print(
        style.kv(
            f"Skipped (>{MAX_FILE_BYTES // 1000} KB)",
            totals["too_large"],
            key_width=kw,
        )
    )
    console.print(style.kv("Skipped (symlinks)", totals["symlink"], key_width=kw))
    console.print(style.kv("Coverage %", f"{coverage_pct:.1f}", key_width=kw))

    for name, entry in coverage["subprojects"].items():
        if entry["missing"]:
            console.print()
            console.print(style.warn(f"[bold {style.WARN}]{name}[/]: path missing — skipped."))
            continue

        skipped = entry["skipped"]
        no_parser = skipped["no_parser"]
        too_large = skipped["too_large"]
        symlink = skipped["symlink"]

        sub_total = len(entry["indexed"]) + len(no_parser) + len(too_large) + len(symlink)

        console.print()
        console.print(
            f"[bold {style.SECONDARY}]{name}[/]  "
            f"[bold {style.FG}]{len(entry['indexed'])}[/][{style.DIM}]/{sub_total} indexed[/]  "
            f"[{style.DIM}]({len(no_parser)} no_parser, "
            f"{len(too_large)} too_large, "
            f"{len(symlink)} symlinks)[/]"
        )

        _render_bucket(console, "Skipped (no parser)", no_parser, verbose)
        _render_bucket(
            console,
            "Skipped (too large)",
            [f"{path}  ({size // 1000} KB)" for path, size in too_large],
            verbose,
        )
        _render_bucket(console, "Skipped (symlink)", symlink, verbose)


def _render_bucket(console: Console, title: str, items: list[str], verbose: bool) -> None:
    """Print up to _EXAMPLE_LIMIT items (or all when verbose)."""
    if not items:
        return
    visible = items if verbose else items[:_EXAMPLE_LIMIT]
    console.print(f"   [{style.DIM}]{title}:[/]")
    for item in visible:
        console.print(f"     [{style.FG}]{item}[/]")
    if not verbose and len(items) > _EXAMPLE_LIMIT:
        console.print(f"     [{style.DIM}]… and {len(items) - _EXAMPLE_LIMIT} more (use --verbose)[/]")
