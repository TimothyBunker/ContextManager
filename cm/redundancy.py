"""The tripwire engine: run every enabled detector, compose the evidence.

Design law: deterministic discrete features — grep beats semantic search.
The engine holds no detection logic of its own. Detectors (cm/detectors/)
each notice one kind of resemblance and emit Evidence; a unit is flagged for
REVIEW when any detector fires against any candidate. The engine's only jobs
are candidate ranking (sum of detector affinities), evidence composition,
and the report schema. It never judges — flagged units go to review, where
the agent reads both and decides; the accept ledger makes that decision
final.
"""
from __future__ import annotations

from .detectors import load_enabled
from .model import Unit

_TOP_EXAMINE = 8  # top-ranked candidates examined in detail per target


def _compose_match(cand: Unit, target: Unit, evidence: list) -> dict:
    tokens = next((e.tokens for e in evidence if e.tokens), [])
    blocks = next((e.blocks for e in evidence if e.blocks), [])
    coeff = (len(tokens) / min(len(target.feats), len(cand.feats))
             if target.feats and cand.feats else 0.0)
    return {
        "file": cand.path, "unit": cand.qualname, "span": [cand.start, cand.end],
        "exact_structural_dup": any(e.detector == "fingerprint" for e in evidence),
        "reasons": [e.reason for e in evidence],
        "evidence": [{"detector": e.detector, "reason": e.reason,
                      "tokens": e.tokens, "blocks": e.blocks} for e in evidence],
        "shared": tokens, "shared_count": len(tokens),
        "overlap_coeff": round(coeff, 3),
        "overlap_lines": sum(b["lines"] for b in blocks), "overlap": blocks,
    }


def score_targets(targets: list[Unit], corpus: list[Unit], top: int = 3,
                  detectors: list | None = None) -> list[dict]:
    """Screen each target unit against the corpus. One report dict per unit;
    report["action"] is "review", "pass", or "trivial"."""
    dets = load_enabled() if detectors is None else detectors
    for d in dets:
        d.prepare(corpus)

    reports = []
    for t in targets:
        report = {
            "unit": t.qualname, "kind": t.kind, "file": t.path,
            "span": [t.start, t.end], "signature": t.signature, "fp": t.fp,
            "trivial": t.trivial,
        }
        if t.trivial or not t.scoreable:
            report.update(action="trivial", matches=[])
            reports.append(report)
            continue

        def is_self(c: Unit) -> bool:
            return c is t or (c.path == t.path and c.start == t.start and c.end == t.end)

        ranked = sorted(
            ((sum(d.affinity(t, c) for d in dets), c)
             for c in corpus if c.scoreable and not is_self(c)),
            key=lambda ac: (-ac[0], ac[1].path, ac[1].start),
        )
        matches = []
        for affinity, c in ranked[:_TOP_EXAMINE]:
            if not affinity:
                break  # ranked list: nothing below has any detector's interest
            evidence = [e for d in dets if (e := d.examine(t, c)) is not None]
            if evidence:
                matches.append(_compose_match(c, t, evidence))
        matches.sort(key=lambda m: (-len(m["reasons"]), -m["shared_count"],
                                    -m["overlap_lines"]))
        report.update(
            action="review" if matches else "pass",
            matches=matches[:max(top, 1)],
        )
        reports.append(report)
    return reports


def requires_review(rep: dict) -> bool:
    return rep.get("action") == "review"
