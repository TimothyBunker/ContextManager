"""Gitignore-style rules for .cmignore (subset: globs, **, negation, dir-only, anchoring)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Always applied before user rules. `*.cm` is a hard rule: the compiler must
# never ingest its own output, or PROJECT.cm would grow without bound.
DEFAULT_RULES = [
    ".git/", ".hg/", ".svn/",
    "__pycache__/", "*.pyc", "*.pyo",
    ".venv/", "venv/", ".tox/", ".mypy_cache/", ".pytest_cache/", ".ruff_cache/",
    "node_modules/", "dist/", "build/", ".next/", "out/", "target/", "*.egg-info/",
    ".idea/", ".vscode/", ".claude/", ".DS_Store",
    "*.cm", ".cm/",
    "*.min.js", "*.map", "*.lock", "package-lock.json",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico", "*.pdf",
    "*.zip", "*.gz", "*.tar", "*.7z",
    "*.exe", "*.dll", "*.so", "*.dylib", "*.bin",
    "*.woff", "*.woff2", "*.ttf", "*.eot",
]


@dataclass
class Rule:
    pattern: str
    regex: re.Pattern
    negated: bool
    dir_only: bool


def _glob_to_regex(pat: str) -> str:
    """Translate a gitignore glob into a regex over posix relpaths."""
    i, n, out = 0, len(pat), []
    while i < n:
        c = pat[i]
        if c == "*":
            if pat.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
            elif pat.startswith("**", i):
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pat[j] in "!^":
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(c))
                i += 1
            else:
                cls = pat[i + 1:j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append("[" + cls + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def _ancestors(relpath: str) -> list[str]:
    parts = relpath.split("/")
    return ["/".join(parts[:k]) for k in range(1, len(parts))]


class IgnoreRules:
    """Ordered rule list; last matching rule wins (gitignore semantics)."""

    def __init__(self, lines: list[str] | None = None, use_defaults: bool = True):
        self.rules: list[Rule] = []
        for raw in (DEFAULT_RULES if use_defaults else []) + list(lines or []):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            line = line.strip()
            dir_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = line.startswith("/")
            line = line.lstrip("/")
            if not line:
                continue
            if "/" in line:
                anchored = True  # gitignore: a slash anywhere anchors to root
            prefix = "" if anchored else "(?:.*/)?"
            regex = re.compile("^" + prefix + _glob_to_regex(line) + "$")
            self.rules.append(Rule(raw, regex, negated, dir_only))

    def _matches(self, rule: Rule, relpath: str, is_dir: bool) -> bool:
        if rule.regex.match(relpath) and (is_dir or not rule.dir_only):
            return True
        # A rule matching a containing directory covers everything beneath it.
        return any(rule.regex.match(anc) for anc in _ancestors(relpath))

    def ignored(self, relpath: str, is_dir: bool) -> bool:
        verdict = False
        for rule in self.rules:
            if self._matches(rule, relpath, is_dir):
                verdict = not rule.negated
        return verdict

    @classmethod
    def load(cls, root: Path) -> "IgnoreRules":
        cmignore = root / ".cmignore"
        lines = []
        if cmignore.is_file():
            lines = cmignore.read_text(encoding="utf-8", errors="replace").splitlines()
        return cls(lines)
