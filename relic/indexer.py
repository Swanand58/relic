"""Indexer — static analysis engine that builds a NetworkX knowledge graph.

No LLM involved. Extracts:
- File nodes with language and subproject metadata
- Symbol nodes (classes, functions, interfaces, types)
- Import edges between files
- Define edges from files to their symbols
- Extends edges between symbols

Languages supported:
- Python     — stdlib ast (accurate)
- TypeScript / JS / TSX / JSX — regex (covers 90% of real-world code)
- Other      — file nodes only, no symbol extraction

Graph schema
------------
Node ID conventions:
    Files:   relative path string   e.g. "src/core/PageDocument.ts"
    Symbols: "{name}@{path}"        e.g. "PageDocument@src/core/PageDocument.ts"

Node attributes:
    ntype    : "file" | "symbol"
    --- file nodes ---
    path     : relative path (same as node ID)
    language : "python" | "typescript" | "javascript" | "other"
    subproject: subproject name from relic.yaml (or "" if unknown)
    --- symbol nodes ---
    name     : symbol name
    stype    : "class" | "function" | "interface" | "type" | "variable"
    path     : file that defines this symbol
    line     : line number (0 if unknown)

Edge types:
    imports  : file  → file    (file A imports from file B)
    defines  : file  → symbol  (file A defines symbol S)
    extends  : symbol → symbol (class A extends class B)
"""

import ast
import pickle
import re
from pathlib import Path, PurePosixPath

import networkx as nx
import yaml


def _posix_rel(path: Path, root: Path) -> str:
    """Return a POSIX-style relative path (forward slashes on all platforms)."""
    return PurePosixPath(path.relative_to(root)).as_posix()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS = {
    ".git",
    ".github",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".tox",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".nuxt",
    "coverage",
    "htmlcov",
    ".knowledge",
    "test-results",
}

LANGUAGE_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

MAX_FILE_BYTES = 200_000


# ---------------------------------------------------------------------------
# Python analyser
# ---------------------------------------------------------------------------


def _analyse_python(source: str, rel_path: str, project_root: Path) -> tuple[list[str], list[dict]]:
    """Parse Python source with ast.

    Returns:
        imports  — list of resolved relative paths this file imports
        symbols  — list of dicts {name, stype, line}
    """
    imports: list[str] = []
    symbols: list[dict] = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports, symbols

    file_dir = (project_root / rel_path).parent

    for node in ast.walk(tree):
        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_python_import(alias.name, file_dir, project_root)
                if resolved:
                    imports.append(resolved)

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                resolved = _resolve_python_import(
                    node.module,
                    file_dir,
                    project_root,
                    level=node.level or 0,
                )
                if resolved:
                    imports.append(resolved)

        # Symbols — top-level only to avoid noise
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef):
                stype = "class"
            else:
                stype = "function"
            symbols.append({"name": node.name, "stype": stype, "line": node.lineno})

    return imports, symbols


def _resolve_python_import(module: str, file_dir: Path, project_root: Path, level: int = 0) -> str | None:
    """Try to resolve a Python import to a relative file path within the project."""
    if level > 0:
        # Relative import
        base = file_dir
        for _ in range(level - 1):
            base = base.parent
        parts = module.split(".") if module else []
        candidate = base.joinpath(*parts)
    else:
        parts = module.split(".")
        candidate = project_root.joinpath(*parts)

    # Try as package or module
    for suffix in [".py", "/__init__.py"]:
        full = Path(str(candidate) + suffix) if not suffix.startswith("/") else candidate / "__init__.py"
        if suffix == ".py":
            full = Path(str(candidate) + ".py")
        else:
            full = candidate / "__init__.py"
        if full.exists():
            return _posix_rel(full, project_root)
    return None


# ---------------------------------------------------------------------------
# TypeScript / JavaScript analyser
# ---------------------------------------------------------------------------

# import X from './foo'  |  import { X } from '../bar'  |  import type ...
_TS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?(?:\*\s+as\s+\w+|\{[^}]*\}|\w+)?\s*(?:,\s*(?:\{[^}]*\}|\w+))?\s*from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)
# require('./foo')
_TS_REQUIRE_RE = re.compile(r"""require\(['"]([^'"]+)['"]\)""")

# export class Foo / class Foo
_TS_CLASS_RE = re.compile(r"""(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?""")
# export function foo / function foo / export const foo = / export async function
_TS_FUNC_RE = re.compile(r"""(?:export\s+)?(?:async\s+)?function\s+(\w+)""")
_TS_ARROW_RE = re.compile(r"""(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(""")
# interface Foo / export interface Foo
_TS_IFACE_RE = re.compile(r"""(?:export\s+)?interface\s+(\w+)""")
# type Foo = / export type Foo =
_TS_TYPE_RE = re.compile(r"""(?:export\s+)?type\s+(\w+)\s*=""")


def _analyse_typescript(source: str, rel_path: str, project_root: Path) -> tuple[list[str], list[dict]]:
    """Parse TypeScript/JS source with regex.

    Returns:
        imports  — list of resolved relative paths this file imports
        symbols  — list of dicts {name, stype, line, extends}
    """
    imports: list[str] = []
    symbols: list[dict] = []

    file_dir = (project_root / rel_path).parent
    lines = source.splitlines()

    # Build line-number index for symbols
    for i, line in enumerate(lines, 1):
        for m in _TS_CLASS_RE.finditer(line):
            sym = {"name": m.group(1), "stype": "class", "line": i}
            if m.group(2):
                sym["extends"] = m.group(2)
            symbols.append(sym)
        for m in _TS_FUNC_RE.finditer(line):
            symbols.append({"name": m.group(1), "stype": "function", "line": i})
        for m in _TS_ARROW_RE.finditer(line):
            symbols.append({"name": m.group(1), "stype": "function", "line": i})
        for m in _TS_IFACE_RE.finditer(line):
            symbols.append({"name": m.group(1), "stype": "interface", "line": i})
        for m in _TS_TYPE_RE.finditer(line):
            symbols.append({"name": m.group(1), "stype": "type", "line": i})

    # Imports
    for pattern in (_TS_IMPORT_RE, _TS_REQUIRE_RE):
        for m in pattern.finditer(source):
            spec = m.group(1)
            resolved = _resolve_ts_import(spec, file_dir, project_root)
            if resolved:
                imports.append(resolved)

    return imports, symbols


_TS_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]


def _resolve_ts_import(spec: str, file_dir: Path, project_root: Path) -> str | None:
    """Resolve a TS/JS import specifier to a relative project path."""
    if not spec.startswith("."):
        return None  # external package

    raw = (file_dir / spec).resolve()

    # Try exact path first
    if raw.exists() and raw.is_file():
        try:
            return _posix_rel(raw, project_root)
        except ValueError:
            return None

    # Try adding extensions
    for ext in _TS_EXTENSIONS:
        candidate = Path(str(raw) + ext)
        if candidate.exists():
            try:
                return _posix_rel(candidate, project_root)
            except ValueError:
                return None

    # Try as directory with index file
    for ext in _TS_EXTENSIONS:
        candidate = raw / f"index{ext}"
        if candidate.exists():
            try:
                return _posix_rel(candidate, project_root)
            except ValueError:
                return None

    return None


# ---------------------------------------------------------------------------
# Core graph builder
# ---------------------------------------------------------------------------


def _collect_source_files(project_root: Path, subprojects: dict) -> list[tuple[Path, str]]:
    """Return list of (absolute_path, subproject_name) for all indexable files."""
    results = []
    seen = set()

    for name, cfg in subprojects.items():
        sub_path = (project_root / cfg["path"]).resolve()
        if not sub_path.exists():
            continue
        for p in sorted(sub_path.rglob("*")):
            if p.is_symlink() or not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.suffix not in LANGUAGE_MAP and p.suffix not in {".py"}:
                continue
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            key = str(p.resolve())
            if key not in seen:
                seen.add(key)
                results.append((p, name))

    return results


def build_graph(project_root: Path, subprojects: dict) -> nx.DiGraph:
    """Build and return the full knowledge graph as a NetworkX DiGraph.

    Runs static analysis on all source files in all subprojects.
    """
    G = nx.DiGraph()

    files = _collect_source_files(project_root, subprojects)

    # First pass — add all file nodes
    for abs_path, subproject in files:
        rel = _posix_rel(abs_path, project_root)
        lang = LANGUAGE_MAP.get(abs_path.suffix, "other")
        G.add_node(rel, ntype="file", path=rel, language=lang, subproject=subproject)

    file_nodes = set(G.nodes)

    # Second pass — analyse each file
    for abs_path, subproject in files:
        rel = _posix_rel(abs_path, project_root)
        lang = LANGUAGE_MAP.get(abs_path.suffix, "other")

        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            continue

        if lang == "python":
            imports, symbols = _analyse_python(source, rel, project_root)
        elif lang in ("typescript", "javascript"):
            imports, symbols = _analyse_typescript(source, rel, project_root)
        else:
            imports, symbols = [], []

        # Import edges — only to files that exist in the project
        for imp in imports:
            if imp in file_nodes and imp != rel:
                G.add_edge(rel, imp, etype="imports")

        # Symbol nodes + define edges
        symbol_map: dict[str, str] = {}  # name → symbol node ID
        for sym in symbols:
            sid = f"{sym['name']}@{rel}"
            G.add_node(
                sid,
                ntype="symbol",
                name=sym["name"],
                stype=sym["stype"],
                path=rel,
                line=sym.get("line", 0),
            )
            G.add_edge(rel, sid, etype="defines")
            symbol_map[sym["name"]] = sid

        # Extends edges (TS only)
        for sym in symbols:
            if "extends" in sym:
                parent_name = sym["extends"]
                child_sid = f"{sym['name']}@{rel}"
                # Try to find parent in this file first, then anywhere in graph
                parent_sid = symbol_map.get(parent_name)
                if parent_sid is None:
                    # Search across all symbol nodes
                    for node, data in G.nodes(data=True):
                        if data.get("ntype") == "symbol" and data.get("name") == parent_name:
                            parent_sid = node
                            break
                if parent_sid:
                    G.add_edge(child_sid, parent_sid, etype="extends")

    return G


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_graph(G: nx.DiGraph, knowledge_dir: Path) -> None:
    """Serialize the graph to .knowledge/index.pkl."""
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    pkl_path = knowledge_dir / "index.pkl"
    with pkl_path.open("wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_graph(knowledge_dir: Path) -> nx.DiGraph:
    """Load the graph from .knowledge/index.pkl.

    Raises FileNotFoundError if the index does not exist.
    """
    pkl_path = knowledge_dir / "index.pkl"
    if not pkl_path.exists():
        raise FileNotFoundError("No index found. Run `relic index` first.")
    with pkl_path.open("rb") as f:
        return pickle.load(f)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def compute_stats(G: nx.DiGraph, knowledge_dir: Path) -> dict:
    """Return health metrics for the loaded knowledge graph.

    Single source of truth for both `relic stats` (CLI) and `relic_stats`
    (MCP tool) so the two paths can never drift.

    Keys:
        last_updated     ISO-ish timestamp of index.pkl mtime, or "unknown"
        files            count of file nodes
        symbols          count of symbol nodes
        edges            total edge count
        edges_by_type    dict mapping edge type → count
        subprojects      sorted list of subproject names present in the graph
    """
    import datetime

    index_path = knowledge_dir / "index.pkl"
    if index_path.exists():
        last_updated = datetime.datetime.fromtimestamp(index_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    else:
        last_updated = "unknown"

    files = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file")
    symbols = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "symbol")
    edges = G.number_of_edges()

    edges_by_type: dict[str, int] = {}
    for _, _, d in G.edges(data=True):
        et = d.get("etype", "unknown")
        edges_by_type[et] = edges_by_type.get(et, 0) + 1

    subprojects: set[str] = set()
    for _, d in G.nodes(data=True):
        sp = d.get("subproject", "")
        if sp:
            subprojects.add(sp)

    return {
        "last_updated": last_updated,
        "files": files,
        "symbols": symbols,
        "edges": edges,
        "edges_by_type": edges_by_type,
        "subprojects": sorted(subprojects),
    }


def run_index(project_root: Path, knowledge_dir: Path, config_file: Path) -> nx.DiGraph:
    """Load relic.yaml, build graph, save to knowledge_dir. Returns the graph."""
    if not config_file.exists():
        raise FileNotFoundError(f"{config_file} not found. Run `relic init` first.")

    with config_file.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    subprojects = cfg.get("subprojects", {})
    if not subprojects:
        raise ValueError("No subprojects defined in relic.yaml.")

    G = build_graph(project_root, subprojects)
    save_graph(G, knowledge_dir)
    return G
