#!/usr/bin/env python3
"""build_report.py - merge specialist finding fragments into the final audit report.

Contract gate G1: fragment(s) in, schema-validated report out. Root-cause
dedup, never-claim lint, coverage backfill, and the composed human summary are
active.

Stdlib only. Schema validation uses the vendored draft-07 subset validator below
because `jsonschema` is NOT in the standard library and the judge's machine gets
no third-party installs. The real jsonschema package remains the dev-time oracle
in tests/run_contract_tests.py.

Discipline (agentskills "using scripts"): reads files from disk, prints small
summaries to stdout, diagnostics to stderr, meaningful exit codes, no prompts.
"""

import argparse
import datetime
import json
import os
import re
import sys

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ---------------------------------------------------------------------------
# Vendored draft-07 subset validator.
# Supports only the keywords our schemas use: type, enum, const, required,
# properties, additionalProperties, items, maxItems, pattern, minimum,
# minLength, maxLength, in-file $ref. Annotation keywords (title, description,
# $id, $schema, format) are ignored, matching draft-07 default behavior.
# oneOf/allOf/if-then are NOT supported - none of the runtime-validated schemas
# (output_schema, finding_fragment, snapshot_schema) use them;
# excerpts_schema.json is dev-oracle-only.
# ---------------------------------------------------------------------------


def _resolve_ref(ref, root):
    if not ref.startswith("#"):
        raise ValueError("only in-file $ref is supported: %s" % ref)
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def _type_ok(inst, t):
    if t == "object":
        return isinstance(inst, dict)
    if t == "array":
        return isinstance(inst, list)
    if t == "string":
        return isinstance(inst, str)
    if t == "integer":
        return isinstance(inst, int) and not isinstance(inst, bool)
    if t == "number":
        return isinstance(inst, (int, float)) and not isinstance(inst, bool)
    if t == "boolean":
        return isinstance(inst, bool)
    if t == "null":
        return inst is None
    return False


def validate(inst, schema, root=None, path="$"):
    """Return a list of error strings (empty means valid)."""
    root = schema if root is None else root
    if "$ref" in schema:
        return validate(inst, _resolve_ref(schema["$ref"], root), root, path)
    errs = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(inst, t) for t in types):
            return ["%s: expected type %s, got %s" % (path, schema["type"], type(inst).__name__)]
    if "enum" in schema and inst not in schema["enum"]:
        errs.append("%s: %r is not one of %r" % (path, inst, schema["enum"]))
    if "const" in schema and inst != schema["const"]:
        errs.append("%s: %r != const %r" % (path, inst, schema["const"]))
    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append("%s: shorter than minLength %d" % (path, schema["minLength"]))
        if "maxLength" in schema and len(inst) > schema["maxLength"]:
            errs.append("%s: longer than maxLength %d" % (path, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append("%s: %r does not match %r" % (path, inst, schema["pattern"]))
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append("%s: below minimum %s" % (path, schema["minimum"]))
    if isinstance(inst, list):
        if "maxItems" in schema and len(inst) > schema["maxItems"]:
            errs.append("%s: more than maxItems %d" % (path, schema["maxItems"]))
        if "items" in schema:
            for i, item in enumerate(inst):
                errs.extend(validate(item, schema["items"], root, "%s[%d]" % (path, i)))
    if isinstance(inst, dict):
        for req in schema.get("required", []):
            if req not in inst:
                errs.append("%s: missing required property %r" % (path, req))
        props = schema.get("properties", {})
        for key in sorted(inst):
            sub = props.get(key)
            if sub is not None:
                errs.extend(validate(inst[key], sub, root, "%s.%s" % (path, key)))
            else:
                ap = schema.get("additionalProperties", True)
                if ap is False:
                    errs.append("%s: unexpected property %r" % (path, key))
                elif isinstance(ap, dict):
                    errs.extend(validate(inst[key], ap, root, "%s.%s" % (path, key)))
    return errs


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fail(what, errs):
    sys.stderr.write("build_report: FAIL - %s\n" % what)
    for e in errs:
        sys.stderr.write("  - %s\n" % e)
    sys.exit(1)


BUDGET_DEFAULT_SECONDS = 300


def _parse_started_at(value):
    """ISO 8601 (with or without trailing Z) or epoch seconds -> epoch float, or None."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError, AttributeError):
        return None


def _parse_shed_arg(raw):
    """'what@seconds:reason' -> a shed row. Seconds and reason are optional."""
    text = (raw or "").strip()
    if not text:
        return None
    reason = ""
    if ":" in text:
        text, reason = text.split(":", 1)
    at = 0
    if "@" in text:
        text, _, tail = text.partition("@")
        try:
            at = int(float(tail))
        except (TypeError, ValueError):
            at = 0
    row = {"what": text.strip()[:120], "at_seconds": max(0, at)}
    if reason.strip():
        row["reason"] = reason.strip()[:300]
    return row if row["what"] else None


def _budget_record(args):
    """Read the budget sidecar written by run_phase1 / the passages post-step.

    The sidecar lives next to snapshot.json so neither the model nor the caller
    has to carry a timestamp through the audit by hand - the clock that the shed
    gates already read is the same clock the report publishes. Explicit CLI flags
    win over the sidecar; the snapshot's own audited_at is the last fallback, so
    time_seconds is populated even for a run that never touched run_phase1.
    """
    record = {"started_at": None, "budget_seconds": BUDGET_DEFAULT_SECONDS, "shed": []}
    sidecar = None
    if args.budget_file:
        sidecar = args.budget_file
    elif args.snapshot:
        sidecar = os.path.join(os.path.dirname(os.path.abspath(args.snapshot)), "budget.json")
    if sidecar and os.path.exists(sidecar):
        try:
            data = load_json(sidecar)
            record["started_at"] = _parse_started_at(data.get("started_at"))
            if isinstance(data.get("budget_seconds"), int):
                record["budget_seconds"] = data["budget_seconds"]
            for row in data.get("shed") or []:
                if isinstance(row, dict) and row.get("what"):
                    record["shed"].append({
                        "what": str(row["what"])[:120],
                        "at_seconds": max(0, int(row.get("at_seconds") or 0)),
                        **({"reason": str(row["reason"])[:300]} if row.get("reason") else {})})
        except (OSError, ValueError, TypeError, KeyError):
            pass  # a malformed sidecar degrades to "not tracked", never fails the report
    if args.started_at:
        record["started_at"] = _parse_started_at(args.started_at) or record["started_at"]
    if args.budget_seconds:
        record["budget_seconds"] = args.budget_seconds
    for raw in args.shed or []:
        row = _parse_shed_arg(raw)
        if row:
            record["shed"].append(row)
    seen = set()
    deduped = []
    for row in record["shed"]:
        if row["what"].lower() in seen:
            continue
        seen.add(row["what"].lower())
        deduped.append(row)
    record["shed"] = sorted(deduped, key=lambda r: (r["at_seconds"], r["what"]))
    return record


def main():
    ap = argparse.ArgumentParser(
        description="Merge specialist finding fragments into the final audit report.")
    ap.add_argument("--fragment", action="append", default=[],
                    help="finding fragment JSON file; repeatable")
    ap.add_argument("--site", required=True, help="audited host, e.g. example.com")
    ap.add_argument("--out", required=True, help="output report path")
    ap.add_argument("--snapshot", help="optional snapshot.json - fills coverage from it")
    ap.add_argument("--degraded", action="store_true",
                    help="single-skill degraded mode: no specialist fragments resolved")
    ap.add_argument("--marketplace-version", default="1.0.0")
    ap.add_argument("--started-at",
                    help="audit start as ISO 8601 UTC or epoch seconds; overrides the "
                         "budget.json sidecar. Omit it and the sidecar next to the "
                         "snapshot (or the snapshot's own audited_at) supplies the clock.")
    ap.add_argument("--budget-seconds", type=int, default=0,
                    help="whole-audit wall-clock budget (default %d)" % BUDGET_DEFAULT_SECONDS)
    ap.add_argument("--budget-file",
                    help="path to the budget sidecar; defaults to budget.json beside --snapshot")
    ap.add_argument("--shed", action="append", default=[],
                    help="work dropped to stay in budget, as 'what@seconds:reason'; "
                         "repeatable. Merged with whatever the sidecar already recorded.")
    args = ap.parse_args()

    refs_dir = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references"))
    frag_schema = load_json(os.path.join(refs_dir, "finding_fragment.json"))
    out_schema = load_json(os.path.join(refs_dir, "output_schema.json"))
    catalog_path = os.path.join(refs_dir, "check_catalog.json")
    if not os.path.exists(catalog_path):
        fail("missing check_catalog.json", [catalog_path])
    catalog = load_json(catalog_path)
    checks_by_id = {c["check_id"]: c for c in catalog["checks"]}

    findings = []
    needs_verification = []
    not_evaluated = []
    checks_passed = []
    opportunities = []
    skill_ids = set()
    all_reported = set()
    lint_warnings = []
    limitations_extra = []
    fragments_loaded = 0
    for frag_path in args.fragment:
        try:
            frag = load_json(frag_path)
        except (OSError, ValueError) as e:
            lint_warnings.append("fragment %s unreadable (%s); its checks are not_evaluated"
                                 % (frag_path, str(e)[:80]))
            limitations_extra.append("Specialist fragment %s was unreadable and was excluded."
                                     % os.path.basename(frag_path))
            continue
        errs = validate(frag, frag_schema)
        if errs:
            # Salvage, not refusal: one invalid fragment must never zero the
            # audit. Skip its results; the not_evaluated backfill below marks
            # all of that skill's checks, and the report still gets built.
            sid = frag.get("skill_id") if isinstance(frag, dict) else None
            lint_warnings.append("fragment %s failed finding_fragment.json validation: %s"
                                 % (frag_path, errs[0][:120]))
            limitations_extra.append("Specialist fragment %s (%s) failed validation and was"
                                     " excluded; its checks are not_evaluated."
                                     % (os.path.basename(frag_path), sid or "unknown skill"))
            continue
        skill_ids.add(frag["skill_id"])
        fragments_loaded += 1
        if not frag.get("results") and not frag.get("not_evaluated"):
            lint_warnings.append("fragment %s from %s is silent (0 results and 0 "
                                 "not_evaluated): the specialist contributed nothing; its "
                                 "checks are backfilled, not judged" % (frag_path, frag["skill_id"]))
        if not frag.get("results") and frag.get("not_evaluated"):
            if not any(re.search(r"capabilit|web_search|browser|deadline|no search|"
                                 r"no pages|empty snapshot|nothing captured|unreachable",
                                 (ne.get("reason") or ""), re.I)
                       for ne in frag["not_evaluated"]):
                lint_warnings.append("fragment %s from %s judged nothing (0 results, all %d "
                                     "checks deferred as not_evaluated); verify the specialist "
                                     "attempted judgment" % (frag_path, frag["skill_id"],
                                                             len(frag["not_evaluated"])))
        for result in frag.get("results", []):
            all_reported.add(result["check_id"])
            gate = result.get("gate")
            if gate == "pass":
                if result.get("evidence_quality") == "hypothesis":
                    not_evaluated.append({
                        "check_id": result["check_id"],
                        "reason": ("pass asserted on hypothesis-grade evidence; treated as "
                                   "not_evaluated (see fragment)"),
                    })
                    lint_warnings.append("fragment %s grades %s as pass on hypothesis evidence; "
                                         "downgraded to not_evaluated" % (frag_path, result["check_id"]))
                else:
                    # A pass is a checked-and-clean verdict and belongs in the
                    # report: "what passed" is the half a reader needs to trust
                    # the half that did not.
                    checks_passed.append(result["check_id"])
                continue
            if gate == "not_evaluated":
                not_evaluated.append({
                    "check_id": result["check_id"],
                    "reason": "specialist reported not_evaluated (see fragment)",
                })
                continue
            PROBE_STATUS_CHECKS = ("REF-SOFT-404", "ACC-BOT-CHALLENGE")
            if result["check_id"] in PROBE_STATUS_CHECKS and not re.search(
                    r"\b\d{3}\b", (result.get("candidate_finding") or {}).get("evidence", "")):
                # A probe verdict quoting no HTTP status is assertion-shaped:
                # the observation (path + measured status) is the evidence.
                not_evaluated.append({
                    "check_id": result["check_id"],
                    "reason": ("probe finding records no HTTP status; treated as "
                               "not_evaluated (see fragment)"),
                })
                lint_warnings.append("fragment %s %s records a probe verdict without a status "
                                     "code; downgraded to not_evaluated"
                                     % (frag_path, result["check_id"]))
                continue
            cand = result["candidate_finding"]
            action = {"summary": cand["suggested_action"]["summary"],
                      "priority": cand["suggested_action"]["priority"]}
            for opt in ("effort", "owner", "acceptance_test"):
                if opt in cand["suggested_action"]:
                    action[opt] = cand["suggested_action"][opt]
            entry = {
                "check_id": result["check_id"],
                "title": cand["title"],
                "severity": cand["severity"],
                "confidence": cand["confidence"],
                "evidence": cand["evidence"],
                "suggested_action": action,
            }
            if cand.get("why_it_matters"):
                entry["why_it_matters"] = cand["why_it_matters"]
            if result.get("urls"):
                entry["affected_urls"] = result["urls"]
            if cand.get("affected_surfaces"):
                entry["affected_surfaces"] = cand["affected_surfaces"]
            if cand["confidence"] == "low":
                # Reporting gate rule 2: low-confidence hypotheses are never findings.
                needs_verification.append({
                    "title": cand["title"],
                    "evidence": cand["evidence"],
                    "what_would_confirm": cand.get("why_it_matters")
                    or "re-observation across runs or owner-side verification",
                })
            else:
                findings.append(entry)
        for ne in frag.get("not_evaluated", []):
            all_reported.add(ne["check_id"])
            not_evaluated.append({"check_id": ne["check_id"], "reason": ne["reason"]})
        for opp in frag.get("opportunities", []):
            opportunities.append(opp)

    # Catalog resolution lint: no invented check ids anywhere.
    used_ids = set(all_reported)
    for f in findings:
        used_ids.add(f["check_id"])
    for ne in not_evaluated:
        used_ids.add(ne["check_id"])

    # Unknown check ids are a build bug, but never a reason to zero the audit:
    # drop affected results to not_evaluated and warn.
    unknown_ids = {f["check_id"] for f in findings} - set(checks_by_id)
    if unknown_ids:
        kept = []
        for f in findings:
            if f["check_id"] in unknown_ids:
                not_evaluated.append({"check_id": f["check_id"],
                                      "reason": "check_id not in check_catalog.json; result dropped"})
            else:
                kept.append(f)
        findings = kept
        for u in sorted(unknown_ids):
            lint_warnings.append("unknown check_id %s dropped to not_evaluated" % u)
    # A cited-id token needs a 3-letter prefix AND a >=3-char tail ("UTF-8" is
    # prose; real ids like ZZZ-NOTREAL cited in evidence are hallucination signals).
    pair_pat = re.compile(r"\b[A-Z]{3}-[A-Z0-9][A-Z0-9-]{2,}\b")
    for f in findings:
        for cid in sorted(set(pair_pat.findall(f.get("evidence", ""))) - {f["check_id"]}):
            if cid not in checks_by_id:
                lint_warnings.append("%s cites unknown check_id %s in evidence"
                                     % (f["check_id"], cid))
    unknown = sorted(used_ids - set(checks_by_id))
    if unknown:
        limitations_extra.append("Unknown check ids were reported and dropped: %s." % ", ".join(unknown))

    # not_evaluated dedupe: a check may arrive via both a gate result and the
    # fragment's not_evaluated array - one entry per check_id (first wins).
    seen_ne = set()
    deduped_ne = []
    for ne in not_evaluated:
        if ne["check_id"] in seen_ne:
            continue
        seen_ne.add(ne["check_id"])
        deduped_ne.append(ne)
    not_evaluated = deduped_ne

    # A check decided in results must not linger in not_evaluated: results win.
    decided = {f["check_id"] for f in findings}
    kept_ne = []
    for ne in not_evaluated:
        if ne["check_id"] in decided:
            lint_warnings.append("%s also listed in not_evaluated; results verdict kept"
                                 % ne["check_id"])
            continue
        kept_ne.append(ne)
    not_evaluated = kept_ne

    # Coverage backfill: every catalog check no fragment reported lands in
    # not_evaluated, so the report's coverage claim is complete and honest.
    for c in catalog["checks"]:
        if c["check_id"] not in used_ids:
            not_evaluated.append({"check_id": c["check_id"],
                                  "reason": "no result reported by %s" % c["skill_id"]})

    # Root-cause dedup (Phase 2 scope): merge exact duplicates, then one
    # documented correlation cluster - client-shell symptoms share one mechanism.
    # known: single hardcoded correlation; generalize via catalog fields if more emerge
    seen_exact = set()
    deduped = []
    for f in findings:
        key = (f["check_id"], tuple(sorted(f.get("affected_urls", []))))
        if key in seen_exact:
            continue
        seen_exact.add(key)
        deduped.append(f)
    findings = deduped
    primary = next((f for f in findings if f["check_id"] == "REP-KEY-FACT-LOSS"), None)
    if primary:
        absorbed = [f for f in findings
                    if f["check_id"] in ("REP-LINKS-SCRIPT-ONLY", "REP-EXTRACTION-LOSS")
                    and set(f.get("affected_urls", [])) & set(primary.get("affected_urls", []))]
        if absorbed:
            urls = sorted({u for f in absorbed for u in f.get("affected_urls", [])})
            primary["evidence"] += (" Correlated same-root-cause symptoms: %s on %s."
                                    % (", ".join(sorted({f["check_id"] for f in absorbed})),
                                       ", ".join(urls) if urls else "the same pages"))
            findings = [f for f in findings if f not in absorbed]

    # Severity-then-check_id stable sort, then id assignment (orchestrator-only duty).
    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], f["check_id"]))
    ordered = []
    for i, f in enumerate(findings, 1):
        rec = {"id": "F-%03d" % i, "check_id": f["check_id"]}
        cat = checks_by_id[f["check_id"]].get("category")
        if cat:
            rec["category"] = cat
        rec.update(f)
        ordered.append(rec)

    # Reporting gate rule 1, enforced by normalization instead of refusal: a
    # finding that claims critical without high confidence is clamped to high
    # and recorded - the audit always produces a report.
    for f in ordered:
        if f["severity"] == "critical" and f.get("confidence") != "high":
            f["severity"] = "high"
            f["suggested_action"]["priority"] = "high" if \
                f["suggested_action"].get("priority") == "critical" else \
                f["suggested_action"].get("priority", "high")
            lint_warnings.append("%s: severity clamped critical->high; confidence=%s does not"
                                 " satisfy severity_model rule 1 (critical requires high)"
                                 % (f["check_id"], f.get("confidence")))

    # Severity-band lint (warn only, never clamp): a judgment may weigh severity
    # within the catalog's band; landing outside it means the check routing or the
    # severity call drifted from the catalog - surface it, do not silently pass it.
    for f in ordered:
        band = checks_by_id[f["check_id"]].get("severity_band") or []
        if band and f["severity"] not in band:
            lint_warnings.append("%s: severity %s outside catalog severity_band %s "
                                 "(warning only - re-check routing and severity)"
                                 % (f["check_id"], f["severity"], band))

    # Opportunities: merge, dedupe by title (stable order), never counted as findings.
    seen_titles = set()
    merged_opps = []
    for opp in opportunities:
        key = opp["title"].strip().lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        merged_opps.append(opp)
    merged_opps.sort(key=lambda o: ({"critical": 0, "high": 1, "medium": 2, "low": 3}
                                    .get(o["priority"], 4), o["title"]))
    opportunities = merged_opps

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in ordered:
        counts[f["severity"]] += 1
    summary = {
        "total_findings": len(ordered),
        "critical": counts["critical"],
        "high": counts["high"],
        "medium": counts["medium"],
        "low": counts["low"],
        "opportunities": len(opportunities),
    }
    if summary["total_findings"] != len(ordered):
        fail("arithmetic guard", ["total_findings != len(findings)"])

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    coverage = {
        "specialists_resolved": 0 if args.degraded else len(skill_ids),
        "specialists_requested": len({c.get("skill_id") for c in catalog.get("checks", [])}),
        "capabilities_unavailable": [],
        "checks_passed_count": len(set(checks_passed)),
        "checks_passed": sorted(set(checks_passed)),
    }
    snapshot_present = bool(args.snapshot and os.path.exists(args.snapshot))
    pages_selected = 0
    snapshot_started = None
    if snapshot_present:
        snap = load_json(args.snapshot)
        coverage["pages_discovered"] = snap["discovery"]["candidates_count"]
        coverage["pages_selected"] = len(snap["discovery"]["selected"])
        pages_selected = coverage["pages_selected"]
        coverage["raw_fetches_succeeded"] = len(snap["pages"])
        coverage["rendered_pages"] = 0
        caps = snap.get("capabilities", {})
        browser_ok = bool(caps.get("browser", False))
        coverage["browser_available"] = browser_ok
        if not browser_ok:
            coverage["capabilities_unavailable"].append("browser")
        # collection_started_at is when fetching began; audited_at is when it
        # ended. Anchoring on audited_at reports ~0s for any scripted run.
        snapshot_started = _parse_started_at(
            snap.get("collection_started_at") or snap.get("audited_at"))

    # Runtime is a reported property of the audit, not a claim in the README.
    # The schema has carried coverage.time_seconds since v1.0 and nothing wrote
    # it, so a run that overran said nothing about overrunning. It does now.
    budget = _budget_record(args)
    started_at = budget["started_at"] or snapshot_started
    coverage["budget_seconds"] = budget["budget_seconds"]
    coverage["shed"] = budget["shed"]
    if started_at is None:
        coverage["time_seconds"] = None
        coverage["budget_exceeded"] = False
    else:
        coverage["time_seconds"] = max(0, int(
            datetime.datetime.now(datetime.timezone.utc).timestamp() - started_at))
        coverage["budget_exceeded"] = coverage["time_seconds"] > budget["budget_seconds"]
    limitations = (["Single-skill degraded mode: no specialist fragments were available;"
                    " specialists_resolved = 0 and the judgment checks are not_evaluated."]
                   if args.degraded else [])
    if not fragments_loaded and not args.degraded:
        limitations.append("No specialist fragments were loaded; every check is "
                           "not_evaluated by backfill, not by judgment.")
    no_capture = (snapshot_present and pages_selected == 0) or (args.degraded and not snapshot_present)
    if no_capture:
        limitations.append("No pages were captured (unreachable site or total capture failure); "
                           "runnable checks are not_evaluated and there is nothing to find.")
    limitations.append("The opportunities[] proactive set maps to the remediation playbook "
                       "(references/remediation_playbook.json).")
    if coverage["time_seconds"] is None:
        limitations.append("Runtime was not measured for this run (no audit start timestamp "
                           "was available); coverage.time_seconds is null rather than guessed.")
    elif coverage["budget_exceeded"]:
        limitations.append(
            "This audit took %ds against a %ds budget. The report is complete but the run "
            "overran; treat the runtime as measured, not as the marketplace's target."
            % (coverage["time_seconds"], coverage["budget_seconds"]))
    for row in coverage["shed"]:
        limitations.append("Shed at %ds to stay inside the %ds budget: %s%s"
                           % (row["at_seconds"], coverage["budget_seconds"], row["what"],
                              (" (%s)" % row["reason"]) if row.get("reason") else ""))
    report = {
        "site": args.site,
        "audited_at": now,
        # Shedding IS a deadline hit, and the schema already defines partial that
        # way. Before this, a run could drop every off-site probe and still call
        # itself complete, which is the one thing this marketplace promises not
        # to do. Overrunning does not make the report partial - nothing was
        # dropped - it makes it late, which limitations[] records.
        "audit_status": ("partial" if (no_capture or coverage["shed"]) else "complete"),
        "report_schema_version": "1.0",
        "marketplace_version": args.marketplace_version,
        "coverage": coverage,
        "summary": summary,
        "findings": ordered,
        "opportunities": opportunities,
        "needs_verification": needs_verification,
        "not_evaluated": not_evaluated,
        "limitations": limitations,
        "lint_warnings": [],
        "human_summary": _human_summary(args.site, ordered, needs_verification,
                                        not_evaluated),
    }
    report["lint_warnings"] = lint_warnings + lint_report(report)

    errs = validate(report, out_schema)
    if errs:
        fail("report does not validate against output_schema.json", errs)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    md_path = os.path.splitext(args.out)[0] + ".md"
    try:
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(_report_markdown(report))
        print("build_report: %s written" % md_path)
    except OSError as e:  # noqa: BLE001 - the .md is additive; never fail the report over it
        print("build_report: note - report.md not written (%s)" % e)
    print("build_report: report written to %s (%d findings, %d needs_verification,"
          " %d not_evaluated)" % (args.out, len(ordered), len(needs_verification),
                                  len(not_evaluated)))
    # Everything step 7 must state in chat, printed here. Without audit_status and
    # coverage on stdout the emit step had to reopen report.json for one field,
    # against its own instruction not to.
    def _n(v):
        return "-" if v is None else v
    print("build_report: audit_status=%s | specialists %s/%s | pages %s/%s sampled"
          " | checks passed %s | severity %dC/%dH/%dM/%dL | opportunities %d"
          % (report["audit_status"], _n(coverage.get("specialists_resolved")),
             _n(coverage.get("specialists_requested")), _n(coverage.get("pages_selected")),
             _n(coverage.get("pages_discovered")), _n(coverage.get("checks_passed_count")),
             summary["critical"], summary["high"], summary["medium"], summary["low"],
             len(opportunities)))
    # The runtime line the emit step reads out loud, so step 7 never reopens
    # report.json to find out how long its own audit took.
    print("build_report: RUNTIME %s/%ds%s | shed: %s"
          % ("%ds" % coverage["time_seconds"] if coverage["time_seconds"] is not None
             else "not measured",
             coverage["budget_seconds"],
             " OVER BUDGET" if coverage["budget_exceeded"] else "",
             ", ".join("%s@%ds" % (r["what"], r["at_seconds"]) for r in coverage["shed"])
             or "nothing"))


FORBIDDEN_PATTERNS = [
    (r"\bFAQPage\b", "recommends FAQPage schema (retired for rich results; plain-HTML Q&A is the fix)"),
    (r"\bHowTo\b", "recommends HowTo schema (retired)"),
    (r"llms\.txt.{0,80}(rank|discover|citation|visibility)", "frames llms.txt as a discoverability/ranking factor"),
    (r"\b(CLS|LCP|INP)\b[^.]{0,40}\d", "reads like a synthesized Core Web Vital"),
    (r"\bguarantee", "states a guarantee"),
    (r"\bwill (rank|be cited|appear)", "predicts a specific ranking/citation outcome"),
    (r"\bIndexNow\b", "IndexNow receipt does not guarantee indexing or citation"),
]


IDENTIFIER_PATTERNS = [
    (r"is_soft_404", "snapshot field path leaked into evidence (state the observation in words)"),
    (r"snapshot\s*\.\s*[a-z_]+", "snapshot field path leaked into evidence (state the observation in words)"),
]


def lint_report(report):
    """Never-claim lint: WARN only, never fails - quoted evidence or the site's
    own prose can legitimately contain trigger strings (PHASE1 fix, build review)."""
    warnings = []
    fields = [("finding.title", f["title"]) for f in report["findings"]]
    fields += [("finding.suggested_action", f["suggested_action"]["summary"])
               for f in report["findings"]]
    fields += [("opportunity.title", o["title"]) for o in report.get("opportunities", [])]
    fields += [("human_summary", report.get("human_summary", ""))]
    for field, text in fields:
        for pattern, why in FORBIDDEN_PATTERNS:
            if re.search(pattern, text, re.I):
                warnings.append("%s: %s (warning only - verify in context)" % (field, why))
    for f in report["findings"]:
        for pattern, why in IDENTIFIER_PATTERNS:
            if re.search(pattern, f.get("evidence", "")):
                warnings.append("finding.evidence: %s (warning only - verify in context)" % why)
    return warnings


def _report_markdown(report):
    """Plain-English rendering of the report - additive artifact alongside report.json."""
    cov = report.get("coverage", {})
    lines = [
        "# AI-retrieval readiness audit — %s" % report.get("site", ""),
        "",
        "_Audited %s · %s · %d pages sampled of %d discovered · %d finding(s)_" % (
            report.get("audited_at", ""), report.get("audit_status", ""),
            cov.get("pages_selected", 0), cov.get("pages_discovered", 0),
            report.get("summary", {}).get("total_findings", 0)),
        "",
        "_Runtime %s against a %ds budget%s._" % (
            ("%ds" % cov["time_seconds"]) if cov.get("time_seconds") is not None
            else "not measured",
            cov.get("budget_seconds", 0),
            " — **over budget**" if cov.get("budget_exceeded") else ""),
        "",
    ]
    if cov.get("shed"):
        lines.append("**Shed to stay inside the budget:** " + "; ".join(
            "%s at %ds%s" % (r["what"], r["at_seconds"],
                             (" — %s" % r["reason"]) if r.get("reason") else "")
            for r in cov["shed"]))
        lines.append("")
    summary = report.get("summary", {})
    lines.append("**Severity:** %d critical · %d high · %d medium · %d low" % (
        summary.get("critical", 0), summary.get("high", 0),
        summary.get("medium", 0), summary.get("low", 0)))
    lines.append("")
    lines.append(report.get("human_summary", ""))

    # Findings in full. The model used to re-compose this from report.json at the
    # end of every run, which is one large authoring turn spent restating fields
    # the merge already holds. Rendering them here leaves the model a short spoken
    # summary to write, not a document.
    findings = report.get("findings", [])
    if findings:
        lines.append("")
        lines.append("## Findings, in fix order")
        for f in findings:
            lines.append("")
            lines.append("### %s %s" % (f.get("id", ""), f.get("title", "")))
            lines.append("**Severity: %s · Confidence: %s · Check: `%s`**" % (
                (f.get("severity") or "").upper(), f.get("confidence", ""),
                f.get("check_id", "")))
            if f.get("evidence"):
                lines.append("")
                lines.append("**Evidence.** %s" % f["evidence"])
            if f.get("why_it_matters"):
                lines.append("")
                lines.append("**Why it matters.** %s" % f["why_it_matters"])
            act = f.get("suggested_action") or {}
            if act.get("summary"):
                bits = [b for b in ("priority: %s" % act["priority"] if act.get("priority") else "",
                                    "effort: %s" % act["effort"] if act.get("effort") else "",
                                    "owner: %s" % act["owner"] if act.get("owner") else "") if b]
                lines.append("")
                lines.append("**Fix.** %s%s" % (act["summary"],
                                                (" (%s)" % ", ".join(bits)) if bits else ""))
            if act.get("acceptance_test"):
                lines.append("")
                lines.append("**Verify.** %s" % act["acceptance_test"])
            urls = f.get("affected_urls") or []
            if urls:
                lines.append("")
                lines.append("**Affected (%d):** %s%s" % (
                    len(urls), ", ".join(urls[:5]),
                    " …" if len(urls) > 5 else ""))
    passed = cov.get("checks_passed") or []
    if passed:
        lines.append("")
        lines.append("## What passed (%d checks run and clean)" % len(passed))
        lines.append(", ".join("`%s`" % c for c in passed))
    opps = report.get("opportunities", [])
    if opps:
        lines.append("")
        lines.append("## Opportunities (unmet demand, not defects)")
        for o in opps:
            lines.append("- **%s** — %s" % (o.get("title", ""), o.get("rationale", "")))
    nv = report.get("needs_verification", [])
    if nv:
        lines.append("")
        lines.append("## Needs verification (not defects yet)")
        for n in nv:
            lines.append("- %s" % (n.get("title") or n.get("check_id") or json.dumps(n)[:120]))
    ne = report.get("not_evaluated", [])
    if ne:
        lines.append("")
        lines.append("## Not evaluated (%d) — never defects" % len(ne))
        for n in ne:
            lines.append("- %s — %s" % (n.get("check_id", ""), n.get("reason", "")))
    if report.get("lint_warnings"):
        lines.append("")
        lines.append("## Lint warnings")
        for w in report["lint_warnings"]:
            lines.append("- %s" % w)
    return "\n".join(lines) + "\n"


def _human_summary(site, findings, needs_verification, not_evaluated):
    lines = ["Audit of %s: %d finding(s). Fix in this order:" % (site, len(findings))]
    for i, f in enumerate(findings, 1):
        action = f["suggested_action"]
        bits = []
        if action.get("effort"):
            bits.append("effort: %s" % action["effort"])
        if action.get("owner"):
            bits.append("owner: %s" % action["owner"])
        lines.append("%d. [%s] %s" % (i, f["severity"].upper(), f["title"]))
        lines.append("   Fix: %s%s" % (action["summary"],
                                       (" (%s)" % ", ".join(bits)) if bits else ""))
        if action.get("acceptance_test"):
            lines.append("   Verify: %s" % action["acceptance_test"])
    if needs_verification:
        lines.append("%d item(s) need verification before they can be called defects."
                     % len(needs_verification))
    if not_evaluated:
        lines.append("%d check(s) were not evaluated - reasons are in the report;"
                     " 'not evaluated' is not a defect." % len(not_evaluated))
    return "\n".join(lines)


if __name__ == "__main__":
    main()
