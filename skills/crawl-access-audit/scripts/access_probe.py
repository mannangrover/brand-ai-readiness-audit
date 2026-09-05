# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests>=2.31",
# ]
# ///
"""
access_probe.py -- L1 reachability measurements for a website.

Self-contained: run with `uv run scripts/access_probe.py <site_url>`; uv reads the
PEP 723 block above and provides `requests` automatically. No install step.

It MEASURES; the SKILL.md JUDGES. Every value here is a fact the agent turns into
a finding with a severity, applying the guard clauses in SKILL.md.

Read-only. GET only. Honours robots.txt for its own sampled-page fetches (the one
deliberate exception is robots.txt itself and a single non-existent path used to
detect soft-404s).

Usage:
    uv run access_probe.py https://example.com
    uv run access_probe.py example.com --urls https://example.com/pricing,https://example.com/about

Output: one JSON object on stdout.

Key design points (from field research):
  * robots.txt status is reported distinctly: 404/absent (permissive) vs
    5xx/error (conventionally treated as disallow-all) vs 200 (parsed).
  * AI agents are partitioned into RETRIEVAL vs TRAINING classes -- blocking a
    retrieval agent kills live citation; blocking a training agent only affects
    model memory.
  * user-agent parity: the same URL is fetched with an audit UA and a browser UA
    to surface WAF/bot-manager discrimination that robots.txt never reveals.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
from urllib.parse import urljoin, urlparse

import requests

AUDIT_UA = "brand-ai-readiness-audit/1.0 (+read-only auditor)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Agents that fetch a page live to build/cite an answer. Blocking these = the
# brand cannot be cited by that assistant, ever.
RETRIEVAL_AGENTS = [
    "OAI-SearchBot",
    "ChatGPT-User",
    "Claude-User",
    "Claude-SearchBot",
    "PerplexityBot",
    "Perplexity-User",
]
# Agents that gather data for training/indexing. Blocking these affects model
# memory, not live citation -- often a deliberate, legitimate business choice.
TRAINING_AGENTS = [
    "GPTBot",
    "anthropic-ai",
    "ClaudeBot",
    "CCBot",
    "Google-Extended",
    "Applebot-Extended",
    "meta-externalagent",
    "Bytespider",
]
ALL_AGENTS = RETRIEVAL_AGENTS + TRAINING_AGENTS

# Path prefixes that SHOULD be blocked -- never a finding on their own.
UTILITY_PATH_HINTS = (
    "/search", "/cart", "/checkout", "/account", "/login", "/signin",
    "/admin", "/api/", "/wp-admin", "?", "/session", "/logout", "/filter",
)

TIMEOUT = 12


def fetch(url: str, ua: str, allow_redirects: bool = True) -> dict:
    t0 = time.time()
    try:
        r = requests.get(
            url,
            headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,*/*"},
            timeout=TIMEOUT,
            allow_redirects=allow_redirects,
        )
        return {
            "ok": True,
            "status": r.status_code,
            "ttfb_ms": int((time.time() - t0) * 1000),
            "bytes": len(r.content),
            "final_url": r.url,
            "hops": len(r.history),
            "headers": {k.lower(): v for k, v in r.headers.items()},
            "text": r.text,
        }
    except requests.exceptions.RequestException as e:
        return {
            "ok": False,
            "status": None,
            "error": f"{type(e).__name__}: {e}",
            "ttfb_ms": int((time.time() - t0) * 1000),
        }


# ---------- robots.txt ----------

def parse_robots(text: str):
    """Minimal group-aware robots parser.

    Returns (groups, sitemaps) where groups maps a lowercased user-agent token to
    {"disallow": [...], "allow": [...]}. A new User-agent line after rules have
    been seen starts a fresh group.
    """
    groups: dict[str, dict[str, list[str]]] = {}
    sitemaps: list[str] = []
    current: list[str] = []
    seen_rule = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "sitemap":
            sitemaps.append(value)
        elif field == "user-agent":
            if seen_rule:
                current = []
                seen_rule = False
            current.append(value.lower())
            groups.setdefault(value.lower(), {"disallow": [], "allow": []})
        elif field in ("disallow", "allow") and current:
            seen_rule = True
            for a in current:
                if value:
                    groups[a][field].append(value)
    return groups, sitemaps


def agent_verdict(groups: dict, agent: str) -> dict:
    grp = groups.get(agent.lower())
    inherited = grp is None
    if grp is None:
        grp = groups.get("*", {"disallow": [], "allow": []})
    return {
        "blocked_sitewide": "/" in grp["disallow"] and "/" not in grp["allow"],
        "disallow_count": len(grp["disallow"]),
        "inherited_from_wildcard": inherited,
    }


def analyze_robots(base: str) -> dict:
    r = fetch(urljoin(base, "/robots.txt"), AUDIT_UA)
    status = r.get("status")
    out = {"status": status, "found": bool(r.get("ok") and status == 200)}

    if not r.get("ok"):
        # Network error / timeout -> conventionally treated as disallow-all.
        out["reachability"] = "error"
        out["error"] = r.get("error")
        out["treat_as"] = "disallow_all"
        return out
    if status and 500 <= status < 600:
        out["reachability"] = "server_error"
        out["treat_as"] = "disallow_all"
        return out
    if status != 200:
        # 404 and other non-200 -> no robots restrictions apply.
        out["reachability"] = "absent"
        out["treat_as"] = "permissive"
        return out

    out["reachability"] = "ok"
    out["treat_as"] = "parsed"
    groups, sitemaps = parse_robots(r["text"])
    wildcard = groups.get("*", {"disallow": [], "allow": []})
    out["wildcard_disallow"] = wildcard["disallow"]
    out["declared_sitemaps"] = sitemaps
    out["agents"] = {}
    for a in ALL_AGENTS:
        v = agent_verdict(groups, a)
        v["class"] = "retrieval" if a in RETRIEVAL_AGENTS else "training"
        out["agents"][a] = v
    out["retrieval_blocked"] = [
        a for a in RETRIEVAL_AGENTS if out["agents"][a]["blocked_sitewide"]
    ]
    out["training_blocked"] = [
        a for a in TRAINING_AGENTS if out["agents"][a]["blocked_sitewide"]
    ]
    return out


# ---------- sitemap ----------

def analyze_sitemap(base: str, declared: list[str]) -> dict:
    candidates = declared[:3] if declared else [urljoin(base, "/sitemap.xml")]
    out = {"candidates": candidates, "reachable": 0, "unreachable": 0, "url_count": 0}
    sample_locs: list[str] = []
    for sm in candidates[:2]:
        r = fetch(sm, AUDIT_UA)
        if not r.get("ok") or r.get("status") != 200:
            out["unreachable"] += 1
            continue
        out["reachable"] += 1
        locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r["text"])
        out["url_count"] += len(locs)
        sample_locs.extend(locs[:10])
    # Spot-check a few listed URLs for dead entries.
    checked = 0
    dead = 0
    for loc in sample_locs[:5]:
        rr = fetch(loc, AUDIT_UA, allow_redirects=True)
        checked += 1
        if not rr.get("ok") or (rr.get("status") or 0) >= 400:
            dead += 1
    out["sampled_entries_checked"] = checked
    out["sampled_entries_dead"] = dead
    return out


# ---------- page-level signals ----------

META_ROBOTS_RE = re.compile(
    r'<meta[^>]+name=["\']robots["\'][^>]+content=["\']([^"\']+)["\']', re.I
)
CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.I
)


def page_signals(url: str) -> dict:
    r = fetch(url, AUDIT_UA)
    sig = {"url": url, "ok": r.get("ok"), "status": r.get("status"),
           "ttfb_ms": r.get("ttfb_ms"), "hops": r.get("hops"),
           "final_url": r.get("final_url")}
    if not r.get("ok") or r.get("status") != 200:
        sig["error"] = r.get("error")
        return sig
    html = r["text"]
    mr = META_ROBOTS_RE.search(html)
    can = CANONICAL_RE.search(html)
    sig["meta_robots"] = mr.group(1) if mr else None
    sig["x_robots_tag"] = r["headers"].get("x-robots-tag")
    sig["canonical"] = can.group(1) if can else None
    return sig


def ua_parity(url: str) -> dict:
    a = fetch(url, AUDIT_UA)
    b = fetch(url, BROWSER_UA)
    out = {
        "url": url,
        "audit_status": a.get("status"),
        "browser_status": b.get("status"),
        "audit_bytes": a.get("bytes"),
        "browser_bytes": b.get("bytes"),
    }
    if a.get("ok") and b.get("ok") and b.get("bytes"):
        out["bytes_ratio"] = round((a.get("bytes") or 0) / max(b.get("bytes"), 1), 3)
        out["status_differs"] = a.get("status") != b.get("status")
    return out


def soft_404(base: str) -> dict:
    probe = urljoin(base, "/zzz-nonexistent-audit-probe-9f3c1a")
    r = fetch(probe, AUDIT_UA)
    return {
        "probe_url": probe,
        "status": r.get("status"),
        "is_soft_404": bool(r.get("ok") and r.get("status") == 200),
    }


def discover_pages(base: str, limit: int = 8) -> list[str]:
    r = fetch(base, BROWSER_UA)
    urls = [base]
    if not r.get("ok") or r.get("status") != 200:
        return urls
    host = urlparse(base).netloc
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', r["text"])
    priority = ("pricing", "product", "service", "about", "docs", "faq", "contact")
    scored: list[tuple[int, str]] = []
    seen = {base}
    for h in hrefs:
        full = urljoin(base, h)
        p = urlparse(full)
        if p.netloc != host or p.scheme not in ("http", "https"):
            continue
        clean = full.split("#")[0]
        if clean in seen:
            continue
        seen.add(clean)
        score = 0
        low = clean.lower()
        for i, kw in enumerate(priority):
            if kw in low:
                score = len(priority) - i
                break
        scored.append((score, clean))
    scored.sort(key=lambda t: (-t[0], t[1]))
    for _, u in scored:
        if len(urls) >= limit:
            break
        urls.append(u)
    return urls


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: uv run access_probe.py <site_url> [--urls u1,u2,...]", file=sys.stderr)
        sys.exit(1)
    site = args[0]
    base = site if site.startswith("http") else "https://" + site
    urls = None
    if "--urls" in args:
        urls = [u.strip() for u in args[args.index("--urls") + 1].split(",") if u.strip()]

    robots = analyze_robots(base)
    if urls is None:
        urls = discover_pages(base, limit=8)

    pages = [page_signals(u) for u in urls]
    ttfbs = [p["ttfb_ms"] for p in pages if p.get("ttfb_ms") is not None]
    latency = {
        "median_ttfb_ms": int(statistics.median(ttfbs)) if ttfbs else None,
        "max_ttfb_ms": max(ttfbs) if ttfbs else None,
        "n": len(ttfbs),
    }

    result = {
        "site": urlparse(base).netloc,
        "base": base,
        "sample_urls": urls,
        "robots": robots,
        "sitemap": analyze_sitemap(base, robots.get("declared_sitemaps", [])),
        "pages": pages,
        "ua_parity": ua_parity(base),
        "latency": latency,
        "soft_404": soft_404(base),
        "redirects": {"homepage_hops": pages[0].get("hops") if pages else None},
        "utility_path_hints": list(UTILITY_PATH_HINTS),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
