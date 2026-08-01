"""The Review module: pending holds and their lifecycle.

When the gate or precheck holds a write, the holds are persisted to
`.cm/holds.json` in a machine-readable form, so a review can happen any time
after the hook fired — `cm review` lists what is outstanding, with the
evidence and the exact resolution commands. Holds clear themselves: a
successful gate commit means nothing is outstanding (rewritten units change
fingerprint; accepted pairs stop blocking), so the file always reflects the
latest screening, never a backlog of stale ones.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .cache import cm_dir

_HOLDS = "holds.json"


def _compact(rep: dict) -> dict:
    return {
        "unit": rep["unit"], "file": rep["file"], "span": rep["span"],
        "fp": rep["fp"], "signature": rep.get("signature", ""),
        "matches": [{
            "unit": m["unit"], "file": m["file"], "span": m["span"], "fp": m["fp"],
            "reasons": m["reasons"], "shared": m["shared"],
            "overlap_lines": m["overlap_lines"],
        } for m in rep["matches"] if m["reasons"]],
    }


def save_holds(root: Path, source: str, blocking: list[dict]) -> None:
    d = cm_dir(root)
    d.mkdir(exist_ok=True)
    payload = {
        "source": source,  # "gate" (write landed) or "precheck" (write withheld)
        "seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "holds": [_compact(rep) for rep in blocking],
    }
    (d / _HOLDS).write_text(json.dumps(payload, indent=2) + "\n",
                            encoding="utf-8", newline="\n")


def load_holds(root: Path) -> dict:
    p = cm_dir(root) / _HOLDS
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def clear_holds(root: Path) -> None:
    p = cm_dir(root) / _HOLDS
    if p.is_file():
        p.unlink()
