"""Tree scanning: walk the codebase, honor .cmignore, produce FileRecords.

With a cache (see cache.py) the scan is incremental: a file whose size+mtime
match the cache is restored without touching disk content; a file whose
content hash matches is restored without re-analysis. Only genuinely changed
files pay for extraction, so recompiling on every change stays cheap and the
global picture stays correct.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .cache import attach_cached_units, record_from_entry
from .extract import extract_units, lang_for
from .ignore import IgnoreRules
from .model import FileRecord, ScanResult

MAX_FILE_BYTES = 1_048_576


def read_file(abspath: Path, relpath: str) -> FileRecord | None:
    """Read and decode one file (no unit analysis). None if binary."""
    raw = abspath.read_bytes()
    if b"\0" in raw[:8192]:
        return None
    text = raw.decode("utf-8", errors="replace")
    crlf, lone_lf = text.count("\r\n"), text.count("\n") - text.count("\r\n")
    eol = "crlf" if crlf and not lone_lf else "mixed" if crlf else "lf"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return FileRecord(
        path=relpath, abspath=str(abspath), lang=lang_for(relpath), text=text,
        sha=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        size=len(raw), lines=len(text.split("\n")), eol=eol,
    )


def load_file(abspath: Path, relpath: str) -> FileRecord | None:
    """Read + analyze one file. None if binary."""
    rec = read_file(abspath, relpath)
    if rec is not None:
        extract_units(rec)
    return rec


def scan_tree(root: Path, rules: IgnoreRules | None = None,
              exclude_abs: set[str] | None = None,
              cache: dict | None = None) -> ScanResult:
    root = root.resolve()
    rules = rules or IgnoreRules.load(root)
    exclude_abs = exclude_abs or set()
    cache = cache or {}
    result = ScanResult(root=str(root))

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(
            d for d in dirnames
            if not rules.ignored(f"{rel_dir}/{d}" if rel_dir else d, is_dir=True)
        )
        for fname in sorted(filenames):
            rel = f"{rel_dir}/{fname}" if rel_dir else fname
            abspath = Path(dirpath) / fname
            if rules.ignored(rel, is_dir=False) or str(abspath.resolve()) in exclude_abs:
                continue
            try:
                st = abspath.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES:
                result.skipped_large.append(rel)
                continue
            entry = cache.get(rel)
            if entry and entry["size"] == st.st_size and entry["mtime_ns"] == st.st_mtime_ns:
                result.records.append(record_from_entry(rel, entry, str(abspath)))
                result.cache_hits += 1
                result.mtimes[rel] = st.st_mtime_ns
                continue
            rec = read_file(abspath, rel)
            if rec is None:
                result.skipped_binary.append(rel)
                continue
            result.mtimes[rel] = st.st_mtime_ns
            if entry and entry["sha"] == rec.sha:
                attach_cached_units(rec, entry)  # content unchanged, mtime drifted
                result.cache_hits += 1
            else:
                extract_units(rec)
                result.changed.append(rel)
            result.records.append(rec)

    result.records.sort(key=lambda r: r.path)
    return result
