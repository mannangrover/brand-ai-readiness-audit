# Brand Retrieval Readiness — Agent Skill Marketplace

Point an agent at a cold URL and get one evidence-backed report: where the brand
stands in AI-assisted discovery, and what to fix first. Zero setup, read-only, no
instrumentation, no account. The audit follows the assistant pipeline — **reach →
read → extract → identify → trust → cite → land** — and for each break reports
which stage failed, on which retrieval surfaces, with quoted evidence and a stated
confidence.

This complements instrumented enterprise monitoring rather than cloning it: those
tools watch known topics on configured infrastructure over time; this diagnoses an
unknown site from the outside in one pass and hands over an acceptance test per fix.

## Credits and provenance

This marketplace is a merge of two independently built audit marketplaces.

- **[404Mayank/brand-retrieval-readiness](https://github.com/404Mayank/brand-retrieval-readiness)**
  — the base. Architecture, all seven specialists, the script/model division of
  labour, the excerpt-as-contract and verdicts-file pattern, the four JSON
  schemas, the severity model, and the contract + 27-scenario fixture suites.
- **[mannangrover/brand-ai-readiness-audit](https://github.com/mannangrover/brand-ai-readiness-audit)**
  — a deterministic, single-command auditor built around a layered model
  (reach → read → extract → corroborate → cover, plus engagement) with a computed
  severity function and a guard clause on every check. Merged in from it:
  `ACC-FETCH-LATENCY` (its L1-08, with the median-not-max guard kept and the
  metric relabelled — the underlying measurement is a full response cycle, not
  TTFB), `REF-VIEWPORT-ABSENT` (its E-07), and
  `references/false_positive_register.md` (its 10-trap register, remapped to this
  repo's check ids).

Both projects reached the same two findings about the same site by completely
different routes — one by regex over a shared fetch bundle, one by model judgment
over prepared excerpts — which is the main reason the merge kept both halves
rather than picking a winner.

## Division of labor

Scripts own what must be exact — status codes, parses, counts, timeouts, schema
validation. The model owns what regex cannot judge — whether a passage answers a
question, whether two claims contradict, whether a name is ambiguous. Every model
verdict cites a recorded observation (engine, query, timestamp, quoted text with
page URL). No record means `not_evaluated`, never a finding, never a pass.

## Skills

| Skill | Stage | Role | Driven by |
|---|---|---|---|
| `audit-orchestrator` | — | **entrypoint** — snapshot, composition, merge, report | `collect_snapshot.py`, `build_report.py` |
| `access-discovery-audit` | reach | robots roles, index controls, canonicals, challenges, sitemaps, link rot, fetch latency | `probe_access.py` |
| `representation-parity-audit` | read | raw/response/state/metadata fact parity | `analyze_representation.py` |
| `answer-coverage-audit` | extract | two-source question set → passage completeness | judgment over bounded excerpts |
| `entity-consistency-audit` | identify | JSON-LD validity, identity matrix, ambiguity gate | `check_entities.py` + judgment |
| `freshness-consistency-audit` | trust | dates, versions, cross-page self-contradictions | judgment over claim index + claim matrix |
| `offsite-visibility-audit` | cite | capability-gated prompt-set probes; external presence | judgment + recorded probes |
| `referral-experience-audit` | land | above-fold confirmation, overlays, soft 404s, fragments | judgment + snapshot probes |

Seven specialists plus one entrypoint. The split is pipeline stages with disjoint
inputs and evidence — remove any skill and the report loses that layer. Specialists
share no state: each reads the snapshot plus its own prepared excerpt and writes
one finding fragment. The orchestrator assigns finding IDs, dedups root causes,
enforces the severity model, validates the report schema, and prints the summary.

## How to run

From the marketplace root, one call runs phase 1 (snapshot + the three scripted
specialists + fragment validation) into `./audit/`:

```
python3 skills/audit-orchestrator/scripts/run_phase1.py --url <URL> --out-dir ./audit \
  --site-type auto --capabilities web_fetch[,web_search][,browser][,subagents]
```

`--site-type auto` is the default: the collector classifies the site from the
titles, headings, link text, URL shapes and JSON-LD types it already parsed, and
prints the proposal with its evidence for the agent to accept or override. Pass
an explicit type only to override. `--reuse-snapshot` skips collection when
`./audit/snapshot.json` already exists, so a site is never fetched twice in one
audit.

Judgment specialists follow their SKILL.md files against the prepared excerpts, then:

```
python3 skills/audit-orchestrator/scripts/build_report.py --site <host> \
  --out ./audit/report.json --snapshot ./audit/snapshot.json --fragment ./audit/findings/<each>.json
```

(`--fragment` repeats per file; the individual script commands in each SKILL.md remain
as fallback.)

`report.json` validates against `skills/audit-orchestrator/references/output_schema.json`
and always includes the required floor (`site`, `audited_at`, counts-by-severity
summary, findings with `id/title/severity/evidence/suggested_action`) plus coverage,
confidence, `opportunities[]`, `needs_verification[]`, `not_evaluated[]`,
`limitations[]`, and a `human_summary` written for a non-expert.

**Composition, fallback, dispatch.** Specialists resolve via `marketplace.json`
(manifest order is dependency order), with a fallback chain down to single-skill
degraded mode — a degraded run still emits a schema-valid report, marked `partial`
when nothing was captured. Where the harness runs work concurrently, Wave 1
(access, representation, entity, freshness, answer-coverage) fans out, then the
`--passages` post-step, then Wave 2 (referral, offsite); serial execution runs the
same waves in order. A failed specialist contributes `not_evaluated`, never a
failed audit. Off-site probes (≤6) are capability-gated and first shed at the
deadline; absence of search is `not_evaluated`, never invention.

## Validation

```sh
uvx --from skills-ref agentskills validate skills/<skill-name>
```

(Note: the `skills-ref` package installs a binary named `agentskills`.)
`tests/` holds contract tests, a 27-scenario fixture suite asserting expected
findings *and* non-findings, and a smoke runner. The false-positive corpus
(nextjs.org, gov.uk, wikipedia.org, linear.app, allbirds.com) re-runs green on
severity (zero critical; highs hand-verified true positives with egress notes).

`references/false_positive_register.md` lists the traps an unguarded audit fires
on and names the guard that stops each one. Adding a check means adding its row
there, its `negative_control` to `check_catalog.json`, and its "when NOT to flag"
line to the owning SKILL.md — a check without a guard is not finished.

## Measured runtime

**Every report states how long it took.** `run_phase1` pins the audit's start in
`audit/budget.json`, the shed gates append what they dropped and at what clock
reading, and `build_report` publishes all of it as `coverage.time_seconds`,
`coverage.budget_seconds`, `coverage.budget_exceeded` and `coverage.shed[]` —
rendered at the top of `report.md` and printed on stdout. A run that overran says
so in `limitations[]`; a run that shed work reports `audit_status: "partial"`,
because shedding is a deadline hit and the schema has always defined it that way.
Runtime is a measured property of each audit, not a claim in this README.

Snapshot collection is bounded (120 s network deadline, 8 s per request) and
typically lands in 5–30 s; the scripted specialists take seconds via the phase-1
runner. Wall-clock is dominated by model judgment turns. Measured end-to-end
across harnesses: ~2–4 minutes fast runs, ~6–8 minutes serial with live probes
(probes run ~15–20 s each all-in); dense or ambiguous sites deliberate longer on
any model. Whole-audit budget is 5 minutes; shed order is probes → opportunities
→ judgments → never the report, and a valid partial always beats an overrun. The
serial-with-live-probes path still exceeds the budget on dense sites — the
measurement now lands in the report instead of only here, which is what makes the
overrun a tracked number rather than a footnote.

## Limitations — what this will never claim

- No universal AI-readiness score. Named, denominator-explicit ratios only.
- No guaranteed rankings, citations, or surface outcomes in any suggested action.
- Training-crawler blocks are policy, not outages. A missing `llms.txt` is never
  a discoverability defect (docs-sites navigation aid at most). No estimated Core
  Web Vitals, ever — static risk observations only.
- Probe observations vary by egress, locale, account state, and time (observed
  live: one path returned 200, a login-200 redirect chain, and a 404 from three
  contexts). One run is one observation; findings carry the qualifier.
- The above-fold proxy (~600 chars of main-content extraction) measures extraction offset,
  not fold position — observed knife-edge (640-confirm vs 648-flag on the same rule);
  heading confirmation and browser observation upgrade it where available.
- Severity and confidence are separate dimensions; `critical` requires high
  confidence; low-confidence hypotheses go to `needs_verification`.
- Findings are site-level patterns with counts and quotes; `affected_urls` carries
  instances. A missing topic is an opportunity, not a defect.
