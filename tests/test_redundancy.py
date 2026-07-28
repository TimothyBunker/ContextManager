import unittest

from tests.test_extract import make_record
from cm.redundancy import pair_redundancy, score_targets, verdict

ORIGINAL = '''def aggregate_metrics(samples, window):
    """Aggregate raw samples into windowed metric rows."""
    rows = []
    bucket = []
    for sample in samples:
        bucket.append(sample.value * sample.weight)
        if len(bucket) >= window:
            total = sum(bucket)
            rows.append({"count": len(bucket), "total": total, "mean": total / len(bucket)})
            bucket = []
    if bucket:
        total = sum(bucket)
        rows.append({"count": len(bucket), "total": total, "mean": total / len(bucket)})
    return rows
'''

# Only *bound* names renamed (function, params, locals); same attributes,
# operators, and callees. Structurally the identical function.
PURE_RENAME = '''def fold_readings(points, span):
    """Collapse sensor readings into fixed-size summary chunks."""
    chunks = []
    acc = []
    for point in points:
        acc.append(point.value * point.weight)
        if len(acc) >= span:
            subtotal = sum(acc)
            chunks.append({"count": len(acc), "total": subtotal, "mean": subtotal / len(acc)})
            acc = []
    if acc:
        subtotal = sum(acc)
        chunks.append({"count": len(acc), "total": subtotal, "mean": subtotal / len(acc)})
    return chunks
'''

# Same algorithm applied to a different data interface: attribute anchors
# differ (.amount/.scale vs .value/.weight). Near-duplicate, but not exact.
INTERFACE_VARIANT = '''def fold_readings(points, span):
    """Collapse sensor readings into fixed-size summary chunks."""
    chunks = []
    acc = []
    for point in points:
        acc.append(point.amount * point.scale)
        if len(acc) >= span:
            subtotal = sum(acc)
            chunks.append({"n": len(acc), "sum": subtotal, "avg": subtotal / len(acc)})
            acc = []
    if acc:
        subtotal = sum(acc)
        chunks.append({"n": len(acc), "sum": subtotal, "avg": subtotal / len(acc)})
    return chunks
'''

UNRELATED = '''def parse_header_line(line):
    """Split an RFC-style header line into key and value."""
    if ":" not in line:
        raise ValueError("malformed header: " + line)
    key, _, value = line.partition(":")
    key = key.strip().lower()
    if not key or any(ch.isspace() for ch in key):
        raise ValueError("bad header key: " + key)
    return key, value.strip()
'''

# The fickle pair: identical shape, one flipped anchor. Same information
# almost everywhere — NOT the same function.
FICKLE_GE = '''def check_ratio_upper(self):
    measured = compute_ratio(self.sample_a, self.sample_b)
    self.assertGreaterEqual(measured, 0.55, "ratio unexpectedly low for this corpus")
'''
FICKLE_LE = '''def check_ratio_lower(self):
    observed = compute_ratio(self.sample_a, self.sample_b)
    self.assertLessEqual(observed, 0.55, "ratio unexpectedly high for this corpus")
'''

# The padding attack (found in the wild by an agent session): the copied core
# of aggregate_metrics buried inside enough novel wrapper code that the
# whole-unit compression score sinks below every threshold.
PADDED_CLONE = '''def summarize_batches(entries, batch_size, label="batch"):
    """Summarize entries in labeled batches with an envelope around the report."""
    if entries is None:
        raise ValueError("entries may not be None for " + label)
    if batch_size <= 0:
        raise ValueError("batch_size must be positive, got " + str(batch_size))
    header = {"label": label, "received": len(entries)}
    cleaned = []
    for candidate in entries:
        if candidate is None:
            continue
        cleaned.append(candidate)
    dropped = len(entries) - len(cleaned)
    rows = []
    bucket = []
    for sample in cleaned:
        bucket.append(sample.value * sample.weight)
        if len(bucket) >= batch_size:
            total = sum(bucket)
            rows.append({"count": len(bucket), "total": total, "mean": total / len(bucket)})
            bucket = []
    if bucket:
        total = sum(bucket)
        rows.append({"count": len(bucket), "total": total, "mean": total / len(bucket)})
    footer = {"label": label, "rows": len(rows), "dropped": dropped}
    return {"header": header, "rows": rows, "footer": footer}
'''


def units_of(path, src):
    return [u for u in make_record(path, "python", src).units if u.kind != "class"]


class TestPairRedundancy(unittest.TestCase):
    def test_pure_rename_scores_high(self):
        [orig] = units_of("a.py", ORIGINAL)
        [clone] = units_of("b.py", PURE_RENAME)
        score = pair_redundancy(clone.norm, orig.norm)
        self.assertGreaterEqual(score, 0.55, f"pure-rename score too low: {score:.3f}")

    def test_unrelated_scores_low(self):
        [orig] = units_of("a.py", ORIGINAL)
        [other] = units_of("c.py", UNRELATED)
        score = pair_redundancy(other.norm, orig.norm)
        self.assertLessEqual(score, 0.40, f"unrelated score too high: {score:.3f}")

    def test_pure_rename_same_fingerprint(self):
        [orig] = units_of("a.py", ORIGINAL)
        [clone] = units_of("b.py", PURE_RENAME)
        self.assertEqual(orig.fp, clone.fp)

    def test_interface_variant_different_fingerprint(self):
        # anchors (.value/.weight vs .amount/.scale) survive normalization
        [orig] = units_of("a.py", ORIGINAL)
        [variant] = units_of("b.py", INTERFACE_VARIANT)
        self.assertNotEqual(orig.fp, variant.fp)

    def test_fickle_pair_distinguished(self):
        # assertGreaterEqual vs assertLessEqual: was an exact-dup collision
        # under full alpha-renaming; anchors must now keep them apart
        [ge] = units_of("g.py", FICKLE_GE)
        [le] = units_of("l.py", FICKLE_LE)
        self.assertNotEqual(ge.fp, le.fp)
        self.assertLess(pair_redundancy(ge.norm, le.norm), 1.0)


class TestScoreTargets(unittest.TestCase):
    def test_pure_rename_flagged_as_exact_duplicate(self):
        corpus = units_of("a.py", ORIGINAL) + units_of("c.py", UNRELATED)
        [rep] = score_targets(units_of("new.py", PURE_RENAME), corpus)
        self.assertGreaterEqual(rep["redundancy"], 0.80)
        self.assertEqual(verdict(rep, 0.55, 0.80), "duplicate")
        best = rep["matches"][0]
        self.assertEqual((best["file"], best["unit"]), ("a.py", "aggregate_metrics"))
        self.assertTrue(best["exact_structural_dup"])

    def test_interface_variant_flagged_with_anchor_evidence(self):
        corpus = units_of("a.py", ORIGINAL) + units_of("c.py", UNRELATED)
        [rep] = score_targets(units_of("new.py", INTERFACE_VARIANT), corpus)
        best = rep["matches"][0]
        self.assertEqual(best["unit"], "aggregate_metrics")
        self.assertFalse(best["exact_structural_dup"])
        self.assertNotEqual(verdict(rep, 0.55, 0.80), "novel")
        self.assertGreaterEqual(best["algo_similarity"], 0.8)
        self.assertIn("amount", best["anchor_diff"]["only_target"])
        self.assertIn("value", best["anchor_diff"]["only_match"])

    def test_novel_function_stays_novel(self):
        corpus = units_of("a.py", ORIGINAL)
        [rep] = score_targets(units_of("new.py", UNRELATED), corpus)
        self.assertEqual(verdict(rep, 0.55, 0.80), "novel")

    def test_marginal_bits_smaller_for_clone(self):
        corpus = units_of("a.py", ORIGINAL) + units_of("c.py", UNRELATED)
        [clone_rep] = score_targets(units_of("n1.py", PURE_RENAME), corpus)
        # the corpus predicts the clone far better than it predicts anything novel
        self.assertLess(clone_rep["marginal_bits"], clone_rep["standalone_bits"] * 0.5)

    def test_padded_clone_escalated_despite_diluted_score(self):
        corpus = units_of("a.py", ORIGINAL) + units_of("c.py", UNRELATED)
        [rep] = score_targets(units_of("new.py", PADDED_CLONE), corpus)
        # the padding must have diluted the aggregate score below the fail bar —
        # otherwise this fixture no longer tests the escalation path
        self.assertLess(rep["best_pair"], 0.80)
        self.assertIsNotNone(rep["escalated"])
        self.assertEqual(rep["escalated"]["unit"], "aggregate_metrics")
        self.assertEqual(verdict(rep, 0.55, 0.80), "duplicate")

    def test_structure_gate_blocks_uncorroborated_duplicate(self):
        rep = {"best_pair": 0.9, "corpus_redundancy": 0.5, "best_structure": 0.2}
        self.assertEqual(verdict(rep, 0.55, 0.80), "overlap")
        rep["best_structure"] = 0.9
        self.assertEqual(verdict(rep, 0.55, 0.80), "duplicate")
        rep["best_structure"] = None  # missing data is not disagreement
        self.assertEqual(verdict(rep, 0.55, 0.80), "duplicate")


if __name__ == "__main__":
    unittest.main()
