import unittest

from cm.model import FileRecord
from cm.extract import extract_units
from cm.normalize import norm_source

PY_SRC = '''"""Module doc first line."""
import os
from pathlib import Path


def process(items, limit=10):
    """Filter items below the limit."""
    kept = []
    for item in items:
        if item.weight < limit and item.active:
            kept.append(item.name.strip().lower())
    return sorted(set(kept))


class Foo:
    """A class."""

    @property
    def bar(self):
        """Bar doc."""
        total = 0
        for chunk in self.chunks:
            total += len(chunk) * self.scale
        return total


async def fetch_all(urls):
    results = {}
    for url in urls:
        results[url] = await grab(url)
    return results
'''

JS_SRC = '''import { thing } from "./thing";

// Adds up weighted values from records.
export function sumWeighted(records, scale) {
  let total = 0;
  for (const rec of records) {
    total += rec.value * scale; // note: "{" inside a string next
    if (rec.tag === "}{weird") total -= 1;
  }
  return total;
}

const shortHelper = (x) => x + 1;

export const groupByKey = (rows, key) => {
  const out = {};
  for (const row of rows) {
    (out[row[key]] ||= []).push(row);
  }
  return out;
};

class Widget {
  constructor(name) { this.name = name; }
}
'''


def make_record(path, lang, text):
    rec = FileRecord(path=path, abspath=path, lang=lang, text=text,
                     sha="x" * 12, size=len(text), lines=len(text.split("\n")))
    extract_units(rec)
    return rec


class TestPythonExtract(unittest.TestCase):
    def setUp(self):
        self.rec = make_record("mod.py", "python", PY_SRC)

    def test_units_found(self):
        quals = {u.qualname for u in self.rec.units}
        self.assertEqual(quals, {"process", "Foo", "Foo.bar", "fetch_all"})

    def test_docs_and_module_doc(self):
        self.assertEqual(self.rec.doc, "Module doc first line.")
        by_name = {u.qualname: u for u in self.rec.units}
        self.assertEqual(by_name["process"].doc, "Filter items below the limit.")
        self.assertEqual(by_name["Foo.bar"].kind, "method")

    def test_decorated_span_starts_at_decorator(self):
        bar = next(u for u in self.rec.units if u.qualname == "Foo.bar")
        self.assertTrue(self.rec.text.split("\n")[bar.start - 1].strip().startswith("@"))

    def test_imports(self):
        self.assertIn("os", self.rec.imports)
        self.assertIn("pathlib", self.rec.imports)

    def test_class_not_scoreable(self):
        foo = next(u for u in self.rec.units if u.qualname == "Foo")
        self.assertFalse(foo.scoreable)


class TestJsExtract(unittest.TestCase):
    def setUp(self):
        self.rec = make_record("mod.js", "javascript", JS_SRC)

    def test_units_found(self):
        names = {u.qualname for u in self.rec.units}
        self.assertEqual(names, {"sumWeighted", "shortHelper", "groupByKey", "Widget"})

    def test_brace_in_string_does_not_break_span(self):
        sw = next(u for u in self.rec.units if u.qualname == "sumWeighted")
        self.assertTrue(sw.body.rstrip().endswith("}"))
        self.assertIn("return total;", sw.body)

    def test_doc_comment_above(self):
        sw = next(u for u in self.rec.units if u.qualname == "sumWeighted")
        self.assertIn("weighted values", sw.doc)

    def test_trivial_arrow_marked(self):
        sh = next(u for u in self.rec.units if u.qualname == "shortHelper")
        self.assertTrue(sh.trivial)


class TestNormalize(unittest.TestCase):
    def test_alpha_rename_makes_renamed_clones_identical(self):
        a = "def f(alpha, beta):\n    # comment\n    return alpha * beta + 'hello'\n"
        b = "def g(gamma, delta):\n    # other words entirely\n    return gamma * delta + 'world'\n"
        self.assertEqual(norm_source(a, "python"), norm_source(b, "python"))

    def test_keywords_survive(self):
        n = norm_source("for x in y: return x", "python")
        for kw in ("for", "in", "return"):
            self.assertIn(kw, n.split())


if __name__ == "__main__":
    unittest.main()
