# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "beautifulsoup4>=4.12",
# ]
# ///
"""
schema_audit.py -- L3 extractability: can a machine pick out the specific fact?

Reads the shared bundle produced by the orchestrator's fetch_bundle.py (no
re-fetching). For each page it checks:
  * JSON-LD presence vs page type            (L3-01)
  * JSON validity                            (L3-02)
  * completeness, resolving @graph and @id   (L3-03)  <- avoids false positives
  * schema-vs-DOM agreement on price/name    (L3-04)  <- highest-value check
  * a quotable definitional sentence         (L3-07)
  * title / meta description / canonical / OG (L3-05/06/09)

Self-contained (PEP 723). Run:
    uv run scripts/schema_audit.py <bundle_dir>
    uv run scripts/schema_audit.py --html page.html --url https://x.com   (single page)

Output: JSON { findings: [...], checks_passed: [...] } in the shared finding shape
(no id, no final severity -- the orchestrator assigns both). The script MEASURES;
the SKILL.md's guard clauses decide what actually becomes a finding.
"""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urlparse

from bs4 import BeautifulSoup

EXPECTED = {
    "Product": ["name", "offers"],
    "Offer": ["price", "priceCurrency"],
    "Organization": ["name", "url"],
    "LocalBusiness": ["name", "address"],
    "Article": ["headline", "datePublished"],
    "NewsArticle": ["headline", "datePublished"],
    "FAQPage": ["mainEntity"],
    "BreadcrumbList": ["itemListElement"],
    "WebSite": ["name", "url"],
    "Person": ["name"],
}
# Page-purpose hints -> the schema type that page type warrants.
PURPOSE_TYPE = [
    (("/dp/", "/product", "/products", "/shop", "/item"), "Product"),
    (("/blog", "/news", "/article", "/post"), "Article"),
    (("/pricing", "/plans"), "Product"),
]
PRICE_RE = re.compile(r"(?:[$£€₹]|USD|EUR|GBP|INR|Rs\.?)\s?\d[\d,]*(?:\.\d{2})?", re.I)


def flatten(node):
    """Yield every dict in a JSON-LD tree, following @graph."""
    if isinstance(node, list):
        for x in node:
            yield from flatten(x)
    elif isinstance(node, dict):
        if isinstance(node.get("@graph"), list):
            for x in node["@graph"]:
                yield from flatten(x)
        yield node


def type_of(obj):
    t = obj.get("@type")
    if isinstance(t, list):
        return t[0] if t else None
    return t


def resolve_missing(obj, all_objs_by_id):
    """Return expected fields missing from obj, resolving @id references and
    nested objects so we don't flag a field that lives elsewhere in the graph."""
    t = type_of(obj)
    if t not in EXPECTED:
        return None
    missing = []
    for field in EXPECTED[t]:
        val = obj.get(field)
        if val in (None, "", [], {}):
            missing.append(field)
        elif isinstance(val, dict) and "@id" in val and len(val) == 1:
            # reference only -> resolve; if target absent, still a gap
            if val["@id"] not in all_objs_by_id:
                missing.append(field)
    return {"type": t, "missing": missing}


def dom_price(soup):
    for text in soup.stripped_strings:
        m = PRICE_RE.search(text)
        if m:
            return m.group(0)
    return None


def jsonld_price(objs):
    for o in objs:
        offers = o.get("offers")
        for off in flatten(offers) if offers else []:
            p = off.get("price") or off.get("lowPrice")
            if p not in (None, ""):
                return str(p)
    return None


def definitional_sentence(text, brand):
    """Look for '<Brand> is a/an <something>' in the first ~1500 chars."""
    head = text[:1500]
    pat = re.compile(rf"{re.escape(brand)}\s+is\s+(?:a|an|the)\s+.{{6,}}", re.I)
    m = pat.search(head)
    return m.group(0)[:160] if m else None


def analyze_page(url, html):
    soup = BeautifulSoup(html, "html.parser")
    text = "\n".join(s for s in soup.stripped_strings)
    findings = []
    passed = []

    # --- extract + validate JSON-LD ---
    blocks = soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)})
    parsed_objs, malformed = [], 0
    for b in blocks:
        raw = b.string or b.get_text() or ""
        try:
            parsed_objs.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            malformed += 1
    objs = [o for tree in parsed_objs for o in flatten(tree)]
    types = sorted({type_of(o) for o in objs if type_of(o)})
    by_id = {o["@id"]: o for o in objs if isinstance(o.get("@id"), str)}

    # L3-02 malformed
    if malformed:
        findings.append(_f("L3-02", "L3", "Malformed JSON-LD is silently ignored",
                           "high" if not objs else "medium", "confirmed",
                           f"{malformed} of {len(blocks)} JSON-LD block(s) on {url} fail to parse.",
                           [url], "Fix the invalid JSON; validate in CI, not by eye."))
    else:
        passed.append("L3-02")

    # L3-01 missing structured data on a page type that warrants it
    warranted = None
    low = url.lower()
    for hints, typ in PURPOSE_TYPE:
        if any(h in low for h in hints):
            warranted = typ
            break
    if warranted and warranted not in types:
        findings.append(_f("L3-01", "L3", f"No {warranted} structured data on a {warranted.lower()} page",
                           "high" if warranted == "Product" else "medium", "confirmed",
                           f"{url} looks like a {warranted.lower()} page but has no schema.org {warranted} JSON-LD (types found: {types or 'none'}).",
                           [url], f"Add {warranted} JSON-LD with at minimum: {', '.join(EXPECTED.get(warranted, []))}."))
    elif warranted:
        passed.append("L3-01")

    # L3-03 completeness (with @graph/@id resolution)
    for o in objs:
        r = resolve_missing(o, by_id)
        if r and r["missing"]:
            findings.append(_f("L3-03", "L3", f"Incomplete {r['type']} structured data",
                               "medium", "confirmed",
                               f"{r['type']} on {url} is missing: {', '.join(r['missing'])}.",
                               [url], f"Populate {', '.join(r['missing'])} on the {r['type']} block."))

    # L3-04 schema-vs-DOM price disagreement (highest value)
    jp, dp = jsonld_price(objs), dom_price(soup)
    if jp and dp:
        jn = re.sub(r"[^\d.]", "", jp)
        dn = re.sub(r"[^\d.]", "", dp)
        if jn and dn and jn != dn:
            findings.append(_f("L3-04", "L3", "Structured-data price disagrees with visible price",
                               "high", "confirmed",
                               f"{url}: JSON-LD price '{jp}' vs visible price '{dp}'. A machine may quote the wrong value.",
                               [url], "Generate markup from the same source that renders the page."))
        else:
            passed.append("L3-04")

    # L3-07 quotable definitional sentence (homepage / about only)
    if urlparse(url).path in ("", "/") or "/about" in low:
        brand = urlparse(url).netloc.split(".")[-2] if "." in urlparse(url).netloc else urlparse(url).netloc
        if not definitional_sentence(text, brand):
            findings.append(_f("L3-07", "L3", "No quotable one-sentence definition of the brand",
                               "high", "probable",
                               f"No '<Brand> is a/an ...' sentence found in the first 1500 chars of {url}. Assistants have nothing clean to quote.",
                               [url], "Add one plain sentence: '<Brand> is a <category> that <does what> for <whom>' in body text and Organization.description."))
        else:
            passed.append("L3-07")

    # L3-05/06/09 meta signals
    title = soup.title.get_text(strip=True) if soup.title else None
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    canon = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    og = soup.find_all("meta", attrs={"property": re.compile("^og:", re.I)})
    if not md or not (md.get("content") or "").strip():
        findings.append(_f("L3-05", "L3", "Missing meta description",
                           "medium", "confirmed",
                           f"{url} has no meta description; this is the snippet assistants quote before opening the page.",
                           [url], "Add a unique, factual meta description."))
    else:
        passed.append("L3-05")
    if not og:
        findings.append(_f("L3-09", "L3", "No Open Graph metadata", "low", "advisory",
                           f"{url} has no og: tags (advisory; not a defect if description + JSON-LD carry the facts).",
                           [url], "Add og:title, og:description, og:url, og:image."))

    return findings, passed, {"types": types, "jsonld_blocks": len(blocks)}


def _f(cid, layer, title, sev, conf, evidence, urls, fix):
    return {"check_id": cid, "layer": layer, "title": title, "raw_severity": sev,
            "confidence": conf, "evidence": evidence, "affected_urls": urls,
            "suggested_action": {"summary": fix, "priority": sev}}


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
    pages = []
    if "--html" in args:
        html = open(args[args.index("--html") + 1], encoding="utf-8").read()
        url = args[args.index("--url") + 1] if "--url" in args else "http://local/"
        pages = [(url, html)]
    elif args:
        pages = list(load_bundle_pages(args[0]))
    else:
        print("Usage: uv run schema_audit.py <bundle_dir> | --html f --url u", file=sys.stderr)
        sys.exit(1)

    all_findings, all_passed, per_page = [], set(), {}
    for url, html in pages:
        f, p, meta = analyze_page(url, html)
        all_findings += f
        all_passed |= set(p)
        per_page[url] = meta
    print(json.dumps({"layer": "L3", "pages_analyzed": len(pages),
                      "findings": all_findings, "checks_passed": sorted(all_passed),
                      "per_page": per_page}, indent=2))


if __name__ == "__main__":
    main()
