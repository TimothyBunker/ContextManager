import unittest

from tests.test_extract import make_record
from cm.redundancy import requires_review, score_targets

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


def best_match(rep):
    return next((m for m in rep["matches"] if m["reasons"]), None)


class TestFingerprints(unittest.TestCase):
    def test_pure_rename_same_fingerprint(self):
        [orig] = units_of("a.py", ORIGINAL)
        [clone] = units_of("b.py", PURE_RENAME)
        self.assertEqual(orig.fp, clone.fp)

    def test_interface_variant_different_fingerprint(self):
        # features (.value/.weight vs .amount/.scale) survive normalization
        [orig] = units_of("a.py", ORIGINAL)
        [variant] = units_of("b.py", INTERFACE_VARIANT)
        self.assertNotEqual(orig.fp, variant.fp)

    def test_fickle_pair_distinguished(self):
        # assertGreaterEqual vs assertLessEqual must not collide
        [ge] = units_of("g.py", FICKLE_GE)
        [le] = units_of("l.py", FICKLE_LE)
        self.assertNotEqual(ge.fp, le.fp)


class TestTripwire(unittest.TestCase):
    def corpus(self):
        return units_of("a.py", ORIGINAL) + units_of("c.py", UNRELATED)

    def test_pure_rename_flagged_by_fingerprint(self):
        [rep] = score_targets(units_of("new.py", PURE_RENAME), self.corpus())
        self.assertTrue(requires_review(rep))
        m = best_match(rep)
        self.assertEqual((m["file"], m["unit"]), ("a.py", "aggregate_metrics"))
        self.assertTrue(m["exact_structural_dup"])

    def test_interface_variant_flagged(self):
        [rep] = score_targets(units_of("new.py", INTERFACE_VARIANT), self.corpus())
        self.assertTrue(requires_review(rep))
        m = best_match(rep)
        self.assertEqual(m["unit"], "aggregate_metrics")
        self.assertFalse(m["exact_structural_dup"])

    def test_padded_clone_flagged(self):
        # padding cannot shrink the line overlap with the copied core
        [rep] = score_targets(units_of("new.py", PADDED_CLONE), self.corpus())
        self.assertTrue(requires_review(rep))
        self.assertEqual(best_match(rep)["unit"], "aggregate_metrics")

    def test_fickle_pair_goes_to_review_not_verdict(self):
        # same scaffold, one flipped assertion: the tripwire flags it and the
        # agent decides — the framework never claims they are the same function
        [rep] = score_targets(units_of("g.py", FICKLE_GE),
                              units_of("l.py", FICKLE_LE) + self.corpus())
        self.assertTrue(requires_review(rep))
        self.assertEqual(best_match(rep)["unit"], "check_ratio_lower")

    def test_novel_function_passes(self):
        [rep] = score_targets(units_of("new.py", UNRELATED),
                              units_of("a.py", ORIGINAL))
        self.assertEqual(rep["action"], "pass")

    def test_every_flag_carries_greppable_evidence(self):
        [rep] = score_targets(units_of("new.py", INTERFACE_VARIANT), self.corpus())
        m = best_match(rep)
        self.assertTrue(m["reasons"])  # a stated rule, never a bare score
        self.assertTrue(m["shared"] or m["overlap_lines"])


if __name__ == "__main__":
    unittest.main()
