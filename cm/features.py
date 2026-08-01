"""Deterministic discrete features: the tokens a unit cannot hide.

The tripwire ranks and flags by exact overlap of discrete features — the
identifiers a unit reaches outside itself for (calls, attributes, globals)
and the literal constants it computes with. No embeddings, no compression,
no learned anything: the same code always produces the same feature set,
every feature is a string you can grep for in PROJECT.cm, and every match
is explainable by listing the shared tokens.

Bound names (params, locals, the unit's own name) are excluded — renaming
them is free, so they carry no identity. Module-level constants are
attributed to the units that reference them, because hoisting a literal
into a named constant is a demonstrated disguise. Common features carry no
weight at match time: rarity is measured against the corpus, not assumed.
"""
from __future__ import annotations

import ast
import re

from .normalize import _KEYWORDS

_LIT_MIN_LEN = 2  # single chars are too common to identify anything
_FEAT_CAP = 64

# Language-universal vocabulary is never distinctive, no matter how rare it
# is in a particular corpus. Document frequency handles big corpora; this
# frozen stoplist keeps small corpora honest — you would never grep for
# `open` or "utf-8" to find a specific function.
import builtins as _builtins

COMMON: frozenset = frozenset(dir(_builtins)) | frozenset("""
    os sys re json math time typing pathlib collections itertools functools
    subprocess logging datetime random string io ast shutil tempfile unittest
    dataclasses enum abc copy struct socket threading asyncio argparse hashlib
    base64 uuid pickle csv glob textwrap traceback warnings weakref zlib gzip
    bisect heapq queue statistics secrets shlex codecs inspect importlib
    contextlib operator keyword builtins types numbers decimal fractions
    append extend insert get items keys values pop add update join split
    strip rstrip lstrip replace format startswith endswith find index count
    sort lower upper encode decode read write close exists mkdir
    self cls args kwargs
    console JSON Object Array Math Promise Error Date RegExp require module
    exports window document undefined NaN length push shift unshift slice
    splice indexOf includes toString trim concat forEach filter reduce
""".split()) | frozenset([
    "utf-8", "utf-8-sig", "ascii", "latin-1", "__main__", "__init__",
    "strict", "ignore", "replace", "\\n", "\\t", "\r\n",
    "rb", "wb", "ab", "true", "false", "null", "None",
    "GET", "POST", "PUT", "DELETE", "http", "https",
])


def _keep_literal(v) -> str | None:
    if isinstance(v, str):
        s = v.strip()
        return s if len(s) >= _LIT_MIN_LEN else None
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return repr(v) if abs(v) > 1 else None
    return None


# ---------------------------------------------------------------- python

def py_module_consts(tree) -> dict:
    """Module-level NAME = <literal> bindings, incl. simple containers."""
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not names or node.value is None:
            continue
        lits = []
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Constant):
                lit = _keep_literal(sub.value)
                if lit:
                    lits.append(lit)
        if lits:
            for name in names:
                out[name] = lits[:_FEAT_CAP]
    return out


def py_features(node, bound: frozenset, module_consts: dict | None = None) -> frozenset:
    """Discrete feature set of a Python unit (docstrings excluded)."""
    docs = set()
    for n in ast.walk(node):
        body = getattr(n, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant):
            docs.add(id(body[0].value))
    feats: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound:
            feats.add(n.id)
            if module_consts:
                feats.update(module_consts.get(n.id, ()))
        elif isinstance(n, ast.Attribute):
            feats.add(n.attr)
        elif isinstance(n, ast.Constant) and id(n) not in docs:
            lit = _keep_literal(n.value)
            if lit:
                feats.add(lit)
    return frozenset(sorted(feats)[:_FEAT_CAP])


# ---------------------------------------------------------------- javascript

_JS_ID = re.compile(r"[A-Za-z_$][\w$]*")
_JS_STR = re.compile(r"'([^'\\\n]*(?:\\.[^'\\\n]*)*)'|\"([^\"\\\n]*(?:\\.[^\"\\\n]*)*)\"")
_JS_NUM = re.compile(r"(?<![\w.])\d{2,}(?:\.\d+)?")
_JS_CONST = re.compile(
    r"(?m)^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*"
    r"('[^'\n]*'|\"[^\"\n]*\"|\d[\w.]*)\s*;?\s*$")


def js_module_consts(text: str) -> dict:
    """Top-level `const NAME = <literal>` bindings in a JS/TS file."""
    out = {}
    for m in _JS_CONST.finditer(text):
        raw = m.group(2)
        value = raw[1:-1] if raw[0] in "'\"" else raw
        lit = _keep_literal(value)
        if lit:
            out[m.group(1)] = [lit]
    return out


def js_features(body: str, masked_body: str, bound: frozenset,
                module_consts: dict | None = None) -> frozenset:
    """Discrete feature set of a JS/TS unit (identifiers from masked text,
    literals from the raw text)."""
    kw = _KEYWORDS["javascript"] | _KEYWORDS["typescript"]
    feats: set[str] = set()
    for m in _JS_ID.finditer(masked_body):
        t = m.group()
        if t in kw or t in bound:
            continue
        feats.add(t)
        if module_consts and t in module_consts:
            feats.update(module_consts[t])
    for m in _JS_STR.finditer(body):
        lit = _keep_literal(m.group(1) if m.group(1) is not None else m.group(2))
        if lit:
            feats.add(lit)
    feats.update(m.group() for m in _JS_NUM.finditer(body))
    return frozenset(sorted(feats)[:_FEAT_CAP])
