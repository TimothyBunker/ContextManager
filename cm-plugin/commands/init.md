---
description: Install cm into this repository (baseline + protocol)
---

Initialize the cm context manager in the current repository:

1. Verify the `cm` command exists (`cm --version`). If missing, tell the user
   to run `pip install -e <path-to-cm-repo>` (or pipx) first and stop.
2. Run `cm init .` — WITHOUT `--hooks`: this plugin already provides the
   write-gate hook, and installing a second hook would run the gate twice per
   write.
3. Report what was installed (baseline stats, protocol location) and mention
   that from now on every Write/Edit is gated automatically: duplicates will
   be blocked with evidence until resolved or accepted.
