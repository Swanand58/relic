"""Indexer — static analysis engine that builds a NetworkX knowledge graph.

No LLM involved. Extracts:
- File nodes with language metadata
- Symbol nodes (classes, functions, interfaces, types) with signatures
- Import edges between files
- Define edges from files to their symbols
- Extends edges between symbols (Python + TypeScript)
- Test mapping edges (tested_by / tests) via naming conventions

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
    subproject: subproject name from relic.yaml (or "" if unlabeled)
    --- symbol nodes ---
    name     : symbol name
    stype    : "class" | "function" | "interface" | "type" | "variable"
    path     : file that defines this symbol
    line     : line number (0 if unknown)
    signature: parameter/return-type signature (empty string if unavailable)

Edge types:
    imports   : file   → file    (file A imports from file B)
    defines   : file   → symbol  (file A defines symbol S)
    extends   : symbol → symbol  (class A extends class B)
    uses      : file   → symbol  (file A references symbol S from another file)
    calls     : symbol → symbol  (function A calls function B)
    tested_by : file   → file    (source file → its test file)
    tests     : file   → file    (test file → the source it tests)
"""

import ast
import json
import os
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
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}

MAX_FILE_BYTES = 200_000


# ---------------------------------------------------------------------------
# Python analyser
# ---------------------------------------------------------------------------


def _extract_python_signature(node: ast.AST, source_lines: list[str]) -> str:
    """Extract a compact signature from a Python AST def/class node.

    For functions: ``name(params) -> ReturnType``
    For classes:   ``Name(Base1, Base2)``
    """
    if isinstance(node, ast.ClassDef):
        if not node.bases:
            return node.name
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:
                bases.append("?")
        return f"{node.name}({', '.join(bases)})"

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        try:
            args_str = ast.unparse(node.args)
        except Exception:
            args_str = "..."
        ret = ""
        if node.returns:
            try:
                ret = f" -> {ast.unparse(node.returns)}"
            except Exception:
                pass
        return f"{node.name}({args_str}){ret}"

    return ""


def _extract_python_intent(node: ast.AST) -> str:
    """First line of docstring, max 80 chars. Empty if none."""
    raw = ast.get_docstring(node) or ""
    if not raw:
        return ""
    first = raw.split("\n")[0].strip()
    return (first[:79] + "…") if len(first) > 79 else first


_LITERAL_MIN_LEN = 8
_LITERAL_MAX_LEN = 200
_LITERALS_PER_SYMBOL = 20


_RICH_MARKUP_RE = re.compile(r"\[/?[a-zA-Z][a-zA-Z0-9 _]*\]")


def _strip_markup(s: str) -> str:
    """Strip Rich/ANSI markup tags like [bold red] or [/dim] from a string."""
    return _RICH_MARKUP_RE.sub("", s).strip()


def _extract_python_literals(node: ast.AST, docstring: str) -> list[dict]:
    """String literals ≥8 chars inside a node body, skipping the docstring."""
    seen: set[str] = set()
    result: list[dict] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
            continue
        val = _strip_markup(child.value)
        if len(val) < _LITERAL_MIN_LEN or val == docstring:
            continue
        truncated = (val[: _LITERAL_MAX_LEN - 1] + "…") if len(val) > _LITERAL_MAX_LEN else val
        key = truncated[:80]
        if key in seen:
            continue
        seen.add(key)
        result.append({"value": truncated, "line": getattr(child, "lineno", 0)})
    result.sort(key=lambda x: len(x["value"]), reverse=True)
    return result[:_LITERALS_PER_SYMBOL]


def _extract_python_decorators(node: ast.AST) -> list[dict]:
    """Decorators with literal args only. Max 5, most-recently-applied first."""
    result: list[dict] = []
    for dec in getattr(node, "decorator_list", []):
        if isinstance(dec, ast.Name):
            result.append({"name": dec.id, "args": []})
        elif isinstance(dec, ast.Attribute):
            try:
                result.append({"name": ast.unparse(dec), "args": []})
            except Exception:
                pass
        elif isinstance(dec, ast.Call):
            name = ""
            if isinstance(dec.func, ast.Name):
                name = dec.func.id
            elif isinstance(dec.func, ast.Attribute):
                try:
                    name = ast.unparse(dec.func)
                except Exception:
                    pass
            if not name:
                continue
            args: list = []
            for arg in dec.args:
                if isinstance(arg, ast.Constant):
                    args.append(arg.value)
            for kw in dec.keywords:
                if isinstance(kw.value, ast.Constant):
                    args.append(kw.value.value)
            result.append({"name": name, "args": args})
    result.reverse()  # most-recently-applied first
    return result[:5]


def _extract_python_calls(func_node: ast.AST) -> list[str]:
    """Extract callee names from a function/method body.

    Resolves ``ast.Call`` targets:
    - ``Name`` nodes → ``foo``
    - ``Attribute`` nodes → ``bar`` (the attribute, not ``obj.bar``)

    Only top-level call names are returned; nested attribute chains like
    ``a.b.c()`` resolve to ``c``.  The caller is responsible for matching
    these against known symbol names in the graph.
    """
    callees: list[str] = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            callees.append(func.id)
        elif isinstance(func, ast.Attribute):
            callees.append(func.attr)
    return callees


def _analyse_python(
    source: str, rel_path: str, project_root: Path
) -> tuple[list[str], list[dict], list[tuple[str, str]], list[tuple[str, str]]]:
    """Parse Python source with ast.

    Returns:
        imports        — list of resolved relative paths this file imports
        symbols        — list of dicts {name, stype, line, signature, extends?}
        imported_names — list of (resolved_path, symbol_name) from ``from X import Y``
        calls          — list of (caller_symbol_name, callee_name) pairs
    """
    imports: list[str] = []
    symbols: list[dict] = []
    imported_names: list[tuple[str, str]] = []
    calls: list[tuple[str, str]] = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports, symbols, imported_names, calls

    source_lines = source.splitlines()
    file_dir = (project_root / rel_path).parent

    for node in ast.walk(tree):
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
                    if node.names:
                        for alias in node.names:
                            if alias.name != "*":
                                imported_names.append((resolved, alias.name))

        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.ClassDef):
                stype = "class"
            else:
                stype = "function"
            docstring = ast.get_docstring(node) or ""
            sym: dict = {
                "name": node.name,
                "stype": stype,
                "line": node.lineno,
                "signature": _extract_python_signature(node, source_lines),
                "intent": _extract_python_intent(node),
                "decorators": _extract_python_decorators(node),
                "literals": _extract_python_literals(node, docstring),
            }
            if isinstance(node, ast.ClassDef) and node.bases:
                for b in node.bases:
                    try:
                        sym["extends"] = ast.unparse(b)
                        break
                    except Exception:
                        pass
            symbols.append(sym)

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for callee in _extract_python_calls(node):
                    if callee != node.name:
                        calls.append((node.name, callee))

    return imports, symbols, imported_names, calls


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


def _ts_func_sig(line: str, name: str) -> str:
    """Extract a compact TS function signature from the source line.

    Tries to capture ``name(params): RetType`` from the declaration line.
    Falls back to just the name if parsing is ambiguous.
    """
    idx = line.find(name)
    if idx == -1:
        return name
    rest = line[idx:]
    paren_start = rest.find("(")
    if paren_start == -1:
        return name
    depth = 0
    for j, ch in enumerate(rest[paren_start:], paren_start):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                after = rest[j + 1 :].strip()
                if after.startswith(":"):
                    ret_type = after[1:].split("{")[0].split("=>")[0].strip().rstrip(",;")
                    if ret_type:
                        return f"{rest[: j + 1]} -> {ret_type}"
                return rest[: j + 1]
    return name


_TS_NAMED_IMPORT_RE = re.compile(
    r"""import\s+(?:type\s+)?\{([^}]+)\}\s*from\s+['"]([^'"]+)['"]""",
    re.VERBOSE,
)

_TS_DECORATOR_RE = re.compile(r"@([\w][\w.]*)\s*(?:\(([^)]*)\))?$")


def _extract_ts_intent(lines: list[str], sym_line_1indexed: int) -> str:
    """First stripped line of a JSDoc or // comment immediately before sym_line."""
    idx = sym_line_1indexed - 2
    if idx < 0:
        return ""
    line = lines[idx].strip()
    if not line:
        return ""
    if line.startswith("//"):
        text = line.lstrip("/ ").strip()
        return (text[:79] + "…") if len(text) > 79 else text
    if line == "*/" or line.endswith("*/"):
        while idx >= 0:
            ln = lines[idx].strip()
            if ln.startswith("/**") or ln.startswith("/*"):
                text = ln.lstrip("/*").rstrip("*/").strip()
                if text and not text.startswith("@"):
                    return (text[:79] + "…") if len(text) > 79 else text
                break
            text = ln.lstrip("* ").rstrip("*/").strip()
            if text and not text.startswith("@"):
                return (text[:79] + "…") if len(text) > 79 else text
            idx -= 1
    return ""


def _extract_ts_decorators(lines: list[str], sym_line_1indexed: int) -> list[dict]:
    """@decorator(literal_args) lines immediately before sym_line. Max 5."""
    result: list[dict] = []
    idx = sym_line_1indexed - 2
    while idx >= 0:
        line = lines[idx].strip()
        m = _TS_DECORATOR_RE.match(line)
        if m:
            name = m.group(1)
            args_str = m.group(2) or ""
            args = [s.strip().strip("\"'") for s in re.findall(r"""['"][^'"]+['"]""", args_str)]
            result.insert(0, {"name": name, "args": args})
            idx -= 1
        elif not line or line.startswith("//") or line.startswith("*"):
            idx -= 1
        else:
            break
    return result[:5]


_TS_CALL_RE = re.compile(r"""\b(\w+)\s*\(""")


def _extract_ts_calls(source: str, own_symbols: set[str], imported_symbols: set[str]) -> list[tuple[str, str]]:
    """Extract call-like patterns from TS/JS source.

    Since we lack AST-level function body scoping, we scan the entire file
    for ``identifier(`` patterns and match against known symbol names
    (both local and imported).  Returns (caller_function, callee) pairs
    where caller_function is the enclosing function/arrow declaration.
    """
    calls: list[tuple[str, str]] = []
    known = own_symbols | imported_symbols
    lines = source.splitlines()
    current_func: str | None = None

    for line in lines:
        stripped = line.strip()
        fm = _TS_FUNC_RE.search(line)
        am = _TS_ARROW_RE.search(line)
        if fm:
            current_func = fm.group(1)
        elif am:
            current_func = am.group(1)

        if not current_func:
            continue
        if stripped.startswith("import ") or stripped.startswith("export ") and "from" in stripped:
            continue

        for m in _TS_CALL_RE.finditer(line):
            callee = m.group(1)
            if callee in known and callee != current_func:
                calls.append((current_func, callee))

    return calls


def _analyse_typescript(
    source: str, rel_path: str, project_root: Path
) -> tuple[list[str], list[dict], list[tuple[str, str]], list[tuple[str, str]]]:
    """Parse TypeScript/JS source with regex.

    Returns:
        imports        — list of resolved relative paths this file imports
        symbols        — list of dicts {name, stype, line, extends}
        imported_names — list of (resolved_path, symbol_name) from named imports
        calls          — list of (caller_symbol_name, callee_name) pairs
    """
    imports: list[str] = []
    symbols: list[dict] = []
    imported_names: list[tuple[str, str]] = []

    file_dir = (project_root / rel_path).parent
    lines = source.splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        for m in _TS_CLASS_RE.finditer(line):
            name = m.group(1)
            sig = f"{name}({m.group(2)})" if m.group(2) else name
            sym = {
                "name": name,
                "stype": "class",
                "line": i,
                "signature": sig,
                "intent": _extract_ts_intent(lines, i),
                "decorators": _extract_ts_decorators(lines, i),
            }
            if m.group(2):
                sym["extends"] = m.group(2)
            symbols.append(sym)
        for m in _TS_FUNC_RE.finditer(line):
            sig = _ts_func_sig(stripped, m.group(1))
            symbols.append(
                {
                    "name": m.group(1),
                    "stype": "function",
                    "line": i,
                    "signature": sig,
                    "intent": _extract_ts_intent(lines, i),
                    "decorators": _extract_ts_decorators(lines, i),
                }
            )
        for m in _TS_ARROW_RE.finditer(line):
            sig = _ts_func_sig(stripped, m.group(1))
            symbols.append(
                {
                    "name": m.group(1),
                    "stype": "function",
                    "line": i,
                    "signature": sig,
                    "intent": _extract_ts_intent(lines, i),
                    "decorators": _extract_ts_decorators(lines, i),
                }
            )
        for m in _TS_IFACE_RE.finditer(line):
            symbols.append(
                {
                    "name": m.group(1),
                    "stype": "interface",
                    "line": i,
                    "signature": m.group(1),
                    "intent": _extract_ts_intent(lines, i),
                    "decorators": _extract_ts_decorators(lines, i),
                }
            )
        for m in _TS_TYPE_RE.finditer(line):
            symbols.append(
                {
                    "name": m.group(1),
                    "stype": "type",
                    "line": i,
                    "signature": m.group(1),
                    "intent": _extract_ts_intent(lines, i),
                    "decorators": _extract_ts_decorators(lines, i),
                }
            )

    for pattern in (_TS_IMPORT_RE, _TS_REQUIRE_RE):
        for m in pattern.finditer(source):
            spec = m.group(1)
            resolved = _resolve_ts_import(spec, file_dir, project_root)
            if resolved:
                imports.append(resolved)

    for m in _TS_NAMED_IMPORT_RE.finditer(source):
        names_str, spec = m.group(1), m.group(2)
        resolved = _resolve_ts_import(spec, file_dir, project_root)
        if resolved:
            for name_part in names_str.split(","):
                name_part = name_part.strip()
                if " as " in name_part:
                    name_part = name_part.split(" as ")[0].strip()
                if name_part:
                    imported_names.append((resolved, name_part))

    own_sym_names = {s["name"] for s in symbols}
    imported_sym_names = {n for _, n in imported_names}
    calls = _extract_ts_calls(source, own_sym_names, imported_sym_names)

    return imports, symbols, imported_names, calls


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


_TEST_DIRS = {"tests", "test", "__tests__", "spec"}


def _load_relicignore(project_root: Path) -> list[str]:
    """Load glob patterns from .relicignore (if it exists).

    Blank lines and lines starting with ``#`` are ignored.
    """
    ignore_file = project_root / ".relicignore"
    if not ignore_file.exists():
        return []
    patterns: list[str] = []
    for line in ignore_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return patterns


def _matches_ignore(rel_posix: str, patterns: list[str]) -> bool:
    """Return True if a POSIX relative path matches any .relicignore pattern."""
    p = PurePosixPath(rel_posix)
    for pattern in patterns:
        if p.match(pattern):
            return True
        # Also match directory-style patterns like "generated/"
        if pattern.endswith("/") and rel_posix.startswith(pattern.rstrip("/")):
            return True
    return False


def _collect_source_files(project_root: Path, subprojects: dict | None = None) -> tuple[list[tuple[Path, str]], dict]:
    """Walk entire project tree and return collected files plus skip statistics.

    Returns:
        files      — list of (absolute_path, subproject_label) pairs
        skip_stats — dict with keys: skipped_dirs (set of dir names found),
                     ignored_count (files matched by .relicignore)
    """
    subprojects = subprojects or {}

    # Pre-resolve subproject paths for matching
    sp_resolved: list[tuple[str, Path]] = []
    for name, cfg in subprojects.items():
        sp_resolved.append((name, (project_root / cfg["path"]).resolve()))

    ignore_patterns = _load_relicignore(project_root)

    results: list[tuple[Path, str]] = []
    seen: set[str] = set()
    skipped_dirs: set[str] = set()
    ignored_count = 0

    for p in sorted(project_root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue

        rel_parts = p.relative_to(project_root).parts
        hit = SKIP_DIRS.intersection(rel_parts)
        if hit:
            skipped_dirs.update(hit)
            continue

        if p.suffix not in LANGUAGE_MAP:
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue

        rel_posix = _posix_rel(p, project_root)
        if ignore_patterns and _matches_ignore(rel_posix, ignore_patterns):
            ignored_count += 1
            continue

        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)

        # Tag with subproject label if the file falls under a declared path
        label = ""
        resolved_p = p.resolve()
        for sp_name, sp_path in sp_resolved:
            try:
                resolved_p.relative_to(sp_path)
                label = sp_name
                break
            except ValueError:
                continue
        results.append((p, label))

    skip_stats = {
        "skipped_dirs": skipped_dirs,
        "ignored_count": ignored_count,
    }
    return results, skip_stats


_TEST_PREFIXES = ("test_", "tests_")
_TEST_SUFFIXES = ("_test", ".test", ".spec")


def _test_candidate_names(path: str) -> list[str]:
    """Given a source file path, return possible test file paths (POSIX)."""
    from pathlib import PurePosixPath

    p = PurePosixPath(path)
    stem = p.stem
    suffix = p.suffix
    parent = str(p.parent)
    candidates: list[str] = []

    # test_foo.py in same dir, tests/ dir, test/ dir
    for prefix in _TEST_PREFIXES:
        candidates.append(f"{parent}/{prefix}{stem}{suffix}")
    for test_dir in ("tests", "test"):
        for prefix in _TEST_PREFIXES:
            candidates.append(f"{test_dir}/{prefix}{stem}{suffix}")
            if parent != ".":
                candidates.append(f"{parent}/{test_dir}/{prefix}{stem}{suffix}")

    # foo_test.py, foo.test.ts, foo.spec.ts in same dir
    for sfx in _TEST_SUFFIXES:
        candidates.append(f"{parent}/{stem}{sfx}{suffix}")

    # __tests__/foo.ts (JS/TS convention)
    candidates.append(f"{parent}/__tests__/{stem}{suffix}")

    return candidates


def _source_candidate_names(test_path: str) -> list[str]:
    """Given a test file path, return possible source file paths (POSIX)."""
    from pathlib import PurePosixPath

    p = PurePosixPath(test_path)
    stem = p.stem
    suffix = p.suffix
    parent = str(p.parent)
    candidates: list[str] = []

    # Strip test_ prefix
    for prefix in _TEST_PREFIXES:
        if stem.startswith(prefix):
            base = stem[len(prefix) :]
            candidates.append(f"{parent}/{base}{suffix}")
            # source might be one dir up from tests/
            pp = PurePosixPath(parent)
            if pp.name in ("tests", "test", "__tests__"):
                candidates.append(f"{pp.parent}/{base}{suffix}")

    # Strip _test, .test, .spec suffix
    for sfx in _TEST_SUFFIXES:
        if stem.endswith(sfx):
            base = stem[: -len(sfx)]
            candidates.append(f"{parent}/{base}{suffix}")
            pp = PurePosixPath(parent)
            if pp.name in ("tests", "test", "__tests__"):
                candidates.append(f"{pp.parent}/{base}{suffix}")

    return candidates


def _is_test_file(path: str) -> bool:
    """Heuristic: does this path look like a test file?"""
    from pathlib import PurePosixPath

    stem = PurePosixPath(path).stem
    for prefix in _TEST_PREFIXES:
        if stem.startswith(prefix):
            return True
    for sfx in _TEST_SUFFIXES:
        if stem.endswith(sfx):
            return True
    parts = PurePosixPath(path).parts
    return "__tests__" in parts


def _add_test_mapping(G: nx.DiGraph) -> None:
    """Add tested_by / tests edges between source and test files."""
    file_nodes = {n for n, d in G.nodes(data=True) if d.get("ntype") == "file"}
    already_paired: set[tuple[str, str]] = set()

    for node in file_nodes:
        if _is_test_file(node):
            for candidate in _source_candidate_names(node):
                if candidate in file_nodes and (candidate, node) not in already_paired:
                    G.add_edge(candidate, node, etype="tested_by")
                    G.add_edge(node, candidate, etype="tests")
                    already_paired.add((candidate, node))
                    break
        else:
            for candidate in _test_candidate_names(node):
                if candidate in file_nodes and (node, candidate) not in already_paired:
                    G.add_edge(node, candidate, etype="tested_by")
                    G.add_edge(candidate, node, etype="tests")
                    already_paired.add((node, candidate))
                    break


_DERIVED_EDGE_TYPES = ("imports", "uses", "calls", "extends", "tested_by", "tests")


def _safe_mtime(path: Path) -> float:
    """Filesystem mtime, or 0.0 if the file is unreadable."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _analyse_one_file(
    abs_path: Path,
    rel_path: str,
    lang: str,
    project_root: Path,
) -> dict | None:
    """Parse a single file and return per-file analysis data.

    Returns ``None`` if the file cannot be read.  Returns an empty-shaped dict
    for unsupported languages (file node still gets created by the caller).
    """
    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except (PermissionError, OSError):
        return None

    if lang == "python":
        imports, symbols, imported_names, raw_calls = _analyse_python(source, rel_path, project_root)
    elif lang in ("typescript", "javascript"):
        imports, symbols, imported_names, raw_calls = _analyse_typescript(source, rel_path, project_root)
    else:
        from relic.parsers.base import get_parser

        ts_parser = get_parser(lang)
        if ts_parser:
            ar = ts_parser.analyse(source, rel_path, project_root)
            imports, symbols, imported_names, raw_calls = ar.imports, ar.symbols, ar.imported_names, ar.calls
        else:
            imports, symbols, imported_names, raw_calls = [], [], [], []

    return {
        "imports": imports,
        "symbols": symbols,
        "imported_names": imported_names,
        "raw_calls": raw_calls,
    }


def _remove_file_node(G: nx.DiGraph, rel_path: str) -> None:
    """Remove a file node and every symbol it defines from the graph."""
    if rel_path not in G.nodes:
        return
    sids = [n for n in list(G.successors(rel_path)) if G.nodes[n].get("ntype") == "symbol"]
    for sid in sids:
        G.remove_node(sid)
    G.remove_node(rel_path)


def _apply_file_data(
    G: nx.DiGraph,
    rel_path: str,
    lang: str,
    subproject: str,
    mtime: float,
    data: dict | None,
) -> None:
    """Add or replace a file node and its symbols using parsed analysis data.

    Stashes per-file analysis (`file_imports`, `file_imported_names`) on the
    file node and per-symbol call/extends data on symbol nodes so cross-file
    edges can be re-derived later without re-parsing source.
    """
    # Drop existing symbol nodes for this file so a rebuild is idempotent.
    if rel_path in G.nodes:
        sids = [n for n in list(G.successors(rel_path)) if G.nodes[n].get("ntype") == "symbol"]
        for sid in sids:
            G.remove_node(sid)

    if data is None:
        # Unreadable — keep a bare file node so coverage / search still see it.
        G.add_node(
            rel_path,
            ntype="file",
            path=rel_path,
            language=lang,
            subproject=subproject,
            mtime=mtime,
            file_imports=[],
            file_imported_names=[],
        )
        return

    G.add_node(
        rel_path,
        ntype="file",
        path=rel_path,
        language=lang,
        subproject=subproject,
        mtime=mtime,
        file_imports=list(data.get("imports", [])),
        file_imported_names=[tuple(p) for p in data.get("imported_names", [])],
    )

    # Index calls per caller name so each symbol carries only its own callees.
    calls_by_caller: dict[str, list[str]] = {}
    for caller_name, callee_name in data.get("raw_calls", []):
        calls_by_caller.setdefault(caller_name, []).append(callee_name)

    for sym in data.get("symbols", []):
        sid = f"{sym['name']}@{rel_path}"
        G.add_node(
            sid,
            ntype="symbol",
            name=sym["name"],
            stype=sym["stype"],
            path=rel_path,
            line=sym.get("line", 0),
            signature=sym.get("signature", ""),
            extends_name=sym.get("extends", ""),
            raw_calls=list(calls_by_caller.get(sym["name"], [])),
            intent=sym.get("intent", ""),
            decorators=sym.get("decorators", []),
            literals=sym.get("literals", []),
        )
        G.add_edge(rel_path, sid, etype="defines")


def _resolve_cross_file_edges(G: nx.DiGraph) -> None:
    """Drop and re-derive every edge type that depends on the global graph.

    Cheap — no parsing.  Uses the per-file analysis data stashed on file and
    symbol nodes by ``_apply_file_data``.
    """
    # Drop derived edges (keep `defines` — that's intra-file structural).
    drop = [(u, v) for u, v, d in G.edges(data=True) if d.get("etype") in _DERIVED_EDGE_TYPES]
    G.remove_edges_from(drop)

    file_nodes: set[str] = {n for n, d in G.nodes(data=True) if d.get("ntype") == "file"}

    syms_by_name: dict[str, list[str]] = {}
    for n, d in G.nodes(data=True):
        if d.get("ntype") == "symbol":
            syms_by_name.setdefault(d["name"], []).append(n)

    # imports + uses (file → file, file → symbol)
    for f in file_nodes:
        d = G.nodes[f]
        for imp in d.get("file_imports", []):
            if imp in file_nodes and imp != f:
                G.add_edge(f, imp, etype="imports")
        for target_file, sym_name in d.get("file_imported_names", []):
            sid = f"{sym_name}@{target_file}"
            if sid in G.nodes:
                G.add_edge(f, sid, etype="uses")

    # extends (symbol → symbol) and calls (symbol → symbol)
    for n, d in list(G.nodes(data=True)):
        if d.get("ntype") != "symbol":
            continue
        own_file = d.get("path", "")

        parent_name = d.get("extends_name") or ""
        if parent_name:
            local_sid = f"{parent_name}@{own_file}"
            if local_sid in G.nodes:
                G.add_edge(n, local_sid, etype="extends")
            else:
                for cand in syms_by_name.get(parent_name, []):
                    G.add_edge(n, cand, etype="extends")
                    break

        for callee_name in d.get("raw_calls", []):
            local_sid = f"{callee_name}@{own_file}"
            if local_sid in G.nodes:
                G.add_edge(n, local_sid, etype="calls")
                continue
            for cand in syms_by_name.get(callee_name, []):
                if G.has_edge(own_file, cand) and G.edges[own_file, cand].get("etype") == "uses":
                    G.add_edge(n, cand, etype="calls")
                    break

    _add_test_mapping(G)


def _build_literal_index(G: nx.DiGraph) -> None:
    """Build inverted string-literal index on G.graph['string_literals'].

    Maps lowercase literal value → list of (original_value, symbol_id, line).
    Used by quoted relic_search queries.
    """
    idx: dict[str, list[tuple[str, str, int]]] = {}
    for n, d in G.nodes(data=True):
        if d.get("ntype") != "symbol":
            continue
        for lit in d.get("literals", []):
            key = lit["value"].lower()
            idx.setdefault(key, []).append((lit["value"], n, lit.get("line", 0)))
    G.graph["string_literals"] = idx


def build_graph(project_root: Path, subprojects: dict | None = None) -> tuple[nx.DiGraph, dict]:
    """Build and return the full knowledge graph plus skip statistics.

    Walks the entire project tree and runs static analysis on all source files.
    If *subprojects* is provided, files are tagged with their subproject name.
    """
    G = nx.DiGraph()
    files, skip_stats = _collect_source_files(project_root, subprojects)

    for abs_path, subproject in files:
        rel = _posix_rel(abs_path, project_root)
        lang = LANGUAGE_MAP.get(abs_path.suffix, "other")
        mtime = _safe_mtime(abs_path)
        data = _analyse_one_file(abs_path, rel, lang, project_root)
        _apply_file_data(G, rel, lang, subproject, mtime, data)

    _resolve_cross_file_edges(G)
    _build_literal_index(G)
    return G, skip_stats


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write *payload* to *path* atomically (tmp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        f.write(payload)
    os.replace(tmp, path)


def save_graph(G: nx.DiGraph, knowledge_dir: Path) -> None:
    """Serialize the graph to .knowledge/index.pkl atomically."""
    payload = pickle.dumps(G, protocol=pickle.HIGHEST_PROTOCOL)
    _atomic_write_bytes(knowledge_dir / "index.pkl", payload)


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
# Mtime sidecar (powers incremental reindex + freshness header)
# ---------------------------------------------------------------------------


def save_mtimes(knowledge_dir: Path, mtimes: dict[str, float]) -> None:
    """Write the per-file mtime sidecar atomically."""
    payload = json.dumps(mtimes, sort_keys=True).encode("utf-8")
    _atomic_write_bytes(knowledge_dir / "mtimes.json", payload)


def load_mtimes(knowledge_dir: Path) -> dict[str, float]:
    """Load the mtime sidecar, or return ``{}`` if missing/corrupt."""
    path = knowledge_dir / "mtimes.json"
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _mtimes_from_graph(G: nx.DiGraph) -> dict[str, float]:
    """Extract the per-file mtime map embedded on file nodes."""
    out: dict[str, float] = {}
    for n, d in G.nodes(data=True):
        if d.get("ntype") == "file":
            out[n] = float(d.get("mtime", 0.0) or 0.0)
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def compute_stats(G: nx.DiGraph, knowledge_dir: Path) -> dict:
    """Return health metrics for the loaded knowledge graph.

    Backs the `relic stats` CLI command (human-facing).  The matching MCP
    tool was removed in Phase 7.5a — agents read freshness from the
    per-response `index{...}` header instead.

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


def _load_subprojects(config_file: Path | None) -> dict:
    if config_file and config_file.exists():
        with config_file.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("subprojects", {}) or {}
    return {}


def run_index(project_root: Path, knowledge_dir: Path, config_file: Path | None = None) -> tuple[nx.DiGraph, dict]:
    """Build the full graph from scratch and persist it.

    Cold-start path used by ``relic index`` and ``relic init``.  Writes both
    the pickled graph and the ``mtimes.json`` sidecar so subsequent reindex
    calls can run incrementally.
    """
    subprojects = _load_subprojects(config_file)
    G, skip_stats = build_graph(project_root, subprojects)
    save_graph(G, knowledge_dir)
    save_mtimes(knowledge_dir, _mtimes_from_graph(G))
    return G, skip_stats


def incremental_index(
    project_root: Path,
    knowledge_dir: Path,
    config_file: Path | None = None,
) -> tuple[nx.DiGraph, dict]:
    """Update the graph in place, reparsing only files whose mtime changed.

    Raises ``FileNotFoundError`` if no existing index is present — the caller
    (CLI or MCP) is expected to surface a clear message instructing the user
    to run a full ``relic index`` first.

    Returns ``(graph, summary)``.  The summary keys are:
        added       count of new files added to the graph
        modified    count of existing files reparsed
        deleted     count of files removed from the graph
        unchanged   count of files skipped (mtime unchanged)
        elapsed_s   wall-clock seconds for the incremental update
        skip_stats  same shape returned by ``_collect_source_files``
    """
    import time as _time

    t0 = _time.monotonic()
    G = load_graph(knowledge_dir)  # raises FileNotFoundError if missing

    # Prefer mtimes embedded on file nodes; fall back to the sidecar so we
    # remain compatible with graphs written by older relic versions.
    old_mtimes = _mtimes_from_graph(G) or load_mtimes(knowledge_dir)

    subprojects = _load_subprojects(config_file)
    files, skip_stats = _collect_source_files(project_root, subprojects)

    current: dict[str, tuple[Path, str, float]] = {}
    for abs_path, subproject in files:
        rel = _posix_rel(abs_path, project_root)
        current[rel] = (abs_path, subproject, _safe_mtime(abs_path))

    old_paths = set(old_mtimes.keys())
    new_paths = set(current.keys())

    deleted = old_paths - new_paths
    added = new_paths - old_paths
    # Use != rather than > so that filesystems with low mtime resolution and
    # editors that rewrite-with-older-timestamp still trigger a reparse.
    modified = {rel for rel in (new_paths & old_paths) if current[rel][2] != old_mtimes.get(rel, 0.0)}

    touched = added | modified

    for rel in deleted:
        _remove_file_node(G, rel)

    for rel in touched:
        abs_path, subproject, mtime = current[rel]
        lang = LANGUAGE_MAP.get(abs_path.suffix, "other")
        data = _analyse_one_file(abs_path, rel, lang, project_root)
        _apply_file_data(G, rel, lang, subproject, mtime, data)

    if touched or deleted:
        _resolve_cross_file_edges(G)
        _build_literal_index(G)
        save_graph(G, knowledge_dir)

    # Always refresh the sidecar so it tracks what's actually on disk now,
    # even when nothing changed (e.g. clock drift recovery).
    save_mtimes(knowledge_dir, {rel: current[rel][2] for rel in current})

    summary = {
        "added": len(added),
        "modified": len(modified),
        "deleted": len(deleted),
        "unchanged": len(new_paths) - len(touched),
        "elapsed_s": _time.monotonic() - t0,
        "skip_stats": skip_stats,
    }
    return G, summary
