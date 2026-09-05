# brand-ai-readiness-audit

An **Agent Skill Marketplace** that audits any website for the problems that keep
a brand from being found and cited by AI assistants, and that keep visitors who
do arrive from staying. Given a URL, the entrypoint produces a single prioritized
report of findings (evidence + severity) and suggested fixes.

**Recommend-only.** No skill here modifies a live site. Everything runs read-only,
GET/HEAD only, honouring `robots.txt` and `Crawl-delay`. Suggested actions are
recommendations, never applied changes.

## How to run
The entrypoint is `audit-orchestrator`. The whole pipeline is one command:

```
uv run skills/audit-orchestrator/scripts/assemble_report.py example.com --max-pages 10 --out audit_out/
```

It writes two files:
- **`audit_out/report.json`** — machine-readable, validates against `skills/audit-orchestrator/references/report-schema.json`.
- **`audit_out/report.md`** — the same audit in plain English for a non-expert.

Under the hood it runs `plan_crawl.py` → `fetch_bundle.py` (one shared crawl) →
the six sub-skill scripts → dedupe → severity → report. Each sub-skill script can
also be run alone over a bundle (see each skill's `## Script` section).

Scripts are self-contained via PEP 723 metadata and run with **`uv run`** — no
`pip install` step. If `uv` is unavailable, `pip install requests beautifulsoup4`
then `python <script>` works identically.

## The layered model
Each skill owns one **mechanism** in the chain a fact must survive to reach an AI
answer — reach → read → extract → corroborate → cover — plus the engagement half.
This is genuine separation of concerns, not topic-splitting: remove any skill and
the report simply loses that layer.

| Skill | Layer | Owns |
|---|---|---|
| **audit-orchestrator** *(entrypoint)* | — | Plans a budgeted crawl, fetches once, runs the others over the shared bundle, dedupes to root cause, applies one severity function, emits the report. |
| crawl-access-audit | L1 Reachability | robots per-agent (retrieval vs training), UA discrimination, sitemap, noindex, latency, canonicalization, soft-404. |
| render-extractability | L2 Readability | raw-vs-rendered gap, facts locked in images/PDF/video/iframes, interaction-gated content, semantics. |
| structured-data-audit | L3 Extractability | JSON-LD validity/completeness, **schema-vs-DOM agreement**, quotable definitional facts, meta/canonical. |
| entity-corroboration | L4 Corroboration | sameAs anchors, entity disambiguation, NAP/self consistency, staleness, **lastmod credibility**. |
| answerability-audit | L5 Query-space | **answer the site's own questions from machine-visible text only**; pricing/comparison/persona/geo coverage; llms.txt. |
| engagement-audit | Engagement | the AI-referred cold arrival — scent match, orientation, next action, dead ends, interstitials, load cost, mobile, anchors. |

## How the entrypoint composes them
1. **Normalize** the target to a canonical origin.
2. **Plan** a sample within `max_pages`, clustered by template class (never a full crawl).
3. **Fetch once** into a shared bundle — every sub-skill reads it; none re-fetches.
4. **Run** the sub-skills L1→ENG over the bundle.
5. **Dedupe** many symptoms of one root cause into one finding.
6. **Score** every finding with one deterministic severity function (layer × breadth × fact-criticality × corroboration-damping).
7. **Order + id** deterministically and **emit** one report against `skills/audit-orchestrator/references/report-schema.json`.

## Design commitments
- **Deterministic** — fixed sampling, fixed sort, stable ids; `audited_at` is the only time-dependent value.
- **Few false positives** — every check carries a "do not fire when" guard; a register of 10 real-site traps is documented and each maps to its guard.
- **Evidence-first** — every finding is a reproducible measurement (URLs, counts, ratios, quoted text).
- **Coverage shown** — the report lists checks that passed and checks skipped (with reasons), not only failures.
- **Generalizes** — no hardcoded domains, verticals, or keyword lists; thresholds derive from the site under audit.

## Report shape
See `skills/audit-orchestrator/references/report-schema.json`. It is a strict
**superset** of the Round-3 handout floor: every required field is present, plus
additive `layer`, `check_id`, `confidence`, `audit_meta` (pages sampled, checks
passed/skipped, robots-compliance, duration).

## Safety
Read-only; no authenticated, destructive, or rate-abusing actions; respects
`robots.txt`; a blocked path is reported as a finding, never bypassed. Submission
contains no model weights.
