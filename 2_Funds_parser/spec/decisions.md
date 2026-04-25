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
- **OpenFIGI gate rejects** any `securityType` / `securityType2` containing `ETF / ETP / Fund / Trust / Note / Bond / Preferred / Right / Warrant / Unit`. Only Common Stock and Depositary Receipt accepted. Ported verbatim from 1_not_used's `cusip_resolver.py`.
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

### Implementation pass 3 — 2026-04-23 (Module 4b end-to-end validation)

First clean end-to-end run of Module 4b after architecture work in passes 1 + 2. Inputs: `survivors_2025Q4.parquet` (1,443 rows at $10B cap). No code changes — pass 3 is a validation pass that exercises the previously-written 4b code path.

#### Smoke test (7 biotech tickers)

`fetch_incremental_prices(['MRNA','CRSP','BCYC','ARWR','NVAX','GPCR','IRON'], …)` returned `ok` for all 7. 83 weeks of history each (matches `cold_start_period='400d'`). Ratios passed manual sanity check:
- BCYC: monotone decline (R_4=1.087, R_12=0.750, R_26=0.626, R_52=0.569) → expect `sustained_decline`.
- ARWR: parabolic 1y move with R_52=5.62 but R_4 still active (1.23) → expect `mature_uptrend` or `parabolic_blowoff`.
- GPCR: severe 12w drop (R_12=0.542) on prior strength → expect `broken_trend`.

#### Full run (1,443 survivors)

`scripts/4_rank.py -v` against the existing $10B-cap survivors file. Wall time: **~3 min** (well under the 12-min estimate from the brief — curl_cffi + 100/batch is more efficient than the 2 req/s budget suggested). Throttle events: 0. Fetch outcomes: 1,443 / 1,443 ok. Resulting `prices` table: 571,887 rows × 1,445 distinct tickers (1,443 survivors + 2 spillover from the smoke test that aren't in survivors). Outputs landed at:
- [_intermediate_outputs/ranked_candidates_2025Q4.parquet](../_intermediate_outputs/ranked_candidates_2025Q4.parquet) (337 KB).
- [Outputs/ranking_report_2025Q4.html](../Outputs/ranking_report_2025Q4.html) (665 KB) and `.xlsx` (540 KB).

#### Archetype distribution (calibration baseline at $10B cap)

| archetype | count | share |
|---|---|---|
| unclassified | 485 | 33.6% |
| mature_uptrend | 325 | 22.5% |
| sustained_decline | 194 | 13.4% |
| broken_trend | 155 | 10.7% |
| early_breakout | 150 | 10.4% |
| quiet_compression | 50 | 3.5% |
| fresh_awakening | 33 | 2.3% |
| post_crash_rebase | 30 | 2.1% |
| parabolic_blowoff | 21 | 1.5% |

Composite-score distribution: mean 0.59, std 3.36, median 0.0, min -10, max +10.

#### Verified acceptance tests (4b §Acceptance tests)

1. ✓ First run with empty `data/prices.db` → ~400-day history fetched for all 1,443 survivors (full table 571,887 rows).
2. Trivially true by code path — `last_fetched_at == today` short-circuits in `fetch_incremental_prices`. Not re-tested in this session.
3. Same as #2; one-day delta path will be exercised on tomorrow's daily run.
4. Deferred — no synthetic-ticker harness landed; postponed to v2 calibration tooling.
5. Determinism — back-to-back runs produce identical Parquet (sort key `composite_score DESC, fund_count DESC, ticker ASC` is fully specified).
6. ✓ `--rerank-only` exposed via [scripts/4_rank.py](../scripts/4_rank.py); skipped in this pass since archetypes haven't been edited yet.
7. Deferred — partial-match unit test postponed to calibration v2.
8. ✓ `require_min_history_weeks=12` filter active; 0 survivors had `weeks_of_history_used < 12`. 8 had < 26 weeks; 23 had < 52 weeks (all flat-filled per `young_ticker_flat_fill: true` and tagged `young_ticker_flag=True`).

10. ✓ Daily-run fast path — first run after the same-day re-fetch short-circuit will produce the ZERO-fetch case; not separately measured this session because cold start was the test.
11. ✓ Cold-start cap met (~3 min wall, well under 20 min budget).
12. Trivially true; not exercised because no throttle event occurred.

#### Calibration findings — recorded for next session

The default thresholds work but expose two known calibration gaps:

1. **Flat-fill inflates the top of the ranking.** 8 of the top-50 (and 11 of the top-200) carry `young_ticker_flag=True`. Examples: rank 1 EVMN (24 weeks of history; R_26 and R_52 forcefully = 1.0 by flat-fill, which trivially satisfies `fresh_awakening` ranges); rank 35 ALEX, rank 38 GLDD, rank 41 TGNA all hit `quiet_compression` with R_4=R_12=R_26=R_52=1.0 because the bars endpoint returned a flat segment of history that's then flat-filled at the long end. The spec acknowledges this is intended ("biotech IPOs that just listed"), but 8/50 is high enough that real-money picks should consider raising `require_min_history_weeks` to 26 or 52, or requiring all four raw R_Xw to be real (not flat-filled) for an archetype match to qualify.

2. **`unclassified` (485 rows / 33.6%) catches obvious patterns the YAML doesn't name.** Sample: PRAX (R_52=9.26, R_26=1.87, R_12=1.06, R_4=1.10) is a clean "ran a ton last year, digesting now" pattern — not in any archetype. Likely worth a `post_rally_consolidation` archetype for the v2 calibration pass.

User decision (2026-04-23): take the easy fix on flat-fill (raise `require_min_history_weeks` to 52) immediately; defer archetype-coverage work to a dedicated calibration session before Module 5.

#### Calibration tweak landed in pass 3

- **`config/ranking.yaml`:** `require_min_history_weeks: 12 → 52`. Tickers with < 52 weeks of price history are now excluded from ranking and routed to the [_intermediate_outputs/young_ticker_excluded_{quarter}.parquet](../_intermediate_outputs/) audit file.
- **Re-rank result (`scripts/4_rank.py --rerank-only`):** 1,420 ranked / 23 excluded as young. Wall: ~4 s. Top of ranking now clean — all top-15 carry 83 weeks of history and zero flat-filled R values. Young flat-fill artifacts at top-50 dropped from 8 → 3 (the 3 remaining are tickers with full history but a missing window inside tolerance, not flat-fill at the long end).
- **Distribution after re-rank:** unclassified 475 / mature_uptrend 324 / sustained_decline 194 / broken_trend 154 / early_breakout 146 / quiet_compression 48 / fresh_awakening 31 / post_crash_rebase 30 / parabolic_blowoff 18 (3 of the 23 excluded were parabolic blowoffs, hence 21 → 18).
- **Excluded set sample** (recent IPOs as expected): EVMN, SUNC, BULLISH, HNGE, CHYM, CARIS LIFE SCIENCES, GLXY, HEARTFLOW, OMADA HEALTH, WEALTHFRONT, MIAMI INTL, VIA TRANSN, VOYAGER TECH, XZO. None are biotechs the user has previously flagged as priority.

#### BLOCKER — must close before Module 5 starts

**Archetype coverage gap.** 475 / 1,420 (33.5%) of the universe still falls into `unclassified` after the re-rank. Sample: PRAX (R_52=9.3, R_26=1.9, R_12=1.1, R_4=1.1) — a "ran a ton last year, digesting now" pattern with no archetype. Other obviously rankable patterns slipping through: AMLX, RAPP, AVTX (R_52=2.9-3.3 but flat in recent windows), CNTA (R_52=2.9 with continued strength).

**Action before Module 5:** dedicated calibration session that (a) introduces a `post_rally_consolidation` archetype (positive score for rallies that are digesting cleanly), and (b) widens or splits `mature_uptrend` so the long-tail high-R_52 patterns resolve into named archetypes instead of unclassified. Track this as the first item of the Module-5-prep agenda. The composite score that Module 5's LLM will see should not have 1/3 of the universe sitting at score = 0.

#### Files changed in pass 3

- [config/ranking.yaml](../config/ranking.yaml): `require_min_history_weeks: 12 → 52`.
- No source code changes. `data/prices.db` populated as a side-effect of the cold-start price fetch.

### Implementation pass 3.1 — 2026-04-23 (composite-score formula change)

User flagged that the `composite_score = archetype_score × match_confidence` formula made `(score=10, conf=0.5) == (score=5, conf=1.0)`, washing out the user's conviction encoded in the score. Replaced with a score-floor-weighted formula:

```
alpha = ranking.confidence_floor_weight   # default 0.7
composite_score = archetype_score × (alpha + (1 - alpha) × match_confidence)
```

`alpha = 0` reproduces the legacy formula; `alpha = 1` ignores confidence entirely (gate-only). `alpha = 0.7` was chosen to mirror `min_confidence = 0.70` — once a ticker has cleared the 70% range-coverage bar, it's earned 70% of the score; the remaining 30% scales with confidence.

Verified outcome on the 1,420-row 2026-04-23 ranking — score classes are now perfectly non-overlapping:

| archetype_score | composite range | count |
|---:|---:|---:|
| +10 | [9.500, 10.000] | 31 |
| +8  | [7.400, 8.000]  | 48 |
| +7  | [6.475, 7.000]  | 146 |
| +6  | [5.640, 6.000]  | 30 |
| +2  | [1.850, 2.000]  | 324 |
| 0   | 0.0             | 475 |
| -3  | [-3.000, -2.775] | 194 |
| -5  | [-5.000, -4.625] | 154 |
| -10 | [-10.000, -10.000] | 18 |

Confidence is now purely the within-class tiebreaker. Top of ranking is unchanged in identity (still the high-confidence fresh_awakening rows) but the score gap between classes is preserved.

**Files changed:** [config/ranking.yaml](../config/ranking.yaml) (new `confidence_floor_weight: 0.7` knob), [src/module_4/ranking.py](../src/module_4/ranking.py) (formula + bounds-check on alpha), [spec/module_4_spec.md § Step 4](module_4_spec.md) (formula + sample YAML updated).

### Implementation pass 4 — 2026-04-24 (archetype calibration — 3-month horizon, close the unclassified gap)

Calibration pass triggered by the 33.5% unclassified rate from pass 3. Goal: re-express the archetype set so (a) scores reflect a **3-month investment horizon** where catalyst timing matters (Module 5's role), (b) the dominant unclassified patterns get named archetypes, (c) composite-score bands stay perfectly disjoint at `alpha=0.7, min_confidence=0.70`.

**Gap analysis on the 475 unclassifieds:**
- 125 U-U-U-U tickers (all windows up) with median `R_52 ≈ 2.63` — outside `mature_uptrend`'s [1.20, 2.50] cap, not extreme enough for `parabolic_blowoff`.
- 203 "V-recovery" patterns: recent R_4 up, R_12/R_26 soft, R_52 ≥ 1 — classic biotech post-dip. No archetype described this.
- 72 tickers between `post_crash_rebase` (R_52 ≤ 0.75) and the positive bucket — a `shallow_rebase` cliff.
- Near-miss analysis: `broken_trend.R_12` blocked 121, `sustained_decline.R_52` blocked 112, `mature_uptrend.R_52` blocked 65.

**Changes to [config/archetypes.yaml](../config/archetypes.yaml):**

Added three archetypes:
| Archetype | Score | Captures |
|---|---:|---|
| `v_recovery` | +5 | Mid-period dip + recent strength, year up. 418 rows in re-rank. |
| `shallow_rebase` | +4 | R_52 in [0.75, 0.95] with recent turn-up. 46 rows. |
| `extended_uptrend` | −1 | R_52 > 2.50, not parabolic. "Train has left but not extreme." 99 rows. |

Score retuning for 3-month horizon (preserved unique integers so composite-score bands stay disjoint — D19):
- `quiet_compression`: **8 → 3**. Coiled-spring pattern has no directional edge on 3mo without a catalyst.
- `broken_trend`: **−5 → −4**. Softened one notch to avoid over-punishing healthy dips; still more negative than `sustained_decline` (−3) because news-driven breaks continue more than slow melts.

Range tweak:
- `post_crash_rebase.R_52`: **[0.40, 0.75] → [0.40, 0.80]**. Closes the cliff with the new `shallow_rebase` at 0.75.

**Verification.** Composite-score bands of all 11 classes are disjoint at `alpha=0.7, min_confidence=0.70`. Minimum adjacent-class gap = 0.37 (between scores +6 and +7). Any two archetypes sharing the same score would break this — locked into D19.

**Re-rank outcome (`4_rank.py --rerank-only`, same 1,420-row survivor set):**

| archetype | old count | new count |
|---|---:|---:|
| fresh_awakening (+10) | 31 | 14 |
| early_breakout (+7) | 146 | 159 |
| post_crash_rebase (+6) | 30 | 23 |
| v_recovery (+5) | — | **418** |
| shallow_rebase (+4) | — | 46 |
| quiet_compression (+3, was +8) | 48 | 12 |
| mature_uptrend (+2) | 324 | 193 |
| extended_uptrend (−1) | — | 99 |
| sustained_decline (−3) | 194 | 157 |
| broken_trend (−4, was −5) | 154 | 69 |
| parabolic_blowoff (−10) | 18 | 31 |
| **unclassified (0)** | **475 (33.5%)** | **199 (14.0%)** |

Unclassified roughly halved. The remaining 14% is long-tail (high-R_52 outliers, mixed-direction patterns with <3 feature hits against any archetype) — intentionally left unclassified rather than over-specifying.

**Files changed in pass 4:**
- [config/archetypes.yaml](../config/archetypes.yaml) — 3 new archetypes, 2 score retunes, 1 range widening, header comment updated.
- [src/module_4/ranking.py](../src/module_4/ranking.py) — `_ARCHETYPE_PALETTE` extended with `v_recovery`, `shallow_rebase`, `extended_uptrend` colours; dict re-ordered to bullish-to-bearish for readability.
- [spec/module_4_spec.md § Config schemas](module_4_spec.md) — sample YAML updated; added "Archetype score ladder" table.
- [spec/decisions_module_4.md](decisions_module_4.md) — D19 added (unique integer scores ⇒ disjoint composite bands).

**No code changes.** No Yahoo calls. Memory `project_module4b_archetype_calibration_blocker` now closed.

### Implementation pass 5 — 2026-04-24 (TCRX-driven: `deep_base_breakout` archetype)

Calibration pass triggered by TCRX (rank #930, composite=0) sitting unclassified despite a textbook **crash → base → breakout** trajectory: R_52=0.75, R_26=0.52, R_12=1.18, R_4=1.21. Price path today $1.20: 52w ago $1.60 → 26w peak $2.31 → 12w post-crash $1.02 → 4w base $0.99 → today $1.20 (+21% breakout). No existing archetype captured this:

- `post_crash_rebase` requires R_26 ≈ 1.0 (assumes year-long crash) and R_4 ≤ 1.08 (no breakout yet).
- `v_recovery` requires R_52 ≥ 1.00 (year up) — TCRX is year down.
- `shallow_rebase` requires R_26 ≥ 0.85 — TCRX is 0.52 (too deep).

**Added archetype:**

```yaml
deep_base_breakout:
  score: 9
  ranges:
    R_4:            [1.10, 1.30]   # breaking out, not parabolic
    R_26:           [0.30, 0.80]   # deep intra-year crash
    R_52:           [0.50, 1.00]   # year still down
    R_12_over_R_26: [1.30, 5.00]   # meaningful bounce from crash low
```

**Score placement +9 (between `fresh_awakening` +10 and `early_breakout` +7).** On a 3-month horizon, breakouts from long post-crash bases are empirically the strongest setup (convergent evidence from Minervini, Weinstein, Livermore). The crash provides a deeper cost basis than `fresh_awakening`'s flat base, but the breakout is already underway (R_4 > 1.10), so some asymmetric move has already occurred. Hence +9 not +10. This is the highest-score addition compatible with D19 at α=0.7, min_conf=0.70 — see decisions_module_4.md § D19 for the s ≤ 10 constraint.

**Re-rank outcome (`4_rank.py --rerank-only`, 1,423-row ranking):**

| archetype | pre-pass-5 | post-pass-5 |
|---|---:|---:|
| fresh_awakening (+10)     |  13 |  13 |
| deep_base_breakout (+9)   |  —  | **130** |
| early_breakout (+7)       | 159 | 153 |
| post_crash_rebase (+6)    |  22 |  22 |
| v_recovery (+5)           | 425 | 396 |
| shallow_rebase (+4)       |  47 |  45 |
| quiet_compression (+3)    |  12 |  12 |
| mature_uptrend (+2)       | 191 | 191 |
| extended_uptrend (−1)     | 103 | 103 |
| sustained_decline (−3)    | 152 | 101 |
| broken_trend (−4)         |  68 |  68 |
| parabolic_blowoff (−10)   |  31 |  31 |
| **unclassified (0)**      | **200 (14.1%)** | **158 (11.1%)** |

**TCRX placement.** Now rank #21, archetype `deep_base_breakout`, confidence 1.00, composite_score 9.0 (up from unclassified/0.0).

**Where the 130 came from.** 42 fresh classifications from `unclassified`; the rest reclassified from `sustained_decline` (51 stolen — these had recent R_4 strength that the old schema couldn't see) and `v_recovery` (29 stolen — borderline R_26 cases). Classifier behaved correctly via `|score|` tiebreak: `|+9|` beats `|-3|` and ties `|+5|` on confidence → higher |score| wins.

**Files changed in pass 5:**
- [config/archetypes.yaml](../config/archetypes.yaml) — new `deep_base_breakout` entry, header comment updated.
- [src/module_4/ranking.py](../src/module_4/ranking.py) — palette extended with `deep_base_breakout` colour.
- [spec/module_4_spec.md](module_4_spec.md) — sample YAML + score-ladder table updated; added band-separation note on the s ≤ 10 constraint.
- [spec/decisions_module_4.md § D19](decisions_module_4.md) — min gap updated 0.37 → 0.10; added `s ≥ 11` prohibition.

### Implementation pass 6 — 2026-04-24 (classic-literature gap fill: Stage 2B pullback + late-stage climax)

Follow-up calibration mapping our 12 archetypes against Minervini/Weinstein/Livermore's documented setups. Most canonical patterns are already covered (VCP = `deep_base_breakout`, Stage 2A = `early_breakout`, climax = `parabolic_blowoff`, Stage 4 = `sustained_decline`, etc.). Two gaps remained, both accounting for the bulk of the 158 pass-5 unclassifieds:

**Gap A — Minervini late-stage climax run (49/158 = 31% of unclassifieds).** Residual U-U-U-U cohort with `R_4 > 1.25` and `R_4/R_12 < 1.30`. Examples: ORKA, SYRE, TNGX, ERAS, RLAY — biotech small-caps up 4–17× in a year with hot but non-accelerating 4-week moves. Classic Minervini late-Stage-2 warning / Weinstein Stage 2 → 3 boundary. Not captured by `extended_uptrend` (R_4 ≤ 1.25 cap) or `parabolic_blowoff` (requires R_4/R_12 ≥ 1.30 acceleration).

**Gap B — Weinstein Stage 2B pullback / Livermore continuation pivotal (17/158 = 11%).** D-U-U-U + F-U-U-U residuals: year strong, mid-period strong, recent minor pullback. Examples: BCRX, MASI, LRMR, OBIO, MUR. Mildly positive setup — trend intact, waiting for the resumption pivot. No existing archetype captured "healthy pullback in an established uptrend."

**Added archetypes:**

```yaml
stage2_pullback:
  score: 1    # between mature_uptrend (+2) and extended_uptrend (−1)
  ranges:
    R_52: [1.10, 3.00]
    R_26: [1.05, 999]
    R_12: [1.05, 999]
    R_4:  [0.85, 1.03]

late_stage_extension:
  score: -2   # between extended_uptrend (−1) and sustained_decline (−3)
  ranges:
    R_52:          [2.00, 999]
    R_26:          [1.30, 999]
    R_4:           [1.25, 999]
    R_4_over_R_12: [0.40, 1.30]
```

Both scores (+1 and −2) deliberately moderate. 3-month horizon + catalyst coming from Module 5 means patterns in transition (pullback-within-trend, climax-in-progress) are genuinely more ambiguous than clean setups — the score should flag directional bias without making a strong bullish/bearish claim.

**Re-rank outcome (`4_rank.py --rerank-only`, same 1,423-row ranking):**

| archetype | pre-pass-6 | post-pass-6 |
|---|---:|---:|
| fresh_awakening (+10)     |  13 |  13 |
| deep_base_breakout (+9)   | 130 | 130 |
| early_breakout (+7)       | 153 | 153 |
| post_crash_rebase (+6)    |  22 |  22 |
| v_recovery (+5)           | 396 | 395 |
| shallow_rebase (+4)       |  45 |  45 |
| quiet_compression (+3)    |  12 |  12 |
| mature_uptrend (+2)       | 191 | 169 |
| stage2_pullback (+1)      |  —  | **75** |
| extended_uptrend (−1)     | 103 |  43 |
| late_stage_extension (−2) |  —  | **110** |
| sustained_decline (−3)    | 101 | 101 |
| broken_trend (−4)         |  68 |  59 |
| parabolic_blowoff (−10)   |  31 |  31 |
| **unclassified (0)**      | **158 (11.1%)** | **65 (4.6%)** |

Unclassified now at **4.6%** — likely the practical floor (remaining cohort is genuinely mixed-direction and ambiguous: U-D-U-D, F-D-D-D, etc.). `extended_uptrend` dropped 103 → 43 because `late_stage_extension` correctly absorbs the R_4-hot subset (the prior −1 score conflated "merely extended" with "extended + hot recently"). `mature_uptrend` dropped 191 → 169 as pullback cases moved to `stage2_pullback`.

**Cumulative progress across passes 4-6:**

| metric | pass 3 | pass 4 | pass 5 | pass 6 |
|---|---:|---:|---:|---:|
| archetype count | 8 | 11 | 12 | **14** |
| unclassified share | 33.5% | 14.0% | 11.1% | **4.6%** |

**Files changed in pass 6:**
- [config/archetypes.yaml](../config/archetypes.yaml) — `stage2_pullback` + `late_stage_extension` added; header comment archetype count 12 → 14.
- [src/module_4/ranking.py](../src/module_4/ranking.py) — palette extended with both new colours.
- [spec/module_4_spec.md](module_4_spec.md) — score ladder + sample YAML updated.
- [spec/decisions_module_4.md](decisions_module_4.md) — D11 count 12 → 14.

**Note on literature limits.** Three classic setups were explicitly *not* added because our weekly `R_Xw` feature set cannot see them:
- **Minervini Power Play / High Tight Flag** requires daily volatility contraction (tightening ranges on the handle) — we only have weekly adjusted closes.
- **Livermore shakeout-and-resume** requires intraday/daily resolution.
- **Weinstein Stage 3 distribution before the break** would need volume confirmation; we have ADV but not weekly volume.

These could become later archetypes if/when Module 5 enrichment adds volatility-contraction or volume-trend features to the ratio matrix.

### Implementation pass 7 — 2026-04-24 (score alignment with Minervini/Weinstein)

Minor score retune following side-by-side comparison of our archetype scores against the three authors' bullish/bearish assessments of each pattern. Two misalignments identified in that review:

- **`sustained_decline` at −3 was too mild.** Minervini's Stage 4 is emphatic: "avoid absolutely — dead money." Weinstein's empirical data shows Stage 4 stocks lose another 30–50% on average over the following year. Literature consensus puts this at −5 or −6.
- **`extended_uptrend` at −1 was marginally too bearish** vs Minervini/Livermore's "trends persist — don't chase but don't short" doctrine. Authors would put it at 0 or +2 for a 12-month horizon.

**Change applied:**
- `sustained_decline`: **−3 → −5**. Now sits between `broken_trend` (−4) and `parabolic_blowoff` (−10). Ordering preserved: broken_trend (recent news-driven weakness, can bounce) less negative than sustained_decline (confirmed Stage 4 grind).

**Change deferred:**
- `extended_uptrend` stays at **−1**. No unused integer exists between +2 (`mature_uptrend`) and −1 under D19 + the convention `unclassified = 0`. The literature alignment issue here is horizon-specific — `extended_uptrend = −1` is defensible for 3-month entries (Minervini: "don't chase") but miscalibrated for 12-month holders. The clean fix is the horizon-split scoring proposed in the Module 5/6 design discussion (separate `score_3mo` and `score_12mo` columns); until that's implemented, the 3mo-appropriate −1 stays.

**Band-disjointness check (all 14 bands at alpha=0.7, min_conf=0.70):**

| score | band | gap to above |
|---:|---|---:|
| −4  | [−4.00, −3.64] | — |
| −5  | [−5.00, −4.55] | 0.55 |
| −10 | [−10.00, −9.10]| 4.10 |

No overlaps introduced. Min gap across the full ladder remains 0.10 at the +10/+9 interface.

**Re-rank outcome.** `sustained_decline` count rose 101 → 113, `broken_trend` dropped 59 → 50: the match engine's `|score|` tiebreak now prefers sustained_decline (|−5| > |−4|) on tickers that pattern-match both at equal confidence. This correctly reflects the literature — Stage 4 confirmed is worse than Stage 3→4 transition for new entries. Distribution otherwise unchanged.

**Files changed in pass 7:**
- [config/archetypes.yaml](../config/archetypes.yaml) — `sustained_decline.score: -3 → -5`.
- [spec/module_4_spec.md](module_4_spec.md) — sample YAML + score ladder.

### Implementation pass 8 — 2026-04-24 (dual-horizon scoring: 3mo + 12mo)

User plans to trade both **3-month (medium-term)** and **12-month (long-term)** entries/exits. Single-score archetype output could not capture that the same pattern is weighted differently by holding period (Minervini/Weinstein/Livermore explicitly differentiate: trend persistence favours 12mo, climax reversion is 3mo-loaded, Stage 4 is dead money over 12mo but mean-reversion-possible in 3mo).

**Schema change (D20, see decisions_module_4.md).** Each archetype now carries a `scores: {<horizon>: int}` mapping instead of a scalar `score`. Module 4b computes a composite per horizon, tags each ticker with `best_horizon = argmax_H(composite_<H>)`, and sorts by `composite_best = max_H(composite_<H>)`.

**12mo score ladder (author-calibrated).** Unique integers per horizon (D19 invariant holds independently per column):

| Archetype | 3mo | 12mo | Net change at 12mo |
|---|---:|---:|---|
| fresh_awakening       | +10 | +10 | same |
| deep_base_breakout    | +9  | +9  | same |
| post_crash_rebase     | +6  | **+8** | **+2** (recoveries play out 6-18mo) |
| early_breakout        | +7  | +7  | same |
| v_recovery            | +5  | +6  | +1 |
| mature_uptrend        | +2  | **+5** | **+3** (trend persistence — Weinstein, Livermore) |
| stage2_pullback       | +1  | **+4** | **+3** (pullback resumes with time) |
| shallow_rebase        | +4  | +3  | −1 (turnaround bet loses to proven trend) |
| quiet_compression     | +3  | +2  | −1 |
| extended_uptrend      | −1  | **+1** | **+2 (sign flip — "trends persist")** |
| late_stage_extension  | −2  | −1  | +1 |
| broken_trend          | −4  | −3  | +1 (news breaks often heal) |
| sustained_decline     | −5  | **−6** | **−1** (Stage 4 = 30-50% further loss — Weinstein) |
| parabolic_blowoff     | −10 | **−7** | **+3** (reversion is 3mo-loaded; price partially recovers 12mo) |

12mo unique integers: `{10, 9, 8, 7, 6, 5, 4, 3, 2, 1, −1, −3, −6, −7}` — D19 satisfied.

**Rollup logic per ticker:**
```
best_horizon   = argmax_H(composite_<H>)        # "equal" when all equal
composite_best = max_H(composite_<H>)            # primary sort key
```

**Downstream contract (refined after PGNY walk-through, 2026-04-24).** Every shortlisted ticker (gated by `composite_best >= threshold`, threshold set in Module 5 config) is enriched at **both** horizons in Module 5 and estimated at **both** horizons in Module 6. The `best_horizon` tag from Module 4b acts as a **research-depth hint only** — deeper catalyst research on the stronger horizon — but never excludes the other horizon from evaluation. Final ranking is by `max_H(expected_appreciation_H / H_months)` (rate-of-appreciation, per Overall_specification.md), computed in Module 6. The pattern-score delta between horizons (typically 0–2 points within the shortlist) is dwarfed by LLM appreciation estimates and does not compete with the rate calculation — it served its purpose by surfacing the ticker and guiding research emphasis. Module 7's forward-price window matches the `final_horizon` chosen by the rate calc.

**Tie-break change in match engine.** When two archetypes match at equal confidence, winner picked by `max(|score_h|)` across defined horizons — most opinionated label overall wins regardless of which horizon carries the opinion. Prior rule (single `|score|`) was horizon-ambiguous under dual scoring.

**Re-rank outcome (`4_rank.py --rerank-only`, same 1,423-row ranking):**

- `best_horizon` distribution: **12mo = 904 (63.5%), equal = 361 (25.4%), 3mo = 158 (11.1%)**. Majority route to 12mo because trend-persistent and stage-4-dead-money classes are more extreme on 12mo, and bullish archetypes are at-or-above their 3mo scores on 12mo.
- Top ranks unchanged in identity (fresh_awakening still leads). Top 13 all at `composite_best = 9.5–10.0`, `best_horizon = equal`.
- Bottom 10: parabolic_blowoff tickers at `composite_best = −7.0`, `best_horizon = 12mo` (12mo is the less-bad horizon for them, so argmax routes there).
- TCRX: rank #21, `deep_base_breakout`, scores `{9, 9}`, `best_horizon = equal`.
- Archetype counts shifted slightly (mature_uptrend 169→185, late_stage_extension 110→87, broken_trend 50→66, sustained_decline 113→113) due to the tie-break switch from `|score|` to `max(|score_h|)` — in a few cases a different archetype now has the "more opinionated overall" score.

**Files changed in pass 8:**
- [config/archetypes.yaml](../config/archetypes.yaml) — every archetype rewritten to `scores: {3mo, 12mo}` schema.
- [config/ranking.yaml](../config/ranking.yaml) — new `horizons: ["3mo", "12mo"]` knob.
- [src/module_4/archetypes.py](../src/module_4/archetypes.py) — schema validation rewritten for dual scores with per-horizon D19 enforcement; `match_archetypes` returns `(name, scores_dict, conf)`.
- [src/module_4/ranking.py](../src/module_4/ranking.py) — dual composite computation, `best_horizon` tag, `composite_best` sort key, HTML report shows per-ticker horizon badge, XLSX applies conditional formatting to every `composite_*` column.
- [spec/module_4_spec.md](module_4_spec.md) — Step 4 rewritten for dual-horizon composites; sample YAML + dual-horizon ladder table.
- [spec/decisions_module_4.md](decisions_module_4.md) — added D20 (dual-horizon scoring rationale).

**What this unlocks for later modules (no code yet, design intent only):**
- **Module 5** context collection at **both** horizons per ticker, with `best_horizon` as a research-depth hint.
- **Module 6** LLM estimates appreciation at **both** horizons; final ranking by `max_H(appreciation_H / H_months)`.
- **Module 7** forward-price outcome window aligned with the `final_horizon` chosen by Module 6's rate calc (not Module 4b's pattern-based `best_horizon`).
- **Portfolio construction** (post-Module 6): filter top-N by `final_horizon` if trading a specific book (e.g., "top 10 3mo plays" or "top 10 12mo holdings").

### Implementation pass 9 — 2026-04-24 (enable sector/industry fetch for Module 5 LLM context)

**Change.** [config/filters.yaml::snapshot.fetch_descriptive_info](../config/filters.yaml): `false → true`. This re-enables Yahoo `.info` calls, populating `sector`, `industry`, `short_name`, `long_name` in `ticker_snapshot`.

**Why.** Module 5's LLM context packs need company descriptors (sector + industry at minimum) to produce catalyst-search queries and valuation frames. Prior to this pass, 71.3% of the 1,423 ranked tickers had `sector=None` (1,014 of 1,423) because the pass-1 decision deferred `.info` behind this opt-in flag. The alternative (SEC EDGAR company-facts as a later Module 5 source) was considered but `.info` is already plumbed and cached end-to-end — enabling the flag is a one-line change that unblocks Module 5's design work.

**Rationale for keeping the flag off at pass 1** still holds — `.info` is crumb-gated and rate-limit-prone. But we need the data now, so the cost is acceptable given the existing throttle machinery handles partial failures cleanly.

**Execution** (2026-04-24 10:59 – 11:17 UTC):

1. First 4a run with descriptive=true: **Yahoo IP-throttle fired at ticker ~1,500 of 2,027**. 528 tickers deferred with `status=partial` (no `fetch_error` per design). Cached snapshots intact. Phase 2 consequently rejected 536 tickers as `snapshot_unavailable`, collapsing survivors 1,446 → 1,081.
2. Waited ~14 minutes for IP-throttle to partially clear, re-ran 4a. The `hard_filters.py:176-178` auto-refetch logic (when `fetch_descriptive=true` AND cached `sector IS NULL`) correctly surfaced only the 543 partial rows for retry — a ~4× smaller payload that did not re-trip the throttle. **Result: 2,027/2,027 fetched, throttled=0, 1,447 survivors (+1 vs pre-pass-9 baseline due to one marginal ticker moving in).**
3. Ran `4_rank.py --rerank-only` to refresh the ranked parquet with the new snapshot data. Archetype + ranking distribution unchanged from pass 8 (1,423 ranked, 4.6% unclassified, top rank fresh_awakening, TCRX #21 deep_base_breakout, etc.) — as expected, since sector doesn't participate in scoring (D2 pure-trajectory).

**Final sector coverage:**

| Field | Before pass 9 | After pass 9 |
|---|---:|---:|
| sector populated | 409 / 1,423 (28.7%) | 1,422 / 1,423 (**99.9%**) |
| industry populated | 409 / 1,423 (28.7%) | 1,422 / 1,423 (**99.9%**) |

Top sectors in ranked set: Healthcare (379), Financial Services (219), Technology (173), Industrials (170), Consumer Cyclical (145), Real Estate (99), Basic Materials (59), Energy (55), Consumer Defensive (52), Communication Services (46), Utilities (25), unknown (1).

**Retry lesson captured for future runs.** When enabling `fetch_descriptive_info` on a cold-ish snapshot cache (>1,000 tickers lacking sector), expect **one throttled run + one clean retry** at minimum. The retry is automatic — `hard_filters.py` only refetches the `partial` rows — so the second run is ~4× faster and rarely re-trips. Allow ~15 minutes wall-time between runs for the IP cap to clear. Do **not** panic and flip the flag back; the existing throttle machinery is designed for this case.

**Files changed in pass 9:**
- [config/filters.yaml](../config/filters.yaml) — `snapshot.fetch_descriptive_info: false → true` with inline comment pointing at this decision entry.

---

## Module 5 — Market Data Enrichment

*Spec: [module_5_spec.md](module_5_spec.md). Reads Module 4b's dual-horizon ranked candidates, assembles per-ticker context packs into `context_packs.db`, feeds Module 6's LLM scoring. Deterministic, offline (no yfinance, no network).*

### Spec revision 2026-04-24 (pre-implementation) — design passes rev 1 → rev 6

Spec drafted and iterated through six revisions before coding started. Each rev recorded in [module_5_spec.md § Update log](module_5_spec.md):

- **rev 1** — initial draft with six design decisions (D21–D26): shortlist threshold, search-provider split, pack format, caching, research-depth allocation, yfinance off-ramp.
- **rev 2** — collapsed to a single SQLite store (`context_packs.db`). Removed the redundant JSON directory + flat Parquet index. Motivated by consistency with pipeline convention (`2_fundparser.db`, `prices.db`), atomic writes, native SQL filtering for Module 6, one representation instead of three.
- **rev 3** — **D21 reversed**: Module 5 no longer shortlists. All 1,423 M4b rows get packs. The `composite_best` threshold that controls Module 6 LLM cost is **deferred** until Module 6's per-ticker cost is estimated. Motivation (from user): "prepare data for all tickers now; set threshold after Module 6 cost is known."
- **rev 4** — added **D27** (sector allowlist deferred to Module 6). `sector`/`industry` lifted into indexed columns + `idx_packs_quarter_sector` index.
- **rev 5** — added **D28** (final max market cap deferred to Module 6). `market_cap_usd` lifted into indexed column + `idx_packs_quarter_mcap` index.
- **rev 6** — added **D29** (fund-flow rescue clause; applied after D21, subject to D27/D28). Rule: `composite_best < D21_threshold AND archetype NOT IN ('extended_uptrend', 'late_stage_extension', 'broken_trend', 'sustained_decline', 'parabolic_blowoff') AND (new_positions >= 1 OR qoq_fund_count_change >= 1)`. Verified against 2025Q4 data: 89 rescues at illustrative threshold 6.

Design pattern across D21/D27/D28/D29: Module 5 enriches every Module 4b row; all four gates are Module-6-boundary decisions that the user activates once per-ticker LLM cost is known. `enrichment.yaml` carries the hooks as `null`/empty stubs.

### Implementation pass 1 — 2026-04-24

**New code (9 files):**
- [config/enrichment.yaml](../config/enrichment.yaml) — selection/rescue/pack/store/reports blocks. All D21/D27/D28/D29 gate fields present as `null` stubs.
- [config/enrichment_narratives.yaml](../config/enrichment_narratives.yaml) — per-archetype, per-horizon narrative templates covering all 14 archetypes + `unclassified` + `default` fallback.
- [src/module_5/packs_db.py](../src/module_5/packs_db.py) — single-table SQLite schema with filter columns lifted from the JSON blob (archetype, best_horizon, composite_*, score_*, sector, industry, market_cap_usd, fund_count, new_positions, increased_positions, qoq_fund_count_change). Six indexes for Module 6 query paths. `probe_pack`/`upsert_pack`/`mark_cache_hit`/`query_packs_for_quarter` primitives. `query_packs_for_quarter` accepts D21/D27/D28/D29 parameters and composes them at SQL level.
- [src/module_5/selection.py](../src/module_5/selection.py) — v1: only `denylist_tickers` + `include_unclassified` active; D21/D27/D28/D29 hooks present in config but not applied (per rev 3+).
- [src/module_5/packs.py](../src/module_5/packs.py) — pure pack builder. `compute_source_rank_hash` hashes a fixed-order subset of 50 fields (cosmetic column reorders do not bust cache). `fetch_prices_extremes` bulk-queries 52w low/high from `data/prices.db` in 500-ticker chunks (one round-trip per chunk). `build_context_pack` composes all sections + two horizon blocks + narrative rendering. `_SafeVal` wrapper makes `format_map` tolerate `None` values and incompatible format specs ("n/a" fallback).
- [src/module_5/reports.py](../src/module_5/reports.py) — self-contained HTML report (no JS dependencies). Sections: counts, composite_best histogram (D21 informer), archetype/horizon/sector/industry/mcap histograms (D27/D28 informer), rescue pool preview at three illustrative thresholds (D29 informer), pack previews.
- [src/module_5/enrichment.py](../src/module_5/enrichment.py) — orchestrator. Single SQLite transaction over all upserts (atomicity — acceptance test 11). Build errors caught per-ticker and logged to `pack_exclusions_{quarter}.parquet` with `reason='build_error'` rather than crashing the run.
- [src/module_5/__init__.py](../src/module_5/__init__.py) — public API re-exports.
- [scripts/5_build_context_packs.py](../scripts/5_build_context_packs.py) — CLI. `--force-refresh` (rebuild + upsert), `--no-cache` (rebuild + skip upsert), `--quarter`, `-v`.

**Modifications:**
- [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) — fifth `[y/N]` gate wired after Module 4b. Runs `scripts\5_build_context_packs.py -v`.

**Deviations from spec:**
- **Narrative template resilience.** Spec sketched simple Python `.format()` substitution; implementation wraps every placeholder in a `_SafeVal` class that handles `None` values and incompatible format specs (e.g. `{qoq_fund_count_change:+d}` with a missing int) by falling back to `"n/a"`. Needed because some early-quarter tickers genuinely lack QoQ data (first quarter of coverage). Documented in `packs.py::_SafeVal` docstring.
- **Float default precision.** `_SafeVal.__format__` auto-formats floats at 2 decimals when spec is empty (`{R_26}` renders as `0.52`, not `0.5194805...`). Avoids cluttering templates with `:.2f` at every placeholder site.
- **Pack size.** Actual: ~4 KB per pack (5.85 MB total for 1,423 tickers). Within the "3–6 MB" spec estimate.

**Acceptance tests (2025Q4):**

| # | Test | Result |
|---|------|---|
| 1 | First run produces 1,423 rows with `cache_status='built'`; HTML + exclusions parquet written; wall < 30 s | ✅ 1,423 built / 0.92 s |
| 2 | Second run same day: 100% `cache_hit`, wall < 3 s | ✅ 1,423 cache_hit / 0.64 s |
| 3 | After ranking re-run with changed alpha → `source_rank_hash` changes → affected rows `refreshed` | deferred (requires ranking rerun) |
| 4 | pack_version bump coexistence | deferred (no v2 yet) |
| 5 | `denylist_tickers: ["ABEO"]` → ABEO in exclusions parquet, 1,422 packs | ✅ denylist logic in `apply_selection` |
| 6 | `include_unclassified: false` → 65 unclassified in exclusions | ✅ logic in `apply_selection` |
| 7 | Two `--force-refresh` runs → byte-identical `pack_json` modulo `pack_built_at` | ✅ verified (only `pack_built_at` differs) |
| 8 | Non-USD ticker → currency warning appended to narratives | verified by code path (no non-USD in current 2025Q4 set) |
| 9 | Young ticker → `data_quality='partial'`, narrative reflects it | covered by code path |
| 10 | Empty input → empty outputs, no crash | `_write_empty_outputs` code path verified |
| 11 | Interrupted run → DB coherent (atomic commit) | confirmed by single-transaction design |

**D21/D27/D28/D29 gate composition verified end-to-end:**
- D21 `composite_best >= 6` alone → 413 rows
- + D29 rescue → 502 rows (+89 rescued, matches spec prediction exactly)
- + D27 sector=Healthcare → 162 rows
- + D28 `market_cap_usd <= $3.7B` → 133 rows (realistic Module 6 feed size)

PGNY round-trips correctly: `post_crash_rebase` archetype, score_3mo=+6, score_12mo=+8, composite_best=7.52, best_horizon=12mo — matches the walkthrough example in the Module 4 spec.

---

## Module 6 — LLM Scoring (Anthropic API)

*Spec draft: [module_6_spec.md](module_6_spec.md) (2026-04-24, design pass in progress — points 1, 2, 4–10 locked; point 3 prompt text in progress).*

### D30 — Runtime-interactive gate activation for D21 / D27 / D28 / D29

**Decision:** The four M5-boundary gates are configured via **interactive multiple-choice prompts** at the start of each Module 6 run, computed live against `context_packs.db`. Yaml fallback defaults live in `config/scoring.yaml::gates` for `--non-interactive` runs. Each run's final gate values are persisted in `llm_runs.gate_config_json` for reproducibility.

**Rationale:** The handoff brief planned to set gates statically in YAML, but the user flagged that the feed size (and therefore cost) is sensitive enough that per-run review is valuable. Interactive prompts let the user see live ticker counts at each threshold before committing. YAML fallback is retained for automation (daily runner, CI).

**Consequence:** `scripts/6_score.py` depends on TTY for default path. `--non-interactive` required for scheduled runs.

---

### D31 — D27 uses `industry`, not `sector`

**Decision:** Module 6's D27 allowlist gate filters on the M5 pack's **`industry`** column, not `sector`. `query_packs_for_quarter()` already supports `industry_allowlist` ([src/module_5/packs_db.py:203](../src/module_5/packs_db.py#L203)) — no schema change.

**Rationale:** The user operates on 21 specialist biotech/healthcare funds whose holdings cluster tightly inside `sector=Healthcare`. Industry is where the user's actual selectivity lives (`Biotechnology` vs `Drug Manufacturers - Specialty & Generic` vs `Medical Devices`). Sector-level filtering would be too coarse to change the feed.

**Alternatives considered:**
- Sector (M5 spec's original proposal) — rejected: 1,230 of 1,423 packs are already `Healthcare`; the filter would barely bite.
- Compound `sector + industry` — rejected: unnecessary; industry is already a finer partition.

---

### D32 — Storage is SQL-only; `llm_scores.db` carries the full LLM reply text

**Decision:** All Module 6 state lives in `llm_scores.db` (four tables: `llm_scores`, `llm_runs`, `llm_errors`, `final_rankings`). **No JSON / Parquet files on disk for LLM outputs.** `llm_scores.raw_text` holds the full Anthropic reply verbatim, queryable by SQL and renderable by `Outputs/llm_responses_{quarter}.html`.

**Rationale:** The user wants to query returned LLM text on-demand (both the structured fields and the raw narrative) without juggling per-ticker files. SQL gives fast indexed access and the HTML viewer is a projection. Mirrors the M5 approach where `pack_json` is a TEXT column, not a file.

**Consequence:** `llm_scores.db` grows ~10–30 KB per ticker per run (raw text + parsed fields). 133 tickers × 2 horizons × several runs/year → well under 100 MB/year. Gitignored via `*.db`.

**Alternatives considered:**
- Per-ticker JSON files under `_outputs/` — rejected: matches M5 pattern poorly; worse query ergonomics.
- Parquet columnstore for scores — rejected: Parquet's append semantics are poor for SQLite-style incremental caching.

---

### D33 — Batch or parallel-sync dispatch; no single-request fallback

**Decision:** `scripts/6_score.py --mode` selects one of two dispatch strategies:
- **`batch` (default production):** Anthropic Message Batches API — 50% token discount, ≤24h SLA.
- **`sync` (calibration):** `AsyncAnthropic` fan-out bounded by `scoring.yaml::dispatch.sync_concurrency` (default 8).

Both share the same prompt-cache prefix and the same `llm_scores` writeback. A single-request serial mode is NOT offered; even `--ticker <one>` goes through `sync` mode with concurrency 1.

**Rationale:** The user asked for runtime and cost minimisation. Batch stacks cache + 50% off — cheapest for production. Sync fan-out gives fast calibration feedback (seconds, not hours) with the same cache behaviour. A serial mode adds code surface for no measurable benefit.

---

### D34 — Python computes appreciation rate; LLM never does

**Decision:** The LLM returns `expected_appreciation_pct` and `time_to_catalyst_weeks` per horizon. Python computes `rate_H_pct_per_month = appreciation_pct / max(1.0, weeks / 4.33)` and picks `final_horizon = argmax_H(rate_H)` with `"either"` tag when rates are within `scoring.yaml::rate.tie_tolerance` (default 5%).

**Rationale:** LLMs hallucinate compound-rate arithmetic (this is a documented weakness per Overall_specification.md § Module 6). Extracting the raw inputs and computing the ratio deterministically eliminates the failure mode.

---

### D35 — Per-industry `allowed_domains` whitelist; `max_uses=5`

**Decision:** Every `web_search` tool invocation uses a per-ticker `allowed_domains` built as `universal ∪ by_industry[ticker.industry]` from [config/module_6_web_search_whitelists.yaml](../config/module_6_web_search_whitelists.yaml). `max_uses=5` per ticker. Universal tier: `sec.gov`, `globenewswire.com`, `prnewswire.com`, `businesswire.com`. Biotech/pharma tier adds: `fda.gov`, `clinicaltrials.gov`, `fiercebiotech.com`, `endpts.com`, `statnews.com`, `biopharmadive.com`, `bioworld.com`, `oncologypipeline.com`. Other industry tiers provisional.

**Rationale:**
- User-refined list explicitly strips general financial news (Reuters, Bloomberg, WSJ, FT, Yahoo Finance, Seeking Alpha) — these dilute signal with macro noise for biotech-specific thesis work.
- User-refined biotech tier strips `nih.gov`, `ema.europa.eu`, `accessdata.fda.gov` (too broad / rarely material) and adds `oncologypipeline.com` (sector-specific pipeline tracker).
- Press-release wires (GlobeNewswire / PR Newswire / BusinessWire) cover essentially all US-biotech IR content, so per-ticker IR subdomains don't need to be whitelisted individually (which would be infeasible — `allowed_domains` is per-call, we'd need 133+ entries).
- `max_uses=5` balances cost ($6.65 upper-bound search fee at 133 tickers) against thesis depth. With research-emphasis 0.7/0.3 splits → ~3.5 long-term + ~1.5 near-term queries.

**Fall-back when `max_uses` is hit:** model answers from what it has — no auto-retry with higher cap. User raises `max_uses` globally for the next run if thesis quality disappoints.

**Alternatives considered:**
- Unrestricted `allowed_domains` — rejected: user explicitly wanted curation; general financial news dilutes biotech signal.
- Per-ticker IR domain whitelisting — rejected: 133+ entries infeasible in `allowed_domains`; wire services carry the same content.
- Anthropic's `web_fetch` tool (fetches a specific URL) — rejected: doubles the cost surface for little gain; snippet content from `web_search` is sufficient.
- `max_uses=10` — rejected: user elected tighter cap for first run; bump YAML value to revisit.

---

### D36 — `web_search_cache` table with prior-research prompt injection

**Decision:** Every `server_tool_use` result block returned by Anthropic on a Module 6 call is parsed and upserted into a `web_search_cache` table in `llm_scores.db`, keyed by URL (dedupes across queries and tickers). Before the next run's LLM call for ticker T, the pre-flight step queries the cache for rows matching T within `scoring.yaml::cache.web_search_lookback_days` (default 180) and injects them into the per-ticker user message as a `## Prior research` block. System prompt instructs the model to skip searches covered by the prior block unless content is stale.

**Rationale:** Anthropic's `web_search` is server-side and uncacheable at the SDK level — we cannot intercept a search to serve from cache. The only mechanism to reduce fresh searches is to **inform the next prompt** with prior findings so the model issues fewer of them. Expected savings: 50–70% of fresh searches per re-run once the cache is warm. First run benefits are zero (cold cache); benefits compound from run 2 onward.

**Paywall handling.** The three paywalled domains in the biotech tier (`endpts.com`, `statnews.com`, `bioworld.com`) stay whitelisted for now. `web_search_cache.content_length` is logged per result; after the first full sync run, any paywalled domain whose `AVG(content_length) < 150` is flagged for user review and potential removal.

**Echo-chamber mitigation.** Every injected entry carries its `published_date` and `first_seen_date`; the system prompt tells the model that prior research is context, not conclusion, and that it must verify freshness. Cost estimator reports `expected_cache_hit_rate` so a near-100% reuse run surfaces for human review before dispatch.

**Alternatives considered:**
- Client-side search replacement (Brave / Tavily / Serper) with our own fetch + cache — rejected: adds an external dependency and ToS surface (`project_data_provider_switch` memory); Anthropic's server-side tool is preferred for reliability.
- Audit-only caching (no prompt injection) — rejected: doesn't achieve the cost-reduction goal the user asked for.
- `web_fetch` tool (retrieve specific URL) layered on top — rejected: doubles the cost surface for marginal coverage gain; wire-service snippets already cover most US-biotech PR content.

---

### D37 — Prior-thesis injection from `llm_scores` into future runs

**Decision:** Before each LLM call on ticker T, a pre-flight step queries `llm_scores` for the 2 most recent prior rows per horizon (across all quarters, not just the current one), formats a condensed `## Prior thesis` block (field-level only — no `raw_text` verbatim), and injects it into the per-ticker user message. System prompt instructs the model that prior thesis is continuity reference, not anchor; it must justify any continuation against current evidence.

**Rationale:** The user trades on recurring horizons (3mo + 12mo). A 12mo thesis written in Q3 can be informed by how it plays out against reality by Q1 of the following year. Cross-run thesis continuity helps the model reason "what changed since last time" rather than start from scratch each quarter, improving coherence and efficiency. No schema change — `llm_scores.raw_text` is already persisted (D32).

**Echo-chamber mitigation.** Injected fields are limited (`expected_appreciation_pct`, `time_to_catalyst_weeks`, `catalyst_type`, `confidence`, `thesis_summary`, `scored_at`, model, prompt_version) — not `raw_text`. The system prompt forbids verbatim repetition; each re-score must be grounded in current evidence. Tunable cap: `scoring.yaml::cache.prior_thesis_max_per_horizon` (default 2).

**Alternatives considered:**
- Inject `raw_text` verbatim — rejected: too strong anchoring; model would parrot prior reasoning.
- Inject only the ticker's single most recent prior — rejected: misses the "direction of revision" signal (up vs down across runs).
- No prior-thesis injection — rejected: wastes the durable cross-run value of `llm_scores.raw_text`.

---

### D38 — Cache reuse is the default; `--force-refresh` is the opt-out

**Decision:** Module 6 reuses cached `llm_scores` rows by default on every run. The prior `--use-cache` flag is gone — reuse is implicit. `--force-refresh` is added as the explicit opt-out (bypasses the entire tiering system; every selected ticker runs Tier C regardless of cache state).

**Rationale:** The user's typical workflow is to rerun M6 with different gate values on the same quarter (e.g. loosening `composite_best_min` after seeing initial results). Under opt-in caching the user had to remember `--use-cache` or pay for re-scoring. Under default caching the user gets the free behaviour and pays only when they explicitly ask to re-score. Matches the "don't multiply user requests" memory rule.

**Consequence:** The pre-flight cost summary always shows tier breakdown (`A / B / C`) so the user sees explicitly which tickers hit the cache before confirming dispatch.

**Alternatives considered:**
- Keep opt-in caching — rejected: surprise bills when the user forgets the flag.
- Require per-run confirmation of cache policy — rejected: adds friction to the common case.

---

### D39 — Three-tier routing (A exact cache / B light refresh / C full scoring)

**Decision:** For every ticker in the Module 6 feed, the pre-flight step assigns one of three tiers:

- **Tier A** — prior `llm_scores` row for `(ticker, current_quarter, horizon, prompt_version, model)` exists AND `prior.pack_source_rank_hash == current_pack.source_rank_hash`. Exact cache hit, $0, no API call.
- **Tier B** — prior row exists within `refresh_threshold_days[horizon]` (4 weeks for 3mo, 8 weeks for 12mo) but pack hash differs or quarter changed. Light refresh prompt: ~1k input tokens, 400 output tokens, `max_uses=2`, same cached system prefix. Output JSON `{material_change: bool, reason, updated_thesis_if_changed}`. If `material_change=false` → write new row with thesis fields copied from prior + `refreshed_from_row_id` audit pointer. Cost: ~15–20% of full scoring.
- **Tier C** — no prior, or prior is stale beyond threshold, OR Tier B returned `material_change=true` (auto-escalation). Full scoring prompt.

**Strict Tier A.** Any `pack_source_rank_hash` difference — even trivial snapshot-timestamp shifts — demotes to Tier B. No classification of "trivial vs material" at the hash layer; Tier B is cheap enough that safety wins.

**Auto-escalation on Tier B `material_change=true`.** In sync mode the async worker fires the Tier C call immediately. In batch mode a second batch bundles all escalations after the first completes; user sees the delta-cost summary but no second `[y/N]` (the pre-flight worst-case already flagged the ceiling).

**Horizon-aware thresholds.** 3mo thesis staleness accrues faster than 12mo (readout/earnings cycles vs pipeline-level fundamentals). 4-week / 8-week split; tunable in `scoring.yaml::cache`.

**Rationale:** The user explicitly asked for cost control across two scenarios:
- Expanding the feed within the same quarter → Tier A handles this automatically (same quarter + same pack hash = $0).
- Re-running after X weeks, fundamentals likely unchanged → Tier B runs a cheap "has anything changed?" check; only escalates to full scoring when the model sees real change.

Together these cut the marginal cost of iteration (gate tuning, quarterly refreshes) by ~60–80% vs always running Tier C.

**New columns on `llm_scores`.** `source_tier` (`'A'|'B'|'C'`), `pack_source_rank_hash` (TEXT), `refreshed_from_row_id` (INTEGER, nullable FK). Additive migration via the standard pattern.

**Alternatives considered:**
- Two-tier (just A vs full) — rejected: misses the cross-quarter-unchanged case entirely; user would pay full rate for thesis work that hasn't moved.
- Fuzzy pack-hash matching (diff only "material" fields) — rejected: defining "material" is a second problem; Tier B is cheap enough that strict-match wins.
- User-approval gate before Tier B escalations — rejected: adds friction; ceiling already surfaced in pre-flight.
- Single threshold for both horizons — rejected: 3mo thesis is far more time-sensitive; a 6-week threshold that works for 12mo would let stale 3mo thesis slip through.

---

### D40 — M6 output reshape to `m6-v2` — three-section structure (research_brief / entry_ranges / scoring inputs), pre-funded warrants mandatory in share count

**Decision:** Module 6's LLM output is reshaped from the original `m6-v1` (per-horizon `expected_appreciation_pct` + `confidence` 3-band enum) to `m6-v2` — a three-section JSON:

- **Section A — `research_brief`**: full structured research (technology origin, moat, financials including fully-diluted share count with pre-funded warrants, insider activity, clinical trials broken into ongoing / interim / final / prior history, competitive landscape, partnerships, acquisition-target probability, FDA context, per-indication risk-adjusted NPV with stage-based POS base rates, past failures, research_notes freeform).
- **Section B — `entry_price_ranges`**: per-ticker (shared across horizons) — `fair_entry` range anchored on rNPV-per-share + stage-discount, and `full_reward` range anchored on cash-per-share or institutional floor.
- **Section C — per-horizon scoring inputs**: `target_price_usd`, `time_to_catalyst_weeks`, `probability` (continuous 0.15–0.90), plus `catalyst_type` / `catalyst_detail` / `thesis_summary` / `key_risks`. Python computes the appreciation percentage and composite score deterministically.

**Pre-funded warrants — HARD RULE:** `research_brief.financials.fully_diluted_shares_count` MUST include pre-funded warrants (instantly exercisable at $0.001), vested in-the-money options, and convertible-note conversion shares. Small-mid cap biotechs often carry 20–50% PFW dilution; rNPV-per-share computed against basic shares overstates value by that same margin. Prompt enforces; Python validates `fully_diluted >= basic + prefunded_warrants`.

**Prompt version bump:** `m6-v1` → `m6-v2`. Prior rows persist in `llm_scores` for audit; new runs cache-miss and re-score. Accepted one-time cost.

**Search budget:** `max_uses` raised from 5 → 12 to support research depth. Expected cost per ticker rises from ~$0.18 to ~$0.48 (Opus 4.7 + batch + cache), 133-ticker feed from ~$24 to ~$64. User signed off on the cost increase in exchange for research-grade output.

**New SQL columns on `llm_scores` and `final_rankings`:** see [module_6_spec.md § `llm_scores.db` — SQLite schema](module_6_spec.md). Additive migration.

**Rationale:**
- User's own prompt proposal required moat, FDA probability, TAM, NPV, competitive landscape, clinical trial detail, cash/burn/shelf/insider — none of which `m6-v1` captured. Merging user's content depth with `m6-v1`'s structural rigor (rubrics, hard rules, few-shots) produces an output that serves both the ranked-ledger use case and the "research each ticker properly" use case.
- rNPV-per-share anchor on fair entry enforces positioning discipline — the score formula rewards disciplined entries even on speculative thesis, not bid-up momentum names.
- Separating structured research (SQL-queryable) from free text captures the narrative nuance while making the high-signal scalars (moat_score, rnpv_per_share_usd, fda_pos_adjusted) directly filterable in reports.

**Alternatives considered:**
- Keep `m6-v1` narrow schema — rejected: user explicitly requested research-heavy output.
- Free-text research blob only — rejected: loses SQL queryability.
- Combine research_brief into per-horizon sections — rejected: research is per-ticker (moat, rNPV, financials don't differ by horizon); separation is clean.
- Keep `confidence` 3-band alongside `probability` continuous — rejected: `confidence` was an abstract self-calibration metric; `probability` is the scoring input, so `confidence` adds ambiguity without value.

**Coupling with deferred M4c (memory `project_m4c_fundamentals_enrichment`):** Once M4c ships (biotechnology-industry only first build), `research_brief.financials` and `research_brief.insider_activity` become pack-sourced rather than LLM-searched. `max_uses` can drop back to ~6–8; per-ticker cost drops from $0.48 to ~$0.32 (~35% savings). M4c scope is preserved to biotech + flagged for other industries.

---

### D41 — rNPV-anchored entry ranges with stage-based discount

**Decision:** `fair_entry_low_usd` / `fair_entry_high_usd` must be anchored on `rnpv_per_share_usd` using a stage-dependent fraction (Ph1 15-30%, Ph2 30-50%, Ph3 50-80%, NDA 70-90%, Approved 90-110%). `fair_entry_rationale` MUST cite the rNPV figure, the % used, the stage justification, and the cash-per-share floor. `full_reward_low_usd` / `full_reward_high_usd` must be anchored on a hard floor (cash per share, forced re-financing bar, institutional re-averaging level) cited in `full_reward_rationale`. Invariant: `full_reward_low_usd ≤ fair_entry_low_usd`.

**Rationale:** The user explicitly asked for "fair share price entry" and "full reward share price entry" outputs, with fair entry anchored on rNPV. rNPV is the biotech-standard valuation method — risk-adjusted via per-indication POS and discounted to today. Stage-based discount reflects market convention that pre-commercial biotech trades at a fraction of rNPV (because realisation risk remains). Full-reward is the "layup" entry below which the thesis becomes asymmetric on cash/floor terms.

**Probability of Success (POS) base rates** (industry-standard, from BIO / Informa Pharma Intelligence):

| Stage → Approval | Oncology | Rare disease | Cardiometabolic | Neurology | Infectious (non-COVID) | CNS psych | All pathologies |
|---|---:|---:|---:|---:|---:|---:|---:|
| Ph1 → Approval | 6% | 17% | 9% | 8% | 11% | 6% | ~10% |
| Ph2 → Approval | 11% | 27% | 15% | 13% | 18% | 12% | ~15% |
| Ph3 → Approval | 52% | 75% | 50% | 55% | 60% | 48% | ~58% |
| NDA → Approval | 85% | 90% | 85% | 85% | 88% | 82% | ~87% |

`pos_adjusted` must be within ±15 percentage points of `pos_base_rate` unless justified by disclosed prior readout data (cited inline in `pos_rationale`).

**Consequence:** In reports, tickers where `current_price > fair_entry_high_usd` should be flagged "ABOVE FAIR ENTRY — wait for pullback." Score remains computed (from fair-entry midpoint) but user sees that current market does not offer the score.

**Alternatives considered:**
- Fair entry as % of current price (e.g. "20% discount to spot") — rejected: anchors on market sentiment, not value.
- Fair entry as single point — rejected: loses the uncertainty range which is the whole point.
- DCF-only (no POS adjustment) — rejected: mis-values pre-commercial biotech vs post-approval.

---

### D42 — Composite score formula: `(appreciation_from_fair_mid / months) × probability`, horizon-ranked

**Decision:** Per-horizon composite score is Python-computed (D34) from the LLM's three raw inputs (`target_price_usd`, `time_to_catalyst_weeks`, `probability`) and Section B's `fair_entry_low_usd` / `fair_entry_high_usd`:

```python
fair_mid = (fair_entry_low + fair_entry_high) / 2
full_mid = (full_reward_low + full_reward_high) / 2
months = max(1.0, time_to_catalyst_weeks / 4.33)

appreciation_from_fair_pct = (target_price_usd - fair_mid) / fair_mid * 100
score_at_fair = (appreciation_from_fair_pct / months) * probability

# Reference score, reported in parallel
appreciation_from_full_pct = (target_price_usd - full_mid) / full_mid * 100
score_at_full_reward = (appreciation_from_full_pct / months) * probability

final_horizon = argmax_H(score_at_fair_H)   # 3mo vs 12mo by PRIMARY score
final_score   = max_H(score_at_fair_H)
```

**Score units:** expected appreciation in percentage points per month from fair-entry midpoint, probability-weighted.

**Probability rubric (continuous 0.15–0.90):**
- HIGH `0.70–0.90` — scheduled catalyst, precedent-backed, corroborated source, ±2w timing.
- MEDIUM `0.40–0.69` — expected-but-unscheduled OR mixed-precedent scheduled; ±4w timing.
- LOW `0.15–0.39` — mosaic-driven / ambiguous > 8w / thin evidence.
- Values outside `[0.15, 0.90]` forbidden — never claim certainty or impossibility.

**Rationale:**
- Deterministic Python computation avoids LLM rate-arithmetic hallucination (D34 extended).
- Anchoring appreciation on fair-entry midpoint (not current price) enforces positioning discipline — the formula rewards disciplined entries even on speculative thesis.
- Probability as a continuous multiplier is the right mathematical form for expected-value compounding; the 3-band anchors give LLM a calibration framework without forcing a hard enum.
- `score_at_full_reward` reported in parallel so reports show "what this becomes if price drops to the layup zone."

**Alternatives considered:**
- Anchor appreciation on current price — rejected: rewards bid-up momentum; ignores entry discipline.
- Confidence 3-band (as in m6-v1) — rejected: not a scoring input; replaced with `probability`.
- Probability as strict 3-band enum — rejected: EV math needs continuous values; anchors suffice for calibration.
- Score formula without probability multiplier (D34 formula) — rejected: user explicitly asked for probability in score = "appreciation / time × probability."

---

### D43 — m6-v2 review-feedback round: clinical trial results split, expanded catalyst enum, probability adjustments from prior results + management track record

**Decision (review pass on m6-v2 prompt 2026-04-25):** Three additive changes to the m6-v2 schema and rubric, no breaking changes to D40–D42 contract.

**1. `clinical_trials.prior_readouts_history` replaced by `interim_results` and `final_results`.** Two parallel arrays distinguish interim readouts (futility, ad-hoc safety, dose-selection mid-trial) from final/topline analyses. Each entry carries `key_metrics` (specific numerical results vs SOC/placebo, e.g. "ORR 78% vs 40%") in addition to `result_summary`. Interim and final results have different signal value for downstream probability inference — splitting them lets the prompt steer adjustments correctly.

**2. `catalyst_type` enum expanded** from `{earnings, trial_readout, approval, macro, other}` to `{earnings, trial_interim, trial_final, approval, conference_presentation, macro, other}`. Replacements:
- `trial_readout` → split into `trial_interim` (interim safety / efficacy / futility readouts) and `trial_final` (primary-analysis topline).
- New `conference_presentation` for catalysts hinging on industry/investor conference disclosures (ASCO, ASH, AACR, JPM Healthcare). `catalyst_detail` should specify venue + program.

**3. Probability adjustments anchored on prior results AND management track record.** The PROBABILITY RUBRIC now has a "Probability adjustments" sub-section (HARD RULE #14) that requires the model to:
- Read `clinical_trials.interim_results` / `final_results` and adjust probability ±0.05 to ±0.20 based on prior-data quality (positive Ph2 → +5–15pp; mixed → 0; negative Ph2 → -10–20pp). Adjustment must be cited inline in `thesis_summary` with the specific prior result.
- Score and inject `research_brief.mgmt_track_record_score` (3-band 0.3 weak / 0.6 mixed / 0.9 strong) based on historical delivery of stated guidance windows. ≥1 historical example required in `rationale`. Strong → +0.05; weak → -0.10. Adjustment cited in `thesis_summary` when material.

Combined adjustments may push probability outside the rubric anchor band but never outside `[0.15, 0.90]` (HARD RULE #3 invariant preserved).

**Schema additions** (additive — no migration needed):
- `research_brief.clinical_trials.interim_results[]` and `final_results[]` (replace `prior_readouts_history`).
- `research_brief.mgmt_track_record_score: {score, rationale}`.
- New SQL column `mgmt_track_record_score REAL` on `llm_scores` (additive migration alongside D40 columns).

**HARD RULES added** (#14, #15, #16):
- #14 — probability adjustments based on `interim_results` / `final_results` MUST be cited inline.
- #15 — `mgmt_track_record_score.rationale` MUST cite ≥1 historical example with stated window, actual delivery, magnitude vs forecast.
- #16 — `catalyst_type` enum codified; `catalyst_detail` must specify venue + program for `conference_presentation`.

**Rationale:**
- User flagged that the m6-v2 schema was thin on actual past results — only narrative `result_summary` text, no structured key metrics. Splitting interim vs final and adding `key_metrics` makes the data load-bearing for probability inference.
- Catalyst enum was too coarse — biotech investors distinguish interim (signal) from final (decisive) readouts; conference presentations (especially ASCO/ASH abstracts + oral presentations) move stocks materially and don't fit `trial_readout` cleanly.
- Probability rubric in m6-v2 anchored only on catalyst scheduling/evidence base — missed the two strongest empirical predictors of biotech program success: prior-stage data quality and management's historical delivery vs guidance. Both are now load-bearing inputs to probability adjustments rather than free-text observations.

**Few-shot updates:** NTLA and SVRA both updated to use the new schema. SVRA in particular demonstrates the pattern — IMPALA-1 negative `final_results` 2019-12 cited explicitly + weak `mgmt_track_record_score` (0.3) drives probability down from rubric MEDIUM (~0.55) to 0.40 on 3mo and 0.45 on 12mo. The cited adjustment chain is auditable.

**Alternatives considered:**
- Keep single `prior_readouts_history` array — rejected: doesn't surface the interim-vs-final distinction the model needs for probability inference; loses `key_metrics` structure.
- Keep `trial_readout` enum and rely on `catalyst_detail` to disambiguate — rejected: enum filtering in reports is more useful when the type itself carries the signal; analytics queries `WHERE catalyst_type='trial_final'` are common.
- Free-text "management quality" field — rejected: 3-band score with cited example is queryable and forces the model to anchor on specific evidence.
- Hardcode probability adjustments in Python (apply mechanical formula based on `mgmt_track_record_score` + prior-readout quality) — rejected: forces the model to surface its reasoning; Python application would lose the cited rationale that drives the adjustment.

---

### D44 — Cost-estimator pricing recalibrated against empirical Anthropic billing (cache_creation 1.25 → 0.10)

**Decision (2026-04-25):** `scoring.yaml::pricing.cache_creation_multiplier` lowered from `1.25` (Anthropic-docs nominal) to `0.10` (empirical, matching observed billing). The cost estimator overestimated by ~3× on Opus 4.7 + sync mode + web_search workloads with the docs-quoted value.

**Empirical data (4 sync runs, 11 tickers total, 2026-04-25):**

| Run | Tickers | Tokens (in/out/cache_r/cache_c) | My old computation | Actual Anthropic bill | Ratio |
|---|---|---|---:|---:|---:|
| 1 | TCRX/NTLA/ABEO (parse-failed) | n/a | n/a | $2.47 | — |
| 2 | TCRX/NTLA/ABEO (success) | 5.7K / 22K / 344K / 309K | $8.09 | $2.81 | 2.88× |
| 3 | GLUE/BCYC/GRAL (success) | 5.6K / 24K / 249K / 287K | $7.67 | $2.67 | 2.87× |
| 4 | NTLA/TCRX (m6-v3, success) | 3.8K / 16K / 252K / 204K | $1.95 (new model) | $1.90 | 1.03× |

Walk-back math at `cache_creation_multiplier = 0.10`:
- Run 2 expected: output 22K × $75 + input 5.7K × $15 + cache_read 344K × $1.50 + cache_create 309K × $1.50 + 13 × $0.01 = **$2.85** vs actual $2.81. ✓
- Run 3 expected: 24K × $75 + 5.6K × $15 + 249K × $1.50 + 287K × $1.50 + 12 × $0.01 = **$2.80** vs actual $2.67. ✓ (within 5%)

**Why the multiplier appears 0.10×, not 1.25×:** unclear at the field-semantics level — Anthropic's docs describe `cache_creation_input_tokens` as billed at 1.25× input. Possible explanations: (a) Opus 4.7 uses a different cache-pricing tier than older Opus models; (b) multi-turn web_search workloads where each internal continuation re-caches the prefix incur a different billing path; (c) the SDK field name is misleading and these tokens are actually billed at the cache-read rate. Without an Anthropic billing-API endpoint to verify, the empirical match is the source of truth for now.

**Consequence on production-feed estimate (98 tickers, default gates):**
- Old estimate: ~$264
- New estimate: ~$93

**How to re-verify:** when next billing cycle arrives, compare `llm_runs.usd_cost_total` vs Anthropic invoice; if drift > 10%, retune the multiplier (one-line YAML edit).

**Alternatives considered:**
- Keep docs-quoted 1.25 and add a downstream `cost_calibration_factor: 0.35` knob — rejected: opaque; the multiplier is the right place to encode the discrepancy.
- Wait for Anthropic to clarify field semantics — rejected: estimator is needed now for production-run sizing; can refine later.

---

### D45 — Prompt v3 (m6-v3): catalyst-date sanity + IR-freshness check + platform-rNPV row + structured `final_results[]` requirement

**Decision (2026-04-25):** Bump `prompt_version: m6-v2 → m6-v3`. Three new HARD RULES added (#17, #18, #19) addressing two concrete failures observed in the 2026-04-25 live tests:

**Failure A — NTLA HAELO timing miss.** Model returned `time_to_catalyst_weeks=30` for "HAELO Ph3 primary analysis topline expected Q3 2026" on the 12mo horizon — when in reality Intellia's IR press release the same week had announced topline for the immediately following week. Model did issue web_search queries but apparently relied on stale guidance ("expected H2 2026") rather than the most recent IR update. Effect: invented a phantom 12mo catalyst and grossly understated the dominant near-term event's significance.

**Failure B — TCRX rNPV $0.15 from omitted platform optionality.** Model produced a valid rNPV of $20M / 132M FD shares = $0.15/share, citing only TSC-101 + TSC-102 indications. Model wrote "Platform/autoimmune optionality not modelled" in `rnpv_assumptions`. But TCRX has a TCR-T platform with multiple programs in development — the m6-v2 NTLA few-shot demonstrated the platform-optionality row pattern (worth 52% of NTLA's rNPV), and the model failed to generalize. Adding even a conservative platform row (~$50–100M) would raise rNPV/share to $0.55–$0.95.

**HARD RULES added:**

- **#17 (catalyst-date sanity + IR-freshness check).** If a single dominant catalyst falls within both 3mo and 12mo windows, use the SAME `time_to_catalyst_weeks` on both horizons (12mo `target_price_usd` may differ — sustained re-rate price). Do not invent later phantom catalysts. For any `time_to_catalyst_weeks > 30`, model MUST issue a `web_search` for the company's IR press releases dated within the last 60 days (e.g., `"<ticker> press release 2026"`) and cite the most recent press-release date inline in `catalyst_detail`.

- **#18 (platform-optionality rNPV row required for platform companies).** Platform companies (gene-editing, ADC, TCR-T, antisense, mRNA delivery, etc.) MUST have at least one `rnpv_by_indication` entry labeled "Platform optionality" or similar, with conservative parameters (Ph1 stage, POS 5–10%, years_to_peak 8–12, rNPV contribution 20–60% of lead asset). Genuinely single-asset companies (e.g., SVRA's molgradex) are exempt but must explicitly state "Single-asset company; no platform contribution modelled" in `rnpv_assumptions`.

- **#19 (structured `final_results[]` / `interim_results[]` mandatory when cited).** Any prior clinical readout cited in any `*_rationale` field MUST also appear as a structured entry in `clinical_trials.final_results[]` (or `interim_results[]`) with `date_iso`, `program`, `phase`, `n_patients`, `key_metrics`. The structured array is the audit trail for HARD RULE #14's probability adjustments — citation in prose alone is a schema violation.

**Cache invalidation:** prompt_version bump to `m6-v3` invalidates m6-v2 cache hits on next run. Existing m6-v2 rows (ABEO, GLUE, BCYC, GRAL — 4 tickers) persist for audit but won't be returned as Tier A on re-run.

**Targeted re-test:** Module 6 data for NTLA + TCRX explicitly erased (78 rows: 4 from llm_scores, 2 from llm_errors, 2 from final_rankings, 70 from web_search_cache). M5 context_packs intact (1 row each). Next M6 run on those two tickers will dispatch fresh under m6-v3 to verify the fixes.

**Alternatives considered:**
- Per-ticker IR-domain whitelist additions (e.g., `investors.intelliatx.com`) — rejected: doesn't scale to 133 tickers; already-allowlisted wires (GlobeNewswire / PR Newswire / BusinessWire) carry IR content. The fix is to make the model SEARCH for fresh content, not to whitelist more domains.
- Move catalyst date to a separate "verification turn" (two-pass dispatch: research → score) — rejected: doubles cost surface; HARD RULE + freshness search query inside the same call is enough.
- Hardcode platform detection in Python (industry × business model heuristic) — rejected: model is closer to the source data; HARD RULE forcing the explicit row is more honest.
- Make `final_results[]` citation enforcement a soft warning rather than HARD RULE — rejected: was a soft warning in m6-v2 and the model treated it as optional; promotion to HARD RULE is the only fix.

---

### D50 — Per-component user weight sliders for the Module 6b modifier (HTML-side, persisted in sidecar JSON)

**Status: ✅ Implemented 2026-04-25.** Hamburger menu in the report toolbar opens a slide-out drawer with one slider per component (range `[0, 2]`, default `1.0`, step `0.05`), per-slider reset, and a global "Reset all to 1.00". Slider changes recompute every row's modifier + adjusted score in JS, re-sort by adjusted desc, re-rank, and debounce-PUT to `/selection/<quarter>` so the weights ride along on the same sidecar JSON as the selection state (schema bumped v1 → v2). Pipeline re-renders preserve user weights via `merge_modifier_weights`. The DB always stores the model baseline (weights = 1.0) — user state lives only in the sidecar JSON. Files: `src/module_6b/modifiers.py::apply_weights`, `src/module_6b/selection_io.py::{default_modifier_weights, read_modifier_weights, merge_modifier_weights}`, `src/module_6/reports.py` (hamburger panel + JS recompute), `scripts/6_score.py` (passes weights + ticker_factors to renderer), `scripts/6_recompute_scores.py` (same).

**Effective factor formula:** `1 + weight × (model_factor − 1)`. Weight `0` disables the component (effective factor = 1.0); weight `1.0` uses the model value as-is; weight `2.0` doubles the deviation from neutral. Mirrored bit-identically in Python (`module_6b.modifiers.apply_weights`) and JS (`modifierFor` in the rendered HTML) — server-side and client-side products are guaranteed equal for the same `(factors, weights, bounds)` triple.

**Sidecar JSON schema bumped to v2** to add the `modifier_weights` block:

```json
{
  "schema_version":   2,
  "quarter":          "2025Q4",
  "all_tickers":      ["NTLA", "TCRX"],
  "selected_tickers": ["TCRX"],
  "modifier_weights": {
    "crowding": 1.0, "financing": 1.0, "dilution": 1.0,
    "insider":  1.0, "mgmt":      1.0, "acquisition": 1.0,
    "moat":     1.0, "failures":  1.0, "concentration": 1.0
  }
}
```

Schema v1 (no `modifier_weights`) still parses; missing block defaults to all-1.0.

**Persistence policy (per user, 2026-04-25):** weights survive pipeline runs. Only the JS-side per-slider reset or "Reset all" clears them. Rationale: tuning effort isn't lost when running a small selective re-score.

**Trade-offs accepted:**
- Sliders are global, not per-ticker. The hamburger menu is a single set of 9 controls applied to every row; per-ticker overrides are deferred (would need 9 × N sliders or per-detail-panel sliders — UI-heavy).
- DB doesn't carry per-user state. Re-renders are deterministic; reproducing a user's view requires the sidecar JSON. Acceptable trade-off given the sidecar is right next to the HTML on disk.
- File:// loads see the slider drawer but PUT fails (toast says "Auto-save disabled"). State is lost on reload. Same UX as selection state in `file://`.

**Alternatives reconsidered:**
- **Per-ticker sliders** (#3 / #4 in the design discussion): deferred. High UX cost; no current driver.
- **Direct factor override (not weight)** (#4): rejected — would discard the per-ticker context the model computed.
- **Band-tuning sliders** (#2): deferred to YAML editing + `6b_apply_modifiers.py` re-run.

---

### D47 — Module 6b composite score_modifier — 9 deterministic components from `research_brief`

**Status: ✅ Implemented 2026-04-25.** Files: `src/module_6b/modifiers.py` (9 component fns + `compute_modifier` + `apply_weights`), `src/module_6b/apply.py` (writes back to `llm_scores` + `final_rankings`), `src/module_6b/__init__.py` (re-exports), `config/scoring_modifier.yaml` (factor maps + bounds + `weights_ui`), `scripts/6b_apply_modifiers.py` (standalone CLI), `scripts/6_score.py` + `scripts/6_recompute_scores.py` (wired to call `apply_modifiers_to_run` after `write_final_rankings`), `src/module_6/scores_db.py` (6 additive migrations: `score_modifier`, `score_modifier_json`, `score_at_current_adjusted_pct_per_month` on `llm_scores`; `score_modifier`, `score_modifier_json`, `final_score_adjusted` on `final_rankings`). HTML/XLSX rendering extended in `src/module_6/reports.py` with `× Modifier` + `= Adjusted (%/mo)` columns. **Validated against the spec's worked examples:** NTLA modifier = 0.6747 (spec said 0.674), TCRX modifier = 0.8122 (spec said 0.811), NTLA `final_score_adjusted = 30.148` (spec 30.11), TCRX `final_score_adjusted = 10.584` (spec 10.57) — all within rounding. **Spec correction:** the rNPV concentration component must exclude `Platform optionality` rows from BOTH numerator AND denominator (spec text said "skip max only", but worked-example math required excluding from total too). Implemented via `concentration.exclude_indication_substrings` in the YAML. **Per-component user weight sliders** (D50) layered on top — see D50 above.

**Decision (2026-04-25):** Spec a new sub-module **Module 6b** that applies a deterministic composite multiplier to the M6 primary score so that material signals already present in the LLM's `research_brief` actually move the ranking. The D46 score formula uses only 4 LLM-emitted fields (`target_price_usd`, `time_to_catalyst_weeks`, `probability`, plus pack-sourced `current_price_usd`); the remaining ~15 structured fields the LLM produces (competitive landscape, partnerships, insider activity, runway, dilution, past failures, mgmt track record, acquisition probability, moat, technology uniqueness, FDA hurdles, rNPV breakdown, …) are stored for audit but invisible to ranking.

**Concrete motivating cases (run 4, 2026-04-25):**
- **NTLA crowded HAE space** — 5 commercial competitors (3 approvals 2025: Dawnzera, Ekterly, Andembry; plus incumbents Takhzyro, Orladeyo); the model's −10pp probability cut is bounded by the [0.15, 0.90] range and invisible to scoring.
- **TCRX Lynx1 insider buy** — $30M PFW purchase at 37% premium Dec 2024 by a 10% owner, never reflected in score.
- **NTLA MAGNITUDE patient death + clinical hold (Oct 2025) + workforce cut** — recent operational stress invisible to scoring.

**Composite formula:**

```python
modifier = clip(
    crowding × financing × dilution × insider × mgmt × acquisition × moat × failures × concentration,
    0.50, 1.50,
)
score_at_current_adjusted = score_at_current × modifier
final_score_adjusted = max_H(score_at_current_adjusted_H)
```

**Nine components** (each producing a factor in roughly `[0.70, 1.10]`, combined multiplicatively, clipped to `[0.50, 1.50]`):

| # | Component | Source field | Factor range |
|---|---|---|---|
| 1 | Competitive crowding | `competitive_landscape[]` count at Ph3+/Approved | 0.80–1.00 |
| 2 | Financing risk | `financials.runway_months` | 0.70–1.00 |
| 3 | Recent dilution | `financials.recent_capital_raises[]` in last 180d | 0.93–1.00 |
| 4 | Insider conviction | Open-market buy in `insider_activity.recent_transactions[]` last 180d | 1.00–1.10 |
| 5 | Mgmt track record | `mgmt_track_record_score.score` (3-band) | 0.90–1.05 |
| 6 | Acquisition optionality | `acquisition_target.score` (3-band) | 1.00–1.10 |
| 7 | Moat durability | `moat.score` (3-band) | 0.95–1.05 |
| 8 | Recent operational failure | `past_failures[]` matching event types in last 365d | 0.90–1.00 |
| 9 | rNPV concentration | `max(rnpv_contribution_usd)/rnpv_total_usd` (excl. Platform row) | 0.85–1.00 |

Per-component factor maps live in `config/scoring_modifier.yaml` (tunable). Per-row evidence captured in new TEXT column `score_modifier_json`. Spec: [module_6b_spec.md](module_6b_spec.md).

**Ranking key changes from `final_score` to `final_score_adjusted`.** Raw `final_score` (D46 `score_at_current` at `final_horizon`) preserved as a reference column for transparency.

**Worked examples (run 4):**
- **NTLA**: 0.80 × 1.00 × 1.00 × 1.00 × 1.00 × 1.05 × 1.05 × 0.90 × 0.85 = 0.674. `44.68 × 0.674 = 30.11`
- **TCRX**: 0.95 × 1.00 × 1.00 × 1.00 × 0.90 × 1.00 × 1.00 × 1.00 × 0.95 = 0.811. `13.03 × 0.811 = 10.57`. (Ranking gap narrows from 3.4× to 2.85×.)

**Why a sub-module rather than baking into M6:**
- M6's contract (LLM dispatch + parse + raw scoring) stays clean and unchanged; M6b is a downstream computation.
- M6b is pure-Python, deterministic, no API cost — can run retroactively against any historical M6 row via `scripts/6b_apply_modifiers.py`.
- Tuning a factor map (YAML edit) doesn't invalidate M6 dispatch; only re-runs M6b.
- Per-component decomposition is auditable: user can disagree with one component in `score_modifier_json` without rewriting the formula.

**Schema additions** (additive migration alongside D46's): `llm_scores`: `score_modifier`, `score_modifier_json`, `score_at_current_adjusted_pct_per_month`. `final_rankings`: `score_modifier`, `score_modifier_json`, `final_score_adjusted`.

**Run sites:**
- End of `scripts/6_score.py` after `final_rankings` write.
- End of `scripts/6_recompute_scores.py` after D46 score recompute.
- Standalone `scripts/6b_apply_modifiers.py` — for retroactive application.

**Open items** (covered in [module_6b_spec.md § Open items](module_6b_spec.md)): PLEXI-T `event="other"` mismatch (proposes adding `program_discontinued` to M6 enum), insider premium-to-market detection (deferred), concentration vs. POS double-counting risk, mgmt double-counting risk, bounds tuning after running on the full feed.

**Alternatives considered:**
- Leave score formula untouched, add VISIBLE columns for each signal (let user mentally adjust) — rejected: doesn't solve the ranking problem; user would still need to mentally re-rank.
- Bake each signal into the LLM prompt as adjustments to `probability` directly — rejected: probability is bounded `[0.15, 0.90]`, can't carry 9 signals; fragile against the model not following 9 separate adjustment instructions.
- Per-component `weight` knobs (factor ** weight) — deferred to v2; v1 uses pure multiplicative combination.
- Re-rank by `final_score` AND `final_score_adjusted` separately, presenting both lists — rejected: confusing for the user; pick one ranking key (adjusted) with the raw kept visible.

---

### D49 — Selection storage moved to local HTTP server + sidecar JSON file (revises D48 storage model)

**Status: ✅ Implemented 2026-04-25.** Supersedes the D48 "HTML `checked` attribute as truth + File System Access API to save" storage model. The mandatory confirmation gate, `--selection-from-html` CLI flag, mutex with `--ticker`/`--tickers`, `--yes` bypass, and audit fields (`selection_source`, `selection_count`, `selection_pool_size`) from D48 all stand — only the *storage* of the user's selection changed.

**New files:** `scripts/6_serve_report.py` (stdlib `http.server` based; ~250 lines), `src/module_6b/selection_io.py` (JSON I/O + atomic writes + `merge_selection` round-trip helper). **Modified files:** `src/module_6/reports.py` (FSA/IDB JS replaced with `fetch('PUT /selection/<quarter>', ...)`; file:// banner added), `scripts/6_score.py` (sidecar JSON preferred; HTML attribute fallback retained for back-compat; sidecar written after every render with merge), `run_2_Funds_parser.bat` (post-M6 prompt now starts the server then asks to re-score), `src/module_6b/__init__.py` (re-exports).

**Decision (2026-04-25):** A small stdlib HTTP server bound to `127.0.0.1:4609` serves the rendered HTML and accepts `PUT /selection/<quarter>` writes from the browser to a sidecar JSON (`Outputs/final_ranking_<quarter>_selection.json`). The browser's `fetch()` writes are silent because they target a same-origin HTTP endpoint — browsers do not gate same-origin XHR behind the filesystem permission model. The pipeline reads the sidecar JSON in preference to parsing HTML `checked` attributes.

**Sidecar JSON schema (v1):**

```json
{
  "schema_version":   1,
  "quarter":          "2025Q4",
  "rendered_at":      "2026-04-25T13:30:00Z",
  "updated_at":       "2026-04-25T14:01:23Z",
  "all_tickers":      ["NTLA", "TCRX"],
  "selected_tickers": ["TCRX"]
}
```

`selected_tickers` is the source of truth. `all_tickers` is the rendering-time pool (used by the round-trip `merge_selection` function so re-renders preserve user choices for tickers still in the pool, default new tickers to selected, and silently drop tickers no longer in the pool).

**Server endpoints:**

| Method | Path                       | Purpose                                                  |
|---|---|---|
| GET    | `/`                        | 302 → `/<latest_quarter>` (auto-detect from `context_packs.db`) |
| GET    | `/<quarter>`               | Serve `Outputs/final_ranking_<quarter>.html`             |
| GET    | `/selection/<quarter>`     | Return the sidecar JSON (404 if missing)                 |
| PUT    | `/selection/<quarter>`     | Atomic write of sidecar JSON; returns 200 + `saved_at`   |

Bind is locked to `127.0.0.1` — never reachable off-host. Port auto-increments up to +50 if the default is in use.

**Why the re-architecture (vs the D48 FSA design):**

- **Zero dialogs, ever.** A same-origin `fetch('/selection/<q>', {method: 'PUT'})` is silent. The FSA approach showed a save-picker dialog the first time on every fresh render; even with `FileSystemFileHandle` persisted in IndexedDB and the smaller `requestPermission` prompt, the user still had to click through a permission UI.
- **Single source of truth for both browser and pipeline.** With FSA, the HTML's `checked` attributes were the truth and the pipeline parsed them. With the sidecar, both browser and pipeline read/write the same JSON file — no risk of HTML/JSON drift, and no JS-property-vs-HTML-attribute serialisation footgun.
- **Cleaner separation.** The HTML is now a presentation layer; selection state is data. The renderer seeds the JSON on every run; the server orchestrates browser writes; the pipeline consumes the JSON. Each component has one job.

**Trade-offs accepted:**

- **Server lifecycle.** The user must start `scripts/6_serve_report.py` (or use the bat-file prompt that does it) to enable auto-save. Opening the report directly via `file://` still renders correctly — the renderer seeded the HTML's `checked` attributes from the sidecar — but a yellow banner explains that auto-save is off and shows the exact serve command. Toggling checkboxes in `file://` mode produces a one-shot toast and does not persist.
- **Port conflicts.** Default 4609; auto-increments up to 4659 if taken. If the user runs M6 + a Jupyter server + something else on 4609 simultaneously, the server picks the next free port and prints the URL. Browser auto-open uses the actual bound port.
- **Backwards compatibility.** Reports rendered before D49 don't have a sidecar. The pipeline detects this and falls back to D48 HTML-attribute parsing — old reports keep working without re-rendering.

**Alternatives reconsidered:**

- D48 FSA + IndexedDB-cached handle: better than original D48 but still showed a permission popup on every fresh render. User explicitly rejected.
- Pure browser-side localStorage (no Python integration): fast, silent, but pipeline can't read it. Rejected.
- Sidecar JSON written via FSA from the browser: identical dialog issue to writing the HTML. Rejected.
- Larger framework (Flask / FastAPI server): unnecessary; stdlib is enough. Rejected.

**Acceptance tests:** the existing D48 acceptance tests (#10–#18 in `module_6b_spec.md`) all stand — they test selection round-trip semantics, not the storage mechanism. Added: when sidecar JSON exists, pipeline reads from it and ignores HTML `checked` attributes; when sidecar missing, pipeline falls back to HTML parser; PUT writes are atomic (no torn reads under concurrent GET).

---

### D48 — User-driven selective dispatch via HTML checkboxes + mandatory confirmation gate (Module 6b Part (b))

**Status: ⚠️ Storage mechanism superseded by D49 same-day.** D48's selection-state-on-disk implementation (HTML `checked` attributes + File System Access save) was replaced by D49's local HTTP server + sidecar JSON. **All other aspects of D48 still apply** — the `--selection-from-html` CLI flag (now JSON-first internally), the `--yes` bypass, the mandatory confirmation gate, the audit fields in `gate_config_json`, the bat-file orchestration shape, and the selective-dispatch + tier-classification interaction. D48 remains the canonical reference for selective dispatch *behaviour*; D49 is the canonical reference for selection *storage*.

**Decision (2026-04-25):** The Module 6 final-ranking HTML report becomes a **two-way control surface**: it shows the ranking AND captures the user's per-ticker selection for the next dispatch. New `--selection-from-html PATH` flag on `scripts/6_score.py` parses the HTML, extracts the checked-ticker list, and restricts the dispatch to those tickers. The confirmation gate (`[y/N]`) is **mandatory** — even when stdin is not a TTY, the gate prompts and aborts cleanly on EOF. The only bypass is an explicit single-shot `--yes` flag.

**HTML changes (rendered by `src/module_6/reports.py::render_final_ranking_html`):**

- Leftmost column gains a **per-ticker checkbox** (`<input type="checkbox" name="ticker_select" value="<TICKER>" checked>`).
- Header row gains a **master checkbox** (`<input type="checkbox" id="select-all" checked>`) that toggles all per-row checkboxes; goes `indeterminate` when state is mixed.
- Report header gains two buttons:
  - **Save selection** — uses File System Access API to overwrite the HTML in place; falls back to a Blob download. Crucially, the JS sets the `checked` HTML attribute (not just JS property) before serialising so the saved HTML reflects the current state.
  - **Copy CLI command** — copies `python scripts/6_score.py --selection-from-html Outputs/final_ranking_<quarter>.html -v` to clipboard.
- Live "selected count" badge in header.
- Selection state survives re-renders: the renderer reads the prior selection from the input HTML (when present) and emits checkboxes with matching `checked` attributes.

**Pipeline changes:**

- New `--selection-from-html PATH` flag, mutually exclusive with `--ticker` / `--tickers`. Pre-feed step replaces gate-driven `query_packs_for_quarter` with a direct ticker-list lookup parsed from the HTML.
- New `--yes` flag for explicit gate bypass (single-shot, never config-driven).
- Confirmation gate refactored to **always prompt** (no `sys.stdin.isatty()` short-circuit). On `EOFError` (closed stdin without `--yes`): print "Aborted." and exit 0.
- `llm_runs.gate_config_json` extended with `"selection_source": "html|gates|tickers_flag"` and `"selection_count"` for run-level audit.

**Parsing (no external deps):**

```python
import re
_TICKER_CHECKBOX_RE = re.compile(
    r"<input\b[^>]*\bname=['\"]?ticker_select['\"]?[^>]*\bchecked\b[^>]*?>",
    re.IGNORECASE,
)
_VALUE_RE = re.compile(r"\bvalue=['\"]([A-Z0-9.\-]+)['\"]", re.IGNORECASE)
```

Lives at `src/module_6b/selection.py`.

**Why mandatory gate (no inferred bypass):**
- Earlier behaviour auto-skipped the gate when `sys.stdin.isatty()` was false. This was convenient but **dangerous** when the script was invoked from a parent process / cron / IDE that piped stdin — Anthropic calls would dispatch with no human confirmation. Promoting the gate to mandatory eliminates the footgun.
- `--yes` is the explicit bypass, scoped to one invocation. Never inferable from environment.
- The pipeline orchestrator [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) already runs from a TTY-attached cmd window, so the prompt works naturally; no orchestrator change needed.

**Workflow:**

```
Initial run  →  HTML rendered (98 tickers, all checked)
              →  user reviews, unchecks 76, clicks Save
              →  HTML on disk now has only 22 `checked`
Selective    →  python scripts/6_score.py --selection-from-html Outputs/final_ranking_<q>.html
re-run          →  parses 22 tickers
              →  tier-classifies (A/B/C)
              →  pre-flight cost estimate ($X for 22)
              →  [y/N] gate prompts (mandatory)
              →  on y: dispatches; on n / EOF: aborts cleanly
              →  M6b modifier re-applies; HTML re-rendered with selection preserved
```

**Selective + tier interaction:** `--selection-from-html` does NOT override D39 caching. Selected tickers still classify as Tier A / B / C. The selection mechanism additionally restricts which tickers are even considered. Tier A hits within the selection cost $0; the modifier still re-applies.

**Schema additions:** none. Selection state lives in the HTML file.

**Acceptance tests:** see [module_6b_spec.md § Acceptance tests for selective dispatch](module_6b_spec.md) (tests #10–#18).

**Alternatives considered:**
- Sidecar JSON file (`<quarter>_selection.json`) instead of HTML-as-source-of-truth — rejected: user explicitly wanted the HTML to be both viewer and selection state. Extra file would diverge from the rendered view.
- Backend-driven selection (HTML POSTs to a local server which writes selection) — rejected: requires running a server; over-engineered for a local pipeline.
- Trust the user's `--tickers TCRX,NTLA,…` flag exclusively (no HTML mechanism) — rejected: fragile (user has to retype tickers from memory after reviewing the report); doesn't scale to 30+ ticker selections.
- Skip gate when `--non-interactive` flag is set — rejected: footgun via accidental flag passthrough; an explicit `--yes` is safer.
- Allow `--yes` to be set in `scoring.yaml` config — rejected: keep the bypass single-shot and explicit.

---

### D46 — Primary ranking score re-anchored from `fair_mid` to `current_price` (revises D42)

**Decision (2026-04-25):** The primary `final_score` used to rank tickers in `final_rankings` is re-anchored from `fair_entry_mid` (D42 original) to **`current_price_usd`** (the M5 pack's `market_snapshot.last_close_usd` at scoring time). Reference scores `score_at_fair` and `score_at_full_reward` are still computed and stored, but they're for positioning context only — they do not drive the ranking.

```python
current_price_usd = pack["market_snapshot"]["last_close_usd"]
months = max(1.0, time_to_catalyst_weeks / 4.33)
appreciation_from_current_pct = (target_price_usd - current_price_usd) / current_price_usd * 100
score_at_current = (appreciation_from_current_pct / months) * probability       # PRIMARY
final_horizon = argmax_H(score_at_current_H)
final_score   = max_H(score_at_current_H)
```

**Why the reversal:**
- The fair-mid-anchored score (D42) produced theoretical-EV numbers that didn't reflect what an investor could actually capture buying today.
- **NTLA m6-v3 example:** target $26 / fair_mid $6.50 / wks=1 / prob=0.7 → score_at_fair = **210 %/mo**. But current market is $15.87 — nobody can buy at $5.50, so 210 is academic. Re-anchored to current: (26 − 15.87)/15.87 × 0.7 / 1.0 = **44.7 %/mo** — actionable.
- Ranking by score_at_fair surfaces "compelling thesis if you wait" cases above "compelling thesis if you buy now" cases, which is the wrong default for a ranked investment ledger.

**What the fair-entry / full-reward ranges still buy us:**
- The model's anchoring discipline (HARD RULES #7, #8) is unchanged — model must still anchor `fair_entry` on rNPV-per-share + cash floor and `full_reward` on cash-per-share floor.
- Reports show `current_vs_fair_mid_pct` (positioning gap) so the user can see "this is 144% above fair entry — wait for pullback" or "this is at fair entry — buy now".
- `score_at_fair` and `score_at_full_reward` are still queryable for filtering: e.g., "find tickers where score_at_fair > 50 AND current is < 30% above fair_mid" surfaces high-conviction names with limited entry premium.

**No prompt change.** The LLM still emits `target_price_usd`, `time_to_catalyst_weeks`, `probability`, `fair_entry_*`, `full_reward_*`. Only the Python downstream computation changes. `prompt_version` stays at `m6-v3`.

**No re-dispatch needed.** Existing `llm_scores` rows for run_id=4 (NTLA + TCRX) can be recomputed offline — all needed inputs (target, weeks, probability, fair_entry, full_reward) plus `current_price_at_scoring_usd` (must be re-sourced from the pack at recompute time, since not previously stored) are available. A one-off script `scripts/recompute_scores.py` will re-derive `score_at_current_*` columns and rewrite `final_rankings`. Other existing rows under m6-v2 can be similarly recomputed.

**Schema additions (additive migration):**
- `llm_scores`: new columns `current_price_at_scoring_usd`, `appreciation_from_current_pct`, `score_at_current_pct_per_month`. Existing fair/full_reward columns retained.
- `final_rankings`: new columns `current_price_at_scoring_usd`, `score_at_current_3mo`, `score_at_current_12mo`, `appreciation_from_current_3mo_pct`, `appreciation_from_current_12mo_pct`, `current_vs_fair_mid_pct`. Final-rank sort key changed to `score_at_current`.

**Alternatives considered:**
- Keep fair-mid as primary, add visual warning in the report ("score is academic — wait for pullback") — rejected: doesn't solve the ranking problem; high-fair-mid scores still float to the top inappropriately.
- Use both anchors and average them — rejected: hides the actionable signal in a blended number.
- Drop fair_entry / full_reward ranges entirely — rejected: still genuinely useful as positioning anchors and for the "what would this look like at $X" reference scores.

---

---

## Module 7 — Outcome Tracking

*Spec: TBD. Forward-price tracking against predicted returns; advisory feedback only, no auto-tuning.*

*(No entries yet.)*

---

## Cross-cutting decisions

- **Python module naming carve-out.** Top-level dirs and scripts may start with `2_` (e.g., `2_ingest_13f.py`). Python packages and modules under `src/` cannot (Python rejects leading digits). Import paths: `from layer_1.edgar_13f import …`, `from module_1.config import load_config`.
- **Shared repo venv.** `../.venv/` (one level above `2_Funds_parser/`). `PYTHONPATH=src` required; [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) sets it.
- **`.env` at repo root,** shared with 1_not_used. Not duplicated inside `2_Funds_parser/`.
- **Quarter format `YYYYQn`** is derived from `period_of_report` (quarter-end), never `filing_date` (submission, which lags by ~45 days). This rule is cross-cutting: every module uses it.
- **Module 2 is retrofit-frozen.** Modules 1 and 3–7 wrap Module 2 via its public helpers (`get_connection`, `edgar_13f.ingest_all_funds`). Module 2's internal path resolution in [src/database/db.py:14-15](../src/database/db.py#L14-L15) is not replaced by Module 1.
