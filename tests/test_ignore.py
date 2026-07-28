import unittest

from cm.ignore import IgnoreRules


class TestIgnoreRules(unittest.TestCase):
    def setUp(self):
        self.rules = IgnoreRules([
            "*.log",
            "build/",
            "!keep.log",
            "/root_only.txt",
            "docs/**/*.tmp",
        ], use_defaults=False)

    def test_basename_glob_any_depth(self):
        self.assertTrue(self.rules.ignored("a.log", False))
        self.assertTrue(self.rules.ignored("sub/dir/b.log", False))

    def test_negation_last_match_wins(self):
        self.assertFalse(self.rules.ignored("keep.log", False))
        self.assertFalse(self.rules.ignored("sub/keep.log", False))

    def test_dir_only_and_containment(self):
        self.assertTrue(self.rules.ignored("build", True))
        self.assertTrue(self.rules.ignored("build/x/y.js", False))
        self.assertFalse(self.rules.ignored("build", False))  # a *file* named build

    def test_anchored(self):
        self.assertTrue(self.rules.ignored("root_only.txt", False))
        self.assertFalse(self.rules.ignored("sub/root_only.txt", False))

    def test_double_star(self):
        self.assertTrue(self.rules.ignored("docs/a/b/c.tmp", False))
        self.assertTrue(self.rules.ignored("docs/direct.tmp", False))
        self.assertFalse(self.rules.ignored("other/a.tmp", False))

    def test_defaults(self):
        d = IgnoreRules([])
        self.assertTrue(d.ignored(".git", True))
        self.assertTrue(d.ignored("PROJECT.cm", False))
        self.assertTrue(d.ignored("sub/OTHER.cm", False))
        self.assertTrue(d.ignored("node_modules/pkg/index.js", False))
        self.assertTrue(d.ignored("cm_compiler.egg-info/PKG-INFO", False))
        self.assertTrue(d.ignored(".cm/cache.json.gz", False))
        self.assertFalse(d.ignored("cm/cli.py", False))


if __name__ == "__main__":
    unittest.main()
