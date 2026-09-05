---
name: crawl-access-audit
description: >-
  Audits whether an AI crawler is even let in to a website (discoverability
  gate 1: reachability). Checks robots.txt per individual AI agent, separating
  live-retrieval agents (block = no citation) from training agents (block = no
  memory); detects user-agent discrimination where a bot gets a 4xx while a
  browser gets 200; checks sitemap presence and reachability, noindex /
  X-Robots-Tag on fact pages, fetch latency, host/canonical consistency, and
  soft-404s. Use as part of a brand AI-readiness audit to explain why a site
  that ranks in search is still invisible to AI assistants.
license: MIT
allowed-tools:
  - bash
  - web_search
  - web_fetch
---

# Crawl & Access Audit (L1 — reachability)

## When to use
Run this first in a discoverability audit. If the crawler cannot reach the
content, nothing downstream matters. This skill answers one question per check:
*is the fact-bearing page actually reachable by the agents that feed AI
assistants?* It reasons from mechanism, not from a fixed site list.

## Inputs
- `site_url` (required): root URL or bare domain.
- `sample_urls` (optional): the pages the orchestrator already planned. If
  absent, the script discovers up to 8 representative pages itself.

## Procedure

1. **Run the access probe.** It is self-contained (PEP 723); no install step:
   ```
   uv run scripts/access_probe.py {site_url} [--urls url1,url2,...]
   ```
   It fetches robots.txt, the homepage and sampled pages (audit-UA and
   browser-UA), the sitemap, and a deliberately non-existent path. Output is one
   JSON object; read it and apply the severity rules below. The script
   *measures*; you *judge* — the calls that need judgement are flagged.

1a. **If the probe reports fetch errors, do NOT treat them as clean results.**
   When a page's `ok` is `false` (network blocked in the run environment,
   timeout, DNS), that measurement is *inconclusive*, not "passed". Two rules:
   - **Never** convert a fetch error into a "no problem" verdict. A blocked
     robots.txt is `disallow_all`, not "permissive" (see step 2). An unreachable
     page is `checks_skipped`, not a clean page. This is the exact silent
     false-negative that makes an audit report fiction.
   - **Fallback path** where `bash` has no network (some sandboxes allow-list
     bash to package registries only): re-fetch the same URLs with the web
     tools. `web_fetch` will only accept a URL that has already appeared in a
     result, so the order is `web_search("{domain} robots.txt")` →
     `web_fetch` the surfaced URL; repeat for the homepage, sitemap, and sampled
     pages. Feed those bodies back through the same judgement rules below. If
     neither path works, emit `checks_skipped` with the reason — say so plainly,
     never invent a result.

2. **robots.txt reachability first (this is the subtle one).** Read
   `robots.status`:
   - `404` or absent → permissive, **not a finding**. Record "robots.txt absent:
     no restriction" in checks_passed.
   - `5xx` / timeout / connection error → per crawler convention a broken
     robots.txt is treated as **disallow-all**. Report **critical** (L1-05b):
     "robots.txt returns 503; conformant crawlers treat this as blocking the
     whole site." Do NOT read this as permissive — that is the bug we explicitly
     guard against.
   - `200` → parse it (below).

3. **Per-agent blocking (L1-01 / L1-02).** From `robots.agents`, for each agent
   with `blocked_sitewide: true`:
   - If it is a **retrieval** agent (OAI-SearchBot, ChatGPT-User, Claude-User,
     Claude-SearchBot, PerplexityBot, Perplexity-User) → **critical** (L1-01):
     live citation is impossible. List the exact agents.
   - If it is only a **training** agent (CCBot, Google-Extended,
     Applebot-Extended, meta-externalagent, GPTBot, anthropic-ai, Bytespider) →
     **medium** (L1-02): often a deliberate, legitimate choice; say so and note
     the trade-off. Do NOT report a boolean "AI blocked" — always name agents and
     the class.

4. **Fact-bearing paths disallowed for all crawlers (L1-03).** From
   `robots.wildcard_disallow`, report only patterns that match a page in
   `sample_urls`. **Do NOT fire** on patterns matching only `/search`, `/cart`,
   `/checkout`, `/account`, session, or filter URLs — blocking those is correct.
   Severity high, scoped to the matched paths.

5. **User-agent discrimination (L1-04).** From `ua_parity`, if the audit-UA
   status is 4xx while the browser-UA status is 200 (or `bytes_ratio < 0.5`) →
   **critical** if it affects the homepage/fact pages. **Do NOT fire** when both
   UAs get the same status or `bytes_ratio > 0.8` (that is normal
   personalization). Evidence: both status codes and the byte ratio.

6. **noindex / X-Robots-Tag (L1-07).** For each sampled page with
   `meta_robots` or `x_robots_tag` containing `noindex`: **critical** on a fact
   page. **Do NOT fire** on login / search / cart / thank-you pages.

7. **Sitemap (L1-06).** If `sitemap.reachable == 0`: medium (high if the site
   has >100 pages and weak internal linking). **Do NOT fire** for a small site
   (<20 pages) fully reachable from the homepage in two clicks. If sampled
   sitemap entries 404, report the ratio.

8. **Latency (L1-08).** From `latency.median_ttfb_ms`: medium above 1500,
   high above 3000. **Report the median, never a single outlier.**

9. **Host / canonical & soft-404 (L1-09 / L1-11).** If `redirects.hops > 1` or
   the canonical host contradicts the served host → medium. If the random
   non-existent path returned 200 → soft-404, medium.

10. **Aggregate.** One finding per distinct problem (not per page). For each,
    emit the shared finding shape below with concrete evidence. Add anything you
    *checked and found clean* to a `checks_passed` list so the orchestrator can
    show coverage.

## Output
A list of finding objects (no `id`, no final `severity` — the orchestrator
assigns both) in the shared shape:
```json
{
  "check_id": "L1-01",
  "layer": "L1",
  "title": "string",
  "raw_severity": "critical|high|medium|low",
  "confidence": "confirmed|probable|advisory",
  "evidence": "concrete, reproducible measurement",
  "affected_urls": ["..."],
  "suggested_action": { "summary": "the specific fix", "priority": "critical|high|medium|low", "detail": "optional" }
}
```
plus `checks_passed: ["L1-06", ...]` and `checks_skipped: [{check_id, reason}]`.

## Suggested-action templates
- Retrieval agent blocked → "Allow {agents} on public content in robots.txt; keep any training opt-out separate. These are independent decisions."
- Broken robots.txt (5xx) → "Fix the robots.txt endpoint so it returns 200 (or 404 if none is intended); a 5xx is treated as blocking the whole site."
- UA discrimination → "Allowlist declared AI-assistant user-agents at the WAF, verified by reverse-DNS / published IP ranges rather than UA string alone."
- Disallowed fact path → "Remove the Disallow for {path}, or publish the same facts on an unrestricted URL."
- noindex on a fact page → "Remove the noindex directive from {url}."

## Guardrails
Read-only; GET/HEAD only. Honour robots.txt and Crawl-delay for our own
requests. Never authenticate or bypass a block — report it. Never treat a 5xx
robots.txt as permissive.
