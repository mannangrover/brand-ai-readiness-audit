---
name: engagement-audit
description: >-
  Audits why a visitor who arrives from an AI answer does not stay (on-site
  engagement). It frames every check around the real arrival: a stranger sent by
  an assistant, landing deep in the site, mid-task, with a question already in
  mind. Checks scent match (is the cited fact visible on arrival), orientation on
  deep pages, the answer-to-action gap, dead ends and broken paths, intrusive
  interstitials, landing-page load cost, mobile rendering, broken deep-link
  anchors, accessibility barriers, and in-visit context loss. Use in a brand
  AI-readiness audit to explain a high bounce rate on AI-referred traffic.
license: MIT
allowed-tools:
  - bash
---

# Engagement Audit (on-site — keeping the AI-referred visitor)

## When to use
The second half of the brief. Discoverability gets the visitor here; engagement
decides whether they stay. The arriving visitor did not pass the homepage.

## Inputs
- `bundle_dir` (required from the orchestrator): shared fetched pages (raw +
  rendered where available).
- `citation_likelihood` (optional): page ranking from answerability-audit, used to
  pick the likely landing pages to scrutinize.

## Procedure
Apply catalogue checks E-01 .. E-10. Key rules:

1. **Scent match (E-01) — bridge check.** Rank pages by citation likelihood
   (answer density × extractability). On each likely landing page, is the fact
   that made it citable visible in the first screen and in the H1 / first
   paragraph? Buried → high. This is where discoverability and engagement meet.
2. **Orientation (E-02).** On deep pages: breadcrumbs, site identity in the
   header, a one-line "what this is". Absent → medium.
3. **Answer-to-action gap (E-03).** A fact page with no next step and no
   contextual links → medium.
4. **Dead ends & broken paths (E-04).** Sample internal links; count non-2xx;
   flag pages with no outbound internal links from main content. **Report external
   link failures separately and lower.**
5. **Intrusive interstitial (E-05).** Overlay covering main content on first load
   (consent wall, newsletter modal, autoplay) → high if blocking. **Do NOT fire**
   on a small non-covering banner or a legally required gate.
6. **Load cost / mobile / anchors (E-06/07/08).** Transfer weight, images without
   dimensions, render-blocking resources; viewport meta and 375px overflow; broken
   in-page `#anchor` targets (assistants cite anchors).
7. **Accessibility & context loss (E-09/10).** Contrast/focus/skip-link/labels;
   filter/search state reflected in the URL (also makes views crawlable).

## Output
Findings in the shared shape (check_id, layer=ENG, ...) plus checks_passed.
Suggested actions name the specific page, element, and change.

## Guardrails
Read-only. Reuse the bundle. Never submit forms or trigger actions to test flows;
inspect the delivered markup/DOM only.

## Script
Run over the orchestrator's shared bundle:
```
uv run scripts/engage_audit.py <bundle_dir>
```
