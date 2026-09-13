#!/usr/bin/env python3
"""Phase-1 runner: snapshot + scripted specialists in one call.

Runs collect_snapshot.py, then probe_access.py, analyze_representation.py and
check_entities.py over the snapshot, validates the fragments, and prints one
status table. Pure composition for turn-count: the model makes one call and
reads one table instead of run-read-act cycling per script. No behavior change
- every script keeps its CLI and runs unchanged; the merge still owns ids,
dedup, severity, and the report.

Failure semantics: a specialist that fails or writes no fragment contributes
not_evaluated (its row says so); only a fatal snapshot aborts with exit 1.
Every directory is created here - a missing dir never fails a run.

Usage:
  python3 run_phase1.py --url <URL> --out-dir ./audit
      --site-type saas,ecommerce --capabilities web_fetch[,web_search][,browser][,subagents]
      [--max-pages N] [--deadline S] [--allow-private]
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

SPECIALISTS = (
    ("access-discovery-audit", "access-discovery-audit/scripts/probe_access.py"),
    ("representation-parity-audit",
     "representation-parity-audit/scripts/analyze_representation.py"),
    ("entity-consistency-audit", "entity-consistency-audit/scripts/check_entities.py"),
)


def resolve_scripts(here):
    """Sibling skill dirs first, else the marketplace manifest walk. Never raises."""
    sib = os.path.normpath(os.path.join(here, "..", ".."))
    found = [(sid, os.path.join(sib, rel)) for sid, rel in SPECIALISTS]
    if all(os.path.exists(p) for _, p in found):
        return found
    cur = os.path.abspath(here)
    for _ in range(6):
        for name in ("marketplace.json", os.path.join(".agents", "marketplace.json")):
            mp = os.path.join(cur, name)
            if os.path.exists(mp):
                try:
                    manifest = json.load(open(mp))
                    by_id = {s["id"]: s.get("path") for s in manifest.get("skills", [])}
                    root = os.path.dirname(mp)
                    out = []
                    for sid, rel in SPECIALISTS:
                        mp_rel = by_id.get(sid)
                        p = (os.path.join(root, mp_rel, "scripts", os.path.basename(rel))
                             if mp_rel else os.path.join(sib, rel))
                        out.append((sid, p if os.path.exists(p) else None))
                    return out
                except (OSError, ValueError, KeyError):
                    pass
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return [(sid, (p if os.path.exists(p) else None)) for sid, p in found]


def run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except Exception as e:  # noqa: BLE001 - record, never raise
        return 99, "", "runner: %s" % e


def print_semantic_gates(frag_dir):
    """Print every prepared_gate the scripted specialists left for the model.

    The gate text carries everything needed to decide, so surfacing it here
    saves opening the fragment just to read one string - the decision and the
    write_fragment call that records it can happen in the same turn.
    """
    lines = []
    for name in sorted(os.listdir(frag_dir)) if os.path.isdir(frag_dir) else []:
        if not name.endswith(".json"):
            continue
        try:
            frag = json.load(open(os.path.join(frag_dir, name)))
        except (OSError, ValueError):
            continue
        for res in frag.get("results") or []:
            gate = (res.get("observations") or {}).get("prepared_gate")
            if not gate:
                continue
            obs = {k: v for k, v in (res.get("observations") or {}).items()
                   if k not in ("prepared_gate", "requires_completion")
                   and not isinstance(v, (dict, list))}
            lines.append((frag.get("skill_id") or name, res.get("check_id"), gate, obs,
                          bool((res.get("observations") or {}).get("requires_completion"))))
    todo = [ln for ln in lines if ln[4]]
    if not todo:
        return
    print("semantic gate(s) awaiting your decision - decide and record in ONE call, "
          "do not open the fragment:")
    for skill_id, check_id, gate, obs, _ in todo:
        print("  %s / %s" % (skill_id, check_id))
        for key, val in obs.items():
            print("      %s: %s" % (key, str(val)[:160]))
        print("      ASK: %s" % gate[:900])
        print("      RECORD: python <orchestrator>/scripts/write_fragment.py "
              "--in <out-dir>/findings/%s.json --complete-gate %s "
              "--gate pass|not_evaluated --reason \"<1-3 sentences>\""
              % (skill_id, check_id))
    skipped = len(lines) - len(todo)
    if skipped:
        print("  (%d other prepared pass(es) need no action - leave them untouched)"
              % skipped)


def summarize_fragment(path):
    try:
        frag = json.load(open(path))
    except (OSError, ValueError):
        return None
    gates: dict = {}
    for r in frag.get("results", []):
        gates[r.get("gate", "?")] = gates.get(r.get("gate", "?"), 0) + 1
    findings = [{"check_id": r.get("check_id"),
                 "title": (r.get("candidate_finding") or {}).get("title", "")[:90]}
                for r in frag.get("results", []) if r.get("gate") == "finding"]
    return (len(findings),
            gates.get("pass", 0),
            len(frag.get("not_evaluated", [])) + gates.get("not_evaluated", 0),
            findings)


def inject_extras(out_dir, frag_paths):
    """Relay phase-1 deterministic facts into the judgment excerpts so the model
    never re-reads phase-1 fragments for pairing, and never re-derives the
    boilerplate gate numbers (analyze_representation already measured them)."""
    phase1_findings, extraction_obs, extraction_gate = [], None, None
    for frag in frag_paths:
        try:
            data = json.load(open(frag))
        except (OSError, ValueError):
            continue
        for r in data.get("results", []):
            if r.get("gate") == "finding":
                phase1_findings.append({"skill_id": data.get("skill_id"),
                                        "check_id": r.get("check_id"),
                                        "title": (r.get("candidate_finding") or {}).get("title", "")[:110]})
            if r.get("check_id") == "REP-EXTRACTION-LOSS":
                extraction_obs = r.get("observations") or {}
                extraction_gate = r.get("gate")
    if not phase1_findings and not extraction_obs:
        return
    phase1_findings = [{k: v for k, v in f.items() if v} for f in phase1_findings]
    for name in ("answer-coverage-audit", "freshness-consistency-audit",
                 "referral-experience-audit"):
        path = os.path.join(out_dir, "excerpts", "%s.json" % name)
        try:
            exc = json.load(open(path))
        except (OSError, ValueError):
            continue
        extras = exc.setdefault("extras", {})
        extras["phase1_findings"] = phase1_findings
        if name == "answer-coverage-audit" and extraction_obs:
            extras["boilerplate"] = {
                "pages_sharing_preamble": extraction_obs.get("pages_sharing_preamble",
                    extraction_obs.get("pages_sharing_long_preamble", 0)),
                "preamble_chars": extraction_obs.get("preamble_chars", 0),
                "median_unique_content_offset": extraction_obs.get("median_unique_content_offset", 0),
                "rep_extraction_loss_gate": extraction_gate or "pass",
                "note": "measured by analyze_representation.py (REP-EXTRACTION-LOSS); "
                        "the ANS-BOILERPLATE-DROWNING gate is shared-preamble pages >= half "
                        "the sample AND median offset > 1500 chars"}
        try:
            exc["file_chars"] = len(json.dumps(exc, indent=1, ensure_ascii=False))
            json.dump(exc, open(path, "w"), indent=1, ensure_ascii=False)
        except OSError:
            pass


def write_budget(out_dir, snap_started_iso, elapsed_now, budget_seconds=300):
    """Create or refresh <out-dir>/budget.json - the audit's single clock.

    started_at is written once and never moved: a later phase re-running this
    must not reset the budget. Existing shed rows are preserved.
    """
    path = os.path.join(out_dir, "budget.json")
    record = {"started_at": None, "budget_seconds": budget_seconds, "shed": []}
    if os.path.exists(path):
        try:
            prev = json.load(open(path))
            record["started_at"] = prev.get("started_at")
            record["shed"] = prev.get("shed") or []
            record["budget_seconds"] = prev.get("budget_seconds", budget_seconds)
        except (OSError, ValueError):
            pass
    if not record["started_at"]:
        # collection_started_at is when fetching BEGAN. audited_at is when it
        # ended, so it is only the last resort - anchoring the budget there
        # silently discards the collection phase and reports a near-zero runtime.
        snap = os.path.join(out_dir, "snapshot.json")
        try:
            sn = json.load(open(snap))
            record["started_at"] = sn.get("collection_started_at") or snap_started_iso                 or sn["audited_at"]
        except (OSError, ValueError, KeyError):
            record["started_at"] = snap_started_iso or datetime.fromtimestamp(
                time.time() - max(0, elapsed_now), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
            fh.write("\n")
        print("phase1: budget clock pinned at %s (%s)" % (record["started_at"], path))
    except OSError as e:  # noqa: BLE001 - the sidecar is additive; never fail phase 1 over it
        print("phase1: note - budget.json not written (%s)" % e)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Phase 1: snapshot + scripted specialists, one call.")
    ap.add_argument("--url", required=True)
    ap.add_argument("--out-dir", default="audit")
    ap.add_argument("--site-type", default="auto",
                    help="'auto' lets collect_snapshot propose from the fetched pages")
    ap.add_argument("--reuse-snapshot", action="store_true",
                    help="use an existing <out-dir>/snapshot.json instead of collecting "
                         "again (the orchestrator's step 2 already collected)")
    ap.add_argument("--capabilities", default="web_fetch")
    ap.add_argument("--max-pages", type=int, default=8)
    ap.add_argument("--deadline", type=int, default=120)
    ap.add_argument("--allow-private", action="store_true")
    args = ap.parse_args(argv)

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.abspath(args.out_dir)
    frag_dir = os.path.join(out_dir, "findings")
    os.makedirs(frag_dir, exist_ok=True)
    snap = os.path.join(out_dir, "snapshot.json")
    t0 = time.monotonic()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("phase1 wall-clock: started %s (shed off-site probes if more than 210 s "
          "have elapsed when wave 2 begins)" % started)

    if args.reuse_snapshot and os.path.exists(snap):
        # The orchestrator's step 2 already collected. Re-collecting here spent a
        # second network pass and a second model turn on identical bytes.
        print("phase1: snapshot REUSED (%s) - no second collection" % snap)
    else:
        cmd = [sys.executable, os.path.join(here, "collect_snapshot.py"),
               "--url", args.url, "--out", snap, "--site-type", args.site_type,
               "--capabilities", args.capabilities, "--max-pages", str(args.max_pages),
               "--deadline", str(args.deadline)]
        if args.allow_private:
            cmd.append("--allow-private")
        rc, out, err = run(cmd, args.deadline + 120)
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-7:]
        print("phase1: snapshot %s" % ("OK" if rc == 0 else "FATAL (rc=%d)" % rc))
        for ln in tail:
            print("  | %s" % ln[:220])
        if err.strip() and rc != 0:
            print("  ! %s" % err.strip().splitlines()[-1][:220])
        if rc != 0 or not os.path.exists(snap):
            print("phase1 status: snapshot failed - specialists skipped, "
                  "record the notes above")
            return 1

    rows = []
    frag_paths = []
    for skill_id, script in resolve_scripts(here):
        frag = os.path.join(frag_dir, "%s.json" % skill_id)
        if script is None:
            rows.append((skill_id, "missing script - contributes not_evaluated"))
            continue
        rc, out, err = run([sys.executable, script, "--snapshot", snap, "--out", frag], 180)
        summary = summarize_fragment(frag) if os.path.exists(frag) else None
        if summary is None:
            rows.append((skill_id, "no fragment (rc=%d) - contributes not_evaluated" % rc))
        else:
            f, ps, ne, fl = summary
            rows.append((skill_id, "fragment ok: %d finding(s), %d pass, %d ne" % (f, ps, ne)))
            for item in fl:
                rows.append(("", "FINDING %s: %s" % (item["check_id"], item["title"])))
            frag_paths.append(frag)
    validator = os.path.join(here, "validate_fragment.py")
    if frag_paths and os.path.exists(validator):
        rc, out, _ = run([sys.executable, validator] + frag_paths, 120)
        rows.append(("validate", "clean" if rc == 0 else "REJECTS - read lines below"))
        if rc != 0:
            for ln in out.strip().splitlines()[:8]:
                rows.append(("", ln[:200]))
    inject_extras(out_dir, frag_paths)
    print("phase1 status:")
    for skill_id, status in rows:
        print("  %-28s %s" % (skill_id, status))
    print_semantic_gates(frag_dir)
    # Elapsed since the AUDIT began, not since this process did: with
    # --reuse-snapshot a fresh t0 would reset the budget clock and under-report.
    elapsed = time.monotonic() - t0
    try:
        snap_started = datetime.fromisoformat(
            json.load(open(snap))["audited_at"].replace("Z", "+00:00")).timestamp()
        elapsed = max(elapsed, time.time() - snap_started)
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    print("BUDGET %ds/300s after phase1 | SHED: %s | TIMEBOX: %s" %
          (int(elapsed),
           "offsite (+referral 3q)" if elapsed > 210 else "none",
           "answer-coverage=core-only" if elapsed > 150 else "full"))
    # Pin the audit's start to a file so the same clock the shed gates read is
    # the clock build_report publishes as coverage.time_seconds. Carrying a
    # timestamp through the audit by hand would cost a model turn and be wrong
    # the first time someone re-ran a step.
    write_budget(out_dir, snap_started_iso=started, elapsed_now=elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
