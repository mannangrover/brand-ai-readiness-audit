# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests>=2.31",
#     "beautifulsoup4>=4.12",
# ]
# ///
"""
fetch_bundle.py -- fetch each planned URL ONCE and write a shared bundle.

This is the heart of the composition contract: every sub-skill reads the bundle
this script produces, so seven skills cost one crawl, not seven. Fetches are
read-only GET, honour robots.txt + Crawl-delay, and record honest error state
(never a clean-looking result on a failed fetch -- the silent-false-negative trap).

Self-contained (PEP 723). Run:
    uv run scripts/fetch_bundle.py --urls https://a.com,https://a.com/pricing --out bundle/
    uv run scripts/fetch_bundle.py --urls-file urls.txt --out bundle/

Bundle layout:
    bundle/index.json          # manifest: urls, per-page status, robots policy
    bundle/pages/<slug>.json   # {url, ok, status, headers, raw_html, raw_text,
                               #  browser_status, browser_bytes, ttfb_ms, error}

The bundle stores RAW (non-JS) HTML + extracted text. Rendered HTML, when a
headless browser is available, is added separately by the L2 skill; its absence
is recorded, never silently treated as "no gap".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

AUDIT_UA = "brand-ai-readiness-audit/1.0 (+read-only auditor)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "template", "svg"]):
        t.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def slug(url: str) -> str:
    p = urlparse(url)
    base = (p.path.strip("/") or "home").replace("/", "_")
    base = re.sub(r"[^A-Za-z0-9_.-]", "-", base)[:60]
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{base}-{h}"


def load_robots(base: str) -> tuple[robotparser.RobotFileParser | None, str, float]:
    """Return (parser, reachability, crawl_delay). On 5xx/error -> treat as blocking."""
    robots_url = urljoin(base, "/robots.txt")
    try:
        r = requests.get(robots_url, headers={"User-Agent": AUDIT_UA}, timeout=TIMEOUT)
    except requests.exceptions.RequestException:
        return None, "error", 0.0
    if 500 <= r.status_code < 600:
        return None, "server_error", 0.0
    if r.status_code != 200:
        rp = robotparser.RobotFileParser()
        rp.parse([])  # empty -> allow all
        return rp, "absent", 0.0
    rp = robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    delay = rp.crawl_delay(AUDIT_UA) or 0.0
    return rp, "ok", float(delay)


def fetch(url: str, ua: str) -> dict:
    t0 = time.time()
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=TIMEOUT)
        return {"ok": True, "status": r.status_code, "ttfb_ms": int((time.time() - t0) * 1000),
                "bytes": len(r.content), "final_url": r.url, "hops": len(r.history),
                "headers": {k.lower(): v for k, v in r.headers.items()}, "text": r.text}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "status": None, "error": f"{type(e).__name__}: {e}",
                "ttfb_ms": int((time.time() - t0) * 1000)}


def main() -> None:
    args = sys.argv[1:]
    urls: list[str] = []
    out_dir = "bundle"
    if "--urls" in args:
        urls = [u.strip() for u in args[args.index("--urls") + 1].split(",") if u.strip()]
    if "--urls-file" in args:
        with open(args[args.index("--urls-file") + 1], encoding="utf-8") as fh:
            urls += [ln.strip() for ln in fh if ln.strip()]
    if "--out" in args:
        out_dir = args[args.index("--out") + 1]
    if not urls:
        print("Usage: uv run fetch_bundle.py --urls u1,u2 [--out bundle/]", file=sys.stderr)
        sys.exit(1)

    base = f"{urlparse(urls[0]).scheme}://{urlparse(urls[0]).netloc}"
    base_host = urlparse(base).netloc
    rp, robots_reach, crawl_delay = load_robots(base)
    pages_dir = os.path.join(out_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)

    manifest = {
        "base": base,
        "site": urlparse(base).netloc,
        "robots": {"reachability": robots_reach, "crawl_delay": crawl_delay,
                   "treat_as": "disallow_all" if robots_reach in ("error", "server_error") else "ok"},
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pages": [],
    }

    for url in urls:
        if urlparse(url).netloc != base_host:
            # A bundle is per-site; robots is fetched once for base_host and does
            # not apply to other hosts. Skip cross-host URLs rather than judge
            # them under the wrong robots policy.
            manifest["pages"].append({"url": url, "ok": False, "skipped": "off_host"})
            continue
        allowed = True if rp is None and robots_reach == "absent" else (
            rp.can_fetch(AUDIT_UA, url) if rp else False)
        if rp is None and robots_reach in ("error", "server_error"):
            # broken robots -> treat as disallow-all; do not fetch, record why
            manifest["pages"].append({"url": url, "ok": False,
                                      "skipped": "robots_unreachable_treated_as_disallow_all"})
            continue
        if not allowed:
            manifest["pages"].append({"url": url, "ok": False, "skipped": "disallowed_by_robots"})
            continue

        raw = fetch(url, AUDIT_UA)
        rec = {"url": url, "ok": raw.get("ok"), "status": raw.get("status"),
               "ttfb_ms": raw.get("ttfb_ms"), "hops": raw.get("hops"),
               "final_url": raw.get("final_url"), "error": raw.get("error")}
        ctype = raw.get("headers", {}).get("content-type", "") if raw.get("ok") else ""
        is_html = "html" in ctype.lower() or (raw.get("status") == 200 and "<html" in (raw.get("text", "")[:2000].lower()))
        if raw.get("ok") and raw.get("status") == 200 and is_html:
            html = raw["text"]
            rec["raw_html"] = html
            rec["raw_text"] = extract_text(html)
            rec["raw_text_len"] = len(rec["raw_text"])
            rec["headers"] = raw["headers"]
            # one browser-UA HEAD-ish GET for parity signal (bytes only)
            b = fetch(url, BROWSER_UA)
            rec["browser_status"] = b.get("status")
            rec["browser_bytes"] = b.get("bytes")
            rec["audit_bytes"] = raw.get("bytes")
        with open(os.path.join(pages_dir, slug(url) + ".json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        manifest["pages"].append({"url": url, "slug": slug(url), "ok": rec["ok"],
                                  "status": rec.get("status"), "raw_text_len": rec.get("raw_text_len")})
        if crawl_delay:
            time.sleep(min(crawl_delay, 5.0))

    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(json.dumps({"out": out_dir, "pages": len(manifest["pages"]),
                      "ok_pages": sum(1 for p in manifest["pages"] if p.get("ok")),
                      "robots_reachability": robots_reach}, indent=2))


if __name__ == "__main__":
    main()
