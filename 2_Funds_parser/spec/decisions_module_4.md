# Decisions Log — Systematic Stock Picker

**Purpose:** Capture the design choices that shaped the module specifications, with rationale. This is what prevents Claude Code (or future-you) from silently drifting during implementation. Every entry is a choice that has alternatives; the rationale explains why the chosen path beat the alternatives.

**Last updated:** 2026-04-22

---

## D1 — Pipeline architecture: modular, stage-gated, API-light

**Decision:** Build as a sequence of independently testable modules (0 through 7), with the Anthropic API called only in Module 5. All other modules are deterministic computation on data.

**Rationale:** Pre-filtering deterministically before reaching the LLM stage is the key cost shape. Paying for intelligence at the final ranking step, not across the full universe.

**Alternatives considered:**
- Monolithic script — rejected for testability and cost control.
- LLM calls earlier in the pipeline (e.g., for filtering) — rejected; deterministic filters are cheaper and auditable.

---

## D2 — "Train hasn't left" expressed as velocity-adjusted dynamics, not static metrics

**Decision:** Rank stocks using rate-of-change across four lookback windows (4w, 12w, 26w, 52w), capturing the *pattern* of movement rather than static distance metrics (distance-from-52w-low, compression, etc.).

**Rationale:** A 20-year quadruple sitting at ATH is structurally different from a 3-week post-IPO stock at ATH. Static distance metrics conflate them. Velocity profiles across multiple horizons distinguish "train hasn't left" (slow-then-accelerating) from "train has left" (fast-and-decelerating or parabolic).

**Alternatives considered:**
- Pure compression (distance from 52w low, volume compression) — rejected: misses time dimension.
- Composite weighted score including compression, fund accumulation, distance metrics — rejected by user: prefers pure trajectory-based ranking. Fund accumulation and compression remain in output for human inspection and Module 5 context, but do not enter the ranking score.

---

## D3 — Ratio matrix formulation over signed returns

**Decision:** Use price ratios R_Xw = price_today / price_Xw_ago (no subtraction of 1), plus the 6 inter-window ratios (R_4/R_12, R_4/R_26, R_4/R_52, R_12/R_26, R_12/R_52, R_26/R_52). Total 10 features per ticker.

**Rationale:** All values strictly positive, so division is safe. Inter-window ratios directly encode "recent vs. medium-term" dynamics as first-class features. Archetype ranges in YAML become intuitive to configure (R_4/R_12 > 1 means "recent faster than medium").

**Alternatives considered:**
- Signed returns r_Xw = (price_today/price_Xw_ago - 1) × 100 / X — rejected by user because subtraction wasn't seen as necessary for ranking purposes, though the real win was the strictly-positive property enabling safe ratio division.
- Ratio without the inter-window matrix — rejected: loses the dynamics information that's the whole point.
- z-scored features across universe — deferred as a possible v2 enhancement; raw ratios used first for simplicity.

---

## D4 — Pattern archetypes over continuous scoring

**Decision:** Define 8 named trajectory archetypes (fresh_awakening, quiet_compression, post_crash_rebase, early_breakout, mature_uptrend, parabolic_blowoff, broken_trend, sustained_decline), each specifying ranges on a subset of the 10 features. Match tickers to archetypes by counting range hits; composite_score = archetype_score × match_confidence.

**Rationale:** Archetypes directly encode the investment thesis. Output is interpretable: "this stock is classified as post_crash_rebase with 85% confidence." Tunable by editing ranges in YAML. Handles the 20-year-at-ATH case naturally (classifies as mature_uptrend or parabolic, both low-scored).

**Alternatives considered:**
- Range-based scoring per window (bucket each r value, sum points) — rejected: doesn't explicitly reward cross-window patterns.
- Ratio-based scoring (score raw ratios R_4/R_12 etc. directly) — rejected: fragile when denominators approach zero (though resolved by using the positive-ratio formulation, the archetype framing was preferred for interpretability).
- Continuous weighted composite of all 10 features — rejected: harder to tune, outputs not interpretable.

---

## D5 — Archetype ranges specify only relevant dimensions

**Decision:** Each archetype YAML entry specifies only the ranges it cares about. Match confidence = matched / specified, not matched / total 10 dimensions.

**Rationale:** Avoids forcing every archetype to fill in defensive ranges for all 10 features. A parabolic_blowoff only needs to specify R_4, R_4/R_12, R_4/R_52 — it doesn't care about R_26/R_52.

**Alternatives considered:**
- All archetypes must specify all 10 ranges — rejected: verbose, brittle, defeats the point of having archetypes.

---

## D6 — Module 4 split into 4a (filters) and 4b (price history + ranking)

**Decision:** Module 4a runs hard filters on snapshot data only. Module 4b fetches price history for survivors only, then computes ratios and archetype matches.

**Rationale:** Pulling full price history for thousands of tickers only to reject most is wasteful. Survivor count typically 10-20% of starting universe. Also enables cheap daily updates: survivors list is stable between quarters, daily runs just fetch the last N trading days per survivor.

**Alternatives considered:**
- Single Module 4 with internal two-phase logic — rejected: less modular, harder to re-run filters independently.
- Merge filters into Module 3 — rejected: conflates enrichment (adding data) with filtering (removing rows).

---

## D7 — Price history stored in SQLite (`prices.db`)

**Decision:** Use SQLite for daily OHLCV data, keyed by (ticker, date). Separate `price_fetch_log` table tracks per-ticker fetch status for incremental updates.

**Rationale:** Persistent across runs. Incremental updates trivially cheap (fetch only from last_date+1 to today). Good query performance for time-series lookups. No external DB to manage.

**Alternatives considered:**
- Parquet files per ticker — rejected: no efficient incremental append.
- Postgres — rejected: overkill, operational overhead.
- In-memory with daily re-download — rejected: wasteful yfinance calls.

---

## D8 — Adjusted close for all ratio computations

**Decision:** Always use `adjusted_close` (split- and dividend-adjusted) for ratio computations. Store both `close` and `adjusted_close` in SQLite for flexibility.

**Rationale:** Splits would otherwise create artifact ratios (e.g., R_4 = 2.0 from a 2-for-1 split is not a real price appreciation).

**Alternatives considered:**
- Raw close + manual split adjustment — rejected: reinventing what yfinance provides.

---

## D9 — Flat-fill for tickers with <52 weeks of history

**Decision:** For windows predating the ticker's first available price, set R_Xw = 1.0 (flat) and flag with `young_ticker_flag = True`. Also expose a `require_min_history_weeks` hard filter (default 12 weeks) to exclude extremely young tickers entirely.

**Rationale:** Flat-fill lets young tickers still be scored by available windows without excluding them wholesale. The `young_ticker_flag` preserves information for downstream review.

**Alternatives considered:**
- Exclude all tickers with <52w history — rejected: would miss recent IPOs which are a legitimate category of interest.
- Separate scoring track for young tickers — rejected: added complexity without clear benefit yet; can be added in v2.

---

## D10 — Full universe ranked; Module 5 top-N selected separately

**Decision:** Module 4b ranks all survivors. Module 5 operates on a user-configurable top-N slice. User can re-rank Module 4b without re-running Module 5, and can re-run Module 5 with different N.

**Rationale:** Separates the cheap deterministic step from the expensive LLM step. Enables tuning archetype ranges without burning API budget.

**Alternatives considered:**
- Module 4b outputs only top-N — rejected: forces Module 5 re-runs whenever ranking is tuned.

---

## D11 — Composite weights / archetype scores exposed in config YAML

**Decision:** All tunable parameters (filter thresholds, archetype ranges, archetype scores, min_confidence, tiebreakers) live in `config/*.yaml` files. Code reads them at runtime.

**Rationale:** User tunes pipeline without code edits. Version control on configs tracks tuning history.

**Alternatives considered:**
- Hardcoded defaults with CLI overrides — rejected: doesn't scale to 14 archetypes × up to 6 ranges each.

---

## D12 — Outcome tracking (Module 7) is advisory, not auto-updating

**Decision:** Module 7 records forward price paths of scored tickers in `outcomes.db`, produces analysis reports showing archetype performance and Module 5 calibration, and *suggests* archetype score adjustments. It does not auto-modify YAML configs.

**Rationale:** Auto-adjusting archetype weights from Module 5 output would compound model opinions into a shared worldview with no external anchor. Outcome tracking against realized market returns is the honest feedback loop. User retains control over whether to apply suggested adjustments.

**Alternatives considered:**
- Auto-tune archetype weights from Module 5 score distributions — rejected: trains Module 4 on Module 5's beliefs, not on reality.
- No feedback loop — rejected: would miss learning from realized performance.
- RL-style automated tuning — deferred: far too complex for this stage; human-in-the-loop adjustment is the right starting point.

---

## D13 — yfinance as price source, with adapter-ready structure

**Decision:** yfinance is the initial price data source. Code structured so the fetch function is pluggable (`fetch_incremental_prices(..., source="yfinance")`) to allow future substitution (Polygon, Alpha Vantage) without architectural changes.

**Rationale:** Free, sufficient for initial development and calibration. Known to be sometimes flaky; the SQLite cache + retry logic mitigates this. Can swap to paid source once workflow is validated.

**Alternatives considered:**
- Polygon.io — deferred: paid, save until yfinance proves insufficient.
- Multi-source adapter from day one — rejected: premature abstraction; single source is fine until a second one is actually needed.

---

## D14 — LLM outputs structured (upside_pct + time_weeks) separately, not as computed rate

**Decision:** Module 5's Claude prompt returns `upside_pct` and `time_to_catalyst_weeks` as separate fields in structured JSON. The composite score (upside_pct / time_weeks) is computed deterministically in Python.

**Rationale:** LLMs anchor poorly on compound rates. Asking the model to "rank by %/week directly" invites hallucinated precision. Asking for two independently-estimable quantities and computing the ratio ourselves is more reliable.

**Alternatives considered:**
- Have the LLM return a composite score directly — rejected for the anchoring reason above.

---

## D15 — Cost optimization stacked: batch API + prompt caching

**Decision:** Module 5 uses Anthropic's Batch API (50% discount) with prompt caching (~90% discount on cached prefix tokens). Prompt is structured with a stable cacheable prefix (framework, scoring rubric, JSON schema, few-shot examples) and a variable suffix (ticker-specific data).

**Rationale:** Confirmed via Anthropic docs that batch and caching discounts stack. For ~200 tickers per quarterly run, expected cost drops from ~$400+ naive to ~$5-10 excluding web search tool fees. Quarterly 13F workflow easily tolerates batch API's 24-hour SLA.

**Alternatives considered:**
- Sync calls — rejected: 2× more expensive with no benefit given quarterly cadence.
- Caching without batch — rejected: leaves 50% savings on the table.

---

## D16 — Pre-flight cost estimator required before every batch submission

**Decision:** Module 5 computes expected cost before submitting the batch and requires user approval to proceed. Cost breakdown shown under three scenarios (no optimization / cache only / cache + batch) so savings are visible.

**Rationale:** Cost control is a first-class concern. Surprise bills are unacceptable. Explicit approval gate prevents runaway costs when filter tuning accidentally lets 1000 tickers through.

**Alternatives considered:**
- Silent submission with post-hoc cost reporting — rejected: too easy to misconfigure.

---

## D17 — Deterministic output ordering with explicit tiebreakers

**Decision:** All ranked outputs have explicit, documented tiebreakers (composite_score desc, then fund_count desc, then ticker alphabetical). Running the pipeline twice on the same inputs produces byte-identical Parquet on sorted data.

**Rationale:** Reproducibility. Also: stable ordering makes git diffs on committed reports meaningful.

**Alternatives considered:**
- Implicit pandas default sort — rejected: pandas sort stability varies by version.

---

## D18 — First-failure-wins rejection logging

**Decision:** When a ticker fails multiple hard filters, only the first-failing filter is recorded as `rejection_reason`. Priority order is documented in Module 4a spec.

**Rationale:** Keeps rejection logs concise and interpretable. If filter order is meaningful (e.g., price filter before market cap filter), the first failure is the most informative.

**Alternatives considered:**
- Log all failing filters per ticker — rejected: noisier output for marginal value.

---

## D19 — Unique integer archetype scores (disjoint composite-score bands)

**Decision:** Every archetype in `archetypes.yaml` must have a **unique integer `score`**. With `alpha = 0.7` and `min_confidence = 0.70`, this guarantees the composite-score ranges of different archetypes never overlap, because the multiplier band `[alpha + (1-alpha)·min_conf, 1]` = `[0.91, 1.00]` is narrower than 1 unit of score for scores in `[−10, +10]`.

**Rationale:** Two archetypes with the same score produce identical composite ranges — a ticker's score class becomes ambiguous whenever confidence happens to align. Keeping scores unique makes the composite score monotonically partition tickers into named classes, so downstream Module 5/6 consumers can treat the score as a reliable bucket identifier. It also means calibration changes that move an archetype's score can be validated by a single invariant (the "all scores unique" check) rather than re-deriving band overlaps by hand.

**Consequence:** When tweaking scores in a calibration pass, a retune cannot collide with an existing score. Example: softening `broken_trend` from −5 cannot use −3 (taken by `sustained_decline`); it must use −4 (or −2, −6, …). This was the constraint that drove pass 4's score choices (`quiet_compression` → +3, `broken_trend` → −4).

**Bounds.** The gap between adjacent integer-score bands at α=0.7 equals `0.91·s_high − s_low` (positive numerator). For `s_high − s_low = 1`, the gap shrinks as the score magnitude grows:

| s_low | s_high | gap |
|---:|---:|---:|
| 6 | 7  | 0.37 |
| 7 | 8  | 0.28 |
| 8 | 9  | 0.19 |
| 9 | 10 | 0.10 |
| 10 | 11 | 0.01 |
| 11 | 12 | −0.08 (OVERLAP) |

Current minimum gap (post pass 5) is **0.10**, between `fresh_awakening` (+10) and `deep_base_breakout` (+9). Scores outside `[−10, +10]` are prohibited without first lowering α or raising `min_confidence`.

**Alternatives considered:**
- Allow duplicate scores, treat overlaps as tied — rejected: fragile, consumers downstream would have to special-case ties on composite_score rather than trusting it as a partition.
- Use non-integer scores (e.g. 2.5, 5.5) — rejected: harder to read, no practical benefit over picking a different integer.
- Expand the score range beyond ±10 — rejected without an α re-design: at α=0.7 even `+11` collides with `+10` (gap = 0.01) and `+12` overlaps outright. If more than 21 distinct archetype classes are ever needed, lower α first.

---

## Open / deferred decisions

These are acknowledged as unresolved. Claude Code should not invent defaults; it should flag them for explicit user input.

- **Fund list to track.** The entire pipeline's signal quality depends on which funds feed Module 1. Needs explicit user selection (specialist biotech: Baker Bros, RA Capital, Perceptive; activist; generalist small-cap; etc.).
- **Sector focus.** Whether to hardcode a biotech/healthcare filter or keep universe generalist. Currently exposed as `sector_allowlist` in `filters.yaml` with default `null` (all sectors).
- **Calibration ticker list.** TCRX, BCYC, MRNA are starting references. Full list with as-of dates and expected archetypes to be built in `config/calibration.yaml`.
- **Web search budget per ticker in Module 5.** How many searches per ticker, max tokens per search, which domains to allow. Affects cost meaningfully; to be specified with Module 5.
- **Output report format.** HTML + Excel confirmed; specific columns, color schemes, interactive features TBD with Module 6.
- **Quarterly vs. on-demand cadence.** Pipeline architected for quarterly 13F runs, but daily price updates to SQLite are supported. Whether to run full re-ranking daily or only quarterly is a workflow choice.
- **Storage for outcome tracking: SQLite vs CSV.** Module 7 spec uses SQLite; could downgrade to CSV for simplicity if preferred.