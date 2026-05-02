"""Discovery — auto-detects subprojects in a project tree using heuristics.

Looks for directories that represent meaningful code boundaries:
- Contain a package manifest (package.json, pyproject.toml, go.mod, etc.)
- Are named source-like dirs (src, lib, packages, apps, services, etc.)
- Contain source files directly

No LLM involved — pure filesystem heuristics.
"""

from pathlib import Path

# Directories that signal a subproject boundary when found inside a dir.
MANIFEST_FILES = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "Cargo.toml",
    "build.gradle",
    "pom.xml",
    "*.csproj",
}

# Top-level directory names that are likely subproject containers.
# Children of these dirs are treated as subprojects.
MONOREPO_CONTAINERS = {"packages", "apps", "services", "libs", "modules", "crates"}

# Directory names that are never subprojects.
SKIP_DIRS = {
    ".git", ".github", ".vscode", ".idea",
    "node_modules", ".venv", "venv", "env",
    "__pycache__", ".tox", ".pytest_cache", ".ruff_cache",
    "dist", "build", "out", "target", ".next", ".nuxt",
    "coverage", "htmlcov", ".coverage",
    ".knowledge", "test-results", ".playwright-mcp",
}

# Source-like directory names that qualify as a subproject on their own.
SOURCE_DIRS = {"src", "lib", "core", "api", "app", "server", "client", "pkg"}

# Extensions that count as source files.
SOURCE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
    ".java", ".kt", ".rb", ".cs", ".cpp", ".c", ".h",
}


def _has_manifest(path: Path) -> bool:
    """Return True if path contains any package manifest file."""
    for name in MANIFEST_FILES:
        if "*" in name:
            if any(path.glob(name)):
                return True
        elif (path / name).exists():
            return True
    return False


def _has_source_files(path: Path, min_files: int = 2) -> bool:
    """Return True if path contains at least min_files source files (non-recursive)."""
    count = 0
    for p in path.iterdir():
        if p.is_file() and p.suffix in SOURCE_EXTENSIONS:
            count += 1
            if count >= min_files:
                return True
    return False


def _is_subproject_candidate(path: Path) -> bool:
    """Return True if this directory looks like a subproject."""
    if not path.is_dir():
        return False
    if path.name in SKIP_DIRS or path.name.startswith("."):
        return False
    return _has_manifest(path) or _has_source_files(path)


def _infer_description(path: Path, name: str) -> str:
    """Best-effort one-line description from manifest or directory name."""
    # Try package.json
    pkg = path / "package.json"
    if pkg.exists():
        try:
            import json
            data = json.loads(pkg.read_text(encoding="utf-8"))
            if desc := data.get("description", "").strip():
                return desc
        except Exception:
            pass

    # Try pyproject.toml
    pyproj = path / "pyproject.toml"
    if pyproj.exists():
        try:
            import tomllib  # Python 3.11+
            with pyproj.open("rb") as f:
                data = tomllib.load(f)
            if desc := data.get("project", {}).get("description", "").strip():
                return desc
        except Exception:
            pass

    # Fall back to humanised directory name
    return name.replace("-", " ").replace("_", " ").capitalize() + " subproject"


def discover_subprojects(project_root: Path) -> dict[str, dict]:
    """Walk project_root and return a dict suitable for relic.yaml subprojects.

    Strategy (in order):
    1. Monorepo containers — if a dir named packages/apps/services/etc exists,
       its children are the subprojects.
    2. Manifest files — any direct child with a package.json / pyproject.toml etc.
    3. Source dirs — direct children named src/lib/core/api/etc with source files.
    4. Fallback — direct children with ≥2 source files.

    Returns:
        {
            "payments": {"path": "./payments", "description": "..."},
            ...
        }
    """
    found: dict[str, dict] = {}

    direct_children = [
        p for p in sorted(project_root.iterdir())
        if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".")
    ]

    # Strategy 1 — monorepo containers
    for child in direct_children:
        if child.name in MONOREPO_CONTAINERS:
            for grandchild in sorted(child.iterdir()):
                if _is_subproject_candidate(grandchild):
                    rel = f"./{grandchild.relative_to(project_root)}"
                    name = grandchild.name
                    found[name] = {
                        "path": rel,
                        "description": _infer_description(grandchild, name),
                    }

    if found:
        return found

    # Strategy 2 — direct children with manifest files
    for child in direct_children:
        if _has_manifest(child):
            rel = f"./{child.relative_to(project_root)}"
            found[child.name] = {
                "path": rel,
                "description": _infer_description(child, child.name),
            }

    # Strategy 3 — well-known source dir names (always runs alongside Strategy 2)
    # A project may have both a demo/ with a manifest and a src/ with real source.
    for child in direct_children:
        if child.name in SOURCE_DIRS and _has_source_files(child) and child.name not in found:
            rel = f"./{child.relative_to(project_root)}"
            found[child.name] = {
                "path": rel,
                "description": _infer_description(child, child.name),
            }

    if found:
        return found

    # Strategy 4 — any direct child with ≥2 source files
    for child in direct_children:
        if _has_source_files(child):
            rel = f"./{child.relative_to(project_root)}"
            found[child.name] = {
                "path": rel,
                "description": _infer_description(child, child.name),
            }

    return found
