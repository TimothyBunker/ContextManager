"""The detector contract: what a tripwire plugin is.

A detector is one self-contained way of noticing that a new unit resembles an
existing one. The engine (redundancy.score_targets) runs every enabled
detector over candidate pairs and flags a unit for review when any detector
produces Evidence. Detectors never judge — they report exact, explainable
observations; the agent reviews.

To write one:

    from .base import Detector, Evidence
    from . import register

    @register
    class MyDetector(Detector):
        name = "mine"

        def prepare(self, corpus):        # optional: once per screening run
            ...precompute corpus state...

        def affinity(self, target, cand) -> int:   # cheap; ranks candidates
            return 0                                # 0 = no interest

        def examine(self, target, cand) -> Evidence | None:  # may cost more
            return Evidence(self.name, "why this pair needs review", ...)

Contract rules: deterministic (same inputs, same output, forever), discrete
(evidence is tokens and line spans, not scores), self-contained (no state
outside prepare/instance), and honest (return None unless a reviewer should
actually look).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Unit


@dataclass
class Evidence:
    detector: str
    reason: str  # one printable sentence
    tokens: list[str] = field(default_factory=list)  # greppable shared tokens
    blocks: list[dict] = field(default_factory=list)  # matching line spans


class Detector:
    name = "base"

    def prepare(self, corpus: list[Unit]) -> None:
        pass

    def affinity(self, target: Unit, cand: Unit) -> int:
        return 0

    def examine(self, target: Unit, cand: Unit) -> Evidence | None:
        return None
