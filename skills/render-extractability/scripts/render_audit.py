# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "beautifulsoup4>=4.12",
# ]
# ///
"""
render_audit.py -- L2 readability: can a crawler actually read the content?

Reads the shared bundle (no re-fetch). Everything here works from RAW (non-JS)
HTML, so it needs no headless browser:
  * raw-text floor / JS-shell            (L2-01)   <- absolute floor, NOT a ratio
  * facts locked in images               (L2-03)
  * facts only in PDF                     (L2-04)
  * media with no transcript             (L2-05)
  * no topical H1 / no <main>            (L2-08)   <- only ZERO h1, never >1
  * substantive cross-origin iframe      (L2-09)

The true raw-vs-rendered diff (L2-02) needs a rendered DOM. If a rendered HTML
file is supplied (--rendered file --url u) it is diffed; otherwise L2-02 is
reported as SKIPPED with a reason -- never silently passed. That is the
degrade-gracefully rule, and it keeps the skill portable and inside the budget.

Self-contained (PEP 723). Run:
    uv run scripts/render_audit.py <bundle_dir>
    uv run scripts/render_audit.py <bundle_dir> --rendered rendered.html --url https://x.com/p
"""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urlparse

from bs4 import BeautifulSoup

RAW_TEXT_FLOOR = 500          # chars; below this a "content" page is a JS shell
IMG_FACT_MIN_BYTES_HINT = 0   # we can't see bytes from HTML; use size attrs/role
PRICE_RE = re.compile(r"(?:[$£€₹]|USD|EUR|GBP|INR|Rs\.?)\s?\d[\d,]*(?:\.\d{2})?", re.I)
CONSENT_RE = re.compile(r"\b(cookie|consent|gdpr|accept all|privacy preferences|age verification)\b", re.I)


def _f(cid, layer, title, sev, conf, evidence, urls, fix, priority=None):
    return {"check_id": cid, "layer": layer, "title": title, "raw_severity": sev,
            "confidence": conf, "evidence": evidence, "affected_urls": urls,
            "suggested_action": {"summary": fix, "priority": priority or sev}}


def text_of(soup):
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    return "\n".join(s for s in soup.stripped_strings)


def looks_like_content_page(url):
    """Homepages, product, pricing, about, docs pages should carry real text.
    Utility endpoints (manifest, feeds) are exempt from the floor check."""
    low = url.lower()
    if any(x in low for x in (".webmanifest", ".xml", "/feed", "/rss", "/api/")):
        return False
    return True


def chunks(text):
    return {c.strip() for c in re.split(r"[\n.]{1,}", text) if len(c.strip()) >= 15}


def analyze(url, raw_html, rendered_html=None):
    findings, passed, skipped = [], [], []
    raw_soup = BeautifulSoup(raw_html, "html.parser")
    body = raw_soup.find("main") or raw_soup.find("body") or raw_soup
    raw_text = text_of(BeautifulSoup(raw_html, "html.parser"))

    # L2-01 raw-text floor (absolute, not a ratio)
    if looks_like_content_page(url):
        if len(raw_text) < RAW_TEXT_FLOOR:
            # Is it consent-dominated? -> L2-07 instead
            if CONSENT_RE.search(raw_text) and len(raw_text) < 300:
                findings.append(_f("L2-07", "L2", "Consent/age gate replaces page content",
                                   "critical", "confirmed",
                                   f"{url}: raw HTML yields {len(raw_text)} chars, dominated by consent/age text.",
                                   [url], "Serve content in the initial response; make consent an overlay, not a gate."))
            else:
                findings.append(_f("L2-01", "L2", "Raw HTML is effectively empty (JS shell)",
                                   "critical", "confirmed",
                                   f"{url}: raw (non-JS) HTML yields only {len(raw_text)} chars of extractable text. A non-rendering fetcher sees almost nothing.",
                                   [url], "Server-side render or pre-render the fact-bearing content into the initial HTML."))
        else:
            passed.append("L2-01")

    # L2-08 no topical H1 / no <main> (ONLY zero h1, never >1)
    h1s = raw_soup.find_all("h1")
    if len(h1s) == 0:
        findings.append(_f("L2-08", "L2", "No H1 heading to anchor the page topic",
                           "medium", "confirmed",
                           f"{url}: 0 <h1> elements. Extractors use headings to locate the answer in a long page.",
                           [url], "Add one descriptive <h1> stating the page's subject."))
    else:
        passed.append("L2-08")

    # L2-03 facts locked in images: a price visible only inside an <img> region
    text_has_price = bool(PRICE_RE.search(raw_text))
    imgs = body.find_all("img") if hasattr(body, "find_all") else []
    pricey_img = None
    for im in imgs:
        alt = (im.get("alt") or "").lower()
        src = (im.get("src") or "").lower()
        if any(k in alt + src for k in ("price", "pricing", "plan", "cost", "rate")):
            pricey_img = im.get("src")
            break
    if pricey_img and not text_has_price and looks_like_content_page(url) and "/pricing" in url.lower():
        findings.append(_f("L2-03", "L2", "Price appears only inside an image",
                           "high", "probable",
                           f"{url}: a pricing-related image ({pricey_img}) is present but no price text was found on the page.",
                           [url], "State the price in visible body text next to the image; alt text alone is insufficient."))

    # L2-04 fact only in PDF (PDF link in main, no equivalent text nearby)
    pdfs = [a.get("href") for a in (body.find_all("a", href=True) if hasattr(body, "find_all") else [])
            if a.get("href", "").lower().endswith(".pdf")]
    if pdfs and len(raw_text) < RAW_TEXT_FLOOR * 2:
        findings.append(_f("L2-04", "L2", "Key content may live only in a PDF",
                           "medium", "probable",
                           f"{url}: {len(pdfs)} PDF link(s) in main content with little surrounding HTML text.",
                           [url], "Publish an HTML equivalent of the PDF's key facts; keep the PDF as a download."))

    # L2-05 media with no transcript
    media = (body.find_all(["video", "audio"]) if hasattr(body, "find_all") else [])
    iframes_media = [i for i in (body.find_all("iframe") if hasattr(body, "find_all") else [])
                     if any(v in (i.get("src") or "").lower() for v in ("youtube", "vimeo", "wistia"))]
    if (media or iframes_media) and not re.search(r"transcript|captions?", raw_text, re.I):
        findings.append(_f("L2-05", "L2", "Video/audio has no text transcript",
                           "medium", "probable",
                           f"{url}: media embed(s) present with no transcript/caption text on the page.",
                           [url], "Publish a transcript or text summary on the same page."))

    # L2-09 substantive cross-origin iframe
    host = urlparse(url).netloc
    for i in (body.find_all("iframe") if hasattr(body, "find_all") else []):
        src = i.get("src") or ""
        if src.startswith("http") and urlparse(src).netloc and urlparse(src).netloc != host:
            if not any(v in src.lower() for v in ("youtube", "vimeo", "maps", "recaptcha", "wistia")):
                findings.append(_f("L2-09", "L2", "Content isolated in a cross-origin iframe",
                                   "medium", "probable",
                                   f"{url}: substantive iframe from {urlparse(src).netloc}; its content is attributed to the other origin.",
                                   [url], "Mirror the key facts as native text on the host page."))
                break

    # L2-02 raw-vs-rendered diff (only if rendered supplied)
    if rendered_html is not None:
        rend_text = text_of(BeautifulSoup(rendered_html, "html.parser"))
        raw_c, rend_c = chunks(raw_text), chunks(rend_text)
        js_only = rend_c - raw_c
        ratio = round(len(js_only) / max(len(rend_c), 1), 3)
        sample = sorted(js_only, key=len, reverse=True)[:8]
        fact_in_gap = any(PRICE_RE.search(s) for s in sample)
        if ratio >= 0.5 or fact_in_gap:
            sev = "high" if fact_in_gap else "medium"
            findings.append(_f("L2-02", "L2", "Substantive content only appears after JavaScript",
                               sev, "confirmed",
                               f"{url}: {int(ratio*100)}% of rendered text chunks are absent from raw HTML"
                               + (" and the gap includes a price/fact." if fact_in_gap else "."),
                               [url], "SSR or pre-render the specific missing content."))
        elif ratio >= 0.05:
            findings.append(_f("L2-02", "L2", "Minor JS-only content", "low", "confirmed",
                               f"{url}: {int(ratio*100)}% of text chunks are JS-only (no key facts detected in the gap).",
                               [url], "Confirm the JS-only chunks are non-essential UI text."))
        else:
            passed.append("L2-02")
    else:
        skipped.append({"check_id": "L2-02",
                        "reason": "no rendered DOM supplied; raw-vs-rendered diff not run. Provide --rendered to enable, or rely on L2-01 JS-shell floor."})

    return findings, passed, skipped


def load_bundle_pages(bundle_dir):
    idx = json.load(open(os.path.join(bundle_dir, "index.json"), encoding="utf-8"))
    for p in idx["pages"]:
        if not p.get("slug"):
            continue
        rec = json.load(open(os.path.join(bundle_dir, "pages", p["slug"] + ".json"), encoding="utf-8"))
        if rec.get("ok") and rec.get("raw_html"):
            yield rec["url"], rec["raw_html"]


def main():
    args = sys.argv[1:]
    rendered_map = {}
    if "--rendered" in args:
        rf = open(args[args.index("--rendered") + 1], encoding="utf-8").read()
        ru = args[args.index("--url") + 1] if "--url" in args else None
        rendered_map[ru] = rf
    if not args or args[0].startswith("--"):
        print("Usage: uv run render_audit.py <bundle_dir> [--rendered f --url u]", file=sys.stderr)
        sys.exit(1)

    all_f, all_p, all_s = [], set(), []
    for url, html in load_bundle_pages(args[0]):
        f, p, s = analyze(url, html, rendered_map.get(url))
        all_f += f
        all_p |= set(p)
        all_s += s
    print(json.dumps({"layer": "L2", "findings": all_f,
                      "checks_passed": sorted(all_p), "checks_skipped": all_s}, indent=2))


if __name__ == "__main__":
    main()
