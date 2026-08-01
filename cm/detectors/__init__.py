"""The detector registry: the tripwire's plugin surface.

Every detector registers itself here; repos choose which ones run via
`.cm/config.json` (`{"detectors": {"name": false}}` disables one — absent
means enabled). `cm detectors` lists and toggles them. See base.py for the
contract and how to write a new detector.
"""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY: dict[str, type] = {}


def register(cls):
    REGISTRY[cls.name] = cls
    return cls


from .base import Detector, Evidence  # noqa: E402  (registry must exist first)
from . import fingerprint, lines, tokens  # noqa: E402,F401  (self-registering)


def enabled_names(root: Path | None = None) -> dict[str, bool]:
    enabled = {name: True for name in REGISTRY}
    if root is not None:
        p = Path(root) / ".cm" / "config.json"
        if p.is_file():
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                for name, on in (cfg.get("detectors") or {}).items():
                    if name in enabled:
                        enabled[name] = bool(on)
            except (ValueError, OSError):
                pass
    return enabled


def load_enabled(root: Path | None = None) -> list[Detector]:
    enabled = enabled_names(root)
    return [REGISTRY[name]() for name in REGISTRY if enabled[name]]


def set_enabled(root: Path, changes: dict[str, bool]) -> None:
    p = Path(root) / ".cm" / "config.json"
    cfg = {}
    if p.is_file():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cfg = {}
    detectors = cfg.setdefault("detectors", {})
    detectors.update(changes)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8", newline="\n")
