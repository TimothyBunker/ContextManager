"""cm command line: build / check / audit / drift."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .cache import (accepted_covers, add_accepted, find_root, ledger_lines,
                    load_accepted, load_cache, save_cache)
from .cmfile import emit, parse
from .detectors import REGISTRY, enabled_names, load_enabled, set_enabled
from .extract import extract_units
from .ignore import IgnoreRules
from .model import Unit
from .redundancy import requires_review, score_targets
from .review import clear_holds, load_holds, save_holds
from .scan import MAX_FILE_BYTES, load_file, record_from_bytes, scan_tree


def _corpus_stats(records) -> dict:
    units = [u for r in records for u in r.units]
    return {
        "files": len(records),
        "units": len(units),
        "functions": sum(1 for u in units if u.kind in ("function", "method")),
        "raw_bytes": sum(r.size for r in records),
    }


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _commit(root: Path, result, stats: dict, no_content: bool = False,
            output: str | None = None) -> Path:
    """Write PROJECT.cm and the cache: the new accepted baseline."""
    meta = {"project": root.name, "root": ".", "generated": _utc_now(), "stats": stats}
    out_path = Path(output) if output else root / "PROJECT.cm"
    text = emit(meta, result.records, include_content=not no_content)
    out_path.write_text(text, encoding="utf-8", newline="\n")
    save_cache(root, result)
    return out_path


def cmd_build(args) -> int:
    root = Path(args.path).resolve()
    cache = {} if args.full else load_cache(root)
    result = scan_tree(root, cache=cache)
    stats = _corpus_stats(result.records)
    out_path = _commit(root, result, stats, args.no_content, args.output)
    print(f"PROJECT.cm written -> {out_path}")
    print(f"  files {stats['files']}  units {stats['units']} "
          f"({stats['functions']} functions)  raw {stats['raw_bytes']:,} B")
    print(f"  incremental: {result.cache_hits} cached, {len(result.changed)} analyzed")
    for label, items in (("binary", result.skipped_binary), ("too large", result.skipped_large)):
        if items:
            print(f"  skipped ({label}): {', '.join(items[:8])}" + (" ..." if len(items) > 8 else ""))
    return 0


def cmd_status(args) -> int:
    root = Path(args.path).resolve()
    cache = load_cache(root)
    if not cache:
        print("no baseline (.cm/ cache missing or stale) — run 'cm build' or 'cm init'")
        return 1
    result = scan_tree(root, cache=cache)
    current = {r.path for r in result.records}
    added = [p for p in result.changed if p not in cache]
    modified = [p for p in result.changed if p in cache]
    removed = sorted(set(cache) - current)
    stale = bool(added or modified or removed) or not (root / "PROJECT.cm").is_file()
    print(f"baseline: {len(cache)} files | added {len(added)}  "
          f"modified {len(modified)}  removed {len(removed)}")
    for label, items in (("added", added), ("modified", modified), ("removed", removed)):
        for p in items[:10]:
            print(f"  {label:>8}  {p}")
    print("PROJECT.cm is " + ("STALE — run 'cm build' or 'cm gate'" if stale else "up to date"))
    return 1 if stale else 0


def _print_unit_report(rep: dict, out=None) -> None:
    out = out or sys.stdout
    print(f"[{rep['action'].upper():>7}] {rep['signature'] or rep['unit']}"
          f"  {rep['file']}@{rep['span'][0]}-{rep['span'][1]}  #{rep['fp']}", file=out)
    for m in rep.get("matches", []):
        tag = "  IDENTICAL-STRUCTURE" if m["exact_structural_dup"] else ""
        print(f"    resembles {m['unit']}  {m['file']}@{m['span'][0]}-{m['span'][1]}{tag}", file=out)
        for reason in m["reasons"]:
            print(f"       - {reason}", file=out)
        if m["shared"]:
            print("       shared tokens: " + ", ".join(repr(s) for s in m["shared"]), file=out)
        for o in m["overlap"][:2]:
            print(f"       lines {rep['file']}:{o['target_lines'][0]}-{o['target_lines'][1]}"
                  f" ~ {m['file']}:{o['match_lines'][0]}-{o['match_lines'][1]}"
                  f"  ({o['lines']} lines)  {o['snippet']}", file=out)


def _summarize(reports: list[dict]) -> dict:
    counts = {"review": 0, "pass": 0, "trivial": 0}
    for rep in reports:
        counts[rep["action"]] += 1
    return counts


def cmd_check(args) -> int:
    root = Path(args.root).resolve()
    targets_abs = {str(Path(t).resolve()) for t in args.targets}
    for t in args.targets:
        if not Path(t).is_file():
            print(f"error: target not found: {t}", file=sys.stderr)
            return 2
    corpus_scan = scan_tree(root, exclude_abs=targets_abs, cache=load_cache(root))
    corpus = corpus_scan.all_units(scoreable_only=True)

    target_units: list[Unit] = []
    for t in args.targets:
        p = Path(t).resolve()
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            rel = p.as_posix()
        rec = load_file(p, rel)
        if rec is None:
            print(f"error: target is binary: {t}", file=sys.stderr)
            return 2
        target_units.extend(rec.units)

    scoreables = [u for u in target_units if u.kind != "class"]
    reports = score_targets(scoreables, corpus, top=args.top,
                            detectors=load_enabled(root))
    counts = _summarize(reports)

    if args.json:
        print(json.dumps({
            "root": str(root), "targets": sorted(targets_abs),
            "corpus_units": len(corpus), "units": reports, "summary": counts,
        }, indent=2))
    else:
        print(f"CHECK {', '.join(args.targets)}  "
              f"(corpus: {len(corpus)} units / {len(corpus_scan.records)} files)\n")
        for rep in sorted(reports, key=lambda r: r["action"] != "review"):
            _print_unit_report(rep)
        print(f"\nSummary: {counts['review']} to review, {counts['pass']} pass, "
              f"{counts['trivial']} trivial")
    return 1 if counts["review"] else 0


def cmd_audit(args) -> int:
    root = Path(args.path).resolve()
    result = scan_tree(root, cache=load_cache(root))
    units = result.all_units(scoreable_only=True)
    reports = score_targets(units, units, top=args.top, detectors=load_enabled(root))
    flagged = [r for r in reports if requires_review(r)]
    flagged.sort(key=lambda r: -max(
        (m["shared_count"] + m["overlap_lines"] for m in r["matches"]), default=0))
    counts = _summarize(reports)

    if args.json:
        print(json.dumps({"root": str(root), "units": reports, "summary": counts}, indent=2))
        return 0
    print(f"AUDIT {root}  ({len(units)} scoreable units / {len(result.records)} files)\n")
    if not flagged:
        print("No unit resembles another closely enough to review.")
    for rep in flagged[:args.limit]:
        _print_unit_report(rep)
    print(f"\nSummary: {counts['review']} to review, {counts['pass']} pass, "
          f"{counts['trivial']} trivial")
    return 0


def _changed_units(result, cache: dict) -> list[Unit]:
    """Scoreable units that are new or modified relative to the cached baseline.

    A unit whose fingerprint already existed in its file's previous version is
    unchanged (moved code and renamed locals keep their fingerprint).
    """
    targets = []
    for rec in result.records:
        entry = cache.get(rec.path)
        if entry is not None and entry["sha"] == rec.sha:
            continue
        prev_fps = {u["fp"] for u in entry["units"]} if entry else set()
        targets.extend(u for u in rec.units if u.scoreable and u.fp not in prev_fps)
    return targets


def _screen(reports, accepted):
    """Units needing review, minus resemblances the ledger already covers.

    Pair-scoped: an accepted (target, match) pair stops blocking that pair
    only — the same unit resembling something new still gets held.
    """
    held = []
    for rep in reports:
        if not requires_review(rep):
            continue
        uncovered = [m for m in rep["matches"]
                     if m["reasons"] and not accepted_covers(accepted, rep["fp"], m["fp"])]
        if uncovered:
            held.append({**rep, "matches": uncovered})
    return held


def _print_block(headline, footer, blocking, out):
    print(headline, file=out)
    for rep in sorted(blocking, key=lambda r: -max(
            (m["shared_count"] + m["overlap_lines"] for m in r["matches"]), default=0)):
        _print_unit_report(rep, out=out)
    print("Read each cited unit, then decide: reuse it, extend it, or — if the "
          "resemblance is intentional — record the decision:", file=out)
    for rep in blocking:
        for m in rep["matches"][:2]:
            print(f'  cm accept {rep["fp"]} --match {m["fp"]} --reason "..."'
                  f'    # {rep["unit"]} vs {m["unit"]}', file=out)
    print(footer, file=out)


def _gate_run(root: Path, top: int, hook: bool) -> int:
    """The write interlock: recompile incrementally, screen what changed,
    hold (exit nonzero) anything needing review, else commit the new baseline."""
    out = sys.stderr if hook else sys.stdout
    cache = load_cache(root)
    result = scan_tree(root, cache=cache)

    if not cache:
        _commit(root, result, _corpus_stats(result.records))
        if not hook:
            print(f"cm gate: baseline created ({len(result.records)} files, "
                  f"{len(result.all_units())} units)")
        return 0

    current_paths = {r.path for r in result.records}
    added = [p for p in current_paths if p not in cache]
    removed = [p for p in cache if p not in current_paths]
    delta = f" (files +{len(added)}/-{len(removed)})" if added or removed else ""

    targets = _changed_units(result, cache)
    if not targets:
        _commit(root, result, _corpus_stats(result.records))
        if not hook:
            print(f"gate clean: no changed units{delta}; baseline refreshed")
        return 0

    reports = score_targets(targets, result.all_units(scoreable_only=True), top=top,
                            detectors=load_enabled(root))
    blocking = _screen(reports, load_accepted(root))

    if blocking:
        save_holds(root, "gate", blocking)
        _print_block(
            f"cm gate: REVIEW REQUIRED — {len(blocking)} unit(s) resemble "
            f"code this project already has. ('cm review' lists these holds.)",
            "Baseline NOT updated; the gate will re-flag until resolved.",
            blocking, out)
        return 2 if hook else 1

    clear_holds(root)
    _commit(root, result, _corpus_stats(result.records))
    if hook:
        # exit-0 stdout is visible in the verbose transcript: leave a trace of
        # what the gate absorbed so silent commits are reconstructible
        print(f"cm gate: {len(targets)} new/changed unit(s) screened clean and "
              f"absorbed into the baseline{delta}")
    else:
        print(f"gate clean: {len(targets)} changed unit(s) screened{delta}; "
              f"baseline updated")
    return 0


def cmd_gate(args) -> int:
    return _gate_run(Path(args.path).resolve(), args.top, args.hook)


def _proposed_content(path: Path, tool: str, tool_input: dict) -> str | None:
    """Model the post-tool content of a Write/Edit. None = cannot model: allow,
    and let the tool (or the post-write gate) handle it."""
    if tool == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None
    if tool in ("Edit", "MultiEdit"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        except OSError:
            return None
        edits = tool_input.get("edits") or [tool_input]
        for e in edits:
            old = (e.get("old_string") or "").replace("\r\n", "\n")
            new = (e.get("new_string") or "").replace("\r\n", "\n")
            if not old:
                return None
            hits = text.count(old)
            if hits == 0 or (hits > 1 and not e.get("replace_all")):
                return None  # the tool itself will reject this edit
            text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
        return text
    return None


def _precheck(root: Path, abspath: Path, proposed: str, top: int) -> int:
    """Score a proposed write before it reaches disk. 0 = allow, 2 = deny."""
    cache = load_cache(root)
    if not cache:
        return 0  # no baseline yet; the post-write gate will create it
    try:
        rel = abspath.resolve().relative_to(root).as_posix()
    except ValueError:
        return 0
    if IgnoreRules.load(root).ignored(rel, is_dir=False):
        return 0
    raw = proposed.encode("utf-8")
    if len(raw) > MAX_FILE_BYTES:
        return 0
    rec = record_from_bytes(raw, rel, str(abspath))
    if rec is None:
        return 0
    extract_units(rec)
    entry = cache.get(rel)
    prev_fps = {u["fp"] for u in entry["units"]} if entry else set()
    targets = [u for u in rec.units if u.scoreable and u.fp not in prev_fps]
    if not targets:
        return 0
    result = scan_tree(root, cache=cache)
    corpus = [u for r in result.records if r.path != rel
              for u in r.units if u.scoreable]
    corpus += [u for u in rec.units if u.scoreable]
    reports = score_targets(targets, corpus, top=top, detectors=load_enabled(root))
    blocking = _screen(reports, load_accepted(root))
    if blocking:
        save_holds(root, "precheck", blocking)
        _print_block(
            f"cm precheck: REVIEW REQUIRED — the write was withheld. "
            f"{len(blocking)} unit(s) resemble code this project already has. "
            f"('cm review' lists these holds.)",
            "Revise the write to reuse/extend the cited unit, or accept first and retry.",
            blocking, sys.stderr)
        return 2
    return 0


def cmd_hook(args) -> int:
    """Claude Code hook entry: reads the hook JSON on stdin.

    PreToolUse on Write/Edit -> score the proposed content and deny duplicates
    before they reach disk. Everything else -> reconcile via the gate.
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input") or {}
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    start = Path(target).parent if target else Path(payload.get("cwd") or ".")
    root = find_root(start)
    if root is None:
        return 0  # repo has not opted in to cm
    if payload.get("hook_event_name") == "PreToolUse":
        if not target:
            return 0
        proposed = _proposed_content(Path(target), payload.get("tool_name", ""), tool_input)
        if proposed is None:
            return 0
        return _precheck(root, Path(target), proposed, args.top)
    return _gate_run(root, args.top, hook=True)


def cmd_accept(args) -> int:
    root = Path(args.root).resolve()
    fps = []
    for fp in args.fps:
        if len(fp) != 8 or any(c not in "0123456789abcdef" for c in fp):
            print(f"error: {fp!r} is not a unit fingerprint (8 hex chars)", file=sys.stderr)
            return 2
        fps.append(fp)
    if args.match != "*" and (len(args.match) != 8
                              or any(c not in "0123456789abcdef" for c in args.match)):
        print(f"error: --match {args.match!r} is not a unit fingerprint", file=sys.stderr)
        return 2
    add_accepted(root, fps, args.reason, args.match)
    scope = f"against {args.match}" if args.match != "*" else "against any match"
    print(f"accepted {len(fps)} fingerprint(s) {scope}; recorded in the ledger")
    return 0


def cmd_review(args) -> int:
    root = Path(args.root).resolve()
    data = load_holds(root)
    holds = data.get("holds", [])
    if args.json:
        print(json.dumps(data, indent=2))
        return 1 if holds else 0
    if not holds:
        print("no pending holds")
        return 0
    withheld = data.get("source") == "precheck"
    print(f"{len(holds)} pending hold(s) from {data.get('source', '?')} "
          f"at {data.get('seen', '?')}"
          + (" — the write was withheld, nothing landed on disk" if withheld else ""))
    for h in holds:
        print(f"\n[ REVIEW] {h['signature'] or h['unit']}  "
              f"{h['file']}@{h['span'][0]}-{h['span'][1]}  #{h['fp']}")
        for m in h["matches"]:
            print(f"    resembles {m['unit']}  {m['file']}@{m['span'][0]}-{m['span'][1]}")
            for reason in m["reasons"]:
                print(f"       - {reason}")
            if m["shared"]:
                print("       shared tokens: " + ", ".join(repr(s) for s in m["shared"]))
            print(f'       to record as intentional:  cm accept {h["fp"]} '
                  f'--match {m["fp"]} --reason "..."')
    print("\nResolve by reusing/extending the cited units (then re-run the write "
          "or 'cm gate'), or accept the pairs above.")
    return 1


def cmd_ledger(args) -> int:
    lines = ledger_lines(Path(args.root).resolve())
    if not lines:
        print("ledger is empty")
        return 0
    print("accepted resemblances (target_fp match_fp  # reason):")
    for line in lines:
        print(f"  {line}")
    return 0


_PROTO_BEGIN = "<!-- cm:protocol:begin -->"
_PROTO_END = "<!-- cm:protocol:end -->"
_PROTOCOL = f"""{_PROTO_BEGIN}
## cm — redundancy gate

This repo is compiled into PROJECT.cm (every file's functions, fingerprints,
and algorithm skeletons). The goal is token efficiency: never rewrite what
the codebase already contains.

- Writes are checked BEFORE they land: the precheck hook withholds writes
  that resemble existing code, with the file untouched. After clean writes
  land, `cm gate` reconciles the baseline incrementally.
- If a write is held for REVIEW, stop and read the cited unit (file@lines;
  the shared tokens explain the resemblance). Then decide: reuse it, extend
  it, or record an intentional difference.
- If the similarity is intentional, run `cm accept <fp>` and continue.
- PROJECT.cm and the baseline update automatically when the gate passes.
{_PROTO_END}"""


def _write_protocol(root: Path) -> str:
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = root / name
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            if _PROTO_BEGIN in text and _PROTO_END in text:
                head, _, rest = text.partition(_PROTO_BEGIN)
                _, _, tail = rest.partition(_PROTO_END)
                text = head + _PROTOCOL + tail
            else:
                text = text.rstrip("\n") + "\n\n" + _PROTOCOL + "\n"
            p.write_text(text, encoding="utf-8", newline="\n")
            return f"protocol section written to existing {name}"
    (root / "CLAUDE.md").write_text(_PROTOCOL + "\n", encoding="utf-8", newline="\n")
    return "protocol written to new CLAUDE.md"


def _install_hook(root: Path) -> str:
    """Install both write gates: PreToolUse denies duplicates before they reach
    disk, PostToolUse reconciles the baseline after clean writes land."""
    p = root / ".claude" / "settings.json"
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return "SKIPPED hook install: .claude/settings.json is not valid JSON"
    command = "cm hook" if shutil.which("cm") else f'"{sys.executable}" -m cm hook'
    hooks = data.setdefault("hooks", {})
    installed = []
    for event in ("PreToolUse", "PostToolUse"):
        matchers = hooks.setdefault(event, [])
        stale = [m for m in matchers
                 if any("cm gate --hook" in h.get("command", "") for h in m.get("hooks", []))]
        for m in stale:
            matchers.remove(m)  # supersede the pre-0.3 post-only gate
        if any("cm hook" in h.get("command", "")
               for m in matchers for h in m.get("hooks", [])):
            continue
        matchers.append({"matcher": "Write|Edit|MultiEdit",
                         "hooks": [{"type": "command", "command": command}]})
        installed.append(event)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    if not installed:
        return "hooks already installed in .claude/settings.json"
    return f"{' + '.join(installed)} hooks installed in .claude/settings.json ({command})"


def cmd_init(args) -> int:
    root = Path(args.path).resolve()
    notes = []
    cmignore = root / ".cmignore"
    if not cmignore.is_file():
        cmignore.write_text(
            "# cm ignore rules (gitignore syntax). Defaults already exclude VCS\n"
            "# dirs, caches, binaries, .cm/ state, and *.cm outputs.\n",
            encoding="utf-8", newline="\n")
        notes.append(".cmignore created")
    result = scan_tree(root)  # full compile for a trustworthy baseline
    stats = _corpus_stats(result.records)
    _commit(root, result, stats)
    notes.append(f"PROJECT.cm + .cm/ baseline built "
                 f"({stats['files']} files, {stats['units']} units)")
    notes.append(_write_protocol(root))
    if args.hooks:
        notes.append(_install_hook(root))
    else:
        notes.append("agent hook not installed (rerun with --hooks to gate writes automatically)")
    if not shutil.which("cm"):
        notes.append("note: 'cm' not on PATH — install with: pip install -e <cm repo>  (or pipx)")
    print("cm init:")
    for n in notes:
        print(f"  - {n}")
    return 0


def cmd_detectors(args) -> int:
    root = Path(args.root).resolve()
    changes = {}
    for name in args.enable:
        changes[name] = True
    for name in args.disable:
        changes[name] = False
    unknown = [n for n in changes if n not in REGISTRY]
    if unknown:
        print(f"error: unknown detector(s): {', '.join(unknown)} "
              f"(known: {', '.join(REGISTRY)})", file=sys.stderr)
        return 2
    if changes:
        set_enabled(root, changes)
    enabled = enabled_names(root)
    print(f"detectors for {root}:")
    for name, cls in REGISTRY.items():
        doc = (sys.modules[cls.__module__].__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else ""
        print(f"  {'on ' if enabled[name] else 'OFF'}  {name:12} {summary}")
    return 0


def cmd_drift(args) -> int:
    cm_path = Path(args.cm)
    if not cm_path.is_file():
        print(f"error: {args.cm} not found (run 'cm build' first)", file=sys.stderr)
        return 2
    _, files = parse(cm_path.read_text(encoding="utf-8"))

    if args.manifest == "-":
        tokens = [ln.strip() for ln in sys.stdin.read().splitlines()]
    else:
        tokens = [ln.strip() for ln in
                  Path(args.manifest).read_text(encoding="utf-8").splitlines()]
    tokens = [t for t in tokens if t and not t.startswith("#")]
    have_fps, have_paths, have_qual = set(), set(), {}
    for tok in tokens:
        if "#" in tok:
            pathpart, _, unitpart = tok.partition("#")
            qual, _, fp = unitpart.partition("@")
            have_qual[f"{pathpart}#{qual}"] = fp
        elif "/" in tok or "." in tok and len(tok) != 8:
            have_paths.add(tok)
        else:
            have_fps.add(tok)

    covered, stale, missing = [], [], []
    for f in files:
        for u in f.units:
            key = f"{f.path}#{u.qualname}"
            status = "missing"
            if u.fp in have_fps or f.path in have_paths:
                status = "covered"
            elif key in have_qual:
                status = "covered" if have_qual[key] == u.fp else "stale"
            if status == "covered":
                covered.append(key)
                continue
            lines = u.end - u.start + 1
            (stale if status == "stale" else missing).append((key, u.kind, lines))

    total = len(covered) + len(stale) + len(missing)
    lines_to_sync = sum(x[2] for x in stale + missing)
    if args.json:
        print(json.dumps({
            "units": total, "covered": len(covered), "stale": len(stale),
            "missing": len(missing), "lines_to_sync": lines_to_sync,
            "stale_units": [{"unit": k, "lines": n} for k, _, n in stale],
            "missing_units": [{"unit": k, "lines": n} for k, _, n in missing],
        }, indent=2))
        return 0
    print(f"DRIFT vs {cm_path.name}: {len(covered)}/{total} units in context, "
          f"{len(stale)} stale, {len(missing)} missing")
    print(f"  to sync: {lines_to_sync:,} lines across {len(stale) + len(missing)} units")
    for key, kind, lines in sorted(stale + missing, key=lambda x: -x[2])[:10]:
        print(f"    {kind:<8} {key}  {lines:,} lines")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cm",
        description="Compile a codebase into a context management artifact (PROJECT.cm) "
                    "and police redundancy with information-theoretic checks.")
    p.add_argument("--version", action="version", version=f"cm {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="compile the tree into PROJECT.cm (incremental)")
    b.add_argument("path", nargs="?", default=".")
    b.add_argument("-o", "--output", default=None)
    b.add_argument("--full", action="store_true", help="ignore the cache, recompile everything")
    b.add_argument("--no-content", action="store_true",
                   help="index only: omit file contents from the .cm")
    b.set_defaults(fn=cmd_build)

    st = sub.add_parser("status", help="is the baseline current? what changed?")
    st.add_argument("path", nargs="?", default=".")
    st.set_defaults(fn=cmd_status)

    g = sub.add_parser("gate", help="recompile + screen changed units; hold resemblances for review")
    g.add_argument("path", nargs="?", default=".")
    g.add_argument("--hook", action="store_true",
                   help="agent-hook mode: silent on success, findings to stderr, exit 2 on hold")
    g.add_argument("--top", type=int, default=3)
    g.set_defaults(fn=cmd_gate)

    hk = sub.add_parser("hook", help="Claude Code hook entry (hook JSON on stdin): "
                                     "PreToolUse withholds resembling writes pre-disk, "
                                     "PostToolUse reconciles via the gate")
    hk.add_argument("--top", type=int, default=3)
    hk.set_defaults(fn=cmd_hook)

    ac = sub.add_parser("accept", help="record a review decision in the ledger")
    ac.add_argument("fps", nargs="+", metavar="fp")
    ac.add_argument("--root", default=".")
    ac.add_argument("--match", default="*", metavar="fp",
                    help="scope the decision to one matched unit (default: any match)")
    ac.add_argument("--reason", default="", help="why the resemblance is intentional (recorded)")
    ac.set_defaults(fn=cmd_accept)

    rv = sub.add_parser("review", help="list pending holds with evidence and resolutions")
    rv.add_argument("--root", default=".")
    rv.add_argument("--json", action="store_true")
    rv.set_defaults(fn=cmd_review)

    lg = sub.add_parser("ledger", help="list recorded review decisions")
    lg.add_argument("--root", default=".")
    lg.set_defaults(fn=cmd_ledger)

    ini = sub.add_parser("init", help="install cm into a repo: baseline + agent protocol")
    ini.add_argument("path", nargs="?", default=".")
    ini.add_argument("--hooks", action="store_true",
                     help="also install the Claude Code PostToolUse write gate")
    ini.set_defaults(fn=cmd_init)

    c = sub.add_parser("check", help="screen files for resemblance vs the tree")
    c.add_argument("targets", nargs="+", help="files treated as 'new code'")
    c.add_argument("--root", default=".", help="corpus root (default: cwd)")
    c.add_argument("--top", type=int, default=3)
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)

    a = sub.add_parser("audit", help="pairwise resemblance self-audit of the whole tree")
    a.add_argument("path", nargs="?", default=".")
    a.add_argument("--top", type=int, default=3)
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_audit)

    dt = sub.add_parser("detectors", help="list or toggle tripwire detectors for a repo")
    dt.add_argument("--root", default=".")
    dt.add_argument("--enable", nargs="*", default=[], metavar="NAME")
    dt.add_argument("--disable", nargs="*", default=[], metavar="NAME")
    dt.set_defaults(fn=cmd_detectors)

    d = sub.add_parser("drift", help="[experimental] context-vs-PROJECT.cm divergence")
    d.add_argument("manifest", help="file of section refs the context holds "
                                    "(fp8 | path | path#qualname@fp8), '-' for stdin")
    d.add_argument("--cm", default="PROJECT.cm")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_drift)

    args = p.parse_args(argv)
    return args.fn(args)
