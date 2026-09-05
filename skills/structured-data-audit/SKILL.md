---
name: structured-data-audit
description: >-
  Audits whether an AI crawler can pick out a specific fact unambiguously
  (discoverability gate 3: extractability). Checks JSON-LD presence, JSON
  validity, and completeness against the page type; verifies the structured data
  AGREES with the human-visible page (a price in markup that differs from the
  price on screen is worse than none); and checks for a quotable definitional
  sentence, meta description, and canonical. Resolves @graph and @id before
  declaring a field missing, to avoid false positives. Use in a brand
  AI-readiness audit to explain vague or wrong AI descriptions of a brand.
license: MIT
allowed-tools:
  - bash
---

# Structured Data Audit (L3 — extractability)

## When to use
After readability (L2). Answers: can the machine label the fact unambiguously, so
it quotes the right value?

## Inputs
- `bundle_dir` (required from the orchestrator): shared fetched pages.
- `site_url` / `sample_urls`: fallback if run standalone.

## Procedure
Apply catalogue checks L3-01 .. L3-10. Key rules:

1. **Missing structured data (L3-01).** Classify each page from its evident
   purpose; if it warrants a schema.org type and has none → high (commercial) /
   medium. **Do NOT fire** when the page type maps to no type, or microdata/RDFa
   is present; damp by one level when corroboration (L4-01) is strong — some of
   the most-cited sites ship no JSON-LD.
2. **Malformed JSON-LD (L3-02).** `json.loads` each `application/ld+json` block;
   report parse failures with the char offset. Silently ignored by consumers.
3. **Incomplete (L3-03).** Check each recognized `@type` for its minimum useful
   properties. **Resolve `@graph` and `@id` references before deciding a property
   is missing** — naive per-block checking is a major false-positive source.
4. **Schema-vs-DOM disagreement (L3-04) — highest value.** Extract comparable
   values (price, currency, availability, name, address, date) from both JSON-LD
   and the DOM; normalize; compare. Disagreement → high. **Do NOT fire** on pure
   formatting differences (`29` vs `29.00`, symbol vs code).
5. **Quotable definitional sentence (L3-07).** Look for one self-contained
   sentence: `<Brand> is a <category> that <does what> for <whom>`. Absence →
   high; this is the most common cause of vague AI descriptions.
6. **Meta/canonical/OG (L3-05/06/09/10).** Missing/duplicate title & description;
   canonical only where a duplicate-URL surface exists; OG advisory only; hreflang
   only when locale variants are actually discovered.

## Output
Findings in the shared shape (check_id, layer=L3, ...) plus checks_passed.
Suggested actions name the exact schema.org type and the exact missing properties.

## Guardrails
Read-only. Reuse the bundle. Resolve @graph/@id before reporting missing fields.

## Script
Run over the orchestrator's shared bundle:
```
uv run scripts/schema_audit.py <bundle_dir>
```
