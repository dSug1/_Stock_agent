# 6_Biotech_platform_discoverer — build decisions

Records deviations from `SPEC_acrivon_pattern_screener.md` and the rationale (repo convention:
specs describe the target, decisions record what was built). Newest first.

---

## D7 (2026-06-29) — Keep the $3B ceiling; positives above it are "graduated", not failures (DECIDED)

The D6 seed-eval flagged TNGX ($5.1B) / IDYA ($3.5B) — known positives — deleted at the band ceiling.
**Analyst decision: KEEP the `$50M–3B` band** (spec §0.1). The screen deliberately targets the "early
but real" small-cap window; a positive that has re-rated above $3B has *graduated* and is out of scope
by design, not a screen failure.

Encoded in the harness so the signal stays honest: `seed_eval._disposition` classifies a positive's
Stage-0 deletion — **`graduated`** (cap > ceiling, accepted) vs **`below_floor`** (cap < floor) /
**`not_live`** (genuine losses). `spec_failure` now fires ONLY on genuine losses; graduates are
reported under `graduated_positives` with an ℹ️ note. Live: TNGX/IDYA → graduated, `spec_failure=False`,
`positives_lost_pre_scoring=0`. The flag is preserved for what it's meant to catch — a positive lost
*below* the floor or for liveness — so a real Stage-0 bug would still scream. 150 tests pass.

## D6 (2026-06-29) — Seed-eval validation harness §13 (BUILT)

The trust gate. `seed_eval.py` + `scripts/6_eval.py` score the labeled seed set
(`config/seed_labels.csv`) through the funnel and report **precision / recall + per-stage survival** →
`Outputs/seed_eval.md`, metrics persisted to `run_meta.metrics_json`. Read-only and FREE (no API): it
evaluates whatever scores already exist; it does NOT dispatch scoring.

**Labels** (`config/seed_labels.csv`, analyst-editable): positives = ACRV/TNGX/IDYA/BOLD; borderline
AI-discovery = RXRX/SDGR/RLAY; negatives = CRL/MEDP/ICLR (CROs) + BRKR/A (tools). Three labels, not
two — **borderline** is reported separately and never counted in precision/recall (the store
`seed_labels` table still only accepts positive|negative; borderline is CSV-only).

**Decisions:**
- **Per-seed funnel position is the headline, not just P/R.** For each seed we resolve: deleted at
  Stage 0 (with reason, recovered from the audit log since the row is gone) / absent (never entered) /
  in-store + tier + tagged + has-evidence + scored + composite. The spec's load-bearing check is a
  **positive DELETED at the hard cut** → `spec_failure=True` + a loud banner; that is a Stage-0 bug to
  fix before trusting the run (a positive can only be lost invisibly at Stage 0).
- **Unscored ≠ lost.** A positive that is in-band and retained but simply not scored yet is reported
  `unscored`, NOT a failure (scoring costs money / runs on selected tiers). P/R are computed only over
  pos/neg seeds that HAVE a composite, at `seed_eval.decision_threshold` (0.6). Both a strict
  confusion matrix (TP/FP/FN/TN) and a survival table per group are emitted.
- **`--persist-labels`** optionally writes pos/neg into the store `seed_labels` table; default off
  (eval stays read-only).

Wired into the run `.bat` after Stage 5 (free). 148 tests pass (was 137).

**LIVE FIRST RUN (2026-06-29) — the harness immediately earned its keep.** It flagged a SPEC-LEVEL
FAILURE: positives **TNGX ($5.1B)** and **IDYA ($3.5B)** (+ borderline **RLAY $4.0B**) were deleted at
Stage 0b as `mktcap_out_of_band` — they've re-rated ABOVE the `$50M–3B` ceiling. Precision/recall came
out 1.0/1.0 but over only 1 scored positive (ACRV) + 0 scored negatives (all 5 negatives correctly
deleted out-of-band) — not yet statistically meaningful. **Open analyst decision (handoff PENDING 0b):**
raise `market_cap.max_usd` so graduated positives stay in, vs. accept the band targets the early window
and these are out-of-scope graduates. The band is analyst-authored (§0.1) — left unchanged pending that
call. This is exactly the calibration loop §13 exists for.

**yfinance field fix (part of this batch):** `ipo_date` capture (D4) read the obsolete
`firstTradeDateEpochUtc`; current yfinance uses `firstTradeDateMilliseconds` + `ipoExpectedDate`.
`market._first_trade_date` now tries all three — populated 3/589 before the fix, 589/589 after, which
is what activated the tiers.

## D5 (2026-06-29) — Market-cap × age TIERING upstream of Claude + interactive tiered report (BUILT)

Analyst request: **(1)** move the IPO-date signal UPSTREAM of the Claude call (use it as a filter, not
only the Stage-5 ranking tilt); **(2)** bucket tickers into **4 tiers** by market cap (`$400M`) AND
IPO age (`20yr`): T1 small&young, T2 large&young, T3 large&old, T4 small&old; **(3)** ask which tiers
to score at run time; **(4)** render with the `3_Biopharmcatalyst_parser` report features (tier tabs,
green-highlight + acknowledge checkbox for seen-tracking, collapsible Claude results, new-items filter).

**Built.**
- **`tiering.py`** — `compute_tier(company, config)` → 1-4, or **0 = untiered** when cap OR ipo_date is
  missing. Tier 0 is a deliberate addition (the user named 4 tiers; missing data needs a home):
  recall-safe, never hidden, operator can still select it. Boundary: `cap < threshold` = small,
  `age < threshold` = young (so the threshold value itself counts as large/old). Computed on the fly
  (never persisted) so it always tracks current cap/IPO. Config `tiers.{mktcap_threshold_usd,
  ipo_age_threshold_years}`. `age_years` now lives here; `stage5` imports it (one definition).
- **Stage 4 tier gate** — `_candidates(..., tiers=set)` filters the DUE set by tier BEFORE any API
  call; `due_tier_breakdown()` feeds the prompt. `run(..., tiers=)` carries `selected_tiers` into the
  estimate + dispatch summary + audit. The gate is the IPO-date filter "upstream of Claude."
- **CLI** — `--tiers 1,2|all` or, when omitted, an **interactive prompt** showing due-candidate counts
  per tier and asking which to score; `--yes` with no `--tiers` defaults to all. Aborts cleanly if the
  selected tiers have 0 due candidates.
- **Interactive report** — `render.py` rewritten as a **template + data-sidecar** pair
  (`screener_report.html` hash-stable + `screener_report_data.js` rewritten per run; repo
  html-template-data-split convention). Features ported from 3_: **tier tabs** (T1 default → T4,
  Untiered, All, with counts), **green highlight + acknowledge checkbox** (localStorage
  `pd_screener_acknowledged_v1`, PK = company_id; every company starts NEW until ticked — no
  bootstrap-seed, unlike 3_, because the screener has no "prior batch" to pre-ack), **collapsible
  Claude panel** (memo / axes / moat / mechanisms / disconfirming), **new-items filter** (any / new /
  ack) + ticker search + min-composite + scored-only. Duplicate company-ids collapse via the Stage-5
  shared-signal union so each real company is one row.

**Operational note — tiers need `ipo_date`.** It populates on the next Stage-0b `--enrich-yf`
(yfinance `firstTradeDateEpochUtc`, wired in D4). Until then every company is **Tier 0 (untiered)** —
verified live (581 untiered, the 6 scored among them). The run `.bat` already does `--enrich-yf`, so
the next full run fills the tiers. SEC first-filing fallback for `ipo_date` remains deferred (D4/D2).

**Relationship to D2 lifecycle multiplier:** tiering is now the PRIMARY use of IPO age (a hard
upstream filter the operator controls); the Stage-5 `lifecycle_weight` multiplier stays as an optional,
off-by-default downstream re-ranking tilt. They don't conflict — one gates *what gets scored*, the
other tilts *ordering of what was scored*. 137 tests pass (was 114).

## D4 (2026-06-29) — Stage 5 rank/dedup/export BUILT (+ lifecycle prereq) (BUILT)

The terminal stage (spec §5.7): rank → dedup → refresh review queue → persist run_meta → export the
analyst-house-format shortlist. No API, no deletions. Built as `stage5.py` + `--stage 5` (writes
`Outputs/shortlist.md`). Three sub-decisions:

**(a) Dedup is presentation-layer, not a row deletion.** The cardinal rule (§0.2) forbids deleting a
company for being a duplicate. So Stage-5 dedup collapses duplicate company-ids *only in the ranked
shortlist* — it keeps the best-scored representative and records the others as `merged_ids`; every row
survives in the store (`get_company` still returns them). Verified by test. The primary ADR/dual
collapse still happens earlier in `dedup.collapse` (before rows exist); this is the safety net for the
seed-CSV-name vs SEC-name double-id (ACRV/RXRX).

**(b) Dedup matches on SHARED SIGNALS via union-find, not a single per-row key.** First attempt used a
"strongest identifier per row" key (ISIN → ticker+country → name) — but it FAILED on the live ACRV
case: the seed row carries an ISIN while the SEC row does not, so they landed in different key tiers
(`isin:…` vs `tkr:ACRV@US`) and never merged (RXRX merged only because neither row had an ISIN). Fix:
each row emits a SET of signals {isin, ticker+country | name-when-tickerless}; rows are unioned if they
share ANY signal (transitive). Legal-entity suffixes (Inc/Corp/Ltd/AB/…) are stripped for the name
fallback; industry words (therapeutics/pharmaceuticals) are NOT, so distinct companies sharing a stem
stay separate. Live result: 8 scored rows → 6 shortlist rows (ACRV + RXRX each collapsed).

**(c) Lifecycle age-weighting wired but OFF by default (D2 Lever 2).** `rank_score = composite ×
lifecycle_weight(age)`; weight is 1.0 for age ≤ `old_threshold_years` (the young are never penalized,
rule 3a), decays linearly to `floor` between `old_threshold` and `hard_old_years`, and sits at `floor`
beyond (ship sailed, rule 3b). Unknown age → 1.0 (recall-safe). It NEVER alters the auditable composite
and never deletes — only reorders. **Prereq built:** schema **v3** adds `companies.ipo_date`;
`market.fetch_ticker_info` now reads `firstTradeDateEpochUtc` → ISO date, persisted on the next 0b
enrich (NULL until then). SEC first-filing fallback + market-cap-appreciation discount remain deferred
(D2 phase 2). Config: `stage5.lifecycle.enabled` (false), `stage5.shortlist_top` (25),
`stage5.review.*` thresholds.

**Review-queue refresh (§11):** scored names with `substance_check ∈ {marketing, mixed}` or
high-composite-but-low-confidence are routed back to the review queue for a human look. (On the current
store all 6 trip it — evidence is sparse because OpenAlex 429'd during the test harvest, so confidence
is low; re-harvest will lift confidence and clear most. See handoff PENDING #3.)

114 tests pass (was 98). Render's "Composite scores" section was upgraded to a deduped, lifecycle-aware
**Ranked shortlist** (axes + rank_score) driven by `stage5.rank`.

## D3 (2026-06-28) — Rescore-TTL: don't re-analyse a ticker within a year (BUILT)

Analyst request: once a ticker has been through the Claude scorer, don't run it again for **1 year**
unless reset. Built: `stage4._scored_within` (using `store.last_scored_at`, the MAX score `run_id`,
an ISO timestamp) filters out any company scored within `stage4_scoring.rescore_ttl_days` (default
365) from the candidate set — for both the cost estimate and the dispatch. `--force-rescore` is the
reset (the estimate then counts everything). Verified live: after one 10-ticker run, re-running the
same set drops 13 → 5 due (only the never-scored survive) at an estimated ~$0.05. This is the scoring
analogue of the Stage-2 evidence TTL; it bounds repeat Claude spend.

## D2 (2026-06-28) — Lifecycle / company-age weighting (PROPOSAL; optional, Stage 5)

Analyst intent: **(3a)** don't penalize *young* companies for thin clinical evidence; **(3b)**
deprioritize companies where "the ship has sailed" or that have been in business **> 20 years**;
**(3c)** weigh via IPO date, market-cap appreciation since IPO, etc. Two separate levers:

**Lever 1 — scoring fairness (3a) — BUILT NOW (rubric rule 9).** The Claude rubric is instructed not
to dock an early/recently-public platform for absent late-stage trials: score the *platform*
(A/B/pedigree/validation), and let a low E lower the score only modestly for a clearly young company.
So youth no longer drags the composite down — it's handled where the judgment happens.

**Lever 2 — a lifecycle MULTIPLIER at ranking (3b/3c) — PROPOSED, optional, Stage 5.** A transparent
post-score multiplier: `rank_score = composite × lifecycle_weight(company)`, **off by default**
(`stage5.lifecycle.enabled: false`). It never alters the Claude composite (auditable) and never
deletes a company (recall-safe) — it only reorders. The shape (config-driven):

| Age since IPO | weight | rationale |
|---|---|---|
| `< young_grace_years` (3) | **1.0** | the young get no penalty (3a) |
| `young_grace … sweet_max` (3–12) | **1.0** | the target "early but real" window |
| `old_threshold … hard_old` (15–20) | linear decay 1.0 → floor | maturing |
| `≥ hard_old_years` (20) | **floor** (0.5) | the ship has sailed (3b) |

**Signals (cheap → richer):** (a) **years since IPO** — yfinance `firstTradeDateEpochUtc`, fallback
SEC first-filing date (`data.sec.gov` submissions, oldest); (b) **market cap** (already have — microcap
≈ early, large ≈ mature, a secondary tilt); (c) **trial maturity** (already have from ClinicalTrials —
many Phase 3/4 ≈ late, few/early ≈ young); (d) **market-cap appreciation since IPO** (phase 2 — needs
IPO price + price history; if a name is already heavily re-rated, the upside ship has sailed → extra
discount; if under-appreciated with a strong platform, a small boost).

**Foundation to build first:** capture the age signal — extend `market.fetch_ticker_info` to read
`firstTradeDateEpochUtc` and persist it (schema v3 `ipo_date` column), populated on the next Stage-0b
enrich. Appreciation (phase 2) needs a price-history fetch. Then `stage5.lifecycle_weight(company,
config)` implements the curve, and Stage 5 surfaces both `composite` and `rank_score`.

Config scaffold is in `config.yaml → stage5.lifecycle` (disabled). Full wiring lands with Stage 5.

---

## D1 (2026-06-28) — Skip Stage 3 embedding; score the universe directly with tiered Claude

**Spec §5.5 / §8** put an embedding semantic pre-rank between harvest (Stage 2) and Claude scoring
(Stage 4): build archetype vectors, rank by cosine similarity, keep the top fraction (with a
`min_keep` floor) so the expensive Claude models only touch a cut-down set. **Decision: skip it** and
feed the whole retained universe straight into the tiered Claude scorer.

**Why.** The embedding cut exists to control cost when the candidate set is large. At our scale it
isn't large: the live universe after Stage 0b is **~589 companies**, and a full tiered Claude run over
*all* of them costs **~$2.57** (measured estimate: Haiku triage 589 → $0.65, Sonnet rubric ~294 →
$1.15 via Batch, Opus finalize ~59 contested → $0.77) — versus the `max_usd_per_run: 50` ceiling. The
embedding stage would save cents while adding a build, an embedding dependency, and a recall risk
(a wrong cut is invisible). The cheap **Haiku triage** tier already does the recall-safe first cut,
and it reads the *full* evidence rather than a cosine proxy.

**What we forgo (all minor at this scale):** (1) embedding *retrieval* of top-k evidence chunks —
unnecessary because our bundles are already compact summaries; (2) the `min_keep` recall floor —
moot, since scoring *everything* pre-cuts nothing (recall is higher); (3) semantic dedup / emergent
clustering — a nice-to-have, off the critical path.

**When to revisit.** If the universe grows to thousands (broader regions / looser filters) where a
Haiku pass over everything starts costing real money, or if semantic dedup becomes valuable, build the
embedding pre-rank then. The `embed/` slot in the spec architecture remains open for that.

**Built instead (M5 = Stage 4, spec §9–§10):** Haiku triage (keep/kill, recall-safe, every live
company) → Sonnet full §9.3 rubric over survivors via the Batch API → Opus adversarial re-score on the
contested composite band [0.55, 0.75]. Composite + penalties computed in code (not trusted from the
model). Pre-dispatch cost estimate + `[y/N]` gate + `max_usd_per_run` hard stop. Candidate set = all
live companies (`score_live_excluded: true`) — Stage 1's description-only exclusion does **not** gate
scoring (recall-safe); `ta_tags` feed the bundle as mechanism candidates that D_mechanism refines.
