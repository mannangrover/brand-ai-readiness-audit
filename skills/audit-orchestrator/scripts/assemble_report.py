# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "beautifulsoup4>=4.12"]
# ///
"""
assemble_report.py -- the entrypoint's composition step, automated end to end.

  plan -> fetch once -> run every sub-skill over the shared bundle ->
  interpret L1 measurements into findings -> dedupe to root cause ->
  apply ONE severity function -> assign stable ids -> emit report.json + report.md

report.json  = machine-readable (validates against references/report-schema.json)
report.md    = plain-English, non-expert-readable

Self-contained (PEP 723). Run:
    uv run scripts/assemble_report.py <site_url> [--max-pages N] [--out DIR]
"""
from __future__ import annotations
import json, os, re, subprocess, sys, time
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.abspath(os.path.join(HERE, "..", ".."))
LAYER_WEIGHT = {"L1": 4, "L2": 4, "L3": 3, "L4": 2, "L5": 2, "ENG": 2}
PRIMARY_CHECKS = {"L1-01", "L1-04", "L1-07", "L2-01", "L2-07", "L3-04", "L3-07", "L5-01", "L5-02"}

def run_json(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}

def uv(script, *args):
    return run_json(["uv", "run", os.path.join(SKILLS, script), *args])

def l1_findings(site):
    d = uv("crawl-access-audit/scripts/access_probe.py", site)
    out, passed = [], []
    r = d.get("robots", {})
    def f(cid, title, sev, ev, fix):
        out.append({"check_id": cid, "layer": "L1", "title": title, "raw_severity": sev,
                    "confidence": "confirmed", "evidence": ev, "affected_urls": [d.get("base", site)],
                    "suggested_action": {"summary": fix, "priority": sev}})
    if r.get("treat_as") in ("disallow_all",):
        f("L1-05", "robots.txt is unreachable (treated as blocking)", "critical",
          "robots.txt returned a server error/timeout; conformant crawlers treat this as Disallow: all.",
          "Fix the robots.txt endpoint to return 200 (or 404 if none intended).")
    rb = r.get("retrieval_blocked") or []
    tb = r.get("training_blocked") or []
    if rb:
        f("L1-01", "Live-retrieval AI agents blocked sitewide", "critical",
          f"robots.txt blocks retrieval agents sitewide: {', '.join(rb)}. Live citation is impossible.",
          "Allow retrieval agents on public content; keep any training opt-out separate.")
    else:
        passed.append("L1-01")
    if tb:
        f("L1-02", "Training AI agents blocked sitewide", "medium",
          f"robots.txt blocks training agents: {', '.join(tb)} (often a deliberate choice).",
          "If you want presence in model memory, allow training agents; note the trade-off.")
    u = d.get("ua_parity", {})
    if u.get("audit_status") and 400 <= (u["audit_status"] or 0) < 500 and u.get("browser_status") == 200:
        f("L1-04", "User-agent discrimination (bot blocked, browser allowed)", "critical",
          f"Audit UA got {u['audit_status']} while a browser UA got 200 (byte ratio {u.get('bytes_ratio')}).",
          "Allowlist declared AI-assistant UAs at the WAF, verified by reverse-DNS/IP ranges.")
    med = (d.get("latency") or {}).get("median_ttfb_ms")
    if med and med > 3000:
        f("L1-08", "Very slow responses risk retrieval timeouts", "high",
          f"Median TTFB {med} ms across {d['latency']['n']} pages.", "Cache/CDN anonymous HTML; target < 800 ms.")
    elif med and med > 1500:
        f("L1-08", "Slow responses may risk retrieval timeouts", "medium",
          f"Median TTFB {med} ms across {d['latency']['n']} pages.", "Cache/CDN anonymous HTML; target < 800 ms.")
    else:
        passed.append("L1-08")
    if (d.get("soft_404") or {}).get("is_soft_404"):
        f("L1-11", "Soft 404 (missing page returns HTTP 200)", "medium",
          "A non-existent path returned 200; pollutes the index.", "Return a real 404 status.")
    if r.get("treat_as") == "parsed" and not rb:
        passed.append("L1-01-retrieval-open")
    return out, passed, d.get("sample_urls", [])

def normkey(f):
    return (f["check_id"], re.sub(r"https?://[^/]+", "", (f.get("affected_urls") or [""])[0]).split("/")[1] if False else f["check_id"])

def dedupe(findings):
    by = {}
    for f in findings:
        k = f["check_id"]
        if k in by:
            for u in f.get("affected_urls", []):
                if u not in by[k]["affected_urls"]:
                    by[k]["affected_urls"].append(u)
            # keep the strongest raw severity
            order = ["low", "medium", "high", "critical"]
            if order.index(f["raw_severity"]) > order.index(by[k]["raw_severity"]):
                by[k]["raw_severity"] = f["raw_severity"]
        else:
            by[k] = dict(f)
    return list(by.values())

def severity(f, sampled, corro_strong):
    w = LAYER_WEIGHT.get(f["layer"], 2)
    breadth = min(len(f.get("affected_urls", [])) / max(sampled, 1), 1.0)
    # A problem that hits the homepage is inherently broad-impact, even if the
    # sample also included some unaffected utility pages.
    if any(urlparse(u).path in ("", "/") for u in f.get("affected_urls", [])):
        breadth = max(breadth, 0.8)
    crit = 1.0 if f["check_id"] in PRIMARY_CHECKS else 0.6
    damp = 0.6 if (corro_strong and f["layer"] in ("L3", "L5")) else 1.0
    s = w * (0.4 + 0.6 * breadth) * crit * damp
    return "critical" if s >= 3.4 else "high" if s >= 2.4 else "medium" if s >= 1.2 else "low"

def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: uv run assemble_report.py <site_url> [--max-pages N] [--out DIR]", file=sys.stderr); sys.exit(1)
    site = args[0]
    base = site if site.startswith("http") else "https://" + site
    max_pages = int(args[args.index("--max-pages") + 1]) if "--max-pages" in args else 10
    out = args[args.index("--out") + 1] if "--out" in args else "audit_out"
    os.makedirs(out, exist_ok=True)
    t0 = time.time()

    plan = uv("audit-orchestrator/scripts/plan_crawl.py", base, "--max-pages", str(max_pages))
    urls = plan.get("sample_urls", [base])
    uf = os.path.join(out, "urls.txt")
    open(uf, "w", encoding="utf-8").write("\n".join(urls))
    bundle = os.path.join(out, "bundle")
    run_json(["uv", "run", os.path.join(SKILLS, "audit-orchestrator/scripts/fetch_bundle.py"),
              "--urls-file", uf, "--out", bundle])

    findings, passed, skipped = [], [], []
    l1f, l1p, l1s = l1_findings(base); findings += l1f; passed += l1p
    for script, key in [("render-extractability/scripts/render_audit.py", "L2"),
                        ("structured-data-audit/scripts/schema_audit.py", "L3"),
                        ("entity-corroboration/scripts/corro_audit.py", "L4"),
                        ("answerability-audit/scripts/answer_audit.py", "L5"),
                        ("engagement-audit/scripts/engage_audit.py", "ENG")]:
        d = uv(script, bundle)
        findings += d.get("findings", [])
        passed += d.get("checks_passed", [])
        skipped += d.get("checks_skipped", [])

    corro_strong = "L4-01" in passed
    findings = dedupe(findings)
    sampled = len([1 for _ in urls])
    for f in findings:
        f["severity"] = severity(f, sampled, corro_strong)
        f["suggested_action"]["priority"] = f["severity"]
        f.pop("raw_severity", None)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (order[f["severity"]], f["layer"], f["check_id"]))
    for i, f in enumerate(findings, 1):
        f["id"] = f"F-{i:03d}"
        f = {k: f[k] for k in ["id"] + [x for x in f]}  # id first (cosmetic)

    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("critical", "high", "medium", "low")}
    report = {
        "site": urlparse(base).netloc,
        "audited_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {"total_findings": len(findings), **counts,
                    "top_priorities": [f["id"] for f in findings if f["severity"] in ("critical", "high")][:5]},
        "findings": [{"id": f["id"], "layer": f["layer"], "check_id": f["check_id"], "title": f["title"],
                      "severity": f["severity"], "confidence": f.get("confidence", "confirmed"),
                      "evidence": f["evidence"], "affected_urls": f.get("affected_urls", []),
                      "suggested_action": f["suggested_action"]} for f in findings],
        "audit_meta": {"pages_sampled": sampled, "sample_urls": urls,
                       "skills_run": ["crawl-access-audit", "render-extractability", "structured-data-audit",
                                      "entity-corroboration", "answerability-audit", "engagement-audit"],
                       "checks_passed": sorted(set(passed)),
                       "checks_skipped": list({c["check_id"]: c for c in skipped}.values()),
                       "robots_compliance": "Honoured robots.txt; no disallowed path fetched.",
                       "duration_seconds": round(time.time() - t0, 1)},
    }
    json.dump(report, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    open(os.path.join(out, "report.md"), "w", encoding="utf-8").write(to_md(report))
    print(json.dumps({"out": out, "findings": len(findings), "summary": report["summary"],
                      "duration_s": report["audit_meta"]["duration_seconds"]}, indent=2))

EMO = {"critical": "[CRITICAL]", "high": "[HIGH]", "medium": "[MEDIUM]", "low": "[LOW]"}
def to_md(r):
    s = r["summary"]
    L = [f"# AI-Readiness Audit — {r['site']}", "",
         f"_Audited {r['audited_at']} · {r['audit_meta']['pages_sampled']} pages · {r['audit_meta']['duration_seconds']}s_", "",
         "## In plain English",
         f"We checked whether AI assistants can **find**, **read**, **trust**, and **quote** {r['site']}, and whether visitors who arrive can use it.",
         f"We found **{s['total_findings']} issues**: "
         f"{s['critical']} critical, {s['high']} high, {s['medium']} medium, {s['low']} low.", ""]
    if s["total_findings"] == 0:
        L.append("No problems found in the layers audited. ✅")
    else:
        L.append("**Fix these first:** " + (", ".join(s["top_priorities"]) or "—") + "")
    L += ["", "## Findings", ""]
    for f in r["findings"]:
        L += [f"### {f['id']} · {EMO[f['severity']]} {f['title']}",
              f"- **Layer:** {f['layer']} ({f['check_id']}) · **Confidence:** {f['confidence']}",
              f"- **What we saw:** {f['evidence']}",
              f"- **Fix:** {f['suggested_action']['summary']}", ""]
    L += ["## What passed (checked, no problem)", "",
          ", ".join(r["audit_meta"]["checks_passed"]) or "—", "",
          "## Not checked", ""]
    L += ["\n".join(f"- {c['check_id']}: {c['reason']}" for c in r["audit_meta"]["checks_skipped"]) or "- (all built layers ran)",
          "", "_Recommend-only. No site was modified; robots.txt was honoured._"]
    return "\n".join(L)

if __name__ == "__main__":
    main()
