# 2_Funds_parser — Implementation Decisions Log

**Purpose.** Record decisions made during implementation that extend, calibrate, or deviate from the module specs in `spec/`. Spec files describe the *target system*; this log records *why* and *how* the built code differs. Ground truth is the current codebase — when decisions.md and code disagree, fix decisions.md.

**Related log.** [decisions_module_4.md](decisions_module_4.md) holds the design-rationale decisions (D1–D18) for Module 4's "train hasn't left" filter and archetype ranking. Those are pre-implementation design choices; this file is the cross-module implementation log and does not duplicate them.

**Update discipline.** At the end of every module step: did anything get decided, calibrated, renamed, or deviate from spec? If yes, append or revise the entry for that module with a link to the code line of record (`[file.py:NN](../src/path/file.py#L<n>)`). If no, state that explicitly in the end-of-step summary.

**Last updated:** 2026-04-22

---

## Module 1 — Configuration & Foundation

Spec: [module_1_spec.md](module_1_spec.md). Implemented 2026-04-22 under [src/module_1/](../src/module_1/). Smoke test: [scripts/verify_module_1.py](../scripts/verify_module_1.py).

### Output folders — three distinct buckets
- **`Outputs/`** — human-browsable HTML and Excel reports. Module 2's existing `2_funds_report.html` stays here; future modules' human-facing reports (Module 4's filter summary + ranking report, Module 6's final integrated report) also land here.
- **`_intermediate_outputs/`** — Parquet files flowing between pipeline modules. Machine-only plumbing (Module 3's universe, Module 4's survivors, Module 5's context pack, etc.).
- **`_outputs/`** — pipeline's final machine-readable artifacts that aren't intermediate plumbing (e.g., Module 6's `llm_scores_{quarter}.parquet`, Module 7's snapshot exports).

**Follow-up:** [module_4_spec.md](module_4_spec.md) currently directs `filter_summary_{quarter}.html` and `ranking_report_{quarter}.{html,xlsx}` to `_outputs/`. Per this decision they belong in `Outputs/`. Patch Module 4 spec at Module 4 implementation time.

### Environment file handling
- **Module 1 loads `.env` automatically.** `load_config()` calls `dotenv.load_dotenv()` on the repo-root `.env` — callers do not pre-load and do not need to know the path.
- **Safe.** `python-dotenv` only reads the file into the current process's environment variables; no file mutation, no network, no persistence beyond the process.
- **`.env.example` at repo root**, committed to git, will be authored to document the required keys (`ANTHROPIC_API_KEY`, optional `OPENFIGI_API_KEY`). The real `.env` stays gitignored.

### Config directory location
- **`2_Funds_parser/config/`** (top-level, sibling of `src/`, `Input/`, `Outputs/`). Users edit YAML without navigating into the Python package tree.

### Implementation choices landed 2026-04-22

- **Validation library: frozen dataclasses + manual validation.** Error surface in [src/module_1/config.py](../src/module_1/config.py) (`ConfigError`, `_require`, `_require_type`, `_load_yaml_with_lines`). Errors include file path + field path; YAML parse errors also include line:column (via `yaml.YAMLError.problem_mark`). No new dependencies beyond PyYAML and python-dotenv. Can be swapped to Pydantic v2 later without touching callers.
- **Log file = `logs/pipeline.log`, daily midnight rotation,** `backupCount = logging.file_rotation_days` (default 14). Implementation in [src/module_1/logging_setup.py](../src/module_1/logging_setup.py) using `TimedRotatingFileHandler`. Console sink uses stdout for all levels (simpler than splitting stderr/stdout by severity; revisit if noisy).
- **`auto-latest` quarter source = `holdings.period_of_report`.** `holdings` chosen over `filings_log` because it is the richer table Module 3 reads from; both carry `period_of_report`. Implementation at [src/module_1/config.py `_resolve_auto_latest_quarter`](../src/module_1/config.py) and [src/module_1/quarter.py `resolve_quarter`](../src/module_1/quarter.py).
- **`load_config` is `@lru_cache(maxsize=None)`.** Repeated calls in one process return the same instance; no file re-reads. Cache can be cleared in tests via `load_config.cache_clear()`.
- **Bool/int disambiguation.** Python treats `bool` as a subclass of `int`, so a raw `isinstance(value, int)` would accept `true`/`false` for integer fields. `_require_type` explicitly rejects `bool` when `expected is int`. Verified by negative test.
- **DB schema validation is optional-if-DB-missing.** `load_config()` only checks table presence when `fundparser_db` exists on disk, so Module 1 can be imported on a fresh clone before Module 2's seed has run. `auto-latest` quarter resolution still fails hard if the DB is absent — that's a runtime prerequisite, not a Module-1-load prerequisite.
- **Dependencies added to shared venv:** `PyYAML==6.0.3`, `python-dotenv==1.2.2`. Installed into `.venv/` at repo root.

### Verified acceptance tests (spec §Acceptance tests)

1. ✓ Happy path — `load_config()` on committed `pipeline.yaml` returns `PipelineConfig(mode=debug, quarter=2025Q4, ...)`.
2. ✓ Missing required field — deleting `pipeline.mode` raises `ConfigError: … required field 'pipeline.mode' is missing`.
3. ✓ Malformed YAML — tab-indented line raises `ConfigError: <file>:2:1 YAML syntax error: …`.
4. ✓ Invalid quarter — `quarter: 2026Q5` raises `ConfigError: pipeline.quarter must be 'auto-latest' or YYYYQn`.
5. ✓ Auto-latest quarter resolution — with current DB, resolves to `2025Q4` (latest `period_of_report` in `holdings`).
6. Deferred — DB schema mismatch test not run in negative suite; covered by construction (`_validate_db_schema` executes on every real-DB load).
7. ✓ Debug flag propagation — `mode: debug` → DEBUG line visible in smoke-test output.
8. Trivially true — `@lru_cache` guarantees idempotent load.

---

## Module 2 — 13F Extraction (Layer 0 + Layer 1)

Specs: [2_layer_0.md](2_layer_0.md) and [2_layer_1.md](2_layer_1.md). Built prior to the Modules 1–7 restructuring; retrofit-compatible without code changes.

### Fund registry (Layer 0)
- **Third Rock Ventures dropped** (CIK `0001600135`). Last 13F-HR filing 2015-11-03 — fund stopped filing (likely below $100M §13(f) threshold or restructured). Row cleared from column A in [Input/list_of_funds.xlsx](../Input/list_of_funds.xlsx); row deleted from `funds` table on 2026-04-22. Current count: **21 funds** (down from 22).
- **Janus Henderson retargeted** from CIK `0000812295` (files only 13F-NT / notice-of-deferral since 2017) to `0001274173` (`JANUS HENDERSON GROUP PLC`, the actual 13F-HR filer). Discovered via `otherManagersInfo` on the 13F-NT cover page. Janus now contributes 18 filings from 2021-11-16 → 2026-02-17.
- **Verbatim legal-name quirks preserved** (not bugs to "fix"):
  - Versant Ventures → `HERSHEY TRUST CO` (Versant files via that vehicle, CIK `0000908551`).
  - BVF Partners → `BVF Inc.` (SEC style).
  - Athos KG display name contains `Str�ngmann` — cp1252 mangling in the xlsx; display-only. CIK `0001681662` is correct.

### 13F ingest pipeline (Layer 1)
- **Canonical date filter is `filing_date`**, never `period_of_report`. Constant `DEFAULT_FROM_DATE = "2025-01-01"` in [src/layer_1/edgar_13f.py](../src/layer_1/edgar_13f.py). Re-sweeps use `--from-date 2020-01-01`.
- **Filings dedup key:** `(fund_id, accession_number)` unique in [filings_log](../src/database/schema.sql#L42).
- **Holdings unique key:** `(fund_id, filing_date, cusip)`. One row per position per filing.
- **Market-value unit conversion.** SEC Release 34-93978 changed 13F `<value>` from thousands to raw USD effective 2023-01-03. Rows with `filing_date < 2023-01-03` are multiplied by 1000 at parse time. Constant `MARKET_VALUE_RAW_USD_CUTOFF` at [src/database/db.py:22](../src/database/db.py#L22).
- **Parallel EDGAR fetch.** `ThreadPoolExecutor(max_workers=5)` + process-global token-bucket `_RateLimiter(9.0)` at 9 req/sec (under SEC's 10 req/sec cap). SQLite writes serialized via `as_completed` in the main thread (SQLite single-writer). Don't refactor toward parallel writes without switching to `journal_mode=WAL` and testing under contention.
- **`_EDGAR_LIMITER` is module-scope, not per-thread.** `.acquire()` is called BEFORE the HTTP request (not after) so parallel workers don't stampede the limiter.

### CUSIP resolution
- **OpenFIGI gate rejects** any `securityType` / `securityType2` containing `ETF / ETP / Fund / Trust / Note / Bond / Preferred / Right / Warrant / Unit`. Only Common Stock and Depositary Receipt accepted. Ported verbatim from 1_Stock_Picker's `cusip_resolver.py`.
- **`ticker_source` column** distinguishes `'openfigi'` (authoritative, default rendering) from `'sec_name'` (fallback, orange italic with asterisk + tooltip in HTML report) from `NULL` (unresolved). Introduced via additive migration in `_apply_additive_migrations`.
- **SEC name-match fallback** (`backfill_tickers_by_sec_name`) uses cached `company_tickers.json` (7-day TTL at `Outputs/sec_company_tickers.json`). Resolved 153 additional names / 953 rows during the 2026-04-22 re-ingest. Pre-seeded rows without `name_of_issuer` backfilled by `backfill_missing_issuer_names` (re-fetches XMLs and UPDATEs by CUSIP match) — ran once for all 24,235 pre-seeded rows.
- **Janus residual unresolved CUSIPs** are ~5,000 per filing — large diversified manager with many non-equity holdings OpenFIGI rejects. Normal. SEC name-match chips away across re-runs; no backfill action needed.

### Schema evolution
- **No migration framework.** `_apply_additive_migrations` in [src/database/db.py:29-51](../src/database/db.py#L29-L51) probes `PRAGMA table_info` and issues `ALTER TABLE ADD COLUMN` for missing columns. Currently covers `holdings.name_of_issuer` and `holdings.ticker_source`.
- **Any rename or drop requires a dedicated migration script.** Not yet needed.

### Reporting
- **HTML sort:** ascending alphabetical on `name_of_issuer COLLATE NOCASE`; NULL/empty names sorted to the bottom.
- **Cache signature** includes `MAX(holdings.updated_at)` so backfills deterministically invalidate the cached HTML.
- **Current DB state (post-2026-04-22 sweep):** 21 funds, 502 filings, 77,763 holdings, date range 2020-01-28 → 2026-02-17.

---

## Module 3 — Data Bridge

*Spec: TBD (not yet written). Reads from `2_fundparser.db`, emits `_intermediate_outputs/universe_{quarter}.parquet` + audit files.*

*(No entries yet.)*

---

## Module 4 — Filter & Archetype Ranking

Spec: [module_4_spec.md](module_4_spec.md). Design rationale (D1–D18): [decisions_module_4.md](decisions_module_4.md).

*(No implementation entries yet.)*

---

## Module 5 — Market Data Enrichment

*Spec: TBD. Reads Module 4's ranked candidates, assembles per-ticker context packs for Module 6.*

*(No entries yet.)*

---

## Module 6 — LLM Scoring (Anthropic API)

*Spec: TBD. Uses batch API + prompt caching; pre-flight cost estimator gates submission.*

*(No entries yet.)*

---

## Module 7 — Outcome Tracking

*Spec: TBD. Forward-price tracking against predicted returns; advisory feedback only, no auto-tuning.*

*(No entries yet.)*

---

## Cross-cutting decisions

- **Python module naming carve-out.** Top-level dirs and scripts may start with `2_` (e.g., `2_ingest_13f.py`). Python packages and modules under `src/` cannot (Python rejects leading digits). Import paths: `from layer_1.edgar_13f import …`, `from module_1.config import load_config`.
- **Shared repo venv.** `../.venv/` (one level above `2_Funds_parser/`). `PYTHONPATH=src` required; [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) sets it.
- **`.env` at repo root,** shared with 1_Stock_Picker. Not duplicated inside `2_Funds_parser/`.
- **Quarter format `YYYYQn`** is derived from `period_of_report` (quarter-end), never `filing_date` (submission, which lags by ~45 days). This rule is cross-cutting: every module uses it.
- **Module 2 is retrofit-frozen.** Modules 1 and 3–7 wrap Module 2 via its public helpers (`get_connection`, `edgar_13f.ingest_all_funds`). Module 2's internal path resolution in [src/database/db.py:14-15](../src/database/db.py#L14-L15) is not replaced by Module 1.
