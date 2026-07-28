"""Adversarial corpus: disguised clones must be caught, novel code must not.

Every CLONE here is a behavior-preserving rewrite of the same source function
using a different evasion strategy (several were authored by an agent given
only the source and the instruction to evade detection, then verified
equivalent by differential testing). Every CONTROL is genuinely new code,
including deliberate traps: a same-genre character scanner, and code sharing
common literals/idioms with the corpus.

Both directions matter. A detector that catches everything is useless.
"""
import unittest

from cm.redundancy import score_targets, verdict
from tests.test_extract import make_record

SOURCE = '''import re


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
'''

# Corpus filler so rare-literal rarity is measured against something.
FILLER = '''import json


def load_settings(path, encoding="utf-8"):
    """Read a settings file and apply defaults."""
    with open(path, encoding=encoding) as handle:
        data = json.load(handle)
    data.setdefault("retries", 3)
    data.setdefault("timeout", 30)
    return data


def format_duration(seconds):
    """Human-readable duration."""
    if seconds < 60:
        return str(round(seconds, 1)) + "s"
    minutes, rest = divmod(int(seconds), 60)
    return str(minutes) + "m" + str(rest) + "s"
'''

CLONE_RENAME = '''import re


def wildcard_pattern_compile(spec: str) -> str:
    """Turn a shell wildcard spec into a regex fragment."""
    pos, total, parts = 0, len(spec), []
    while pos < total:
        symbol = spec[pos]
        if symbol == "*":
            if spec.startswith("**/", pos):
                parts.append("(?:.*/)?")
                pos += 3
            elif spec.startswith("**", pos):
                parts.append(".*")
                pos += 2
            else:
                parts.append("[^/]*")
                pos += 1
        elif symbol == "?":
            parts.append("[^/]")
            pos += 1
        elif symbol == "[":
            scan = pos + 1
            if scan < total and spec[scan] in "!^":
                scan += 1
            if scan < total and spec[scan] == "]":
                scan += 1
            while scan < total and spec[scan] != "]":
                scan += 1
            if scan >= total:
                parts.append(re.escape(symbol))
                pos += 1
            else:
                group = spec[pos + 1:scan]
                if group.startswith("!"):
                    group = "^" + group[1:]
                parts.append("[" + group + "]")
                pos = scan + 1
        else:
            parts.append(re.escape(symbol))
            pos += 1
    return "".join(parts)
'''

CLONE_IDIOM_SWAP = '''import re


def translate_glob(mask: str) -> str:
    """Regex fragment for a shell-style mask."""
    idx = 0
    chunks = []
    while idx < len(mask):
        token = mask[idx]
        if token == "*":
            if mask[idx:idx + 3] == "**/":
                chunks += ["(?:.*/)?"]
                idx += 3
            elif mask[idx:idx + 2] == "**":
                chunks += [".*"]
                idx += 2
            else:
                chunks += ["[^/]*"]
                idx += 1
        elif token == "?":
            chunks += ["[^/]"]
            idx += 1
        elif token == "[":
            stop = idx + 1
            if stop < len(mask) and mask[stop] in "!^":
                stop += 1
            if stop < len(mask) and mask[stop] == "]":
                stop += 1
            while stop < len(mask) and mask[stop] != "]":
                stop += 1
            if stop >= len(mask):
                chunks += [re.escape(token)]
                idx += 1
            else:
                inner = mask[idx + 1:stop]
                if inner[:1] == "!":
                    inner = "^" + inner[1:]
                chunks += ["[" + inner + "]"]
                idx = stop + 1
        else:
            chunks += [re.escape(token)]
            idx += 1
    return "".join(chunks)
'''

CLONE_RECURSIVE = '''import re


def rx_of(glob: str) -> str:
    """Regex fragment for a glob, consuming the head recursively."""
    if not glob:
        return ""
    if glob.startswith("**/"):
        return "(?:.*/)?" + rx_of(glob[3:])
    if glob.startswith("**"):
        return ".*" + rx_of(glob[2:])
    head = glob[0]
    if head == "*":
        return "[^/]*" + rx_of(glob[1:])
    if head == "?":
        return "[^/]" + rx_of(glob[1:])
    if head == "[":
        j = 1
        if j < len(glob) and glob[j] in "!^":
            j += 1
        if j < len(glob) and glob[j] == "]":
            j += 1
        while j < len(glob) and glob[j] != "]":
            j += 1
        if j >= len(glob):
            return re.escape(head) + rx_of(glob[1:])
        body = glob[1:j]
        if body.startswith("!"):
            body = "^" + body[1:]
        return "[" + body + "]" + rx_of(glob[j + 1:])
    return re.escape(head) + rx_of(glob[1:])
'''

# Literals hoisted to module constants so the function body holds only names.
CLONE_HOISTED_CONSTS = '''import re

_ANY_SEGMENT = ".*"
_CROSS_DIR = "(?:.*/)?"
_ONE_NAME = "[^/]*"
_ONE_CHAR = "[^/]"


def compile_mask(source: str) -> str:
    """Table-free scanner emitting named fragments."""
    cursor, width, stack = 0, len(source), []
    while cursor < width:
        current = source[cursor]
        if current == "*":
            if source.find("**/", cursor, cursor + 3) == cursor:
                stack.append(_CROSS_DIR)
                cursor += 3
            elif source.find("**", cursor, cursor + 2) == cursor:
                stack.append(_ANY_SEGMENT)
                cursor += 2
            else:
                stack.append(_ONE_NAME)
                cursor += 1
        elif current == "?":
            stack.append(_ONE_CHAR)
            cursor += 1
        elif current == "[":
            scan = cursor + 1
            if scan < width and source[scan] in "!^":
                scan += 1
            if scan < width and source[scan] == "]":
                scan += 1
            terminator = source.find("]", scan)
            if terminator == -1:
                stack.append(re.escape(current))
                cursor += 1
            else:
                payload = source[cursor + 1:terminator]
                if payload.startswith("!"):
                    payload = "^" + payload[1:]
                stack.append("[" + payload + "]")
                cursor = terminator + 1
        else:
            stack.append(re.escape(current))
            cursor += 1
    return "".join(stack)
'''

CLONES = {
    "rename": CLONE_RENAME,
    "idiom_swap": CLONE_IDIOM_SWAP,
    "recursive": CLONE_RECURSIVE,
    "hoisted_consts": CLONE_HOISTED_CONSTS,
}

CONTROL_RANGE_HEADER = '''def parse_range_header(value, total_size):
    """Parse an RFC 7233 Range header into concrete byte spans."""
    if not value.startswith("bytes="):
        return None
    spans = []
    for piece in value[6:].split(","):
        piece = piece.strip()
        if not piece:
            continue
        start_txt, _, end_txt = piece.partition("-")
        if not start_txt:
            length = int(end_txt)
            if length <= 0:
                continue
            spans.append((max(0, total_size - length), total_size - 1))
        else:
            start = int(start_txt)
            end = int(end_txt) if end_txt else total_size - 1
            if start > end or start >= total_size:
                continue
            spans.append((start, min(end, total_size - 1)))
    return spans or None
'''

# Same genre as the source (index loop scanning a string) but a different
# function: the false-positive trap.
CONTROL_SEMVER = '''def parse_semver_range(spec):
    """Parse a semver range spec like '>=1.2.3 <2.0.0 || ~3.1' into clauses."""
    clauses = []
    for alternative in spec.split("||"):
        terms = []
        cursor = 0
        text = alternative.strip()
        while cursor < len(text):
            char = text[cursor]
            if char in " \\t":
                cursor += 1
                continue
            operator = ""
            while cursor < len(text) and text[cursor] in "<>=~^":
                operator += text[cursor]
                cursor += 1
            start = cursor
            while cursor < len(text) and text[cursor] not in " \\t":
                cursor += 1
            version = text[start:cursor]
            if version:
                terms.append({"op": operator or "==", "version": version})
        if terms:
            clauses.append(terms)
    return clauses
'''

# Shares common literals/idioms with the corpus ("utf-8", json, defaults).
CONTROL_SHARED_IDIOM = '''import json


def export_manifest(records, destination, encoding="utf-8"):
    """Write a JSON manifest of records to destination."""
    payload = {"version": 1, "count": len(records), "items": []}
    for record in records:
        payload["items"].append({
            "name": record.name,
            "size": record.size,
            "tags": sorted(record.tags),
        })
    with open(destination, "w", encoding=encoding) as handle:
        handle.write(json.dumps(payload, indent=2))
    return payload["count"]
'''

CONTROLS = {
    "range_header": CONTROL_RANGE_HEADER,
    "semver_scanner": CONTROL_SEMVER,
    "shared_idiom": CONTROL_SHARED_IDIOM,
}


def units_of(path, src, scoreable_only=True):
    rec = make_record(path, "python", src)
    return [u for u in rec.units if u.scoreable or not scoreable_only]


def corpus():
    return units_of("ignore.py", SOURCE) + units_of("util.py", FILLER)


def worst_verdict(src, name):
    """Strongest verdict across the units of a candidate file."""
    reports = score_targets(units_of(name, src), corpus())
    order = {"novel": 0, "overlap": 1, "duplicate": 2}
    best, rep = "novel", None
    for r in reports:
        v = verdict(r, 0.55, 0.80)
        if order[v] > order[best]:
            best, rep = v, r
    return best, rep


class TestDisguisedClonesCaught(unittest.TestCase):
    def test_every_disguise_is_flagged_duplicate(self):
        for name, src in CLONES.items():
            with self.subTest(disguise=name):
                v, rep = worst_verdict(src, f"clone_{name}.py")
                self.assertEqual(v, "duplicate", f"{name} evaded detection")
                self.assertEqual(rep["escalated"]["unit"], "_glob_to_regex")

    def test_hoisted_constants_still_attribute_literals(self):
        # constants moved to module scope must still count as the unit's
        v, rep = worst_verdict(CLONE_HOISTED_CONSTS, "clone_hoisted.py")
        self.assertEqual(v, "duplicate")
        self.assertGreaterEqual(rep["escalated"]["shared_rare_literals"], 3)

    def test_restructured_clone_found_outside_top_score_candidates(self):
        # the recursive rewrite scores poorly on compression; it must still be
        # examined via the evidence channels
        _, rep = worst_verdict(CLONE_RECURSIVE, "clone_rec.py")
        self.assertLess(rep["best_pair"], 0.80)
        self.assertIsNotNone(rep["escalated"])


class TestNovelCodeAllowed(unittest.TestCase):
    def test_controls_are_not_flagged_duplicate(self):
        for name, src in CONTROLS.items():
            with self.subTest(control=name):
                v, _ = worst_verdict(src, f"ctrl_{name}.py")
                self.assertNotEqual(v, "duplicate", f"false positive on {name}")

    def test_same_genre_scanner_stays_novel(self):
        # a different string scanner is the sharpest false-positive risk
        v, _ = worst_verdict(CONTROL_SEMVER, "ctrl_semver.py")
        self.assertEqual(v, "novel")

    def test_common_literals_do_not_trigger_escalation(self):
        _, rep = worst_verdict(CONTROL_SHARED_IDIOM, "ctrl_idiom.py")
        self.assertIsNone(rep["escalated"] if rep else None)


if __name__ == "__main__":
    unittest.main()
