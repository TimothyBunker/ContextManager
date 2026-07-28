import ast
import unittest

from cm.skeleton import algo_similarity, anchor_diff, js_algo, py_algo
from cm.normalize import mask_code

AGG = '''def aggregate_metrics(samples, window):
    """doc"""
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

FACTORIAL = '''def fact(n):
    if n <= 1:
        return 1
    return n * fact(n - 1)
'''

GEN = '''def pairs(items):
    for a in items:
        for b in items:
            yield a, b
'''


def algo_of(src):
    node = ast.parse(src).body[0]
    return py_algo(node, node.name)


class TestPyAlgo(unittest.TestCase):
    def test_cfg_shape(self):
        algo = algo_of(AGG)
        self.assertIn("cfg==,=,for{call,if{=,call,=}},if{=,call},ret", algo)

    def test_anchors_counted(self):
        algo = algo_of(AGG)
        self.assertIn("append:3", algo)
        self.assertIn("len:5", algo)
        self.assertIn(">=:1", algo)

    def test_recursion_flag(self):
        self.assertIn("fl=rec", algo_of(FACTORIAL))
        self.assertNotIn("fl=", algo_of(AGG))

    def test_generator_flag(self):
        self.assertIn("gen", algo_of(GEN))

    def test_docstring_not_a_statement(self):
        self.assertNotIn("expr", algo_of(AGG).split(" an=")[0])


class TestJsAlgo(unittest.TestCase):
    def test_flat_cfg_and_anchors(self):
        src = '''function sumAll(rows) {
  let total = 0;
  for (const row of rows) {
    if (row.ok) total += row.value;
  }
  return total;
}'''
        algo = js_algo(mask_code(src, "javascript"), "sumAll")
        cfg = algo.split(" an=")[0]
        self.assertIn("for1", cfg)
        self.assertIn("ret1", cfg)
        self.assertIn("value:1", algo)


class TestSimilarity(unittest.TestCase):
    def test_same_algorithm_high(self):
        a = algo_of(AGG)
        b = algo_of(AGG.replace("aggregate_metrics", "other_name"))
        self.assertEqual(algo_similarity(a, b), 1.0)

    def test_different_algorithm_low(self):
        sim = algo_similarity(algo_of(AGG), algo_of(FACTORIAL))
        self.assertLess(sim, 0.5)

    def test_missing_side_is_none(self):
        self.assertIsNone(algo_similarity(algo_of(AGG), ""))

    def test_anchor_diff(self):
        a = algo_of(AGG)
        b = algo_of(AGG.replace(".weight", ".scale"))
        only_a, only_b = anchor_diff(a, b)
        self.assertEqual(only_a, ["weight"])
        self.assertEqual(only_b, ["scale"])


if __name__ == "__main__":
    unittest.main()
