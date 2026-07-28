"""Shared data records for the cm pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Unit:
    """One indexable section of a file: a function, method, class, or the file itself."""

    kind: str  # "function" | "method" | "class" | "file"
    name: str
    qualname: str
    signature: str
    start: int  # 1-based line span, inclusive
    end: int
    doc: str = ""
    body: str = ""
    norm: str = ""  # normalized body: bound names alpha-renamed, anchors kept
    fp: str = ""  # structural fingerprint: sha256(norm)[:8]
    algo: str = ""  # algorithm skeleton: control-flow shape + anchor multiset
    bound: frozenset = frozenset()  # names this unit binds (params, locals, own name)
    scoreable: bool = True  # participates in redundancy scoring
    trivial: bool = False  # too small to score meaningfully
    path: str = ""  # posix relpath of owning file
    lang: str = ""


@dataclass
class FileRecord:
    path: str  # posix relpath from root
    abspath: str
    lang: str
    text: str  # utf-8 decoded, newlines normalized to \n
    sha: str  # sha256(text)[:12]
    size: int  # raw size in bytes on disk
    lines: int
    eol: str = "lf"  # "lf" | "crlf" | "mixed"
    doc: str = ""
    imports: list[str] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)


@dataclass
class ScanResult:
    root: str
    records: list[FileRecord] = field(default_factory=list)
    skipped_binary: list[str] = field(default_factory=list)
    skipped_large: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)  # paths (re)analyzed this scan
    cache_hits: int = 0
    mtimes: dict = field(default_factory=dict)  # path -> st_mtime_ns

    def all_units(self, scoreable_only: bool = False) -> list[Unit]:
        out = []
        for rec in self.records:
            for u in rec.units:
                if scoreable_only and not u.scoreable:
                    continue
                out.append(u)
        return out
