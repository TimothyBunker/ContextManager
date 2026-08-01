# cm — the context manager compiler

`cm` compiles a codebase into a single readable, structured artifact — **`PROJECT.cm`** —
and polices the codebase with one objective: **accomplish the task with the least
amount of redundant code.**

The goal is **token efficiency**. The compiler recompiles incrementally on
every change, so the global picture is never stale — and the write gate tells
an agent, at write time and with evidence, *"this resembles code the project
already has; it lives here"*, holding the write until the resemblance is
reviewed. It is deliberately not (yet) a retrieval system, and it does not
manage what an agent keeps in context — normal context behavior is untouched.

Zero dependencies. Python 3.10+. `pip install -e .` for the `cm` command, or run
`python -m cm` straight from the repo.

## The framework

Four pieces. The protocol is the product — the similarity machinery behind it
is deliberately simple and swappable.

1. **Index** — the repo compiled into PROJECT.cm: every file, every unit
   (function/method/class) with its span, doc line, structural fingerprint,
   and discrete feature keys. Incremental: unchanged files restore from the
   `.cm/` cache; only edits are re-analyzed.
2. **Tripwire** — new/changed units are screened against the index by exact,
   deterministic rules (below). The tripwire never judges; it flags.
3. **Review** — a flagged write is held (pre-disk when possible), the holds
   persist to `.cm/holds.json`, and `cm review` lists them with evidence and
   the exact resolution commands. The *agent* reads both units and decides:
   reuse, extend, or intentionally different. Semantic judgment is done by
   the thing that is good at it.
4. **Ledger** — the decision is recorded pair-scoped
   (`cm accept <fp> --match <fp> --reason "..."`): it covers that pair only,
   the same unit resembling something new is still held, and editing either
   unit changes its fingerprint and re-opens the question. `cm ledger` lists
   every decision with its reason.

## Commands

```
cm init [path] [--hooks]     install into a repo: baseline + agent protocol (+ write-gate hooks)
cm gate [path] [--hook]      recompile incrementally, screen changed units, hold for review
cm review [--root .]         list pending holds with evidence and resolutions
cm accept <fp> --match <fp>  record a pair-scoped review decision in the ledger
cm ledger [--root .]         list recorded decisions and their reasons
cm build [path] [--full]     compile the tree -> PROJECT.cm (incremental by default)
cm status [path]             is the baseline current? what changed since it?
cm check <files> --root .    screen specific files against the tree
cm audit [path]              pairwise resemblance audit of the whole tree
cm detectors [--root .]      list or toggle tripwire detectors for a repo
cm drift <manifest>          [experimental] context-vs-PROJECT.cm divergence
```

`--json` on check/audit/drift emits full reports for machine consumers.

## How the tripwire decides

Design law: **deterministic discrete features — grep beats semantic search.**
No embeddings, no compression models, no similarity score to tune. Each unit
carries a feature set: the identifiers it reaches outside itself for (calls,
attributes, globals) and the literal constants it computes with. Bound names
(params, locals, its own name) are excluded — renaming them is free, so they
carry no identity. Module-level constants count for the units that reference
them, because hoisting a literal into a named constant is a demonstrated
disguise. Every feature is a string you can grep for in PROJECT.cm.

The tripwire is a **detector registry** ([cm/detectors/](cm/detectors/)):
each detector is a self-contained plugin that notices one kind of
resemblance and emits Evidence; a unit is flagged when any enabled detector
fires. Three ship by default:

| detector | fires on | defeats |
|---|---|---|
| `fingerprint` | identical hash of the normalized body | pure renames |
| `tokens` | 3+ shared **distinctive** tokens (or 2+ covering most of both feature sets) | restructuring: loops→recursion, dispatch tables, helper splits |
| `lines` | 10+ matching normalized lines with one unit | copied cores buried in padding |

"Distinctive" is decided by the corpus, not assumed: a token counts only if
few units use it (document frequency), and language-universal vocabulary
(builtins, stdlib names, `"utf-8"`) never counts regardless — you would not
grep for `open` to find a specific function.

Repos choose their detectors (`cm detectors --disable lines` writes
`.cm/config.json`), and writing a new one is a single class implementing
`prepare/affinity/examine` — see the contract in
[cm/detectors/base.py](cm/detectors/base.py). Future evidence providers
(behavioral probes, dataflow shapes, sketch vectors) are drop-ins here, not
core changes.

Every flag prints its evidence — the shared tokens and matching line spans —
so the review starts with the proof in hand:

```
[ REVIEW] gitignore_to_regex(pattern)  globutil.py@6-61  #28c0bfa6
    resembles _glob_to_regex  cm/ignore.py@33-71
       - 5 shared distinctive tokens
       shared tokens: '(?:.*/)?', '[^/]*', '**/', 'startswith', 'escape'
       lines globutil.py:14-24 ~ cm/ignore.py:39-49  (11 lines)
```

This is red-teamed, both directions: nine behavior-preserving disguises of one
function (four authored by a blind agent told to evade detection, verified
equivalent over 222,632 differential cases) are caught 9/9, and four
genuinely-novel controls — including a same-genre string scanner and code
sharing common idioms — pass 4/4. The corpus lives in
[tests/test_adversarial.py](tests/test_adversarial.py) and asserts both sides,
because a tripwire that flags everything is as useless as one that flags
nothing.

## The agentic loop

With the plugin (or `cm init --hooks`), every agent write passes through cm:

1. **Before it lands (PreToolUse).** The proposed content — `Write.content`,
   or the file with `Edit` strings applied — is screened *in memory*. A
   resemblance holds the write with the file untouched: nothing to revert,
   no tokens spent on code that gets rolled back.
2. **After it lands (PostToolUse).** `cm gate` reconciles: incremental
   recompile, screen anything the precheck could not model, and commit the
   new baseline — PROJECT.cm is always current. Unresolved flags freeze the
   baseline until the review is done (reuse/extend, or `cm accept`).

Moved code and renamed locals keep their fingerprints and are not re-flagged;
comment-only edits pass straight through. A warm gate on this repo runs in
well under a second.

## Claude Code plugin

[`cm-plugin/`](cm-plugin/) packages the loop, and this repo doubles as a local
plugin marketplace:

- **hooks** — PreToolUse holds resembling writes before they reach disk;
  PostToolUse reconciles after clean writes land. Both run a fail-open shim
  around `cm hook`: only repos that opted in via `cm init` are gated, and any
  infrastructure failure means the write proceeds.
- **skill** — `redundancy-gate` teaches the agent the review protocol: read
  the cited unit, then reuse > extend > accept-with-reason; renaming past the
  tripwire does not work.
- **commands** — `/cm:init`, `/cm:status`, `/cm:audit`.

Install (needs `cm` on PATH — `pip install -e .` or pipx):

```
/plugin marketplace add TimothyBunker/ContextManager
/plugin install cm@cm-marketplace
```

Then in any repo you want managed: `cm init .` — plain, without `--hooks`;
the plugin already provides the hooks.

## The .cm format

Line-oriented, human- and LLM-readable, grep-friendly. `::` lines are
directives; file contents are embedded verbatim under a declared line count.
`.cmignore` (gitignore syntax) controls scope; `*.cm` and `.cm/` are always
excluded.

```
::cm 0.3
::project ContextManager
::stats files=34 units=182 functions=159 raw_bytes=141421

::file cm/ignore.py
::lang python
::sha 4b0c1de9a2f1
::lines 122
::doc Gitignore-style rules for .cmignore.
::imports dataclasses, pathlib, re
::unit function _glob_to_regex @33-71 #8b3bbd1f
::sig _glob_to_regex(pat: str) -> str
::doc Translate a gitignore glob into a regex over posix relpaths.
::keys ["(?:.*/)?", "**/", "[^/]*", "escape", "re", "startswith", ...]
::content 122
...122 lines verbatim...
::endfile
```

The fingerprint `#fp` hashes the unit's normalized body (comments stripped,
literals collapsed, bound names alpha-renamed, outside names kept), so a pure
rename keeps its fingerprint. The `::keys` line is the unit's discrete feature
set — the thing to grep before writing a new helper.

## Known limits

- The tripwire is recall-oriented: expect some review flags on genuinely
  similar-but-different code. That is by design — review is cheap, carrying
  duplicates is not — and every decision lands in the ledger exactly once.
- A rewrite that shares *no* distinctive tokens and *no* line structure with
  its source can slip through. Settling that tail requires running the code
  (`cm probe`, roadmap).
- Unit extraction is AST-exact for Python, regex-based for JS/TS (top-level
  functions, arrows, classes), file-level for other code languages.

## Roadmap

- `cm probe` — the behavior layer: run flagged pairs on shared inputs, report
  agreement or a divergence witness.
- `cm annotate` — LLM-generated `::doc` lines for undocumented units.
- Candidate pruning for very large corpora so gate latency stays flat.
- Retrieval (`cm get`/`cm find`, MCP slices) — deferred by design; the
  current product is the gate, not context serving.
- Tree-sitter extractors for more languages.
