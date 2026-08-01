---
description: Work through pending cm review holds
---

Run `cm review` in the repository root with the Bash tool.

- If it reports no pending holds: say so in one line.
- Otherwise, for each hold: READ the cited existing unit at its file@lines
  (never skip this), compare with the held unit, and decide:
  1. Same behavior needed → rewrite the change to reuse/import the existing
     unit instead.
  2. Existing unit almost fits → extend/generalize the existing unit so one
     copy serves both callers.
  3. Genuinely different despite the resemblance → record it with the exact
     command the hold prints: `cm accept <fp> --match <fp> --reason "..."`.
- After resolving all holds, run `cm gate` and confirm it reports clean.
- Summarize each decision and why in one line per hold.
