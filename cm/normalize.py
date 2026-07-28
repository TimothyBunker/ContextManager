"""Code normalization for structural comparison.

Three views of source text come out of one comment/string-aware scanner:

- mask_code(): same-length text with comment and string interiors blanked.
  Offsets and line numbers stay valid, so extractors can brace-match on it.
- bound_names(): the identifiers a fragment *binds* — params, locals, its own
  name. Everything else an identifier can be is an anchor: builtins, imports,
  attributes, called functions — the names that reach outside the fragment.
- norm_source(): comments dropped, strings -> S, numbers -> N, bound
  identifiers alpha-renamed to V0, V1, ... in order of appearance, anchors
  kept verbatim (two-tier renaming). Naming and comments can't hide
  structure, but *what the code calls into* still distinguishes it:
  assertGreaterEqual vs assertLessEqual no longer normalize identically.
"""
from __future__ import annotations

import ast
import keyword
import re
import textwrap

# lang -> (line_comment, block_pairs, quotes, triple_quotes, template_literal)
_SCAN_CFG: dict[str, tuple | None] = {
    "python": ("#", [], "'\"", True, False),
    "javascript": ("//", [("/*", "*/")], "'\"", False, True),
    "typescript": ("//", [("/*", "*/")], "'\"", False, True),
    "c": ("//", [("/*", "*/")], "'\"", False, False),
    "cpp": ("//", [("/*", "*/")], "'\"", False, False),
    "csharp": ("//", [("/*", "*/")], "'\"", False, False),
    "go": ("//", [("/*", "*/")], "'\"", False, True),
    "rust": ("//", [("/*", "*/")], "\"", False, False),
    "java": ("//", [("/*", "*/")], "'\"", False, False),
    "shell": ("#", [], "'\"", False, False),
    "powershell": ("#", [("<#", "#>")], "'\"", False, False),
    "toml": ("#", [], "'\"", True, False),
    "yaml": ("#", [], "'\"", False, False),
    "sql": ("--", [("/*", "*/")], "'\"", False, False),
    "_default": None,  # no comment/string structure known: treat all as code
}

_KEYWORDS: dict[str, frozenset] = {
    "python": frozenset(keyword.kwlist + keyword.softkwlist),
    "javascript": frozenset(
        "function var let const if else for while do return class new this super typeof "
        "instanceof async await import export from default null undefined true false try "
        "catch finally throw switch case break continue delete in of yield static get set "
        "extends void".split()
    ),
}
_KEYWORDS["typescript"] = _KEYWORDS["javascript"] | frozenset(
    "interface type enum implements declare readonly public private protected namespace "
    "abstract as is keyof infer never unknown any string number boolean".split()
)

_TOKEN = re.compile(r'[A-Za-z_$][\w$]*|\d[\w.]*|"S"|[^\sA-Za-z_$0-9]+')


def _segments(text: str, lang: str) -> list[tuple[str, int, int]]:
    """Split text into ("code"|"comment"|"string", start, end) segments."""
    cfg = _SCAN_CFG.get(lang, _SCAN_CFG["_default"])
    if cfg is None:
        return [("code", 0, len(text))] if text else []
    line_c, blocks, quotes, triples, template = cfg
    segs: list[tuple[str, int, int]] = []
    i, n = 0, len(text)
    code_start = 0

    def close_code(end: int) -> None:
        if end > code_start:
            segs.append(("code", code_start, end))

    while i < n:
        c = text[i]
        if line_c and text.startswith(line_c, i):
            close_code(i)
            j = text.find("\n", i)
            j = n if j == -1 else j  # newline stays in the following code segment
            segs.append(("comment", i, j))
            i = code_start = j
            continue
        block = next((b for b in blocks if text.startswith(b[0], i)), None)
        if block:
            close_code(i)
            j = text.find(block[1], i + len(block[0]))
            j = n if j == -1 else j + len(block[1])
            segs.append(("comment", i, j))
            i = code_start = j
            continue
        if triples and (text.startswith('"""', i) or text.startswith("'''", i)):
            q = text[i:i + 3]
            close_code(i)
            j = i + 3
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text.startswith(q, j):
                    j += 3
                    break
                j += 1
            j = min(j, n)
            segs.append(("string", i, j))
            i = code_start = j
            continue
        if c in quotes or (template and c == "`"):
            close_code(i)
            j = i + 1
            while j < n:
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == c:
                    j += 1
                    break
                if ch == "\n" and c != "`":
                    break  # unterminated single-line string
                j += 1
            j = min(j, n)
            segs.append(("string", i, j))
            i = code_start = j
            continue
        i += 1
    close_code(n)
    return segs


def mask_code(text: str, lang: str) -> str:
    """Same-length text with comment and string interiors blanked (newlines kept)."""
    out = []
    for kind, a, b in _segments(text, lang):
        seg = text[a:b]
        if kind == "code":
            out.append(seg)
        elif kind == "comment":
            out.append("".join(ch if ch == "\n" else " " for ch in seg))
        else:  # string: keep delimiters, blank the interior
            inner = "".join(ch if ch == "\n" else " " for ch in seg[1:-1])
            out.append(seg[0] + inner + (seg[-1] if len(seg) > 1 else ""))
    return "".join(out)


# ---------------------------------------------------------------- bound names

def bound_from_ast(node) -> frozenset:
    """Names bound inside a Python AST node (params, locals, nested defs).

    Names declared global/nonlocal and imported names are excluded: they refer
    outward, which makes them anchors even though the syntax binds them.
    """
    bound, outward = set(), set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            outward.update(n.names)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            outward.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            bound.add(n.name)
    return frozenset(bound - outward)


_JS_ID = r"[A-Za-z_$][\w$]*"
_JS_DECL = re.compile(rf"\b(?:const|let|var)\s+({_JS_ID})")
_JS_DESTRUCT = re.compile(r"\b(?:const|let|var)\s*[\[{]([^\]}]*)[\]}]")
_JS_FUNC_SIG = re.compile(rf"\bfunction\s*\*?\s*({_JS_ID})?\s*\(([^()]*)\)")
_JS_PAREN_ARROW = re.compile(r"\(([^()]*)\)\s*=>")
_JS_BARE_ARROW = re.compile(rf"\b({_JS_ID})\s*=>")
_JS_CATCH = re.compile(rf"\bcatch\s*\(\s*({_JS_ID})")


def _js_params(param_src: str) -> set[str]:
    out = set()
    for piece in param_src.split(","):
        m = re.match(rf"\s*(?:\.\.\.)?\s*({_JS_ID})", piece)
        if m:
            out.add(m.group(1))
    return out


def _js_bound(masked: str) -> frozenset:
    """Heuristic bound-name set for JS/TS (no AST): declarations, params, catch."""
    out: set[str] = set()
    out.update(m.group(1) for m in _JS_DECL.finditer(masked))
    for m in _JS_DESTRUCT.finditer(masked):
        for piece in m.group(1).split(","):
            # `{k: v}` binds v (k stays an anchor); shorthand `{k}` binds k
            ids = re.findall(_JS_ID, piece)
            if ids:
                out.add(ids[-1])
    for m in _JS_FUNC_SIG.finditer(masked):
        if m.group(1):
            out.add(m.group(1))
        out.update(_js_params(m.group(2)))
    for m in _JS_PAREN_ARROW.finditer(masked):
        out.update(_js_params(m.group(1)))
    out.update(m.group(1) for m in _JS_BARE_ARROW.finditer(masked))
    out.update(m.group(1) for m in _JS_CATCH.finditer(masked))
    return frozenset(out)


def bound_names(text: str, lang: str) -> frozenset:
    """Bound-name set for a source fragment; empty when unknown/unparseable."""
    if lang == "python":
        try:
            return bound_from_ast(ast.parse(textwrap.dedent(text)))
        except SyntaxError:
            return frozenset()
    if lang in ("javascript", "typescript"):
        return _js_bound(mask_code(text, lang))
    return frozenset()


# ---------------------------------------------------------------- normalization

def norm_source(text: str, lang: str, bound: frozenset | None = None) -> str:
    """Two-tier normalized skeleton (see module docstring).

    bound=None computes the bound set from the text itself (right for whole
    units/files). Pass a precomputed set when normalizing fragments that lack
    context, e.g. single lines of a known unit — an explicitly empty set means
    "rename nothing", not "recompute".
    """
    if bound is None:
        bound = bound_names(text, lang)
    parts = []
    for kind, a, b in _segments(text, lang):
        if kind == "code":
            parts.append(text[a:b])
        elif kind == "string":
            parts.append(' "S" ')
        else:
            parts.append(" ")
    stripped = "".join(parts)
    kw = _KEYWORDS.get(lang, _KEYWORDS["javascript"] if lang != "python" else _KEYWORDS["python"])
    rename: dict[str, str] = {}
    out = []
    prev = ""
    for m in _TOKEN.finditer(stripped):
        t = m.group()
        if t == '"S"':
            out.append("S")
        elif t[0].isdigit():
            out.append("N")
        elif t[0].isalpha() or t[0] in "_$":
            if prev.endswith("."):
                out.append(t)  # attribute access: always an anchor
            elif t in kw:
                out.append(t)
            elif t in bound:
                out.append(rename.setdefault(t, f"V{len(rename)}"))
            else:
                out.append(t)  # free name: semantic anchor, kept verbatim
        else:
            out.append(t)
        prev = t
    return " ".join(out)
