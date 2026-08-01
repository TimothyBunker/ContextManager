"""Incremental compile state: the .cm/ directory.

Holds the compile cache (per-file text, hashes, mtimes, and fully-analyzed
units) and the accept-list of reviewed fingerprints. The cache is what makes
"recompile on every change" cheap: unchanged files are restored from here
without re-reading or re-analyzing; the baseline it represents is also what
`cm gate` diffs new writes against.
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

from . import __version__
from .model import FileRecord, ScanResult, Unit

CM_DIR = ".cm"
_CACHE = "cache.json.gz"
_ACCEPTED = "accepted"


def cm_dir(root: Path) -> Path:
    return root / CM_DIR


def find_root(start: Path) -> Path | None:
    """Walk up from `start` to the nearest cm-initialized directory."""
    d = Path(start).resolve()
    while True:
        if (d / CM_DIR).is_dir() or (d / "PROJECT.cm").is_file():
            return d
        if d.parent == d:
            return None
        d = d.parent


def _unit_to_dict(u: Unit) -> dict:
    return {
        "kind": u.kind, "name": u.name, "qualname": u.qualname,
        "signature": u.signature, "start": u.start, "end": u.end,
        "doc": u.doc, "norm": u.norm, "fp": u.fp,
        "scoreable": u.scoreable, "trivial": u.trivial, "bound": sorted(u.bound),
        "feats": sorted(u.feats),
    }


def _unit_from_dict(d: dict, lines: list[str], path: str, lang: str) -> Unit:
    return Unit(
        kind=d["kind"], name=d["name"], qualname=d["qualname"],
        signature=d["signature"], start=d["start"], end=d["end"],
        doc=d["doc"], body="\n".join(lines[d["start"] - 1:d["end"]]),
        norm=d["norm"], fp=d["fp"],
        scoreable=d["scoreable"], trivial=d["trivial"],
        path=path, lang=lang, bound=frozenset(d["bound"]),
        feats=frozenset(d.get("feats", ())),
    )


def entry_from_record(rec: FileRecord, mtime_ns: int) -> dict:
    return {
        "sha": rec.sha, "size": rec.size, "mtime_ns": mtime_ns,
        "lang": rec.lang, "lines": rec.lines, "eol": rec.eol,
        "doc": rec.doc, "imports": rec.imports, "text": rec.text,
        "units": [_unit_to_dict(u) for u in rec.units],
    }


def record_from_entry(path: str, entry: dict, abspath: str) -> FileRecord:
    rec = FileRecord(
        path=path, abspath=abspath, lang=entry["lang"], text=entry["text"],
        sha=entry["sha"], size=entry["size"], lines=entry["lines"],
        eol=entry["eol"], doc=entry["doc"], imports=list(entry["imports"]),
    )
    lines = rec.text.split("\n")
    rec.units = [_unit_from_dict(d, lines, path, rec.lang) for d in entry["units"]]
    return rec


def attach_cached_units(rec: FileRecord, entry: dict) -> None:
    """Reuse analyzed units for a file whose content hash is unchanged."""
    lines = rec.text.split("\n")
    rec.doc = entry["doc"]
    rec.imports = list(entry["imports"])
    rec.units = [_unit_from_dict(d, lines, rec.path, rec.lang) for d in entry["units"]]


def load_cache(root: Path) -> dict:
    """path -> entry mapping, or {} when absent/stale/incompatible."""
    p = cm_dir(root) / _CACHE
    if not p.is_file():
        return {}
    try:
        data = json.loads(gzip.decompress(p.read_bytes()))
        if data.get("cm") != __version__:
            return {}
        return data.get("files", {})
    except (OSError, ValueError):
        return {}


def save_cache(root: Path, result: ScanResult) -> None:
    files = {
        rec.path: entry_from_record(rec, result.mtimes.get(rec.path, 0))
        for rec in result.records
    }
    payload = gzip.compress(
        json.dumps({"cm": __version__, "files": files},
                   separators=(",", ":")).encode("utf-8"), 6)
    d = cm_dir(root)
    d.mkdir(exist_ok=True)
    tmp = d / (_CACHE + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, d / _CACHE)


def load_accepted(root: Path) -> set[str]:
    p = cm_dir(root) / _ACCEPTED
    if not p.is_file():
        return set()
    out = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def add_accepted(root: Path, fps: list[str], reason: str = "") -> None:
    d = cm_dir(root)
    d.mkdir(exist_ok=True)
    suffix = f"  # {reason}" if reason else ""
    with open(d / _ACCEPTED, "a", encoding="utf-8") as f:
        for fp in fps:
            f.write(fp + suffix + "\n")
