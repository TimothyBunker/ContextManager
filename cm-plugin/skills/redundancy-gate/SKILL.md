---
name: redundancy-gate
description: Resolve cm review holds and manage codebase redundancy. Use when a write is held with "REVIEW REQUIRED", when deciding whether to reuse existing code versus record an intentional similarity, or when working with PROJECT.cm, unit fingerprints, ::keys features, or the cm commands (gate, accept, audit, status, check).
---

# cm — the review protocol

This repo is compiled by `cm` into `PROJECT.cm`: every function with its
signature, doc, structural fingerprint, and `::keys` — the discrete tokens it
computes with. Writes are screened by a tripwire of exact rules; anything
resembling existing code is HELD FOR REVIEW. The tripwire never judges — *you*
are the reviewer. The goal is token efficiency: never re-write, re-review, or
re-carry code the project already has.

## Reading a hold

Each flagged unit lists its evidence:

- `resembles <unit>  <file>@<lines>` — **where** the existing code lives.
- `shared tokens: '(?:.*/)?', startswith, ...` — the distinctive names and
  literal constants both units use. These are behavior-bound: a rewrite can
  rename everything and restructure freely, but not change the values it
  computes with.
- `IDENTICAL-STRUCTURE` — same fingerprint: the new unit is a rename of the
  existing one.
- `lines a.py:5-43 ~ b.py:33-71 (N lines)` — matching normalized line spans.

## The review (in order)

1. **Read the cited unit at file@lines. Never skip this.**
2. Decide, based on what you read:
   - **Same behavior needed** → delete/withhold your new unit; import or call
     the existing one. This is the expected common case.
   - **Existing unit almost fits** → extend or generalize the *existing* unit
     in place so one copy serves both callers.
   - **Genuinely different despite the resemblance** (a mirror case, a
     variant over a different interface) → record the decision with the exact
     command the hold prints:
     `cm accept <fp> --match <fp> --reason "why this is intentionally separate"`.
     Decisions are pair-scoped: they cover this-unit-vs-that-unit only, so
     the same unit resembling something *new* is still held. Editing either
     unit changes its fingerprint and re-opens the question — by design.
3. **Do not dodge the tripwire**: renaming identifiers or reshuffling lines
   does not work (fingerprints survive renames; shared tokens survive
   restructuring), and it defeats the purpose.
4. The baseline stays frozen while a hold is unresolved; it advances
   automatically the next time the gate passes.

## Pre-write holds

If a Write/Edit was held by the precheck, the file was NOT modified — there
is nothing to clean up. Do not retry identical content. Review as above, then
either write the reuse version, or accept and retry.

## Before writing a new helper

`PROJECT.cm` is grep-friendly by design. Search the `::keys`, `::sig`, and
`::doc` lines for the tokens you are about to use — thirty seconds of grep is
cheaper than a review round-trip.

## Commands

```
cm review                 list pending holds with evidence and resolutions
cm status                 is the baseline current; what changed
cm gate [path]            recompile + screen changed units (the hook runs this)
cm accept <fp> --match <fp> --reason   record a pair-scoped review decision
cm ledger                 list recorded decisions and their reasons
cm audit --limit 10       whole-tree resemblance audit
cm check <file>           screen one file against the tree
cm detectors              list or toggle tripwire detectors
cm build --full           force a full recompile of PROJECT.cm
```
