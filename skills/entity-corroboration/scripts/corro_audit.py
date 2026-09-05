# /// script
# requires-python = ">=3.10"
# dependencies = ["beautifulsoup4>=4.12", "requests>=2.31"]
# ///
"""L4 corroboration & freshness. Reads the shared bundle. Emits findings (no id/severity)."""
from __future__ import annotations
import json, os, re, sys, datetime as dt
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ANCHORS = ("wikipedia.org", "wikidata.org", "linkedin.com", "crunchbase.com", "github.com")
UA = "brand-ai-readiness-audit/1.0"

def _f(cid, title, sev, conf, ev, urls, fix):
    return {"check_id": cid, "layer": "L4", "title": title, "raw_severity": sev,
            "confidence": conf, "evidence": ev, "affected_urls": urls,
            "suggested_action": {"summary": fix, "priority": sev}}

def load(bundle):
    idx = json.load(open(os.path.join(bundle, "index.json"), encoding="utf-8"))
    base = idx["base"]; pages = []
    for p in idx["pages"]:
        if p.get("slug"):
            r = json.load(open(os.path.join(bundle, "pages", p["slug"] + ".json"), encoding="utf-8"))
            if r.get("ok") and r.get("raw_html"):
                pages.append((r["url"], r["raw_html"]))
    return base, pages

def lastmod_spread(base):
    try:
        rt = requests.get(urljoin(base, "/robots.txt"), headers={"User-Agent": UA}, timeout=10).text
        sms = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", rt) or [urljoin(base, "/sitemap.xml")]
        t = requests.get(sms[0], headers={"User-Agent": UA}, timeout=10).text
        lms = re.findall(r"<lastmod>\s*([^<]+?)\s*</lastmod>", t)
        dates = {m.group(0) for x in lms if (m := re.match(r"\d{4}-\d{2}-\d{2}", x.strip()))}
        return len(lms), len(dates)
    except Exception:
        return 0, 0

def run(bundle):
    base, pages = load(bundle); findings = []; passed = []
    allhtml = " ".join(h for _, h in pages)
    # L4-01 identity anchors
    found = [a for a in ANCHORS if a in allhtml] + (["sameAs"] if "sameAs" in allhtml else [])
    if not found:
        findings.append(_f("L4-01", "No external identity anchors (sameAs / Wikipedia / LinkedIn)",
                           "high", "confirmed",
                           "No sameAs property and no outbound links to Wikipedia, Wikidata, LinkedIn, Crunchbase, or GitHub across sampled pages. A claim with no independent echo is fragile.",
                           [base], "Add sameAs to Organization JSON-LD listing the brand's real profiles; pursue a Wikidata entry."))
    else:
        passed.append("L4-01")
    # L4-05 staleness (copyright year)
    years = [int(y) for y in re.findall(r"(?:©|&copy;|Copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d{2})", allhtml)]
    if years and max(years) < dt.date.today().year - 1:
        findings.append(_f("L4-05", "Stale copyright / freshness signal", "medium", "confirmed",
                           f"Newest copyright year found is {max(years)} (current year {dt.date.today().year}). Visibly old content is discounted.",
                           [base], "Refresh or auto-generate the copyright year; date-stamp key content."))
    elif years:
        passed.append("L4-05")
    # L4-06 lastmod credibility
    n, distinct = lastmod_spread(base)
    if n >= 10 and distinct <= 1:
        findings.append(_f("L4-06", "Synthetic sitemap lastmod (no information)", "low", "confirmed",
                           f"Sitemap has {n} lastmod values but only {distinct} distinct — an auto-stamp that carries no freshness signal.",
                           [base], "Emit lastmod from each page's real modification time, or omit it."))
    elif n:
        passed.append("L4-06")
    # L4-08 accountability
    if not re.search(r"/about|/contact", allhtml, re.I):
        findings.append(_f("L4-08", "No About/Contact accountability signals", "medium", "probable",
                           "No About or Contact links found across sampled pages. Unattributed claims are weaker evidence.",
                           [base], "Publish About and Contact pages; add author bylines with markup."))
    else:
        passed.append("L4-08")
    return findings, passed

def main():
    if len(sys.argv) < 2:
        print("Usage: uv run corro_audit.py <bundle_dir>", file=sys.stderr); sys.exit(1)
    f, p = run(sys.argv[1])
    print(json.dumps({"layer": "L4", "findings": f, "checks_passed": p}, indent=2))

if __name__ == "__main__":
    main()
