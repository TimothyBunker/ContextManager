"""Line-overlap detector: catches copied cores buried in padding.

Lines are compared in per-line normalized form (bound names renamed, outside
names kept), so a renamed copy still aligns line-for-line with its source.
Padding around a copied core cannot shrink this overlap. Candidate ranking
uses cheap set intersection of normalized lines; contiguity and spans are
computed only for examined pairs.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from . import register
from .base import Detector, Evidence
from ..model import Unit
from ..normalize import norm_source

OVERLAP_LINES = 10  # matching normalized lines that are never idiom
OVERLAP_MIN = 6  # ...or fewer, when they cover most of the smaller unit
_MIN_BLOCK = 3


def _norm_lines(unit: Unit) -> list[str]:
    return [norm_source(s, unit.lang, unit.bound) for s in unit.body.split("\n")]


@register
class LineOverlapDetector(Detector):
    name = "lines"

    def __init__(self):
        self._lines: dict[int, list[str]] = {}
        self._sets: dict[int, frozenset] = {}

    def _get(self, unit: Unit) -> tuple[list[str], frozenset]:
        key = id(unit)
        if key not in self._lines:
            lines = _norm_lines(unit)
            self._lines[key] = lines
            self._sets[key] = frozenset(s for s in lines if s.strip())
        return self._lines[key], self._sets[key]

    def prepare(self, corpus: list[Unit]) -> None:
        self._lines, self._sets = {}, {}
        for c in corpus:
            self._get(c)

    def affinity(self, target: Unit, cand: Unit) -> int:
        return len(self._get(target)[1] & self._get(cand)[1])

    def blocks(self, target: Unit, cand: Unit, limit: int = 3) -> list[dict]:
        """Aligned matching line blocks, in original line numbers."""
        a, b = self._get(target)[0], self._get(cand)[0]
        a_raw = target.body.split("\n")
        sm = SequenceMatcher(None, a, b, autojunk=False)
        out = []
        for blk in sm.get_matching_blocks():
            if blk.size < _MIN_BLOCK:
                continue
            if sum(1 for s in a[blk.a:blk.a + blk.size] if s.strip()) < 2:
                continue  # a run of blank/comment-only lines is not evidence
            snippet = " / ".join(s.strip() for s in a_raw[blk.a:blk.a + 2] if s.strip())[:120]
            out.append({
                "target_lines": [target.start + blk.a, target.start + blk.a + blk.size - 1],
                "match_lines": [cand.start + blk.b, cand.start + blk.b + blk.size - 1],
                "lines": blk.size,
                "snippet": snippet,
            })
            if len(out) >= limit:
                break
        return out

    def examine(self, target: Unit, cand: Unit) -> Evidence | None:
        if not self.affinity(target, cand):
            return None
        blocks = self.blocks(target, cand)
        ov = sum(b["lines"] for b in blocks)
        min_lines = min(target.end - target.start + 1, cand.end - cand.start + 1)
        if ov >= OVERLAP_LINES or (ov >= OVERLAP_MIN and ov >= 0.5 * min_lines):
            return Evidence(self.name, f"{ov} matching normalized lines", blocks=blocks)
        return None
