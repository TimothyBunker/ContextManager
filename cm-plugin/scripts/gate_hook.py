#!/usr/bin/env python3
"""cm write-gate hook for Claude Code (PostToolUse on Write/Edit).

Safe to install globally: it gates only repos that opted in (a `.cm/` baseline
or PROJECT.cm exists somewhere above the edited file), and it exits silently
when cm isn't installed or anything unexpected happens — a broken gate must
never block unrelated work.

Exit codes: 0 = pass/no-op, 2 = blocked (cm's findings are relayed on stderr,
which Claude Code feeds back to the model).
"""
import json
import os
import shutil
import subprocess
import sys


def find_cm_root(start: str) -> str | None:
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, ".cm")) or os.path.isfile(os.path.join(d, "PROJECT.cm")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    start = os.path.dirname(target) if target else (payload.get("cwd") or os.getcwd())
    root = find_cm_root(start)
    if root is None:
        return 0  # repo has not opted in to cm

    cm = shutil.which("cm")
    cmd = [cm, "gate", root, "--hook"] if cm else [sys.executable, "-m", "cm", "gate", root, "--hook"]
    try:
        # pin both sides to UTF-8: Windows pipes otherwise default to the ANSI
        # codepage and non-ASCII in findings would crash the decode
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace",
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if proc.returncode == 2:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.write(proc.stderr or proc.stdout)
        return 2
    return 0  # pass, or cm missing/crashed — never block on infrastructure failure


if __name__ == "__main__":
    raise SystemExit(main())
