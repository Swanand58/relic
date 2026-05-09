"""Tree-sitter based parsers for Go, Rust, and Java.

These are activated only when ``tree-sitter-language-pack`` is installed::

    pip install relic-graph[treesitter]

Each parser walks the concrete syntax tree to extract:
- Symbol definitions (functions, classes/structs, interfaces)
- Import paths (resolved to relative project paths where possible)
- Intra-file call edges (function → function)
"""

from __future__ import annotations

import re
from pathlib import Path

from relic.parsers.base import AnalysisResult, register

# ---------------------------------------------------------------------------
# Shared source-text utilities (no tree-sitter dependency)
# ---------------------------------------------------------------------------

_RUST_ATTR_RE = re.compile(r"#\[(\w[\w:]*)\s*(?:\(([^)]*)\))?\]")
_JAVA_ANNOT_RE = re.compile(r"@(\w+)\s*(?:\(([^)]*)\))?$")


def _src_lines(source_bytes: bytes) -> list[str]:
    return source_bytes.decode("utf-8", errors="replace").splitlines()


def _leading_intent(src_lines: list[str], start_line_0indexed: int) -> str:
    """First stripped line of a leading doc comment. Works for ///, //, /** */."""
    idx = start_line_0indexed - 1
    while idx >= 0 and not src_lines[idx].strip():
        idx -= 1
    if idx < 0:
        return ""
    line = src_lines[idx].strip()
    if line.startswith("///"):
        text = line[3:].strip()
        return (text[:79] + "…") if len(text) > 79 else text
    if line.startswith("//"):
        text = line[2:].strip()
        return (text[:79] + "…") if len(text) > 79 else text
    if line == "*/" or line.endswith("*/"):
        while idx >= 0:
            ln = src_lines[idx].strip()
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


def _leading_rust_attrs(src_lines: list[str], start_line_0indexed: int) -> list[dict]:
    """#[attr(literal_args)] lines immediately before start_line. Max 5."""
    result: list[dict] = []
    idx = start_line_0indexed - 1
    while idx >= 0:
        line = src_lines[idx].strip()
        m = _RUST_ATTR_RE.match(line)
        if m:
            name = m.group(1)
            args_str = m.group(2) or ""
            args = [a.strip().strip("\"'") for a in re.findall(r"""['"][^'"]+['"]""", args_str)]
            result.insert(0, {"name": name, "args": args})
            idx -= 1
        elif not line or line.startswith("//"):
            idx -= 1
        else:
            break
    return result[:5]


def _leading_java_annotations(src_lines: list[str], start_line_0indexed: int) -> list[dict]:
    """@Annotation(literal_args) lines immediately before start_line. Max 5."""
    result: list[dict] = []
    idx = start_line_0indexed - 1
    while idx >= 0:
        line = src_lines[idx].strip()
        m = _JAVA_ANNOT_RE.match(line)
        if m:
            name = m.group(1)
            args_str = m.group(2) or ""
            args = [lit for lit in re.findall(r'"([^"]+)"', args_str)]
            result.insert(0, {"name": name, "args": args})
            idx -= 1
        elif not line or line.startswith("//") or line.startswith("*") or line == "*/":
            idx -= 1
        else:
            break
    return result[:5]


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


class GoParser:
    lang = "go"

    def analyse(self, source: str, rel_path: str, project_root: Path) -> AnalysisResult:
        from tree_sitter_language_pack import get_parser

        parser = get_parser("go")
        src = source.encode("utf-8")
        tree = parser.parse(src)
        root = tree.root_node
        lines = _src_lines(src)

        result = AnalysisResult()
        func_names: set[str] = set()

        for node in _walk(root):
            if node.type == "function_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    params = _go_func_sig(node, src, name)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": name,
                            "stype": "function",
                            "line": ln + 1,
                            "signature": params,
                            "intent": _leading_intent(lines, ln),
                            "decorators": [],
                        }
                    )
                    func_names.add(name)

            elif node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    params = _go_func_sig(node, src, name)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": name,
                            "stype": "function",
                            "line": ln + 1,
                            "signature": params,
                            "intent": _leading_intent(lines, ln),
                            "decorators": [],
                        }
                    )
                    func_names.add(name)

            elif node.type == "type_declaration":
                for spec in _children_of_type(node, "type_spec"):
                    type_name_node = spec.child_by_field_name("name")
                    type_node = spec.child_by_field_name("type")
                    if type_name_node:
                        name = _node_text(type_name_node, src)
                        stype = "class"
                        if type_node and type_node.type == "interface_type":
                            stype = "interface"
                        ln = spec.start_point[0]
                        result.symbols.append(
                            {
                                "name": name,
                                "stype": stype,
                                "line": ln + 1,
                                "signature": name,
                                "intent": _leading_intent(lines, ln),
                                "decorators": [],
                            }
                        )

            elif node.type == "import_declaration":
                for spec in _walk(node):
                    if spec.type == "import_spec" or spec.type == "interpreted_string_literal":
                        path_text = _node_text(spec, src).strip('"')
                        if path_text.startswith(".") or "/" not in path_text:
                            result.imports.append(path_text)

        # Extract calls within function bodies
        for node in _walk(root):
            if node.type in ("function_declaration", "method_declaration"):
                fname_node = node.child_by_field_name("name")
                if not fname_node:
                    continue
                caller = _node_text(fname_node, src)
                body = node.child_by_field_name("body")
                if body:
                    for call in _walk(body):
                        if call.type == "call_expression":
                            fn = call.child_by_field_name("function")
                            if fn:
                                callee = _node_text(fn, src).split(".")[-1]
                                if callee in func_names and callee != caller:
                                    result.calls.append((caller, callee))

        return result


def _go_func_sig(node, src: bytes, name: str) -> str:
    params = node.child_by_field_name("parameters")
    result_node = node.child_by_field_name("result")
    sig = name
    if params:
        sig = f"{name}{_node_text(params, src)}"
    if result_node:
        sig += f" -> {_node_text(result_node, src)}"
    return sig


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------


class RustParser:
    lang = "rust"

    def analyse(self, source: str, rel_path: str, project_root: Path) -> AnalysisResult:
        from tree_sitter_language_pack import get_parser

        parser = get_parser("rust")
        src = source.encode("utf-8")
        tree = parser.parse(src)
        root = tree.root_node
        lines = _src_lines(src)

        result = AnalysisResult()
        func_names: set[str] = set()

        for node in _walk(root):
            if node.type == "function_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    sig = _rust_func_sig(node, src, name)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": name,
                            "stype": "function",
                            "line": ln + 1,
                            "signature": sig,
                            "intent": _leading_intent(lines, ln),
                            "decorators": _leading_rust_attrs(lines, ln),
                        }
                    )
                    func_names.add(name)

            elif node.type == "struct_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": name,
                            "stype": "class",
                            "line": ln + 1,
                            "signature": name,
                            "intent": _leading_intent(lines, ln),
                            "decorators": _leading_rust_attrs(lines, ln),
                        }
                    )

            elif node.type == "enum_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": name,
                            "stype": "class",
                            "line": ln + 1,
                            "signature": name,
                            "intent": _leading_intent(lines, ln),
                            "decorators": _leading_rust_attrs(lines, ln),
                        }
                    )

            elif node.type == "trait_item":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": name,
                            "stype": "interface",
                            "line": ln + 1,
                            "signature": name,
                            "intent": _leading_intent(lines, ln),
                            "decorators": _leading_rust_attrs(lines, ln),
                        }
                    )

            elif node.type == "impl_item":
                trait_node = node.child_by_field_name("trait")
                type_node = node.child_by_field_name("type")
                if trait_node and type_node:
                    impl_name = _node_text(type_node, src)
                    trait_name = _node_text(trait_node, src)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": impl_name,
                            "stype": "class",
                            "line": ln + 1,
                            "signature": f"{impl_name}({trait_name})",
                            "extends": trait_name,
                            "intent": _leading_intent(lines, ln),
                            "decorators": _leading_rust_attrs(lines, ln),
                        }
                    )

            elif node.type == "use_declaration":
                path_text = _node_text(node, src).removeprefix("use ").rstrip(";").strip()
                if path_text.startswith("crate::") or path_text.startswith("super::"):
                    result.imports.append(path_text)

        # Extract calls
        for node in _walk(root):
            if node.type == "function_item":
                fname_node = node.child_by_field_name("name")
                if not fname_node:
                    continue
                caller = _node_text(fname_node, src)
                body = node.child_by_field_name("body")
                if body:
                    for call in _walk(body):
                        if call.type == "call_expression":
                            fn = call.child_by_field_name("function")
                            if fn:
                                callee = _node_text(fn, src).split("::")[-1].split(".")[-1]
                                if callee in func_names and callee != caller:
                                    result.calls.append((caller, callee))

        return result


def _rust_func_sig(node, src: bytes, name: str) -> str:
    params = node.child_by_field_name("parameters")
    ret = node.child_by_field_name("return_type")
    sig = name
    if params:
        sig = f"{name}{_node_text(params, src)}"
    if ret:
        sig += f" -> {_node_text(ret, src)}"
    return sig


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


class JavaParser:
    lang = "java"

    def analyse(self, source: str, rel_path: str, project_root: Path) -> AnalysisResult:
        from tree_sitter_language_pack import get_parser

        parser = get_parser("java")
        src = source.encode("utf-8")
        tree = parser.parse(src)
        root = tree.root_node
        lines = _src_lines(src)

        result = AnalysisResult()
        method_names: set[str] = set()

        for node in _walk(root):
            if node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    superclass = node.child_by_field_name("superclass")
                    ln = node.start_point[0]
                    sym: dict = {
                        "name": name,
                        "stype": "class",
                        "line": ln + 1,
                        "signature": name,
                        "intent": _leading_intent(lines, ln),
                        "decorators": _leading_java_annotations(lines, ln),
                    }
                    if superclass:
                        parent = _node_text(superclass, src)
                        sym["extends"] = parent
                        sym["signature"] = f"{name}({parent})"
                    result.symbols.append(sym)

            elif node.type == "interface_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": name,
                            "stype": "interface",
                            "line": ln + 1,
                            "signature": name,
                            "intent": _leading_intent(lines, ln),
                            "decorators": _leading_java_annotations(lines, ln),
                        }
                    )

            elif node.type == "method_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = _node_text(name_node, src)
                    sig = _java_method_sig(node, src, name)
                    ln = node.start_point[0]
                    result.symbols.append(
                        {
                            "name": name,
                            "stype": "function",
                            "line": ln + 1,
                            "signature": sig,
                            "intent": _leading_intent(lines, ln),
                            "decorators": _leading_java_annotations(lines, ln),
                        }
                    )
                    method_names.add(name)

            elif node.type == "import_declaration":
                path_text = _node_text(node, src).removeprefix("import ").rstrip(";").strip()
                if path_text.startswith("static "):
                    path_text = path_text.removeprefix("static ")
                result.imports.append(path_text)

        # Extract calls
        for node in _walk(root):
            if node.type == "method_declaration":
                mname_node = node.child_by_field_name("name")
                if not mname_node:
                    continue
                caller = _node_text(mname_node, src)
                body = node.child_by_field_name("body")
                if body:
                    for call in _walk(body):
                        if call.type == "method_invocation":
                            callee_node = call.child_by_field_name("name")
                            if callee_node:
                                callee = _node_text(callee_node, src)
                                if callee in method_names and callee != caller:
                                    result.calls.append((caller, callee))

        return result


def _java_method_sig(node, src: bytes, name: str) -> str:
    params = node.child_by_field_name("parameters")
    ret_type = node.child_by_field_name("type")
    sig = name
    if params:
        sig = f"{name}{_node_text(params, src)}"
    if ret_type:
        sig += f" -> {_node_text(ret_type, src)}"
    return sig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk(node):
    """Depth-first walk of a tree-sitter node."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _children_of_type(node, type_name: str):
    return [c for c in node.children if c.type == type_name]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_all() -> None:
    """Register all tree-sitter parsers."""
    register(GoParser())
    register(RustParser())
    register(JavaParser())
