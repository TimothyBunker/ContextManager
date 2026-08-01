"""Shared-tokens detector: catches restructuring.

A behavior-preserving rewrite can rename everything and reshape the control
flow, but it cannot change the outside names and literal constants the code
computes with. Sharing several *distinctive* tokens with one existing unit is
not coincidence. Distinctive is decided by the corpus (document frequency),
with language-universal vocabulary excluded outright.
"""
from __future__ import annotations

from . import register
from .base import Detector, Evidence
from ..features import COMMON
from ..model import Unit

RARE_DF = 3  # a feature in <= this many corpus units is distinctive
SHARED_STRONG = 3  # this many shared distinctive tokens is not coincidence
SHARED_WEAK = 2  # ...or fewer, when they dominate both feature sets
COEFF_FLOOR = 0.6


@register
class SharedTokensDetector(Detector):
    name = "tokens"

    def __init__(self):
        self._df: dict[str, int] = {}

    def prepare(self, corpus: list[Unit]) -> None:
        self._df = {}
        for c in corpus:
            for f in c.feats:
                self._df[f] = self._df.get(f, 0) + 1

    def shared(self, target: Unit, cand: Unit) -> list[str]:
        out = [f for f in (target.feats & cand.feats)
               if f not in COMMON and self._df.get(f, 0) <= RARE_DF]
        return sorted(out, key=lambda f: (self._df.get(f, 0), -len(f)))

    def affinity(self, target: Unit, cand: Unit) -> int:
        return len(self.shared(target, cand))

    def examine(self, target: Unit, cand: Unit) -> Evidence | None:
        shared = self.shared(target, cand)
        n = len(shared)
        if not (target.feats and cand.feats):
            return None
        coeff = n / min(len(target.feats), len(cand.feats))
        if n >= SHARED_STRONG:
            reason = f"{n} shared distinctive tokens"
        elif n >= SHARED_WEAK and coeff >= COEFF_FLOOR:
            reason = (f"{n} shared distinctive tokens covering "
                      f"{coeff:.0%} of the smaller unit's features")
        else:
            return None
        return Evidence(self.name, reason, tokens=shared[:8])
