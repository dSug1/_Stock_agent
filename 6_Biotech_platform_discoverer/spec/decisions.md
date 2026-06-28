# 6_Biotech_platform_discoverer — build decisions

Records deviations from `SPEC_acrivon_pattern_screener.md` and the rationale (repo convention:
specs describe the target, decisions record what was built). Newest first.

---

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
