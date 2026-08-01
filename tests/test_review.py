"""Review + Ledger modules: pair-scoped accepts, holds lifecycle, cm review."""
import unittest
from pathlib import Path

from cm.cache import accepted_covers, load_accepted
from cm.review import load_holds
from cm.scan import load_file
from tests.test_incremental import run_cli
from tests.test_redundancy import INTERFACE_VARIANT, ORIGINAL, UNRELATED

import tempfile


def unit_fp(path: Path, rel: str) -> str:
    return load_file(path, rel).units[0].fp


class TestPairScopedLedger(unittest.TestCase):
    def test_parse_and_covers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".cm").mkdir()
            (root / ".cm" / "accepted").write_text(
                "aaaaaaaa  # old-format wildcard\n"
                "bbbbbbbb cccccccc  # pair-scoped\n", encoding="utf-8")
            acc = load_accepted(root)
            self.assertTrue(accepted_covers(acc, "aaaaaaaa", "anything"))
            self.assertTrue(accepted_covers(acc, "bbbbbbbb", "cccccccc"))
            self.assertFalse(accepted_covers(acc, "bbbbbbbb", "dddddddd"))
            self.assertFalse(accepted_covers(acc, "cccccccc", "bbbbbbbb"))


class TestReviewFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.py").write_text(ORIGINAL, encoding="utf-8")
        (self.root / "c.py").write_text(UNRELATED, encoding="utf-8")
        run_cli("gate", str(self.root))  # baseline

    def tearDown(self):
        self.tmp.cleanup()

    def hold_pair(self):
        (self.root / "b.py").write_text(INTERFACE_VARIANT, encoding="utf-8")
        run_cli("gate", str(self.root))
        return (unit_fp(self.root / "b.py", "b.py"),
                unit_fp(self.root / "a.py", "a.py"))

    def test_block_persists_holds_and_review_reports_them(self):
        tfp, mfp = self.hold_pair()
        data = load_holds(self.root)
        self.assertEqual(data["source"], "gate")
        self.assertEqual(data["holds"][0]["fp"], tfp)
        self.assertEqual(data["holds"][0]["matches"][0]["fp"], mfp)
        self.assertEqual(run_cli("review", "--root", str(self.root)), 1)

    def test_wrong_pair_does_not_unblock(self):
        tfp, _ = self.hold_pair()
        run_cli("accept", tfp, "--match", "deadbeef", "--root", str(self.root))
        self.assertEqual(run_cli("gate", str(self.root)), 1)

    def test_right_pair_unblocks_and_clears_holds(self):
        tfp, mfp = self.hold_pair()
        run_cli("accept", tfp, "--match", mfp, "--root", str(self.root),
                "--reason", "interface variant is intentional")
        self.assertEqual(run_cli("gate", str(self.root)), 0)
        self.assertEqual(load_holds(self.root), {})
        self.assertEqual(run_cli("review", "--root", str(self.root)), 0)

    def test_accepted_pair_still_blocks_new_resemblance(self):
        # accept b-vs-a, then make b ALSO a copy of c: must hold again
        tfp, mfp = self.hold_pair()
        run_cli("accept", tfp, "--match", mfp, "--root", str(self.root))
        run_cli("gate", str(self.root))
        (self.root / "b.py").write_text(
            INTERFACE_VARIANT + "\n\n" + UNRELATED.replace(
                "parse_header_line", "parse_footer_line"), encoding="utf-8")
        self.assertEqual(run_cli("gate", str(self.root)), 1)

    def test_wildcard_accept_still_works(self):
        tfp, _ = self.hold_pair()
        run_cli("accept", tfp, "--root", str(self.root))
        self.assertEqual(run_cli("gate", str(self.root)), 0)

    def test_ledger_lists_decisions(self):
        tfp, mfp = self.hold_pair()
        run_cli("accept", tfp, "--match", mfp, "--root", str(self.root),
                "--reason", "documented variant")
        self.assertEqual(run_cli("ledger", "--root", str(self.root)), 0)


class TestAdapterRegistry(unittest.TestCase):
    def test_builtin_adapters_registered(self):
        from cm.extract import ADAPTERS
        self.assertLessEqual({"python", "javascript", "typescript", "markdown"},
                             set(ADAPTERS))


if __name__ == "__main__":
    unittest.main()
