# /// script
# requires-python = ">=3.10"
# dependencies = ["beautifulsoup4>=4.12"]
# ///
"""Engagement (on-site) audit of the AI-referred cold arrival. Reads the shared
bundle. Emits findings (no id/severity)."""
from __future__ import annotations
import json, os, re, sys
from urllib.parse import urlparse
from bs4 import BeautifulSoup

INTERSTITIAL = re.compile(r"\b(subscribe|newsletter|sign up|cookie|consent|accept all)\b", re.I)

def _f(cid, title, sev, conf, ev, urls, fix):
    return {"check_id": cid, "layer": "ENG", "title": title, "raw_severity": sev,
            "confidence": conf, "evidence": ev, "affected_urls": urls,
            "suggested_action": {"summary": fix, "priority": sev}}

def load(bundle):
    idx = json.load(open(os.path.join(bundle, "index.json"), encoding="utf-8"))
    pages = []
    for p in idx["pages"]:
        if p.get("slug"):
            r = json.load(open(os.path.join(bundle, "pages", p["slug"] + ".json"), encoding="utf-8"))
            if r.get("ok") and r.get("raw_html"):
                pages.append((r["url"], r["raw_html"]))
    return idx["base"], pages

def run(bundle):
    base, pages = load(bundle); findings = []; passed = []
    host = urlparse(base).netloc
    no_viewport, no_orient, dead_ends = [], [], []
    for url, html in pages:
        soup = BeautifulSoup(html, "html.parser")
        # E-07 mobile viewport  
        if not soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)}):
            no_viewport.append(url)
        # E-02 orientation on deep pages
        if urlparse(url).path.strip("/"):
            if not (soup.find("nav") or soup.find(attrs={"class": re.compile("breadcrumb", re.I)})):
                no_orient.append(url)
        # E-04 dead ends: no internal links in main
        main = soup.find("main") or soup.find("body") or soup
        internal = [a for a in main.find_all("a", href=True)
                    if urlparse(a["href"]).netloc in ("", host)]
        if len(internal) == 0:
            dead_ends.append(url)
    if no_viewport:
        findings.append(_f("E-07", "Missing mobile viewport meta tag", "high", "confirmed",
                           f"{len(no_viewport)}/{len(pages)} sampled pages have no <meta name=viewport>. Most AI-referred traffic is mobile.",
                           no_viewport[:5], "Add <meta name=viewport content='width=device-width, initial-scale=1'>."))
    else:
        passed.append("E-07")
    if no_orient:
        findings.append(_f("E-02", "No orientation (nav/breadcrumb) on deep pages", "medium", "probable",
                           f"{len(no_orient)}/{len(pages)} deep pages lack a nav or breadcrumb. AI-referred visitors land deep with no context.",
                           no_orient[:5], "Add breadcrumbs and a one-line site id               entity on every page."))
    else:
        passed.append("E-02")
    if dead_ends:
        findings.append(_f("E-04", "Dead-end pages with no internal links", "medium", "probable",
                           f"{len(dead_ends)}/{len(pages)} pages have no outbound internal links in main content — the visit ends there.",
                           dead_ends[:5], "Add contextual related links and a clear next step in main content."))
    else:
        passed.append("E-04")
    return findings, passed

def main():
    if len(sys.argv) < 2:
        print("Usage: uv run engage_audit.py <bundle_dir>", file=sys.stderr); sys.exit(1)
    f, p = run(sys.argv[1])
    print(json.dumps({"layer": "ENG", "findings": f, "checks_passed": p}, indent=2))

if __name__ == "__main__":
    main()
