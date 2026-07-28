<!-- cm:protocol:begin -->
## cm — redundancy gate

This repo is compiled into PROJECT.cm (every file's functions, fingerprints,
and algorithm skeletons). The goal is token efficiency: never rewrite what
the codebase already contains.

- After creating or modifying code, `cm gate` runs (via hook, or run it
  yourself). It recompiles incrementally and scores only what changed.
- If it reports DUPLICATE, stop: read the cited unit (file@lines, anchor
  diff explains any difference) and reuse or extend it instead.
- If the similarity is intentional, run `cm accept <fp>` and continue.
- PROJECT.cm and the baseline update automatically when the gate passes.
<!-- cm:protocol:end -->
