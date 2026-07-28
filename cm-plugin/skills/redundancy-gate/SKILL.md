---
name: redundancy-gate
description: Resolve cm redundancy-gate blocks and manage codebase redundancy. Use when a write is blocked with "cm gate BLOCKED" or DUPLICATE findings, when deciding whether to reuse existing code versus accept intentional similarity, or when working with PROJECT.cm, unit fingerprints, ::algo skeletons, or the cm commands (gate, accept, audit, status, check).
---

# cm — resolving the redundancy gate

This repo is compiled by `cm` into `PROJECT.cm`: every file's functions with
signatures, docs, structural fingerprints, and `::algo` skeletons. After every
write, `cm gate` recompiles incrementally and scores your changed units against
the whole tree. A DUPLICATE verdict means the codebase very likely already
contains what you just wrote. The goal is token efficiency: never re-write,
re-review, or re-carry code the project already has.

## Reading a block

Each flagged unit shows:

- `-> 0.93 vs <unit>  <file>@<lines>` — **where** the existing code lives.
- `token-sim` / `algo-sim` — text similarity vs algorithm-shape similarity.
- `EXACT-STRUCTURAL-DUP` — same fingerprint: your unit is a rename of the match
  (fingerprints rename params/locals but preserve anchors — the calls,
  attributes, and operators the code uses).
- `anchors only here: X | only there: Y` — the exact operations that differ.
  This line is the heart of the decision: it is the discrete evidence of
  whether you wrote the same function or a meaningfully different one.
- `overlap a.py:5-43 ~ b.py:33-71` — the aligned line ranges.

## Resolution protocol (in order)

1. **Read the matched unit at the cited file@lines. Never skip this.**
2. Decide, based on what you read:
   - **Same behavior needed** → delete your new unit and import/call the
     existing one. This is the expected common case.
   - **Existing unit almost fits** → extend or generalize the *existing* unit
     in place (add a parameter, widen a type) so one copy serves both callers.
   - **The anchor diff is the point** (e.g. `>=` vs `<=`, `.amount` vs
     `.value` — a deliberate mirror or interface variant) → accept it with a
     recorded reason:  `cm accept <fp> --reason "mirror of X for lower bound"`.
     The fp appears in the block output next to each finding.
3. **Never dodge the gate**: renaming identifiers, reshuffling lines, or
   deleting the original to silence the block does not work (fingerprints
   survive renames; the gate re-scores every write) and defeats the purpose.
4. The baseline stays frozen while blocked. It advances automatically the next
   time the gate passes — no manual rebuild needed.

## Pre-write denials

If your Write/Edit was DENIED by the precheck, the file was NOT modified —
there is nothing to clean up or revert. Do not retry the identical content.
Read the cited unit, then either rewrite your change to call/extend it, or —
if the similarity is intentional — `cm accept <fp> --reason "..."` and retry
the write, which will then pass.

## Before writing a new helper

`PROJECT.cm` at the repo root is grep-friendly. Before writing a utility,
search it for the anchors you are about to use (`::sig`, `::doc`, and `::algo`
lines carry names, docs, and called functions). Thirty seconds of grep is
cheaper than a gate round-trip.

## Commands

```
cm status                 is the baseline current; what changed
cm gate [path]            recompile + score changed units (the hook runs this)
cm accept <fp> --reason   record a reviewed-and-intentional similarity
cm audit --limit 10       whole-tree redundancy audit, ranked by wasted bits
cm check <file>           score one file against the tree
cm build --full           force a full recompile of PROJECT.cm
```
