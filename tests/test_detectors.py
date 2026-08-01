"""The tripwire's plugin surface: registry, config, and module isolation.

The isolation tests are the point: each disguise class must be caught by
exactly the detector built for it, and disabling that detector must let it
through. That proves the module boundary is real, not decorative.
"""
import tempfile
import unittest
from pathlib import Path

from cm.detectors import REGISTRY, enabled_names, load_enabled, set_enabled
from cm.detectors.base import Detector, Evidence
from cm.detectors.fingerprint import FingerprintDetector
from cm.detectors.lines import LineOverlapDetector
from cm.detectors.tokens import SharedTokensDetector
from cm.redundancy import requires_review, score_targets
from tests.test_adversarial import CLONE_RECURSIVE, corpus, units_of
from tests.test_redundancy import ORIGINAL, PADDED_CLONE, PURE_RENAME


class TestRegistryAndConfig(unittest.TestCase):
    def test_builtin_detectors_registered(self):
        self.assertLessEqual({"fingerprint", "tokens", "lines"}, set(REGISTRY))

    def test_config_toggles_per_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(enabled_names(root)["tokens"])
            set_enabled(root, {"tokens": False})
            self.assertFalse(enabled_names(root)["tokens"])
            self.assertTrue(enabled_names(root)["fingerprint"])
            self.assertNotIn("tokens", [d.name for d in load_enabled(root)])
            set_enabled(root, {"tokens": True})
            self.assertIn("tokens", [d.name for d in load_enabled(root)])

    def test_broken_config_means_all_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".cm").mkdir()
            (root / ".cm" / "config.json").write_text("not json", encoding="utf-8")
            self.assertTrue(all(enabled_names(root).values()))


class TestModuleIsolation(unittest.TestCase):
    def test_recursive_clone_is_the_tokens_detectors_catch(self):
        targets = units_of("clone.py", CLONE_RECURSIVE)
        [rep] = [r for r in score_targets(targets, corpus(),
                                          detectors=[SharedTokensDetector()])]
        self.assertTrue(requires_review(rep))
        # without tokens, the other detectors must NOT claim this catch
        [rep] = score_targets(targets, corpus(),
                              detectors=[FingerprintDetector(), LineOverlapDetector()])
        self.assertEqual(rep["action"], "pass")

    def test_pure_rename_is_the_fingerprint_detectors_catch(self):
        targets = units_of("clone.py", PURE_RENAME)
        cps = units_of("a.py", ORIGINAL)
        [rep] = score_targets(targets, cps, detectors=[FingerprintDetector()])
        self.assertTrue(requires_review(rep))
        self.assertIn("fingerprint", rep["matches"][0]["reasons"][0])

    def test_padded_clone_is_the_lines_detectors_catch(self):
        targets = units_of("clone.py", PADDED_CLONE)
        cps = units_of("a.py", ORIGINAL)
        [rep] = score_targets(targets, cps, detectors=[LineOverlapDetector()])
        self.assertTrue(requires_review(rep))
        self.assertIn("matching normalized lines", rep["matches"][0]["reasons"][0])

    def test_all_disabled_means_everything_passes(self):
        [rep] = score_targets(units_of("clone.py", PURE_RENAME),
                              units_of("a.py", ORIGINAL), detectors=[])
        self.assertEqual(rep["action"], "pass")


class _ToyDetector(Detector):
    """Fires on every same-kind pair — exists to prove third-party drop-in."""
    name = "toy"

    def affinity(self, target, cand):
        return 1 if cand.kind == target.kind else 0

    def examine(self, target, cand):
        if cand.kind == target.kind:
            return Evidence(self.name, "toy detector fired")
        return None


class TestCustomDetector(unittest.TestCase):
    def test_third_party_detector_composes(self):
        [rep] = score_targets(units_of("x.py", ORIGINAL),
                              units_of("c.py", CLONE_RECURSIVE),
                              detectors=[_ToyDetector()])
        self.assertTrue(requires_review(rep))
        ev = rep["matches"][0]["evidence"]
        self.assertEqual(ev[0]["detector"], "toy")
        self.assertEqual(ev[0]["reason"], "toy detector fired")


if __name__ == "__main__":
    unittest.main()
