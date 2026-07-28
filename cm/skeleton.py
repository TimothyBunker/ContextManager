"""Algorithm skeletons: the structure channel (`::algo` in PROJECT.cm).

A skeleton captures what survives rewriting — control-flow shape and the
operations performed — and breaks when meaning-relevant structure changes.
It is deliberately blind to naming, comments, and literals (the info channel
covers text), and it preserves exactly what two-tier renaming preserves:
anchors, the names code calls into.

One line per unit:

    cfg=<nested control-flow shape> an=<anchor:count,...> [fl=rec|gen|rec+gen]

Python skeletons come from the AST (nested, exact). JS/TS skeletons are flat
keyword-at-depth sequences from masked text (comparable, coarser).
"""
from __future__ import annotations

import ast
import re
from collections import Counter
from difflib import SequenceMatcher

_OPS = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//",
    ast.Mod: "%", ast.Pow: "**", ast.LShift: "<<", ast.RShift: ">>",
    ast.BitOr: "|b", ast.BitAnd: "&b", ast.BitXor: "^", ast.MatMult: "@",
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
    ast.Gt: ">", ast.GtE: ">=", ast.Is: "is", ast.IsNot: "is-not",
    ast.In: "in", ast.NotIn: "not-in",
    ast.And: "and", ast.Or: "or", ast.Not: "not",
    ast.USub: "-", ast.UAdd: "+", ast.Invert: "~",
}
_TRY_TYPES = (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)
_CFG_CAP = 600
_ANCHOR_CAP = 24


def _stmts(body) -> list[str]:
    out = []
    for node in body:
        if isinstance(node, (ast.For, ast.AsyncFor)):
            s = "for{" + ",".join(_stmts(node.body)) + "}"
            if node.orelse:
                s += "else{" + ",".join(_stmts(node.orelse)) + "}"
            out.append(s)
        elif isinstance(node, ast.While):
            s = "while{" + ",".join(_stmts(node.body)) + "}"
            if node.orelse:
                s += "else{" + ",".join(_stmts(node.orelse)) + "}"
            out.append(s)
        elif isinstance(node, ast.If):
            s = "if{" + ",".join(_stmts(node.body))
            if node.orelse:
                s += "|" + ",".join(_stmts(node.orelse))
            out.append(s + "}")
        elif isinstance(node, _TRY_TYPES):
            s = "try{" + ",".join(_stmts(node.body))
            handler = [t for h in node.handlers for t in _stmts(h.body)]
            if handler:
                s += "|" + ",".join(handler)
            if node.finalbody:
                s += "|" + ",".join(_stmts(node.finalbody))
            out.append(s + "}")
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            out.append("with{" + ",".join(_stmts(node.body)) + "}")
        elif isinstance(node, ast.Match):
            arms = [t for c in node.cases for t in _stmts(c.body)]
            out.append("match{" + ",".join(arms) + "}")
        elif isinstance(node, ast.Return):
            out.append("ret")
        elif isinstance(node, ast.Raise):
            out.append("raise")
        elif isinstance(node, ast.Break):
            out.append("brk")
        elif isinstance(node, ast.Continue):
            out.append("cnt")
        elif isinstance(node, ast.AugAssign):
            out.append(_OPS.get(type(node.op), "?") + "=")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            out.append("=")
        elif isinstance(node, ast.Assert):
            out.append("assert")
        elif isinstance(node, ast.Delete):
            out.append("del")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append("imp")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append("def")
        elif isinstance(node, ast.ClassDef):
            out.append("class")
        elif isinstance(node, ast.Expr):
            v = node.value
            if isinstance(v, ast.Constant):
                continue  # docstring / bare literal
            if isinstance(v, (ast.Yield, ast.YieldFrom)):
                out.append("yield")
            elif isinstance(v, (ast.Call, ast.Await)):
                out.append("call")
            else:
                out.append("expr")
        elif isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal)):
            continue
        else:
            out.append("stmt")
    return out


def _py_anchors(node, own_name: str) -> tuple[Counter, bool, bool]:
    anchors: Counter = Counter()
    rec = gen = False
    call_funcs = {id(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name:
                anchors[name] += 1
                if name == own_name:
                    rec = True
        elif isinstance(n, ast.Attribute) and id(n) not in call_funcs:
            anchors[n.attr] += 1
        elif isinstance(n, (ast.BinOp, ast.UnaryOp)):
            anchors[_OPS.get(type(n.op), "?")] += 1
        elif isinstance(n, ast.BoolOp):
            anchors[_OPS.get(type(n.op), "?")] += max(1, len(n.values) - 1)
        elif isinstance(n, ast.Compare):
            for op in n.ops:
                anchors[_OPS.get(type(op), "?")] += 1
        elif isinstance(n, (ast.Yield, ast.YieldFrom)):
            gen = True
    return anchors, rec, gen


def _render(cfg: str, anchors: Counter, rec: bool, gen: bool) -> str:
    top = sorted(anchors.items(), key=lambda kv: (-kv[1], kv[0]))[:_ANCHOR_CAP]
    an = ",".join(f"{k}:{v}" for k, v in sorted(top))
    flags = [f for f, on in (("rec", rec), ("gen", gen)) if on]
    s = f"cfg={cfg[:_CFG_CAP] or 'lin'} an={an}"
    return s + (f" fl={'+'.join(flags)}" if flags else "")


def py_algo(node, own_name: str) -> str:
    """Skeleton of a Python function/method from its AST node."""
    cfg = ",".join(_stmts(node.body))
    anchors, rec, gen = _py_anchors(node, own_name)
    return _render(cfg, anchors, rec, gen)


# ---------------------------------------------------------------- literals
#
# The literal channel. Renaming, restructuring, and re-idioming all preserve
# behavior — but a behavior-preserving rewrite cannot change the constants the
# code emits or compares against. Rare literals ("(?:.*/)?") are therefore a
# near-unique signature that survives every disguise; common ones ("", 0, 1)
# are noise and get filtered by corpus rarity at scoring time.

_LIT_MIN_LEN = 2  # single chars are too common to identify anything
_LIT_CAP = 40


def _keep_literal(v) -> str | None:
    if isinstance(v, str):
        s = v.strip()
        return s if len(s) >= _LIT_MIN_LEN else None
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return repr(v) if abs(v) > 1 else None
    return None


def py_module_consts(tree) -> dict:
    """Module-level NAME = <literal> bindings, incl. simple containers.

    Hoisting a literal into a module constant is a favorite disguise; the value
    is still behavior-bound, so units referencing the name inherit it.
    """
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
                out[name] = lits[:_LIT_CAP]
    return out


def py_literals(node, module_consts: dict | None = None) -> frozenset:
    """Distinctive constants in a Python unit (docstrings excluded).

    Includes values of module-level constants the unit references.
    """
    docs = set()
    for n in ast.walk(node):
        body = getattr(n, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant):
            docs.add(id(body[0].value))
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and id(n) not in docs:
            lit = _keep_literal(n.value)
            if lit:
                out.add(lit)
        elif module_consts and isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            out.update(module_consts.get(n.id, ()))
    return frozenset(sorted(out)[:_LIT_CAP])


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


def js_literals(body: str, module_consts: dict | None = None) -> frozenset:
    """Distinctive constants in a JS/TS unit (regex-scanned from raw text)."""
    out = set()
    for m in _JS_STR.finditer(body):
        lit = _keep_literal(m.group(1) if m.group(1) is not None else m.group(2))
        if lit:
            out.add(lit)
    out.update(m.group() for m in _JS_NUM.finditer(body))
    if module_consts:
        for name, lits in module_consts.items():
            if re.search(rf"\b{re.escape(name)}\b", body):
                out.update(lits)
    return frozenset(sorted(out)[:_LIT_CAP])


# ---------------------------------------------------------------- javascript

_JS_CFG_KW = {"for", "while", "do", "if", "else", "switch", "try", "catch",
              "finally", "return", "throw", "break", "continue", "yield"}
_JS_KW_MAP = {"return": "ret", "break": "brk", "continue": "cnt", "throw": "raise"}
_JS_SKIP_CALL = _JS_CFG_KW | {"function", "new", "typeof", "await", "in", "of", "case"}
_JS_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
_JS_ATTR = re.compile(r"\.\s*([A-Za-z_$][\w$]*)(?!\s*\()")
_JS_OP = re.compile(r"===|!==|=>|==|!=|<=|>=|&&|\|\||[+\-*/%<>!]")


def js_algo(masked_body: str, own_name: str = "") -> str:
    """Flat skeleton of a JS/TS unit from its masked body text."""
    depth = 0
    cfg = []
    for m in re.finditer(r"[A-Za-z_$][\w$]*|[{}]", masked_body):
        t = m.group()
        if t == "{":
            depth += 1
        elif t == "}":
            depth = max(0, depth - 1)
        elif t in _JS_CFG_KW:
            cfg.append(_JS_KW_MAP.get(t, t) + str(depth))
    anchors: Counter = Counter()
    rec = False
    for m in _JS_CALL.finditer(masked_body):
        name = m.group(1)
        if name in _JS_SKIP_CALL:
            continue
        anchors[name] += 1
        if own_name and name == own_name:
            rec = True
    for m in _JS_ATTR.finditer(masked_body):
        anchors[m.group(1)] += 1
    for m in _JS_OP.finditer(masked_body):
        if m.group() != "=>":
            anchors[m.group()] += 1
    return _render(",".join(cfg[:64]), anchors, rec, any(c.startswith("yield") for c in cfg))


# ---------------------------------------------------------------- comparison

def _parse_algo(s: str) -> tuple[list[str], Counter]:
    m = re.search(r"cfg=(.*?) an=", s)
    cfg_tokens = re.findall(r"[^{}|,\s]+|[{}|]", m.group(1)) if m else []
    counts: Counter = Counter()
    m = re.search(r" an=([^ ]*)", s)
    if m:
        for pair in m.group(1).split(","):
            k, _, v = pair.rpartition(":")
            if k:
                try:
                    counts[k] = int(v)
                except ValueError:
                    pass
    return cfg_tokens, counts


def algo_similarity(a: str, b: str) -> float | None:
    """Structure similarity in [0,1]: half control-flow shape, half anchor overlap.

    None when either side has no skeleton (missing data, not disagreement).
    """
    if not a or not b:
        return None
    cfg_a, an_a = _parse_algo(a)
    cfg_b, an_b = _parse_algo(b)
    if not (cfg_a or an_a) or not (cfg_b or an_b):
        return None
    seq = SequenceMatcher(None, cfg_a, cfg_b, autojunk=False).ratio()
    union = sum((an_a | an_b).values())
    jac = sum((an_a & an_b).values()) / union if union else 0.0
    return round(0.5 * seq + 0.5 * jac, 3)


def anchor_diff(a: str, b: str, cap: int = 6) -> tuple[list[str], list[str]]:
    """Anchors present only on each side — the discrete 'why' for near-clones."""
    _, an_a = _parse_algo(a)
    _, an_b = _parse_algo(b)
    return sorted((an_a - an_b).keys())[:cap], sorted((an_b - an_a).keys())[:cap]
