"""Per-language unit extraction: files -> functions/methods/classes with spans and docs.

Python uses the stdlib ast (exact). JavaScript/TypeScript use a regex +
brace-matching pass over masked text (good-enough v0; tree-sitter is the
upgrade path). Other code languages fall back to one file-level unit so
redundancy detection still works at file granularity.
"""
from __future__ import annotations

import ast
import bisect
import hashlib
import re

from .model import FileRecord, Unit
from .normalize import bound_from_ast, bound_names, mask_code, norm_source
from .skeleton import (js_algo, js_literals, js_module_consts, py_algo,
                       py_literals, py_module_consts)

LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".json": "json", ".md": "markdown", ".rst": "text", ".txt": "text",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".ini": "toml", ".cfg": "toml",
    ".html": "html", ".css": "css",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell", ".sql": "sql",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cs": "csharp", ".go": "go", ".rs": "rust", ".java": "java",
}

# Languages whose files are code but have no structural extractor yet:
# score them as one file-level unit.
_FILE_UNIT_LANGS = {"c", "cpp", "csharp", "go", "rust", "java", "shell", "powershell", "sql", "css"}

_MIN_NORM_CHARS = 64  # below this a unit is "trivial": too small to score honestly


def lang_for(path: str) -> str:
    dot = path.rfind(".")
    return LANG_BY_EXT.get(path[dot:].lower(), "text") if dot != -1 else "text"


def _finish(unit: Unit, lang: str, path: str) -> Unit:
    unit.lang = lang
    unit.path = path
    unit.norm = norm_source(unit.body, lang, unit.bound if unit.bound else None)
    unit.fp = hashlib.sha256(unit.norm.encode("utf-8")).hexdigest()[:8]
    if len(unit.norm) < _MIN_NORM_CHARS:
        unit.trivial = True
        unit.scoreable = False
    if unit.kind == "class":
        unit.scoreable = False  # methods are scored individually; avoid double counting
    return unit


# ---------------------------------------------------------------- python

def _py_signature(node) -> str:
    try:
        sig = f"{node.name}({ast.unparse(node.args)})"
        if getattr(node, "returns", None) is not None:
            sig += f" -> {ast.unparse(node.returns)}"
        return sig
    except Exception:
        return f"{node.name}(...)"


def _first_doc_line(node) -> str:
    doc = ast.get_docstring(node)
    return doc.strip().splitlines()[0][:200] if doc else ""


def _extract_python(rec: FileRecord) -> None:
    try:
        tree = ast.parse(rec.text)
    except SyntaxError:
        rec.units.append(Unit("file", rec.path, rec.path, "(unparseable python)",
                              1, max(rec.lines, 1), body=rec.text))
        return
    rec.doc = _first_doc_line(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            rec.imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            rec.imports.append(node.module)
    rec.imports = sorted(set(rec.imports))[:20]
    lines = rec.text.split("\n")
    module_consts = py_module_consts(tree)

    def visit(body, prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                rec.units.append(Unit(
                    kind="method" if prefix else "function",
                    name=node.name, qualname=prefix + node.name,
                    signature=_py_signature(node),
                    start=start, end=node.end_lineno,
                    doc=_first_doc_line(node),
                    body="\n".join(lines[start - 1:node.end_lineno]),
                    bound=bound_from_ast(node),
                    algo=py_algo(node, node.name),
                    lits=py_literals(node, module_consts),
                ))
            elif isinstance(node, ast.ClassDef):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                rec.units.append(Unit(
                    kind="class", name=node.name, qualname=prefix + node.name,
                    signature=f"class {node.name}",
                    start=start, end=node.end_lineno,
                    doc=_first_doc_line(node),
                    body="\n".join(lines[start - 1:node.end_lineno]),
                    bound=bound_from_ast(node),
                ))
                visit(node.body, prefix + node.name + ".")

    visit(tree.body, "")


# ---------------------------------------------------------------- javascript / typescript

_JS_FUNC = re.compile(
    r"(?m)^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(")
_JS_ARROW = re.compile(
    r"(?m)^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s+)?(?:function\b\s*\*?|(?:\([^()\n]*\)|[A-Za-z_$][\w$]*)\s*=>)")
_JS_CLASS = re.compile(r"(?m)^[ \t]*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)")
_JS_IMPORT = re.compile(r"(?m)^\s*import\b[^;\n]*?from\s+['\"]([^'\"]+)['\"]"
                        r"|require\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _brace_span(masked: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(masked)):
        if masked[i] == "{":
            depth += 1
        elif masked[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(masked) - 1


def _expr_end(masked: str, start: int) -> int:
    """End of an expression-bodied arrow: first depth-0 ';', blank line, or EOF."""
    p = b = c = 0
    i = start
    while i < len(masked):
        ch = masked[i]
        if ch == "(":
            p += 1
        elif ch == ")":
            p -= 1
        elif ch == "[":
            b += 1
        elif ch == "]":
            b -= 1
        elif ch == "{":
            c += 1
        elif ch == "}":
            c -= 1
        elif ch == ";" and p == b == c == 0:
            return i
        elif ch == "\n" and p == b == c == 0 and masked.startswith("\n", i + 1):
            return i
        i += 1
    return len(masked) - 1


def _js_doc_above(lines: list[str], start_line: int) -> str:
    i = start_line - 2  # 0-based index of the line above the unit
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0:
        return ""
    s = lines[i].strip()
    if s.startswith("//"):
        while i > 0 and lines[i - 1].strip().startswith("//"):
            i -= 1
        return lines[i].strip().lstrip("/").strip()[:200]
    if s.endswith("*/"):
        j = i
        while j >= 0 and "/*" not in lines[j]:
            j -= 1
        if j >= 0:
            for cand in lines[j:i + 1]:
                cleaned = cand.strip().lstrip("/*").rstrip("*/").strip(" *")
                if cleaned:
                    return cleaned[:200]
    return ""


def _extract_js(rec: FileRecord) -> None:
    masked = mask_code(rec.text, rec.lang)
    lines = rec.text.split("\n")
    masked_lines = masked.split("\n")
    nl = [i for i, ch in enumerate(masked) if ch == "\n"]
    module_consts = js_module_consts(rec.text)

    def line_of(offset: int) -> int:
        return bisect.bisect_right(nl, offset - 1) + 1

    for m in _JS_IMPORT.finditer(rec.text):
        rec.imports.append(m.group(1) or m.group(2))
    rec.imports = sorted(set(filter(None, rec.imports)))[:20]

    seen_spans: set[tuple[int, int]] = set()

    def add(kind: str, name: str, start_off: int, end_off: int) -> None:
        s, e = line_of(start_off), line_of(end_off)
        if (s, e) in seen_spans:
            return
        seen_spans.add((s, e))
        body = "\n".join(lines[s - 1:e])
        masked_body = "\n".join(masked_lines[s - 1:e])
        sig = body.split("\n", 1)[0].strip()[:160]
        rec.units.append(Unit(kind=kind, name=name, qualname=name, signature=sig,
                              start=s, end=e, doc=_js_doc_above(lines, s), body=body,
                              bound=bound_names(body, rec.lang),
                              algo=js_algo(masked_body, name) if kind == "function" else "",
                              lits=js_literals(body, module_consts) if kind == "function"
                              else frozenset()))

    for m in _JS_FUNC.finditer(masked):
        ob = masked.find("{", m.end() - 1)
        if ob != -1:
            add("function", m.group(1), m.start(), _brace_span(masked, ob))
    for m in _JS_ARROW.finditer(masked):
        if m.group(0).rstrip().endswith("=>"):
            k = m.end()
            while k < len(masked) and masked[k] in " \t\r\n":
                k += 1
            end = _brace_span(masked, k) if k < len(masked) and masked[k] == "{" else _expr_end(masked, k)
        else:
            ob = masked.find("{", m.end() - 1)
            if ob == -1:
                continue
            end = _brace_span(masked, ob)
        add("function", m.group(1), m.start(), end)
    for m in _JS_CLASS.finditer(masked):
        ob = masked.find("{", m.end())
        if ob != -1:
            add("class", m.group(1), m.start(), _brace_span(masked, ob))

    rec.units.sort(key=lambda u: (u.start, u.end))


# ---------------------------------------------------------------- dispatch

def extract_units(rec: FileRecord) -> None:
    """Populate rec.units (and doc/imports) in place."""
    if rec.lang == "python":
        _extract_python(rec)
    elif rec.lang in ("javascript", "typescript"):
        _extract_js(rec)
    elif rec.lang in _FILE_UNIT_LANGS:
        rec.units.append(Unit("file", rec.path, rec.path, f"({rec.lang} file)",
                              1, max(rec.lines, 1), body=rec.text))
    elif rec.lang == "markdown":
        for line in rec.text.split("\n"):
            if line.startswith("#"):
                rec.doc = line.lstrip("#").strip()[:200]
                break
    for u in rec.units:
        _finish(u, rec.lang, rec.path)
