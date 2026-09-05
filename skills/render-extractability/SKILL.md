---
name: render-extractability
description: >-
  Audits whether an AI crawler can actually read a page's content (discoverability
  gate 2: readability). Compares raw HTTP HTML against the rendered DOM to find
  content that only exists after JavaScript runs, and finds facts locked in
  images, PDFs, video, cross-origin iframes, or content that needs interaction to
  appear. Reasons from an absolute raw-text floor, not from a text/HTML ratio,
  which is a known false-positive source. Use as part of a brand AI-readiness
  audit to explain why a page a human can read is invisible to a machine.
license: MIT
allowed-tools:
  - bash
---

# Render & Extractability Audit (L2 — readability)

## When to use
After reachability (L1). Answers: once the crawler is in, can it actually read the
fact? A page complete to a person can be near-empty to a non-rendering fetcher.

## Inputs
- `bundle_dir` (required from the orchestrator): the shared fetched pages (raw
  HTML + extracted text) so this skill does not re-fetch.
- `site_url` / `sample_urls`: fallback if run standalone.

## Procedure
Apply catalogue checks L2-01 .. L2-09 (see `references/` in the marketplace root
catalogue). Key rules:

1. **Raw-text floor (L2-01).** For each page, take the extracted text from the
   raw (non-JS) HTML. If it is under ~500 characters on a page whose purpose
   implies real content → **critical** (JS shell). **Do NOT fire on a low
   text/HTML ratio alone** — two heavily-cited sites in research sit at 0.005–0.007.
2. **Partial render gap (L2-02).** Where a headless browser is available, diff
   raw vs rendered text chunks. Read the JS-only sample and judge whether it
   holds a *fact* (price, spec, name, address) vs chrome. Fact in the gap → high;
   otherwise scale by size. If no browser is available, emit `checks_skipped`
   with a reason — never silently pass.
3. **Facts in non-text media (L2-03/04/05).** Flag large in-`<main>` images that
   are the sole content of their section when the fact they carry appears nowhere
   in page text; PDF-only facts with no HTML equivalent; media with no transcript.
   **Do NOT bulk-count missing alt text** — mostly decorative (43/51 on a strong
   site in research).
4. **Interaction / semantics (L2-06/07/08/09).** Content behind "load more",
   consent gate replacing the body, zero topical H1 (never flag multiple H1s),
   substantive cross-origin iframes with no text equivalent.

## Output
Findings in the shared shape (check_id, layer=L2, raw_severity, confidence,
evidence, affected_urls, suggested_action) plus checks_passed / checks_skipped.
Suggested actions name the specific content to SSR/pre-render or the specific
fact to add as visible text.

## Guardrails
Read-only. Reuse the orchestrator's bundle; do not re-fetch. Degrade to
`checks_skipped` when rendering is unavailable.

## Script
Run over the orchestrator's shared bundle:
```
uv run scripts/render_audit.py <bundle_dir> [--rendered rendered.html --url <page_url>]
```
