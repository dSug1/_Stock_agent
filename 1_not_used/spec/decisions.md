# Implementation Decisions Log

Records decisions made during implementation that extend, calibrate,
or deviate from the original TimeStamp9 specification. Spec files
in `spec/` describe the *target system*; this file records *why*
and *how* the built code differs, with the ground truth for every
entry being the current codebase — not this document. When they
disagree, read the code (file paths linked below) and update this
log.

A new Claude Code conversation should read this file alongside the
relevant layer spec.

---

## Layer -1 decisions (Steps 1.1-1.3)

### Institution registry
- **Final count:** 34 institutions (not 25-35 as originally
  estimated).
- **ARCH Venture Partners CIK corrected** to `0001274403`
  (original `0000882603` returned 404 on EDGAR). Also renamed to
  `ARCH Venture Management LLC` to match SEC EDGAR record.
- **ARCH last filed 13F in 2022** — expect zero holdings in recent
  quarterly runs. Not an error.
- **Goehring & Rozencwajg** CIK corrected to `0001863154`
  (`Goehring & Rozencwajg Associates, LLC`).
- **Ariel Investments** CIK corrected to `0000936753`
  (`Ariel Investments, LLC`).

### TWOS calibration (April 2026, 3 quarters of data)
- **Active monitoring threshold** raised from 0.5 to **3.0**.
  Rationale: the 13 Tier 1A biotech specialist funds produce a
  large signal-driven universe; 0.5 was too loose.
- **Passive monitoring:** `0.5 <= TWOS < 3.0` and no active
  override triggered.
- **Watchlist:** `0 < TWOS < 0.5`.
- **Signal-override gate:** `any_significant_increase AND TWOS >= 0.3`
  (raised from 0.1 after calibration). Implemented at
  [twos_calculator.py:124-125](../src/layer_minus1/twos_calculator.py).
- **Tier 1A/1B minimum position gate:**
  `TIER1_MIN_MARKET_VALUE_USD = 5_000_000` (raw USD, not thousands).
  Defined at [twos_calculator.py:32](../src/layer_minus1/twos_calculator.py).
  Positions below $5M are treated as clerical/tracking positions —
  they still contribute to TWOS but do not trigger the Tier 1A/1B
  `new_position` / `significant_increase` active-monitoring
  override on their own.
- **Revised active universe target: 1,500-1,800 tickers.** Original
  150-250 estimate assumed a generalist institution mix; the actual
  biotech-specialist mix (13 Tier 1A funds collectively holding
  ~795 unique tickers) produces a larger but high-quality universe.
- **Empirical active count:** ~1,625 tickers (243 pure-TWOS + ~1,382
  signal-driven).

### CUSIP resolution
- **Overall OpenFIGI resolution rate pre-filter:** ~69% across 34
  institutions.
- Low rates (45-58%) for biotech Tier 1A funds are expected:
  private placements, warrants, foreign-listed biotechs,
  convertible notes, pre-IPO instruments.
- **Non-equity filter added** in April 2026:
  [cusip_resolver.py](../src/layer_minus1/cusip_resolver.py).
  Accept only `securityType == 'Common Stock'` or
  `'Depositary Receipt'`. Reject any record whose
  `securityType` / `securityType2` contains
  `ETF / ETP / Fund / Trust / Note / Bond / Preferred / Right / Warrant / Unit`.
  Reject check runs before accept check so composite labels like
  "ETF Common Stock" cannot slip through.
- Filtered-out CUSIPs logged at DEBUG (not WARNING) — 13F filings
  legitimately contain ETF/bond holdings; a warning per
  non-equity holding would flood the log.
- `cusip_ticker_map.security_type` column added in migration v6 to
  make the filter decision auditable per row.

### Database migrations (source of truth: [db.py](../src/database/db.py))
- **v1** — initial schema baseline (no migration function; `schema.sql`
  covers it on fresh DBs).
- **v2** — 13F ingestion tables: `institution_holdings`,
  `twos_scores`, `cusip_ticker_map`. (`cik_ticker_map` is NOT in
  this version — it was added in v7.)
- **v3** — `institutions.cik` + `institutions.edgar_name` columns.
- **v4** — `filings_log` table (filing-level dedup).
- **v5** — pre-2023 `institution_holdings.market_value` × 1000
  normalization. SEC Form 13F amendment effective 2023-01-03
  (Release No. 34-93978) changed `value` from thousands-of-USD
  to whole USD. This migration unifies everything to whole USD
  across history so downstream code can treat `market_value`
  uniformly.
- **v6** — `cusip_ticker_map.security_type` column.
- **v7** — Step 1.3 signal tables: `cik_ticker_map`, `form4_signals`,
  `thirteendg_signals`. Tables are created empty — Step 1.3
  Python modules (cik_resolver, form4_monitor, thirteendg_monitor)
  are not yet implemented at the time of this log entry.

### Performance
- **`run_quarterly_update` bulk rewrite** — replaced O(tickers ×
  institutions × 2) per-ticker queries with 3 bulk SELECTs + 2
  bulk executemany writes. Current runtime: **~0.5s to score
  5,815 tickers** (was previously 10-30 minutes at the old
  per-ticker query rate).
- **`ingest_all_institutions`** orchestration layer added at
  [edgar_13f_parser.py:210](../src/layer_minus1/edgar_13f_parser.py) —
  was missing from the original Step 1.2 scaffold.

### Signal freshness — known limitation
- Signal-driven active path uses all filings from `from_date`
  onwards (currently `2025-01-01`). A Tier 1A Q1 2025 new_position
  signal is treated identically to a Q3 2025 signal — no freshness
  decay.
- Acceptable now because `from_date=2025-01-01` caps signal age
  implicitly.
- **Planned enhancement: post-Step 5.4** (not "Layer 3 generic").
  Add a `signal_freshness_weight` that decays QoQ signals older
  than 2 quarters — a Q1 position not reinforced in Q2/Q3 is
  stale conviction.

### Step 1.3 — approved design decisions (migration v7 landed;
### Python modules pending)
- **File split:** `form4_monitor.py` and `thirteendg_monitor.py`
  implemented as two files. Spec §−1.8 originally listed a single
  `form4_monitor.py`; the split is an intentional deviation (Form 4
  P-code filter logic and 13D/13G ownership-threshold logic are
  unrelated and would interleave unnecessarily).
- **Layer 4 / Module 12 integration deferred to Step 8.1.** Step 1.3
  writes only to `form4_signals` and `thirteendg_signals`. No
  `alert_queue` inserts, no `action_records` inserts from this
  step.
- **Active-monitoring gate before XML download.** RSS atom gives
  issuer CIK → resolve to ticker via `cik_ticker_map` → look up
  `twos_scores.processing_tier` for the most recent `run_date` →
  if `!= 'active'`, skip the XML download and stamp the row
  `processing_status = 'skipped_not_active'`.
- **Daily polling (one poll per orchestrator run), not 15-minute.**
  15-minute cadence is a live-mode enhancement deferred.
- **Weekday check without holiday calendar.** `today.weekday() < 5`
  is sufficient for now. Holiday calendar deferred to Layer 0.
- **Dollar thresholds as constants** in `form4_monitor.py`:
  `CSUITE_MIN_USD = 500_000`, `DIRECTOR_MIN_USD = 100_000`. Same
  pattern as `TIER1_MIN_MARKET_VALUE_USD`.
- **Reading A on dollar thresholds:** always insert on P-code
  regardless of dollar amount; `source_tier` column reflects the
  dollar-bucket classification (1 = C-suite >$500K, 2 = Director
  >$100K, ...); Layer 4 decides what to do with low-tier signals
  when it comes online. **Do not** skip low-dollar P-codes at
  insert time.
- **`processing_status` column** on both signal tables from day
  one. Values: `pending` / `elevated` / `skipped_below_threshold`
  / `skipped_not_active`. On `thirteendg_signals` also
  `skipped_untracked_filer`. Default `'pending'`. Saves a schema
  bump at Step 8.1.

### Orchestrator
- **Entry point:** [run_1_Stock_Picker.bat](../run_1_Stock_Picker.bat)
  in `1_not_used/`, scheduled daily at 18:00 via Windows Task
  Scheduler. Renamed from `run_stockpicker.bat` and moved from the
  repo root into `1_not_used/` so the picker project is
  self-contained; the shared venv at `../.venv/` is still activated
  one level up from the .bat.
- **Orchestrator:** [daily_orchestrator.py](../scripts/daily_orchestrator.py)
  at `1_not_used/scripts/`.
- **PYTHONPATH=src** (not `.`), consistent with
  [CLAUDE.md](../CLAUDE.md). Imports are `from database.db ...`
  and `from layer_minus1.X ...`.
- **Saturday** (`INGEST_WEEKDAY = 5`): 13F ingest + TWOS
  recompute. Saturday chosen so the work runs when markets are
  closed and no interactive session competes for the DB file.
- **Staleness catch-up:** `INGEST_MAX_AGE_DAYS = 7`. If the PC was
  off the previous Saturday (or longer), the first available run
  whose last ingest is >= 7 days old forces ingest regardless of
  weekday. Prevents 14+ day gaps when a Saturday slot is missed.
  Implemented at
  [daily_orchestrator.py `_should_ingest`](../scripts/daily_orchestrator.py).
- **Weekdays Mon-Fri (Step 1.3 onwards):** Form 4 + 13D/13G polling.
  **Not yet wired.** Orchestrator currently has only the Saturday
  branch + a heartbeat-only default.
- **Weekends:** heartbeat log entry only.

### Scripts that exist in `1_not_used/scripts/`
Source of truth: `ls scripts/`.
- `check_state.py` — ad-hoc DB state inspection.
- `clear_etf_cusips.py` — deletes known non-equity tickers + all
  NULL rows from `cusip_ticker_map` so the tightened filter gets
  a chance to re-classify them.
- `daily_orchestrator.py` — the daily driver (described above).
- `diagnose_signals.py` — per-filer signal counts and market-value
  bucketing.
- `find_cik_candidates.py` — EDGAR CIK lookup helper used during
  institution-list construction.
- `probe_missing_ciks.py` — checks which seeded CIKs return 404
  on EDGAR.
- `sync_institution_seed.py` — re-applies the seed roster to
  `institutions` after CIK corrections.
- `twos_distribution.py` — TWOS score distribution and
  threshold-sensitivity diagnostic.

---

## Current implementation status

### Complete
- **Step 1.1** — Institution registry (34 institutions with
  corrected CIKs).
- **Step 1.2** — EDGAR 13F parser, TWOS calculator, ingestion
  orchestrator, CUSIP resolver (with non-equity filter), holdings
  store, daily orchestrator + Task Scheduler wiring.

### In progress
- **Step 1.3** — Form 4 / 13G / 13D monitor.
  - Done: migration v7 + schema.sql additions for `cik_ticker_map`,
    `form4_signals`, `thirteendg_signals`.
  - Pending: `cik_resolver.py`, `edgar_rss_parser.py`,
    `form4_monitor.py`, `thirteendg_monitor.py`, orchestrator
    weekday branch, spec §−1.7 / §−1.8 / §−1.9 updates.

### Not started
- Steps 1.4 through 11.4 — see [README.md](../README.md).

---

## Open questions / future decisions
- **OpenFIGI API key** — free tier (250/day) has been sufficient;
  consider a free API key if CUSIP resolution volume grows.
- **Holiday calendar** — deferred to Layer 0 (macro regime
  classifier).
- **15-minute Form 4 polling** — deferred; currently daily.
- **BlackRock Inc CIK** — listed as `0001364742` in the seed; not
  independently re-verified against EDGAR in recent work. Verify
  if a discrepancy surfaces.

---

## How to use this log

- When you implement a decision or calibration, add an entry here
  with a link to the code line (format: `[file.py:NN](path)`).
- When a decision changes, update the entry in place — don't
  accumulate superseded history. Git already preserves the prior
  version.
- If a decision in this log contradicts the current code, trust
  the code and fix the log. The code is ground truth.
