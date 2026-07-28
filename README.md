# cm — the context manager compiler

`cm` compiles a codebase into a single readable, structured artifact — **`PROJECT.cm`** —
and polices the codebase with one objective: **accomplish the task with the least
amount of redundant code.**

The goal is **token efficiency**. The compiler recompiles incrementally on
every change, so the global picture is never stale — and the write gate tells
an agent, at write time and with evidence, *"you already wrote this; it lives
here"*, holding the write from standing until the match is investigated. It is
deliberately not (yet) a retrieval system, and it does not manage what an
agent keeps in context — normal context behavior is untouched.

Zero dependencies. Python 3.10+. `pip install -e .` for the `cm` command, or run
`python -m cm` straight from the repo.

## Commands

```
cm init [path] [--hooks]     install into a repo: baseline + agent protocol (+ write-gate hook)
cm gate [path] [--hook]      recompile incrementally, score changed units, block on duplicates
cm accept <fp...>            mark a flagged fingerprint as reviewed-and-intentional
cm build [path] [--full]     compile the tree -> PROJECT.cm (incremental by default)
cm status [path]             is the baseline current? what changed since it?
cm check <files> --root .    score specific files for redundancy vs the tree
cm audit [path]              pairwise redundancy self-audit of the whole tree
cm drift <manifest>          [experimental] context-vs-PROJECT.cm divergence
```

`--json` on check/audit/drift emits full reports for machine consumers.

## The agentic loop

With the plugin (or `cm init --hooks`), every agent write passes through cm
twice:

1. **Before it lands (PreToolUse).** The proposed content — `Write.content`,
   or the file with `Edit` strings applied — is scored *in memory*. Units
   that duplicate existing code are **DENIED with the file untouched**: the
   agent sees the *where* (file@lines), the *why* (overlap + anchor diff),
   and revises instead of writing. Nothing to clean up, no tokens spent on
   code that gets reverted.
2. **After it lands (PostToolUse).** `cm gate` recompiles incrementally
   (unchanged files restore from the cache by size+mtime, content hash as
   fallback), scores anything the precheck could not model, and commits the
   new baseline — so PROJECT.cm is always current. Unresolved duplicates
   freeze the baseline until reused or accepted (`cm accept <fp> --reason`).

Moved code and renamed locals keep their fingerprints and are not re-flagged;
comment-only edits pass straight through. Overlap-level findings are reported
but never block. A warm gate on this repo runs in ~300 ms.

## Claude Code plugin

[`cm-plugin/`](cm-plugin/) packages the loop for distribution, and this repo
doubles as a local plugin marketplace:

- **hooks** — PreToolUse denies duplicate writes before they reach disk;
  PostToolUse reconciles and commits after clean writes land. Both run
  [gate_hook.py](cm-plugin/scripts/gate_hook.py), a fail-open shim around
  `cm hook`: it gates only repos that opted in via `cm init` (a `.cm/` or
  PROJECT.cm exists up the tree) and exits silently on any infrastructure
  failure — a broken gate must never block unrelated work.
- **skill** — `redundancy-gate` teaches the agent the judgment half: how to
  read a block (anchor diffs, algo-sim, overlaps), the resolution protocol
  (reuse > extend > `cm accept --reason`), and why dodging the gate by
  renaming doesn't work.
- **commands** — `/cm:init`, `/cm:status`, `/cm:audit`.

Install (needs `cm` on PATH — `pip install -e .` or pipx — the hook falls
back to `python -m cm`):

```
/plugin marketplace add D:\ContextManager
/plugin install cm@cm-marketplace
```

Then in any repo you want managed: `cm init .` — plain, without `--hooks`;
the plugin already provides the hook, and a second one would gate every
write twice.

## The .cm format

One file, line-oriented, human- and LLM-readable. `::` lines are directives; file
contents are embedded verbatim under a declared line count (so content can safely
contain `::`). `.cmignore` (gitignore syntax) controls scope; `*.cm` is always
excluded so the compiler never ingests its own output.

```
::cm 0.2
::project ContextManager
::stats files=20 units=120 functions=102 raw_bytes=86977 info_bits=103104 structural_redundancy=0.27

::file cm/ignore.py
::lang python
::sha 4b0c1de9a2f1
::lines 122
::doc Gitignore-style rules for .cmignore.
::imports dataclasses, pathlib, re
::unit function _glob_to_regex @33-71 #8b3bbd1f
::sig _glob_to_regex(pat: str) -> str
::doc Translate a gitignore glob into a regex over posix relpaths.
::algo cfg==,while{=,if{...}},ret an=+:6,<:4,==:4,append:7,escape:2,len:1,startswith:3
::content 122
...122 lines verbatim...
::endfile
```

Each unit (function / method / class / file) carries a span, its doc line, a
**structural fingerprint** `#fp`, and an **algorithm skeleton** `::algo`. The
fingerprint hashes the unit's *normalized* body under two-tier renaming:
comments stripped, strings/numbers collapsed, and **bound** names — params,
locals, the unit's own name — alpha-renamed to `V0, V1, ...`, while
**anchors** — builtins, imports, attributes, called functions, operators: the
names code reaches *outside itself* for — are kept verbatim. A pure-rename
clone therefore has the *same fingerprint* as its original, but
`assertGreaterEqual` vs `assertLessEqual` no longer collide. The skeleton
records what survives rewriting entirely: nested control-flow shape (`cfg=`),
the anchor multiset (`an=`), recursion/generator flags (`fl=`).

Extractors: Python (ast, exact), JavaScript/TypeScript (masked regex +
brace-matching), other code languages as file-level units, prose/config indexed
without units.

## The information model

Your cross-entropy intuition, made computable. A compressor stands in for the
probability model: the cost of encoding `x` is `C(x) = len(compress(x)) * 8`
bits, an upper bound on cross-entropy under the compressor's model. Everything
is normalized first, so naming and comments don't hide structure.

**Redundancy of a new unit `u`** against corpus `K`:

```
R(u) = 1 - C(u | K) / C(u)        C(u|K) measured by conditional compression
```

`R ~ 0`: the corpus can't predict `u` — genuinely novel information.
`R ~ 1`: the corpus already predicts `u` almost entirely — you paid tokens for
information the project already had. The scenario this is built for: an agent
holding context slice `S` writes `u`; `C(u | S)` is large (its context didn't
predict it) but `C(u | K)` is near zero (the full project does). That gap *is*
the "cross entropy went up" alarm — and it is localizable:

- **where**: the corpus unit maximizing conditional compression savings
  (pairwise zlib with the candidate's normalized body as preset dictionary;
  corpus-level lzma over the best candidates for the overall verdict)
- **why**: aligned matching line blocks between the two units, reported in
  original line numbers (alignment runs on per-line normalized text, so renamed
  clones still align)

### The literal channel

A behavior-preserving rewrite may rename every identifier, restructure the
control flow, split the work across helpers, or hoist values into module
constants — but it **cannot change the constants the code emits or compares
against**. So literals are extracted per unit (docstrings excluded, module
constants attributed to the units referencing them) and weighted by corpus
rarity: `"(?:.*/)?"` appearing in ≤3 units is a near-unique signature, while
`"utf-8"` or `100` is filtered out as noise. Sharing 3+ rare literals with
one unit escalates to duplicate regardless of every other score.

This channel is what catches the hard cases. Nine behavior-preserving
disguises of one function — including four written by an agent told to evade
detection, verified equivalent over 222,632 differential test cases — are
caught 9/9, while four genuinely-novel controls (including a same-genre
string scanner and a file sharing common idioms) pass 4/4. That corpus lives
in [tests/test_adversarial.py](tests/test_adversarial.py); both directions
are asserted, because a detector that flags everything is useless.

### Composing the verdict

Verdicts compose the channels. The specific best-pair (info) score drives them
(`--warn` 0.55 / `--fail` 0.80 by default), but a "duplicate" claim must be
*corroborated by structure*: an info-hot match whose algorithm skeleton
disagrees (similarity < 0.5) is shared idiom, not duplication, and is
downgraded to "overlap". Independently of the aggregate score, **partial-clone
escalation** fires on evidence that padding and restructuring cannot dilute:
10+ overlapping normalized lines with one candidate, 3+ shared rare literals,
or algorithm-shape similarity ≥ 0.70. Candidates are selected by that evidence
too, not only by compression rank — a heavily restructured clone often scores
poorly on compression and would otherwise never be examined. When a near-clone
is flagged, the report prints the
**anchor diff** — `anchors only here: assertGreaterEqual | only there:
assertLessEqual` — the discrete evidence of what actually distinguishes the
pair. The corpus-conditional score is held to a higher bar because normalized
same-language code is always somewhat cross-predictable — that's a language
prior, not duplication; it exists to catch units stitched together from
several sources. Ranking uses **wasted bits** (`C(u) * R`), not the ratio:
one half-duplicated 40-line function outranks two structurally identical
3-line helpers.

**Drift** of a context slice `S` against the project: every section of
PROJECT.cm has a fingerprint; a manifest of the fingerprints currently
in-context yields `C(P | S)` — the bits of project information the context is
missing or holding stale, i.e. how much syncing costs:

```
cm drift context_manifest.txt      # fp8 | path | path#qualname@fp8, one per line
DRIFT vs PROJECT.cm: 31/90 units in context, 0 stale, 59 missing
  bits to sync: 58,920 (~7.2 KiB of novel information)
```

## Known limits (v0)

- Structure and information are still not *behavior*: a corroborated duplicate
  whose anchor diff is one flipped comparator may be a deliberate mirror.
  Only the behavioral layer (`cm probe`, roadmap) can certify equivalence;
  until then the anchor diff makes such pairs one-glance reviewable.
- JS/TS bound-name detection is heuristic (declarations, params, catch), and
  JS skeletons are flat keyword-at-depth sequences rather than exact trees.
  Misclassified names fail toward anchors — i.e. toward fewer false positives.
- JS/TS extraction is regex-based: top-level functions, arrows, classes; class
  methods are not yet individual units. Tree-sitter is the upgrade path.
- Compression is an upper bound on cross-entropy; scores are comparative, not
  absolute probabilities.

## Roadmap

- `cm probe` — the behavior channel: run flagged pairs on shared inputs
  (reusing the original's tests where they exist), report an agreement rate
  or a divergence witness — the input where the pair differs.
- `cm annotate` — LLM-generated `::doc` lines for undocumented units.
- Candidate pruning for very large corpora (fingerprint buckets, anchor
  prefilters before compression) so gate latency stays flat as repos grow.
- Retrieval (`cm get`/`cm find`, MCP slices) — deferred by design for now;
  the current product is the gate, not context serving.
- Tree-sitter extractors; per-language keyword tables beyond py/js/ts.
