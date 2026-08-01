"""The .cm format: emit and parse PROJECT.cm.

Line-oriented and human/LLM-readable. Lines starting with `::` are directives;
file contents are embedded verbatim in a block whose exact line count is
declared, so content can safely contain `::`.

    ::cm 0.1
    ::project NAME
    ::stats files=N units=M ...
    ::file path/to/file.py
    ::lang python
    ::sha 4b0c...        (sha256 of normalized text, 12 hex)
    ::doc First line of the module docstring.
    ::imports os, re
    ::unit function scan_tree @14-52 #a1b2c3d4   (#fp = structural fingerprint)
    ::sig scan_tree(root, rules) -> ScanResult
    ::doc Walk the codebase honoring .cmignore.
    ::keys ["IgnoreRules", "cmignore", "os.walk", ...]   (discrete features, greppable)
    ::content 140
    ...140 lines verbatim...
    ::endfile
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .model import FileRecord

FORMAT_VERSION = "0.3"


def emit(meta: dict, records: list[FileRecord], include_content: bool = True) -> str:
    out = [f"::cm {FORMAT_VERSION}"]
    for k, v in meta.items():
        if k == "stats":
            out.append("::stats " + " ".join(f"{sk}={sv}" for sk, sv in v.items()))
        else:
            out.append(f"::{k} {v}")
    out.append("")
    for rec in records:
        out.append(f"::file {rec.path}")
        out.append(f"::lang {rec.lang}")
        out.append(f"::sha {rec.sha}")
        out.append(f"::lines {rec.lines}")
        if rec.eol != "lf":
            out.append(f"::eol {rec.eol}")
        if rec.doc:
            out.append(f"::doc {rec.doc}")
        if rec.imports:
            out.append("::imports " + ", ".join(rec.imports))
        for u in rec.units:
            out.append(f"::unit {u.kind} {u.qualname} @{u.start}-{u.end} #{u.fp}")
            if u.signature:
                out.append(f"::sig {u.signature}")
            if u.doc:
                out.append(f"::doc {u.doc}")
            if u.feats:
                out.append("::keys " + json.dumps(sorted(u.feats)))
        if include_content:
            lines = rec.text.split("\n")
            out.append(f"::content {len(lines)}")
            out.extend(lines)
        out.append("::endfile")
        out.append("")
    return "\n".join(out)


@dataclass
class CmUnit:
    kind: str
    qualname: str
    start: int
    end: int
    fp: str
    signature: str = ""
    doc: str = ""
    keys: list[str] = field(default_factory=list)


@dataclass
class CmFile:
    path: str
    lang: str = ""
    sha: str = ""
    lines: int = 0
    eol: str = "lf"
    doc: str = ""
    imports: list[str] = field(default_factory=list)
    units: list[CmUnit] = field(default_factory=list)
    content: str | None = None


class CmParseError(ValueError):
    pass


def parse(text: str) -> tuple[dict, list[CmFile]]:
    lines = text.split("\n")
    if not lines or not lines[0].startswith("::cm "):
        raise CmParseError("not a .cm file: missing '::cm' header")
    meta: dict = {"cm": lines[0][5:].strip()}
    files: list[CmFile] = []
    cur: CmFile | None = None
    i = 1
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.startswith("::"):
            continue
        directive, _, rest = line[2:].partition(" ")
        rest = rest.strip()
        if directive == "file":
            cur = CmFile(path=rest)
            files.append(cur)
        elif directive == "endfile":
            cur = None
        elif directive == "stats":
            stats = {}
            for pair in rest.split():
                k, _, v = pair.partition("=")
                stats[k] = v
            meta["stats"] = stats
        elif cur is None:
            meta[directive] = rest
        elif directive == "content":
            try:
                n = int(rest)
            except ValueError:
                raise CmParseError(f"line {i}: bad ::content count {rest!r}")
            if i + n > len(lines):
                raise CmParseError(f"line {i}: ::content declares {n} lines but file ends early")
            cur.content = "\n".join(lines[i:i + n])
            i += n
        elif directive == "unit":
            try:
                kind, qualname, span, fp = rest.split(" ")
                a, _, b = span.lstrip("@").partition("-")
                cur.units.append(CmUnit(kind, qualname, int(a), int(b), fp.lstrip("#")))
            except ValueError:
                raise CmParseError(f"line {i}: bad ::unit line {rest!r}")
        elif directive == "sig" and cur.units:
            cur.units[-1].signature = rest
        elif directive == "keys" and cur.units:
            try:
                cur.units[-1].keys = list(json.loads(rest))
            except ValueError:
                raise CmParseError(f"line {i}: bad ::keys payload")
        elif directive == "doc":
            if cur.units:
                cur.units[-1].doc = rest
            else:
                cur.doc = rest
        elif directive == "lang":
            cur.lang = rest
        elif directive == "sha":
            cur.sha = rest
        elif directive == "lines":
            cur.lines = int(rest)
        elif directive == "eol":
            cur.eol = rest
        elif directive == "imports":
            cur.imports = [s.strip() for s in rest.split(",") if s.strip()]
    return meta, files
