# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""L5 answerability. Headline check: answer the site's own questions using ONLY
machine-visible (raw, non-JS) text from the shared bundle. Emits findings."""
from __future__ import annotations
import json, os, re, sys
from urllib.parse import urljoin, urlparse
import requests

UA = "brand-ai-readiness-audit/1.0"
PRICE = re.compile(r"(?:[$£€₹]|USD|EUR|GBP|INR|Rs\.?)\s?\d[\d,]*(?:\.\d{2})?|(?:\bfree\b|\bpricing\b|\bper month\b|/mo\b)", re.I)
LOC = re.compile(r"\b(based in|headquarter|located in|[A-Z][a-z]+,\s?[A-Z]{2}\b|United States|United Kingdom|India|London|New York|San Francisco)\b")
CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\bcontact\b|\+?\d[\d ()-]{7,}\d")

def _f(cid, title, sev, conf, ev, urls, fix):
    return {"check_id": cid, "layer": "L5", "title": title, "raw_severity": sev,
            "confidence": conf, "evidence": ev, "affected_urls": urls,
            "suggested_action": {"summary": fix, "priority": sev}}

def load(bundle):
    idx = json.load(open(os.path.join(bundle, "index.json"), encoding="utf-8"))
    base = idx["base"]; txt = []
    for p in idx["pages"]:
        if p.get("slug"):
            r = json.load(open(os.path.join(bundle, "pages", p["slug"] + ".json"), encoding="utf-8"))
            if r.get("ok") and r.get("raw_text"):
                txt.append(r["raw_text"])
    return base, "\n".join(txt)

def run(bundle):
    base, text = load(bundle); findings = []; passed = []
    brand = urlparse(base).netloc.split(".")[-2] if "." in urlparse(base).netloc else urlparse(base).netloc
    head = text[:8000]
    def sent(pat):
        m = re.search(pat, text, re.I)
        return m.group(0)[:120] if m else None
    checks = {
        "what_it_is": sent(rf"{re.escape(brand)}\s+is\s+(?:a|an|the)\s+.{{6,}}"),
        "cost": PRICE.search(text) and PRICE.search(text).group(0),
        "location": LOC.search(text) and LOC.search(text).group(0),
        "contact": bool(CONTACT.search(text)) and "present",
        "differentiation": sent(r"\b(unlike|compared to|vs\.?|why choose|better than)\b"),
    }
    answerable = {k: v for k, v in checks.items() if v}
    n, tot = len(answerable), len(checks)
    if n / tot < 1/3:
        sev = "critical"
    elif n / tot < 2/3:
        sev = "high"
    else:
        sev = None
    unans = [k for k, v in checks.items() if not v]
    if sev:
        findings.append(_f("L5-01", "Site does not answer common questions from machine-visible text",
                           sev, "confirmed",
                           f"Answerable from raw (non-JS) text: {n}/{tot}. Unanswerable: {', '.join(unans)}. "
                           f"(what-it-is: {checks['what_it_is'] or 'NOT FOUND'}).",
                           [base], "For each unanswerable question, add a plain-text answer in the initial HTML on the relevant page."))
    else:
        passed.append("L5-01")
    # L5-02 pricing
    if not PRICE.search(text):
        findings.append(_f("L5-02", "No pricing information in machine-visible text", "high", "probable",
                           "No price, range, or 'free/pricing' text found. 'How much does X cost' is a high-intent question.",
                           [base], "Publish at least a starting price or band in text (skip if genuinely bespoke/enterprise)."))
    else:
        passed.append("L5-02")
    # L5-07 llms.txt (advisory)
    try:
        r = requests.get(urljoin(base, "/llms.txt"), headers={"User-Agent": UA}, timeout=8)
        has = r.status_code == 200 and "text/plain" in r.headers.get("Content-Type", "").lower()
    except Exception:
        has = False
    if not has:
        findings.append(_f("L5-07", "No llms.txt content map (proactive)", "low", "advisory",
                           "No /llms.txt found. An emerging convention that points assistants at canonical plain-text content. Not a defect.",
                           [base], "Publish /llms.txt linking the definitional page, pricing, docs, and contact."))
    else:
        passed.append("L5-07")
    return findings, passed

def main():
    if len(sys.argv) < 2:
        print("Usage: uv run answer_audit.py <bundle_dir>", file=sys.stderr); sys.exit(1)
    f, p = run(sys.argv[1])
    print(json.dumps({"layer": "L5", "findings": f, "checks_passed": p}, indent=2))

if __name__ == "__main__":
    main()
