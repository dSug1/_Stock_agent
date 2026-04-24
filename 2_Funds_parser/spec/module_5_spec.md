# Module 5 — Market Data Enrichment (pre-LLM context)

**Status:** Draft specification (2026-04-24, revised — SQLite-only store). Awaiting user approval before implementation.
**Last updated:** 2026-04-24
**Runtime:** Deterministic assembly. SQLite-only write. **No external network.** (All market data comes from `data/prices.db`, populated by Module 4.)

Module 5 consumes Module 4b's dual-horizon ranked candidates and assembles a **structured context pack per ticker** (for every row Module 4b produces — 1,423 on current 2025Q4 data) that Module 6's LLM will score. The context pack contains the objective evidence the LLM cannot derive on its own — fund accumulation story, price trajectory numbers, ratio matrix, liquidity, snapshot fundamentals — organised into **two horizon-scoped sections** so the LLM can reason about 3-month and 12-month appreciation in parallel.

**No shortlist gate in Module 5** — see D21, D27, and D28. The `composite_best` threshold, the sector allowlist, and the final max-market-cap filter that eventually control which packs get LLM-scored all live in Module 6 config, not here. This keeps Module 5 a pure prep step and lets all three filters be set *after* Module 6's per-ticker cost is known.

**Module 5 does not call any external API.** Catalyst/news research is the LLM's job in Module 6 via the Anthropic `web_search` tool. This keeps Module 5 deterministic, idempotent, and cacheable; and it sidesteps the `project_data_provider_switch` memory rule (no yfinance, no Tavily/Serper before public deploy).

---

## Downstream contract from Module 4b (do not re-litigate)

Module 5 reads `_intermediate_outputs/ranked_candidates_{quarter}.parquet` with the dual-horizon schema locked in Module 4 pass 8 (D20). The following must be honoured:

1. **Both horizons are evaluated.** `best_horizon` is a **research-depth hint** — it biases how much narrative emphasis each section of the context pack gets — but it does **not** gate inclusion. Every ticker receives a full `near_term_3mo` section *and* a full `long_term_12mo` section.
2. **Final ranking is by rate-of-appreciation.** Module 6 computes `rate_H = expected_appreciation_H / H_months` per ticker per horizon, then picks `final_horizon = argmax_H(rate_H)`. Module 5's job is to give the LLM enough context to estimate `expected_appreciation_H` well at both horizons.
3. **The pattern-score delta between horizons is small.** `score_3mo` vs `score_12mo` differ by 0–2 points, always same-sign except for `extended_uptrend` (−1/+1). The pattern prior surfaces the ticker; the LLM's appreciation estimate dominates. Module 5 must not collapse the two horizons into one.
4. **No shortlist filtering in Module 5 (D21, D27, D28).** All 1,423 ranked rows receive packs, including `unclassified`, all sectors, and every market cap up to the Module 4a fetch ceiling ($10B). The composite-score threshold (D21), sector allowlist (D27), and max-market-cap filter (D28) are all Module 6's concern, deferred until per-ticker LLM cost is known.

---

## Storage layout

| Path | Purpose | Lifetime |
|---|---|---|
| `2_Funds_parser/context_packs.db` | **Primary store.** SQLite with one table (`context_packs`) keyed by `(ticker, quarter, pack_version)`. Holds every pack ever built across quarters. Module 6 reads from here directly. | Cross-run durable cache. Gitignored via `*.db`. |
| `2_Funds_parser/_intermediate_outputs/pack_exclusions_{quarter}.parquet` | Audit. Empty by default (no filtering in D21). Populated when `denylist_tickers` is set, or when a ticker fails to build. Columns: `ticker`, `reason` (`denylist` \| `build_error`), `detail`. | Per-run. |
| `2_Funds_parser/Outputs/enrichment_report_{quarter}.html` | User-facing summary: pack count, archetype histogram, best_horizon split, composite_best distribution, cache efficiency, sample pack preview. | Per-run. |
| `2_Funds_parser/logs/module_5_{quarter}.log` | Structured log: one line per ticker processed (cache hit / built / refreshed / errored). | Per-run. |

No JSON files, no Parquet index: the SQLite table is simultaneously the output, the cache, and the index. Filterable columns (`archetype`, `best_horizon`, `composite_best`, `score_3mo`, `score_12mo`) are lifted out of the JSON blob into indexed columns so Module 6 can run `WHERE composite_best >= 6 AND best_horizon = '3mo'` natively. `context_packs.db` sits next to `2_fundparser.db` at the project root — the existing pattern for SQLite stores in this module.

Folder conventions follow the pipeline-wide rule (`Outputs/`, `_intermediate_outputs/`, `data/` — see [decisions.md § Output folders](decisions.md)).

---

## Inputs

- **`_intermediate_outputs/ranked_candidates_{quarter}.parquet`** (Module 4b output, 54 columns). Required.
- **`data/prices.db`** — read-only in Module 5. `ticker_snapshot`, `prices`, `price_fetch_log` populated by Module 4a/4b. Module 5 must not write here.
- **`config/pipeline.yaml`** — quarter, paths, logging (via Module 1).
- **`config/enrichment.yaml`** *(new)* — Module 5's own config (schema in § Config schema).
- **`context_packs.db`** — primary store; created on first run. Cross-run durable.

---

## Outputs

- **`context_packs.db`** — primary. One row per `(ticker, quarter, pack_version)` in the `context_packs` table (schema in § SQLite schema). Module 6 queries this table to assemble its LLM prompts.
- **`_intermediate_outputs/pack_exclusions_{quarter}.parquet`** — audit. Usually empty. Columns: `ticker`, `rank`, `archetype`, `composite_best`, `reason` (`denylist` | `build_error`), `detail` (free-text).
- **`Outputs/enrichment_report_{quarter}.html`** — user-facing summary. Sections:
  - Counts: total Module 4b rows → denylist excluded → build errors → packs written.
  - Archetype distribution across all packs.
  - `best_horizon` split (12mo / 3mo / equal).
  - `composite_best` histogram (so the user can eyeball future threshold choices — D21).
  - **Sector + industry histogram** (so the user can choose the Module 6 allowlist — D27). Clickable bars that show the ticker list per sector.
  - **Market-cap histogram** (so the user can choose the Module 6 cap — D28). Same buckets as Module 4a's prompt: `<$100M`, `$100M–$500M`, `$500M–$1B`, `$1B–$2B`, `$2B–$3.7B`, `$3.7B–$5B`, `$5B–$7.5B`, `$7.5B–$10B`.
  - Cache efficiency (% `cache_hit` vs `refreshed` vs `built`).
  - First 10 packs rendered as collapsible previews (default — see `reports.preview_count`).

---

## Design decisions (settled for v1; each tunable in `enrichment.yaml`)

These are the six items the handoff flagged as open. Defaults below are the starting point; all surface in `enrichment.yaml` so calibration is YAML-only.

### D21 — No shortlist in Module 5; enrich every Module 4b row

Module 5 builds a pack for **every ticker in `ranked_candidates_{quarter}.parquet`** (1,423 rows on 2025Q4 data). No threshold, no top-N cap, no unclassified exclusion by default.

**Rationale.** The threshold was always a Module 6 LLM-cost knob. Module 5 itself is free (no external calls, deterministic, local SQLite write) — running on 1,423 vs 300 tickers is negligible in wall time (~30 s cold / 2–3 s warm) and blob storage (~3–6 MB total). Pushing the gate down into Module 5 would force re-runs every time the threshold changes; keeping every ticker enriched means the threshold becomes a one-line `WHERE composite_best >= ?` in Module 6 at query time.

**DEFERRED — pending Module 6 cost estimate.** Once Module 6 is designed and its per-ticker LLM cost is known, the user will set a `composite_best` threshold that gates which tickers actually get scored. That threshold lives in Module 6 config, not Module 5 config. When that decision lands, revisit this section — we may then choose to promote the threshold back into Module 5 (so prep-work also skips) or keep it at the Module 6 boundary.

**Safety valve available today:** `enrichment.denylist_tickers` (explicit ticker list to exclude regardless of score). Useful for one-off "don't waste prep on this" cases. No other gates by default.

**Unclassified tickers (65 on current data)** are included by default. Their pack is near-empty in the `archetype_verdict` section (`archetype="unclassified"`, `score_*=0`) but the market-snapshot, price-trajectory, and fund-accumulation sections are still populated. Module 6 can filter with `WHERE archetype != 'unclassified'`.

### D22 — Catalyst search: deferred to Module 6's LLM web_search tool

Three options were considered: (a) Anthropic `web_search` invoked by the LLM inside Module 6; (b) a pre-fetch layer in Module 5 using a direct search provider; (c) a paid curated feed (Tavily / Serper). Recommendation: **(a)**.

Rationale:
- Module 5 stays deterministic and offline. Reruns are idempotent. Cache invalidation is trivial (quarter boundary).
- The LLM is better at deciding *what* to search per ticker than a generic fetcher (e.g. biotech tickers need FDA-calendar queries, commodities tickers need catalyst-month keywords).
- No new API key, no new billing line, no new rate-limit failure mode in Module 5.
- Cost control stays concentrated in Module 6's pre-flight cost estimator (D16).

Module 5 therefore **builds the context pack entirely from local data** (`ranked_candidates`, `prices.db`). Module 6 owns all external enrichment via tool use.

### D23 — Context pack format: SQLite row with structured JSON blob + indexed filter columns

Every pack is a row in `context_packs` (see § SQLite schema). The row has:
- Indexed top-level columns for filtering: `ticker`, `quarter`, `pack_version`, `archetype`, `best_horizon`, `composite_best`, `score_3mo`, `score_12mo`.
- A `pack_json` TEXT column holding the full structured pack as JSON — keyed fields for deterministic sections plus a free-text `narrative_summary` per horizon section.

Module 6 prompts construct the user message by:
1. Running a `SELECT pack_json FROM context_packs WHERE quarter = ? AND composite_best >= ?` query.
2. `json.loads(row.pack_json)` to get the structured pack.
3. Serialising the sections it needs into the prompt, injecting narrative summaries verbatim.

Rationale:
- **One store, one representation.** No JSON directory + Parquet index + cache DB triad to keep in sync.
- **Matches pipeline convention.** `2_fundparser.db` (Modules 2/3) and `prices.db` (Module 4) are both SQLite. Adding a third is less surprising than introducing a new per-file layer.
- **Queryable.** Filtering by archetype, horizon, score, or quarter is a SQL WHERE; no pandas load-and-filter round-trip.
- **Atomic.** A single transaction per run guarantees all-or-nothing writes; a half-finished run leaves the DB coherent (old rows still intact, stale by hash).
- **Stable schema via `pack_version`.** A single `pack_version` string (semver-ish: `"m5-v1"`) lets Module 6 refuse mismatched packs cleanly and invalidates the entire cache on a schema bump.

Full blob schema in § Context pack JSON schema.

### D24 — Caching: SWR on insert, store *is* the cache

There is no separate cache DB. `context_packs.db` is both output and cache. The `(ticker, quarter, pack_version)` PRIMARY KEY and `source_rank_hash` column implement stale-while-revalidate on re-run:

1. Compute `source_rank_hash = sha1(<row slice from ranked_candidates that feeds the pack>)`. Only the fields that actually appear in the pack are hashed; cosmetic column reorders in ranked parquet do not invalidate.
2. `SELECT source_rank_hash FROM context_packs WHERE ticker=? AND quarter=? AND pack_version=?`.
3. If row exists AND hash matches: **cache hit** — no rebuild, only `cache_status = "cache_hit"` recorded for the run summary.
4. If row exists AND hash differs: **refresh** — rebuild pack, `INSERT OR REPLACE`, set `cache_status = "refreshed"`.
5. If no row: **build** — build pack, INSERT, set `cache_status = "built"`.

The memory rule `feedback_swr_pattern` says: render cached value immediately then refresh in background. Module 5's equivalent: same-day reruns skip the JSON rebuild for unchanged tickers (cheap SELECT), and the hash check rebuilds only what actually changed. There is no async layer — Module 5 is synchronous — but the principle (serve cache, validate, rebuild only when source changed) is honoured.

CLI overrides:
- `--force-refresh` rebuilds every pack (still upserts fresh rows).
- `--no-cache` rebuilds AND skips upserts (for one-off debug runs that should not pollute the store).

Old quarters and old `pack_version` rows persist in the DB indefinitely — they cost nothing and make historical pack comparisons trivial. A future `scripts/5_compact_cache.py` can prune on demand; not needed in v1.

### D25 — Research-emphasis allocation: best_horizon → (0.7, 0.3) / (0.5, 0.5)

Every pack emits two numbers: `research_emphasis_3mo` and `research_emphasis_12mo`, summing to 1.0.

| `best_horizon` | emphasis_3mo | emphasis_12mo |
|---|---:|---:|
| `3mo`  | 0.7 | 0.3 |
| `12mo` | 0.3 | 0.7 |
| `equal`| 0.5 | 0.5 |

Module 6 reads these fractions and allocates search-tool budget accordingly (e.g. 7 near-term catalyst queries and 3 long-term queries for a `3mo`-best ticker). Weights are only a hint — Module 6 may round or override with user-level knobs. The 70/30 ratio is the v1 default; tunable via `enrichment.emphasis_weights`.

### D26 — yfinance off-ramp: Module 5 calls nothing

Module 5 reads `data/prices.db` only. It does **not** import `yfinance`. The snapshot TTL enforcement and any yfinance fetches already happened in Module 4a/4b. This makes Module 5 compatible with the `project_data_provider_switch` off-ramp: when Twelve Data replaces yfinance pre-public, Module 4 swaps its fetcher and Module 5 is untouched.

Any future fundamentals enrichment (forward EPS, analyst targets, earnings calendar) that would require an external call is **out of scope for v1** and documented in § Open items deferred to v2.

### D27 — Sector selection deferred to Module 6

Module 5 enriches every sector present in the Module 4b output; it does **not** filter by `sector` or `industry`.

**Rationale (same logic as D21).** Sector filtering is a cost-control / focus decision, not a prep decision. Module 5 is free to run; rebuilding packs when the user narrows the sector focus would be wasteful. Lifting `sector` and `industry` into indexed columns of the `context_packs` table (see § SQLite schema) means Module 6 can filter with a native SQL `WHERE sector IN (...)` or `WHERE industry = ?`, no JSON parsing needed.

**DEFERRED — user to define the allowlist before Module 6 runs.** Although the universe is sourced from 21 biotech/healthcare specialist funds, the funds do hold adjacent sectors (tools, diagnostics, pharma services, occasional non-healthcare names). Before the first Module 6 run the user will decide which sectors / industries are in scope — likely biotech + pharma + healthcare tools, possibly with a manual denylist for mismatches — and set the allowlist in Module 6 config.

**Hooks reserved in `enrichment.yaml`** (see § Config schema): `selection.sector_allowlist: null`, `selection.industry_allowlist: null`, `selection.sector_blocklist: []`, `selection.industry_blocklist: []`. All currently inactive; if the user later prefers Module 5 to skip prep for out-of-scope sectors (e.g. saves cache disk, shortens HTML report), the gate can be activated with a one-line config change.

**Visibility.** The HTML report breaks out the sector histogram across all built packs so the user can see what's in the cache before deciding which slice to LLM-score.

### D28 — Final max market cap deferred to Module 6

Module 4a enforces two caps: a hard **fetch ceiling** ($10B, governing what enters the candidate pool and gets snapshot-cached) and a **user cap** applied at survivors time (currently $10B — i.e. the ceiling — on 2025Q4 data; the 1,446 survivors feeding Module 4b are everything up to $10B). Module 5 inherits whatever Module 4a produced; it does **not** apply a further market-cap cut.

**Rationale (same pattern as D21 / D27).** The final max cap is a focus / cost-control decision, not a prep decision. Because Module 4a's snapshot cache is keyed on the fetch ceiling, lowering the final cap at any point below $10B costs zero Yahoo calls — re-running Module 4a with a tighter user cap is free. But even that is unnecessary if Module 6 simply filters at query time: every pack already carries `market_cap` in `identity → market_snapshot`, and we lift it into an indexed column below so `WHERE market_cap <= ?` is a native SQL predicate.

**DEFERRED — user to set the max market cap before Module 6 runs.** The current 2025Q4 cache holds everything up to $10B. Before the first Module 6 run the user will pick the cap that defines the actual investable universe — historically the YAML default was $3.7B, but the user signalled this is a per-run decision they want to make after seeing Module 6 cost.

**Hook reserved in `enrichment.yaml`**: `selection.market_cap_max_usd: null` (plus a symmetric `market_cap_min_usd: null` in case the user ever wants to tighten the $50M floor Module 4a already enforces). Currently inactive — activating either is a one-line config change.

**Interaction with Module 4a.** If the user eventually wants the Module 4a `survivors_{quarter}.parquet` to also respect this cap (e.g. to shrink the `ranked_candidates_{quarter}.parquet` feeding Module 5), they re-run Module 4a with `--market-cap-max-usd <value>`. This is the existing Module 4a prompt flow; D28 does not change it. D28 is strictly about the **Module 5 → Module 6** boundary.

**Visibility.** The HTML enrichment report adds a market-cap histogram (same buckets as Module 4a's prompt: `<$100M`, `$100M–$500M`, `$500M–$1B`, `$1B–$2B`, `$2B–$3.7B`, `$3.7B–$5B`, `$5B–$7.5B`, `$7.5B–$10B`) so the user can choose the Module 6 cap from real numbers.

---

## Algorithm

### Step 0 — Load and select

1. Load `ranked_candidates_{quarter}.parquet`. Validate expected columns (`composite_best`, `best_horizon`, `score_3mo`, `score_12mo`, `archetype`, all 10 ratio columns, `price_today`, snapshot columns). Fail loudly on schema mismatch.
2. Apply the only filter that exists in v1: `enrichment.denylist_tickers` (default empty) — excluded rows go to `pack_exclusions_{quarter}.parquet` with `reason="denylist"`.
3. Every other row proceeds to Step 1. On current 2025Q4 data that is 1,423 tickers. No threshold, no top-N cap, no unclassified exclusion — per D21 (see "DEFERRED" note: this is the hook where a Module-6-driven `composite_best_min` will be added once LLM cost is estimated).

### Step 1 — Build per-ticker context pack

For each ticker from Step 0:

1. **Cache probe** (D24). Compute `source_rank_hash`. Query `context_packs.db`. If hit (row exists, `pack_version` matches, hash matches), mark `cache_status = "cache_hit"` and skip the rebuild.
2. **Assemble deterministic sections** from the ranked row + `prices.db`:
   - `identity`: `ticker`, `name_of_issuer`, `cusip`, `quarter`, `sector`, `industry`, `exchange`.
   - `market_snapshot`: `market_cap`, `shares_out`, `last_close`, `adv_30d`, `price_today`, `currency`, `snapshot_fetched_at`.
   - `price_trajectory`: full ratio matrix + source dates + `weeks_of_history_used`. `young_ticker_flag` if present.
   - `fund_accumulation`: `fund_count`, `total_shares`, `total_market_value`, `qoq_share_change`, `qoq_fund_count_change`, `new_positions`, `increased_positions`, `decreased_positions`, `exited_positions`. Plus flags (`ticker_is_verified`, `has_prefunded_warrants`, `has_regular_warrants`, `has_options`, `has_unknown_class`).
   - `archetype_verdict`: `archetype`, `match_confidence`, `score_3mo`, `score_12mo`, `composite_3mo`, `composite_12mo`, `best_horizon`, `composite_best`.
3. **Compute two horizon sections** (`near_term_3mo`, `long_term_12mo`). Each section carries:
   - `horizon_months`: 3 or 12.
   - `pattern_score`: `score_3mo` or `score_12mo`.
   - `composite`: `composite_3mo` or `composite_12mo`.
   - `research_emphasis`: from D25 weights.
   - `entry_price_context`: current-price distance to the relevant window's anchor (3mo → `price_source_date_12w` price; 12mo → `price_source_date_52w` price). Includes `pct_off_anchor`, `pct_off_52w_low` (computed from the 400-day history in `prices.db`), `pct_off_52w_high`.
   - `narrative_summary`: 2–4 sentence natural-language description of what this horizon should focus on. Template per archetype — e.g. for `deep_base_breakout` at 3mo: "Stock crashed ~40–50% earlier in the year, built a base, and is breaking out now. Over the next 3 months, validate whether the base-to-breakout structure is confirmed by volume and fund flow." For the 12mo section of the same ticker: "Over 12 months, assess whether the base-breakout marks a Stage 2 transition or a false start. Catalysts that convert the pattern: new contracts, pipeline advancement, sector rotation." Templates live in `config/enrichment.yaml` and can be edited without code changes.
4. **Pack assembly.** Compose JSON per blob schema below. `INSERT OR REPLACE` into `context_packs` (single transaction covering all tickers at end of run).

### Step 2 — Write exclusions audit + HTML report

After all packs are built and committed:

1. Write `_intermediate_outputs/pack_exclusions_{quarter}.parquet` (from Step 0 + any per-ticker build failures captured during Step 1).
2. Render `Outputs/enrichment_report_{quarter}.html` — queries `context_packs.db` for the current quarter to produce the histogram / preview / cache-efficiency sections.
3. Log summary line to the module log (counts by `cache_status`, wall time, DB size delta).

Module 5 processing is O(n) in the Module 4b row count. On current 2025Q4 data (1,423 tickers) wall time target is **under 30 seconds** cold, **under 3 seconds** warm (full cache hit). No network.

---

## SQLite schema (`context_packs.db`)

Single table. `pack_version` bumps on any blob-schema change; `source_rank_hash` drives per-ticker invalidation.

```sql
CREATE TABLE IF NOT EXISTS context_packs (
    ticker            TEXT NOT NULL,
    quarter           TEXT NOT NULL,          -- 'YYYYQn'
    pack_version      TEXT NOT NULL,          -- e.g. 'm5-v1'
    source_rank_hash  TEXT NOT NULL,          -- sha1 of the ranked-row subset that feeds the pack

    -- Indexed routing / filter columns (lifted from the blob so Module 6 can WHERE on them)
    archetype         TEXT,
    best_horizon      TEXT,                   -- '3mo' | '12mo' | 'equal'
    composite_best    REAL,
    composite_3mo     REAL,
    composite_12mo    REAL,
    score_3mo         INTEGER,
    score_12mo        INTEGER,
    match_confidence  REAL,
    sector            TEXT,                   -- D27: Module 6 filters with WHERE sector IN (...)
    industry          TEXT,                   -- D27: finer-grained filter if needed
    market_cap_usd    INTEGER,                -- D28: Module 6 filters with WHERE market_cap_usd <= ?

    -- The full structured pack (JSON TEXT)
    pack_json         TEXT NOT NULL,

    -- Run metadata
    cache_status      TEXT NOT NULL,          -- 'built' | 'cache_hit' | 'refreshed'
    built_at          TEXT NOT NULL,          -- ISO UTC of the most recent build

    PRIMARY KEY (ticker, quarter, pack_version)
);

CREATE INDEX IF NOT EXISTS idx_packs_quarter_score
    ON context_packs(quarter, composite_best DESC);

CREATE INDEX IF NOT EXISTS idx_packs_quarter_horizon
    ON context_packs(quarter, best_horizon);

CREATE INDEX IF NOT EXISTS idx_packs_quarter_sector
    ON context_packs(quarter, sector);

CREATE INDEX IF NOT EXISTS idx_packs_quarter_mcap
    ON context_packs(quarter, market_cap_usd);

PRAGMA journal_mode = WAL;
```

Module 6 reads via SQL:

```python
# Module 6 applies all three gates (D21 threshold + D27 sector + D28 market cap) in one query.
rows = conn.execute("""
    SELECT ticker, archetype, best_horizon, composite_best, sector, market_cap_usd, pack_json
    FROM context_packs
    WHERE quarter = ?
      AND pack_version = ?
      AND composite_best >= ?
      AND sector IN ('Healthcare', 'Biotechnology')
      AND market_cap_usd <= ?
    ORDER BY composite_best DESC
""", (quarter, "m5-v1", 6.0, 3_700_000_000)).fetchall()

for ticker, archetype, best_horizon, composite_best, sector, market_cap, pack_json in rows:
    pack = json.loads(pack_json)
    # build prompt from pack...
```

---

## Context pack JSON blob schema (`pack_version: "m5-v1"`)

```json
{
  "pack_version": "m5-v1",
  "pack_built_at": "2026-04-24T14:30:00Z",
  "source_rank_hash": "ab12cd34...",
  "identity": {
    "ticker": "TCRX",
    "name_of_issuer": "TScan Therapeutics Inc",
    "cusip": "87288A106",
    "quarter": "2025Q4",
    "sector": "Healthcare",
    "industry": "Biotechnology",
    "exchange": "NMS"
  },
  "market_snapshot": {
    "market_cap_usd": 210000000,
    "shares_out": 31500000,
    "last_close_usd": 6.67,
    "price_today_usd": 8.20,
    "adv_30d_usd": 1850000,
    "currency": "USD",
    "snapshot_fetched_at": "2026-04-23T18:02:11Z"
  },
  "price_trajectory": {
    "R_4": 1.18, "R_12": 0.72, "R_26": 0.55, "R_52": 0.78,
    "R_4_over_R_12": 1.64, "R_4_over_R_26": 2.15, "R_4_over_R_52": 1.51,
    "R_12_over_R_26": 1.31, "R_12_over_R_52": 0.92, "R_26_over_R_52": 0.71,
    "price_source_date_4w": "2026-03-27",
    "price_source_date_12w": "2026-01-30",
    "price_source_date_26w": "2025-10-24",
    "price_source_date_52w": "2025-04-25",
    "weeks_of_history_used": 52,
    "young_ticker_flag": false
  },
  "fund_accumulation": {
    "fund_count": 6,
    "total_shares": 1850000,
    "total_market_value_usd": 12340000,
    "qoq_share_change": 450000,
    "qoq_fund_count_change": 2,
    "new_positions": 2,
    "increased_positions": 3,
    "decreased_positions": 1,
    "exited_positions": 0,
    "ticker_is_verified": true,
    "has_prefunded_warrants": false,
    "has_regular_warrants": false,
    "has_options": false,
    "has_unknown_class": false
  },
  "archetype_verdict": {
    "archetype": "deep_base_breakout",
    "description": "Deep intra-year crash, multi-month base, now breaking out (VCP-style)",
    "match_confidence": 1.00,
    "score_3mo": 9,
    "score_12mo": 9,
    "composite_3mo": 9.0,
    "composite_12mo": 9.0,
    "best_horizon": "equal",
    "composite_best": 9.0
  },
  "near_term_3mo": {
    "horizon_months": 3,
    "pattern_score": 9,
    "composite": 9.0,
    "research_emphasis": 0.5,
    "entry_price_context": {
      "anchor_window": "12w",
      "anchor_price_usd": 11.39,
      "pct_off_anchor": -27.9,
      "pct_off_52w_low": 52.3,
      "pct_off_52w_high": -18.0
    },
    "narrative_summary": "Deep crash earlier in the year, bottomed around Q1, now breaking out of a multi-month base. Over 3 months, validate whether the breakout holds above the base high on rising volume, whether fund accumulation continues, and whether any catalyst (earnings, trial readout, guidance) triggers follow-through."
  },
  "long_term_12mo": {
    "horizon_months": 12,
    "pattern_score": 9,
    "composite": 9.0,
    "research_emphasis": 0.5,
    "entry_price_context": {
      "anchor_window": "52w",
      "anchor_price_usd": 10.51,
      "pct_off_anchor": -21.9,
      "pct_off_52w_low": 52.3,
      "pct_off_52w_high": -18.0
    },
    "narrative_summary": "Base-to-breakout is a classic Stage 2 transition setup. Over 12 months, assess whether the pattern resolves into a durable uptrend: pipeline / moat / approval milestones, competitive dynamics, and whether fund holders keep accumulating through the next two filings."
  }
}
```

Field stability guarantees:
- Numeric fields use **USD throughout**, even if `currency != "USD"` (FX conversion is out-of-scope for v1 — non-USD tickers get `currency` flagged and the LLM told to account for it).
- Missing numeric values are emitted as `null`, never `NaN` or omitted.
- Dates are ISO strings.
- The two horizon sections are **always both present**, regardless of `best_horizon`.

---

## Config schema

```yaml
# config/enrichment.yaml — Module 5 knobs. All tunable; no code changes needed.

selection:
  # v1 enriches every Module 4b row (D21 + D27 + D28). The filter knobs below are stubs
  # reserved for when Module 6 cost estimates land and the investable universe is finalised.
  # Leave null / empty for now.
  composite_best_min: null         # DEFERRED — set once Module 6 per-ticker cost is known (D21)
  shortlist_top_n: null            # DEFERRED (D21)
  sector_allowlist: null           # DEFERRED — user-chosen sectors for Module 6 scope (D27)
  industry_allowlist: null         # DEFERRED — optional finer-grained filter (D27)
  sector_blocklist: []             # DEFERRED — opt-out for specific mismatched sectors (D27)
  industry_blocklist: []           # DEFERRED (D27)
  market_cap_max_usd: null         # DEFERRED — final max market cap before Module 6 (D28)
  market_cap_min_usd: null         # DEFERRED — optional tighter floor above Module 4a's $50M (D28)
  include_unclassified: true       # v1 includes unclassified; Module 6 filters at query time
  denylist_tickers: []             # explicit list to exclude; usually empty

pack:
  pack_version: "m5-v1"            # bumped on any schema change; invalidates cache
  emphasis_weights:                # D25
    best: 0.7                      # weight on the best horizon
    other: 0.3                     # weight on the non-best horizon
    equal: 0.5                     # split when best_horizon == "equal"
  narrative_templates_path: "config/enrichment_narratives.yaml"
                                   # per-archetype, per-horizon narrative templates
                                   # falls back to a generic template if archetype missing

store:
  db_path: "context_packs.db"      # relative to 2_Funds_parser/; primary store + cache
  enable_cache: true               # --no-cache on CLI forces off (rebuild + skip upsert)

reports:
  preview_count: 10                # how many packs to inline into the HTML report
  top_sector_rows: 10              # sector histogram depth
```

```yaml
# config/enrichment_narratives.yaml — editable prose templates; one block per archetype.
# Placeholders: {ticker}, {pct_off_52w_low}, {pct_off_52w_high}, {fund_count},
# {qoq_fund_count_change}, {sector}, {industry}, {horizon_months}.

templates:
  deep_base_breakout:
    "3mo": "Deep intra-year crash, now breaking out of a multi-month base. Over {horizon_months} months, validate whether the breakout holds above base resistance on rising volume, whether fund accumulation continues ({fund_count} holders, QoQ {qoq_fund_count_change:+d}), and whether any near-term catalyst triggers follow-through."
    "12mo": "Base-to-breakout is a Stage 2 transition setup per Minervini / Weinstein. Over {horizon_months} months, assess whether the pattern resolves into a durable uptrend: pipeline / moat / approval milestones in {industry}, competitive dynamics, and whether fund holders keep accumulating."
  fresh_awakening:
    "3mo": "..."
    "12mo": "..."
  # ... one entry per archetype; missing archetypes fall back to `default`.
  default:
    "3mo": "Pattern: {archetype}. Over {horizon_months} months, evaluate the near-term setup quality and whether any catalyst is scheduled."
    "12mo": "Pattern: {archetype}. Over {horizon_months} months, assess the long-run thesis in {sector} / {industry}."
```

---

## Function signatures

```python
# src/module_5/selection.py

def apply_selection(
    ranked_df: pd.DataFrame,
    selection_config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """v1: applies only denylist_tickers (D21). Returns (to_enrich_df, exclusions_df).
    Hook reserved for a future composite_best_min / shortlist_top_n gate once the
    Module 6 cost estimate lands."""


# src/module_5/packs.py

def build_context_pack(
    ranked_row: pd.Series,
    prices_db: Path,
    pack_config: dict,
    narrative_templates: dict,
) -> dict:
    """Build a single pack dict from one ranked row + prices.db. Pure function.
    Emphasis split per D25; narrative rendered via template substitution."""


def compute_source_rank_hash(ranked_row: pd.Series) -> str:
    """sha1 over the subset of columns that actually feed the pack.
    Cosmetic column reorders in ranked parquet do not bust the cache."""


# src/module_5/packs_db.py

def init_packs_db(db_path: Path) -> None:
    """CREATE TABLE IF NOT EXISTS context_packs + indexes. Sets WAL."""


def probe_pack(
    conn: sqlite3.Connection,
    ticker: str,
    quarter: str,
    pack_version: str,
    source_rank_hash: str,
) -> dict | None:
    """Returns the pack dict on hit (hash + version match), None otherwise.
    Parses pack_json lazily; returns None without decoding on hash mismatch."""


def upsert_pack(
    conn: sqlite3.Connection,
    pack: dict,
    quarter: str,
    source_rank_hash: str,
    cache_status: str,
) -> None:
    """INSERT OR REPLACE. Lifts filter columns (archetype, best_horizon, composite_*,
    score_*, sector, industry, market_cap_usd) from the pack into indexed columns."""


def query_packs_for_quarter(
    conn: sqlite3.Connection,
    quarter: str,
    pack_version: str,
    composite_best_min: float | None = None,
    best_horizon: str | None = None,
    sector_allowlist: list[str] | None = None,
    industry_allowlist: list[str] | None = None,
    market_cap_max_usd: int | None = None,
    market_cap_min_usd: int | None = None,
) -> list[dict]:
    """Convenience for Module 6 and the HTML report. Applies D21 (composite) + D27
    (sector/industry) + D28 (market cap) filters at SQL level — no JSON parsing needed."""


# src/module_5/reports.py

def generate_enrichment_report_html(
    pack_rows: list[dict],             # one dict per built/cached pack
    exclusions_df: pd.DataFrame,
    ranked_count: int,
    run_stats: dict,                   # {'built': N1, 'cache_hit': N2, 'refreshed': N3, 'wall_s': ...}
    output_path: Path,
    preview_count: int = 10,
) -> None: ...


# src/module_5/enrichment.py  (orchestrator)

def run_enrichment(
    ranked_path: Path,
    prices_db_path: Path,
    enrichment_config_path: Path,
    output_dir: Path,
    intermediate_dir: Path,
    quarter: str,
    force_refresh: bool = False,
    debug: bool = False,
) -> pd.DataFrame:
    """End-to-end: selection (denylist only in v1) → packs → upsert → reports.
    Returns a DataFrame with one row per processed ticker (ticker, cache_status,
    composite_best, archetype, best_horizon)."""
```

---

## CLI

Following the established `scripts/N_*.py` pattern:

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_build_context_packs.py
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_build_context_packs.py --quarter 2025Q4 -v
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_build_context_packs.py --force-refresh
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_build_context_packs.py --no-cache
# (Threshold/top-N flags deliberately absent in v1 — D21. Will return once Module 6 cost lands.)
```

CLI flags override `enrichment.yaml` values for that run; they do not mutate the YAML.

### `run_2_Funds_parser.bat` integration

Add a fifth `[y/N]` gate after Module 4b:

```
[... after Module 4b succeeds ...]
Proceed to Module 5 (build context packs)? [y/N]: _
```

On `y`: runs `scripts/5_build_context_packs.py --quarter {resolved}` and opens `Outputs/enrichment_report_{quarter}.html`.

Per the `feedback_update_daily_runner` memory, this bat update lands in the same commit as the Module 5 implementation — the daily scheduled 18:00 run is the single live-data entry point and must stay in sync.

---

## Caching semantics (SWR, D24)

Primary store and cache are the same file: `context_packs.db`. Schema in § SQLite schema. Behaviour:

- `cache_status` is recorded per row on every write (`built` | `cache_hit` | `refreshed`) and aggregated in the HTML report so the user can tell how much actually changed.
- Quarter rollover (e.g. `2025Q4 → 2026Q1`) builds new rows with the new `quarter` value; prior-quarter rows remain untouched.
- Bumping `pack_version` (blob schema or narrative-template change) makes every old-version row unreachable on read; fresh `m5-v2` rows coexist. `scripts/5_compact_cache.py --drop-version m5-v1` can prune later if size matters.
- `--force-refresh` rebuilds every ticker (hash check skipped); still INSERT OR REPLACE.
- `--no-cache` rebuilds AND skips the upsert entirely — use for debug runs that should leave the DB untouched.

---

## Failure modes

- **Missing ranked parquet**: exit code 1, clear error ("Run Module 4b first; no ranked_candidates file for 2025Q4").
- **Schema mismatch in ranked parquet** (e.g. old single-horizon column layout): fail loudly with column-diff dump. Do not attempt to patch; tell the user to regenerate via Module 4b.
- **`prices.db` missing or empty for a ticker**: emit pack with `weeks_of_history_used=0`, omit `pct_off_52w_low/high`, set `price_trajectory.data_quality = "partial"`. Module 6 can still score the ticker on fund + snapshot signal alone.
- **Narrative template missing for an archetype**: fall back to `templates.default`. Log a warning; do not fail.
- **`context_packs.db` corrupted or locked**: catch, log, rebuild table (preserving other rows if partially recoverable), continue. Same defensive pattern as Module 4's prices DB. WAL reduces the likelihood to near-zero.
- **Empty input** (no ranked rows — e.g. wrong quarter): no pack rows written; exclusions parquet still emitted; HTML report states zero packs. Module 6 can then be skipped without error.

All failure paths are logged structured-line to `logs/module_5_{quarter}.log`.

---

## Acceptance tests

1. **First run** on 2025Q4 data: produces 1,423 rows in `context_packs` for `quarter='2025Q4' AND pack_version='m5-v1'`. Every row has `cache_status='built'`. HTML report and (empty) exclusions parquet written. Wall time < 30 s.
2. **Second run same day, no config change**: `SELECT COUNT(*) FROM context_packs WHERE quarter='2025Q4' AND pack_version='m5-v1'` is still 1,423 and every row logs `cache_status='cache_hit'` for the run. Wall time < 3 s. No INSERT OR REPLACE statements executed (the hash matched before write).
3. **After editing `ranking.yaml` alpha and re-running Module 4b then Module 5**: composite_best values change → `source_rank_hash` changes → affected rows update with `cache_status='refreshed'`. Unchanged tickers keep the old row and log `cache_hit` for the run.
4. **Bumping `pack_version` from `m5-v1` to `m5-v2`**: `m5-v1` rows remain in the table; `m5-v2` rows insert alongside. Module 6 queries with `pack_version='m5-v2'` and sees only new rows.
5. **Explicit denylist** (`denylist_tickers: ["ABEO"]`): ABEO appears in `pack_exclusions_{quarter}.parquet` with `reason='denylist'`; no pack row written for it; other 1,422 tickers enriched normally.
6. **Unclassified inclusion**: 65 rows (current 2025Q4 count) have `archetype='unclassified'` and score_3mo=0 / score_12mo=0. Their price_trajectory, fund_accumulation, and market_snapshot sections are fully populated.
7. **Deterministic pack output**: two back-to-back runs (`--force-refresh` both times) produce byte-identical `pack_json` strings for every ticker. Key order stable (explicit `sort_keys=True`), float formatting stable.
8. **Non-USD ticker** (rare in biotech small-cap but possible): `currency != "USD"` is surfaced in the pack; a warning string is appended to both horizon `narrative_summary` sections: "Priced in {currency}; convert before reasoning about USD appreciation." No crash.
9. **Young ticker** (< 52 weeks, carried with `young_ticker_flag=true`): pack row written; `long_term_12mo.entry_price_context.pct_off_anchor` in blob = null; narrative tells Module 6 the 12mo window is partially synthetic.
10. **Empty input** (synthetic `ranked_candidates` parquet with zero rows): zero pack rows inserted; empty exclusions parquet written; HTML report states zero packs cleanly.
11. **Interrupted run**: if Module 5 is killed mid-transaction, the table is left in its pre-run state (SQLite's atomic commit guarantee). Re-running picks up where it left off — every not-yet-built ticker appears as `built`, previously-built ones as `cache_hit`.

---

## Open items deferred (flagged for later)

- **🟡 Shortlist threshold (D21, deferred explicitly).** The `composite_best`-based gate that controls which packs feed Module 6's paid LLM runs. Decision depends on Module 6's per-ticker cost estimate (token count × price). When Module 6 is designed and its cost model exists, the user will pick a threshold — either as a Module 5 gate (so prep also skips weak tickers) or as a Module 6 query-time filter. Hook already reserved in `enrichment.yaml` (`selection.composite_best_min`, currently `null`).
- **🟡 Sector allowlist (D27, deferred explicitly).** The `sector` / `industry` filter that narrows Module 6 scope to the user's actual investment universe. Decision is user-driven: although the pipeline sources from 21 biotech/healthcare specialist funds, those funds do hold adjacent and occasional non-healthcare names, and the user will choose which slices to LLM-score. Hooks reserved in `enrichment.yaml` (`selection.sector_allowlist`, `selection.industry_allowlist`, plus `_blocklist` companions). The HTML enrichment report surfaces the sector/industry histogram so the decision is informed. Module 6 applies the filter at query time via the indexed `sector`/`industry` columns.
- **🟡 Final max market cap (D28, deferred explicitly).** The current cache covers everything up to Module 4a's $10B fetch ceiling. Before the first Module 6 run the user will set the final max market cap that defines the investable universe (historically $3.7B, but this is a per-run decision post-cost-estimate). Hook reserved in `enrichment.yaml` (`selection.market_cap_max_usd`, plus a symmetric `market_cap_min_usd` for tightening the $50M floor). The HTML report includes the market-cap histogram so the choice is informed. Module 6 filters via the indexed `market_cap_usd` column — no Module 4a re-run required to tighten.
- **🟡 Adaptive shortlist width / per-archetype quotas.** If any single archetype (e.g. `v_recovery` with 395 rows) dominates a future shortlist, consider "top-N per archetype bucket" so rare-but-strong patterns (fresh_awakening, post_crash_rebase) are never drowned out. Tied to the D21 decision above.
- **🟡 Fundamentals enrichment** (forward EPS, analyst targets, next earnings date, FDA catalyst calendar). Requires an external data source. Path: pluggable `src/module_5/fundamentals.py` adapter when Twelve Data (or similar licensed provider) replaces yfinance pre-public-deploy (memory `project_data_provider_switch`).
- **🟡 Per-ticker peer snapshots** (5–10 sector+archetype peers with R_52 / R_12 distributions so the LLM sees relative positioning). Needs a peer-selection heuristic. Defer until v1 shows whether the LLM actually needs this context.
- **🟡 News headline pre-fetch.** Single cached Bing/NewsAPI call per ticker with last-90-day headlines. Skipped in v1 per D22. Revisit if Module 6's `web_search` cost turns out dominant.
- **🟡 Currency conversion.** Non-USD tickers currently pass through with the native currency flagged. Add a conversion layer once the data-provider swap introduces reliable FX.

---

## Update log

- **2026-04-24** — Initial draft. Design decisions D21–D26 recorded; awaiting user approval before `src/module_5/` implementation. Cross-references: dual-horizon contract (D20, `decisions_module_4.md`), yfinance off-ramp (memory `project_data_provider_switch`), SWR caching (memory `feedback_swr_pattern`), bat-runner parity (memory `feedback_update_daily_runner`), spec + decisions update cadence (memory `feedback_2_funds_parser_spec_decisions`).
- **2026-04-24 (rev 2)** — Collapsed D23 + D24 to a single SQLite store (`context_packs.db`). Removed the `_intermediate_outputs/context_packs_{quarter}/` JSON directory and the flat Parquet index — both were redundant with the cache DB. Motivation: consistency with pipeline convention (`2_fundparser.db`, `prices.db`), atomic writes, native SQL filtering for Module 6, one representation instead of three. `pack_version` + `source_rank_hash` still drive invalidation; the cache *is* the output.
- **2026-04-24 (rev 3)** — D21 reversed: Module 5 no longer shortlists. All 1,423 Module 4b rows get packs. The `composite_best` threshold that controls Module 6 LLM cost is **deferred** until Module 6's per-ticker cost is estimated. Hooks kept in `enrichment.yaml` (`selection.composite_best_min: null`, `selection.shortlist_top_n: null`) so the gate can be reintroduced as a one-line config change later. Unclassified (65 tickers) now included by default; Module 6 can filter at query time. Wall-time estimates revised up (30 s cold / 3 s warm). Motivation (from user): "prepare data for all tickers now; set threshold after Module 6 cost is known."
- **2026-04-24 (rev 4)** — Added D27: **sector selection deferred to Module 6**, same pattern as D21. `sector` and `industry` now lifted into indexed columns of the `context_packs` table (with an `idx_packs_quarter_sector` index) so Module 6 can filter with a native SQL `WHERE sector IN (...)`. `enrichment.yaml` gains four stub knobs (`sector_allowlist`, `industry_allowlist`, `sector_blocklist`, `industry_blocklist`) — all null/empty in v1. Enrichment HTML report now includes a clickable sector+industry histogram so the user can make an informed allowlist choice before Module 6 runs. Motivation (from user): "I want to select specific sectors before running module 6."
- **2026-04-24 (rev 5)** — Added D28: **final max market cap deferred to Module 6**, same pattern as D21/D27. Current cache holds everything up to Module 4a's $10B fetch ceiling; user will set the final cap before the first Module 6 run. `market_cap_usd` lifted into an indexed column (new `idx_packs_quarter_mcap` index) so Module 6 filters via `WHERE market_cap_usd <= ?`. `enrichment.yaml` gains `market_cap_max_usd` + `market_cap_min_usd` stubs (both null). HTML report gains a market-cap histogram using the same buckets as Module 4a's interactive prompt. Interaction with Module 4a preserved: if the user wants Module 4a survivors to also respect the new cap, they re-run 4a with `--market-cap-max-usd`; D28 is strictly the Module 5 → Module 6 boundary. Motivation (from user): "flag that I shall define the max market cap before I run module 6."
