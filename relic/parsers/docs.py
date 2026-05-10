"""Static document parsers — OpenAPI, Markdown, JSON Schema, pyproject.toml, package.json.

Each parser returns symbols representing the doc's key concepts:
- OpenAPI: HTTP endpoints as symbols (e.g. "GET /users")
- Markdown: H1/H2 headings as symbols
- JSON Schema: top-level definitions/$defs as symbols
- pyproject.toml: project name + script entry points as symbols
- package.json: package name + script names as symbols
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from relic.parsers.base import AnalysisResult


def analyse_openapi(source: str, rel_path: str, project_root: Path) -> AnalysisResult:
    """Parse OpenAPI/Swagger YAML or JSON — extract endpoints as symbols."""
    import yaml

    try:
        doc = yaml.safe_load(source)
    except Exception:
        return AnalysisResult()
    if not isinstance(doc, dict):
        return AnalysisResult()

    paths = doc.get("paths", {})
    if not isinstance(paths, dict):
        return AnalysisResult()

    symbols = []
    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue
            name = f"{method.upper()} {path_str}"
            summary = op.get("summary") or op.get("description") or ""
            if isinstance(summary, str):
                summary = summary.splitlines()[0][:80]
            op_id = op.get("operationId", "")
            sig = op_id if op_id else name
            symbols.append(
                {
                    "name": name,
                    "stype": "endpoint",
                    "line": 0,
                    "signature": sig,
                    "intent": summary,
                    "decorators": [],
                    "literals": [],
                    "raw_calls": [],
                }
            )

    return AnalysisResult(symbols=symbols)


def analyse_markdown(source: str, rel_path: str, project_root: Path) -> AnalysisResult:
    """Parse Markdown — extract H1/H2 headings as symbols."""
    symbols = []
    for i, line in enumerate(source.splitlines(), start=1):
        m = re.match(r"^(#{1,2})\s+(.+)", line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        stype = "h1" if level == 1 else "h2"
        symbols.append(
            {
                "name": title,
                "stype": stype,
                "line": i,
                "signature": title,
                "intent": "",
                "decorators": [],
                "literals": [],
                "raw_calls": [],
            }
        )
    return AnalysisResult(symbols=symbols)


def analyse_jsonschema(source: str, rel_path: str, project_root: Path) -> AnalysisResult:
    """Parse JSON Schema — extract top-level definitions as symbols."""
    try:
        doc = json.loads(source)
    except Exception:
        return AnalysisResult()
    if not isinstance(doc, dict):
        return AnalysisResult()

    symbols = []
    for section in ("definitions", "$defs", "properties"):
        defs = doc.get(section, {})
        if not isinstance(defs, dict):
            continue
        for name, schema in defs.items():
            if not isinstance(schema, dict):
                continue
            description = schema.get("description") or schema.get("title") or ""
            if isinstance(description, str):
                description = description[:80]
            sym_type = schema.get("type", "object")
            symbols.append(
                {
                    "name": name,
                    "stype": "schema",
                    "line": 0,
                    "signature": f"{name}: {sym_type}",
                    "intent": description,
                    "decorators": [],
                    "literals": [],
                    "raw_calls": [],
                }
            )

    return AnalysisResult(symbols=symbols)


def analyse_pyproject(source: str, rel_path: str, project_root: Path) -> AnalysisResult:
    """Parse pyproject.toml — extract project name and script entry points."""
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return AnalysisResult()

    try:
        doc = tomllib.loads(source)
    except Exception:
        return AnalysisResult()
    if not isinstance(doc, dict):
        return AnalysisResult()

    symbols = []
    project = doc.get("project", {})
    if isinstance(project, dict):
        name = project.get("name", "")
        version = project.get("version", "")
        desc = project.get("description", "")[:80]
        if name:
            symbols.append(
                {
                    "name": name,
                    "stype": "package",
                    "line": 0,
                    "signature": f"{name} v{version}" if version else name,
                    "intent": desc,
                    "decorators": [],
                    "literals": [],
                    "raw_calls": [],
                }
            )
        scripts = project.get("scripts", {})
        if isinstance(scripts, dict):
            for script_name, entry in scripts.items():
                symbols.append(
                    {
                        "name": script_name,
                        "stype": "script",
                        "line": 0,
                        "signature": str(entry),
                        "intent": "",
                        "decorators": [],
                        "literals": [],
                        "raw_calls": [],
                    }
                )

    return AnalysisResult(symbols=symbols)


def analyse_packagejson(source: str, rel_path: str, project_root: Path) -> AnalysisResult:
    """Parse package.json — extract package name and npm scripts."""
    try:
        doc = json.loads(source)
    except Exception:
        return AnalysisResult()
    if not isinstance(doc, dict):
        return AnalysisResult()

    symbols = []
    name = doc.get("name", "")
    version = doc.get("version", "")
    desc = (doc.get("description") or "")[:80]
    if name:
        symbols.append(
            {
                "name": name,
                "stype": "package",
                "line": 0,
                "signature": f"{name}@{version}" if version else name,
                "intent": desc,
                "decorators": [],
                "literals": [],
                "raw_calls": [],
            }
        )

    scripts = doc.get("scripts", {})
    if isinstance(scripts, dict):
        for script_name, cmd in scripts.items():
            symbols.append(
                {
                    "name": script_name,
                    "stype": "script",
                    "line": 0,
                    "signature": str(cmd)[:80],
                    "intent": "",
                    "decorators": [],
                    "literals": [],
                    "raw_calls": [],
                }
            )

    return AnalysisResult(symbols=symbols)
