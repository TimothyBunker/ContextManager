import tempfile
import unittest
from pathlib import Path

from cm.cmfile import emit, parse
from cm.ignore import IgnoreRules
from cm.scan import scan_tree

PY = '''"""Fixture module."""


def alpha(x):
    """Double it."""
    return x * 2


def beta(items):
    """Contains ::content sentinel text to stress the parser."""
    out = []
    for it in items:
        out.append(str(it) + "::content 999")
    return out
'''


class TestCmFileRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "pkg").mkdir()
        (root / "pkg" / "mod.py").write_text(PY, encoding="utf-8")
        (root / "notes.md").write_text("# Title\nBody\n", encoding="utf-8")
        (root / "ignored.log").write_text("x", encoding="utf-8")
        (root / ".cmignore").write_text("*.log\n", encoding="utf-8")
        self.result = scan_tree(root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_respects_cmignore(self):
        paths = [r.path for r in self.result.records]
        self.assertIn("pkg/mod.py", paths)
        self.assertIn("notes.md", paths)
        self.assertIn(".cmignore", paths)
        self.assertNotIn("ignored.log", paths)

    def test_round_trip(self):
        meta = {"project": "fixture", "root": ".",
                "stats": {"files": len(self.result.records)}}
        text = emit(meta, self.result.records)
        meta2, files = parse(text)
        self.assertEqual(meta2["project"], "fixture")
        self.assertEqual(meta2["stats"]["files"], str(len(self.result.records)))
        by_path = {f.path: f for f in files}
        self.assertEqual(set(by_path), {r.path for r in self.result.records})
        for rec in self.result.records:
            f = by_path[rec.path]
            self.assertEqual(f.sha, rec.sha)
            self.assertEqual(f.content, rec.text, f"content mismatch for {rec.path}")
            self.assertEqual([u.fp for u in f.units], [u.fp for u in rec.units])
            self.assertEqual([u.algo for u in f.units], [u.algo for u in rec.units])
            self.assertEqual([(u.start, u.end) for u in f.units],
                             [(u.start, u.end) for u in rec.units])

    def test_content_with_directive_lookalike_survives(self):
        text = emit({"project": "x"}, self.result.records)
        _, files = parse(text)
        mod = next(f for f in files if f.path == "pkg/mod.py")
        self.assertIn('"::content 999"', mod.content)

    def test_no_content_mode(self):
        text = emit({"project": "x"}, self.result.records, include_content=False)
        _, files = parse(text)
        self.assertTrue(all(f.content is None for f in files))


if __name__ == "__main__":
    unittest.main()
