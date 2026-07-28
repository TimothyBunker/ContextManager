<!-- cm:protocol:begin -->
## cm — redundancy gate

This repo is compiled into PROJECT.cm (every file's functions, fingerprints,
and algorithm skeletons). The goal is token efficiency: never rewrite what
the codebase already contains.

- Writes are checked BEFORE they land: the precheck hook denies duplicate
  code with the file untouched. After clean writes land, `cm gate`
  reconciles the baseline incrementally.
- If a write is DENIED or the gate reports DUPLICATE, stop: read the cited
  unit (file@lines; the anchor diff explains any difference) and reuse or
  extend it instead.
- If the similarity is intentional, run `cm accept <fp>` and continue.
- PROJECT.cm and the baseline update automatically when the gate passes.
<!-- cm:protocol:end -->
