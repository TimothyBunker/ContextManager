---
description: Check whether the cm baseline is current and reconcile it
---

Run `cm status` in the repository root with the Bash tool.

- If it reports up to date: tell the user in one line.
- If it reports STALE: run `cm gate` to reconcile. If the gate passes, report
  what changed. If the gate BLOCKS, follow the redundancy-gate skill's
  resolution protocol (read the matched units, then reuse, extend, or
  `cm accept --reason`).
- If it reports no baseline: suggest `/cm:init`.
