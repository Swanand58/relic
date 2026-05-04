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


def compute_coverage(project_root: Path, subprojects: dict) -> dict:
    """Walk every subproject and classify each file.

    Returns a dict shaped:
        {
          "subprojects": {
            <name>: {
              "missing": bool,                    # subproject path doesn't exist
              "indexed": list[str],               # relative paths
              "skipped": {
                "no_parser": list[str],
                "too_large": list[tuple[str, int]],   # (path, size_bytes)
                "symlink":   list[str],
              }
            },
            ...
          },
          "totals": {
            "indexed": int,
            "no_parser": int,
            "too_large": int,
            "symlink": int,
          }
        }

    Files inside SKIP_DIRS (.git, node_modules, build, etc) are not surfaced —
    those exclusions are intentional and not actionable for the user.
    """
    report: dict[str, dict] = {}
    totals = {"indexed": 0, "no_parser": 0, "too_large": 0, "symlink": 0}

    for name, cfg in subprojects.items():
        sub_path = (project_root / cfg["path"]).resolve()
        entry: dict = {
            "missing": False,
            "indexed": [],
            "skipped": {"no_parser": [], "too_large": [], "symlink": []},
        }

        if not sub_path.exists():
            entry["missing"] = True
            report[name] = entry
            continue

        for p in sorted(sub_path.rglob("*")):
            # Honour the same SKIP_DIRS exclusion as the indexer. We don't
            # report on these because they're well-known opt-outs, not files
            # the user might be surprised to see missing from the index.
            if any(part in SKIP_DIRS for part in p.parts):
                continue

            if p.is_symlink():
                rel = _safe_rel(p, project_root)
                entry["skipped"]["symlink"].append(rel)
                totals["symlink"] += 1
                continue

            if not p.is_file():
                continue

            rel = _safe_rel(p, project_root)

            if p.suffix not in LANGUAGE_MAP:
                entry["skipped"]["no_parser"].append(rel)
                totals["no_parser"] += 1
                continue

            try:
                size = p.stat().st_size
            except OSError:
                continue

            if size > MAX_FILE_BYTES:
                entry["skipped"]["too_large"].append((rel, size))
                totals["too_large"] += 1
                continue

            entry["indexed"].append(rel)
            totals["indexed"] += 1

        report[name] = entry

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
