"""Fingerprint detector: catches pure renames.

The fingerprint hashes a unit's normalized body — comments stripped, literals
collapsed, bound names alpha-renamed, outside names kept — so two units with
the same fingerprint are the same code modulo naming. This is the only
resemblance cm treats as near-certain, and the cheapest to check.
"""
from __future__ import annotations

from . import register
from .base import Detector, Evidence
from ..model import Unit


@register
class FingerprintDetector(Detector):
    name = "fingerprint"

    def affinity(self, target: Unit, cand: Unit) -> int:
        return 1000 if cand.fp == target.fp else 0

    def examine(self, target: Unit, cand: Unit) -> Evidence | None:
        if cand.fp != target.fp:
            return None
        return Evidence(self.name, "identical fingerprint (a rename of the same code)")
