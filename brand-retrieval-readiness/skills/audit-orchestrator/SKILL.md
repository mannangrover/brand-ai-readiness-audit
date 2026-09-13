---
name: audit-orchestrator
description: Audit a website for AI-discoverability and on-site-engagement problems and emit a single evidence-backed audit report. Use when asked to audit a site or domain, or to diagnose why a brand is hard to find or misrepresented in AI assistants, or why visitors who arrive do not engage. The sole entrypoint of this marketplace; composes the seven specialist skills in manifest order.
license: MIT
compatibility: Requires Python 3.9+ (standard library only) and outbound HTTPS. A headless browser is optional and only raises confidence on render checks.
allowed-tools: Bash Read Write Grep
metadata:
  version: "1.0.0"
---

# Audit Orchestrator (marketplace entrypoint)

This marketplace audits the assistant pipeline — **reach → read → extract → identify → trust →
cite → land** — and reports, for each break, which stage failed, on which retrieval surfaces, with
quoted evidence and a stated confidence.

**Division of labor:** scripts own what must be exact — status codes, parses, counts, timeouts,
schema validation. You (the model) own what regex cannot judge — whether a passage stands alone
as an answer, whether two claims contradict, whether a name is ambiguous.

**Recommend-only.** This marketplace never modifies a website. Refuse any request to change,
authenticate to, or submit forms on the audited site.

## Inputs

- A public http(s) URL (required).
- Optional: an existing `snapshot.json` to re-report from.

All artifacts go to a working directory `./audit/`: `snapshot.json`, `excerpts/`, `findings/`,
`passages.json`, `passages_checked.json`, `report.json`. Create it as needed and run scripts
from there with explicit paths.

## Paths (resolve these before step 2)

- `<orchestrator>` = the directory containing this SKILL.md (the audit-orchestrator skill
  folder). Every `<orchestrator>/scripts/...` and `<orchestrator>/references/...` path below
  starts here.
- `MARKETPLACE_ROOT` = the directory containing `marketplace.json`. It is the PARENT of the
  `skills/` directory (or `.agents/skills/`) — never inside a skill folder, so never look for
  `marketplace.json` under `skills/<anything>/`. Probe in order from your working directory:
  `./marketplace.json`, then `./.agents/marketplace.json`, then walk up toward the filesystem
  root checking each level for both names; then the composition fallback chain below.
- Relative paths quoted inside a specialist's SKILL.md (e.g.
  `../audit-orchestrator/scripts/...`) resolve from THAT SPECIALIST's own directory. When you
  run from anywhere else, build the path as `MARKETPLACE_ROOT` + that skill's manifest `path`
  instead of guessing.

## Budgets (hard)

- **Network:** `collect_snapshot.py` enforces a 120 s deadline, 8 s per request, serial
  requests. Do not retry it; read its printed notes instead.
- **Off-site probes:** hard sub-budget of <= 6 queries, and the first thing shed at the
  deadline. Absence of a search capability means `not_evaluated`, never a finding.
- **Clock:** elapsed time is printed, never guessed. `run_phase1` prints its started
  wall-clock and elapsed seconds; the `--passages` post-step prints seconds elapsed since the
  snapshot. When the post-step reports > 210 s (3.5 of the 5 minutes), shed off-site probes:
  every OFF check becomes `not_evaluated` ("deadline shed") and the remaining judgments finish
  in one pass each. **That printed number is the gate, read once, and it does not get
  re-evaluated later** — if it said 178 s, off-site probes run even when more time has passed
  by the time you reach them. One clock, read once, no re-estimating. That clock is pinned in
  `audit/budget.json` by `run_phase1`, the shed gates append what they dropped to it, and
  `build_report` publishes it as `coverage.time_seconds`, `coverage.shed[]` and
  `coverage.budget_exceeded`. You never carry a timestamp by hand — and you never state a
  runtime the scripts did not measure.
- **Round trips:** a shell call costs more than the command inside it. Chain any commands
  that are sequential with no decision between them into one call — snapshot + phase 1, and
  the final fragment write + cleanup + report build. Never chain across a decision you have
  not made yet.
- **Model turns:** judge each specialist from its excerpt in a single pass and write the
  fragment once; run the report build once. Emit partial findings with `not_evaluated`
  rather than overrun — a valid partial report always beats an overrun. Do not re-read
  inputs, re-derive script outputs, or revise across multiple passes. If evidence is
  missing, emit partial findings with the rest `not_evaluated` and move on. Shed in this
  order: off-site probes, then `opportunities`, then whole judgments to `not_evaluated` —
  never the report itself. Deliberation budget: settle each ambiguity in one sentence and
  act — never re-list a settled enumeration (question set, probe results) before acting on
  it; if a judgment is already past 3 turns, finish it with the remainder `not_evaluated`.
- **Context:** Do not read `audit/snapshot.json`. Only scripts touch it. You read excerpt files and script stdout only. Read each excerpt
  file once, top to bottom; if a read is truncated, continue from the truncation offset rather
  than restarting. A script's stdout larger than a screen means you called it wrong — file
  reads are exempt; read them fully. The command
  strings in steps 4–6 are complete: do not read script or registry source to reconstruct
  them, and do not re-read schemas before judging — schemas are the merge's contract and
  your excerpts already conform. `extras.fragment_shape` in each excerpt is the complete
  fragment contract — never open `finding_fragment.json` mid-audit. Apply references directly;
  never restate their contents (tables, templates, shapes) in thinking or output.

## Procedure

1. **Preflight.** Normalize the URL (add `https://` if missing; drop fragments). Refuse
   non-http(s) URLs, URLs with credentials, and private/loopback hosts — unless the user
   explicitly declared a local test fixture, in which case pass `--allow-private`. Refuse
   authenticated-area or site-altering requests entirely.

2. **Declare capabilities; the collector proposes the site type.** Declare the capabilities this
   harness exposes, as comma-separated flags — that is an environment fact, not a plan, so
   declare `subagents` whenever the harness has them and let the dispatch step decide how to
   use them. Declare nothing the harness lacks. Scripts take declared
   facts as flags and never probe for tools.
   **Do not classify the site first.** You have not fetched it yet, and a guess from the bare
   URL costs a second collection when the pages contradict it. Pass `--site-type auto` (the
   default): the collector classifies from the titles, headings, link text, URL shapes and
   JSON-LD types it already parsed, and prints one line, for example
   `site_type (proposed): ecommerce - JSON-LD @type offer/product; 88 URL(s) under /products/`.
   Accept that line and move on. Override only when it is plainly wrong against the same
   summary, by re-running with explicit `--site-type <types>` (multiple allowed, max 3, from
   `saas, ecommerce, local-business, docs-developer, publisher, gov-edu, marketplace-platform,
   org-portfolio`), and say in one sentence why. `site_type (undetermined)` is honest: the
   run proceeds with every check in scope and confidence capped.
   The type gates page sampling, question archetypes, in-scope claim types, and which checks
   apply. Once printed it is frozen for the run — never revisited in judgments; genuine doubt
   goes to `limitations`, not re-derivation.
   Run:
   `python3 <orchestrator>/scripts/collect_snapshot.py --url <URL> --out ./audit/snapshot.json --site-type auto --capabilities web_fetch[,web_search][,browser][,subagents]`
   It validates its own output, prints a small summary, and writes
   `audit/excerpts/<skill>.json` for each judgment specialist. Read only the printed summary.
   If it reports unreachable or deadline problems, continue with what was captured and record
   it. Pages are fetched on a small pool (`--concurrency`, default 4); only the waiting
   overlaps, so the snapshot is identical to a serial run.
   **Chain this with step 4 in a single shell call** (`… collect_snapshot.py … && …
   run_phase1.py …`). Nothing is decided between them, and the round trip costs more than
   the commands do.

3. **Enumerate specialists.** Resolve `MARKETPLACE_ROOT` (Paths above; fallback chain below) and read
   `marketplace.json`. Specialists run in **manifest order** (skip this entrypoint).

4. **Phase 1 (one call).** Run:
   `python3 <orchestrator>/scripts/run_phase1.py --url <URL> --out-dir ./audit --reuse-snapshot --capabilities <flags>`
   `--reuse-snapshot` uses the snapshot step 2 already wrote. Never collect the site twice.
   (Drop that flag only if step 2 was skipped, and then pass `--site-type auto` here instead.)
   It builds the snapshot, runs the three scripted specialists (access-discovery,
   representation-parity, entity-consistency), validates their fragments, and prints one
   status table — read only that table. Exit nonzero means the snapshot itself failed: read
   its notes, continue with any partial capture, record it. A specialist row marked
   missing/failed contributes `not_evaluated` — never invent its output. If the runner file
   is absent, run the three scripts individually with
   `--snapshot ./audit/snapshot.json --out ./audit/findings/<skill-id>.json` (script named in
   each skill's SKILL.md).
   Then **complete the one semantic gate**: `ENT-AMBIGUOUS-NAME`. `run_phase1` already prints
   it under "semantic gate(s) awaiting your decision", with the observations you need, the
   question to answer, and the exact command. **Decide from that print — do not open the
   fragment to read it again.** Record it with one command; never hand-edit the fragment:
   `python3 <orchestrator>/scripts/write_fragment.py --in ./audit/findings/entity-consistency-audit.json --complete-gate ENT-AMBIGUOUS-NAME --gate pass|not_evaluated --reason "<1-3 sentences>"`
   (To promote it to a finding instead, submit it through `--verdicts` with the finding
   prose.) Other `gate: "pass"` results carrying `evidence_quality: "semantic-judgment"` are
   prepared passes — leave them untouched.

5. **Judgment specialists** (in manifest order). Each writes its fragment through
   `write_fragment.py`, which validates on the way out; the merge salvages anything still
   invalid to `not_evaluated` with a lint warning.
   Read each specialist's SKILL.md once, when you start that specialist, for its procedure.
   Its excerpt carries the data contract — `checks`, `fragment_shape`, `authoring_rules`,
   `severity_facts` — so do not go looking for schemas or catalogs beyond the two files.
   Two files per specialist, read once each, then author: nothing else. Quote only
   `check_id`s from the excerpt's `extras.checks` — ids outside the catalog do not exist.
   Remove helper/scratch scripts from the working directory before step 6. Verdicts files and
   the passages draft are not scratch — they are the audit's judgment inputs. Keep them.
   - `answer-coverage-audit`: read `audit/excerpts/answer-coverage-audit.json` ONCE, follow its
     SKILL.md, write `audit/findings/answer-coverage-audit.json` AND `audit/passages.json`
     (questions from two sources: `site-derived` and `market-derived`; market-derived questions
     with no answering page feed `opportunities[]`, not findings).
   - Then run the post-step:
     `python3 <orchestrator>/scripts/collect_snapshot.py --passages ./audit/passages.json --snapshot ./audit/snapshot.json --out ./audit/passages_checked.json`
     (it also writes the offsite prompt-set excerpt).
   - `freshness-consistency-audit`: read `audit/excerpts/freshness-consistency-audit.json`
     once; write its fragment.
   - `offsite-visibility-audit` (wave 2): read `audit/excerpts/offsite-visibility-audit.json`
     (the prompt set) once. Run live probes ONLY if `web_search` was declared and the elapsed
     print from the post-step is <= 210 s — past that, shed: all four OFF checks become
     `not_evaluated` ("deadline shed"). Without search, reason over the snapshot's
     `external_presence[]` only; never invent probe results.
   - `referral-experience-audit` (wave 2): read `audit/excerpts/referral-experience-audit.json`
     once, plus `audit/passages_checked.json`; write its fragment. Past 210 s on that same
     printed clock,
     referral judges continuation failures for at most 3 questions (its SKILL.md timebox)
     and offsite-visibility sheds entirely — then go straight to step 6.

6. **Report.** Every fragment was already validated when it was written (`run_phase1.py`
   validates the scripted three; `write_fragment.py` validates each judgment one), so do not
   re-validate here. Run:
   `python3 <orchestrator>/scripts/build_report.py --site <host> --out ./audit/report.json --snapshot ./audit/snapshot.json --fragment ./audit/findings/<each>.json` (repeat `--fragment` once per fragment file; shell globs are not expanded).
   It assigns finding IDs, dedups root causes, backfills `not_evaluated`, lints forbidden
   claims, validates the report schema, publishes the measured runtime, and prints the human
   summary. It finds `audit/budget.json` beside the snapshot on its own — pass no clock flags.
   The one exception: if you shed work the gates did not record (a judgment you cut short
   yourself), add it with `--shed "<what>@<seconds>:<reason>"`, repeatable. Shedding anything
   makes the report `partial` — that is the honest status, not a failure.
   **Chain the last specialist's `write_fragment.py`, any scratch cleanup, and this
   `build_report.py` into one shell call.** They are sequential with no decision between
   them, so three round trips buy nothing.

7. **Emit.** The build has already written the full document to `./audit/report.md` —
   findings with evidence, why-it-matters, fix, owner, verify line and affected URLs; what
   passed; opportunities; needs-verification; every `not_evaluated` check with its reason —
   alongside the machine-readable `./audit/report.json`. **Do not rewrite either one, and do
   not re-read `report.json` to restate it.** From the summary the build printed, say in chat
   in at most ten lines: the headline problem, the top two or three fixes in order,
   `audit_status`, coverage, the `RUNTIME` line the build printed (measured seconds against
   the budget, and what was shed), and the honest limits. Read that runtime off stdout —
   never estimate how long your own audit took. Then point at `./audit/report.md` for
   the rest. Never paste report JSON in chat. "Not evaluated" is never a defect and is never
   silently dropped; it is in the document. After a valid report: stop. No further
   verification passes.

## Dispatch (parallel-first when subagents are declared, serial fallback)

Wall-clock is the binding constraint. If the declared capabilities include `subagents`, fan
the waves out as concurrent tasks — never test, probe, or deliberate which mode to use; one
sentence, then execute. If `subagents` was not declared or the fan-out is unavailable, run
the waves serially in this session. Specialists share no state and only read the snapshot
plus their own excerpt, so either mode gives identical inputs and outputs.

Hand each delegated task its snapshot path, excerpt path, SKILL.md path, and output path,
and require back a one-line status only — never fragment contents, never page text. Two
conditions: the task must be able to write its own fragment file, and off-site probing
stays with the parent session — worker tasks have no search tool, so when
fanning out the parent performs offsite-visibility itself: it selects and runs the probes,
records the rows, and writes the offsite fragment directly.

- **Wave 1 (no dependencies):** access-discovery, representation-parity, entity-consistency,
  freshness-consistency, answer-coverage
- **Post-step:** `collect_snapshot.py --passages audit/passages.json`
- **Wave 2 (needs wave 1):** referral-experience (needs passages_checked),
  offsite-visibility (needs the prompt set)

Where concurrent work is unavailable — or a delegated task fails or times out — that skill
contributes `not_evaluated` entries. It never fails the audit.

## Composition fallback chain (resolving MARKETPLACE_ROOT)

Try in order; use the first that works:

1. Walk up from `<orchestrator>` (Paths above: the folder holding this SKILL.md) until a
   directory containing `marketplace.json` is found; specialists live at the manifest's
   declared `path` values.
2. Else, if sibling skill directories exist next to this skill (`../<specialist-id>/`), use
   them directly.
3. Else, if the harness has the specialist skills installed by id, activate each by name and
   follow its SKILL.md (all specialists are snapshot-mode capable).
4. Else **single-skill degraded mode**: run steps 1–2 and 6 only, passing `--degraded` to
   `build_report.py`. Perform the judgment checks yourself at reduced scope directly from the
   excerpt files (answer completeness, claim conflicts, landing confirmation), writing
   fragments in the `finding_fragment.json` shape under the corresponding specialist's
   `skill_id`, and record the degraded mode in the report's limitations. A one-skill
   marketplace is a valid floor — a degraded run must still emit a schema-valid report.

## Severity and confidence

In a composed run the severity facts ride in each excerpt (`extras.severity_facts`) —
`references/severity_model.md` is the canonical reference for standalone and degraded runs.
In short: severity and confidence are separate; `critical` requires high confidence;
low-confidence hypotheses go to `needs_verification`, never findings; a failed tool call is
never a site defect; findings name the affected retrieval surfaces from
`references/provider_registry.json`.

## Output

One JSON report against `references/output_schema.json` at `./audit/report.json`, plus the
human summary in chat. Every finding carries evidence (URL + observation + method), a
suggested action with an acceptance test, and honest coverage. Do not invent scores; there is
no universal AI-readiness number.
