# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests>=2.31",
# ]
# ///
"""
plan_crawl.py -- choose a small, representative page sample within budget.

A full crawl cannot fit the < 5 minute budget, so we SAMPLE: discover URLs from
the sitemap and homepage, cluster them into template classes by URL shape, and
return one representative per class (up to --max-pages), always including the
homepage and, when present, an about / pricing-or-product / docs-or-faq page.

Self-contained (PEP 723). Run:
    uv run scripts/plan_crawl.py https://example.com --max-pages 12

Output: JSON { site, sample_urls, template_classes, discovery } on stdout.
Deterministic: fixed priority order, fixed sort, no randomness.
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urljoin, urlparse

import requests

AUDIT_UA = "brand-ai-readiness-audit/1.0 (+read-only auditor)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 12

# Page roles we always want one of, in priority order. Each maps to substrings.
ROLE_HINTS = {
    "pricing": ("pricing", "plans", "/price"),
    "product": ("/product", "/products", "/services", "/service", "/shop", "/dp/"),
    "about": ("/about", "/company", "/who-we-are"),
    "docs": ("/docs", "/documentation", "/developer", "/api"),
    "faq": ("/faq", "/help", "/support"),
    "blog": ("/blog", "/news", "/articles"),
    "contact": ("/contact",),
}


def fetch_text(url: str, ua: str = BROWSER_UA) -> tuple[int | None, str]:
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=TIMEOUT)
        return r.status_code, r.text
    except requests.exceptions.RequestException:
        return None, ""


def robots_sitemaps(base: str) -> list[str]:
    status, text = fetch_text(urljoin(base, "/robots.txt"), AUDIT_UA)
    if status != 200:
        return []
    return re.findall(r"(?im)^\s*sitemap:\s*(\S+)", text)


def sitemap_urls(base: str, declared: list[str], cap: int = 400) -> list[str]:
    candidates = declared[:3] if declared else [urljoin(base, "/sitemap.xml")]
    urls: list[str] = []
    for sm in candidates[:3]:
        status, text = fetch_text(sm, AUDIT_UA)
        if status != 200:
            continue
        # A sitemap index points at child sitemaps; fetch the first couple.
        child = re.findall(r"<sitemap>.*?<loc>\s*([^<]+?)\s*</loc>", text, re.S)
        if child:
            for c in child[:2]:
                st2, t2 = fetch_text(c, AUDIT_UA)
                if st2 == 200:
                    urls += re.findall(r"<url>.*?<loc>\s*([^<]+?)\s*</loc>", t2, re.S)
        else:
            urls += re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text)
        if len(urls) >= cap:
            break
    return urls[:cap]


def homepage_links(base: str) -> list[str]:
    status, html = fetch_text(base, BROWSER_UA)
    if status != 200:
        return []
    host = urlparse(base).netloc
    out = []
    seen = set()
    for h in re.findall(r'href=["\']([^"\']+)["\']', html):
        full = urljoin(base, h).split("#")[0]
        p = urlparse(full)
        if p.netloc == host and p.scheme in ("http", "https") and full not in seen:
            seen.add(full)
            out.append(full)
    return out


def template_key(url: str) -> str:
    """Collapse a URL to its template shape: digits -> #, long slugs -> {slug}."""
    p = urlparse(url)
    segs = [s for s in p.path.split("/") if s]
    norm = []
    for s in segs:
        if re.fullmatch(r"\d+", s):
            norm.append("#")
        elif re.search(r"\d", s) and len(s) > 8:
            norm.append("{id}")
        elif len(s) > 24 or s.count("-") >= 3:
            norm.append("{slug}")
        else:
            norm.append(s.lower())
    return "/" + "/".join(norm) if norm else "/"


def depth(url: str) -> int:
    return len([s for s in urlparse(url).path.split("/") if s])


def role_of(url: str) -> str | None:
    """Match a role only when a hint is a whole path SEGMENT, so /pricing matches
    but /legal/adaptive-pricing does not. Shallower URLs are preferred by the
    caller, which iterates shallow-first."""
    segs = [s.lower() for s in urlparse(url).path.split("/") if s]
    segset = set(segs)
    for role, hints in ROLE_HINTS.items():
        for h in hints:
            token = h.strip("/").split("/")[-1]
            if token in segset:
                return role
    return None


def plan(base: str, max_pages: int) -> dict:
    declared = robots_sitemaps(base)
    sm = sitemap_urls(base, declared)
    nav = homepage_links(base)
    # Merge both pools: homepage nav pages are the highest-value key pages, the
    # sitemap gives breadth. Dedupe, keep order nav-first.
    host = urlparse(base).netloc
    seen: set[str] = set()
    discovered: list[str] = []
    for u in nav + sm:
        full = urljoin(base, u.strip()).split("#")[0]  # absolutize relative locs
        p = urlparse(full)
        if p.scheme not in ("http", "https") or p.netloc != host:
            continue
        if full not in seen:
            seen.add(full)
            discovered.append(full)
    source = "sitemap+nav" if sm and nav else ("sitemap" if sm else "homepage-links")

    # Cluster by template key; keep the shortest URL as each cluster's rep
    # (shortest tends to be the canonical/section landing page).
    clusters: dict[str, list[str]] = {}
    for u in discovered:
        clusters.setdefault(template_key(u), []).append(u)
    reps: dict[str, str] = {k: min(v, key=len) for k, v in clusters.items()}

    chosen: list[str] = [base]  # homepage always first

    # Guarantee one page per priority role; prefer the SHALLOWEST matching URL
    # so /pricing beats /legal/adaptive-pricing.
    shallow_first = sorted(discovered, key=lambda u: (depth(u), len(u)))
    roles_filled: dict[str, str] = {}
    for u in shallow_first:
        r = role_of(u)
        if r and r not in roles_filled:
            roles_filled[r] = u
    for role in ROLE_HINTS:  # deterministic priority order
        if role in roles_filled and roles_filled[role] not in chosen:
            if len(chosen) < max_pages:
                chosen.append(roles_filled[role])

    # Fill remaining budget with one representative per template class, preferring
    # shallow pages (key section landings) over deep leaf/legal pages.
    for rep in sorted(reps.values(), key=lambda u: (depth(u), len(u))):
        if rep not in chosen and len(chosen) < max_pages:
            chosen.append(rep)

    return {
        "site": urlparse(base).netloc,
        "base": base,
        "sample_urls": chosen[:max_pages],
        "discovery": {
            "source": source,
            "declared_sitemaps": declared,
            "urls_discovered": len(discovered),
            "template_classes": len(clusters),
        },
        "template_classes": {k: len(v) for k, v in sorted(clusters.items())},
        "roles_found": sorted(roles_filled),
    }


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: uv run plan_crawl.py <site_url> [--max-pages N]", file=sys.stderr)
        sys.exit(1)
    site = args[0]
    base = site if site.startswith("http") else "https://" + site
    max_pages = 12
    if "--max-pages" in args:
        max_pages = int(args[args.index("--max-pages") + 1])
    print(json.dumps(plan(base, max_pages), indent=2))


if __name__ == "__main__":
    main()
