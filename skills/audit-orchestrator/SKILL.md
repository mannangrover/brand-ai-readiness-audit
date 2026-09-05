---
name: audit-orchestrator
description: >-
  Entrypoint for the brand-ai-readiness-audit marketplace. Given a website URL,
  it plans a budgeted read-only crawl, invokes the focused audit skills
  (crawl-access, render-extractability, structured-data, entity-corroboration,
  answerability, engagement) over one shared set of fetched pages, deduplicates
  their findings to root causes, applies one deterministic severity function,
  and emits a single prioritized audit report of problems + suggested actions.
  Use this when asked to audit a site for why AI assistants fail to find, cite,
  or correctly describe a brand, or why visitors who arrive do not engage.
license: MIT
allowed-tools:
  - bash
  - read
  - webfetch
---

# Audit Orchestrator (entrypoint)

## When to use
Use this as the single entrypoint for a brand AI-readiness audit. It is the only
skill invoked directly; it composes the other six and produces the one report.
Do not run the sub-skills standalone for a full audit — they assume the shared
fetch bundle this skill prepares.

## Inputs
- `site_url` (required): root URL or bare domain, e.g. `https://example.com` or `example.com`.
- `max_pages` (optional, default 12): hard cap on pages fetched. Keeps the run under the 5-minute budget.
- `enable_render` (optional, default auto): whether to attempt headless rendering for L2. Auto-detects; degrades gracefully if unavailable.

## Automated path (one command)
The whole procedure below is implemented end to end by:
```
uv run scripts/assemble_report.py {site_url} --max-pages {max_pages} --out audit_out/
```
It writes `audit_out/report.json` (machine-readable, validates against
`references/report-schema.json`) and `audit_out/report.md` (plain-English). The
numbered steps below document what that script does, and can also be run by hand.

## Procedure (deterministic)

1. **Normalize the target.** Resolve bare domain to `https://`. Follow redirects
   once to the canonical origin. Record the final origin as `site`. All later
   fetches use this origin.

2. **Plan the crawl within budget.** Run:
   ```
   uv run scripts/plan_crawl.py {site_url} --max-pages {max_pages}
   ```
   It discovers pages from the sitemap and homepage links, clusters them into
   **template classes** by URL shape + DOM fingerprint, and returns up to
   `max_pages` representatives (always including the homepage and one each of:
   an About/company page, a pricing/product page, a docs/FAQ page — when they
   exist). This is a *sample*, never a full crawl. Record the chosen URLs in
   `audit_meta.sample_urls`. Never exceed `max_pages`; if the sitemap is huge,
   sample across template classes rather than taking the first N.

3. **Fetch once, share.** Run:
   ```
   uv run scripts/fetch_bundle.py --urls-file {planned_urls} --out bundle/
   ```
   This fetches each sampled URL **one time** (raw HTTP, both audit-UA and
   browser-UA where a parity check is needed), honouring robots.txt and any
   `Crawl-delay`, and writes a per-URL artifact (status, headers, raw HTML,
   extracted text, timing). **Every sub-skill reads from `bundle/` — no skill
   re-fetches a page another already has.** This shared-fetch rule is what keeps
   seven skills inside one time budget.

4. **Run the sub-skills over the bundle.** In order (cheapest / most-blocking
   first), invoke each and collect its raw findings:
   1. `crawl-access-audit`      (L1 — if it reports a sitewide block, note it but still run the rest on what is reachable)
   2. `render-extractability`   (L2)
   3. `structured-data-audit`   (L3)
   4. `entity-corroboration`    (L4)
   5. `answerability-audit`     (L5)
   6. `engagement-audit`        (ENG)
   Each returns findings **without** an `id` (this skill assigns ids) and
   **without** a final `severity` (each returns the raw signals; this skill
   computes severity uniformly — see step 6).

5. **Deduplicate to root cause.** If several findings share one root cause
   (e.g. the same missing-schema template hit on six pages, or one robots rule
   that explains multiple access failures), **merge them into one finding** whose
   `evidence` lists the affected count/URLs. Six symptoms of one cause = one
   finding, not six. This single step does more for the false-positive score than
   anything else. Use `check_id` + normalized root to detect duplicates.

6. **Apply one severity function.** Compute every finding's severity with the
   shared formula so severities are comparable across skills (implemented in
   `scripts/assemble_report.py`, function `severity()`): layer weight × breadth ×
   fact-criticality × corroboration-damping, defined in `references/severity.md`.
   The damping term lowers L3/L5 severity when L4 shows strong external
   corroboration; a homepage-affecting finding gets a breadth floor so a
   site-defining failure is not diluted by unaffected utility pages. Do not
   hand-assign severities in prose.

7. **Assign stable ids and order.** Sort findings by (severity desc, layer,
   check_id, normalized_url). Assign `F-001`, `F-002`, … in that fixed order so
   the same site always yields the same ids. Derive nothing from wall-clock time
   except `audited_at`.

8. **Emit the report.** Produce one JSON object validating against
   `references/report-schema.json`. Populate `summary` counts, `summary.top_priorities`
   (the ordered ids a non-expert should fix first), the `findings` array, and
   `audit_meta` (pages sampled, checks run, **checks passed**, **checks skipped
   with reasons**, robots-compliance statement, duration). Reporting passed and
   skipped checks — not only failures — is required: it shows the audit looked.

## Output
A single audit report (JSON) conforming to `references/report-schema.json`:
findings (each with id, title, severity, evidence, suggested_action, plus
layer/check_id/confidence) and a summary with counts and top priorities. Nothing
in this marketplace modifies the audited site; suggested actions are
recommendations only.

## Composition notes (why this is a marketplace, not one skill)
Each sub-skill owns one **mechanism** from the discoverability chain (reach →
read → extract → corroborate → cover) plus the engagement half — a genuine
separation of concerns, not topic-splitting. The orchestrator adds the three
things a bag of skills cannot do for itself: **one budgeted fetch** shared by
all, **root-cause dedupe** across skills, and **one severity scale**. Remove any
sub-skill and the report simply loses that layer; the contract is unchanged.

## Guardrails
- Read-only. GET/HEAD only. Never submit a form, authenticate, or POST.
- Honour robots.txt and `Crawl-delay` for our own fetching. If robots blocks a
  path, that is a *finding* (via crawl-access-audit) — never fetch it anyway.
- Budget: `max_pages` pages, target < 5 minutes total. Prefer breadth of
  template classes over depth.
- Deterministic: fixed sort orders, fixed sample strategy, stable ids, no
  randomness. `audited_at` is the only time-dependent value.
