---
name: answerability-audit
description: >-
  Audits whether a site actually answers the questions people ask AI assistants
  about it (discoverability: query-space coverage). Its headline check simulates
  the failure: using ONLY the machine-visible (non-JavaScript) text, it tries to
  answer the site's own question set - what it is, who it is for, what it costs,
  where it operates, how to contact, what makes it different - and reports which
  are unanswerable, with the exact supporting text. Also checks pricing
  transparency, comparison/alternatives content, question-shaped content, persona
  and geo coverage, and llms.txt. Use in a brand AI-readiness audit to explain
  why an assistant gives a thin or generic answer about a brand.
license: MIT
allowed-tools:
  - bash
---

# Answerability Audit (L5 — query-space coverage)

## When to use
The synthesis layer. Even with reachable, readable, labelled, trusted content, an
assistant can only cite what the site actually says. This skill measures whether
the answers exist in machine-visible form.

## Inputs
- `bundle_dir` (required from the orchestrator): shared fetched pages, including
  the raw (non-JS) extracted text used for the simulation.
- `site_url`: to infer the site's category and question set.

## Procedure
Apply catalogue checks L5-01 .. L5-07. Key rules:

1. **Answerability simulation (L5-01) — headline.** Derive the site's own
   question set from its content and evident category (never a hardcoded list):
   at minimum what-it-is, who-for, cost, location, contact, differentiation.
   Attempt to answer each **using only the raw non-JS text**, recording the exact
   supporting sentence or its absence. < 1/3 answerable → critical; < 2/3 → high.
   **Do NOT fire** on questions the site has no business answering (a non-commercial
   site has no price; a global product has no address) — derive applicability
   from the site, then exclude.
2. **Pricing transparency (L5-02).** Any concrete figure/range/starting price in
   machine-visible text. "Contact us" only → high for commercial sites; advisory
   if the model is genuinely bespoke.
3. **Comparison / alternatives (L5-03).** Owned "X vs Y" / "alternatives" pages;
   absence → medium proactive (skip for a category monopoly).
4. **Question-shaped content (L5-04).** Question headings with a direct answer
   first; and/or valid FAQPage markup.
5. **Persona & geo coverage (L5-05/06).** Pages for each audience the site itself
   names; a stated operating region (high for local businesses).
6. **llms.txt (L5-07) — advisory only.** Fetch `/llms.txt`. Present on 7/16 sites
   in research, all AI-forward brands. Never a defect; a proactive recommendation.

## Output
Findings in the shared shape (check_id, layer=L5, ...) plus checks_passed. The
simulation result (answerable N/total, with the supporting quotes) is the most
persuasive evidence in the whole report — include it verbatim.

## Guardrails
Read-only. Reuse the bundle. Derive the question set from the site, never hardcode.

## Script
Run over the orchestrator's shared bundle:
```
uv run scripts/answer_audit.py <bundle_dir>
```
