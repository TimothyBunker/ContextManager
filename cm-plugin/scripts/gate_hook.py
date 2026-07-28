#!/usr/bin/env python3
"""cm hook shim for Claude Code: pipes the hook JSON to `cm hook`.

All real logic lives in the cm package (root discovery, pre-write denial,
post-write gate). This shim guarantees fail-open behavior only: if cm is not
installed, times out, or crashes, the write proceeds — a broken gate must
never block unrelated work.

Exit codes: 0 = allow/no-op, 2 = deny or block (cm's findings are relayed on
stderr, which Claude Code feeds back to the model).
"""
import os
import shutil
import subprocess
import sys


def main() -> int:
    stdin = sys.stdin.read()
    cm = shutil.which("cm")
    cmd = [cm, "hook"] if cm else [sys.executable, "-m", "cm", "hook"]
    try:
        # pin both sides to UTF-8: Windows pipes otherwise default to the ANSI
        # codepage and non-ASCII in findings would crash the decode
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                              timeout=120, encoding="utf-8", errors="replace",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if proc.returncode == 2:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.write(proc.stderr or proc.stdout)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
