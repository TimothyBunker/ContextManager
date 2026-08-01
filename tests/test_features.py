import ast
import unittest

from cm.features import py_features, py_module_consts
from cm.normalize import bound_from_ast

SRC = '''LIMIT_PATTERN = "(?:.*/)?"

def convert(items, scale):
    """Docstring words must not become features."""
    results = []
    for item in items:
        adjusted = item.value * scale
        results.append(helpers.clamp(adjusted, LIMIT_PATTERN, "fallback-mode"))
    return results
'''


class TestPyFeatures(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(SRC)
        self.consts = py_module_consts(tree)
        self.node = tree.body[1]
        self.feats = py_features(self.node, bound_from_ast(self.node), self.consts)

    def test_bound_names_excluded(self):
        for name in ("items", "scale", "results", "item", "adjusted", "convert"):
            self.assertNotIn(name, self.feats)

    def test_outside_names_and_attrs_included(self):
        for feat in ("helpers", "clamp", "value", "append"):
            self.assertIn(feat, self.feats)

    def test_literals_included_docstring_excluded(self):
        self.assertIn("fallback-mode", self.feats)
        self.assertNotIn("Docstring words must not become features.", self.feats)

    def test_module_const_value_attributed(self):
        # referencing LIMIT_PATTERN pulls in its literal value
        self.assertIn("(?:.*/)?", self.feats)

    def test_deterministic(self):
        again = py_features(self.node, bound_from_ast(self.node), self.consts)
        self.assertEqual(self.feats, again)


if __name__ == "__main__":
    unittest.main()
