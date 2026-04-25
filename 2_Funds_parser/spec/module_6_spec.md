# Module 6 — LLM Scoring (Claude API)

**Status:** Draft specification (2026-04-24). Awaiting user approval before implementation.
**Last updated:** 2026-04-24
**Model:** `claude-opus-4-7` (Anthropic Opus 4.7).
**Dispatch:** Anthropic **Message Batches API** (50% discount, ≤24h SLA) with **prompt caching** (cache-read tokens at 10% of base). Discounts stack. Local parallel fan-out (sync mode) available for calibration runs.
**Storage:** SQL-only (`llm_scores.db` at `2_Funds_parser/` root). **No JSON files on disk** — every payload, response, error, and run-level metric is a SQL row.

Module 6 reads `context_packs.db`, applies the four Module-6-boundary gates (D21/D27/D28/D29) at SQL query time, and asks Claude Opus 4.7 to estimate **expected appreciation and time-to-catalyst at both 3-month and 12-month horizons** for each selected ticker. Python deterministically computes the appreciation rate per horizon and picks `final_horizon = argmax_H(rate_H)`. Final output is a ranked SQL table plus an HTML / Excel report.

---

## Role and contract

**Reads.** `context_packs.db` (M5 output, 1,423 rows/quarter), `config/pipeline.yaml`, `config/scoring.yaml` *(new)*. `ANTHROPIC_API_KEY` from `.env` (loaded by M1).

**Writes.** `llm_scores.db` (new SQLite at `2_Funds_parser/` root) — durable cross-run cache. Four tables:
- `llm_scores` — one row per `(ticker, quarter, horizon, prompt_version, model)` with the LLM's structured estimate plus **the full text of the LLM reply** (raw + parsed sections) so you can inquire or render it later.
- `llm_runs` — one row per Module 6 dispatch (gate values, prompt_version, model, wall time, token totals, USD cost, batch_id).
- `llm_errors` — quarantine table. Any ticker whose response fails parsing lands here with `(ticker, quarter, run_id, raw_text, error_kind, error_detail)`. Never blocks the run.
- `final_rankings` — one row per `(ticker, quarter, run_id)` carrying the post-Python rate calc and final rank. Re-queryable; the HTML report is a view over this table.

**User-facing output.**
- `Outputs/final_ranking_{quarter}.html` — sortable table (dual-horizon display; M4 archetype + M5 fund-flow + M6 LLM rate).
- `Outputs/final_ranking_{quarter}.xlsx` — Excel with conditional formatting on `final_rate_pct_per_month`.
- `Outputs/cost_estimate_{quarter}.html` — pre-flight cost preview; shown before batch dispatch and requires `[y/N]` approval.
- `Outputs/llm_responses_{quarter}.html` — one collapsible card per ticker with its raw LLM text, reasoning trace, and per-horizon JSON. Rendered directly from `llm_scores.raw_text` — no JSON files intermediate.

**Does not write.** `context_packs.db` (read-only). `2_fundparser.db` (read-only). `data/prices.db` (untouched — Module 6 does not call yfinance or Twelve Data).

---

## Runtime flow

`scripts/6_score.py` is fully interactive. Order of operations:

1. **Health checks.** Probe `context_packs.db` (packs exist for quarter), verify `ANTHROPIC_API_KEY` in env, load `config/scoring.yaml`.

2. **Gate configuration — interactive prompts (D21/D27/D28/D29).** See § *Interactive gate prompts* below. Each prompt lists concrete options with live ticker counts computed from `context_packs.db`. User picks by number.

3. **Cost preview.** After gates are set, run the cost estimator (see § *Cost estimator*) and open `Outputs/cost_estimate_{quarter}.html`. Ask `[y/N] Proceed to dispatch N tickers for $X?`.

4. **Build per-ticker user messages.** For each selected pack, construct the user message as `{pack_body}` + `{prior_research from web_search_cache}` + `{prior_thesis from llm_scores}`. See § *Cross-run caches*. On a cold cache both injected blocks are empty — no effect on the first run.
   - **Pack stripping (per prompt-design choice #2 = B):** the `fund_accumulation` section of the M5 pack JSON is removed before insertion. The LLM scores fundamentals; the fund-flow signal is already scored upstream by M5's archetype + rescue logic. Exposing fund accumulation to the LLM would double-count the signal at the final ranking stage. `src/module_6/prompt.py::build_user_message()` owns this transformation.

5. **Dispatch.** Two modes, set by `--mode`:
   - `batch` (default, production) — single Anthropic Batch API submission. 50% discount, ≤24h SLA. Poll until complete, then write rows.
   - `sync` (calibration) — parallel fan-out with bounded concurrency (default 8 concurrent requests), using `anthropic.AsyncAnthropic`. Faster feedback, full list price. Same prompt caching applies.
   - Every (ticker, horizon) call shares the cacheable prefix — prefix cache hit rate should be ~100% across the run.

6. **Parse + store.** For each response: attempt JSON extraction → write parsed row to `llm_scores` AND store raw text + `response_id` + token counts. Parse failures → `llm_errors` table, run continues. **Also parse every `server_tool_use` block and write/update rows in `web_search_cache`.**

7. **Rate calc + rank.** Python computes `rate_H_pct_per_month` per horizon, picks `final_horizon = argmax_H`, tags `"either"` when within 5%. Writes `final_rankings` rows.

8. **Render reports.** HTML + XLSX final ranking. HTML LLM-responses viewer. Log a row to `llm_runs`.

9. **Exit gate.** Summary table printed; user can re-run with `--use-cache` to skip all previously-scored `(ticker, quarter, horizon, prompt_version, model)` rows.

---

## Interactive gate prompts (activates D21/D27/D28/D29)

All four gates are configured at runtime via multiple-choice prompts, computed against current `context_packs.db` state. The user's answers are **also written** into `llm_runs.gate_config_json` for reproducibility.

### D21 — `composite_best_min`

```
D21 — Minimum composite_best score
Current packs (2025Q4): 1,423 total.

  [1]  ≥ 5    (live count: 620 tickers)
  [2]  ≥ 6    (live count: 413 tickers)    ← handoff default
  [3]  ≥ 7    (live count: 181 tickers)
  [4]  ≥ 8    (live count: 61 tickers)
  [5]  custom

Choose [1-5]:
```

Counts recomputed live from `context_packs.db` each run.

### D27 — `industry_allowlist` *(changed from sector to industry per user)*

```
D27 — Industry allowlist
Top industries in current pack pool (2025Q4):

  [1]  Biotechnology                   (980 tickers)
  [2]  Biotechnology + Drug Mfrs Specialty & Generic    (1,070)
  [3]  All Healthcare sector industries                 (1,230)
  [4]  Custom (enter list interactively)
  [5]  No filter (pass through)

Choose [1-5]:
```

The concrete industry breakdown is computed at runtime from `SELECT industry, COUNT(*) FROM context_packs WHERE quarter=? GROUP BY industry ORDER BY 2 DESC` — options adapt per quarter. `query_packs_for_quarter()` already accepts `industry_allowlist` ([src/module_5/packs_db.py](../src/module_5/packs_db.py):203), so no schema change needed.

### D28 — `market_cap_max_usd`

```
D28 — Maximum market cap (USD)
Same buckets as Module 4a's prompt:

  [1]  $500M     (live count after D21+D27: 28 tickers)
  [2]  $1B       (live count: 52)
  [3]  $2B       (live count: 81)
  [4]  $3.7B     (live count: 112)    ← handoff default
  [5]  $5B       (live count: 130)
  [6]  $7.5B     (live count: 144)
  [7]  $10B      (live count: 158)
  [8]  No cap

Choose [1-8]:
```

### D29 — Fund-flow rescue

```
D29 — Fund-flow rescue clause
Rescues tickers below the D21 threshold when funds are actively
rotating in (new_positions ≥ 1 OR qoq_fund_count_change ≥ 1),
excluding the five 'train has left' archetypes.

  [1]  ENABLED  — rescued tickers (current gates): +19 added → final feed N
  [2]  DISABLED — strict D21 only

Choose [1-2]:
```

The `+19` is computed live. The handoff default of (D21=6, industry=Healthcare, mcap≤3.7B, rescue on) → **~133 tickers**; live counts will drift quarter-to-quarter.

### Confirmation

After all four are set, a summary line prints:

```
Final Module 6 feed: 133 tickers.
  D21 composite_best_min = 6.0
  D27 industry_allowlist = ['Biotechnology', 'Drug Mfrs Specialty & Generic']
  D28 market_cap_max_usd = $3.7B
  D29 rescue_enabled = true
```

…before the cost preview opens.

---

## `llm_scores.db` — SQLite schema

All Module 6 state lives here. No Parquet, no JSON files. Six tables: four run-state tables (below) plus two cache tables (§ *Cross-run caches* further down).

### Table `llm_scores`

Primary key `(ticker, quarter, horizon, prompt_version, model)`. Re-runs at same key are cache hits (zero API cost). See § *Three-tier routing* for how cross-quarter and stale-within-quarter cases are handled.

| Column | Type | Notes |
|---|---|---|
| `ticker` | TEXT | |
| `quarter` | TEXT | `YYYYQn` |
| `horizon` | TEXT | `3mo` \| `12mo` |
| `prompt_version` | TEXT | e.g. `m6-v1`. Bump to invalidate cache for prompt changes. |
| `model` | TEXT | e.g. `claude-opus-4-7` |
| `run_id` | INTEGER | FK to `llm_runs.run_id` |
| `target_price_usd` | REAL | LLM's expected price at catalyst |
| `time_to_catalyst_weeks` | INTEGER | |
| `probability` | REAL | continuous 0.15–0.90 (D42) |
| `catalyst_type` | TEXT | enum (D43): `earnings` \| `trial_interim` \| `trial_final` \| `approval` \| `conference_presentation` \| `macro` \| `other` |
| `catalyst_detail` | TEXT | ≤120 chars; specifies the actual catalyst |
| `thesis_summary` | TEXT | ≤300 chars |
| `key_risks_json` | TEXT | JSON-encoded `string[]` |
| `current_price_at_scoring_usd` | REAL | Snapshot of `pack.market_snapshot.last_close_usd` at scoring time (D46) |
| `appreciation_from_current_pct` | REAL | Python-computed: (target − current_price) / current_price × 100 — **primary** (D46) |
| `appreciation_from_fair_pct` | REAL | Python-computed: (target − fair_mid) / fair_mid × 100 — reference |
| `appreciation_from_full_reward_pct` | REAL | Python-computed: (target − full_mid) / full_mid × 100 — reference |
| `score_at_current_pct_per_month` | REAL | **PRIMARY ranking score** (D46): (appr_from_current / months) × probability |
| `score_at_fair_pct_per_month` | REAL | Reference: (appr_from_fair / months) × probability |
| `score_at_full_reward_pct_per_month` | REAL | Reference: same formula vs full-reward mid |
| `source_tier` | TEXT | `'A'` exact cache, `'B'` light refresh, `'C'` full scoring (D39) |
| `pack_source_rank_hash` | TEXT | The M5 pack's `source_rank_hash` at scoring time (D39) |
| `refreshed_from_row_id` | INTEGER | FK to a prior `llm_scores` row when this row was written by a Tier B "no material change" refresh; NULL otherwise |

Per-ticker columns (same value for both horizon rows of a single `(ticker, run_id)` pair; duplicated for query simplicity):

| Column | Type | Notes |
|---|---|---|
| `fair_entry_low_usd` | REAL | |
| `fair_entry_high_usd` | REAL | |
| `fair_entry_rationale` | TEXT | ≤300 chars |
| `full_reward_low_usd` | REAL | |
| `full_reward_high_usd` | REAL | |
| `full_reward_rationale` | TEXT | ≤300 chars |
| `fully_diluted_shares_count` | REAL | INCLUDES pre-funded warrants (D40) |
| `prefunded_warrants_count` | REAL | called out separately for audit |
| `cash_and_equivalents_usd` | REAL | |
| `runway_months` | REAL | |
| `rnpv_total_usd` | REAL | |
| `rnpv_per_share_usd` | REAL | = rnpv_total_usd / fully_diluted_shares_count |
| `moat_score` | REAL | 3-band |
| `technology_uniqueness_score` | REAL | 3-band |
| `acquisition_target_score` | REAL | 3-band |
| `mgmt_track_record_score` | REAL | 3-band (D43) |
| `lead_indication` | TEXT | `research_brief.fda.lead_indication` |
| `research_brief_json` | TEXT | full nested research_brief as TEXT for audit / HTML rendering |
| `reasoning_trace` | TEXT | LLM's top-level reasoning block |
| `raw_text` | TEXT | **Full LLM reply text, as returned.** User-requested — queryable / renderable. |
| `response_id` | TEXT | Anthropic `msg_...` id |
| `input_tokens` | INTEGER | |
| `output_tokens` | INTEGER | |
| `cache_read_tokens` | INTEGER | |
| `cache_creation_tokens` | INTEGER | |
| `web_search_calls` | INTEGER | count of `web_search` tool invocations this turn |
| `usd_cost` | REAL | computed from token breakdown + search fee |
| `scored_at` | TEXT | ISO-8601 UTC |

Indexes: `(quarter, prompt_version, model)`, `(run_id)`, `(ticker, quarter)`.

### Table `llm_runs`

| Column | Type |
|---|---|
| `run_id` | INTEGER PRIMARY KEY AUTOINCREMENT |
| `quarter` | TEXT |
| `prompt_version` | TEXT |
| `model` | TEXT |
| `mode` | TEXT — `batch` or `sync` |
| `batch_id` | TEXT nullable — Anthropic batch id when `mode='batch'` |
| `gate_config_json` | TEXT — D21/D27/D28/D29 values as JSON |
| `feed_size` | INTEGER |
| `wall_time_s` | REAL |
| `input_tokens_total` | INTEGER |
| `output_tokens_total` | INTEGER |
| `cache_read_tokens_total` | INTEGER |
| `cache_creation_tokens_total` | INTEGER |
| `web_search_calls_total` | INTEGER |
| `usd_cost_total` | REAL |
| `usd_cost_list_price` | REAL — comparison against no-optim baseline |
| `started_at` | TEXT |
| `finished_at` | TEXT |

### Table `llm_errors`

| Column | Type |
|---|---|
| `error_id` | INTEGER PRIMARY KEY AUTOINCREMENT |
| `run_id` | INTEGER |
| `ticker` | TEXT |
| `quarter` | TEXT |
| `horizon` | TEXT |
| `error_kind` | TEXT — `json_parse_fail` \| `schema_violation` \| `api_error` \| `timeout` |
| `error_detail` | TEXT |
| `raw_text` | TEXT |
| `occurred_at` | TEXT |

### Table `final_rankings`

One row per `(ticker, quarter, run_id)`. The HTML / XLSX reports are a projection of this table.

| Column | Type |
|---|---|
| `ticker` | TEXT |
| `quarter` | TEXT |
| `run_id` | INTEGER |
| `final_rank` | INTEGER |
| `final_horizon` | TEXT — `3mo` \| `12mo` \| `either` |
| `final_score` | REAL — `score_at_current` at `final_horizon` (D46) |
| `current_price_at_scoring_usd` | REAL — D46 |
| `score_at_current_3mo` | REAL — primary score (D46) |
| `score_at_current_12mo` | REAL — primary score (D46) |
| `score_at_fair_3mo` | REAL — reference |
| `score_at_fair_12mo` | REAL — reference |
| `score_at_full_reward_3mo` | REAL — reference |
| `score_at_full_reward_12mo` | REAL — reference |
| `target_price_3mo_usd` | REAL |
| `target_price_12mo_usd` | REAL |
| `appreciation_from_current_3mo_pct` | REAL — D46 |
| `appreciation_from_current_12mo_pct` | REAL — D46 |
| `appreciation_from_fair_3mo_pct` | REAL — reference |
| `appreciation_from_fair_12mo_pct` | REAL — reference |
| `current_vs_fair_mid_pct` | REAL — = (current − fair_mid)/fair_mid × 100; positioning gap shown in reports |
| `time_to_catalyst_3mo_weeks` | INTEGER |
| `time_to_catalyst_12mo_weeks` | INTEGER |
| `probability_3mo` | REAL |
| `probability_12mo` | REAL |
| `fair_entry_low_usd` | REAL |
| `fair_entry_high_usd` | REAL |
| `full_reward_low_usd` | REAL |
| `full_reward_high_usd` | REAL |
| `fully_diluted_shares_count` | REAL |
| `rnpv_per_share_usd` | REAL |
| `moat_score` | REAL |
| `fda_pos_adjusted_lead` | REAL — `research_brief.rnpv_by_indication[lead].pos_adjusted` |
| `archetype` | TEXT — passthrough from M5 |
| `composite_best` | REAL — passthrough from M5 |
| `fund_count` | INTEGER — passthrough |
| `market_cap_usd` | REAL — passthrough |
| `industry` | TEXT — passthrough |

---

## Cross-run caches — `web_search_cache` and prior-thesis reuse

Two mechanisms reduce the cost of subsequent Module 6 runs by surfacing prior knowledge in the user prompt. Neither intercepts Anthropic's server-side `web_search` (that's not possible — the tool is server-side and bills before we see the result). Instead, both **inject prior content into the per-ticker user message** so the model issues fewer fresh searches and reasons in continuity with its prior assessments.

### Table `web_search_cache`

Populated by parsing every `server_tool_use` result block Anthropic returns on a Module 6 call. Keyed by URL — the same press release surfaced by different query wordings deduplicates.

| Column | Type | Notes |
|---|---|---|
| `url` | TEXT PRIMARY KEY | |
| `ticker` | TEXT | ticker whose call surfaced this URL |
| `quarter` | TEXT | quarter of run that first captured it |
| `run_id` | INTEGER | first-seen `llm_runs` id |
| `search_query` | TEXT | the query that surfaced it (audit) |
| `title` | TEXT | |
| `content` | TEXT | snippet body as returned by Anthropic |
| `content_length` | INTEGER | `len(content)` — drives the paywall-drop heuristic |
| `domain` | TEXT | extracted host (e.g. `globenewswire.com`), indexed |
| `published_date` | TEXT | parsed from the snippet where visible; nullable |
| `first_seen_date` | TEXT | ISO-8601 UTC |
| `last_seen_date` | TEXT | last run that re-surfaced the same URL |
| `seen_count` | INTEGER | runs that have hit this URL |

Indexes: `(ticker, last_seen_date DESC)`, `(domain)`, `(ticker, published_date DESC)`.

On every `server_tool_use` result, the writer does `INSERT ... ON CONFLICT(url) DO UPDATE SET last_seen_date=?, seen_count=seen_count+1` — no duplicates.

**Paywall-drop heuristic.** After the first full sync run, we run:

```sql
SELECT domain, AVG(content_length), COUNT(*)
FROM web_search_cache
WHERE domain IN ('endpts.com','statnews.com','bioworld.com')
GROUP BY domain;
```

Any domain whose `AVG(content_length) < 150` is a candidate for removal from the whitelist (user review). The three paywalled domains stay in `config/module_6_web_search_whitelists.yaml` until this check.

### Prior-research injection (web_search cache → next prompt)

Before dispatching a call for ticker T on run N+1:

1. Query `web_search_cache` for rows matching T, sorted by `last_seen_date DESC`, limited to entries within `scoring.yaml::cache.web_search_lookback_days` (default 180).
2. For each hit, format as `- [title] (source, date): {content[:500]}`.
3. Inject into the per-ticker user message as a `## Prior research` block.
4. System prompt instructs: *"You have prior research for this ticker listed below, collected {oldest_date} → {newest_date}. Only issue new web_search queries for (a) events AFTER {newest_date}, or (b) gaps in the prior research. Prior research is context, not conclusion — verify freshness if in doubt."*

Expected savings: 50–70% of fresh searches on ticker re-runs once the cache is warm (empirical — first run has zero priors, so all searches are fresh). Tokens for injected content are non-cacheable (variable per ticker) but still cheaper than the search fee + full fresh-search token cost.

### Prior-thesis injection (llm_scores → next prompt)

Before dispatching a call for ticker T on run N+1:

1. Query `llm_scores` for prior rows on `(T, any quarter, horizon)` — not restricted to the current quarter, since M6 thesis work has cross-quarter value. Limit to the 2 most recent `scored_at` per horizon.
2. Format a condensed `## Prior thesis` block with fields only (no `raw_text` — echo-chamber risk):
   ```
   Prior thesis for TCRX:
   - 2026-01-15 [Opus 4.7, m6-v1] near_term_3mo: +18% / 6w / trial_readout / conf 0.6
     "Phase 2 readout expected early 2026; top-line could move 20-30%."
   - 2026-01-15 [Opus 4.7, m6-v1] long_term_12mo: +35% / 32w / approval / conf 0.55
     "Accelerated approval pathway on orphan indication; dependent on readout success."
   ```
3. System prompt instructs: *"Prior thesis is for continuity reference, not anchoring. If a prior catalyst has realised or become stale, update your view based on current pack + current search. Do not merely repeat a prior thesis — justify any continuation."*

Table already exists (`llm_scores`); no schema change. A read-only query helper lives in `src/module_6/priors.py`.

**Echo-chamber mitigations (both caches):**
- Both injections carry explicit dates in the prompt so the model can reason about staleness.
- System prompt forbids verbatim repetition — the thesis must be justified by current evidence.
- Cost estimator reports `expected_cache_hit_rate` so a near-100% cache-reuse run surfaces for human review before dispatch.

---

## Three-tier routing (D38 / D39)

Every ticker in the feed is routed into one of three tiers **before dispatch**. The tier determines whether the API call happens at all, and if so, which prompt is used. Default behaviour on every run; disabled only by `--force-refresh`.

### Tier decision per ticker

Let `prior` = most recent `llm_scores` row for `(ticker, horizon, prompt_version, model)` across **all quarters**.

```
Tier A — exact cache hit ($0, no call):
    prior exists
    AND prior.quarter == current_quarter
    AND prior.pack_source_rank_hash == current_pack.source_rank_hash

Tier B — light refresh (~15–20% of full cost):
    prior exists
    AND days_since(prior.scored_at) <= refresh_threshold_days[horizon]
    AND NOT Tier A

Tier C — full scoring (full cost):
    no prior, OR prior is stale, OR Tier A/B didn't apply
    OR Tier B returned material_change=true (auto-escalation)
```

**Strict pack-hash match for Tier A.** Any difference in `pack_source_rank_hash` — even trivial ones like snapshot timestamp moves — demotes to Tier B. Safer than trying to classify "trivial vs material" at the hash layer; Tier B is cheap.

### Refresh thresholds (horizon-aware)

| Horizon | `refresh_threshold_days` | Rationale |
|---|---|---|
| `3mo` | **28** (4 weeks) | One readout/earnings cycle — a 4-week-old 3mo thesis is 1/3 stale |
| `12mo` | **56** (8 weeks) | Pipeline fundamentals rarely shift in under 2 months |

Tunable in `scoring.yaml::cache.refresh_threshold_weeks_3mo` / `_12mo`.

### Tier B — the light refresh call

Single LLM turn per horizon, ~1k tokens input. Shares the same cacheable system-prompt prefix (prompt cache applies).

**User message (abbreviated):**

```
Ticker: {ticker}
Current pack snapshot (key deltas only):
  - archetype: {archetype} (was {prior.pack_archetype})
  - composite_best: {composite_best:+.2f} (was {prior.pack_composite_best:+.2f})
  - market_cap_usd: {market_cap_usd}
  - fund_count: {fund_count}
  - pct_off_52w_low/high: {...}

Your prior thesis on {prior.scored_at}:
  - {horizon} → {prior.expected_appreciation_pct:+.1f}% / {prior.time_to_catalyst_weeks}w /
    catalyst={prior.catalyst_type}, confidence={prior.confidence:.2f}
    "{prior.thesis_summary}"

Since {prior.scored_at}, has anything material changed that would revise this
estimate by more than 5 percentage points in appreciation OR shift the catalyst
timing by more than 2 weeks? You have up to 2 web_search uses.
```

**Output JSON schema:**

```jsonc
{
  "ticker": "TCRX",
  "material_change": true,       // or false
  "reason": "≤300 chars",
  "updated_thesis_if_changed": {
    // same shape as the full output's near_term_3mo / long_term_12mo block,
    // OR null when material_change=false
  }
}
```

Max output tokens: 400 (vs 1500 for full). `max_uses: 2` (vs 5 for full).

### On "no material change" (`material_change=false`)

Write a new `llm_scores` row for the current quarter with:
- All thesis fields copied from `prior` (expected_appreciation_pct, time_to_catalyst_weeks, catalyst_type, confidence, thesis_summary, key_risks_json, reasoning_trace).
- `source_tier = 'B'`
- `refreshed_from_row_id = prior.id`
- `pack_source_rank_hash = current_pack.source_rank_hash`
- `raw_text` = the light-prompt response (so you can audit "why did it say no change?").
- `scored_at` = now, `run_id` = current run.
- `input_tokens` / `output_tokens` / `usd_cost` reflect the light-prompt actuals.

Queryable as a normal score row; `final_rankings` doesn't need to look across quarters.

### On "material change" (`material_change=true`)

**Auto-escalate to Tier C in the same run.**
- In `sync` mode: the async worker fires the full prompt for that ticker/horizon immediately after the light-refresh response decodes.
- In `batch` mode: the first batch runs all Tier B + Tier C tickers; material-change escalations are bundled into a **second batch** submitted after the first completes. User is shown the delta-cost summary (e.g. `"Tier B escalations detected: 12 tickers → additional $8.40 batch"`) and the second batch dispatches automatically — no second `[y/N]`. The pre-flight estimate flags this as a worst-case in the original cost preview.

The escalated Tier C row replaces the Tier B row for that ticker/horizon in the same run; both rows are persisted in `llm_scores` (the Tier B row for audit of the escalation decision), but `final_rankings` reads only the Tier C row.

### Pre-flight tier summary (shown before `[y/N]`)

```
Feed: 248 tickers
  Tier A (exact cache, $0):           133  ← same quarter, same pack hash
  Tier B (light refresh, ~$0.12/tk):   76  ← prior within threshold, pack shifted or new quarter
  Tier C (full score,    ~$0.70/tk):   39  ← no prior or stale beyond threshold

Estimated cost:
  Tier B tokens + search:             $  9.12
  Tier C tokens + search:             $ 27.30
  Search fees (upper bound):          $  1.30
                                      ────────
  Base estimate:                      $ 37.72
  Worst-case if all Tier B escalate:  $ 81.92   ← rare; shown as ceiling

Proceed? [y/N]
```

### Reporting

`final_rankings` and the HTML report carry `source_tier` as a column. The `llm_responses_{quarter}.html` viewer groups Tier B cards in their own collapsible section so the user can skim "no material change" judgments quickly.

---

## Dispatch — batch vs parallel sync

Two mutually exclusive modes selected by CLI flag.

### `--mode batch` *(default for production)*

- Anthropic **Message Batches API** (`client.messages.batches.create`). Up to 100k requests per batch, 24h SLA, **50% discount on all tokens** (input, output, cache read, cache creation).
- Each request in the batch shares the **same cacheable prompt prefix** → cache-read discount applies to every request after the first.
- One batch submission per run, polled until `ended_at` is set, then rows are decoded and written.
- `llm_runs.batch_id` preserves the reference for post-hoc audit.

### `--mode sync` *(for calibration / debug)*

- `anthropic.AsyncAnthropic.messages.create` called in parallel with `asyncio.gather` bounded by `asyncio.Semaphore(scoring.sync_concurrency)` (default 8).
- List price (no 50% discount), but prompt caching still applies.
- Useful for calibration: see responses in seconds, not hours.
- Same schema writes, same cache check — a `sync` run whose results are written can be reused by a later `batch` run's `--use-cache`.

Concurrency value is in `scoring.yaml::dispatch.sync_concurrency`; default 8. Anthropic's per-org rate limit governs the ceiling.

---

## Web search (D22) — settled

Anthropic's server-side `web_search` tool is enabled on every ticker call. The model decides when to issue searches; Anthropic runs them and injects page snippets back into the context. Two billing lines apply:
1. **Search fee** — $10 per 1,000 queries (tracked by the SDK as `server_tool_use.web_search_requests`).
2. **Token cost** — the returned snippets count as input tokens on the next turn; additional output tokens are emitted as the model reasons over them.

Both are surfaced as separate lines in the pre-flight cost estimator.

### Final parameters

| Parameter | Value | Notes |
|---|---|---|
| `max_uses` | **5 per ticker** | Tunable via `scoring.yaml::web_search.max_uses`. With research_emphasis 0.7/0.3 → ~3.5 long-term + ~1.5 near-term queries. Raise to 7 or 10 if thesis quality is thin. |
| `allowed_domains` | **Per-industry whitelist** — [config/module_6_web_search_whitelists.yaml](../config/module_6_web_search_whitelists.yaml). Universal tier + industry tier, concatenated per ticker. Never unrestricted. | |
| `blocked_domains` | unused (mutually exclusive with `allowed_domains`). | |
| `user_location` | unused. | |
| Fall-back at `max_uses` | Accept — model answers from what it has. | No re-dispatch with higher cap. If thesis quality comes back poor, the user raises `max_uses` globally for the next run. |

### Whitelist — universal tier (applied to every ticker)

- `sec.gov` — 10-K / 10-Q / 8-K / 13F
- `globenewswire.com` — press-release wire (carries most biotech IR content)
- `prnewswire.com` — press-release wire
- `businesswire.com` — press-release wire

### Whitelist — biotech / pharma tier

- `fda.gov` — approvals, CRLs, AdCom, PDUFA dates
- `clinicaltrials.gov` — trial registry / endpoints
- `fiercebiotech.com`, `endpts.com`, `statnews.com`, `biopharmadive.com`, `bioworld.com` — industry trade press
- `oncologypipeline.com` — pipeline tracker

Other industries (Medical Devices, Diagnostics, Healthcare Plans, Medical Care Facilities) have provisional tiers defined in the YAML; user reviews them when a non-biotech ticker first surfaces in the feed.

### IR / investor-relations capture

US biotechs post essentially all material news through the three wire services (universal tier). Per-ticker IR subdomains (`investors.<ticker>.com`) are **not** whitelisted individually — `allowed_domains` is a per-call global list and we'd need 133+ entries. The wires carry the same press-release text, so a search like `"<ticker> press release Q3 2025"` returns the PR content directly.

### Research-emphasis guidance (not enforced)

Every M5 pack carries `research_emphasis` — `(0.7, 0.3)` when `best_horizon=12mo`, `(0.3, 0.7)` when `3mo`, `(0.5, 0.5)` when `equal`. The system prompt tells the model to bias its search-query mix accordingly (~3.5 long-term + ~1.5 near-term at `max_uses=5` with 0.7/0.3 emphasis), but `max_uses` is the only hard cap.

### Cost estimator surface

The pre-flight HTML shows search fees as a separate line:

```
  Token cost (cache + batch):       $  8.42
  Web search fees (upper bound):    $  6.65   ← 133 × 5 × $0.010
  Additional input tokens (search   $ ~10
    snippet results, estimated):
                                    ────────
  Total estimated:                  $ ~25
```

The search-snippet input-token estimate is a calibration figure (~2,000 tokens per search × feed_size × max_uses × batch-adjusted input price); refined after the first sync run.

---

## LLM prompt structure — **OPEN: to be discussed after point 5 is settled**

**Plan** (placeholder — actual text drafted in a separate review pass):

- **System prompt** (cacheable prefix, `cache_control: {type: "ephemeral"}`, ≥1024 tokens): scoring framework, dual-horizon rubric, output JSON schema, **8 few-shot examples** covering all three confidence bands, archetype diversity (fresh_awakening, post_crash_rebase, broken_trend override, unclassified, deep_base_breakout × 4 at different scales), and scale diversity (micro-cap $72M → mid-cap $2.1B). See [config/module_6_few_shots.md](../config/module_6_few_shots.md) for the ticker list. Explicit "do not compute rate — Python does that" and "reason from the pack first, then call web_search for catalysts" instructions in HARD RULES.
- **User message** (per-ticker, NOT cached): the pack JSON blob (from `context_packs.pack_json`) rendered as structured text + explicit "score BOTH horizons" directive.
- **Model output:** a single JSON object matching the schema in § *Output JSON schema* below, wrapped in code fences so it's robustly extractable.

Draft + review happens after point 5's web-search parameters are settled.

---

## Output JSON schema (per LLM call, prompt_version `m6-v2`)

The LLM returns a **three-section** structured JSON object: research brief (qualitative + structured research), entry price ranges (per-ticker), and per-horizon scoring inputs. Python computes appreciation percentages and the composite score deterministically (D34) — the LLM never computes rates.

```jsonc
{
  "ticker": "TCRX",
  "reasoning_trace": "≤300 chars — top-level synthesis across horizons",

  // ────── SECTION A — Research brief (SQL → HTML) ──────
  "research_brief": {
    "technology": {
      "origin": "≤200 chars — academic lab / spin-out / licensed-in",
      "licensing_source": "≤120 chars | null",
      "uniqueness_score": 0.6,               // 3-band: 0.3 common / 0.6 differentiated / 0.9 novel
      "uniqueness_rationale": "≤250 chars"
    },
    "moat": {
      "score": 0.6,                          // 3-band 0.3/0.6/0.9
      "rationale": "≤300 chars — IP remaining years, data exclusivity, switching cost, capability"
    },

    "financials": {
      "fully_diluted_shares_count": 0,       // MUST include pre-funded warrants (D40 HARD RULE)
      "basic_shares_count": 0,               // for audit
      "prefunded_warrants_count": 0,         // called out separately — biotech small-cap dilution driver
      "cash_and_equivalents_usd": 0,
      "quarterly_burn_usd": 0,
      "runway_months": 0,
      "shelf_registration_usd_capacity": 0,
      "recent_capital_raises": [
        { "date_iso": "YYYY-MM", "instrument": "equity|convertible|debt", "gross_proceeds_usd": 0 }
      ],
      "prefunded_warrants_detail": [
        { "count": 0, "strike_usd": 0.001, "expiry_iso": "YYYY-MM-DD | null" }
      ]
    },

    "insider_activity": {
      "last_3y_summary": "≤300 chars — net buy/sell, patterns, notable names",
      "recent_transactions": [
        { "date_iso": "YYYY-MM", "insider_name": "string", "role": "CEO|CFO|Director|10% owner",
          "type": "buy|sell|option_exercise|gift", "shares": 0, "price_usd": 0 }
      ]
    },

    "clinical_trials": {
      "ongoing": [
        { "nct_id": "NCT00000000", "program": "string", "indication": "string",
          "phase": "Ph1|Ph2|Ph3|NDA", "status": "string",
          "enrollment_target": 0, "enrollment_current": 0,
          "primary_endpoint": "≤200 chars" }
      ],
      "interim_readouts_expected": [   /* future expected interim */
        { "program": "string", "phase": "Ph1|Ph2|Ph3",
          "expected_date_iso": "YYYY-QQ or YYYY-MM",
          "readout_type": "interim efficacy|safety|futility",
          "rationale": "≤200 chars — per mgmt guidance / CT.gov milestone" }
      ],
      "final_readouts_expected": [
        { "program": "string", "phase": "Ph1|Ph2|Ph3",
          "expected_date_iso": "YYYY-QQ or YYYY-MM",
          "readout_type": "primary analysis|topline|PFS|OS",
          "rationale": "≤200 chars" }
      ],
      "interim_results": [             /* actual past interim readouts (D43) */
        { "date_iso": "YYYY-MM", "program": "string", "phase": "Ph1|Ph2|Ph3",
          "indication": "string", "n_patients": 0,
          "key_metrics": "≤200 chars — actual numbers (ORR, PFS, AE rate, etc.) vs SOC / placebo",
          "result_summary": "≤300 chars — positive / mixed / negative + interpretation" }
      ],
      "final_results": [               /* actual past final/topline readouts (D43) */
        { "date_iso": "YYYY-MM", "program": "string", "phase": "Ph1|Ph2|Ph3",
          "indication": "string", "n_patients": 0,
          "key_metrics": "≤200 chars — actual numbers on primary + key secondary endpoints",
          "result_summary": "≤300 chars — primary endpoint hit/miss + clinical context" }
      ]
    },

    "competitive_landscape": [
      { "competitor": "string", "program": "string", "stage": "string",
        "differentiator_vs_subject": "≤120 chars" }
    ],

    "partnerships": [
      { "partner": "string", "deal_type": "development|licensing|option|commercialization",
        "value_usd_upfront": 0, "value_usd_potential_milestones": 0, "date_iso": "YYYY-MM" }
    ],

    "acquisition_target": {
      "score": 0.3,                          // 3-band 0.3/0.6/0.9
      "rationale": "≤250 chars — named strategic fits and rationale"
    },

    "mgmt_track_record_score": {             // D43 — feeds probability adjustment
      "score": 0.6,                          // 3-band 0.3 weak / 0.6 mixed / 0.9 strong
      "rationale": "≤300 chars — ≥1 historical example: stated guidance window, actual delivery, magnitude vs forecast"
    },

    "fda": {
      "lead_indication": "string",
      "regulatory_hurdles": "≤300 chars — specific, not generic",
      "base_rate_precedent": "≤200 chars — comparable program's regulatory outcome"
    },

    "rnpv_by_indication": [
      {
        "indication": "string",
        "program": "string",
        "stage": "Ph1|Ph2|Ph3|NDA|Approved",
        "pos_base_rate": 0.35,               // from PoS table in system prompt (stage × pathology)
        "pos_adjusted": 0.45,                // LLM's adjustment vs base, within ±15pp unless prior-data-justified
        "pos_rationale": "≤250 chars — why adjusted up/down",
        "tam_total_usd": 0,
        "peak_sales_addressable_usd": 0,     // subject's realistic capture after competitive share
        "years_to_peak": 5,
        "rnpv_contribution_usd": 0           // probability-weighted, discounted to today
      }
    ],
    "rnpv_total_usd": 0,
    "rnpv_per_share_usd": 0,                 // = rnpv_total_usd / fully_diluted_shares_count (D40)
    "rnpv_assumptions": "≤400 chars — WACC, royalty stack, tax, warrant-dilution treatment",

    "past_failures": [
      { "date_iso": "YYYY-MM", "program": "string",
        "event": "endpoint_miss|clinical_hold|CRL|partnership_break|going_concern|other",
        "impact": "≤120 chars — how it affects current thesis" }
    ],

    "research_notes": "≤800 chars — freeform synthesis tying the structured fields together"
  },

  // ────── SECTION B — Entry price ranges (per-ticker, shared across horizons) ──────
  "entry_price_ranges": {
    "fair_entry_low_usd": 0.0,
    "fair_entry_high_usd": 0.0,
    "fair_entry_rationale": "≤300 chars — MUST cite rNPV-per-share, % of rNPV used as anchor given stage, and cash-per-share floor",
    "full_reward_low_usd": 0.0,
    "full_reward_high_usd": 0.0,
    "full_reward_rationale": "≤300 chars — MUST cite cash-per-share or institutional-basis floor"
  },

  // ────── SECTION C — Per-horizon scoring inputs (LLM returns 3 fields; Python computes score) ──────
  "near_term_3mo": {
    "target_price_usd": 0.0,                 // LLM's expected price at catalyst
    "time_to_catalyst_weeks": 0,             // integer ≥ 1
    "probability": 0.55,                     // continuous 0.15–0.90 (D42)
    "catalyst_type": "earnings|trial_interim|trial_final|approval|conference_presentation|macro|other",
    "catalyst_detail": "≤120 chars",
    "thesis_summary": "≤300 chars",
    "key_risks": ["≤120 chars", "..."]
  },
  "long_term_12mo": { /* same shape */ }
}
```

### Python-side deterministic score (D42 / **revised D46**)

The **primary ranking score** is anchored on the **current market price** (sourced from the M5 pack's `market_snapshot.last_close_usd` at scoring time). This reflects the actual EV of buying TODAY rather than the academic EV of buying at the analyst-computed fair entry. The `fair_entry` / `full_reward` reference scores are still computed and stored for positioning analysis ("how much better would this look at a pullback?") but do NOT drive the ranking.

```python
# Sourced from the M5 pack at scoring time
current_price_usd = pack["market_snapshot"]["last_close_usd"]

# Per-ticker entry range midpoints (still used for reference scores)
fair_mid = (fair_entry_low_usd + fair_entry_high_usd) / 2.0
full_mid = (full_reward_low_usd + full_reward_high_usd) / 2.0

# Per-horizon scoring (for H in {3mo, 12mo})
months = max(1.0, time_to_catalyst_weeks / 4.33)
appreciation_from_current_pct = (target_price_usd - current_price_usd) / current_price_usd * 100.0
appreciation_from_fair_pct    = (target_price_usd - fair_mid) / fair_mid * 100.0
appreciation_from_full_pct    = (target_price_usd - full_mid) / full_mid * 100.0

score_at_current      = (appreciation_from_current_pct / months) * probability   # PRIMARY (D46)
score_at_fair         = (appreciation_from_fair_pct    / months) * probability   # reference
score_at_full_reward  = (appreciation_from_full_pct    / months) * probability   # reference

# Final ranking per ticker — uses score_at_current (D46)
final_horizon = argmax_H(score_at_current_H)
final_score   = max_H(score_at_current_H)
```

**Score units:** expected appreciation in percentage points per month **from current market price**, probability-weighted. A score of `10` = "pro-rata, this earns ~10%/month of expected value if bought at today's close." Negative scores (target < current price) rank last.

**Why current-price anchoring (D46):** The earlier fair-mid-anchored score (D42 original) produced theoretical-EV numbers that didn't reflect what an investor could actually capture buying today. NTLA at $15.87 with HAELO topline next week scored 210 %/mo at fair-mid $6.50 — meaningless because nobody could buy at $5.50. Re-anchored to current price, the same thesis scores 44.7 %/mo — actionable. Reference `score_at_fair` and `score_at_full_reward` remain so the user can still see the "wait for pullback" upside.

**`fair_entry` / `full_reward` ranges still required** — they drive the positioning gap shown in reports (`(current_price − fair_mid) / fair_mid`) and the `score_at_fair` reference column. The model's anchoring discipline is unchanged: `fair_entry` MUST cite rNPV-per-share + cash floor (HARD RULE #7), `full_reward` MUST cite cash-per-share floor (HARD RULE #8).

**Probability band anchors (continuous 0.15–0.90):**
- HIGH `0.70–0.90` — scheduled catalyst, precedent-backed direction, corroborating source, timing ±2w.
- MEDIUM `0.40–0.69` — expected-but-unscheduled OR scheduled with mixed precedent; timing ±4w.
- LOW `0.15–0.39` — mosaic-driven, ambiguous > 8w, thin evidence. Default for Tier-B "no material change".
- `< 0.15` or `> 0.90` forbidden — never claim certainty or impossibility.

**`catalyst_detail` free-text** pairs with the enum for filtering clarity: e.g. `catalyst_type=approval`, `catalyst_detail="Molgradex PDUFA autoimmune PAP Oct 2026"`. Persisted as indexed TEXT column.

**Pre-funded warrants (D40 HARD RULE).** `fully_diluted_shares_count` MUST include pre-funded warrants (instantly exercisable at $0.001, effectively shares). Small-mid cap biotechs often carry 20–50% dilution from PFWs; excluding them overstates rNPV-per-share by the same margin. Prompt enforces; Python validates that `fully_diluted_shares_count ≥ basic_shares_count + prefunded_warrants_count`.

**Deterministic rate computation in Python (NOT the LLM):**

```python
def rate(appreciation_pct: float, weeks: int) -> float:
    months = max(1.0, weeks / 4.33)
    return appreciation_pct / months

rate_3mo  = rate(r["near_term_3mo"]["expected_appreciation_pct"],
                 r["near_term_3mo"]["time_to_catalyst_weeks"])
rate_12mo = rate(r["long_term_12mo"]["expected_appreciation_pct"],
                 r["long_term_12mo"]["time_to_catalyst_weeks"])

final_rate   = max(rate_3mo, rate_12mo)
tighter      = abs(rate_3mo - rate_12mo) / max(abs(rate_3mo), abs(rate_12mo), 1e-9) < 0.05
final_horizon = "either" if tighter else ("3mo" if rate_3mo > rate_12mo else "12mo")
```

---

## Cost estimator (`scripts/6_estimate_cost.py`)

Standalone script run before any dispatch. Writes `Outputs/cost_estimate_{quarter}.html` and prints a summary to stdout.

**Compute (for feed of N tickers):**
- `prefix_tokens` — measured once from the actual system prompt (live count via `client.messages.count_tokens`).
- `suffix_tokens_avg` — average `pack_json` length in tokens; sample 5 packs to estimate.
- `max_output_tokens` — from `scoring.yaml`, e.g. 1500.
- Token totals:
  - Raw input: `N × (prefix_tokens + suffix_tokens_avg)`
  - Cache-read input (after first request): `(N − 1) × prefix_tokens`
  - Cache-creation (first request): `prefix_tokens`
  - Plain input: `N × suffix_tokens_avg`
  - Output: `N × max_output_tokens`

**Price three scenarios using Opus 4.7 pricing from `scoring.yaml::pricing`:**
- **No optimization** — list price on every token, no cache, no batch.
- **Cache only** — list price on cache-creation + plain input + output; cache-read tokens at 10% of base.
- **Cache + batch** — all prices halved on top of the cache-only scenario. *This is production.*

**Plus search fee line** (for all three): `N × max_uses × $0.010` upper bound, with a note that actual is usually lower.

**Output HTML shows all three side by side** + tokens breakdown + `[y/N]` approval is required in the calling script. No auto-dispatch.

---

## CLI (`scripts/6_score.py`)

```
python scripts/6_score.py                          # interactive, default mode=batch, tiered routing on
python scripts/6_score.py --mode sync              # parallel sync fan-out
python scripts/6_score.py --dry-run                # cost estimator only, no dispatch
python scripts/6_score.py --force-refresh          # bypass all caching; every ticker → Tier C
python scripts/6_score.py --no-light-refresh       # Tier A hits; Tier B demoted to Tier C
python scripts/6_score.py --refresh-threshold-weeks 6   # override both horizons to 6 weeks
python scripts/6_score.py --prompt-version m6-v2   # force a new prompt version
python scripts/6_score.py --quarter 2025Q4         # explicit quarter (defaults to latest)
python scripts/6_score.py --ticker TCRX            # single-ticker debug run
python scripts/6_score.py -v                       # verbose logging
```

---

## `config/scoring.yaml` (new)

```yaml
# ---------------- Prompt / model ----------------
model: claude-opus-4-7
prompt_version: m6-v2                                  # reshape from m6-v1 (D40): research_brief + entry ranges + probability-weighted score
max_output_tokens: 6000                                # raised from 1500 — research_brief is verbose
system_prompt_path: config/module_6_system_prompt.md
few_shot_examples_path: config/module_6_few_shots.md

# ---------------- Gates (D21 / D27 / D28 / D29) ----------------
# These are OVERRIDDEN by interactive prompts at run time. The YAML values
# below are fallback defaults, used when --non-interactive is passed.
gates:
  composite_best_min: 6.0
  industry_allowlist: ["Biotechnology", "Drug Mfrs Specialty & Generic"]
  market_cap_max_usd: 3700000000
  rescue:
    enabled: true
    new_positions_min: 1
    qoq_fund_count_change_min: 1
    excluded_archetypes:
      - extended_uptrend
      - late_stage_extension
      - broken_trend
      - sustained_decline
      - parabolic_blowoff

# ---------------- Web search (D22) ----------------
web_search:
  enabled: true
  max_uses: 12                # hard cap per ticker — raised from 5 for research-heavy m6-v2 (D40)
  whitelists_path: config/module_6_web_search_whitelists.yaml  # per-industry + research tier
  # allowed_domains per ticker = universal + research + by_industry[ticker.industry]

# ---------------- Dispatch ----------------
dispatch:
  mode: batch                 # "batch" | "sync"
  sync_concurrency: 8         # only used in sync mode
  poll_interval_s: 30         # batch polling cadence
  timeout_s: 86400            # batch hard deadline

# ---------------- Cross-run caches (D36 / D37 / D38 / D39) ----------------
cache:
  web_search_lookback_days: 180      # prior search content injected if newer than this (D36)
  prior_thesis_max_per_horizon: 2    # latest N prior llm_scores rows injected (D37)
  default_cache_reuse: true          # tiered routing on by default (D38); --force-refresh flips off
  refresh_threshold_weeks_3mo: 4     # Tier B window for 3mo horizon (D39)
  refresh_threshold_weeks_12mo: 8    # Tier B window for 12mo horizon (D39)
  tier_b_max_uses: 2                 # web_search cap for light-refresh calls
  tier_b_max_output_tokens: 400      # output cap for light-refresh calls

# ---------------- Rate calc ----------------
rate:
  tie_tolerance: 0.05         # final_horizon="either" when rates within 5%

# ---------------- Failure handling ----------------
failures:
  abort_threshold_pct: 5.0    # abort run if >5% tickers fail to parse

# ---------------- Pricing (for cost estimator) ----------------
# All prices per 1M tokens, USD. Keep in sync with Anthropic's pricing page.
pricing:
  input_per_mtok: 15.00
  output_per_mtok: 75.00
  cache_read_multiplier: 0.10    # cache-read = 10% of input
  cache_creation_multiplier: 1.25 # cache-write = 125% of input
  batch_discount: 0.50            # 50% off all tokens
  web_search_per_1k: 10.00        # $10 per 1000 searches
```

---

## Failure handling (point 10: SQL only, no JSON files)

- Malformed LLM JSON → row goes to `llm_errors` with `error_kind='json_parse_fail'`, `raw_text` preserved. Run continues.
- Anthropic API error (429, 500, timeout) → SDK retries internally; if it exhausts retries, row goes to `llm_errors` with `error_kind='api_error'`. Run continues.
- If aggregate failure rate > `failures.abort_threshold_pct` (default 5%) → run aborts, `llm_runs.finished_at` marked, summary printed.
- **No `.parquet` error dumps. No `.json` quarantine files.** Everything queryable from `llm_errors`.
- HTML report includes a collapsible "Parse failures (N)" section when `llm_errors` has rows for this run.

---

## Idempotency / cache

- Re-running Module 6 with unchanged `(quarter, prompt_version, model)` and same gates is **0 API calls** — every row is a cache hit in `llm_scores`.
- Changing `prompt_version` → all prior rows remain for audit but are ignored when computing the new run. `--use-cache` respects the current prompt_version.
- Changing `model` (e.g. Opus → Sonnet) is treated as a new cache namespace. Both coexist.
- `gate_config_json` on the `llm_runs` row reproduces exactly what gates were in force, so post-hoc audit of "why did ticker X not get scored on 2026-04-24" is one `SELECT`.

---

## Acceptance tests

| # | Test | Expected |
|---|---|---|
| 1 | Dry-run cost estimate on feed of 133 tickers | HTML opens, prints three scenarios, requires `[y/N]`, does not call API |
| 2 | Sync mode run on a 3-ticker `--ticker` slice | Scores written, `llm_runs` row, `llm_scores` has 3×2=6 rows (2 horizons × 3 tickers), raw_text non-empty |
| 3 | Batch mode on full feed, cold run | All tickers scored within SLA, `usd_cost_total` ≈ cache+batch scenario, `final_rankings` populated |
| 4 | Re-run same gates same prompt_version same model | `--use-cache` skips everything; $0; no API calls |
| 5 | Re-run with `--prompt-version m6-v2` | Full re-score; old rows untouched; `llm_scores` now has both versions per ticker |
| 6 | Inject malformed response for one ticker | Row lands in `llm_errors`; run completes; HTML report shows it |
| 7 | Interactive gate prompt accepts `custom` and adjusts feed size live | Count updates correctly at each prompt |
| 8 | Final ranking HTML sorted by `final_rate_pct_per_month` desc | Matches `SELECT * FROM final_rankings WHERE run_id=? ORDER BY final_rank` |
| 9 | `llm_responses_{quarter}.html` renders `raw_text` from SQL, no JSON on disk | Confirmed via `ls Outputs/` |

---

## Open items before implementation

1. ~~**Point 5 (web_search parameters)**~~ — **settled 2026-04-24.** Reshaped to `max_uses=12` + research tier in m6-v2 reshape (D40).
2. ~~**Point 3 (prompt text)**~~ — **reshaped 2026-04-24 to m6-v2.** See [config/module_6_system_prompt.md](../config/module_6_system_prompt.md) and [config/module_6_few_shots.md](../config/module_6_few_shots.md).
3. **Opus 4.7 pricing** — the values in `scoring.yaml::pricing` need to be verified against Anthropic's current pricing page at implementation time.
4. **Non-biotech industry whitelists** — device / diagnostics / healthcare-services tiers in the YAML are provisional. User reviews when a non-biotech ticker first surfaces in the feed.
5. **M4c fundamentals enrichment** — deferred. Once M4c ships (biotechnology-only first build), `research_brief.financials` and `research_brief.insider_activity` become pack-sourced, not LLM-searched. Drops `max_uses` back to ~6–8 and cuts per-ticker cost by ~40%. See memory `project_m4c_fundamentals_enrichment`.

Once these three are resolved, implementation proceeds in this order: cost estimator → `src/module_6/` → `scripts/6_score.py` → batch-file wiring → `spec/decisions.md` entries.
