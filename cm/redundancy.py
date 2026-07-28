"""Information-theoretic redundancy scoring.

A compressor stands in for the probability model q: the cost in bits of
encoding x under q is len(compress(x)) * 8, i.e. an upper bound on the
cross-entropy of x. Two quantities drive everything:

  standalone_bits(u)          C(u)      what u costs with no context
  conditional_bits(u, K)      C(u|K)    what u costs given corpus K as context

Redundancy R(u) = 1 - C(u|K) / C(u), in [0, 1]. R near 1 means the corpus
already predicts u — the unit adds almost no new information to the project.

Pairwise scoring (the "where") uses zlib with the candidate's normalized body
as a preset dictionary; corpus scoring (the overall verdict) uses lzma with
the best candidates concatenated as context. The "why" is recovered by
aligning matching line blocks between the unit and its best candidates.
"""
from __future__ import annotations

import lzma
import zlib
from difflib import SequenceMatcher

from .model import Unit
from .normalize import norm_source
from .skeleton import algo_similarity, anchor_diff

_CTX_SEP = b"\n\x00\n"
_CTX_CAP = 262_144  # cap on corpus context fed to lzma, in chars
_TOP_CTX_UNITS = 64

# Partial-clone escalation: a copied core padded with novel wrapper code
# dilutes the whole-unit compression score below any threshold, but the
# padding cannot shrink the contiguous overlap with the source, and it only
# weakly dilutes the algorithm-shape similarity. Escalate on either.
_ESC_OVERLAP_LINES = 10  # this many overlapping normalized lines is never idiom
_ESC_OVERLAP_MIN = 6  # ...or fewer, when they cover most of the smaller unit
_ESC_ALGO = 0.70
_ESC_ALGO_MIN_SCORE = 0.2

# Literal channel: a behavior-preserving rewrite may rename everything and
# restructure the control flow, but it cannot change the constants the code
# emits or compares against. Literals rare in the corpus are near-unique
# signatures; common ones ("utf-8", 100) are filtered by document frequency.
_LIT_RARE_DF = 3  # a literal in <= this many corpus units counts as distinctive
_ESC_LIT_SHARED = 3  # sharing this many rare literals is not coincidence
_ESC_LIT_JACCARD = 0.6  # ...or a high enough share of a smaller literal set
_EVIDENCE_CANDIDATES = 5  # extra units to examine on evidence rather than score


def rare_literal_overlap(target: Unit, cand: Unit, df: dict) -> tuple[int, list[str]]:
    """Count of shared corpus-rare literals between two units, plus examples."""
    shared = [l for l in (target.lits & cand.lits) if df.get(l, 0) <= _LIT_RARE_DF]
    return len(shared), sorted(shared, key=len, reverse=True)[:4]


def standalone_bits(data: bytes) -> float:
    if not data:
        return 8.0
    return max(8.0, float(len(_zlib_deflate(data)) * 8))


def _zlib_deflate(data: bytes, zdict: bytes | None = None) -> bytes:
    if zdict:
        c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS, 9,
                             zlib.Z_DEFAULT_STRATEGY, zdict[-32768:])
    else:
        c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS, 9)
    return c.compress(data) + c.flush()


def pair_redundancy(new_norm: str, cand_norm: str) -> float:
    """1 - C(new|cand)/C(new) via zlib preset-dictionary compression."""
    nb = new_norm.encode("utf-8")
    base = standalone_bits(nb)
    cond = max(8.0, float(len(_zlib_deflate(nb, cand_norm.encode("utf-8"))) * 8))
    return max(0.0, 1.0 - cond / base)


def corpus_conditional_bits(new_norm: str, ctx_norms: list[str]) -> float:
    """C(new | ctx) via lzma: C(ctx + new) - C(ctx)."""
    nb = new_norm.encode("utf-8")
    if not ctx_norms:
        return float(len(lzma.compress(nb)) * 8)
    ctx = "\n".join(ctx_norms)[:_CTX_CAP].encode("utf-8") + _CTX_SEP
    return max(8.0, float((len(lzma.compress(ctx + nb)) - len(lzma.compress(ctx))) * 8))


def token_similarity(a: str, b: str, cap: int = 4000) -> float:
    return SequenceMatcher(None, a[:cap], b[:cap], autojunk=False).ratio()


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


def score_targets(targets: list[Unit], corpus: list[Unit], top: int = 3) -> list[dict]:
    """Score each target unit against the corpus. Returns one report dict per unit."""
    reports = []
    fp_index: dict[str, list[Unit]] = {}
    lit_df: dict[str, int] = {}
    for c in corpus:
        fp_index.setdefault(c.fp, []).append(c)
        for lit in c.lits:
            lit_df[lit] = lit_df.get(lit, 0) + 1

    for t in targets:
        nb = t.norm.encode("utf-8")
        base = standalone_bits(nb)
        report = {
            "unit": t.qualname, "kind": t.kind, "file": t.path,
            "span": [t.start, t.end], "signature": t.signature, "fp": t.fp,
            "standalone_bits": round(base),
            "trivial": t.trivial,
        }
        if t.trivial or not t.scoreable:
            report.update(marginal_bits=round(base), redundancy=0.0, best_pair=0.0,
                          corpus_redundancy=0.0, best_structure=None,
                          wasted_bits=0, matches=[])
            reports.append(report)
            continue

        scored: list[tuple[float, Unit]] = []
        exact = [c for c in fp_index.get(t.fp, []) if not (c.path == t.path and c.start == t.start)]
        for c in corpus:
            if c is t or (c.path == t.path and c.start == t.start and c.end == t.end):
                continue
            if not c.scoreable:
                continue
            scored.append((1.0 if c.fp == t.fp else pair_redundancy(t.norm, c.norm), c))
        scored.sort(key=lambda x: x[0], reverse=True)

        ctx = [c.norm for _, c in scored[:_TOP_CTX_UNITS]]
        marginal = corpus_conditional_bits(t.norm, ctx)
        corpus_red = max(0.0, 1.0 - marginal / base) if ctx else 0.0
        best_pair = scored[0][0] if scored else 0.0
        redundancy = max(corpus_red, best_pair)

        # Candidates to examine in detail: the best compression matches, plus
        # any unit the cheap evidence channels flag. A restructured clone can
        # rank poorly on compression while sharing rare literals or shape —
        # selecting only by score would never look at it.
        by_score = [c for _, c in scored[:top]]
        seen_ids = {id(c) for c in by_score}
        extra = []
        for score_c, c in scored[top:]:
            n_l, _ = rare_literal_overlap(t, c, lit_df)
            a_s = algo_similarity(t.algo, c.algo)
            if n_l >= 2 or (a_s is not None and a_s >= _ESC_ALGO):
                extra.append((score_c, c))
                seen_ids.add(id(c))
            if len(extra) >= _EVIDENCE_CANDIDATES:
                break
        examine = scored[:top] + extra

        matches = []
        escalated = None
        for score, c in examine:
            if score <= 0.05 and id(c) not in seen_ids:
                continue
            sim = algo_similarity(t.algo, c.algo)
            overlap = overlap_blocks(t, c)
            ov_lines = sum(o["lines"] for o in overlap)
            min_lines = min(t.end - t.start + 1, c.end - c.start + 1)
            n_lits, lit_examples = rare_literal_overlap(t, c, lit_df)
            lit_jac = n_lits / min(len(t.lits), len(c.lits)) if (t.lits and c.lits) else 0.0
            esc_reason = None
            if ov_lines >= _ESC_OVERLAP_LINES or (
                    ov_lines >= _ESC_OVERLAP_MIN and ov_lines >= 0.5 * min_lines):
                esc_reason = f"{ov_lines} normalized lines overlap"
            elif n_lits >= _ESC_LIT_SHARED or (n_lits >= 2 and lit_jac >= _ESC_LIT_JACCARD):
                shown = ", ".join(repr(l) for l in lit_examples)
                esc_reason = (f"{n_lits} rare literal constants shared ({shown}) — "
                              f"behavior-bound values a rewrite cannot change")
            elif sim is not None and sim >= _ESC_ALGO and score >= _ESC_ALGO_MIN_SCORE:
                esc_reason = f"algorithm shape match (algo-sim {sim:.2f})"
            entry = {
                "file": c.path, "unit": c.qualname, "span": [c.start, c.end],
                "score": round(score, 3),
                "exact_structural_dup": c.fp == t.fp,
                "token_similarity": round(token_similarity(t.norm, c.norm), 3),
                "algo_similarity": sim,
                "overlap_lines": ov_lines,
                "shared_rare_literals": n_lits,
                "overlap": overlap,
            }
            if sim is not None and (score >= 0.5 or esc_reason):
                only_t, only_c = anchor_diff(t.algo, c.algo)
                if only_t or only_c:
                    entry["anchor_diff"] = {"only_target": only_t, "only_match": only_c}
            if esc_reason and escalated is None:
                escalated = {"file": c.path, "unit": c.qualname, "reason": esc_reason,
                             "overlap_lines": ov_lines, "algo_similarity": sim,
                             "shared_rare_literals": n_lits}
            matches.append(entry)
        report.update(
            marginal_bits=round(marginal),
            redundancy=round(redundancy, 3),
            best_pair=round(best_pair, 3),
            corpus_redundancy=round(corpus_red, 3),
            best_structure=matches[0]["algo_similarity"] if matches else None,
            best_literal_overlap=max((m["shared_rare_literals"] for m in matches), default=0),
            wasted_bits=round(base * redundancy),
            exact_dups=[f"{c.path}#{c.qualname}" for c in exact],
            escalated=escalated,
            matches=matches,
        )
        reports.append(report)
    return reports


def verdict(rep: dict, warn: float, fail: float) -> str:
    """Classify a unit report from two corroborating channels.

    The specific best-pair (info) match drives the verdict, but a "duplicate"
    claim additionally requires the structure channel to corroborate: an
    info-hot match whose algorithm skeleton disagrees is shared idiom, not
    duplication, and is downgraded to "overlap". The corpus-conditional
    measure is held to a higher bar because normalized same-language code is
    always somewhat cross-predictable (a language prior, not duplication) —
    it exists to catch units stitched together from several sources, which
    no single pair reveals.
    """
    if rep.get("escalated"):
        return "duplicate"  # partial-clone evidence: padding can't dilute it
    bp = rep.get("best_pair", 0.0)
    cr = rep.get("corpus_redundancy", 0.0)
    bs = rep.get("best_structure")
    corroborated = bs is None or bs >= 0.5
    if (bp >= fail and corroborated) or cr >= min(0.97, fail + 0.15):
        return "duplicate"
    if bp >= warn or cr >= min(0.92, warn + 0.30):
        return "overlap"
    return "novel"
