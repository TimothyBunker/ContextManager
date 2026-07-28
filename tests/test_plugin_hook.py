import contextlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_incremental import run_cli
from tests.test_redundancy import ORIGINAL, PURE_RENAME

HOOK = Path(__file__).resolve().parent.parent / "cm-plugin" / "scripts" / "gate_hook.py"


def run_hook(payload, cwd=None) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=stdin,
                          capture_output=True, text=True, timeout=120,
                          encoding="utf-8", errors="replace", cwd=cwd)


@contextlib.contextmanager
def repo(baseline: bool):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.py").write_text(ORIGINAL, encoding="utf-8")
        if baseline:
            run_cli("gate", tmp)
        yield root


class TestGateHook(unittest.TestCase):
    def test_uninitialized_repo_is_a_silent_noop(self):
        with repo(baseline=False) as root:
            proc = run_hook({"cwd": str(root)})
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    def test_initialized_repo_blocks_duplicate_via_file_path(self):
        with repo(baseline=True) as root:
            (root / "sub").mkdir()
            (root / "sub" / "b.py").write_text(PURE_RENAME, encoding="utf-8")
            # root discovery must walk up from the edited file's directory
            proc = run_hook({"tool_input": {"file_path": str(root / "sub" / "b.py")}})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("BLOCKED", proc.stderr)
        self.assertIn("aggregate_metrics", proc.stderr)

    def test_clean_write_passes(self):
        with repo(baseline=True) as root:
            (root / "notes.md").write_text("# notes\n", encoding="utf-8")
            proc = run_hook({"tool_input": {"file_path": str(root / "notes.md")}})
        self.assertEqual(proc.returncode, 0)

    def test_malformed_stdin_never_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_hook("not json", cwd=tmp)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
