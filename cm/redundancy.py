"""The tripwire: find existing code that resembles what an agent just wrote.

Design law: deterministic discrete features — grep beats semantic search.
There is no similarity score to tune and no verdict to compose. A unit is
flagged for REVIEW when any of four rules fires, each of which is exact,
explainable, and resistant to a specific disguise:

  1. identical fingerprint          (pure renames: fp hashes the normalized body)
  2. 3+ shared distinctive tokens   (restructures: rewrites can't change the
                                     values and names the code computes with)
  3. 2+ shared distinctive tokens   (small units, where 3 features may not exist)
     covering most of both feature sets
  4. 10+ matching normalized lines  (copied cores: padding can't shrink the
     with a single unit             overlap with the source)

"Distinctive" is measured against the corpus, not assumed: a token counts
only if it appears in few units (document frequency <= RARE_DF). The
tripwire never judges — flagged units go to review, where the agent reads
both and decides. The recorded decision (the accept ledger) is what makes a
review final.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from .features import COMMON
from .model import Unit
from .normalize import norm_source

RARE_DF = 3  # a feature in <= this many corpus units is distinctive
SHARED_STRONG = 3  # this many shared distinctive features is not coincidence
SHARED_WEAK = 2  # ...or fewer, when they dominate both feature sets
COEFF_FLOOR = 0.6
OVERLAP_LINES = 10  # matching normalized lines that are never idiom
OVERLAP_MIN = 6  # ...or fewer, when they cover most of the smaller unit
_TOP_EXAMINE = 8  # candidates ranked into detailed evidence per target


def overlap_blocks(target: Unit, cand: Unit, min_lines: int = 3, limit: int = 3) -> list[dict]:
    """Aligned matching line blocks between two units, in original line numbers.

    Lines are compared in per-line normalized form, so renamed clones still align.
    """
    a_raw, b_raw = target.body.split("\n"), cand.body.split("\n")
    a = [norm_source(s, target.lang, target.bound) for s in a_raw]
    b = [norm_source(s, cand.lang, cand.bound) for s in b_raw]
    sm = SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for blk in sm.get_matching_blocks():
        if blk.size < min_lines:
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


def _feature_df(corpus: list[Unit]) -> dict:
    df: dict[str, int] = {}
    for c in corpus:
        for f in c.feats:
            df[f] = df.get(f, 0) + 1
    return df


def _distinctive_shared(t: Unit, c: Unit, df: dict) -> list[str]:
    shared = [f for f in (t.feats & c.feats)
              if f not in COMMON and df.get(f, 0) <= RARE_DF]
    return sorted(shared, key=lambda f: (df.get(f, 0), -len(f)))


def _examine(t: Unit, c: Unit, df: dict, deep: bool) -> dict:
    shared = _distinctive_shared(t, c, df)
    n = len(shared)
    coeff = n / min(len(t.feats), len(c.feats)) if (t.feats and c.feats) else 0.0
    reasons = []
    if c.fp == t.fp:
        reasons.append("identical fingerprint (a rename of the same code)")
    if n >= SHARED_STRONG:
        reasons.append(f"{n} shared distinctive tokens")
    elif n >= SHARED_WEAK and coeff >= COEFF_FLOOR:
        reasons.append(f"{n} shared distinctive tokens covering "
                       f"{coeff:.0%} of the smaller unit's features")
    blocks = []
    ov = 0
    if deep or reasons:
        blocks = overlap_blocks(t, c)
        ov = sum(b["lines"] for b in blocks)
        min_lines = min(t.end - t.start + 1, c.end - c.start + 1)
        if ov >= OVERLAP_LINES or (ov >= OVERLAP_MIN and ov >= 0.5 * min_lines):
            reasons.append(f"{ov} matching normalized lines")
    return {
        "file": c.path, "unit": c.qualname, "span": [c.start, c.end],
        "exact_structural_dup": c.fp == t.fp,
        "shared": shared[:8], "shared_count": n,
        "overlap_coeff": round(coeff, 3),
        "overlap_lines": ov, "overlap": blocks,
        "reasons": reasons,
    }


def score_targets(targets: list[Unit], corpus: list[Unit], top: int = 3) -> list[dict]:
    """Screen each target unit against the corpus. One report dict per unit;
    report["action"] is "review", "pass", or "trivial"."""
    df = _feature_df(corpus)
    fp_index: dict[str, list[Unit]] = {}
    for c in corpus:
        fp_index.setdefault(c.fp, []).append(c)

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

        # rank every candidate by cheap set arithmetic; no similarity model
        ranked = sorted(
            (c for c in corpus if c.scoreable and not is_self(c)),
            key=lambda c: (-(len(_distinctive_shared(t, c, df))
                            + (100 if c.fp == t.fp else 0)),
                           c.path, c.start),
        )
        matches = []
        for i, c in enumerate(ranked[:_TOP_EXAMINE]):
            m = _examine(t, c, df, deep=i < top)
            if m["reasons"] or (i < top and (m["shared_count"] or m["overlap_lines"])):
                matches.append(m)
        matches.sort(key=lambda m: (-len(m["reasons"]), -m["shared_count"], -m["overlap_lines"]))
        report.update(
            action="review" if any(m["reasons"] for m in matches) else "pass",
            matches=matches[:max(top, sum(1 for m in matches if m["reasons"]))],
        )
        reports.append(report)
    return reports


def requires_review(rep: dict) -> bool:
    return rep.get("action") == "review"
