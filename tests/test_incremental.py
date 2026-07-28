import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from cm import cli
from cm.cache import load_cache, save_cache
from cm.scan import load_file, scan_tree
from tests.test_redundancy import ORIGINAL, PURE_RENAME, UNRELATED


def run_cli(*argv) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return cli.main(list(argv))


class TestIncrementalScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_text(ORIGINAL, encoding="utf-8")
        (self.root / "c.py").write_text(UNRELATED, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_second_scan_is_all_cache_hits(self):
        first = scan_tree(self.root)
        self.assertEqual(len(first.changed), 2)
        save_cache(self.root, first)
        second = scan_tree(self.root, cache=load_cache(self.root))
        self.assertEqual(second.cache_hits, 2)
        self.assertEqual(second.changed, [])

    def test_only_modified_file_reanalyzed(self):
        first = scan_tree(self.root)
        save_cache(self.root, first)
        (self.root / "c.py").write_text(UNRELATED + "\nEXTRA = 1\n", encoding="utf-8")
        second = scan_tree(self.root, cache=load_cache(self.root))
        self.assertEqual(second.changed, ["c.py"])
        self.assertEqual(second.cache_hits, 1)

    def test_bom_file_still_parses(self):
        # Windows editors often write a UTF-8 BOM; it must not break extraction
        (self.root / "bom.py").write_bytes(b"\xef\xbb\xbf" + ORIGINAL.encode("utf-8"))
        rec = load_file(self.root / "bom.py", "bom.py")
        self.assertEqual([u.qualname for u in rec.units], ["aggregate_metrics"])
        self.assertFalse(rec.text.startswith("﻿"))

    def test_cached_records_match_fresh_analysis(self):
        first = scan_tree(self.root)
        save_cache(self.root, first)
        second = scan_tree(self.root, cache=load_cache(self.root))
        for fresh, cached in zip(first.records, second.records):
            self.assertEqual(fresh.sha, cached.sha)
            self.assertEqual([u.fp for u in fresh.units], [u.fp for u in cached.units])
            self.assertEqual([u.norm for u in fresh.units], [u.norm for u in cached.units])
            self.assertEqual([u.body for u in fresh.units], [u.body for u in cached.units])
            self.assertEqual([u.algo for u in fresh.units], [u.algo for u in cached.units])


class TestGateFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_text(ORIGINAL, encoding="utf-8")
        (self.root / "c.py").write_text(UNRELATED, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_gate_creates_baseline(self):
        self.assertEqual(run_cli("gate", str(self.root)), 0)
        self.assertTrue((self.root / "PROJECT.cm").is_file())
        self.assertTrue(load_cache(self.root))

    def test_duplicate_write_blocks_and_baseline_frozen(self):
        run_cli("gate", str(self.root))
        (self.root / "b.py").write_text(PURE_RENAME, encoding="utf-8")
        self.assertEqual(run_cli("gate", str(self.root)), 1)
        self.assertEqual(run_cli("gate", str(self.root), "--hook"), 2)
        self.assertNotIn("b.py", load_cache(self.root))  # baseline not advanced

    def test_accept_unblocks_and_commits(self):
        run_cli("gate", str(self.root))
        (self.root / "b.py").write_text(PURE_RENAME, encoding="utf-8")
        run_cli("gate", str(self.root))
        clone = load_file(self.root / "b.py", "b.py").units[0]
        self.assertEqual(run_cli("accept", clone.fp, "--root", str(self.root)), 0)
        self.assertEqual(run_cli("gate", str(self.root)), 0)
        self.assertIn("b.py", load_cache(self.root))

    def test_novel_write_passes_and_commits(self):
        run_cli("gate", str(self.root))
        (self.root / "d.py").write_text(
            UNRELATED.replace("parse_header_line", "parse_footer_block")
                     .replace("header", "footer"), encoding="utf-8")
        # near-identical to c.py -> should block, proving corpus includes tree state
        self.assertEqual(run_cli("gate", str(self.root)), 1)
        (self.root / "d.py").write_text(
            "def totally_new(matrix):\n"
            "    best_row = None\n"
            "    best_score = float('-inf')\n"
            "    for row in matrix:\n"
            "        score = min(row) * 3 - max(row)\n"
            "        if score > best_score:\n"
            "            best_score = score\n"
            "            best_row = row\n"
            "    return best_row\n", encoding="utf-8")
        self.assertEqual(run_cli("gate", str(self.root)), 0)
        self.assertIn("d.py", load_cache(self.root))

    def test_comment_only_edit_passes_quickly(self):
        run_cli("gate", str(self.root))
        (self.root / "a.py").write_text("# leading comment\n" + ORIGINAL, encoding="utf-8")
        self.assertEqual(run_cli("gate", str(self.root)), 0)

    def test_status_reports_staleness(self):
        run_cli("gate", str(self.root))
        self.assertEqual(run_cli("status", str(self.root)), 0)
        (self.root / "c.py").write_text(UNRELATED + "\nX = 2\n", encoding="utf-8")
        self.assertEqual(run_cli("status", str(self.root)), 1)


class TestInit(unittest.TestCase):
    def test_init_installs_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text(ORIGINAL, encoding="utf-8")
            self.assertEqual(run_cli("init", str(root), "--hooks"), 0)
            self.assertTrue((root / ".cmignore").is_file())
            self.assertTrue((root / "PROJECT.cm").is_file())
            self.assertTrue((root / "CLAUDE.md").is_file())
            self.assertIn("cm gate", (root / "CLAUDE.md").read_text(encoding="utf-8"))
            settings = (root / ".claude" / "settings.json").read_text(encoding="utf-8")
            self.assertIn("cm hook", settings)
            self.assertIn("PreToolUse", settings)
            self.assertIn("PostToolUse", settings)
            # idempotent
            self.assertEqual(run_cli("init", str(root), "--hooks"), 0)
            claude_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertEqual(claude_md.count("cm:protocol:begin"), 1)


if __name__ == "__main__":
    unittest.main()
