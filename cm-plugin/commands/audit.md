---
description: Run a cm redundancy audit and propose consolidations
---

Run `cm audit --limit 10` in the repository root with the Bash tool.

Then, from the output:
1. Group findings that reference each other (A matches B and B matches A are
   one cluster, not two findings).
2. Rank clusters by total wasted bits.
3. For each of the top clusters, read the units involved and propose a
   concrete consolidation: which unit to keep, which callers to redirect, and
   whether the anchor diff indicates a deliberate variant that should instead
   be `cm accept`-ed with a reason.
4. Present the proposals to the user. Do NOT apply refactors without the
   user's confirmation.
