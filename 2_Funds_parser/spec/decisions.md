# 2_Funds_parser — Implementation Decisions Log

**Purpose.** Record decisions made during implementation that extend, calibrate, or deviate from the module specs in `spec/`. Spec files describe the *target system*; this log records *why* and *how* the built code differs. Ground truth is the current codebase — when decisions.md and code disagree, fix decisions.md.

**Related log.** [decisions_module_4.md](decisions_module_4.md) holds the design-rationale decisions (D1–D18) for Module 4's "train hasn't left" filter and archetype ranking. Those are pre-implementation design choices; this file is the cross-module implementation log and does not duplicate them.

**Update discipline.** At the end of every module step: did anything get decided, calibrated, renamed, or deviate from spec? If yes, append or revise the entry for that module with a link to the code line of record (`[file.py:NN](../src/path/file.py#L<n>)`). If no, state that explicitly in the end-of-step summary.

**Last updated:** 2026-04-22 (Modules 1 + 3 implemented; Module 2 schema extended for share-type fields + ticker_source on cusip_ticker_map; Boxer Capital CIK retargeted after Oct-2024 IMA handoff)

---

## Module 1 — Configuration & Foundation

Spec: [module_1_spec.md](module_1_spec.md). Implemented 2026-04-22 under [src/module_1/](../src/module_1/). Smoke test: [scripts/verify_module_1.py](../scripts/verify_module_1.py).

### Output folders — three distinct buckets (pipeline-wide rule)

**The user-facing test is the deciding factor.** If the user wants to open the file themselves (browse a report, inspect a chart, share with a colleague), it goes to `Outputs/`. If only the pipeline reads it, it goes to `_outputs/` or `_intermediate_outputs/`.

- **`Outputs/`** — files the user wants to open themselves. HTML reports, XLSX, dashboards. Examples: Module 2's `2_funds_report.html`, Module 4's `filter_summary_{quarter}.html` and `ranking_report_{quarter}.{html,xlsx}`, Module 6's final integrated report.
- **`_intermediate_outputs/`** — Parquet files flowing between pipeline modules. Machine-only plumbing the user does not open. Examples: Module 3's `universe_{quarter}.parquet`, Module 4a's `survivors_{quarter}.parquet`, Module 5's context pack.
- **`_outputs/`** — pipeline's final machine-readable artifacts that aren't intermediate plumbing (e.g., Module 6's `llm_scores_{quarter}.parquet`, Module 7's snapshot exports). Same rule: user does not open these directly.
- **`data/`** — durable cross-run caches not specific to one run (e.g., `data/prices.db` for Module 4's snapshot + price history). User does not open; pipeline-managed.

This rule applies to every module from now on. Spec drafts that violate it are corrected at implementation time.

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

### Spec revision 2026-04-23 (pre-implementation)

The original Module 4 spec referenced two non-existent input files (`enriched_snapshot.parquet` "from Module 3", `fund_positions_aggregated.parquet` "from Module 2"). Module 3 emits a single `universe_{quarter}.parquet` with 18 columns and zero market-data fields; Module 2 writes only to SQLite. The spec was rewritten cover-to-cover to fix this and adjacent issues. Locked decisions for the build:

- **Module 4a now does its own snapshot enrichment** (option (a) of the three considered). Per-ticker `market_cap`, `sector`, `industry`, `exchange`, `adv_30d`, `last_close` are fetched from Yahoo Finance into a durable `ticker_snapshot` table in `data/prices.db`. Snapshot rows have a 7-day TTL; filter-threshold changes never trigger a refetch. This satisfies the user's requirement that raising `fund_count_min` from 1 → 2 (or any other threshold tweak) reads from cache only, no network.
- **Filter priority reordered: cheap fund-side first, expensive snapshot-side second.** Fund-side filters (already in the universe parquet) slash the universe before any yfinance call. Per D6's rationale ("survivor count typically 10-20% of starting universe"), this minimises external calls.
- **New filter `exclude_lonely_seller`**: drops rows where `fund_count == 1 AND decreased_positions >= 1` ("the only holder is selling"). User explicitly requested this. Default `true`.
- **`exclude_pink_sheet` dropped** from the spec — the universe carries no exchange/OTC field, and adding a separate fetch path just for this gate isn't worth it. Pink-sheet exposure is implicitly handled by `market_cap_min_usd` and `adv_30d_min_usd`.
- **`require_min_history_weeks` moved to 4b** — the value is unknowable until price history is fetched, so 4a (which is supposed to be cheap-snapshot-only) can't enforce it. Default 12 weeks.
- **Output paths corrected** to obey the pipeline-wide rule (`Outputs/` for user-consultable; `_intermediate_outputs/` for pipeline plumbing). HTML and XLSX reports go to `Outputs/`; Parquet artifacts go to `_intermediate_outputs/`.
- **`prices.db` lives at `2_Funds_parser/data/prices.db`** (new top-level dir, sibling of `Input/`). Already covered by the repo `*.db` gitignore. Separate file from `0_Renderer/_outputs/cache/prices.db` — no shared data between pipelines.
- **File/CLI naming aligned with the established pattern.** Code under `src/module_4/` (`hard_filters.py`, `prices.py`, `ratios.py`, `archetypes.py`, `ranking.py`); CLIs `scripts/4_run_hard_filters.py` and `scripts/4_rank.py`. Earlier spec used flat `module_4a_hard_filters.py` names that didn't match Modules 1 and 3.
- **`auto_adjust=False, actions=False` for the bars fetch** so both `Close` and `adjusted_close` are stored separately. Spec D8 (always use adjusted close for ratio math) is satisfied; raw close is kept for reference. 0_Renderer's `auto_adjust=True` pattern is intentionally not reused for this reason.
- **Quarter column propagated** through every Module 4 output (survivors, rejections, ranked_candidates) for audit consistency with Module 3.
- **Calibration tooling deferred to v2.** `calibration.yaml`, `module_4_tools/calibrate.py` etc. are documented in the spec's "Open items deferred to v2" section but are not part of MVP acceptance.
- **YAML numeric literals use plain integers** (`50000000`) rather than underscores (`50_000_000`) — unambiguous across YAML loaders.
- **Code reuse from `0_Renderer/2_stock_visualizer.py`**: SQLite WAL setup, `_db()` context manager, batched `yf.download(tickers=" ".join(...), group_by="ticker", threads=True)` with per-ticker fallback, `_age_seconds` TTL helpers, SWR pattern. Copied (not imported) into `src/module_4/prices.py` so the two pipelines remain decoupled. Adaptations: ISO-date keys instead of unix-second `t`, `(ticker, date)` PK instead of `(ticker, interval, t)`, `auto_adjust=False`, no internet-clock calibration (4b is daily, not intraday).
- **Yahoo Finance throughput plan locked.** Full detail in [module_4_spec.md § Yahoo Finance throughput](module_4_spec.md). Highlights: shared `curl_cffi.requests.Session(impersonate="chrome")` (yfinance 1.3.0 + curl_cffi 0.15.0 already in venv) is the single biggest speedup vs default `requests`; bars batched at 100–200 tickers per `yf.download(threads=True)`; `.info` parallelised with `ThreadPoolExecutor(max_workers=8)` under a process-global token-bucket at 5 req/s (ported from the proven `_EDGAR_LIMITER` pattern in [src/layer_1/edgar_13f.py](../src/layer_1/edgar_13f.py)); exponential-backoff retry 1s→2s→4s, max 2 retries; SQLite cache is the long-term dominant optimisation. Deliberately NOT done: `requests_cache`, async, `yf.Tickers(plural)`, proxy rotation. All knobs (rate, workers, batch sizes, TTL) exposed in `config/filters.yaml` + `config/ranking.yaml`.

### Implementation pass 1 — 2026-04-23 (revised after first 4a runs)

Code landed under [src/module_4/](../src/module_4/) (six files). CLIs `scripts/4_run_hard_filters.py` and `scripts/4_rank.py` chained into [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) behind two `[y/N]` prompts.

The first end-to-end run on the 2025Q4 universe (2,486 → 2,026 phase-1 survivors → ~7 min cold start) exposed two Yahoo-side issues that drove a rewrite of the snapshot fetcher. Recording the diagnosis and the resulting design for future-me:

#### Issue 1 — Yahoo "Invalid Crumb" cascade (resolved)
- **Symptom:** First 50 `.info` calls returned `status=ok`. Every call from #100 onward came back `status=partial` (bars worked, `.info` returned `{}`). Final result: 1,890 / 2,026 rejections were `market_cap_missing`.
- **Cause:** Yahoo's `.info` endpoint is gated behind a per-session anti-CSRF "crumb" token. yfinance fetches the crumb once per `requests.Session` and reuses it. Yahoo invalidates the crumb after a few hundred calls; once invalidated, every subsequent `.info` call on that session returns 401 / `Invalid Crumb`.
- **Fix:** `_retry` detects 401/`invalid crumb` substrings and calls `reset_yf_session()` before the next attempt. Each `fetch_info` / `fetch_fast_info` calls `get_yf_session()` fresh per attempt so the reset takes effect.

#### Issue 2 — Yahoo IP-level throttle (`YFRateLimitError`)
- **Symptom:** After the first broken run + a 100-ticker test (which worked at 97% ok) + the start of a second full run, every `fast_info` attribute on every ticker — including AAPL we'd never queried — raised `YFRateLimitError: Too Many Requests`. Bars endpoint started failing intermittently too.
- **Cause:** Cumulative request volume across the broken first run + the test + the start of the second run exceeded Yahoo's per-IP soft cap. The 5 req/s sustained rate compounded over ~5,000 cumulative requests is too high. IP throttles persist for ~30-60 minutes regardless of backoff.
- **Fix (architectural — not just a rate tweak):**
  1. **Process-global throttle flag.** First `YFRateLimitError` flips `_THROTTLED_AT`. All subsequent `_retry` calls in the run raise `YahooThrottled` immediately without an HTTP attempt. Custom exception bubbles up to `fetch_snapshots_parallel`, which marks the deferred ticker as `partial` (no `fetch_error`) so the next-run staleness check picks it up.
  2. **`.info` is now opt-in.** `snapshot.fetch_descriptive_info` defaults to `false`. With the default, each ticker needs only 1 fast_info call (~halved HTTP volume vs the earlier path that did fast_info + .info). Tickers without sector/industry get `partial` status by design — but they still pass cap/ADV filters because `market_cap` is populated. Set the flag to `true` only when `sector_allowlist`/`blocklist` are actually used.
  3. **Default rate dropped from 5 → 2 req/s.** Empirically, 5 req/s sustained over a full universe trips the IP cap. 2 req/s gives ~17 min cold-start wall time but stays under threshold.

#### Issue 3 — SEC `/` vs Yahoo `-` ticker separators
- **Symptom:** 4 tickers (`MOG/A`, `NUVB/WS`, `PBR/A`, `UHAL/B`) failed with `'Response' object has no attribute 'get'` — yfinance choked on the slash.
- **Fix:** `_yahoo_symbol(ticker)` translates `/` → `-` for Yahoo lookups; snapshot rows are still keyed under the original SEC ticker so the join with the universe parquet works.

#### The market-cap freshness redesign — the key insight
The user asked: "market_cap changes daily. Do I need to refetch every day?" The answer drove the snapshot fetcher's final design.

`market_cap = shares_outstanding × last_close`. Decomposed:

| Field | Changes | Source | Cache TTL |
|---|---|---|---|
| `last_close`, `adv_30d` | Daily | Bars batch (chart endpoint, reliable) | Recompute every run |
| `shares_out` | ~Quarterly | `fast_info` (rate-limit-prone) | 30 days |
| `sector`, `industry`, names | ~Yearly | `.info` (rate-limit-prone) | 30 days |
| `market_cap` | Daily, **derived** | `shares_out × last_close` | Computed every run |

Implementation: `fetch_or_reuse_snapshots` checks two TTLs. If a ticker's `static_ttl_days` (default 30) hasn't elapsed and `shares_out` is present, it skips the yfinance HTTP entirely and recomputes `market_cap = cached.shares_out × bars_last_close`. Bars are fetched in a batch on every run (cheap, reliable, ~1-2 min for 2,000 tickers).

**Cost shape:**
- Day 1 cold start: ~2,000 fast_info calls + bars batches → ~17 min wall.
- Day 2 daily run: 0 fast_info calls, just bars batches → ~1-2 min wall, no throttle risk.
- Day 30+: small subset (those whose static_ttl expires) refetch → ~5-10 min.

This means daily runs after the first cold-start are cheap and don't risk Yahoo throttle. The user's daily 18:00 scheduled run becomes a 1-2 minute operation, not a 17-minute one.

#### Files changed
- [src/module_4/prices.py](../src/module_4/prices.py): `YahooThrottled` + `mark_throttled` + `is_throttled`; `_retry` detects throttle and crumb errors; `_yahoo_symbol`; `fetch_fast_info`; `_compute_price_derived` (free price-derivation from bars); `fetch_snapshot_one` rewritten with `cached_static` + `static_is_fresh` + `fetch_descriptive_info` parameters; `fetch_snapshots_parallel` plumbs cached static rows + freshness map.
- [src/module_4/hard_filters.py](../src/module_4/hard_filters.py): `fetch_or_reuse_snapshots` rewritten — two-tier freshness (static_ttl + always-fresh price-derived), passes `cached_static` and `fresh_static` set into `fetch_snapshots_parallel`.
- [config/filters.yaml](../config/filters.yaml): `ttl_days` → `static_ttl_days` (30); new `fetch_descriptive_info: false`; default `info_rate_per_s: 2.0`; `info_max_workers: 4`.
- [config/ranking.yaml](../config/ranking.yaml): `fetch_rate_per_s: 2.0`.

*(End-to-end smoke test pending Yahoo cooldown; first run's 567 cached `ok` snapshots + this run's `partial` rows preserved for next attempt.)*

### Implementation pass 2 — 2026-04-23 (post-cooldown, two-cap design)

Successful end-to-end run after Yahoo unblock: 2,486 universe → 2,026 phase-1 → 1,992 ok snapshots (98.3%) → 996 survivors at the original `$3.7B` cap. Wall time ~10 min, throttled=0. Architecture validated.

User then requested a runtime-driven cap selection. Implemented:

- **Two cap layers, not one.** `filters.yaml` now exposes `market_cap_fetch_ceiling_usd` (default $10B — outer boundary; what gets snapshot-cached) and `market_cap_max_usd_default` (default $3.7B — what the prompt suggests). The previous single `market_cap_max_usd` field is removed.
- **Phase 2 uses the ceiling, not the user cap.** Candidates between `default` and `ceiling` enter the candidate pool, get their market_cap cached, but are recorded with `rejection_reason='user_market_cap_max'` if the user picks a tighter cap. This means re-running at any cap ≤ ceiling costs zero Yahoo calls.
- **Interactive histogram-style prompt.** At the end of Phase 2, `prompt_user_for_cap()` prints a 9-bucket market-cap histogram of the candidate pool and asks for a max in $M (or `all` for the ceiling, or Enter for default). ASCII-only output (Windows cp1252 console).
- **Non-interactive paths.** `--market-cap-max-usd <USD>` CLI flag skips the prompt; `--no-prompt` uses the YAML default. Non-TTY runs (piped output, scheduled .bat without console) auto-fall-back to the default.
- **Per-`feedback_avoid_multiplying_user_requests`:** this is the explicitly-carved-out "batched triage" exception — single bounded calibration prompt, not runtime per-row prompting.
- **Smoke-test result with new design:** Phase 2 with $10B ceiling: 1,443 candidates. Default cap $3.7B → 997 survivors / 446 in `$3.7B-$10B` band as `user_market_cap_max` rejections / 520 `market_cap_fetch_ceiling_usd` rejections. Re-running at $5B would yield ~1,150 survivors instantly with no yfinance calls.
- **Files changed:** [config/filters.yaml](../config/filters.yaml) (schema split), [src/module_4/hard_filters.py](../src/module_4/hard_filters.py) (`_check_snapshot` uses ceiling, new `prompt_user_for_cap` + `apply_user_market_cap` helpers, `run_hard_filters` accepts `user_market_cap_max_usd` + `interactive` params, HTML summary shows fetch ceiling + user cap), [scripts/4_run_hard_filters.py](../scripts/4_run_hard_filters.py) (`--market-cap-max-usd` and `--no-prompt` flags).

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
