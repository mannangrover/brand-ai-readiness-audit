# Check catalogue — signal → mechanism → detection → evidence → severity → guard

Derived from field research (`research/FINDINGS.md`, observations O1–O9).
Every check is site-agnostic: thresholds derive from the site under audit, never
from a hardcoded domain, vertical, or keyword list.

**Severity ladder:** `critical` = the fact cannot reach an assistant at all ·
`high` = reaches but is unusable/wrong · `medium` = degraded · `low` = advisory.

**Confidence tiers:** `confirmed` (directly observed) · `probable` (inferred from
a sample) · `advisory` (best practice, no defect observed).

**Global rule:** every check must state when it does *not* fire. A check without
a guard clause is not finished.

---

# L1 · Reachability — is the crawler let in?

### L1-01 Retrieval-agent blocked sitewide
- **Mechanism:** robots.txt `Disallow: /` for an agent that performs *live retrieval* (OAI-SearchBot, ChatGPT-User, Claude-User, Claude-SearchBot, PerplexityBot, Perplexity-User). No live fetch → no citation, ever.
- **Detect:** parse robots.txt into per-agent groups; resolve each agent against its own group, falling back to `*`.
- **Evidence:** "robots.txt line 14: 'Disallow: /' under 'User-agent: OAI-SearchBot'. 6 of 6 retrieval agents blocked sitewide."
- **Severity:** critical.
- **Do NOT fire when:** only *training* agents are blocked — that is L1-02.
- **Fix:** allow retrieval agents on public content; keep training opt-outs if desired. These are separable decisions and most sites conflate them.

### L1-02 Training-agent blocked sitewide
- **Mechanism:** CCBot / Google-Extended / Applebot-Extended / meta-externalagent blocked → brand absent from model memory; still citable live.
- **Detect:** as L1-01, partitioned by agent class.
- **Evidence:** agent names plus the exact directive lines.
- **Severity:** medium — it is often a deliberate, legitimate business choice.
- **Do NOT fire when:** the site publishes an AI-use or licensing policy indicating intent — downgrade to advisory and say so.
- **Fix:** state the trade-off; recommend allowing retrieval agents at minimum.

### L1-03 Fact-bearing paths disallowed for all crawlers
- **Mechanism:** `Disallow:` on `/pricing`, `/products`, `/docs` under `*` hides exactly the pages that answer questions.
- **Detect:** intersect wildcard disallow patterns against the discovered page set; report only patterns matching pages we actually found.
- **Evidence:** "Disallow: /pricing/ blocks 4 of 12 sampled pages, including the only page stating a price."
- **Severity:** high, scoped to the paths.
- **Do NOT fire when:** the pattern only matches search/filter/cart/session URLs — blocking those is correct practice, not a defect.
- **Fix:** narrow the rule, or publish the equivalent facts on an allowed URL.

### L1-04 User-agent discrimination (bot 4xx / browser 200)
- **Mechanism:** a WAF or bot manager serves a challenge or 403 to non-browser UAs. Invisible in a browser; robots.txt says nothing about it.
- **Detect:** fetch the same URL twice — audit UA vs a common browser UA — and compare status and body size.
- **Evidence:** "GET / with AuditBot UA → 403 (1.2 KB); same URL with a browser UA → 200 (18 KB). Body-size ratio 0.068." *(observed on a real site during research)*
- **Severity:** critical if it affects the homepage or fact pages.
- **Do NOT fire when:** both UAs get the same status, or the difference is a personalised/geo variant of similar size (ratio > 0.8).
- **Fix:** allowlist declared AI-assistant UAs at the WAF, verified by reverse DNS or published IP ranges rather than by UA string alone.

### L1-05 robots.txt missing, malformed, or unreadable
- **Mechanism:** absence is permissive and not a defect, but a *malformed* file parses unpredictably across vendors.
- **Detect:** status; then count non-empty, non-comment, unrecognised lines and rules appearing before any `User-agent`.
- **Evidence:** the offending line numbers and content.
- **Severity:** medium if malformed. **Not a finding** if simply absent.
- **Do NOT fire when:** the file is absent and everything is reachable — record as "checked, no restriction".
- **Fix:** correct the syntax; add a `Sitemap:` directive.

### L1-06 No sitemap declared, or sitemap unreachable
- **Mechanism:** sitemaps are how a crawler finds pages that are not linked from the homepage.
- **Detect:** look for `Sitemap:` in robots.txt; fall back to `/sitemap.xml`; fetch, parse, count `<loc>`, sample-fetch a few entries.
- **Evidence:** "No Sitemap: directive in robots.txt; /sitemap.xml returns 404." or "Sitemap lists 1,015 URLs; 3 of 10 sampled return 404."
- **Severity:** medium; high if the site has >100 pages and weak internal linking.
- **Do NOT fire when:** the site is small (<20 pages) and fully reachable from the homepage within two clicks.
- **Fix:** publish and declare a sitemap; remove dead entries.

### L1-07 noindex on fact-bearing pages
- **Mechanism:** `<meta name="robots" content="noindex">` or an `X-Robots-Tag` header removes the page from indexes entirely.
- **Detect:** parse both the meta tag and the response header on every sampled page.
- **Evidence:** URL, the exact directive, and where it came from.
- **Severity:** critical on a fact page.
- **Do NOT fire when:** the page is a login, search-results, cart, or thank-you page.
- **Fix:** remove the directive from public content pages.

### L1-08 Fetch latency risks assistant timeout
- **Mechanism:** live retrieval runs on a short budget; slow responses are abandoned.
- **Detect:** median TTFB over the sampled pages, three cold fetches each.
- **Evidence:** "Median TTFB 2,049 ms across 8 pages (slowest 3,400 ms)."
- **Severity:** medium above ~1.5 s median; high above ~3 s.
- **Do NOT fire when:** a single outlier skews it — always report the median, never the max.
- **Fix:** cache/CDN the HTML response for anonymous requests; target TTFB under 800 ms.

### L1-09 Host/canonical inconsistency and redirect chains
- **Mechanism:** the same content on several hosts splits corroboration signals; long chains get truncated.
- **Detect:** request `http`/`https` × `www`/apex; count hops; compare the final URL against the declared canonical.
- **Evidence:** "http://www → https://www → https://apex (2 hops); canonical on the final page points to http://www."
- **Severity:** medium; high when the canonical contradicts the served URL.
- **Do NOT fire when:** exactly one redirect resolves to a self-consistent canonical.
- **Fix:** one canonical host, single-hop redirects, self-referential canonical.

### L1-10 Fact-bearing content behind auth or a paywall
- **Mechanism:** unauthenticated fetchers see the gate, not the content.
- **Detect:** compare the anonymous response against the page's own linked description or OG summary; look for auth-wall markers where the page is linked as public.
- **Evidence:** the URL and the gate text returned.
- **Severity:** high — reported as a strategy finding, never bypassed.
- **Do NOT fire when:** the gated area is plainly a customer account or dashboard.
- **Fix:** publish an unauthenticated summary page carrying the key facts.
- **Guardrail:** never attempt to authenticate. Report and stop.

### L1-11 Soft 404s
- **Mechanism:** a missing page returning 200 pollutes the index with contentless URLs and wastes crawl budget.
- **Detect:** request a random non-existent path; if it returns 200, compare its text against a known-good page.
- **Evidence:** "GET /zzz-nonexistent-9f3 → 200 with 'Page not found' in body."
- **Severity:** medium.
- **Do NOT fire when:** the response is 404 or 410.
- **Fix:** return a real 404 status.

---

# L2 · Readability — can it read the page?

### L2-01 Raw HTML is effectively empty (JS shell)
- **Mechanism:** the page is assembled client-side; a non-rendering fetcher receives a shell.
- **Detect:** extract text from the raw response with scripts and styles stripped. Fire on an **absolute floor**, not a ratio: raw text under ~500 characters on a page whose evident purpose implies substantive content.
- **Evidence:** "Homepage raw HTML yields 6 characters of extractable text from 8.6 KB of markup." *(a real, major site in our panel)*
- **Severity:** critical.
- **Do NOT fire when:** raw text is above the floor. A low text/HTML **ratio alone is never a finding** — two heavily-cited sites in our panel sit at ratios of 0.005–0.007 (O5).
- **Fix:** server-side render or pre-render the fact-bearing content into the initial HTML response.

### L2-02 Partial JS-render gap
- **Mechanism:** some substantive text is injected after load.
- **Detect:** raw-vs-rendered text-chunk diff; then **read the JS-only sample and judge whether it contains a fact** (price, spec, name, address, hours) rather than chrome (nav, cookie banner, footer).
- **Evidence:** "58% of text chunks are JS-only; the JS-only set includes the price ('$49/mo') and all 6 product names."
- **Severity:** high if a fact sits in the gap; medium at 0.15–0.5 with no facts; low at 0.05–0.15; nothing below 0.05.
- **Do NOT fire when:** the JS-only chunks are nav labels, consent text, or footer boilerplate. Quantity alone does not make it a finding.
- **Degradation:** if no headless browser is available, emit `skipped` with a reason. Never silently pass.
- **Fix:** SSR or pre-render the specific named content.

### L2-03 A fact exists only inside an image
- **Mechanism:** text baked into a raster image is not extractable text.
- **Detect:** find images that are large, inside `<main>`, and the sole content of their section; then check whether the fact they plausibly carry appears anywhere in the page text.
- **Evidence:** "/pricing: the only occurrence of a currency figure is inside pricing-table.png; no price appears in page text."
- **Severity:** high for price, contact, or spec; medium otherwise.
- **Do NOT fire when:** the same fact is present in body text, or the image is decorative. **Do not count missing alt attributes in bulk** — 43 of 51 images lacked alt on a strong site in our panel (O8).
- **Fix:** state the fact in visible body text beside the image; alt text alone is insufficient.

### L2-04 Fact only in a PDF
- **Mechanism:** many fetchers skip or poorly parse PDFs, and the fact loses its page context.
- **Detect:** PDF links inside `<main>`; check whether an HTML equivalent exists.
- **Evidence:** the PDF URL and the absent HTML equivalent.
- **Severity:** medium; high if it is the only source of a primary fact.
- **Do NOT fire when:** an HTML version exists, or the PDF is a form or brochure duplicating on-page content.
- **Fix:** publish an HTML equivalent and keep the PDF as a download.

### L2-05 Fact only in video or audio, with no transcript
- **Mechanism:** speech is not text.
- **Detect:** media embeds in `<main>` with no adjacent transcript, caption track, or summary.
- **Evidence:** the embed URL and the absence of a transcript element.
- **Severity:** medium.
- **Do NOT fire when:** a transcript, captions, or a text summary is present nearby.
- **Fix:** publish a transcript or text summary on the same page.

### L2-06 Content requires interaction to enter the DOM
- **Mechanism:** "load more", lazy tabs, and virtualised lists mean the content is never in the fetched document.
- **Detect:** in the rendered DOM without interaction, look for pagination or expander controls whose target content is absent.
- **Evidence:** the control text and the count of items present versus claimed.
- **Severity:** medium; high if it hides the primary listing.
- **Do NOT fire when:** the content is present in the DOM and merely visually collapsed — accordions with real markup are fine.
- **Fix:** render the first meaningful set server-side and provide crawlable paginated URLs.

### L2-07 Consent or age gate replaces the content
- **Mechanism:** the fetcher receives the interstitial as the entire document.
- **Detect:** raw text dominated by consent or age vocabulary while the page's own description implies otherwise.
- **Evidence:** the raw text head and its length.
- **Severity:** critical.
- **Do NOT fire when:** the banner overlays real content that is present in the document.
- **Fix:** serve content in the initial response; make consent an overlay, not a gate.

### L2-08 No topical H1, or no semantic landmarks
- **Mechanism:** heading and landmark structure is how an extractor locates the answer inside a long page.
- **Detect:** **zero** H1s, or an H1 carrying only the site name; missing `<main>`; skipped heading levels.
- **Evidence:** "/pricing: 0 `<h1>` elements; page title is 'Pricing' but no heading states it."
- **Severity:** medium.
- **Do NOT fire when:** more than one H1 exists — that is valid HTML5 and appeared on three well-cited sites in our panel (O7).
- **Fix:** one descriptive H1 per page stating the page's subject; wrap content in `<main>`.

### L2-09 Content isolated in a cross-origin iframe
- **Mechanism:** iframed content is attributed to the other origin, not to this page.
- **Detect:** iframes in `<main>` carrying substantive content with no on-page text equivalent.
- **Evidence:** the iframe `src` and what it carries.
- **Severity:** medium.
- **Do NOT fire when:** the iframe is a video player, map, or widget that is not the page's primary fact.
- **Fix:** mirror the key facts as native text on the host page.

---

# L3 · Extractability — can it pick out the specific fact?

### L3-01 No structured data on a page type that warrants it
- **Mechanism:** without explicit labelling, a machine must infer what each fact means from prose.
- **Detect:** classify the page from its own evident purpose (product, article, organisation, local business, FAQ), then check for a matching JSON-LD `@type`.
- **Evidence:** "Crawled 12 product pages; 0 of 12 contain schema.org Product markup."
- **Severity:** high on commercial or local-business pages; medium elsewhere.
- **Do NOT fire when:** the page type does not map to a schema.org type, or equivalent microdata/RDFa is present. Also damp by one level when external corroboration is strong (L4-01) — two of the most-cited sites in our panel ship no JSON-LD at all (O6).
- **Fix:** add the specific type, naming the minimum required properties.

### L3-02 Malformed JSON-LD
- **Mechanism:** an invalid block is silently discarded — it looks present to a human reading the source and is absent to every parser.
- **Detect:** `json.loads` each `application/ld+json` block; count failures.
- **Evidence:** "2 of 3 JSON-LD blocks fail to parse (trailing comma at char 412)."
- **Severity:** medium — high when it is the only structured data on the page.
- **Do NOT fire when:** all blocks parse.
- **Fix:** correct the JSON and validate in CI, not by eye.

### L3-03 Structured data present but incomplete
- **Mechanism:** a `Product` without `offers.price` cannot answer "what does it cost".
- **Detect:** for each recognised `@type`, check its minimum useful property set; resolve `@graph` and `@id` references before deciding a property is missing.
- **Evidence:** "Product blocks on 12 pages declare `name` only; `offers`, `price`, `priceCurrency` absent on all 12."
- **Severity:** medium; high when the missing property is the page's primary fact.
- **Do NOT fire when:** the property is supplied elsewhere in the `@graph` or via a resolvable `@id`. Naive per-block checking is a major false-positive source.
- **Fix:** name the exact missing properties and the type they belong to.

### L3-04 Structured data disagrees with the visible page
- **Mechanism:** when the labelled fact and the human-visible fact differ, the machine repeats the wrong one — worse than having no markup at all.
- **Detect:** extract comparable values (price, currency, availability, name, address, date) from JSON-LD and from the DOM; normalise; compare.
- **Evidence:** "JSON-LD offers.price = 29.00; visible price on the page is $49.00."
- **Severity:** high.
- **Do NOT fire when:** the difference is formatting (`29` vs `29.00`), currency symbol versus code, or a legitimate variant (a page listing several tiers).
- **Fix:** generate the markup from the same data source that renders the page.

### L3-05 Missing or duplicated title / meta description
- **Mechanism:** these are the snippet a retrieval system quotes before it ever opens the page.
- **Detect:** presence, length, and cross-page duplication across the sample.
- **Evidence:** "8 of 12 sampled pages share the identical meta description."
- **Severity:** medium.
- **Do NOT fire when:** unique and descriptive, regardless of length.
- **Fix:** unique, factual, page-specific text — not marketing copy.

### L3-06 Missing or wrong canonical
- **Mechanism:** duplicates split the corroboration signal across URLs.
- **Detect:** presence of `<link rel="canonical">`; whether it is self-referential and absolute.
- **Evidence:** the URL and the canonical value.
- **Severity:** medium.
- **Do NOT fire when:** the site has no duplicate-URL surface (no query params, no pagination) — one well-cited site in our panel ships no canonical at all (O6).
- **Fix:** self-referential absolute canonical on every indexable page.

### L3-07 No quotable definitional sentence
- **Mechanism:** an assistant needs one self-contained sentence it can lift. "Reimagine what's possible" is not quotable; "Acme is a UK payroll platform for companies under 50 staff" is.
- **Detect:** search the homepage and About page for a sentence containing the brand name, a category noun, and a qualifier, within the first screen of text.
- **Evidence:** the first 200 characters of extracted text, plus the absence of any matching sentence.
- **Severity:** high — this is the single most common cause of a brand being described vaguely or wrongly.
- **Do NOT fire when:** such a sentence exists anywhere in the main content, even below the fold.
- **Fix:** add one plain declarative sentence: `<Brand> is a <category> that <does what> for <whom>`, in body text and in `Organization.description`.

### L3-08 Facts are implied by prose rather than stated atomically
- **Mechanism:** a fact spread across a paragraph cannot be extracted as a unit.
- **Detect:** for each key fact type the page should carry, test whether it appears in a single self-contained sentence or a labelled element.
- **Evidence:** the paragraph in which the fact is embedded, and what is missing.
- **Severity:** medium.
- **Do NOT fire when:** the fact appears in a definition list, table, or labelled element.
- **Fix:** state facts as labelled atoms — a spec table, a definition list, a one-line answer under a question heading.

### L3-09 Missing Open Graph / social metadata
- **Mechanism:** OG tags are a cheap, widely-consumed fallback summary.
- **Detect:** count `og:` and `twitter:` meta tags.
- **Evidence:** the count and which core tags are absent.
- **Severity:** low.
- **Do NOT fire as a standalone defect** when a valid meta description and JSON-LD already carry the same facts. Advisory only.
- **Fix:** add `og:title`, `og:description`, `og:url`, `og:image`.

### L3-10 Missing hreflang on multilingual content
- **Mechanism:** without language annotation, locale variants compete and the wrong one is served or cited.
- **Detect:** only when several language or region variants of the same page are discovered.
- **Evidence:** the discovered variants and the absent annotations.
- **Severity:** medium.
- **Do NOT fire when:** the site is single-locale. Never assume multilingual intent from a language switcher alone.
- **Fix:** reciprocal `hreflang` on all variants plus `x-default`.

---

# L4 · Corroboration and freshness — will it be believed?

### L4-01 No external identity anchors (`sameAs`)
- **Mechanism:** a claim living in only one place is fragile; agreement across independent sources is what makes a fact repeatable (Appendix D).
- **Detect:** `sameAs` in `Organization` JSON-LD, plus outbound links to Wikipedia, Wikidata, LinkedIn, Crunchbase, GitHub, or a registry.
- **Evidence:** "No `sameAs` property; 0 outbound links to any independent identity source."
- **Severity:** high for an unknown brand; low for one with heavy independent coverage.
- **Do NOT fire when:** identity anchors exist elsewhere on the site (footer, About page) even if not in JSON-LD.
- **Fix:** add `sameAs` listing every profile the brand actually controls, and pursue a Wikidata entry.

### L4-02 Entity name collision with no disambiguator
- **Mechanism:** when several things share a name, the system merges or mistakes them unless something separates them (Appendix D).
- **Detect:** search the brand name; count distinct entities in the results; check whether the site states a disambiguating descriptor (category, location, founding year) near the name.
- **Evidence:** the competing entities found and the absence of any distinguishing statement.
- **Severity:** high when collisions exist and no disambiguator does; not a finding when the name is unique.
- **Do NOT fire when:** the name is distinctive, or the site already states a clear category and location.
- **Fix:** always pair the name with a category and location in the definitional sentence, the title, and `Organization`.

### L4-03 Inconsistent name, address, or phone across the site
- **Mechanism:** conflicting values reduce confidence in all of them.
- **Detect:** extract NAP from every sampled page, from JSON-LD, and from the footer; normalise and compare.
- **Evidence:** each variant with the URL it came from.
- **Severity:** medium; high for a local business.
- **Do NOT fire when:** the differences are formatting (`+44 20` vs `020`) — normalise before comparing.
- **Fix:** one canonical NAP, rendered from a single source.

### L4-04 The site contradicts itself
- **Mechanism:** two different prices or claims for the same thing means at least one cited answer will be wrong.
- **Detect:** cross-page comparison of the same fact type (price, headcount, founding year, headline claim).
- **Evidence:** both statements with their URLs.
- **Severity:** high.
- **Do NOT fire when:** the pages describe genuinely different plans, regions, or time periods, and say so.
- **Fix:** single source of truth; remove or date-stamp the stale statement.

### L4-05 Staleness signals
- **Mechanism:** visibly old content is discounted, and dated claims get repeated as current.
- **Detect:** copyright year, most recent `datePublished`/`dateModified`, dated language in body text, and the proportion of dead outbound links.
- **Evidence:** "Footer copyright reads 2023; newest article dated 2024-02; 6 of 31 outbound links return 404."
- **Severity:** medium.
- **Do NOT fire when:** the content is genuinely evergreen and carries no dated claims.
- **Fix:** refresh or date-stamp; automate the copyright year; fix dead links.

### L4-06 Synthetic `lastmod` — freshness signal carries no information
- **Mechanism:** if every URL claims the same modification date, the value is auto-stamped and consumers learn to ignore it. The site loses the ability to signal genuine updates.
- **Detect:** collect `<lastmod>` across the sitemap; measure distinct-value count and spread.
- **Evidence:** "102 sitemap URLs; all 102 carry the identical lastmod of today (1 distinct value)." *(observed on a well-known site in our panel, O9)*
- **Severity:** low to medium — a lost opportunity rather than a hard defect.
- **Do NOT fire when:** lastmod values show a real distribution — one panel site showed 962 values spanning 2020 to today.
- **Fix:** emit `lastmod` from the content's real modification timestamp, or omit it entirely. A wrong signal is worse than none.

### L4-07 Sitemap freshness contradicts server freshness
- **Mechanism:** the two claims disagree, so neither is trusted.
- **Detect:** compare the newest sitemap `lastmod` against the `Last-Modified` header and the newest on-page date.
- **Evidence:** "Newest sitemap lastmod is 86 days old; the Last-Modified header on the same page is today." *(observed, O9)*
- **Severity:** low.
- **Do NOT fire when:** the two agree within a few days.
- **Fix:** drive both from the same timestamp.

### L4-08 No authorship or accountability signals
- **Mechanism:** unattributed claims are weaker evidence than attributed ones.
- **Detect:** presence of an About page, a contact route, a physical address, and author bylines on editorial content.
- **Evidence:** which are absent.
- **Severity:** medium.
- **Do NOT fire when:** the signals exist anywhere reachable, even if not marked up.
- **Fix:** publish About and Contact; add bylines with `author` markup.

### L4-09 Facts asserted nowhere but here
- **Mechanism:** a claim with no independent echo is fragile (Appendix D).
- **Detect:** take the two or three primary claims and search for independent restatements outside the domain.
- **Evidence:** the claim and the count of independent sources found.
- **Severity:** medium, advisory in tone.
- **Do NOT fire when:** independent corroboration exists.
- **Fix:** targeted earned coverage, directory and registry listings, and consistent phrasing everywhere so the restatements match.

---

# L5 · Query-space coverage — is there anything to cite?

Appendix E: assistants weight answers toward the asker. A site that speaks to one
persona in one framing surfaces for one slice of the question space.

### L5-01 Answerability gap *(headline check)*
- **Mechanism:** the union of L1–L3. If the fact is not in machine-visible text, no amount of markup rescues it.
- **Detect:** derive the site's own question set from its content and evident category — never a hardcoded list — covering at minimum: what it is, who it is for, what it costs, where it operates, how to contact, what makes it different. Then attempt to answer each **using only the raw, non-JS text** and record the exact supporting sentence.
- **Evidence:** "Answerable from machine-visible text: 3 of 6. Unanswerable: cost, location, differentiation. 'What is it' answered by: '<quoted sentence>'."
- **Severity:** critical below one third answerable; high below two thirds.
- **Do NOT fire on a question the site has no business answering** — a non-commercial site has no price; a global SaaS has no address. Derive applicability from the site, then exclude.
- **Fix:** for each unanswerable question, name the page that should carry it and the sentence shape it needs.

### L5-02 No pricing transparency
- **Mechanism:** "how much does X cost" is among the highest-intent questions, and "contact us for pricing" is unciteable.
- **Detect:** look for any concrete figure, range, or starting price in machine-visible text.
- **Evidence:** the pricing page URL and what it says instead.
- **Severity:** high for commercial sites.
- **Do NOT fire when:** a range, a starting price, or a published calculator exists, or the model is genuinely bespoke (enterprise-only, regulated) — then recommend a published range, at advisory severity.
- **Fix:** publish at least a starting price or a band in text.

### L5-03 No comparison or alternatives content
- **Mechanism:** "X vs Y" and "alternatives to X" are extremely common prompts. With no owned page, the answer is assembled from competitors and review sites.
- **Detect:** look for comparison, alternative, or "why choose" pages in the sitemap and internal links.
- **Evidence:** their absence.
- **Severity:** medium, proactive.
- **Do NOT fire when:** such pages exist, or the brand is a category monopoly with no meaningful comparators.
- **Fix:** publish factual, verifiable comparison pages; unverifiable claims backfire.

### L5-04 No question-shaped content
- **Mechanism:** content shaped as question-then-direct-answer is the easiest to lift verbatim.
- **Detect:** headings that are questions, followed by a self-contained answer paragraph; and/or valid `FAQPage` markup.
- **Evidence:** the count of question-shaped headings across the sample.
- **Severity:** medium.
- **Do NOT fire when:** the content already answers directly under declarative headings — form matters less than the direct answer.
- **Fix:** restructure key content as question headings with a one-or-two-sentence answer first, detail after; add `FAQPage` markup.

### L5-05 Single-persona framing
- **Mechanism:** Appendix E — different askers get different answers. One framing reaches one audience.
- **Detect:** infer the audience segments the site itself names, then check for pages addressing each; also check use-case and industry coverage.
- **Evidence:** the segments named in copy versus the pages that exist for them.
- **Severity:** low to medium, proactive.
- **Do NOT fire when:** the product is genuinely single-audience and says so.
- **Fix:** one page per named segment or use case, each with its own definitional sentence.

### L5-06 No geographic or locale grounding
- **Mechanism:** location-qualified prompts are extremely common; ungrounded content cannot match them.
- **Detect:** whether any page states where the business operates or is based, in text and in markup.
- **Evidence:** the absence of any location statement.
- **Severity:** high for a local or regional business; low for a global digital product.
- **Do NOT fire when:** the business is genuinely location-independent.
- **Fix:** state the operating region in text and in `Organization`/`LocalBusiness` markup.

### L5-07 No machine-readable content map (`llms.txt`)
- **Mechanism:** an emerging convention pointing assistants at the canonical, plain-text version of key content.
- **Detect:** fetch `/llms.txt`.
- **Evidence:** present or absent. In our 16-site panel, 7 sites had one — and all 7 were brands that visibly invest in AI discoverability.
- **Severity:** low, always advisory.
- **Do NOT present as a defect.** It is a proactive recommendation, never a finding of fault.
- **Fix:** publish `/llms.txt` linking the definitional page, pricing, docs, and contact.

---

# ENG · On-site engagement — why the arriving visitor leaves

Frame every check around the actual arrival: a stranger, sent by an AI answer,
landing deep in the site, mid-task, with a specific question already in mind.

### E-01 Scent mismatch on the likely landing page *(bridge check)*
- **Mechanism:** the assistant cited a claim; if the visitor cannot see that claim immediately on arrival, they bounce. This is where discoverability and engagement meet.
- **Detect:** rank pages by citation likelihood (answer density × extractability). For each, check whether the fact that made it citable appears within the first screen of main content and in the H1 or first paragraph.
- **Evidence:** "The only page stating price is /pricing; the figure first appears 2,400 characters into main content, below three marketing sections."
- **Severity:** high.
- **Do NOT fire when:** the fact appears early in the main content.
- **Fix:** lead with the answer; put supporting material after it.

### E-02 No orientation on deep pages
- **Mechanism:** an AI-referred visitor did not pass the homepage and has no context for where they are.
- **Detect:** on non-homepage pages, check for breadcrumbs, a site identity in the header, and a one-line statement of what the site is.
- **Evidence:** which are absent and on how many sampled pages.
- **Severity:** medium.
- **Do NOT fire when:** the header carries clear identity and navigation.
- **Fix:** breadcrumbs plus a one-line identity statement on every page.

### E-03 Answer-to-action gap
- **Mechanism:** the page answers the question and offers nothing next, so the visit ends there.
- **Detect:** on fact-bearing pages, look for a primary action and contextual links onward.
- **Evidence:** "/pricing contains no call to action and 2 outbound internal links, both in the footer."
- **Severity:** medium.
- **Do NOT fire when:** a clear next step exists in the main content.
- **Fix:** one specific next step in the main content, matched to the question the page answers.

### E-04 Dead ends and broken paths
- **Mechanism:** a broken next step ends the session.
- **Detect:** sample internal links per page; count non-2xx; flag pages with no outbound internal links from main content.
- **Evidence:** "4 of 38 sampled internal links return 404; /guides/setup has no outbound links from main content."
- **Severity:** medium; high when a primary nav link is broken.
- **Do NOT fire when:** the failures are external links — report those separately and at lower severity.
- **Fix:** fix or remove; add related links to terminal pages.

### E-05 Intrusive interstitial on arrival
- **Mechanism:** a modal between a first-time visitor and the answer they were sent for is the highest-yield bounce cause.
- **Detect:** in the rendered page, look for overlays covering the main content on first load — consent walls, newsletter modals, autoplaying media.
- **Evidence:** the overlay type and whether it blocks the main content.
- **Severity:** high for a blocking overlay; medium for a dismissible one appearing immediately.
- **Do NOT fire when:** it is a small banner not covering the content, or a legally required gate.
- **Fix:** delay or suppress on first visit; never cover the content that answered the query.

### E-06 Load cost on the landing page
- **Mechanism:** slow or shifting pages lose the visitor before they read the answer.
- **Detect:** total transfer weight, render-blocking resources, images without dimensions, largest element timing where measurable.
- **Evidence:** "Landing page transfers 6.2 MB; 14 images lack width/height; 3 render-blocking stylesheets."
- **Severity:** medium; high above roughly 5 MB or with severe layout shift.
- **Do NOT fire on weight alone** for a media-led page where imagery is the content — weigh it against what the page is for.
- **Fix:** name the specific heaviest resources and the specific images missing dimensions.

### E-07 Mobile rendering failures
- **Mechanism:** most AI-referred traffic is mobile.
- **Detect:** viewport meta presence; horizontal overflow at 375 px; base font size; tap-target spacing.
- **Evidence:** "No viewport meta tag; content overflows horizontally by 180 px at 375 px width."
- **Severity:** high if the viewport meta is missing; medium for overflow.
- **Do NOT fire when:** the layout is responsive and does not overflow.
- **Fix:** the specific declaration or rule needed.

### E-08 Broken deep-link anchors
- **Mechanism:** assistants cite `#section` URLs; if the anchor does not exist the visitor lands at the top of a long page with no idea why.
- **Detect:** collect internal anchor links; verify each target id exists; check that headings carry stable ids.
- **Evidence:** "6 of 20 in-page anchor links target ids that do not exist."
- **Severity:** medium.
- **Do NOT fire when:** all anchors resolve.
- **Fix:** stable ids on every heading; fix the broken targets.

### E-09 Accessibility barriers that are also engagement barriers
- **Mechanism:** unreadable is unusable, and the same signals degrade machine parsing.
- **Detect:** text contrast, focus visibility, skip link, landmark roles, form label association.
- **Evidence:** the specific elements and measured values.
- **Severity:** medium.
- **Do NOT fire on decorative elements** or on contrast within images.
- **Fix:** the specific element and the value required.

### E-10 Context loss during the visit
- **Mechanism:** if filters, search state, or partly-filled forms reset on navigation, the visitor restarts and usually leaves instead.
- **Detect:** whether filter and search state is reflected in the URL; whether multi-step forms preserve entries across steps.
- **Evidence:** "Catalogue filters produce no URL change; navigating back resets all selections."
- **Severity:** medium.
- **Do NOT fire when:** state is encoded in the URL or otherwise persisted.
- **Fix:** encode state in the URL — this also makes filtered views crawlable, so it pays twice.

---

# Severity function

Severity is computed, not asserted, so the same site always yields the same result:

```
base      = layer_weight[L1=4, L2=4, L3=3, L4=2, L5=2, ENG=2]
breadth   = affected_pages / sampled_pages          # 0.0–1.0
criticality = 1.0 if the affected fact is primary (what/price/where/contact)
              else 0.6
damping   = 0.6 if corroboration is strong (L4-01 passes with 3+ anchors)
              and the finding is L3/L5, else 1.0     # see O6
score     = base * (0.4 + 0.6*breadth) * criticality * damping
severity  = critical >= 3.4 | high >= 2.4 | medium >= 1.2 | low
```

The damping term encodes observation O6: an obscure brand missing structured data
is in real trouble; a heavily-corroborated reference site missing the same markup
is not. Without it we would report false alarms on exactly the sites that prove
the on-site checks are insufficient on their own.

---

# False-positive register

Traps confirmed against real sites during research. Every one of these is a check
an unguarded audit would fire on, and be wrong.

| Trap | Why it is wrong | Guard |
|---|---|---|
| Low text/HTML ratio | Two heavily-cited panel sites sit at 0.005–0.007 | Absolute raw-text floor plus missing facts (L2-01) |
| Multiple `<h1>` | Valid HTML5; three strong panel sites ship two | Only flag zero H1 (L2-08) |
| Bulk missing alt text | 43/51 and 52/58 on strong sites; nearly all decorative | Only fact-bearing images (L2-03) |
| No JSON-LD | Two of the most-cited sites in the panel have none | Damp by corroboration (L3-01, severity fn) |
| Missing canonical | Fine when there is no duplicate-URL surface | Require a duplicate surface first (L3-06) |
| Blocked in robots | Search/cart/session paths *should* be blocked | Only fact-bearing paths (L1-03) |
| "AI blocked" as a boolean | Blocking is per-agent; one panel site blocks 5 of 14 | Per-agent, split retrieval vs training (L1-01/02) |
| Missing `hreflang` | Meaningless on a single-locale site | Require discovered variants (L3-10) |
| Slow page from one sample | Outliers are common | Median over the sample (L1-08) |
| Missing OG tags | Redundant when description and JSON-LD exist | Advisory only (L3-09) |
