# Module 7 — Claude API deep-dive (and Module 6.5 — FDSC enrichment)

**Status:** DRAFT for review. Nothing in this spec has been built yet.
**Owners:** `3_Biopharmcatalyst_parser/`
**Depends on:** M6 (`catalyst_scores`), `2_Funds_parser/2_fundparser.db` (read-only).
**Reuses:** `2_Funds_parser/src/module_4c/edgar_client.py` (SEC XBRL fetch + capital-raise parser), `0_Renderer/2_stock_visualizer.py` (yfinance prices + local cache).

---

## 1. Goal & non-goals

### Goal
Convert the M6 hard-passing shortlist into a ranked **expectancy table**: per ticker, a probability-weighted expected stock-price move resolved against its single most-relevant catalyst, computed from a Claude API deep-dive *plus* the existing pipeline signals (insider purchases, fund accumulation, momentum).

### Non-goals (v1)
- **No portfolio optimisation.** M7 outputs ticker-level expectancy; sizing/budgeting is downstream.
- **No multi-horizon scoring.** Unlike `2_Funds_parser` M6 (which has 3mo + 12mo), M7 has **one horizon = the catalyst window**. The catalyst date is in `catalyst_timing.date_min / date_max`.
- **No tiered cache (no Tier A/B/C).** Catalyst events are short-lived; re-running on the same ticker is rare. Drop the ~300 LOC of complexity.
- **No light-refresh path.** Every dispatched ticker is a full deep-dive.

---

## 2. Strategy — what we ask Claude vs. what Python computes

We learned from `2_Funds_parser` D40 that **letting Claude see the same signals we use for ranking causes double-counting and groupthink**. So:

> Claude receives a context pack **without** insider/funds/momentum signals.
> Claude returns a clean clinical-science probability + asymmetric move estimates.
> Python applies the three pipeline signals as **small capped modifiers** on top.

### Probability (A)

```
p_clinical   ∈ [0.10, 0.90]                          Claude — pure science/precedent
m_insider    ∈ [0.85, 1.15]                          Python — CEO/CFO buys, log-scaled
m_funds      ∈ [0.85, 1.15]                          Python — specialist-fund accumulation
p_final      = clamp(p_clinical · m_insider · m_funds, 0.05, 0.95)
```

### Appreciation (B) — **asymmetric**, both legs required

```
move_on_hit_pct   = Claude's expected_move_on_hit_pct         capped at +400%
move_on_miss_pct  = Claude's expected_move_on_miss_pct        capped at −90%
m_momentum        ∈ [0.95, 1.05]                              Python — entry timing only
E[move_pct]       = p_final · move_on_hit_pct + (1 − p_final) · move_on_miss_pct
expectancy_pct    = E[move_pct] · m_momentum
```

### Final ranking key — time-normalised

```
expectancy_per_week_pct = expectancy_pct / max(weeks_to_catalyst, 1)
```

A +30% / 4-wk catalyst ranks above a +60% / 12-wk catalyst, which it should.

### Why these caps

| Modifier | Range | Why this shape |
|---|---|---|
| `m_insider` | [0.85, 1.15] | CEO/CFO buying = mild prior. M6 already uses it as a primary score component; here it's only a tilt, otherwise M7's expectancy collapses back onto M6. |
| `m_funds` | [0.85, 1.15] | Same reasoning. Specialist-fund accumulation is M6's third signal; M7 must not double-count it. |
| `m_momentum` | [0.95, 1.05] | Momentum is an **entry-timing** signal, not a science signal. A ticker that just rallied isn't more likely to have a successful readout — but you may want to wait for a pullback before sizing in. |
| `p_final` clamp | [0.05, 0.95] | Clinical biotech does not produce 99%-confident outcomes. Hard floor + ceiling prevents calibration runaway. |

The exact piecewise/log shapes live in `config/module_7.yaml`. **All modifiers are anchored at 1.0** (a neutral signal does not move expectancy at all).

---

## 3. Architecture

```
┌─── M6.5 — FDSC enrichment (NEW) ───────────────────────────────────┐
│  • Fetch shares_outstanding from SEC EDGAR XBRL                    │
│  • Estimate prefunded_warrants_count from capital-raise filings    │
│  • Fetch live share price (yfinance, cached)                       │
│  • Compute market_cap_fdsc_usd = (basic + PFW) × price             │
│  • Cache to data/fundamentals.db (mirror M4c schema)               │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─── M7 — Claude API deep-dive (NEW) ────────────────────────────────┐
│  1. Read M6 shortlist (hard_pass=1, user-selected slice)           │
│  2. Build per-ticker context pack (catalyst + fundamentals + drug) │
│  3. Estimate cost; render Outputs/cost_estimate_<date>.html        │
│  4. Mandatory [y/N] gate                                           │
│  5. Dispatch (Anthropic Batch API, prompt-cached prefix)           │
│  6. Parse + validate JSON; persist to data/claude_deep_dives.db    │
│  7. Apply Python modifiers → expectancy table                      │
│  8. Render Outputs/expectancy_ranking.html                         │
└────────────────────────────────────────────────────────────────────┘
```

---

## 4. Module 6.5 — FDSC enrichment

### 4.1 Why pre-fetch instead of asking Claude

`2_Funds_parser` learned (D54): fetching shares outstanding, cash, runway from SEC XBRL ahead of dispatch
- saves **~4 web_search calls per ticker** (≈40% of the 10-call budget)
- removes the most common parse error (FDSC < basic + PFW)
- makes the H1 market-cap filter use the **same** number Claude reasons against

### 4.2 Three data sources (each reusable)

| Source | Reuse from | What we get | Cache TTL |
|---|---|---|---|
| **SEC EDGAR XBRL** `companyfacts` | `2_Funds_parser/src/module_4c/edgar_client.py::fetch_companyfacts()` | `basic_shares_count`, cash, R&D, G&A, op-CF, runway, FY/FP | 30 days |
| **SEC EDGAR capital raises** (8-K Items 1.01/3.02, S-3, 424B5) | `2_Funds_parser/src/module_4c/edgar_client.py::fetch_capital_raise()` — already classifies `raise_type='pfw'` when the filing body mentions "pre-funded" | `prefunded_warrants_count` (estimate), recent raise history | 30 days |
| **yfinance** (live share price) | `0_Renderer/2_stock_visualizer.py::fetch_prices_batch()` — already does batched single-call yfinance fetches with a `_outputs/cache/prices.db` SWR cache | `last_close_usd`, `as_of_date` | 6 s during market hours, 1 day off-hours (per existing 0_Renderer policy) |

**Locked rules carried over:**
- Reuse `2_Funds_parser/layer_1/edgar_13f::_EDGAR_LIMITER` (9 req/s shared bucket — never spawn a parallel limiter; M2/M3 share this exact instance per spec §11 rule D5).
- Reuse `EDGAR_USER_AGENT` from `.env` (SEC fair-use requirement).
- All HTTP failures are **fail-open** — write `status='failed'` to `fetch_log`, continue with the next ticker. Caller decides whether the partial pack is good enough.

### 4.3 PFW estimation — heuristic v1

`module_4c/edgar_client.py` already classifies a raise as `pfw` when the filing body contains "pre-funded" / "prefunded". For v1:

```
prefunded_warrants_count(ticker) =
    SUM(capital_raises.shares_issued
        WHERE ticker = ?
          AND raise_type = 'pfw'
          AND filing_date >= today − 730 days)            # 2-year lookback
```

**Known limitation:** PFWs may have been exercised by the snapshot date; we have no easy way to detect this without 10-Q footnote parsing. Documented as "v2" in `module_4c` (line 354). For v1 we accept the over-count and audit-flag it via `pfw_source = 'capital_raises_sum_2yr'` so the user knows the provenance.

If PFW count > 25% of `basic_shares_count`, raise a soft warning in the pack (`pfw_share_dilution_warning = true`) — Claude reads this and can call out PFW dilution risk.

### 4.4 Output schema — `data/fundamentals.db`

**Mirrors `2_Funds_parser/data/fundamentals.db` exactly** so the M5 reader (`2_Funds_parser/src/module_5/fundamentals.py::load_fundamentals_for_tickers()`) drops in unchanged. Tables:

```sql
financials (
    ticker TEXT, period_end_date TEXT, form TEXT,
    cash_and_equivalents_usd, short_term_investments_usd, cash_total_usd,
    total_assets_usd, total_liabilities_usd,
    rd_expense_ttm_usd, ga_expense_ttm_usd, operating_cf_ttm_usd,
    quarterly_burn_usd, runway_months,
    basic_shares_count, prefunded_warrants_count, fully_diluted_shares_count,
    -- M7 additions:
    last_price_usd REAL,
    last_price_as_of TEXT,
    market_cap_fdsc_usd REAL,                            -- (basic + pfw) × price
    pfw_source TEXT,                                     -- 'capital_raises_sum_2yr' | 'xbrl_explicit' | null
    PRIMARY KEY (ticker, period_end_date)
);

capital_raises (...);                                    -- unchanged from M4c
fetch_log (...);                                         -- unchanged
```

`fully_diluted_shares_count = basic_shares_count + prefunded_warrants_count` is computed at write time (not asked of Claude).

### 4.5 CLI

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_5_enrich_fundamentals.py [--tickers TCRX,RCKT] [--force-refresh] [--dry-run]
```

No-arg default: read distinct tickers from `catalyst_scores WHERE hard_pass=1` and enrich any that are stale or missing.

### 4.6 Update points (per project memory)

- Wire into `run_3_Biopharmcatalyst_parser.bat` between M6 and M7.
- Update `daily_orchestrator.py` (per memory `feedback_update_daily_runner`).
- Append D-next to `spec/decisions.md` (per memory `feedback_update_decisions_log`).

---

## 5. Module 7 — Claude API deep-dive

### 5.1 Context pack — what we send Claude

One JSON pack per ticker, ~3–8 KB. **No insider, no funds, no momentum, no M6 composite_score** — those bias the deep-dive.

```python
{
  "identity": {
    "ticker": "TCRX",
    "company_name": "Tectonic Therapeutic",
    "drug": "TX45",
    "indication": "Pulmonary arterial hypertension (PAH)",
    "stage": "phase2",
    "nct_number": "NCT06234567",
  },
  "catalyst": {
    "next_catalyst_type": "Topline Data",
    "catalyst_text": "Topline Phase 2 APEX-HFpEF readout in Q3 2026",
    "snapshot_date": "2026-05-28",
    "date_min": "2026-07-01",
    "date_max": "2026-09-30",
    "weeks_to_catalyst_min": 5,
    "weeks_to_catalyst_max": 18,
    "precision_tier": "quarter",
    "fda_designations": ["FTD", "ODD"],                  # extracted from BPC drug field
  },
  "market_snapshot": {
    "last_price_usd": 24.13,
    "last_price_as_of": "2026-05-28",
    "basic_shares_count": 38_500_000,
    "prefunded_warrants_count": 4_200_000,
    "fully_diluted_shares_count": 42_700_000,
    "market_cap_fdsc_usd": 1_030_351_000,
    "pfw_share_dilution_warning": false,
  },
  "fundamentals": {
    "as_of_period_end": "2026-03-31",
    "cash_total_usd": 178_000_000,
    "quarterly_burn_usd": 22_000_000,
    "runway_months": 24.3,
    "rd_expense_ttm_usd": 71_000_000,
    "recent_capital_raises": [
      {"date_iso": "2025-11-12", "form": "424B5", "raise_type": "equity",
       "gross_proceeds_usd": 120_000_000, "shares_issued": 6_000_000,
       "price_per_share_usd": 20.00},
    ],
  },
  # M2 → M3 → M4 already produce these tables but
  # we deliberately do NOT include them in the pack:
  # • CEO/CFO insider buys              (used as m_insider modifier)
  # • Specialist fund accumulation      (used as m_funds modifier)
  # • 30d momentum                      (used as m_momentum modifier)
}
```

### 5.2 Cacheable system prefix

Copy `2_Funds_parser/src/module_6/prompt.py::load_cacheable_prefix()` **verbatim**. Two files concatenated with one `cache_control: {type: "ephemeral"}` breakpoint at the end:

```
config/module_7_system_prompt.md      ROLE, TASK, INPUT, OUTPUT, HARD RULES
config/module_7_few_shots.md          2–3 worked examples (different stages/MoA)
```

### 5.3 Web search tool

`web_search_20250305`, `max_uses: 10` per ticker (matches `2_Funds_parser` post-D54). Domain whitelist collapses to one list (no industry routing — all M7 tickers are biotech). Reuse the whitelist file content from `2_Funds_parser/config/module_6_web_search_whitelists.yaml`'s `universal + research + by_industry["Biotechnology"]` merged → `3_Biopharmcatalyst_parser/config/module_7_web_search_domains.yaml`.

### 5.4 Required output JSON schema (m7-v1)

The model returns one fenced ```json``` block. Top-level keys:

```jsonc
{
  "ticker": "TCRX",
  "reasoning_trace": "...",                              // free text, stored separately

  "drug_profile": {
    "moa": "Activin receptor type II ligand trap",
    "moa_class_precedent": "Acceleron sotatercept (acquired by MSD $11.5B)",
    "differentiation": "Receptor selectivity, half-life, dosing freq",
    "competition_landscape": "first_to_market | follower | crowded",
    "competition_bar_set_by_others": "describe / null",
    "patent_moat": {
      "composition_patent_expiry": "2039-04",            // ISO yyyy-mm or null
      "method_patent_expiry": "2041-08",
      "summary": "Composition + method coverage to 2039 + Orange Book LCM"
    },
    "fda_designations": ["FTD", "ODD"],
    "regulatory_pathway": "accelerated_approval | standard | breakthrough | null",
    "tam_usd": 4_200_000_000,
    "tam_rationale": "..."
  },

  "clinical_evidence": {
    "preclinical_summary": "...",
    "phase1_results": "...",
    "phase2_interim": "...",
    "phase2_final": null,                                // null if not yet read out
    "prior_class_successes": ["sotatercept-PAH-2024 (positive)"],
    "prior_class_failures": ["BMS-986278-IPF-2023 (failed phase 3)"]
  },

  "rnpv_by_indication": [
    {
      "indication": "PAH",
      "pos_base_rate": 0.28,                             // industry base rate for stage+indication
      "pos_adjusted": 0.42,                              // Claude's adjustment given the evidence
      "rnpv_contribution_usd": 1_200_000_000,
      "peak_sales_year": 2032,
      "rationale": "..."
    },
    {
      "indication": "HFpEF (platform optionality)",
      "pos_base_rate": 0.15,
      "pos_adjusted": 0.18,
      "rnpv_contribution_usd": 800_000_000,
      "rationale": "..."
    }
  ],
  "rnpv_total_usd": 2_000_000_000,
  "rnpv_per_share_usd": 46.84,                           // = rnpv_total / fully_diluted_shares
  "lead_indication": "PAH",

  "catalyst_outcome": {
    "p_clinical": 0.42,                                  // in [0.10, 0.90]
    "p_clinical_low": 0.30,                              // 1-sigma band
    "p_clinical_high": 0.55,
    "expected_move_on_hit_pct": 115.0,                   // 1-week move after positive readout
    "expected_move_on_miss_pct": -68.0,                  // 1-week move after negative readout
    "move_anchor_rationale": "Hit reprices toward 0.55 of rNPV/share; miss to cash floor +20%."
  },

  "financial_overhang": {
    "cash_runway_quarters": 8,
    "dilution_risk": "low | medium | high",
    "near_term_raise_likely": false,
    "rationale": "..."
  },

  "management_track_record": {
    "score": 0.65,                                       // [0, 1]
    "summary": "CEO led Selecta to Phase 3 partnership 2018; CMO ex-Novartis cardio."
  },

  "acquisition_target": {
    "score": 0.55,                                       // [0, 1]
    "rationale": "PAH franchise consolidation post-MRK/sotatercept makes TCRX a logical bolt-on."
  },

  "thesis_summary": "...",
  "key_risks": ["...", "..."],

  "catalyst_date_sanity_check": {
    "ir_page_consistent": true,
    "catalyst_passed_already": false,
    "notes": "Reaffirmed Q3 2026 in Q1 earnings call 2026-05-09."
  }
}
```

### 5.5 HARD RULES (validated at parse time; violations write `llm_errors` row)

| # | Rule | Why |
|---|---|---|
| #1 | `p_clinical ∈ [0.10, 0.90]` | Calibration discipline (`2_Funds_parser` HARD RULE #3). |
| #2 | `p_clinical_low ≤ p_clinical ≤ p_clinical_high` | Internal consistency. |
| #3 | `expected_move_on_miss_pct ≤ 0` AND `expected_move_on_hit_pct ≥ 0` | Sign discipline; the schema enforces direction. |
| #4 | `expected_move_on_hit_pct ≤ 400`, `expected_move_on_miss_pct ≥ −90` | Outlier clamp. |
| #5 | `rnpv_by_indication` non-empty list | Multi-asset companies must surface per-indication splits. |
| #6 | Every indication has `pos_base_rate ∈ [0, 1]` AND `pos_adjusted ∈ [0, 1]` | |
| #7 | `lead_indication` matches one of `rnpv_by_indication[].indication` (exact or substring) | Lead must be in the list. |
| #8 | `catalyst_date_sanity_check.catalyst_passed_already == false` (else write row with `error_kind='catalyst_already_passed'`) | M6-v3 D45 lesson — HAELO incident. |
| #9 | No prose outside the fenced ```json``` block | Reuse `2_Funds_parser/src/module_6/parsing.py::extract_json_block` + `_balance_truncated_json`. |
| #10 | If `competition_landscape == 'first_to_market'`, `competition_bar_set_by_others` must be `null` | Internal consistency. |

### 5.6 Modifier definitions (Python — applied after Claude responds)

```python
# Insider modifier — from existing v_executive_open_market_trades view (CEO×2 + CFO×1, log-scaled $5M cap)
# Maps M6's existing insider_score ∈ [0, 100] → m_insider ∈ [0.85, 1.15]:
m_insider = 0.85 + (insider_score / 100.0) * 0.30

# Funds modifier — from 2_Funds_parser holdings cross-DB (M6's existing fund_accumulation_score)
m_funds = 0.85 + (fund_accumulation_score / 100.0) * 0.30

# Momentum modifier — from BPC price_history_30d (M6's existing momentum_score)
# Tighter range — momentum is timing, not thesis:
m_momentum = 0.95 + (momentum_score / 100.0) * 0.10
```

This is a **straight linear remap** of M6's existing 0–100 signal scores onto the bounded modifier ranges. No new computation, no new web fetches. All three constants live in `config/module_7.yaml` and can be re-tuned without touching code or re-paying for API calls.

### 5.7 Output schema — `data/claude_deep_dives.db`

Separate DB from `data/biotech.db` so M7 reruns don't churn the M0–M6 audit trail. Mirrors `2_Funds_parser/llm_scores.db` shape.

```sql
CREATE TABLE deep_dives (
  -- PK — matches catalyst_snapshots composite PK plus run_id
  snapshot_date TEXT NOT NULL,
  ticker TEXT NOT NULL,
  drug TEXT NOT NULL,
  nct_number TEXT NOT NULL,
  next_catalyst_type TEXT NOT NULL,
  run_id INTEGER NOT NULL,

  -- Claude raw fields
  p_clinical REAL, p_clinical_low REAL, p_clinical_high REAL,
  expected_move_on_hit_pct REAL, expected_move_on_miss_pct REAL,
  rnpv_total_usd REAL, rnpv_per_share_usd REAL,
  lead_indication TEXT,
  rnpv_by_indication_json TEXT,                  -- full list
  drug_profile_json TEXT,                        -- moa, moat, competition, patents, TAM
  clinical_evidence_json TEXT,
  financial_overhang_json TEXT,
  management_track_record_score REAL,
  acquisition_target_score REAL,
  thesis_summary TEXT, key_risks_json TEXT,
  reasoning_trace TEXT,

  -- Compounded — what Python computed
  insider_score_input REAL,                      -- copied from M6 for audit
  fund_accumulation_score_input REAL,            -- copied from M6 for audit
  momentum_score_input REAL,                     -- copied from M6 for audit
  m_insider REAL, m_funds REAL, m_momentum REAL,
  p_final REAL,
  e_move_pct REAL,
  expectancy_pct REAL,                           -- = e_move_pct × m_momentum
  weeks_to_catalyst_mid INTEGER,
  expectancy_per_week_pct REAL,                  -- ranking key

  -- Metadata
  prompt_version TEXT,                           -- 'm7-v1'
  model TEXT,                                    -- 'claude-opus-4-7'
  response_id TEXT,
  raw_text TEXT,
  input_tokens INTEGER, output_tokens INTEGER,
  cache_read_tokens INTEGER, cache_creation_tokens INTEGER,
  web_search_calls INTEGER,
  usd_cost REAL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type, run_id)
);

CREATE TABLE deep_dive_runs (
  run_id INTEGER PRIMARY KEY,
  snapshot_date TEXT, prompt_version TEXT, model TEXT,
  mode TEXT,                                     -- 'sync' | 'batch'
  feed_size INTEGER,
  batch_id TEXT,                                 -- Anthropic batch UUID (NULL for sync)
  gate_config_json TEXT,                         -- selection_source + cost ceiling + caps
  wall_time_s REAL,
  input_tokens_total INTEGER, output_tokens_total INTEGER,
  cache_read_tokens_total INTEGER, cache_creation_tokens_total INTEGER,
  web_search_calls_total INTEGER,
  usd_cost_total REAL, usd_cost_list_price REAL,
  opened_at TEXT, closed_at TEXT
);

CREATE TABLE deep_dive_errors (
  run_id INTEGER, ticker TEXT,
  error_kind TEXT,                               -- 'json_parse_fail' | 'schema_violation' | 'api_error' | 'catalyst_already_passed' | 'db_write_error'
  error_detail TEXT, raw_text TEXT,
  created_at TEXT
);

CREATE TABLE web_search_cache (
  url TEXT PRIMARY KEY, ticker TEXT,
  snapshot_date TEXT, run_id INTEGER,
  search_query TEXT, title TEXT, content TEXT,
  domain TEXT, published_date TEXT,
  cached_at TEXT
);

-- No separate expectancy_rankings table needed.
-- The deep_dives row already carries p_final / e_move_pct / expectancy_pct /
-- expectancy_per_week_pct. The renderer ATTACHes claude_deep_dives.db and
-- LEFT-JOINs deep_dives onto catalyst_scores; sorting + ranking happen in the
-- HTML/JS layer (so the user can resort on the fly without re-rendering).
-- Per-run audit is recovered via (snapshot_date, ticker, …, MAX(run_id)) in
-- the rendering query.
```

### 5.8 Dispatch — copied verbatim from `2_Funds_parser` M6

| Pattern | Source | Why preserve |
|---|---|---|
| `cache_control: {type: "ephemeral"}` on the system block | `dispatch.py:253-263` | 100% cache hit across a run |
| `submit_batch()` + `poll_and_collect_batch()` split | `dispatch.py:332-369` | Crash recovery (`--resume-run N`) |
| Persist `batch_id` to `deep_dive_runs` *before* polling starts | `6_score.py:1000-1013` | Same |
| Per-result `commit()` in the parse loop | `6_score.py:1147` | One bad row doesn't roll back the batch |
| Mandatory `[y/N]` gate, only `--yes` bypasses, EOFError → abort | `6_score.py:898-922` | Memory: `claude-api` + D48 |
| `cost_calibration_factor: 0.10` in pricing | `scoring.yaml:89` | Memory: `project_anthropic_cost_calibration` |
| `extract_json_block` + `_balance_truncated_json` | `parsing.py:70-149` | Tolerates truncated responses |
| Web-search result writeback to `web_search_cache` | `dispatch.py:188-232` + `6_score.py:1135-1146` | Audit + future Tier B prior research (if added) |

### 5.9 Cost estimate

Same three-scenario engine (`no-optim`, `cache-only`, `cache+batch`) as M6. Reuse `2_Funds_parser/src/module_6/cost_estimate.py` with two changes:
1. Drop Tier B/C breakdown (M7 is single-tier full).
2. Read pricing from `config/module_7.yaml::pricing` instead.

Estimated cost per 20–30 ticker run (per `2_Funds_parser` empirical billing × calibration factor 0.10): **~$5–15**.

### 5.10 Selection editor

Per memory `feedback_browser_writes_via_local_server`: use a local HTTP server (stdlib `http.server`) + sidecar JSON, not the FSA API. Reuse `2_Funds_parser/scripts/6_serve_report.py` wholesale.

The existing `Outputs/catalyst_scores.html` (M6 output) needs a leftmost checkbox column added — the same pattern as `final_ranking_<quarter>.html` in M6. M7 dispatcher reads `catalyst_scores_selection.json` (sidecar) to learn which tickers the user marked.

### 5.11 Reports — **extend `Outputs/catalyst_scores.html`** (do NOT add a new HTML)

The M7 deep-dive output is rendered *inside the existing M6 report*, not in a sibling file. The user opens one HTML to see both the M6 ranking and the M7 deep-dive on the same row.

Same template+sidecar split as the rest of the project (memory `feedback_html_template_data_split`):
- Template `Outputs/catalyst_scores.html` — gains three new columns and an extended expanded-row body.
- Sidecar `Outputs/catalyst_scores_data.js` — gains the deep-dive payload joined onto each row.

#### 5.11.1 New visible columns (appended to the right of the existing M6 columns in the `defined` and `excluded` tabs)

| # | Column header | Source | Format | Sort default |
|---|---|---|---|---|
| Nm+1 | **Probability** | `deep_dives.p_final` | `42%` (1-decimal if <10%) | desc |
| Nm+2 | **Share price appreciation** | `deep_dives.e_move_pct` | `+58%` (signed; red if negative) | desc |
| Nm+3 | **Expectancy / time** | `deep_dives.expectancy_per_week_pct` | `+3.2% / wk` | desc — **primary sort key when M7 has run** |

Rows for which M7 has not run (no matching `deep_dives` row for the current snapshot) show `—` in the three new columns and sort to the bottom of the M7-sorted view. The existing M6 composite-score sort remains available via the column header click.

The `undefined` tab (catalyst date is unresolved beyond half/year buckets) is left untouched — M7 only dispatches for `precision_tier ∈ {specific, conference, month, quarter}`.

#### 5.11.2 Expanded row — deep-dive content **below the BPC catalyst text**

Existing M6 expanded-row content stays as-is (signal breakdown, CEO/CFO buys, per-fund position deltas). The expanded row now appends a new `<section class="m7-deep-dive">` *after* the BPC catalyst text block, structured like this (markdown shown for clarity; rendered HTML uses the same monospace blocks as the rest of the report):

```
─────────────────────────────────────────────────────────────
 BPC catalyst text:
   "Topline Phase 2 APEX-HFpEF readout in Q3 2026"           ← existing
─────────────────────────────────────────────────────────────
 Claude deep-dive (m7-v1, run #N, 2026-09-02 18:24 UTC)      ← NEW

 ┌─ Thesis ───────────────────────────────────────────────┐
 │ <Claude thesis_summary>                                │
 └────────────────────────────────────────────────────────┘

 Probability breakdown
   p_clinical (Claude)         0.42  [0.30–0.55]
   m_insider                   1.05  (insider_score = 67)
   m_funds                     1.12  (fund_score = 82)
   p_final                     0.49           ← clamped to [0.05, 0.95]

 Move estimates (1-week post-readout)
   expected_move_on_hit       +115%
   expected_move_on_miss       −68%
   m_momentum                  1.02  (momentum_score = 70)
   E[move]                     +21.2%
   expectancy_pct              +21.6%
   weeks_to_catalyst (mid)     12
   expectancy / week           +1.80%

 Drug profile
   MoA                        Activin receptor type II ligand trap
   MoA class precedent        Acceleron sotatercept (acquired MSD $11.5B)
   Differentiation            Receptor selectivity, half-life, dosing freq
   Competition landscape      follower
   Competition bar            Sotatercept STELLAR p<0.001 6MWD +40m
   FDA designations           FTD, ODD
   Regulatory pathway         accelerated_approval
   Patent moat                Composition 2039-04 / Method 2041-08
   TAM                        $4.2B (rationale: <Claude>)

 rNPV by indication
   ┌─ Indication ──── Stage ── POS base ── POS adj ── rNPV ($M) ─┐
   │ PAH              Ph2      0.28        0.42        1,200    │
   │ HFpEF (platform) Ph2      0.15        0.18          800    │
   └────────────────────────────────────────────────────────────┘
   rNPV total: $2.0B    rNPV / share: $46.84    vs. price: +94%

 Clinical evidence
   Preclinical          <text>
   Phase 1              <text>
   Phase 2 interim      <text>
   Phase 2 final        (none yet)
   Prior class wins     sotatercept-PAH-2024 (positive)
   Prior class losses   BMS-986278-IPF-2023 (failed phase 3)

 Financial overhang
   Cash runway          8 quarters
   Dilution risk        low
   Near-term raise      no

 Management track record   0.65   <one-line summary>
 Acquisition target        0.55   <one-line rationale>

 Key risks
   • <risk 1>
   • <risk 2>

 Catalyst-date sanity check
   IR page consistent: true
   Already passed:     false
   Notes:              Reaffirmed Q3 2026 in Q1 earnings call 2026-05-09

 Reasoning trace                                  [show / hide]
   <expandable — Claude's full chain-of-thought, monospace>

 Audit
   model              claude-opus-4-7
   prompt_version     m7-v1
   tokens (in/out)    91,200 / 4,800
   cache (read/cre)   12,400 / 1,800
   web_search calls   10
   usd_cost (paid)    $1.23
   response_id        msg_01abc…                  [view raw JSON]
─────────────────────────────────────────────────────────────
```

The "view raw JSON" link toggles `deep_dives.raw_text` inline (the full Anthropic reply verbatim, per D32 — no separate JSON file on disk).

#### 5.11.3 Sidecar payload extension

`Outputs/catalyst_scores_data.js` adds a `deep_dive` key per row. The renderer ATTACH-es `claude_deep_dives.db` read-only and writes:

```js
window.__DATA = {
  // …existing M6 fields per row…
  rows: [
    {
      snapshot_date: "2026-09-02",
      ticker: "TCRX",
      // …existing M6 columns…
      deep_dive: {                                      // ← NEW; null when M7 hasn't run
        run_id: 7,
        run_completed_at: "2026-09-02T18:24:00Z",
        prompt_version: "m7-v1",
        p_clinical: 0.42, p_clinical_low: 0.30, p_clinical_high: 0.55,
        m_insider: 1.05, m_funds: 1.12, m_momentum: 1.02,
        insider_score_input: 67, fund_accumulation_score_input: 82, momentum_score_input: 70,
        p_final: 0.49,
        expected_move_on_hit_pct: 115.0, expected_move_on_miss_pct: -68.0,
        e_move_pct: 21.2, expectancy_pct: 21.6,
        weeks_to_catalyst_mid: 12,
        expectancy_per_week_pct: 1.80,
        drug_profile: { /* full Claude block */ },
        clinical_evidence: { /* full Claude block */ },
        rnpv_by_indication: [ /* full list */ ],
        rnpv_total_usd: 2_000_000_000,
        rnpv_per_share_usd: 46.84,
        lead_indication: "PAH",
        financial_overhang: { /* full Claude block */ },
        management_track_record: { score: 0.65, summary: "…" },
        acquisition_target: { score: 0.55, rationale: "…" },
        catalyst_date_sanity_check: { /* full Claude block */ },
        thesis_summary: "…",
        key_risks: ["…"],
        reasoning_trace: "…",
        usd_cost: 1.23,
        tokens: { input: 91200, output: 4800, cache_read: 12400, cache_creation: 1800 },
        web_search_calls: 10,
        response_id: "msg_01abc…",
        raw_text_id: 1042                                 // FK into deep_dives row; raw_text streamed lazily on "view raw JSON" click
      }
    }
  ]
};
```

`raw_text` is held in the DB, not in `_data.js` (the verbatim Anthropic replies are 30–80 KB each — bundling all of them into the sidecar would bloat the file 10–30× and slow first paint). The local HTTP server returns it on demand for the "view raw JSON" click.

#### 5.11.4 Renderer ownership

`scripts/3_6_render_scores.py` is **extended** to read `claude_deep_dives.db` via `ATTACH DATABASE … AS dd` and LEFT-JOIN deep-dive rows onto its existing query. No new renderer script.

Template-version hash (memory `feedback_html_template_data_split`) bumps when M7 columns/sections are added, so the `.html` file is rewritten once on first M7 deploy and then only on actual template changes; the `.js` sidecar rewrites every pipeline run as today.

#### 5.11.5 Selection editor stays on the same page

The local HTTP server (`scripts/3_7_serve_selection.py`) serves the same `Outputs/catalyst_scores.html` so the user has one URL. M6 selection checkboxes (existing) and the deep-dive expanded section coexist. After M7 runs, checked rows that successfully scored get their three M7 columns filled in; unchecked rows stay `—`.

---

## 5.12 Storage rules (locked — carried from 2_Funds_parser M5 rev 2 + D32 + D49)

These four rules govern every file M6.5 / M7 writes. They mirror what M5/M6 of `2_Funds_parser` and the existing 3_Biopharm M5/M6 already do:

1. **Machine-generated pipeline data → SQLite, never JSON files on disk.**
   Applies to: FDSC enrichment results, Claude raw replies, parsed fields, web_search results, expectancy projections. Verbatim Anthropic replies sit in a `raw_text TEXT` column (`deep_dives.raw_text`) so SQL queries can grep them. Nested objects (`rnpv_by_indication`, `drug_profile`, etc.) sit in TEXT columns named `*_json` — they are JSON-encoded inside SQLite, not files on disk. (2_Funds_parser M5 rev 2 + D32.)

2. **User-edited state → sidecar JSON file, not the DB.**
   The DB never carries per-user state. Applies to: the selection checkboxes the local HTTP server reads/writes. File: `Outputs/catalyst_scores_selection.json` (already exists from M6; M7 reuses it). The DB carries the *eligible pool* (`hard_pass=1`); the sidecar carries *which of those the user picked*. (2_Funds_parser D49 + D50.)

3. **HTML reports → template + `.js` data sidecar split.**
   Template `Outputs/catalyst_scores.html` is hash-versioned and rewritten only when the renderer's CSS/JS/markup changes. `Outputs/catalyst_scores_data.js` (`window.__DATA = {...};`) is the per-run data payload — rewritten every pipeline run. M7 adds the `deep_dive` key per row into the existing sidecar; it does NOT produce a new HTML. (3_Biopharm D11 + memory `feedback_html_template_data_split`.)

4. **Raw Anthropic replies stay in the DB, not in the sidecar.**
   `deep_dives.raw_text` is 30–80 KB per row. Bundling 20–30 of them into `catalyst_scores_data.js` would 10–30× the sidecar size and slow first paint. The renderer emits a `raw_text_id` foreign key per row; the local HTTP server returns the raw text on demand when the user clicks "view raw JSON" in the expanded section.

If a future module wants to write a `.json` file under `Outputs/` or `data/`, that's a deliberate spec deviation requiring a new decisions.md entry — these four rules are not a default to be quietly overridden.

---

## 6. Config — `config/module_7.yaml`

```yaml
model: claude-opus-4-7
prompt_version: m7-v1
max_output_tokens: 12500
system_prompt_path: config/module_7_system_prompt.md
few_shot_examples_path: config/module_7_few_shots.md

web_search:
  enabled: true
  max_uses: 10
  domains_path: config/module_7_web_search_domains.yaml

dispatch:
  mode: batch                                 # 'batch' | 'sync'
  sync_concurrency: 8
  poll_interval_s: 30
  timeout_s: 86400

selection:
  source_html: Outputs/catalyst_scores.html
  sidecar_json: Outputs/catalyst_scores_selection.json
  hard_pass_only: true

modifiers:
  insider:    { min: 0.85, max: 1.15 }
  funds:      { min: 0.85, max: 1.15 }
  momentum:   { min: 0.95, max: 1.05 }

clamps:
  p_final_min: 0.05
  p_final_max: 0.95
  move_on_hit_pct_max: 400
  move_on_miss_pct_min: -90

# Pricing — verified against current Anthropic page on first run
pricing:
  model: claude-opus-4-7
  input_per_mtok: 15.00
  output_per_mtok: 75.00
  cache_read_multiplier: 0.10
  cache_creation_multiplier: 0.10              # empirical, not docs (memory: project_anthropic_cost_calibration)
  batch_discount: 0.50
  web_search_per_1k: 10.00
  search_result_avg_tokens: 1500
  cost_calibration_factor: 0.10                # memory: project_anthropic_cost_calibration

cost_ceiling_usd: 50.0                         # estimator aborts dispatch if scenario_cache_batch.total_usd > this
```

---

## 7. File map (planned)

```
3_Biopharmcatalyst_parser/
├─ run_3_Biopharmcatalyst_parser.bat               # adds M6.5 + M7 steps
├─ data/
│   ├─ biotech.db                                  # unchanged
│   ├─ fundamentals.db                             # NEW — M6.5 output
│   └─ claude_deep_dives.db                        # NEW — M7 output
├─ config/
│   ├─ scoring.yaml                                # unchanged
│   ├─ module_7.yaml                               # NEW
│   ├─ module_7_system_prompt.md                   # NEW — cacheable prefix
│   ├─ module_7_few_shots.md                       # NEW — 2-3 worked examples
│   └─ module_7_web_search_domains.yaml            # NEW — biotech-collapsed whitelist
├─ scripts/
│   ├─ 3_6_5_enrich_fundamentals.py                # NEW — M6.5 CLI
│   ├─ 3_7_estimate_cost.py                        # NEW — dry-run, no API call
│   ├─ 3_7_deep_dive.py                            # NEW — M7 dispatcher (gate, batch, parse, write)
│   ├─ 3_6_render_scores.py                        # MODIFIED — LEFT-JOIN claude_deep_dives.db; render 3 new columns + expanded-row section
│   └─ 3_7_serve_selection.py                      # NEW — local HTTP server (copy of 2_Funds_parser 6_serve_report.py); also serves on-demand raw_text
└─ src/
    ├─ module_6_5/                                 # NEW — FDSC enrichment
    │   ├─ __init__.py
    │   ├─ edgar_client.py                         # thin wrapper importing 2_Funds_parser/src/module_4c/edgar_client
    │   ├─ price_client.py                         # thin wrapper importing 0_Renderer/2_stock_visualizer fetch_prices_batch + cache
    │   ├─ pfw_estimator.py                        # PFW heuristic v1
    │   ├─ fundamentals_db.py                      # mirror of 2_Funds_parser/src/module_4c/fundamentals_db.py
    │   └─ enrich.py                               # orchestrator (per-ticker fail-open)
    └─ module_7/                                   # NEW — Claude deep-dive
        ├─ __init__.py
        ├─ config.py                               # pydantic loader for module_7.yaml
        ├─ context_pack.py                         # per-ticker pack builder (joins biotech.db + fundamentals.db)
        ├─ prompt.py                               # copy of 2_Funds_parser/src/module_6/prompt.py
        ├─ dispatch.py                             # copy + simplify (drop Tier B)
        ├─ parsing.py                              # m7-v1 schema validators
        ├─ scoring.py                              # modifier remaps + expectancy compounding
        ├─ cost_estimate.py                        # copy of 2_Funds_parser/src/module_6/cost_estimate.py (single-tier)
        ├─ deep_dives_db.py                        # schema + writes
        └─ render_join.py                          # ATTACH claude_deep_dives.db; build per-row deep_dive sidecar payload (consumed by 3_6_render_scores.py)
```

---

## 8. Test plan

| Suite | Coverage |
|---|---|
| `test_module_6_5_edgar_client.py` | Reuse fixtures from `2_Funds_parser/tests/test_edgar_client.py` — XBRL shapes, 404 fail-open, conditional GET |
| `test_module_6_5_pfw_estimator.py` | PFW = sum of capital_raises.shares_issued where raise_type='pfw' in 2y window; dilution-warning threshold |
| `test_module_6_5_fundamentals_db.py` | Mirror M4c schema test; `market_cap_fdsc_usd = (basic + pfw) × price` round-trip |
| `test_module_7_context_pack.py` | Pack does NOT contain insider/funds/momentum/M6-composite; FDA designations parsed from drug field |
| `test_module_7_parsing.py` | All 10 HARD RULES — `pos_base_rate ∈ [0,1]`, asymmetric signs, lead indication match, catalyst-passed sanity, JSON fence recovery |
| `test_module_7_scoring.py` | Modifier linear remaps; `p_final` clamp; asymmetric E[move]; time normalisation |
| `test_module_7_dispatch.py` | Cost-gate `[y/N]`, EOFError → abort, `--yes` bypass; batch submit/poll split |
| `test_module_7_resume.py` | `--resume-run N` recovers a half-completed batch |
| `test_module_7_reports.py` | HTML template-version hash detection (memory `feedback_html_template_data_split`); sidecar has expected fields |

Target: ~80 tests added on top of the existing 279 → ~360 total.

---

## 9. Open questions (locked by user 2026-05-28 in this conversation)

| # | Decision | Status |
|---|---|---|
| Q1 | Claude does NOT see M6 composite_score / hard_pass | LOCKED |
| Q2 | Web search budget = 10 calls / ticker, re-tune after first live run | LOCKED |
| Q3 | Move estimates apply to whole stock (not per-indication rNPV contribution) | LOCKED |
| Q4 | Claude returns pure science p_clinical; Python applies modifiers | LOCKED — Option C |
| Q5 | Compound formula: `p_final = p_clinical × m_insider × m_funds`, `expectancy_pct = E[move] × m_momentum` | LOCKED |
| Q6 | Final ranking key = `expectancy_per_week_pct` | LOCKED |
| Q7 | FDSC pre-fetch as separate module M6.5 (mirror 2_Funds_parser M4c + 0_Renderer prices) | LOCKED |

Still TBD before implementation:

| # | Question | Default proposed |
|---|---|---|
| Q8 | Should `move_on_hit/miss` be capped harder than the schema bound? | No additional cap — defer to first live-run calibration |
| Q9 | Output HTML location | LOCKED — **extend `Outputs/catalyst_scores.html` in place** (no separate file). Three new columns + deep-dive content below the catalyst text in the expanded row. Renderer = the existing `3_6_render_scores.py` with a LEFT-JOIN against `claude_deep_dives.db`. |
| Q10 | Should batch failures (Anthropic content-filter, max_tokens) trigger an automatic single-ticker sync retry? | No for v1 — surface in end-of-run summary like M6 does, user manually retries with `--tickers X --mode sync --yes` |
| Q11 | Should we pull additional fields from M6 into the pack (e.g., the BPC catalyst sentiment score)? | No for v1 — keep Claude's deep-dive evidence-independent from BPC's own scoring |

---

## 10. Decisions to log (when we proceed)

- **D-next: Module 7 design pinned** — strategy (this file), modifier shapes, schema, single-horizon catalyst-window scoring.
- **D-next+1: Module 6.5 FDSC enrichment** — SEC XBRL + capital-raise PFW heuristic + yfinance price → `data/fundamentals.db`.

---

## 11. References

- `2_Funds_parser/spec/module_6_spec.md` — parent design pattern
- `2_Funds_parser/spec/decisions.md` D40 (strip fund_accumulation from pack), D44 (cost calibration), D45 (catalyst-date sanity HARD RULE), D48 (cost gate locked), D49 (sidecar JSON pattern), D51 (batch crash recovery), D54 (financials pre-fetch + new HARD RULES)
- `2_Funds_parser/src/module_4c/edgar_client.py` — SEC XBRL + capital-raise parser (reused as-is by M6.5)
- `2_Funds_parser/src/module_6/{prompt,dispatch,parsing,cost_estimate}.py` — copy-pasta scaffolding for M7
- `0_Renderer/2_stock_visualizer.py::fetch_prices_batch + fetch_meta` — live price source, SWR-cached
- Memory: `claude-api`, `project_anthropic_cost_calibration`, `feedback_html_template_data_split`, `feedback_browser_writes_via_local_server`, `feedback_cache_user_prefs`, `feedback_swr_pattern`, `project_data_provider_switch`
