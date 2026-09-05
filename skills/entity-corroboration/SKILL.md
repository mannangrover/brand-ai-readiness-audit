---
name: entity-corroboration
description: >-
  Audits whether an AI assistant will trust and correctly identify a brand
  (discoverability: corroboration and freshness). Checks for external identity
  anchors (sameAs to Wikipedia/Wikidata/LinkedIn/registries), entity-name
  collisions with no disambiguator, name/address/phone consistency across the
  site, self-contradictions, staleness signals, and the credibility of sitemap
  lastmod dates (all-identical lastmods are a synthetic signal that carries no
  information). Use in a brand AI-readiness audit to explain mistaken identity or
  distrust of a brand's claims.
license: MIT
allowed-tools:
  - bash
---

# Entity & Corroboration Audit (L4 — trust and freshness)

## When to use
After extractability (L3). Answers: will the machine believe the fact and attach
it to the right entity? Agreement across independent sources is what makes a fact
repeatable; a claim living in only one place is fragile.

## Inputs
- `bundle_dir` (required from the orchestrator): shared fetched pages.
- `site_url`: for identity/collision checks that look beyond the domain.

## Procedure
Apply catalogue checks L4-01 .. L4-09. Key rules:

1. **Identity anchors (L4-01).** `sameAs` in `Organization` JSON-LD plus outbound
   links to Wikipedia, Wikidata, LinkedIn, Crunchbase, GitHub, or a registry.
   None → high for an unknown brand; low when independent coverage is heavy.
   **Do NOT fire** when anchors exist anywhere on the site, even outside JSON-LD.
2. **Entity disambiguation (L4-02).** Does the brand name collide with other
   entities? Is a disambiguator (category + location + founding year) stated near
   the name? Collision with no disambiguator → high. Unique name → not a finding.
3. **NAP + self-consistency (L4-03/04).** Extract name/address/phone and repeated
   facts (price, headcount, founding year) across pages; normalize; compare.
   Contradiction → high. **Do NOT fire** on formatting differences or genuinely
   different plans/regions that say so.
4. **Staleness (L4-05).** Copyright year, newest date, dated language, dead
   outbound-link ratio.
5. **lastmod credibility (L4-06/07).** Collect sitemap `<lastmod>` values; measure
   distinct-count and spread. **All-identical → synthetic signal**, low–medium
   ("102 URLs, 1 distinct lastmod"). Compare sitemap freshness to the
   `Last-Modified` header. **Do NOT fire** when values show a real distribution.
6. **Accountability (L4-08).** About page, contact route, address, bylines.

## Output
Findings in the shared shape (check_id, layer=L4, ...) plus checks_passed.
Suggested actions: add specific sameAs targets, pair name with category+location,
single-source the NAP, emit real lastmods or none.

## Guardrails
Read-only. Reuse the bundle. Normalize before comparing (never flag formatting).

## Script
Run over the orchestrator's shared bundle:
```
uv run scripts/corro_audit.py <bundle_dir>
```
