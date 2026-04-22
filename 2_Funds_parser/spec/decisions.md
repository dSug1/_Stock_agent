# 2_Funds_parser — Implementation Decisions Log

**Purpose.** Record decisions made during implementation that extend, calibrate, or deviate from the module specs in `spec/`. Spec files describe the *target system*; this log records *why* and *how* the built code differs. Ground truth is the current codebase — when decisions.md and code disagree, fix decisions.md.

**Related log.** [decisions_module_4.md](decisions_module_4.md) holds the design-rationale decisions (D1–D18) for Module 4's "train hasn't left" filter and archetype ranking. Those are pre-implementation design choices; this file is the cross-module implementation log and does not duplicate them.

**Update discipline.** At the end of every module step: did anything get decided, calibrated, renamed, or deviate from spec? If yes, append or revise the entry for that module with a link to the code line of record (`[file.py:NN](../src/path/file.py#L<n>)`). If no, state that explicitly in the end-of-step summary.

**Last updated:** 2026-04-22 (Modules 1 + 3 implemented; Module 2 schema extended for share-type fields + ticker_source on cusip_ticker_map; Boxer Capital CIK retargeted after Oct-2024 IMA handoff)

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
- **Boxer Capital retargeted 2026-04-22** from CIK `0001465837` (`Boxer Capital, LLC`) to `0002018299` (`Boxer Capital Management, LLC`). On 2024-10-10 the original Boxer Capital, LLC entered an Investment Management Agreement delegating all voting/investment power to a newly formed RIA, Boxer Capital Management, LLC (BCM). The old entity filed a single-row 13F-HR stub for 2024-12-31 (accn `0001213900-25-014355`, `cusip='000000000'`, `shares=0`) signalling it no longer beneficially owns securities; all subsequent filings are under BCM's new CIK. Retarget steps executed: (a) updated [Input/list_of_funds.xlsx](../Input/list_of_funds.xlsx) row 49 CIK + legal_name, (b) `UPDATE funds SET cik='0002018299', legal_name='Boxer Capital Management, LLC' WHERE id=20`, (c) deleted the stub from `filings_log` + its 1 holding from `holdings`, (d) re-ingested with `--from-date 2024-11-01` — pulled 5 filings (2024-12-31 through 2025-12-31 + one 2025-12-31 amendment), 187 holdings. Boxer now has full quarterly coverage through 2025Q4. Same pattern as Janus Henderson precedent; any future `funds` row whose latest filing is a 1-row stub with `cusip='000000000'` should be investigated for an entity handoff.
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
- **No migration framework.** `_apply_additive_migrations` in [src/database/db.py](../src/database/db.py) probes `PRAGMA table_info` and issues `ALTER TABLE ADD COLUMN` for missing columns. Currently covers `holdings.name_of_issuer`, `holdings.ticker_source`, `holdings.title_of_class`, `holdings.put_call`.
- **Any rename or drop requires a dedicated migration script.** Not yet needed.

### Schema extension 2026-04-22 — `title_of_class` + `put_call` (Module 3 prerequisite)
- **New columns** `holdings.title_of_class TEXT` and `holdings.put_call TEXT` added via additive migration. Captures 13F `<titleOfClass>` (e.g., `"COM"`, `"COM CL A"`, `"PRE-FUND WT"`, `"PFD"`) and `<putCall>` (`"Put"` / `"Call"` / NULL). Required by Module 3 to classify common vs. preferred vs. warrants vs. options.
- **Parser** [`parse_13f_xml`](../src/layer_1/edgar_13f.py) extended to extract both fields; existing `sshPrnamtType == "SH"` filter preserved.
- **INSERT** in `ingest_all_funds` updated to persist both columns for new ingests.
- **One-time backfill** `backfill_share_type_fields` [`src/layer_1/edgar_13f.py`](../src/layer_1/edgar_13f.py) re-fetches each filing's info-table XML once and UPDATEs existing rows by `(fund_id, filing_date, cusip)`. Wired into `ingest_all_funds` alongside the existing name-of-issuer and sec-name-ticker backfills; idempotent guard is `WHERE h.title_of_class IS NULL`.
- **Result of 2026-04-22 backfill:** 502 filings rescanned in ~60 s, 77,763/77,763 rows populated, 0 errors. Option positions surfaced: 303 Call + 241 Put rows (544 total) previously indistinguishable from common stock.
- **Observed top classifications** (for tuning `share_types.yaml` in Module 3): `COM` 51,747; `COM NEW` 3,742; `CL A` 3,246; `COM CL A` 2,749; `SHS` 1,835; `SPONSORED ADS` 997; `Common` 882; `COMMON STOCK` 829; `SPONSORED ADR` 727; `SH` 672; `COMM` 583; `CL A COM` 546; `CLASS A COM` 523; `UNIT 99/99/9999` 373 (SPAC units, expected `unknown`); `ORD SHS` 368.
- **Notes for Module 3 YAML:** rules must be **case-insensitive** (title_of_class appears as both `"COM"` and `"Common"`). The bare `"CL A"` and `"SH"` rows also classify as common. Regular `"UNIT"` (SPAC) positions will fall through to `unknown` — expected; user can promote into YAML if biotech-relevant.

### Reporting
- **HTML sort:** ascending alphabetical on `name_of_issuer COLLATE NOCASE`; NULL/empty names sorted to the bottom.
- **Cache signature** includes `MAX(holdings.updated_at)` so backfills deterministically invalidate the cached HTML.
- **Current DB state (post-2026-04-22 sweep incl. Boxer retarget):** 21 funds, 506 filings, 77,949 holdings, date range 2020-01-28 → 2026-02-17.

---

## Module 3 — Data Bridge

Spec: [module_3_spec.md](module_3_spec.md). Reads from `2_fundparser.db`, emits `_intermediate_outputs/universe_{quarter}.parquet` + audit files.

### Spec calibrations locked 2026-04-22 (implementation pending)

- **`unknown` share-class rows retained, flagged, and handled gracefully downstream.** No runtime prompts. Audit surface: `unresolved_positions_{quarter}.parquet` + `has_unknown_class=True` column on the universe aggregate. Downstream modules must treat this flag as non-fatal.
- **Pre-funded warrant detection: v1 substring rules accepted** (`config/share_types.yaml` as specced). Tune from audit output if v2 needs per-ticker overrides.
- **Ticker-null rows: bucketed on CUSIP** (not dropped). Companion manual-fill-in scripts live in `2_Funds_parser/scripts/`:
  - `list_unresolved_cusips.py` dumps `(cusip, name_of_issuer)` pairs for external AI-tool lookup.
  - `apply_manual_ticker_mappings.py` ingests a pasted/CSV mapping and UPSERTs `cusip_ticker_map` with `ticker_source='manual'`.
  - Third `ticker_source` value `'manual'` joins `'openfigi'` + `'sec_name'`. Default rendering: treated as verified (user vouched). HTML report treatment confirmed at Module 2 addendum implementation time.
- **CLI chained in `run_2_Funds_parser.bat`:** Module 2 ingest + HTML report → printed summary (rows ingested, new filings, unresolved CUSIP count) → interactive `Proceed to Module 3? [y/N]` prompt → Module 3 with `--quarter auto-latest`. User may override with explicit `--quarter YYYYQn`.
- **No sidecar cache in v1.** Overwrite-on-run; revisit only if aggregation exceeds ~5 s.
- **Future requirement flagged — historical-quarter re-runs.** Module 7's feedback loop will require re-running Module 3 against past `period_of_report` values to regenerate back-test universes. The `--quarter YYYYQn` override already covers mechanics; carried forward to Module 7 spec.

### Prerequisite: Module 2 schema extension — COMPLETE 2026-04-22

Option A executed: `title_of_class` + `put_call` columns added, parser extended, 502 filings rescanned, 77,763 rows backfilled. Details under Module 2 below.

### Spec refinement 2026-04-22 (post-backfill)

After the backfill revealed actual `title_of_class` distribution, the spec was refined:

- **Classification rules tuned** from observed data: added `"CL "`, `"CLASS "`, `"SH"` rules (for 3,246 `CL A` + 672 bare `SH` rows that v1 would have classified as `unknown`); added `"UNIT"`, `"RIGHT"`, `"PFW"`, `"CONV"` rules for edge cases surfaced by the distribution. Rule ordering re-validated: PFD before CL, COM before CL, specific before broad.
- **`exited_positions` added to universe schema.** Previously implied by `qoq_fund_count_change` but not explicit; Module 4 ranking may penalise heavy exits.
- **Downstream contract section formalized.** Modules 4/5/6/7 must handle `ticker IS NULL`, `has_unknown_class`, `has_regular_warrants`, `has_options`, and null `qoq_*` columns non-fatally. Spec section re-stated in each downstream module spec when written.
- **Manual-ticker fill-in workflow fully specified.** `list_unresolved_cusips.py` writes `_intermediate_outputs/unresolved_cusips_{quarter}.tsv`; `apply_manual_ticker_mappings.py` UPSERTs `cusip_ticker_map` AND updates `holdings.ticker` in place. `ticker_source='manual'` treated as verified (renders identically to `openfigi` in HTML report).
- **`ticker_is_verified` definition narrowed** to `ticker_source IN ('openfigi', 'manual')`. `'sec_name'` no longer counts as verified because name-match can return the wrong security class for the same issuer (flagged in Module 2 decisions).
- **CLI chaining concrete.** `run_2_Funds_parser.bat` prompts `Proceed to Module 3? [y/N]` after the existing Module 2 ingest + report; if yes, runs `3_build_universe.py` then optionally `list_unresolved_cusips.py`. `apply_manual_ticker_mappings.py` is explicitly NOT chained — user runs manually after populating the mapping file.
- **Implementation order documented** in spec for the next session.

### Implemented 2026-04-22

Code landed under [src/module_3/bridge.py](../src/module_3/bridge.py) (single-module orchestrator, 6 public functions). CLI at [scripts/3_build_universe.py](../scripts/3_build_universe.py). Companion scripts at [scripts/list_unresolved_cusips.py](../scripts/list_unresolved_cusips.py) and [scripts/apply_manual_ticker_mappings.py](../scripts/apply_manual_ticker_mappings.py). Chained into [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) behind a `[y/N]` prompt.

Implementation decisions on top of the spec:

- **`aggregate_fund_positions` returns a 4-tuple** `(fund_positions, dropped, unresolved, flags_by_key)` rather than the 3-tuple specced. Reason: a fund can hold an options position on ticker X without holding X common. The per-fund `(fund_id, position_key)` flag row is then orphaned at the cross-fund rollup (no kept row to merge into), so `has_options` silently drops to `False` at the universe level. Fix: carry a separate `flags_by_key` frame (cross-fund `any()` keyed only on `position_key`) and merge it into the universe aggregation. Verified: MRNA now shows `has_options=True` when any fund holds a MRNA put even if that fund holds no MRNA common. See [src/module_3/bridge.py:219-325](../src/module_3/bridge.py#L219-L325) and [src/module_3/bridge.py:357-447](../src/module_3/bridge.py#L357-L447). Tickers that are held ONLY as options (no kept common anywhere) still do not enter the universe — that's by design (universe = common-equity exposure).
- **`cusip_ticker_map.ticker_source` column added** via additive migration in [src/database/db.py:_apply_additive_migrations](../src/database/db.py#L29). Legacy rows backfilled to `'openfigi'` in-migration. OpenFIGI writer ([src/layer_1/cusip_resolver.py:82-96](../src/layer_1/cusip_resolver.py#L82-L96)) now stamps `'openfigi'` explicitly; manual workflow stamps `'manual'`. Spec's `ticker_source='manual'` requirement is now actually honoured on the map (was previously only on `holdings`).
- **`share_types.yaml` is loaded from `PROJECT_ROOT / "config" / "share_types.yaml"`**, imported from `module_1` ([src/module_1/paths.py:11](../src/module_1/paths.py#L11)). Earlier draft used `config.paths.project_root` which does not exist on `PathsConfig`; caught before first real run.
- **Deterministic output contract preserved.** Universe sorted by `(fund_count DESC, total_market_value DESC, ticker ASC, cusip ASC)` with stable sort; `position_key` dropped before Parquet write. Same DB snapshot + same YAML → byte-identical output.
- **No `--force` flag yet.** Spec mentions `--force` in the CLI; current implementation always overwrites. Add only if overwrites cause real user friction.

### Verified acceptance tests (spec §13 tests)

Run against live DB (2025Q4; 3,470 raw rows, 502 filings):

1. ✓ Happy path — universe parquet has 2,484 unique tickers, schema matches `_UNIVERSE_COLUMNS` (18 cols).
2. ✓ Amendment dedup — only latest `filing_date` per `(fund_id, period_of_report)` makes it into the aggregation (enforced in SQL via `MAX(filing_date)` CTE).
3. ✓ Classification rules applied — 28 rows dropped (11 regular_warrant, 8 put, 5 call, 2 debt, 2 preferred); 78 rows flagged `unknown`; rest `common` or `prefunded_warrant`.
4. ✓ Put/call precedence — fund 2's MRNA Put (`title_of_class='COM', put_call='Put'`) classified as `put` not `common`.
5. ✓ `has_options` propagates cross-fund — MRNA row shows `has_options=True` even though the Put-holding fund doesn't hold MRNA common. 7 universe rows flagged.
6. ✓ `ticker_is_verified` — 2,375 / 2,484 rows verified (`ticker_source IN ('openfigi','manual')`); `sec_name` not counted.
7. ✓ `has_unknown_class` — 47 universe rows flagged; 78 per-filing rows in `unresolved_positions_2025Q4.parquet` (some unresolved rows share a ticker).
8. ✓ Ticker-null bucketing — 1 universe row with ticker=NULL (CUSIP `92332V107`, Ventyx Biosciences, 16 funds) — position_key=`CUSIP:92332V107`, flagged `has_unknown_class=True`.
9. ✓ QoQ metrics populated — `qoq_share_change`, `new_positions`, `exited_positions`, etc. all `Int64` (nullable) and populated against 2025Q3 prior.
10. ✓ Empty quarter — passing `--quarter 2020Q1` (no holdings) writes empty parquet, does not crash.
11. ✓ `list_unresolved_cusips.py` output — one row emitted for Ventyx, `name_variants` pipe-joins 4 distinct `name_of_issuer` strings across funds.
12. ✓ `apply_manual_ticker_mappings.py` input parser — accepts TSV and CSV; skips blank tickers, comment lines, and blank lines; uppercases tickers; requires `cusip` + `ticker` headers.
13. Deferred — full round-trip of manual mapping (apply → re-run Module 3 → verify ticker resolved) not run against prod DB; mechanics verified via parser unit test. User runs end-to-end when populating real manual_map.tsv.

Smoke-test output summary: 2,484 tickers / 2,375 verified / 47 `has_unknown_class` / 7 `has_options` / 1 `has_regular_warrants` / 1 null-ticker row / 28 dropped / 78 unresolved. Wall time ~5s end-to-end.

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
