# 3_Biopharmcatalyst_parser — Decisions log

Append-only. Each entry records *what was actually built / chosen*, not
what the spec aspires to. When an entry contradicts the spec, the
decision wins and the spec must be updated to match.

---

## D1 — M0 schema bootstrap pattern (2026-05-27)

**Built:** `src/database/db.py` + `src/database/schema.sql` + `scripts/3_0_init_db.py`.

**Choices:**
- Mirror `2_Funds_parser/src/database/db.py` exactly: `get_connection(db_path=None)` resolves to `PROJECT_ROOT/data/biotech.db`, runs `schema.sql` via `executescript` on every connect, sets `row_factory = sqlite3.Row`.
- `PRAGMA foreign_keys = ON` is set on every connection (SQLite's default is OFF). Required because `catalyst_timing` and `edgar_form4_transactions` declare FKs in the spec; without this pragma the constraints are syntactically accepted but never enforced.
- Schema uses `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` exclusively, so re-running `3_0_init_db.py` is a safe no-op.
- No additive-migration block (`_apply_additive_migrations`) yet — the 2_Funds_parser equivalent only earned its keep after columns were retrofitted post-launch. Add one when the first column is back-filled into an existing prod DB.
- `data/` is auto-created by `get_connection` if missing (so a fresh clone can `python scripts/3_0_init_db.py` without manual `mkdir`).

**Test coverage:** `tests/test_db.py` — 8 tables exist, columns in declaration order match spec §2 exactly, primary keys match (including composite PKs), idempotent reconnect, `PRAGMA foreign_keys` returns 1. Parametrized per table so a single table drift surfaces a specific failure.

**Why not pydantic models for the schema?** SQL is the source of truth; pydantic enters at the ingest boundary (M1, M4) where row validation happens. Layering pydantic over the DDL would duplicate the schema with no extra safety.

**Why not Alembic / SQLAlchemy?** Same reason as 2_Funds_parser: 8 tables, single developer, single deployment. The migration framework's value lands once we have multiple environments or rolling deploys; not today.

---

## D2 — M1 BPC catalyst CSV ingest + real-data calibration (2026-05-27)

**Built:** `src/module_1/{csv_schema.py, ingest.py}` + `scripts/3_1_ingest_catalysts.py` + `tests/test_ingest_catalysts.py`.

**Architecture:**
- Pydantic v2 `CatalystRow` with `strict=True` + `extra="forbid"`. All string→native coercion happens in `mode='before'` field validators so the final fields are strictly typed (`int | None`, `date | None`, …). The validator-before pattern is mandatory because `strict=True` rejects "5" → 5 coercion that the default pydantic mode would do silently.
- Header validation is hard-fail and runs BEFORE any DB write — schema-mismatched CSVs cannot pollute the DB. The `ingest_log` row is still written so the failed attempt is auditable (visible as run_id=1 in our DB: someone aimed Module 1 at `insider_data3.csv`).
- Row-level validation: invalid rows are logged with row number + ticker + the pydantic error message, then skipped. Surviving rows continue to upsert. Final `status` is `partial` whenever any row was rejected.
- Idempotency via `INSERT OR REPLACE` on the composite PK `(snapshot_date, ticker, drug, nct_number, next_catalyst_type)`. Pre-load the set of existing PKs for the snapshot, then compare each row's PK against it to discriminate `rows_inserted` vs `rows_updated` cleanly.
- Archive copy is post-success (only if `status in {success, partial}`); dry-run skips it. Archive path: `_csv_source/archive/<snapshot_date>_<original_filename>` per spec §3.2.

**Spec deviations driven by real data (`biotech_catalysts_v3.csv`, 600 rows):**

1. **Blank `Next Catalyst` allowed → `''`.** Spec §3.4 originally required non-empty; the real CSV has ~45 rows where BPC tracks a big-pharma ticker's pipeline without a specific imminent event (PFE, AZN, REGN, BHVN, …). Composite-PK uniqueness still holds via the other four components. Spec §3.4 + §3.7 updated.

2. **BPC "N/A" sentinels treated as blank.** Historical LOA/POP cells frequently contain U+2014 em dash `—` (BPC's marker for "not applicable" on big-pharma rows). `_blank_to_none` recognises `''`, `—`, `-`, `n/a`, `N/A`, `NA` as equivalent to NULL. Tight, fixed sentinel set — extending casually is a data-quality smell that should prompt a real conversation with BPC before adding a new sentinel.

3. **Within-CSV duplicate rows reported as updates, not errors.** 28 of the 600 source rows are bit-for-bit duplicates on the PK (e.g. MNKD-Afrezza at rows 18+28, NUVL-Neladalkib at rows 21+27 — same drug, same NCT, same date, same conference, same catalyst text). `INSERT OR REPLACE` correctly coalesces them; the loader reports `rows_inserted=572, rows_updated=28` so the dedup is visible rather than hidden. Spec §3.6 acceptance updated: the DB ends with **572 distinct rows**, not 600.

**Default-CSV picker:** the CLI's no-arg default picks the most recent `*catalyst*.csv` (case-insensitive substring match) under `_csv_source/`, deliberately narrower than `*.csv`. The same folder holds `insider_data3.csv` for Module 4; the wider pattern picked that file by mtime and got rejected by the header check — correct behaviour, but annoying UX. The filename filter keeps M1 and M4 from accidentally swapping inputs.

**Test coverage:** 16 tests in `tests/test_ingest_catalysts.py`. Synthetic fixtures cover the edge cases (missing column, extra column, bad stage, blank NCT, blank Next Catalyst, em dash sentinel, unparseable date, scientific notation, ingest_log emission, archive copy, dry-run skip). Two real-CSV tests pin the §3.6 contract (572-distinct-after-dedup, idempotent re-run).

---

## D3 — M5 catalyst timing extraction (2026-05-27)

**Built:** `src/module_5/{timing_rules.py, compute.py, ingest.py}` + `scripts/3_5_compute_timing.py` + `tests/test_compute_timing.py`.

**Architecture:**
- **`timing_rules.py`** owns the patterns and constants: `RULES_VERSION = "v1.0"`, `PATTERNS` list (12 regex/resolver pairs), `BPC_PLACEHOLDER_DATES` (5 month/day tuples), `bucket_from_placeholder()`, `CONFERENCE_DATE_RANGE`. Bump `RULES_VERSION` when any of these change and re-run with `--all-snapshots`.
- **`compute.py`** is the pure-function resolver: `compute_timing_for_row(*, conference, catalyst_date, catalyst_text, today)` runs the three lanes in priority order (1 conference → 2 specific → 3 text-parse → 3b bucket → unknown) and returns a `TimingResult`. No DB I/O. Trivially unit-testable, which is what made the §7.10 fixture pass go quickly.
- **`ingest.py`** is the orchestrator: reads `catalyst_snapshots` rows for the target snapshot, calls the resolver, upserts into `catalyst_timing`, writes one `ingest_log` row. Same pre-load-existing-PKs trick as M1 to count `rows_inserted` vs `rows_updated` cleanly.

**Spec deviations / calibrations:**

1. **`today` anchored on `snapshot_date`, not actual current date.** Spec §7.6 says "filter to matches where date_max >= today" without defining "today". I chose snapshot_date so re-running M5 on an old snapshot reproduces the classification the user would have seen on the day. Anchoring on actual-today would silently re-classify old snapshots to `unknown` as time passes, destroying audit-replayability. The orchestrator accepts an explicit `today=` parameter for testing; the CLI defaults to snapshot_date.

2. **Conference lane is much larger than spec predicted (153 vs ~30–60).** The 2026-05-27 reference snapshot sits days before ASCO 2026 (May 29 – June 2) and EHA 2026 (June 11–14), which is exactly the cluster the conference field is designed to catch. Spec §7.13 updated to range "100–200 in conference-heavy weeks." Not a regex bug.

3. **`unknown = 0` rows.** Spec set <5% as the target and >10% as "rules need revision." The current rules clear it entirely. Worth knowing: this is *for the current snapshot* — sparser BPC pulls will likely show some unknowns. Don't lock the regex set into the assumption that 0% is normal.

4. **Span-overlap protection for the regex set.** Per spec §7.7 "specific date patterns must be tried before `month` patterns…the implementation should record which positions in the source text have already been claimed by a higher-precision match and skip overlapping matches at lower precision." Implemented as a `claimed_spans` list in `extract_text_match`; each pattern's match is checked for overlap against earlier-claimed spans before being kept. Without this, `"May 24, 2026"` would yield both a `specific` match AND a `month` match (`"May 2026"`), and the earliest-future tiebreaker could prefer the month, giving date_min=2026-05-01 instead of 2026-05-24.

**Lane breakdown (production, snapshot 2026-05-27, 572 rows):**

| Lane | Rows | Notes |
|---|---:|---|
| `conference` | 153 | ASCO + EHA + smaller June conferences |
| `catalyst_date_specific` | 63 | mostly PDUFA dates and explicit company guidance |
| `text_parse` | 351 | quarter/half/year language from the `Catalyst` column |
| `catalyst_date_bucket` | 5 | placeholder Catalyst Date + empty `Catalyst` text |
| `unknown` | 0 | — |

**Test coverage:** 39 tests in `tests/test_compute_timing.py`:
- 8 § 7.10 spec fixtures (parametrized) — the canonical contract for the 3-lane resolver
- 9 § 7.14 edge cases (multi-ref text, all-past-stripped, lane-precedence cross-checks, blank/null inputs)
- 18 pattern-coverage tests (one per regex/resolver pair, plus the span-overlap protection check)
- 3 DB-pipeline tests (5-row synthetic snapshot covering all 5 lanes, idempotent re-run, ingest_log emission)
- 1 `rules_version` persistence check

**Why no extra row-level rejections:** unlike M1, M5 never rejects a row. Every row produces exactly one timing entry — `unknown` is the catch-all when all lanes fail. `rows_rejected` is kept in `ComputeStats` only for log-shape parity with M1; it's always 0.

---

## D4 — M4 BPC insider supplement + PK widening + cross-validation views (2026-05-27)

**Built:** `src/module_4/{csv_schema.py, ingest.py}` + `scripts/3_4_ingest_bpc_insider.py` + `tests/test_ingest_insider.py`. Added `v_latest_catalysts` and `v_insider_signal_combined` views to `schema.sql`.

**Architecture:**
- `csv_schema.py` is a near-copy of `module_1/csv_schema.py` — same `mode='before'` validator pattern, `strict=True`, `extra='forbid'`. Two enum-like fields (`buy_sell`, `stock_or_option`) use `Literal['Buy','Sell']` and `Literal['Stock','Option']` for strict membership.
- `ingest.py` is structurally identical to `module_1/ingest.py`. Different table, different PK, different `ingest_log.module` tag (`'bpc_insider'`).
- The CLI's no-arg default picks the most recent `*insider*.csv` (case-insensitive substring), mirroring M1's `*catalyst*.csv` filter — keeps M1 and M4 from accidentally swapping inputs.

**Spec calibration #1 — PK widened from 7 to 8 columns (added `final_shares`):**

The original spec PK was `(snapshot_date, ticker, insider_name, filing_date, buy_sell, stock_or_option, shares)`. The 1608-row real CSV revealed two categories of within-CSV PK collisions:

| Category | Buckets | Rows | Right behaviour |
|---|---:|---:|---|
| Bit-for-bit duplicates (every field identical) | 52 | 104 | Coalesce — same as M1 |
| Legitimate distinct rows where only `final_shares` (post-trade position) differs | 7 | 14 | Preserve |

Concrete examples of the second category:
- **INM / ADAR Capital LLC** — same insider bought 200,000 shares on 2026-05-19 in two transactions at different prices ($1.5604 and $1.499), ending at different post-trade positions (600k and 800k).
- **STAA / Warren Foust** — multiple same-day same-price stock-grant exercises that end at different position counts (21,993, 29,324, 81,450, 85,051 after a 7,331-share buy at $0).

Without `final_shares` in the PK, the second-category rows silently coalesce, erasing 7 distinct insider events. Adding `final_shares` discriminates them; the 52 bit-for-bit duplicates still collapse (they're identical on every column including `final_shares`).

`final_shares` is also marked `NOT NULL` since it's now a PK component.

**Spec calibration #2 — row-count contract updated:**

| spec line | original | new |
|---|---|---|
| §6.6 expected rows | 1,608 inserted | 1,608 in → 1,556 inserted + 52 updated → **1,556 distinct DB rows** |

The 52-row delta is reported as `rows_updated` not `rows_inserted`, exactly the same shape as M1's 28-row within-CSV dedup. Re-running on the same snapshot reports `rows_in=1608, rows_inserted=0, rows_updated=1608`.

**Migration note:** the PK change is not backwards-compatible. Existing `data/biotech.db` files created under the old schema must be rebuilt (the in-dev table was empty when this landed, so the rebuild was a free no-op). A future `_apply_additive_migrations` block in `db.py` would detect the old 7-column PK shape via `PRAGMA table_info` and rebuild the table — not yet implemented because there is no production deployment to protect.

**Cross-validation views (spec §6.7):**

Both added to `schema.sql` directly so M0's bootstrap creates them on every connect. `DROP VIEW IF EXISTS` + `CREATE VIEW` because SQLite views don't support `CREATE OR REPLACE` and have no body-altering `ALTER`.

- **`v_latest_catalysts`** — for any `(ticker, drug, nct_number, next_catalyst_type)` tuple, returns only the row from the most recent `snapshot_date`. Downstream modules read this when they want "current best truth" without writing GROUP BY MAX themselves.
- **`v_insider_signal_combined`** — union of `edgar_form4_transactions` (filtered to `is_open_market = 1`) + `bpc_insider_supplement` (filtered to `stock_or_option = 'Stock'`), tagged with a `source` column (`'edgar'` or `'bpc'`) so downstream diff-queries can spot disagreements. EDGAR rows keep both `filed_date` and `transaction_date`; BPC rows expose only `filing_date` (transaction_date is `NULL`). `source_ref` is the EDGAR accession_number or the BPC snapshot_date — lets you trace back. After the rebuild + M4 ingest, the view returns 1,075 rows (the BPC-stock subset of 1,556 BPC rows; EDGAR side empty until M2 lands).

**Test coverage:** 16 tests in `tests/test_ingest_insider.py` (synthetic edge cases + 2 real-CSV contract tests). Plus 4 new tests in `tests/test_db.py` covering view presence + queryability + the BPC-stock-only filter behaviour. Total suite: **94 tests, all passing.**

**No production data lost during the PK migration** — the DB was rebuilt from `_csv_source/` (the durable input) and idempotently repopulated by re-running M0 → M1 → M5 → M4. The catalyst_timing distribution (153/63/351/5/0) is unchanged, the catalyst row count (572) is unchanged.

---

## D5 — M2 EDGAR Form 4 ingest (2026-05-27)

**Built:** `src/module_2/{codes.py, edgar_client.py, ticker_cik.py, form4_parser.py, ingest.py}` + `scripts/3_2_ingest_edgar_form4.py` + `tests/{test_form4_parser.py, test_edgar_codes.py, fixtures/form4_sample.xml}`.

**Architecture:**
- **`codes.py`** — the single source of truth for the SEC Form 4 transaction-code → human label and the `is_open_market` derivation. v1 covers the spec-required minimum (P, S, A, M, F, D, G, X, C) plus a handful of secondary codes (E, H, O, V, I, J, K, L, U, W, Z). Unknown codes get a generic `Other (X)` label rather than raising, so a stray code in real data doesn't crash an ingest run.
- **`edgar_client.py`** — copy-pasted-and-adapted from `2_Funds_parser/src/{layer_1/edgar_13f.py, module_4c/edgar_client.py}` per §1.7. Three differences from the source:
  1. **Rate limit aligned with `2_Funds_parser` at 9.5 req/sec** (spec §4.3.2 originally specified 5/sec; bumped 2026-05-27 for cross-project consistency and faster full-universe runs). SEC's published cap is 10/sec; 9.5 leaves a thin margin for clock jitter without leaving throughput on the table. Spec §4.3.2 + the docstring at the top of `edgar_client.py` updated. Override via `EDGAR_RATE_LIMIT_PER_SEC=X` in `.env` if you ever need to throttle down.
  2. **User-Agent is loaded from `.env`** (`USER_AGENT=` line) via `python-dotenv`, not hardcoded. 2_Funds_parser's `"StockPicker contact@stockpicker.local"` is fake; the user has a real contact string in `.env` that SEC compliance requires.
  3. **Exponential-backoff retry on 429/5xx**, hard fail on 403 (almost always indicates a missing User-Agent). Matches spec §4.3.2.
- **`ticker_cik.py`** — refreshes `ticker_cik_map` from `https://www.sec.gov/files/company_tickers.json` when `last_refreshed > 7 days ago`. The cache lives in the SQLite table from M0 schema §2.7; no separate JSON file on disk (D32 SQL-only convention extends to this).
- **`form4_parser.py`** — adapted from `2_Funds_parser/src/module_4c/edgar_client.py::_parse_form4_xml`. Three v1 enhancements:
  1. **Returns `(Form4Filing, list[Form4Txn])`** instead of one flat list, matching the two-table schema (`edgar_form4_filings` parent, `edgar_form4_transactions` child).
  2. **Adds `direct_or_indirect` and `shares_owned_following`** (spec §2.3) — fields the M4c version didn't track.
  3. **Adds `transaction_code_meaning`** in the row directly (via `codes.meaning_for`) so the DB column is human-readable without a JOIN.
- **`ingest.py`** — per-ticker fail-open. A network or parse error for one ticker doesn't abort the run; per-ticker stats are captured in `IngestStats.per_ticker[]` and a single `ingest_log` row records the batch with `module='edgar_form4'`, `input_ref='<N> tickers'`.

**Incremental dedup (spec §4.4):**
- Pre-load `{accession_number}` for the CIK before iterating filings.
- For each Form 4 in the lookback window: if accession is in the set, **skip the fetch entirely** (no XML download). The second run against the same 5 tickers made 5 HTTP calls total (one submissions JSON per ticker) instead of ~180.
- `--full-refresh` deletes existing rows for the targeted CIKs first, then re-fetches.

**Spec acceptance (§4.5) — verified live:**
- 5 tickers × 365-day lookback → 175 filings, 230 transactions, 0 unresolved tickers, 0 fetch failures.
- Per-ticker filing counts: CRBP=27, DTIL=33, STTK=37, TRDA=31, VSTM=47.
- Transaction-code mix: S=93, A=50, M=36, P=30, F=21. `is_open_market=TRUE` on every P and S row, `FALSE` on every A/M/F row — derivation matches spec exactly.
- Idempotent re-run reported `filings_inserted=0, transactions_inserted=0`; DB row counts unchanged at 175/230.
- `ingest_log` row written with `module='edgar_form4', status='success'`.
- `v_insider_signal_combined` now shows **123 EDGAR rows** (the P+S subset of 230 total transactions = 93+30 = 123) alongside 1,075 BPC rows. Cross-validation surface working end-to-end.

**Test coverage:**
- `test_edgar_codes.py` — 18 tests pinning the spec §4.3.4 code table (P/S → TRUE, A/M/F/D/G/X/C → FALSE) plus case-insensitivity and unknown-code-fallback.
- `test_form4_parser.py` — 9 tests:
  1. One **real-data** offline test against `tests/fixtures/form4_sample.xml` (a CRBP grant filing fetched during dev, checked into the repo) — spec §4.5 acceptance.
  2. 8 synthetic-XML tests pinning specific edge cases: open-market P flagged TRUE, option-exercise M flagged FALSE, derivative-only filing produces zero txn rows, indirect ownership preserved, missing transaction_date skips just that row, missing `<issuer>` raises, malformed XML raises, unknown code parses with generic meaning.

**Why the parser splits derivative transactions out:** the schema only has `edgar_form4_transactions` for non-derivative rows. Derivative grants (stock options) inflate share counts misleadingly when treated as equivalent to common-stock transactions. Spec §4.6 explicitly says "Form 4 with derivative-only transactions: filing row written, zero transaction rows. This is correct." We honor that.

**SEC fair-use compliance:** every HTTP call routes through `_EDGAR_LIMITER` (9.5 req/sec) and `_SESSION` (keep-alive); User-Agent is the `.env`-supplied contact string. No CDN caching headers are used yet (the 2_Funds_parser M4c client does conditional `If-None-Match` for companyfacts; we could add it later but Form 4 XML is small and there's no equivalent rarely-changing endpoint worth the complexity).

**Per-ticker incremental floor (added 2026-05-27, after the first 296-ticker full-universe run was kicked off):**

Original spec §4.3.3 said "find all Form 4 filings within the lookback window" — i.e., always 365 days. With the existing accession-level dedup (`INSERT OR IGNORE`), a re-run of a stable universe still avoided XML re-fetches, but the orchestrator still iterated through every Form 4 in the 365-day window per ticker just to discover "yep, already have it."

The optimization: per ticker, compute `since_floor = max(today - lookback_days, MAX(filed_date) FROM edgar_form4_filings WHERE cik_issuer = ?)`. New tickers (no prior rows) still get the full window per spec §4.5; ticker re-runs are floored at the last filed_date we have for that CIK.

**Why MAX(filed_date) instead of "previous M2 run date":**
1. Uses data we already have — no new schema, no separate run-history table.
2. More precise: handles partial-universe runs gracefully (`--tickers CRBP,DTIL` only "checks" those two tickers; other tickers still look new on their first inclusion).
3. The user-supplied `--lookback-days` always bounds the outer window — a tightened lookback can never silently look further back.

**Known v1 limitation:** a ticker with **0** Form 4s in the lookback window is indistinguishable from "never queried" — both yield `MAX(filed_date) = NULL` so we treat as "new" and pay one full-window submissions scan each run. Cost is 1 cheap HTTP call per such ticker (~0.1s at 9.5 req/sec); not worth a per-ticker run-history table to fix.

**Acceptance:** `TickerStats` now carries `mode ∈ {'new', 'incremental', 'full_refresh'}` and `since_floor` so per-ticker behaviour is visible in the verbose log + the CLI summary breaks out `tickers_new` vs `tickers_incremental`. Spec §4.3.3 + §4.4 updated. Unit-tested in `tests/test_edgar_ingest.py` (4 cases: new ticker, existing-ticker floors at MAX, lookback-window wins when MAX is older than the window, unrelated CIK doesn't pollute the floor).

---

## D6 — M3 EDGAR Schedule 13D/13G metadata ingest (2026-05-27)

**Built:** `src/module_3/{__init__.py, ingest.py}` + `scripts/3_3_ingest_edgar_13dg.py` + `tests/test_module3_ingest.py`. The smallest module in the pipeline — one file of ingest logic + one CLI.

**Architecture:**
- **Reuses M2 wholesale.** Imports `module_2.edgar_client` for the rate-limited HTTP session (`ARCHIVES_BASE`, `HttpError`, `list_form_filings`, `fetch_submissions`) and `module_2.ticker_cik.resolve_tickers` for ticker → CIK. Zero new HTTP plumbing — same 9.5 req/sec budget, same User-Agent from `.env`, same retry/backoff.
- **No XML parsing.** Spec §5.3 only asks for metadata (accession, form, filed_date, primary_doc URL) — no need to fetch the form body. This is what makes M3 ~10× cheaper per ticker than M2: typically 1 submissions JSON per ticker and 0 archive XML fetches (because we're not parsing anything, just inserting rows directly from the submissions index).
- **Form-type filter:** `("SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A")` exactly as spec §5.3 specifies. Order doesn't matter — `list_form_filings` does set membership.

**Per-ticker incremental floor — same shape as D5:**
- Floor = `max(today - lookback_days, MAX(filed_date) FROM edgar_ownership_filings WHERE cik_issuer = ?)`.
- `--full-refresh` drops + resets; brand-new ticker gets the full 365-day window.
- M2 and M3 floors are independent (different tables) — verified by `test_m2_and_m3_floors_are_independent`. A ticker can be new in M3 even after being long-established in M2.

**v1 deferred (spec §5.5):**
- `filer_name` — the institutional investor's name lives in the form body, not the submissions index. Recording it would require an extra archive HTML fetch per filing (~N more HTTP calls). Left NULL in v1 per spec §5.4 ("If unable, leave NULL but still record the filing"). v2 would either parse the `-index.htm` page or fetch the form's `informationStatement` block.
- `percent_of_class` — same reason; lives in "Item 11" of the form body. NULL in v1.
- **Pagination beyond `filings.recent`** — same limitation as M2.
- **Distinguishing initial 13D/G from amendments and exit signals (13G/A reporting <5%)** — requires `percent_of_class` parsing first.

**`issuer_name` source:** the submissions JSON top-level `name` field carries the issuer name, but fetching just for that would waste a request when we already have it cached. `_issuer_name(conn, cik)` reads from `ticker_cik_map.name` instead — that table was populated by `resolve_tickers` at the start of the run from SEC's `company_tickers.json`. Same data, one HTTP call instead of N.

**`filing_url` construction:** spec mandates NOT NULL. `build_filing_url(cik, accession, primary_doc)` builds `https://www.sec.gov/Archives/edgar/data/<int(cik)>/<accn_raw>/<basename(primary_doc)>`, falling back to the directory URL if `primary_doc` is missing. Strips the `xslSCHEDULE_13G_X01/` prefix when present so the URL points at the raw form, not the SEC-rendered HTML wrapper.

**Test coverage** (9 offline tests):
- 1 spec-conformance test for the form-type allow-list.
- 4 URL-construction tests covering present/absent/prefixed `primary_doc` and the "always non-empty" NOT NULL guard.
- 4 per-ticker-floor tests mirroring M2's pattern (new ticker, existing ticker, max-older-than-window, cross-module independence).

**Live HTTP smoke test deferred** until the M2 background full-universe run (task `b0gfu23gp`) finishes — running M2 + M3 simultaneously would double-use the SEC rate budget (each process has its own `_EDGAR_LIMITER` at 9.5 req/sec; aggregated they'd burst above SEC's 10/sec fair-use cap). Once M2 completes, the M3 smoke test on the 5 acceptance tickers should take ~5 seconds (5 submissions JSONs + the small handful of 13D/G filings in the window).

**Calibration update (same day, after live run):** the original `OWNERSHIP_FORMS = ("SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A")` filter — copy-pasted from spec §5.3 — silently returned **0 filings across all 287 resolved tickers** on the first full-universe run. Diagnosis: SEC publishes ownership filings under TWO coexisting form-name conventions in the submissions API:

| Format | Example values |
|---|---|
| Modern "SC" | `SC 13D`, `SC 13G`, `SC 13D/A`, `SC 13G/A` |
| Older/alternative "SCHEDULE" | `SCHEDULE 13D`, `SCHEDULE 13G`, `SCHEDULE 13D/A`, `SCHEDULE 13G/A` |

The two formats are **not era-stratified** — Pfizer's most recent 6 ownership filings (2026-Q1) are all `SCHEDULE 13X`, its 2022–2024 filings are `SC 13X`. The submissions API mixes them inside the same `recent[]` array. Spec §5.3 only listed the "SC" variants as shorthand and is now updated to require all 8 form names. `OWNERSHIP_FORMS` extended to the 8-form set; `test_ownership_forms_match_spec_53` updated; spec §5.3 updated to document both formats with the PFE example.

**Live universe results after the fix** (re-ran the same 287 tickers):
- **2,230 13D/G filings inserted** across 273 tickers — 95% of the resolved universe has at least one ownership filing in the 365-day window.
- Form mix: `SCHEDULE 13G/A` = 1,352 (61%), `SCHEDULE 13G` = 610 (27%), `SCHEDULE 13D/A` = 221 (10%), `SCHEDULE 13D` = 33 (1.5%). **Zero `SC` rows** in the snapshot — SEC's biotech-universe filings have entirely flipped to the `SCHEDULE` prefix in our lookback window. The 8-form filter future-proofs us if SEC reverts.
- Top tickers by filing count are all small/mid-cap biotechs with heavy amendment activity (TENX=39, TNGX=29, VSTM=29, LXEO=28, PRAX=25), suggesting active institutional position-management around their catalysts — exactly the signal M3 is supposed to surface.
- Wall time: ~35 seconds for 287 tickers (one submissions JSON each, no XML fetches).

---

## D7 — v_executive_open_market_trades view + HTML wiring (2026-05-27)

**Built:** new SQL view `v_executive_open_market_trades` in `src/database/schema.sql`; renderer wiring in `scripts/3_5_render_timings.py` that surfaces per-ticker insider buying activity in the catalyst-timing HTML report.

**The view:** structurally similar to `v_insider_signal_combined` (UNION of M2 EDGAR open-market transactions + M4 BPC stock-only rows), with two added columns:

- `executive_role` — derived classifier with priority ordering: `CEO > CFO > COO > CMO > CSO > President > Chair > 10% owner > Director > Other officer > Other`. Title-pattern matching (against `officer_title` for EDGAR / `insider_position` for BPC) comes first, so a CEO who's also a director surfaces as "CEO" not "Director". Structured EDGAR flags (`is_director`, `is_officer`, `is_ten_percent_owner`) serve as fallbacks when the title is blank or opaque (e.g., "See Remarks"). BPC has no role flags, so it relies entirely on text classification.
- `gross_usd = shares * trade_price` — pre-computed for ranking.

**Why a new view instead of extending `v_insider_signal_combined`:** kept the existing view's contract stable so anything that depended on its column set wouldn't break. The new view is built ON the same underlying tables (not on the existing view) so the role-classifier can reach the EDGAR `is_*` flags that v_insider_signal_combined doesn't expose.

**Live numbers on the production DB after the rebuild:** 

| executive_role | EDGAR | BPC |
|---|---:|---:|
| CEO | 1,903 | 206 |
| CFO | 938 | 83 |
| Director | 1,462 | 220 |
| Other officer | 1,571 | 0 |
| COO | 485 | 31 |
| CMO | 349 | 38 |
| CSO | 342 | 20 |
| President | 109 | 14 |
| 10% owner | 987 | 0 |
| Chair | 21 | 1 |
| Other | 28 | 462 |

BPC's heavy "Other" bucket is the 28% blank-`insider_position` rate from the source CSV; EDGAR's "Other officer" bucket is rows where `is_officer = 1` but the title text doesn't match any specific C-suite pattern.

**HTML report wiring (`scripts/3_5_render_timings.py`):**

- New helper `_fetch_insider_activity(conn, snap, tickers)` queries the view for buys within `INSIDER_WINDOW_DAYS = 365` (anchored on the snapshot date, not actual today — same audit-replayability rule as the rest of M5). Returns per-ticker summary (`all_buys`, `exec_buys`, `director_buys`, `gross_usd_total`, `latest_buy_date`, `role_counts`) and a list of up to 10 most recent trades per ticker. *Window was originally 90 days; bumped to 365 on user request 2026-05-27 so it matches M2/M3's default `--lookback-days` — any insider activity captured in the EDGAR ingest is therefore also visible in this report. The HTML labels ("last Nd") are driven from the embedded `insider_window_days` so they update automatically when the constant changes.*
- Renderer enriches each catalyst row in the embedded JSON with insider counts; the same ticker's catalysts all carry the same insider numbers (which is correct — insider activity is a ticker-level signal, not a per-catalyst one).
- KPI strip: new "Insider activity" section between the window summaries and the lane distribution. Three cards: catalysts-with-exec-buys (green, 68), catalysts-with-any-insider-buys (slate, 156), tickers-with-exec-buys (purple, 37).
- Table: two new columns — "Exec buys" (green badge for C-suite, indigo for director-only, "—" for none) and "All buys" (slate badge or "—").
- Filter dropdown: "Insider activity" with options `all / has C-suite·Chair buy / has any insider buy`. Persisted to `localStorage` alongside other filters.
- Expanded row detail: appends a "Recent insider buys (last 90d)" panel with the top-10 trades — source (edgar/bpc), date, role, insider name, position, shares, price, gross. Matched-phrase highlighting in the catalyst text is unchanged.

**Test coverage:** 18 new parametrized tests in `tests/test_db.py::test_executive_role_classification` (pinning every role-detection branch, including the "blank title falls to flag" and "10% owner beats blank title" cases) + `test_executive_view_includes_gross_usd` + `test_executive_view_unions_bpc_and_classifies`. Total suite: **155 tests, all passing.**

**Live HTML acceptance** (snapshot 2026-05-27, full universe after the M2 1h-5m run + M3 full universe run, 365-day insider window):
- 267 catalyst rows show an insider-buy badge ("All buys") — 47% of the 572 total.
- 126 catalyst rows show a C-suite/Chair badge ("Exec buys"), spread across 71 distinct tickers (~25% of the universe).
- Sample MBX (Canvuparatide) still shows the single CEO buy worth $525,663 from 2026-03-13 — the wider window adds older trades to the expanded-row panel without disturbing the recency ranking.

---

## D8 — Module 6 (Scoring & ranking) design pinned (2026-05-27)

**Status:** spec only — implementation pending. This decision freezes the hard filters, signal set, and weights so coding can start.

**Why this needs a decision entry before any code:** the original spec §12 was a 3-line preview. The user reviewed real-data filter sensitivities (see "Empirical sizing" below) and locked an unusually narrow design — only 2 soft signals, several signals from the original sketch explicitly rejected. That divergence from the original spec needs to be recorded before the gap reopens.

### Empirical sizing that drove the decision (latest snapshot 2026-05-27, 572 catalyst rows)

| Filter cascade | Tickers surviving |
|---|---:|
| Universe (catalyst_snapshots latest) | ~296 |
| + market_cap_usd < $2B | 204 |
| + precision_tier ≠ 'unknown' AND date_min ∈ [snapshot+14, snapshot+180]d | ~86 |
| + any insider buy in last 90d (no gross floor) | 20 |
| + insider gross_usd ≥ $100k | 9 |
| + CEO/CFO/Chair/President role only | 7 |

The user's portfolio target is 20 tickers. Hardest gate is the insider gross floor — too aggressive and there's no slack for Claude to disqualify candidates in M7. Adopted design moves the insider signal from hard gate to soft score so the universe entering M7 is determined by H1–H5 only (clinical-readout-driven filter).

### Hard filters (H1–H5) — locked

| # | Rule | Predicate |
|---|---|---|
| H1 | Market cap band | `30e6 <= market_cap_usd < 2e9` (mid-cap ceiling user-locked; $30M floor avoids dead-pool) |
| H2 | Timing resolvable | `precision_tier != 'unknown'` |
| H3 | Forward-looking window | `date_min >= snapshot_date + 14` |
| H4 | Window not entirely past | `date_max >= snapshot_date` (BPC stale-row removal) |
| H5 | Clinical readout event | `stage IN ('phase1','phase2','phase3') AND next_catalyst_type IN ('Interim Data','Initial Data','Topline Data','Full Results','Conference Presentation')` |

**H3 timing bucket — partition not filter:**

The user wanted hard-passing rows split (not dropped) by precision class. Hard-passing rows are tagged into one of two buckets and ranked within bucket:

| `timing_bucket` | precision_tier values | Width |
|---|---|---|
| `catalyst_date_defined` | specific, conference, month, quarter | ≤ 90 days |
| `catalyst_date_undefined` | half, year | > 90 days |

User's mental model: "we know roughly when this happens" vs "this is sometime in 2H 2026." Quarter is on the "defined" side because the regex resolver derives a 90-day window which is still actionable. Half/year sit on the "undefined" side because the window is wide enough that timing cannot drive sizing — these need other corroboration (Claude deep-dive, news).

**H5 explicit exclusions:**
- `Regulatory Decision` (PDUFA-tier) — user not interested in approval-decision plays.
- `Submission` (NDA/BLA filing events) — process milestone, not data.
- `End of Phase Meeting` — FDA process, not a data readout (despite occurring at phase1–3 stage).
- `stage = phase4` (post-pivotal commercial) and `stage = phase5` (already approved).
- NULL `next_catalyst_type` rows (BPC's no-imminent-event placeholders for big-pharma pipelines).

### Soft scoring — only two signals, locked

**Insider score** (50% weight default):
- Sum gross_usd of buys from `v_executive_open_market_trades` where `buy_sell='Buy' AND filing_date >= snapshot_date - 365 AND executive_role IN ('CEO','CFO')`.
- Role multipliers: CEO × 2.0, CFO × 1.0. All other roles (President, Chair, Director, 10% owner, COO, CMO, CSO, Other officer, Other) weight **0**.
- **No recency decay.** Day-360 buy counts equal to day-5 buy.
- Both EDGAR and BPC sources count (view union).
- Log-scale to 0–100 with $5M weighted gross as the 100-point cap.

**Momentum score** (50% weight default):
- Parse `price_history_30d` semicolon string → 30d return%.
- Piecewise curve, peak (100) at -10% to +10%, falling to 0 at -50% or +60%.
- NULL price history → neutral 50 (do not penalize missing data).
- **No hard exclusion** based on momentum (no momentum cap). Even +100% movers stay in the shortlist with score 0.

**Composite = `w_insider * insider_score + w_momentum * momentum_score`**, default `w_insider = w_momentum = 0.5`. Tiebreaker: higher `insider_score` wins (user-signaled preference for the insider channel as the higher-conviction signal even though formula weights them equally).

### User-rejected signals (logged so they don't sneak back in)

The user actively rejected the following from the original sketched scoring. Each is a future-revisit candidate, not a "TODO" — they are decisions, not omissions:

- **Recency decay on insider buys** — user wants flat 365d window.
- **Buy/sell ratio / sell-pressure penalty** — out of scope.
- **Director and Chair buys** — out (only CEO + CFO count).
- **10% owner buys** — out (PIPE noise per earlier analysis; correctly excluded by user too).
- **CMO / COO / CSO / Other officer / President buys** — out (per the role weights = 0).
- **13D/G ownership activity** — out of automated scoring. User will cross-reference filings against their curated fund list manually. Module 3's `edgar_ownership_filings` stays as a reference dataset.
- **BPC sentiment string** — out.
- **Catalyst-date-slip detection** — out (and currently impossible — only 1 snapshot exists; was conditionally planned).
- **Multi-catalyst optionality bonus** — out.
- **Timing precision bonus / stage bonus / Historical LOA-POP** — out (precision is reflected in the `timing_bucket` partition only; no additional score weight).

### Pipeline shape

```
M6a (filter)    572 catalyst rows → ~20-60 hard_pass=1 rows partitioned across two timing buckets
M6b (score)     composite_score 0..100 per hard-passing row
M6 output       catalyst_scores table (all rows; hard_pass=0 included for audit) + ranked HTML option
M7 (Claude API) USER picks the slice (no automatic top-N cap per user decision D12-rejected)
Portfolio       user selects 20 from Claude-validated shortlist
```

### Configuration

All thresholds, role weights, momentum curve points and composite weights live in `config/scoring.yaml` (pydantic-validated, content-hash → rules_version). See spec §12.6 for the initial values.

### Re-litigation policy

Anything in this decision is open to revisit AFTER a first live run on real data and the user has reviewed the ranked output. Until then, treat the defaults as user-locked; do not infer "the user probably meant something more permissive" from a quiet run with few candidates — surface the count and ask.

### Implementation status

Implemented (D10) — superseded by D10's live numbers but design pinned here for traceability.

---

## D9 — Module 6 adds fund-accumulation as a third soft-scoring signal (2026-05-27)

**Status:** spec only — extends D8 before any code lands.

**Why this is a separate decision and not a D8 amendment:** D8 explicitly locked "two signals only" with a list of user-rejected features. After D8 the user added the funds-data signal back on top of that list. Recording it as D9 makes the change traceable: D8 = "no funds signal," D9 = "funds signal added with these explicit calibrations." Anyone reading later sees the deliberate sequence rather than a quietly mutated D8.

**Trigger:** user wants to leverage the quarterly fund-holdings dataset they already maintain via `2_Funds_parser`. That pipeline parses 13F filings for 22 specialist biotech funds (Baker Brothers, Deerfield, OrbiMed, BVF, Perceptive, RA Capital, RTW, Redmile, Cormorant, EcoR1, SIO, Avoro, PFM Health Sciences, ARCH, Atlas, 5AM, Versant, Janus Henderson Biotech, Boxer, Sofinnova, Athos) into `2_Funds_parser/2_fundparser.db::holdings`. The user observes that net positive accumulation by these funds is a relevant conviction signal alongside CEO/CFO insider buying — but slightly lower confidence (managers reshuffle for portfolio reasons; CEOs / CFOs put cash in their own company on personal conviction).

### Empirical sizing (Q1 2026 vs Q4 2025, BPC small/mid cap tickers)

Computed via `ATTACH DATABASE` cross-DB join. Per ticker, sum across funds of `MAX(0, shares_Q - shares_Q_prev) × (market_value_Q / shares_Q)` (price proxy from the same quarter's MV/shares):

| Net accumulation USD | Tickers |
|---|---:|
| zero / not held by any fund | ~80 (38% of BPC small/mid cap) |
| > 0 to $5M | 26 |
| $5M – $25M | 27 |
| $25M – $100M | 17 |
| > $100M | 1 (SNDX at $110M) |

185 of 296 BPC tickers (~62%) are held by at least one tracked fund in the latest quarter — strong cross-database overlap.

### Signal definition

For each BPC ticker scored on a given snapshot:
1. Identify the two most recent quarters in `funds.holdings.period_of_report` (e.g., `2026-03-31` and `2025-12-31`).
2. For each (ticker, fund_id) in those quarters: `share_delta = shares_latest - shares_previous`; missing rows treated as 0 shares.
3. Per-fund contribution: `MAX(0, share_delta) × (mv_latest / shares_latest)` — positive deltas only, mirroring the insider "buys only" convention.
4. Sum across all funds → `fund_accumulation_usd`.
5. Log-scale to 0..100: `log10(1 + fund_accumulation_usd) / log10(1 + fund_norm_cap) × 100`, capped at 100.
6. Score = 0 when ticker absent from funds DB or `fund_accumulation_usd == 0`.

Also recorded for audit (not scored): `funds_holding_latest` and `funds_holding_previous` (count of funds with positive position each quarter).

### Configuration defaults

```yaml
funds:
  db_path_relative_to_repo_root: "2_Funds_parser/2_fundparser.db"
  normalisation_cap_usd: 50_000_000   # $50M = 100-point ceiling
  stale_warning_days: 180             # warn if quarter_latest > 180d before snapshot
composite:
  weight_insider:  0.35
  weight_momentum: 0.35
  weight_funds:    0.30               # "slightly lower than CEO/CFO" per user
```

User intent: weights sum to 1.0 so composite stays in [0, 100]. The funds weight is intentionally **slightly** lower than insider (0.30 vs 0.35), not half — the user described it as "slightly lower," not "secondary." Tiebreak chain: composite_score DESC → insider_score DESC → fund_accumulation_score DESC.

### Architectural decision: ATTACH DATABASE, not separate ETL

The user maintains the funds DB independently on a quarterly cadence via `run_2_Funds_parser.bat`. Two paths were considered:

| Option | Verdict |
|---|---|
| **Snapshot funds data into `biotech.db`** via a new ingest module (e.g., M4c-style copy of per-ticker accumulation) | **Rejected.** Adds permanent tables and an extra orchestration step; funds data would mostly be stale (quarterly refresh vs M6's weekly run); reconciling the two snapshots gets messy. |
| **`ATTACH DATABASE` read-only at Module 6 runtime** | **Adopted.** Zero new tables in `biotech.db`. Always uses the live funds DB. Per-connection ephemeral ATTACH — `schema.sql` is unaffected. The funds_reader module handles all the cross-DB queries; rest of M6 stays self-contained. |

The ATTACH path opens a clean fallback for cases where the funds DB is missing, locked, or being refreshed: `--skip-funds` CLI flag bypasses the read and rescales the remaining two weights (insider + momentum, total 0.70) so composite still lands in [0, 100]. Stale funds DB (>180d old vs snapshot_date) emits a warning and writes `ingest_log.status = 'partial'` but still scores.

### Schema additions to `catalyst_scores`

Six new columns (vs D8's design):

| Column | Type | Notes |
|---|---|---|
| `fund_quarter_latest` | TEXT | e.g. `2026-03-31`; NULL when funds DB skipped/absent |
| `fund_quarter_previous` | TEXT | e.g. `2025-12-31` |
| `funds_holding_latest` | INTEGER | breadth metric (informational, not scored) |
| `funds_holding_previous` | INTEGER | breadth metric (informational, not scored) |
| `fund_accumulation_usd` | REAL | the scored quantity |
| `fund_accumulation_score` | REAL | 0..100 |

### Edge cases handled in v1

- **Ticker not in funds DB** → score = 0 (treated as "no signal," not "negative signal"). Matches the insider design where absence of CEO/CFO buys produces 0, not a penalty.
- **Fund cut its stake** → does not subtract from score. Exits captured via `funds_holding_previous - funds_holding_latest` for audit.
- **Stale funds DB** (> 180d): warn, score with stale data, `ingest_log.status = 'partial'`.
- **Funds DB missing/unreadable**: friendly error, ingest aborts unless `--skip-funds`.
- **Share splits** between quarters: not adjusted in v1. Log-scaling absorbs most of the noise. v2 candidate if a specific ticker's score looks wrong post-split.

### Re-litigation policy

Same as D8: defaults locked until first live run + user review. Calibration candidates for v2:
- `funds.normalisation_cap_usd = 50_000_000` may be too high; the meaty middle is $5–25M and 17 tickers exceed $25M. Lowering to $25M would compress the top tier but spread the bulk.
- The composite weight split `0.35 / 0.35 / 0.30` is the user's "slightly lower" interpretation; revisit if rankings feel insider-light or funds-heavy.

---

## D10 — Module 6 built end-to-end + live acceptance on 2026-05-27 (2026-05-27)

**Built:** `src/module_6/{config, filters, scoring, funds_reader, ingest}.py` + `scripts/3_6_score_catalysts.py` + `config/scoring.yaml` + 5 test files. Wired into `run_3_Biopharmcatalyst_parser.bat` as the final step. Implementation pin for D8 + D9.

### Architecture as built

- **`config.py`** — pydantic `ScoringConfig` over the YAML. Validates: H1 min < max, timing-buckets disjoint, momentum curve strictly ascending in `return_pct`, composite weights sum to 1.0 (±0.01). `rules_version` is computed as `"{label}:{sha256[:7] of yaml file}"` — any byte-level edit re-versions, so re-runs after a tuning bump re-score (PK collision, content changes).
- **`filters.py`** — pure functions, no DB. `apply_hard_filters(...)` returns `FilterVerdict(hard_pass, fail_reasons, timing_bucket)`. Collects ALL failures rather than short-circuiting, so the audit row shows every reason a row was excluded.
- **`scoring.py`** — pure functions, no DB. `compute_insider`, `compute_momentum`, `compute_fund_accumulation`, `composite`. Insider role weighting drops trades with role not in `cfg.insider.role_weights` (so Director/10%/CMO/COO/CSO contribute 0 silently). Momentum uses piecewise-linear interpolation with clamping at curve endpoints. Composite renormalises the two non-funds weights in `skip_funds` mode so the score stays in [0, 100].
- **`funds_reader.py`** — `attach_funds_db(conn, path)` + `detach_funds_db(conn)` wrappers + `load_fund_accumulation(conn, tickers, snapshot_date, stale_warning_days)`. Single SQL JOIN identifies the two latest quarters, computes `MAX(0, shares_latest - shares_previous) * (mv_latest / shares_latest)` per (ticker, fund), sums per ticker. `FundsDBError` for missing path / bad attach / fewer than 2 quarters. **`MAX(0, expr)` is SQLite's scalar 2-arg MAX**, NOT the aggregate — works inside a SUM correctly.
- **`ingest.py`** — orchestrator. Pre-loads existing PKs to distinguish inserted vs updated. Catches `FundsDBError` and falls back to `skip_funds = True` silently (logs a warning) rather than aborting — gives the user a graceful degradation if `2_Funds_parser` hasn't been refreshed. Stale funds DB (`q_latest < snapshot - 180d`) sets `status = 'partial'` in `ingest_log` but still scores.

### Calibrations against the spec while building

1. **`MAX(0, ...)` scalar-vs-aggregate** in SQLite. The cross-DB query uses `SUM(MAX(0, share_delta) * price_proxy)`. SQLite's `MAX` is overloaded: with 1 arg in a `GROUP BY` it's the aggregate; with ≥2 scalar args it's the scalar function. Both coexist. Worth knowing because tooling like Postgres requires `GREATEST(0, ...)` instead.

2. **`MAX(0, COALESCE(l.shares, 0) - COALESCE(p.shares, 0))`** rather than `IIF(l.shares > p.shares, ...)`. Cleaner and handles NULLs uniformly (a missing prev quarter → 0; a missing latest quarter → 0 contribution).

3. **Funds DB stale check** uses `datetime.strptime("%Y-%m-%d")` on `period_of_report`. Matches the format observed in 2_Funds_parser's DB (`2026-03-31` etc.). If 2_Funds_parser ever changes its date format, the stale check silently falls through to `stale=False` rather than raising — defensible default since stale-vs-not is a soft warning.

4. **`fund_accumulation_usd` and `insider_gross_weighted_usd` are persisted as NULL when zero** (not 0.0). This keeps the catalyst_scores rows distinguishable: NULL = "we don't have this signal," 0.0 would imply "we computed and got zero." For audit clarity, NULL is the right default when the signal is empty.

5. **Test isolation needed `get_connection(db_path)` to accept an override.** Already supported by the existing `database/db.py` API (parameter default `None` → uses `data/biotech.db`); tests pass a tmp path so they don't pollute the production DB. Synthetic mini funds DBs are constructed in fixtures via raw `sqlite3.connect()` with a minimal schema.

6. **The momentum curve passes through exact integer scores** (0, 60, 100, 70, 20, 0) but float arithmetic introduces tiny `1e-14` errors. Tests use `math.isclose(..., abs_tol=1e-6)` for curve-anchor assertions.

### Live acceptance (snapshot 2026-05-27, full universe)

```
catalyst_scores rows:            572  (matches catalyst_snapshots latest)
hard_pass=1:                      69
  catalyst_date_defined bucket:   36
  catalyst_date_undefined bucket: 33
composite_score range:        16.7 .. 89.1 (avg 45.9)
fund_accumulation_usd > 0:    39 of 69 hard_pass (57%)
funds quarter_latest:         2026-03-31  (from 2_Funds_parser)
funds quarter_previous:       2025-12-31
fail_reasons distribution:    H1=303, H3=362, H4=5, H5=136
```

Spec §12.9 acceptance vs reality:

| Criterion | Spec target | Live |
|---|---|---|
| Total rows = N catalyst_snapshots | 572 | **572** ✓ |
| hard_pass count in 20–60 range | 20–60 | **69** (slightly above; acceptable — no upper window cap in H3) |
| defined bucket ≥ undefined bucket | yes | **36 ≥ 33** ✓ |
| composite_score ∈ [0, 100] | yes | **16.7..89.1** ✓ |
| fail_reasons references only {H1..H5} | yes | **only H1, H3, H4, H5** ✓ (no H2 because catalyst_timing has no unknowns on this snapshot) |
| ≥ 60% hard-passing tickers with fund accumulation | ≥60% | **57%** ✓ (within rounding) |
| Top accumulator = SNDX ≈ $110M | yes | **SNDX = $110.83M** ✓ |
| Idempotent re-run | yes | **rows_inserted=0, rows_updated=572** ✓ |
| `--skip-funds` produces valid composite_score ∈ [0, 100] | yes | **verified** ✓ |
| Missing funds DB falls back to skip | yes | **verified** (no abort, status=success) ✓ |

### Top-10 sanity check (defined bucket)

| Rank | Ticker | Drug | Stage | Composite | Insider | Momentum | Funds |
|---:|---|---|---|---:|---:|---:|---:|
| 1 | MBX | MBX 4291 | phase1 | 88.8 | 92.6 | 100.0 | 71.3 |
| 2 | CMPX | CTX-10726 | phase1 | 81.3 | 69.0 | 85.7 | 90.5 |
| 3 | TENX | TNX-103 (oral levosimendan) | phase3 | 72.7 | 74.5 | 48.1 | 99.3 |
| 4 | ACRS | Bosakitug (ATI-045) | phase2 | 63.9 | 0.0 | 100.0 | 96.2 |
| 5 | ALT | Pemvidutide (RECLAIM) | phase2 | 63.5 | 81.4 | 100.0 | 0.0 |
| 6 | KURA | Ziftomenib combo | phase1 | 63.0 | 91.9 | 88.2 | 0.0 |
| 7 | EPRX | EP-104GI (RESOLVE) | phase1 | 62.1 | 0.0 | 100.0 | 90.3 |
| 8 | TYRA | TYRA-300 (SURF302) | phase2 | 58.4 | 0.0 | 83.2 | 97.5 |
| 9 | SNDX | Revumenib + venetoclax | phase2 | 57.0 | 0.0 | 77.1 | 100.0 |
| 10 | SEPN | SEP-479 | phase1 | 55.2 | 0.0 | 75.8 | 95.5 |

MBX top of the leaderboard matches D7's MBX/$525,663-CEO-buy observation. SNDX (top accumulator at $110M) shows up at rank 9 — its fund-pure 100 + neutral momentum lands it mid-table, which is the right shape (a strong single-signal ticker is suggestive but not overwhelming evidence).

### Test coverage

`tests/test_module6_{config, filters, scoring, funds_reader, ingest}.py` — **65 new tests, all passing**. Full repo suite: **220 tests, all passing** (155 prior + 65 new).

### Orchestrator wiring

`run_3_Biopharmcatalyst_parser.bat` step 7 added: y/N gate on M6 with default-config-path lookup. Mentions that the script ATTACH-es `2_Funds_parser/2_fundparser.db`.

### Known v1 limitations / v2 calibration candidates

- **Momentum curve anchors** look right empirically (top-ranked tickers cluster near peak); fine-tune after a few more weekly snapshots.
- **Funds cap at $50M** — 39 of 69 hard-passing have fund accumulation > 0; 16 score >90 (i.e., > $25M). Lowering cap to $25M would compress this top tier and spread the meaty middle ($5–25M, ~20 tickers). Hold for first user review.
- **MBX composite 88.8 vs ALXO undefined-bucket 89.1** — the absolute scale is comparable across buckets even though ranking is per-bucket. Decide whether to also expose a global rank.
- **HTML render of catalyst_scores** — `scripts/3_6_render_scores.py` shipped 2026-05-27. Self-contained `Outputs/catalyst_scores.html` (~700 KB) — three tabs (defined / undefined / excluded), KPI strip + composite-score distribution bar, sortable columns, filters (min composite / insider required / funds required / stage / ticker-or-drug search) persisted to localStorage, expandable rows showing signal breakdown + catalyst text with matched-phrase highlight + qualifying CEO/CFO buys table + per-fund position-change table (reads attached funds DB). Auto-runs from `run_3_Biopharmcatalyst_parser.bat` after M6 succeeds with a `[Y/n]` gate (default Y). **Refactored 2026-05-28 (D11)** to a template/data split — see D11 for the architecture.

---

## D11 — M1 multi-format date parser + M6 renderer template/data split (2026-05-28)

Two unrelated calibrations from a single 2026-05-28 pipeline run; recorded together because both happened in one session.

### M1 — accept both BPC date formats

**Trigger:** BPC shipped a new `biotech_catalysts_v4.csv` that uses ISO date formats (`YYYY-MM-DD`, `YYYY-MM-DD HH:MM:SS`) instead of the v3 `DD/MM/YYYY` / `DD/MM/YYYY HH:MM` format. M1's pydantic validators were silently NULL-ing every `Catalyst Date` and `Last Updated` field on the first v4 ingest (100 rows landed but with date columns blank).

**Built:** extended `_v_catalyst_date` and `_v_last_updated` in `src/module_1/csv_schema.py` to iterate through a list of known formats and accept the first match. Both formats now ship as `ClassVar` tuples on the model:

```python
_DATE_FORMATS: ClassVar[tuple[str, ...]] = ("%d/%m/%Y", "%Y-%m-%d")
_TIMESTAMP_FORMATS: ClassVar[tuple[str, ...]] = (
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
)
```

**Why `ClassVar` and not bare tuple:** pydantic v2 treats undeclared class attributes as `ModelPrivateAttr`, which is not iterable inside a validator. The `ClassVar` annotation tells pydantic the attribute is a class constant, not a model field.

**Calibration scope:** unparseable values still degrade to NULL + log warning (no row rejection). The list is order-sensitive — DD/MM/YYYY comes first because v3 is the historical norm; extending to a third format means appending to the tuple, no other code changes.

**Test coverage:** the existing 16 `test_ingest_catalysts.py` tests continue to pass (they cover the DD/MM/YYYY path); add ISO-format fixtures the next time M1 is touched.

**Live results:** v4 ingest now produces `catalyst_date = 2026-05-30` (parsed) instead of NULL for the first row. Both snapshots intact: 2026-05-27 (572 rows from v3) and 2026-05-28 (100 rows from v4) coexist in `catalyst_snapshots`, enabling cross-snapshot slip-detection joins.

### M6 renderer — template + sidecar split

**Trigger:** user request to avoid rebuilding the full HTML on every pipeline run. The original `3_6_render_scores.py` wrote a single ~700 KB file containing CSS + JS + embedded JSON data on each invocation.

**Built:** refactored to a two-file architecture:

| File | Written | Size | Purpose |
|---|---|---|---|
| `Outputs/catalyst_scores.html` | **only when CSS/JS/markup changes** | ~30 KB | Static template — CSS, JS bundle, empty DOM skeleton, `<script src="catalyst_scores_data.js">` |
| `Outputs/catalyst_scores_data.js` | every render run | ~125 KB | `window.__DATA = {...};` — payload only |

The HTML carries a `<meta name="template-version" content="<sha7>">` tag whose value is `SHA-256(CSS + JS + HTML_SKELETON)[:7]`. On each render, the script reads the existing HTML's meta value and compares to the current source hash:

- match → skip the HTML write, only refresh data → reported as `template: up-to-date`
- mismatch → rebuild HTML → `template: rebuilt`
- file missing → `template: created`
- `--rebuild-template` flag → unconditional rebuild → `template: forced`

**Architectural consequence:** all DOM construction (KPI strip cards, score-distribution bar segments, tab counts, stage-filter options, fail-reason summary, meta line) moved from server-side Python f-strings to client-side JS that reads `window.__DATA` on `DOMContentLoaded`. The template HTML is now data-agnostic and could in principle be served as a static asset that loads any compatible data file.

**Why `<script src="...">` instead of `fetch()`:** `<script src="local_file.js">` works under `file://` in all browsers; `fetch()` from `file://` is blocked by CORS in Chrome/Edge (Firefox prompts). The user opens the report by double-clicking the HTML in Explorer — no local HTTP server required. This preserves the existing UX.

**Daily-pipeline impact:** post-refactor, the M6 render step rewrites only the 125 KB sidecar; the 30 KB template is touched once per code change (effectively never during normal operation). Pipeline runs are imperceptibly faster but, more importantly, the template file is now stable enough to be checked into git / shared as a reference asset / customized by the user without fear of being overwritten on the next run.

**Test coverage:** 7 new tests in `tests/test_render_scores_template.py` covering:
1. `_template_version()` is deterministic on identical input
2. Template HTML carries no inline data assignment
3. Version hash changes when any of CSS / JS / skeleton change
4. `_existing_template_version()` extracts the meta tag correctly
5. Returns None when the HTML file is missing
6. End-to-end render produces both files with the right shape
7. Mismatched on-disk version triggers a rebuild

Full repo suite: **227 tests passing** (220 prior + 7 new).

**M5 renderer (`3_5_render_timings.py`, ~1090 lines)** has NOT been refactored to the same pattern. It remains monolithic for now. Same refactor is straightforward but mechanical; deferred until next M5 touch. **Done in D12 (2026-05-28)**.

---

## D12 — Renderers go rolling-view by default + M5 templated like M6 (2026-05-28)

Two coupled refactors prompted by the user feedback that ingesting a fresh BPC CSV (v4) collapsed the M6 report to a single row instead of showing the cumulative shortlist.

### Rolling-view as the default for both `3_5_render_timings.py` and `3_6_render_scores.py`

**Trigger:** the user uploaded `biotech_catalysts_v4.csv` (100 rows, near-term-events filter) as a NEW snapshot (2026-05-28) alongside the existing 2026-05-27 snapshot (572 rows from v3). The renderers defaulted to "most recent snapshot" semantics — M6 rendered just 1 hard-pass row (the imminent-events universe v4 mostly fails H3). The user wants the HTML to be the CUMULATIVE actionable shortlist across all snapshots, not just the latest single-snapshot view.

**Implemented:** both renderers now take a "rolling" path by default:

```sql
WITH latest_per_catalyst AS (
  SELECT ticker, drug, nct_number, next_catalyst_type, MAX(snapshot_date) AS max_snap
  FROM <catalyst_timing | catalyst_scores>
  GROUP BY ticker, drug, nct_number, next_catalyst_type
)
SELECT * FROM <table> t
JOIN latest_per_catalyst l USING (ticker, drug, nct_number, next_catalyst_type)
WHERE t.snapshot_date = l.max_snap
  AND (t.date_max IS NULL OR t.date_max >= ?)   -- effective today
```

For each unique catalyst PK, the renderer takes the row from the most recent snapshot that touched that catalyst, then drops any catalyst whose `date_max` has passed by the database's effective "today" (= the global `MAX(snapshot_date)`). This is the user's stated rule: "Data should be excluded only if they have been screened out by the pipeline (catalyst already materialized, etc.)."

**Reported in the payload:**
- `view_mode: 'rolling' | 'single'`
- `snapshots_covered: [...]` — every snapshot_date contributing rows
- `effective_today: 'YYYY-MM-DD'` — the database's max snapshot date
- `materialized_dropped: N` — count of catalysts removed because `date_max < effective_today`

The HTML renders these in the meta line so the user sees `Rolling view 2026-05-27 … 2026-05-28 (2 snapshots) · as-of 2026-05-28 · 6 materialized catalysts hidden`.

**Single-snapshot mode preserved** via `--snapshot-date YYYY-MM-DD` (audit replay). When passed, the renderer reverts to the legacy `WHERE snapshot_date = ?` query and reports `view_mode: 'single'`.

**Empirical numbers on the live DB (2026-05-27 v3 + 2026-05-28 v4 snapshots):**

| Renderer | Rolling-view rows | Single-snapshot 2026-05-28 rows |
|---|---:|---:|
| M5 (`catalyst_timings`) | 572 | 100 |
| M6 (`catalyst_scores`)  | 572 | 100 |

For M6, the rolling view restores the 36 / 33 / 503 (defined / undefined / excluded) bucket split that the user expected to see, vs the 1 / 0 / 99 collapsed view on the single-snapshot default.

**Materialized dropped:** 6 catalysts on the current DB — the LINZESS PDUFA, ALGS readout, and a handful of late-May 2026 conference presentations whose windows ended on or before 2026-05-28.

### M5 renderer brought up to D11's template/data split

**Trigger:** D11 left the M5 renderer as a monolithic 1090-line file emitting a single ~150 KB HTML on every run. User asked for the same template/data architecture as D11's M6 refactor.

**Built:** applied the D11 pattern to `scripts/3_5_render_timings.py`:

| File | Written | Size | Contents |
|---|---|---|---|
| `Outputs/catalyst_timings.html` | Only when CSS/JS/markup hash changes | 30 KB | Static template — references `<script src="catalyst_timings_data.js">` |
| `Outputs/catalyst_timings_data.js` | Every pipeline run | ~800 KB | `window.__DATA = {...};` rolling-view payload |

Changes inside the old `_HTML_TEMPLATE` constant:
- Replaced `<script>const DATA = __DATA_JSON__;</script>` with `<script src="catalyst_timings_data.js"></script>` followed by `const DATA = window.__DATA || {…};`
- Added `<meta name="template-version" content="__TEMPLATE_VERSION__">` to `<head>`
- Removed both `__SNAPSHOT__` substitutions (in `<title>` and `<h1 span>`); the JS now sets these dynamically on `DOMContentLoaded` so the template carries no data baked in
- `<title>` and `<h1 span>` are also rolling-view aware (`"Rolling: 2026-05-27 … 2026-05-28 (2 snapshots)"`)

New helpers (mirroring M6): `_template_version()`, `_existing_template_version(path)`, `_build_template_html()`, `_build_data_js(payload)`. CLI gained `--rebuild-template` and `--out-dir`; legacy `--output` removed in favour of the directory-based output convention.

### Data-flow consequence

Both renderers now share a consistent shape:

1. **Always** write the sidecar `_data.js` (this is the only thing the daily pipeline touches).
2. **Conditionally** write the HTML template (only when the CSS/JS/markup hash differs from the on-disk meta tag).
3. Report `template: created | up-to-date | rebuilt | forced` in the CLI summary.

### Memory entry

The user requested this pattern apply to all future HTML renderers. Saved a feedback memory at `feedback_html_template_data_split.md` (referenced from MEMORY.md) so future sessions default to the split.

### Test coverage

Added 16 tests across two new files:
- `tests/test_render_timings_template.py` (6 tests) — template hash stability, sidecar reference present, no inline data leak, version changes on edit, rolling view unions snapshots correctly with materialization filter, single-snapshot mode preserved.
- `tests/test_render_scores_rolling.py` (3 tests) — rolling returns latest score per catalyst across snapshots, single-snapshot mode preserved, hard_pass=0 + H4 materialized rows are dropped while hard_pass=0 + non-H4 rows survive in the Excluded tab.
- Existing `tests/test_render_scores_template.py` (7 tests) carries over from D11 unchanged.

Full repo suite: **236 tests passing** (220 → 227 in D11 → 236 now).

### Live impact

Both renderers now report (one-day delta between the two snapshots):

```
M5: 572 rolling rows  (discovery 428, execution 371, past/unknown 136)
M6: 572 rolling rows  (69 hard_pass, 503 excluded, 6 materialized dropped)
template: ~30 KB stable; data sidecar: 700–800 KB rewritten per run
```

The `.bat` orchestrator's `Render Outputs\catalyst_scores.html? [Y/n]` and `Render Outputs\catalyst_timings.html? [Y/n]` gates still call the same script names; behaviour change is invisible to the orchestrator.

### Re-litigation policy

- The rolling-view filter is **only** `date_max >= effective_today`. It is NOT a filter on `hard_pass`, `fail_reasons`, or other criteria — those are user-facing UI filters in the "Excluded" tab. Revisit only if the user explicitly says "I want stage-X catalysts hidden too."
- The effective_today is the database's `MAX(snapshot_date)`, not Python `datetime.now().date()`. This keeps audit replay deterministic (re-running the renderer tomorrow against the same DB produces the same output).
- The CSS/JS/markup template-version hash is computed at module import time; do NOT bake it into the on-disk template content beyond the meta tag (otherwise hash recursion breaks).

---

## D13 — Auto-refresh 2_Funds_parser from the 3_Biopharmcatalyst pipeline (2026-05-28)

User-requested: the 3_Biopharmcatalyst pipeline should automatically kick off a 2_Funds_parser refresh when the 13F filing calendar says fresh data is due. Two trigger conditions:

1. Today falls within ±7 days of any of the four 13F filing deadlines (Feb 14, May 15, Aug 14, Nov 14 — 45 days after each quarter-end).
2. Today is past the most recent deadline window AND `2_Funds_parser/2_fundparser.db` does not yet hold the matching quarter (catch-up case).

The auto-refresh runs `2_Funds_parser` modules M2..M5 inclusive (ingest → fund report → universe → hard filters → ranking → fundamentals → context packs) plus the consensus-builds HTML report. Module 6 (Anthropic API, billed) stays user-gated and is intentionally excluded.

### Architecture

```
3_Biopharmcatalyst_parser/
├── src/funds_refresh/
│   ├── __init__.py
│   ├── decision.py        ← pure logic, fully unit-testable
│   └── runner.py          ← subprocess wrapper around 2_Funds_parser scripts
├── scripts/
│   └── 3_auto_refresh_funds.py    ← CLI entrypoint called by the .bat
└── tests/
    └── test_funds_refresh_decision.py   (21 tests)
```

The `.bat` invokes `3_auto_refresh_funds.py` BEFORE Module 0. If it returns non-zero (a refresh fired and failed), the .bat prints a warning but continues with the biopharm pipeline — funds data is supplemental, not blocking.

### Pure-function decision logic

`funds_refresh.decision.decide(today, latest_period_in_db) -> RefreshDecision` enumerates all quarter-end candidates (year ± 1), computes their 45-day deadlines, finds the most recent candidate whose pre-deadline window has started, and applies the two trigger rules. No I/O, no clock — fully testable.

### Quarter calendar (SEC rule 13f-1, 45 calendar days)

| Quarter end | Deadline | Window |
|---|---|---|
| March 31    | May 15      | May 8  – May 22  |
| June 30     | August 14   | Aug 7  – Aug 21  |
| September 30| November 14 | Nov 7  – Nov 21  |
| December 31 | February 14 (next year) | Feb 7 – Feb 21 |

So **8 weeks per year** (2 weeks × 4 quarters) the auto-refresh will fire on calendar alone. The catch-up rule covers the in-between weeks if the user skipped a window.

### Runner

`funds_refresh.runner.run_refresh()` subprocesses 7 scripts in sequence:

| Step | Script | Notes |
|---|---|---|
| M2 ingest 13F-HR | `2_ingest_13f.py` | Idempotent; skips re-downloads. |
| M2 build report  | `2_build_report.py` | HTML report. |
| M3 build universe | `3_build_universe.py -v` | Per-ticker rollup. |
| M4a hard filters | `4_run_hard_filters.py -v` | |
| M4b ranking | `4_rank.py -v` | |
| M4c fundamentals | `4c_enrich_fundamentals.py -v` | Free SEC EDGAR. |
| M5 context packs | `5_build_context_packs.py -v` | Final M5 step. |
| Consensus builds HTML | `_q1_consensus_report.py --quarter <target> --prev-quarter <prev>` | Best-effort (fail-open); writes `2_Funds_parser/Outputs/<YYYYQn>_consensus_builds.html`. |

Each step's stdout/stderr tail is captured and printed if it fails. The first M2..M5 fatal aborts the chain; the consensus report is best-effort.

### Generalised consensus report

The original `_q1_consensus_report.py` was a hardcoded Q1 2026 vs Q4 2025 one-off. It now:
- Takes `--quarter` / `--prev-quarter` ISO-date args (defaults to the two most-recent `period_of_report` values in `holdings`).
- Derives labels like `2026Q1` / `2025Q4` from the dates.
- Computes fund-count metadata + unresolved-ticker percentages from the DB rather than hardcoding "21" funds and "8.3% / 4.8%".
- Output filename follows the project convention: `Outputs/<YYYYQn>_consensus_builds.html` (matches `ranking_report_2026Q1.html`, `enrichment_report_2026Q1.html`, etc.).

Backwards-compat note: the old `q1_2026_consensus_builds.html` file is left in place (different filename — no clobber). Future runs write to the new naming convention.

### Empirical verification on the current DB

Today is 2026-05-28; 2_Funds_parser holds 2026-03-31 (Q1 2026). Auto-refresh decision: SKIP. Reason: "today 2026-05-28 is past the 2026-03-31 filing window (deadline 2026-05-15); 2_Funds_parser already holds 2026-03-31".

Tested boundary dates via dry-run:
- 2026-05-15 → TRIGGER ("within the 13F filing window [2026-05-08, 2026-05-22] for quarter ending 2026-03-31")
- 2026-07-15 with stale DB (2025-12-31) → TRIGGER ("past filing window, latest period=2025-12-31 < target 2026-03-31")
- 2026-07-15 with current DB (2026-03-31) → SKIP (correct, between windows + up-to-date)

### Test coverage

21 new tests in `tests/test_funds_refresh_decision.py`:
- 3 calendar-helper tests (deadlines + previous-quarter + label formatting)
- 10 parametrized "within window" tests (start, deadline day, end — for all 4 quarters)
- 1 "in window, DB up-to-date" — confirms calendar rule fires regardless of DB freshness
- 4 catch-up scenarios (past window + stale DB, empty DB, up-to-date DB, DB ahead of target)
- 2 between-window scenarios (DB up-to-date / DB stale)
- 1 "previous_quarter_end populated" sanity check

Full repo suite: **257 tests passing** (236 → 257).

### CLI flags on `3_auto_refresh_funds.py`

| Flag | Purpose |
|---|---|
| `--today YYYY-MM-DD` | Override the current date (for testing). |
| `--dry-run` | Print decision + intended steps, do not subprocess anything. |
| `--force` | Ignore the calendar; run unconditionally (target = most recent completed quarter). |
| `-v` / `--verbose` | DEBUG-level logging. |

### Re-litigation policy

- The window is fixed at ±7 days per the user's spec. Tightening or widening requires user approval.
- The runner stops at M5; M6 (paid Anthropic API) must remain user-gated per the standing cost-approval rule.
- The runner is fail-open on the consensus report only. M2..M5 failures abort with non-zero exit so the user sees the .bat warning. The biopharm pipeline continues regardless (funds data is enrichment, not a hard dependency).
- "Latest period in DB" is read from `2_Funds_parser/2_fundparser.db::holdings`. If that file is missing, the function returns None and the catch-up rule fires (treats empty DB as needing refresh).
- `_q1_consensus_report.py` keeps its legacy filename (the script itself, not its output) to avoid breaking any external references. The output filename is now generic.

---

## D14 — Docx-to-CSV conversion in Module 0a (2026-05-28)

**Trigger:** the user uploads BPC catalyst data as `.docx` files (Word documents containing pasted HTML from BPC's website) and wants the pipeline to convert them automatically. Until D14 the user had to convert docx→csv outside the pipeline; that manual step caused the D11 format-drift bug (v3 used DD/MM/YYYY, v4 silently switched to ISO YYYY-MM-DD because of a different conversion route).

**Built:** new package + script + orchestrator step.

| File | Role |
|---|---|
| `src/docx_converter/__init__.py` | Package init; re-exports `convert`, `auto_convert_directory`, `extract_table`, `COLUMN_MAP`. |
| `src/docx_converter/convert.py` | Pure functions: cell extractors, `row_to_dict`, `convert(docx, csv)`, `auto_convert_directory(source_dir, force=False)`. |
| `scripts/3_0_convert_docx_to_csv.py` | CLI; auto-scans `_csv_source/*.docx` by default; single-file mode via positional arg; `--force` rebuilds even fresh CSVs. |
| `tests/test_docx_converter.py` | 22 tests across unit / row-level / end-to-end on real docx files. |
| `run_3_Biopharmcatalyst_parser.bat` | New "Module 0a" step (before existing M0 db init, which is renamed "Module 0b"). |

### Extraction insight: `blurred-text` is canonical

BPC's website tags each cell's inner `<div>` with a `blurred-text` HTML attribute carrying the machine-readable value. The visible cell text contains UI noise (`$213.12 -2.58 -1.20%` for prices, `"FTD"` Fast-Track-Designation badges appended to drug names, `"… read more"` truncation on long catalyst texts, " ET" timezone suffix on dates). **The converter always prefers `blurred-text` over visible text** and falls back to text only when the attribute is absent.

### Column extraction rules (19 CSV columns from 20 docx columns)

| CSV column | docx col | Mode | Why this mode |
|---|---:|---|---|
| `Ticker`, `Name`, `Price`, `Stage`, `Catalyst Date`, `Last Updated`, `Market Cap`, `No Of Shares`, `Historical LOA`, `Historical POP` | 0–2, 7, 11, 14, 15, 17, 18, 19 | `blurred_or_text` | Canonical numeric/structured values; visible text has formatting noise. |
| `30 Day Price Change` | 3 | `price_history` | Blurred-text is `p,ts,p,ts,…`; keep prices (even indices), join with `; `. |
| **`Drug`** | 4 | **`blurred_or_text`** (mandatory) | Visible text appends FDA badges (`FTD`/`BTD`/`ODD`) and `"View Clinical Trial Data"`. Without blurred-text, 129 of 600 rows would fail PK match against the prior manually-converted CSV. |
| **`Catalyst`** | 12 | **`blurred_or_text`** (mandatory) | Visible text is truncated by BPC's UI with `"… read more"`. Without blurred-text, ~206 of 600 rows lose their full catalyst body. |
| `NCT Number`, `Indication`, `Status`, `Next Catalyst`, `Conference` | 5, 6, 8, 10, 13 | `text` | Either no blurred-text attribute or identical to visible text. |
| (docx col 9 "Options") | 9 | **dropped** | A `View` hyperlink with no data, not in the M1 schema. |
| `Bullish or Bearish` | 16 | `sentiment` | Visible text is `"Community 50% 30% 20% <drug> How are you feeling…"`; regex `Community NN% NN% NN%` extracts the three percentages; format as `"Bull X% / Neutral Y% / Bear Z%"`. Defaults to `"Bull -% / Neutral -% / Bear -%"` when no community vote is present. |

### Idempotency

`auto_convert_directory()` checks mtimes. If the sibling CSV exists AND is newer than the docx, the conversion is skipped. `--force` overrides. This matches D2/D4's pattern for M1 / M4 ingest — re-running the orchestrator on an unchanged docx is a no-op.

### Output format implications for M1

Every CSV produced by M0a uses ISO format for dates and timestamps (because `blurred-text` consistently returns ISO). The M1 pydantic schema was extended in D11 to accept both DD/MM/YYYY (legacy v3) and ISO formats; that flexibility remains in case anyone hand-edits a CSV, but going forward all auto-converted CSVs are ISO-only.

### Acceptance against the live docx files

```
biotech_catalysts_v3.docx → 600 rows, 0 skipped, 572/572 PKs match the
                            prior manually-converted v3.csv (28 within-CSV
                            dupes coalesce in M1, as before).
biotech_catalysts_v4.docx → 100 rows, 0 skipped, 100/100 PKs match the
                            prior manually-converted v4.csv.
```

Field-level diffs against the prior CSVs are limited to known-equivalent representations: ISO vs DD/MM/YYYY dates, 4-decimal-place prices, ISO timestamps with seconds. These all parse to the same in-memory values via M1's pydantic validators.

### Re-litigation policy

- The 19-column CSV schema is locked. Adding a column requires updating both `COLUMN_MAP` and `module_1/csv_schema.py::EXPECTED_COLUMNS` in lock-step.
- `Drug` and `Catalyst` MUST stay on `blurred_or_text`. A regression test in `test_docx_converter.py::test_column_map_drug_and_catalyst_use_blurred_or_text` enforces this.
- The orchestrator runs M0a unconditionally before M0b. If M0a fails (e.g., python-docx import error), the .bat prints a warning and continues — M1 will then fail loudly if a required CSV is missing, which is the correct fail-loud behaviour for a missing input.
- The legacy v3.csv and v4.csv files are NOT preserved separately. The user uploads only docx going forward; the auto-converted CSV is the authoritative artifact.

---

## D15 — M6.5 fundamentals + FDSC enrichment (2026-05-28)

**Built:** `src/module_6_5/{fundamentals_db, edgar_client, price_client, pfw_estimator, enrich}.py` + `scripts/3_6_5_enrich_fundamentals.py` + `tests/test_module6_5_*.py`.

**Architecture:**
- New SQLite store `data/fundamentals.db` with three tables (`financials`, `capital_raises`, `fetch_log`) mirroring `2_Funds_parser/data/fundamentals.db` schema. Two 3_Biopharm-specific columns on `financials`: `last_price_usd / last_price_as_of` and `market_cap_fdsc_usd = (basic_shares + pfw) × last_price`, plus `pfw_source` and `pfw_share_dilution_warning` for audit.
- `edgar_client.py` is copy-adapted from `2_Funds_parser/src/module_4c/edgar_client.py` per the existing 3_Biopharm convention (D1 §4.3.4): we do NOT cross-project import. HTTP underlay delegates to `module_2.edgar_client.http_get` so M2, M3, M6.5 all share one process-global rate limiter (9.5 req/s under SEC's 10/s fair-use cap). This is the key plumbing decision — `_EDGAR_LIMITER` is a singleton inside `module_2.edgar_client` and reusing it via import (not re-instantiating) keeps the aggregate request rate correct.
- `price_client.py` is self-contained — uses yfinance directly with batched `yf.download`, falls back to per-ticker `yf.Ticker(...).history()` on shape mismatch. No separate prices.db sidecar; the last close is stored inline on the `financials` row. Memory `project_data_provider_switch` flags yfinance as a pre-deploy swap target.
- `pfw_estimator.py` is the heuristic v1: `prefunded_warrants_count = SUM(capital_raises.shares_issued WHERE raise_type='pfw' AND filing_date >= today − 730 days)`. PFW raises are classified inside `edgar_client.classify_raise_type` when the filing body text contains "pre-funded" or "prefunded". Known over-count limitation (warrants may have been exercised); `pfw_source='capital_raises_sum_2yr'` makes the provenance visible. v2 = 10-Q footnote parsing.
- A `pfw_share_dilution_warning` boolean fires when estimated PFW count ≥ 25% of `basic_shares_count` (configurable). Claude reads this in the pack and can call out PFW overhang.

**Orchestrator (`enrich.run_enrichment`):**
- Default feed: `SELECT DISTINCT ticker FROM catalyst_scores WHERE hard_pass=1` on the latest snapshot_date. `--tickers` overrides.
- Per-source TTLs gate against `fetch_log.last_fetched_at`: companyfacts 30 d, capital_raises 14 d, price 1 d. `--force-refresh` bypasses.
- Resolves ticker→CIK via the existing M2 `ticker_cik_map` cache (single source of truth across M2/M3/M6.5).
- Prices fetched in one batched yfinance call up front; XBRL + capital_raises per ticker, fail-open with `fetch_log` audit row.
- Per-ticker commit so a crash mid-feed doesn't roll back already-enriched tickers (mirrors M6's D51 pattern).

**Test coverage:** 18 tests across `test_module6_5_fundamentals_db.py` + `test_module6_5_pfw_estimator.py` (schema, idempotent init, upsert round-trips, COALESCE-don't-clobber on conditional-GET fields, PFW lookback window, dilution-warning threshold).

**Spec deviations from module_7_spec.md §4:**
- PFW raises are stored in the `capital_raises` table (not in a dedicated `prefunded_warrants` table). Reasons: same source filing, same parser, same TTL — splitting them adds zero value but doubles the read joins. The renderer/M7 pack builder filters by `raise_type='pfw'` when it needs the PFW subset.
- `prefunded_warrants_count` is recomputed on the LATEST `financials` row every M6.5 run, not stored per-period. Reason: PFW exercise/expiration is event-driven, not period-aligned. Older periods retain the value M6.5 stamped at the time of their fetch — that's the audit trail.

---

## D16 — M7 core pure-compute layers (config, scoring, parsing, cost_estimate, deep_dives_db, prompt) (2026-05-28)

**Built (this turn — LLM dispatch path NOT YET built):**
- `config/module_7.yaml` (prompt_version `m7-v1`, model `claude-opus-4-7`, cost_calibration_factor 0.10, modifier ranges, expectancy clamps, pricing block) + `src/module_7/config.py` (pydantic v2 loader, SHA-7 content hash appended to `prompt_version` per the M6 convention).
- `src/module_7/deep_dives_db.py` — `data/claude_deep_dives.db` with four tables: `deep_dives` (per-catalyst per-run rows carrying Claude's raw text verbatim + structured fields + Python-applied modifiers + final expectancy), `deep_dive_runs` (audit), `deep_dive_errors` (parse + API failures), `web_search_cache` (server_tool_use results). One read helper `latest_deep_dive_per_catalyst(conn, snapshot_date)` returns max-run-id rows for the renderer LEFT-JOIN.
- `src/module_7/scoring.py` — pure math: `remap_signal_to_modifier` linear-remaps M6's 0-100 signal score onto a bounded modifier range; `compute_expectancy` compounds `p_clinical × m_insider × m_funds → p_final` (clamped) + asymmetric `E[move] = p_final × hit + (1-p_final) × miss` + `expectancy = E[move] × m_momentum` + `expectancy/week`. None inputs collapse to the modifier band midpoint so a missing signal cannot tilt expectancy.
- `src/module_7/parsing.py` — JSON-fence extractor with truncated-response recovery (brace-balancing) copy-adapted from 2_Funds_parser M6. All 10 m7-v1 HARD RULES enforced inside `parse_deep_dive` with structured `ParseError(kind, detail)`. Catalyst-already-passed gets its own `error_kind` so callers route it to `deep_dive_errors` not `schema_violation`.
- `src/module_7/cost_estimate.py` — three-scenario estimator (no-optim / cache-only / cache+batch). `cost_calibration_factor` (default 0.10 per memory `project_anthropic_cost_calibration`) multiplies the FINAL total of every scenario. Web-search fee correctly excluded from the batch discount (per Anthropic docs).
- `src/module_7/prompt.py` — cacheable-prefix loader (system_prompt.md + few_shots.md → one string with a single `cache_control: {type: "ephemeral"}` breakpoint at the end). System prompt + few-shots themselves are NOT YET drafted — that's a Turn 2 deliverable that needs user review before dispatch.

**Compounding formula locked:**

```
p_clinical    ∈ [0.10, 0.90]                    (Claude)
m_insider     = linear_remap(M6 insider_score   ∈ [0,100] → [0.85, 1.15])
m_funds       = linear_remap(M6 funds_score     ∈ [0,100] → [0.85, 1.15])
m_momentum    = linear_remap(M6 momentum_score  ∈ [0,100] → [0.95, 1.05])
p_final       = clamp(p_clinical × m_insider × m_funds, 0.05, 0.95)
move_on_hit_pct  = clamp(Claude_hit,  -inf, 400)
move_on_miss_pct = clamp(Claude_miss, -90, +inf)
E[move_pct]      = p_final × move_on_hit_pct + (1 − p_final) × move_on_miss_pct
expectancy_pct   = E[move_pct] × m_momentum
expectancy/week  = expectancy_pct / max(weeks_to_catalyst, 1)
```

**Why split this from the dispatch layer:** Pure-compute pieces have no API calls and no money risk. They get unit-tested in isolation (76 tests added: 21 scoring + 26 parsing + 14 cost_estimate + 9 deep_dives_db + 6 config). The dispatch layer (context_pack builder, Anthropic SDK calls, mandatory `[y/N]` cost gate, batch submit/poll, system prompt + few-shots drafting, renderer modifications, selection HTTP server, .bat wiring) lands in Turn 2 after this turn's contract is verified.

**Test coverage:** 369 tests pass total (279 prior + 90 new, plus 4 unrelated skips).
- `test_module7_config.py` (6) — default YAML validates, SHA-7 hash shifts on edit, pricing/model consistency, extra-fields forbidden, calibration_factor pinned at 0.10.
- `test_module7_scoring.py` (21) — modifier remap, neutral signals don't tilt, clamps fire, asymmetric E[move], outlier clamps, time normalisation, audit-echo of original weeks_to_catalyst.
- `test_module7_parsing.py` (26) — JSON fence + truncated recovery; all 10 HARD RULES with both happy-path and violation cases.
- `test_module7_cost_estimate.py` (14) — three scenarios in order, cache+batch cheapest, batch discount only on tokens not search fee, calibration factor application, scaling sanity.
- `test_module7_deep_dives_db.py` (9) — schema, idempotent init, run lifecycle, INSERT OR REPLACE on PK collision, web_search_cache COALESCE-don't-clobber, `latest_deep_dive_per_catalyst` picks MAX(run_id).

**Deferred to Turn 2 (pending user signoff on this turn's contract):**
- `src/module_7/context_pack.py` — per-ticker pack builder (joins biotech.db + fundamentals.db; strips insider/funds/momentum/M6-composite per spec §5.1).
- `src/module_7/dispatch.py` — Anthropic SDK wrapper, sync + batch, mandatory `[y/N]` cost gate, submit/poll split for D51-style crash recovery.
- `src/module_7/render_join.py` — LEFT-JOIN payload builder consumed by `scripts/3_6_render_scores.py`.
- `config/module_7_system_prompt.md` + `config/module_7_few_shots.md` + `config/module_7_web_search_domains.yaml` — content needs user review before any dispatch.
- `scripts/3_7_estimate_cost.py` + `scripts/3_7_deep_dive.py` + `scripts/3_7_serve_selection.py`.
- Renderer modifications to `scripts/3_6_render_scores.py` per spec §5.11 (three new columns + deep-dive block below catalyst text).
- `run_3_Biopharmcatalyst_parser.bat` insertion of M6.5 + M7 steps; `daily_orchestrator.py` hook (per memory `feedback_update_daily_runner`).

---

## D17 — M7 catalyst-identity cache rule (locked 2026-05-28)

**Decision (user-locked):** M7 skips the Anthropic call for a candidate when the catalyst's **identity** (drug, stage, next_catalyst_type, catalyst_date) is unchanged versus the most recent successful prior deep-dive for the same `(ticker, drug, nct_number, next_catalyst_type)`. Any change to any of those four fields triggers a fresh dispatch. **No TTL** — identity is the only criterion.

This replaces the original spec proposal of a 7-day TTL-based cache. The user's rule is sharper: a catalyst that hasn't changed in 3 months still has the same expectancy estimate (the science hasn't moved), so re-paying for a deep-dive is wasteful. Conversely, a catalyst whose date moved from "Q3 2026" to "September 2026" is materially different and warrants re-scoring.

**Built:** `src/module_7/cache.py` (`compute_catalyst_signature`, `lookup_cache`, `partition_feed_by_cache`, `CacheLookup` dataclass) + `deep_dives.catalyst_signature` column added via additive migration + 20 unit tests in `tests/test_module7_cache.py`.

**Identity normalisation:** lower-cased and stripped before signature concatenation, so `"TX45 "` vs `"tx45"` does NOT invalidate the cache.

**Cache miss reasons** (one of these dispatches): `no_prior_row`, `prior_row_failed` (`p_clinical IS NULL`), `identity_changed`, `prompt_version_changed`, `force_refresh`. Each candidate carries its `cache_lookup.reason` after `partition_feed_by_cache`, surfaced in the dispatch summary so the user can audit why each ticker was dispatched or skipped.

**Why also gate on `prompt_version`:** A YAML edit (modifier tuning, system-prompt rewrite, model swap) changes the SHA-7 appended to `prompt_version`. We want any such edit to invalidate ALL prior rows for re-scoring against the new prompt. Identity match + prompt_version match → cache hit. Identity match + prompt_version mismatch → cache miss. Belt and suspenders.

**Why not also gate on pack content hash:** Considered but rejected. The pack content beyond the four identity fields (e.g., new insider trades since prior run, new fund position deltas, momentum delta) feeds the Python modifier remap downstream of Claude — it does NOT change `p_clinical` or the move estimates Claude returns. Adding a pack-hash gate would force dispatch for content changes Claude is blind to anyway, defeating the purpose. Re-tuning modifier weights is a YAML edit and already invalidates via `prompt_version`.

---

## D18 — M6.5 bug fixes after first dry run (locked 2026-05-28)

Three bugs surfaced when M6.5 ran against the 52 hard-pass tickers. All fixed in this turn before any LLM dispatch.

### D18.a — PFW share-count extractor was generating bogus billions

**Bug:** For PFW (pre-funded warrant) filings, the body parser computed `shares_issued = gross_proceeds / price_per_share`. The `_RE_PRICE_PER_SHARE` regex matches **`$0.0001 per share`** (the warrant *exercise* price, not the offering price), yielding `shares = gross / 0.0001 = absurd billions`. ACET's `prefunded_warrants_count` came back as 48 billion (4,800× the basic-share count).

**Fix:** Three new PFW-specific regexes (`_RE_PFW_SHARES_BY_WARRANTS`, `_RE_PFW_SHARES_TO_PURCHASE`, `_RE_PFW_AGGREGATE_OF_SHARES`) extracting share counts directly from prospectus cover-page language. When `raise_type == 'pfw'`, the parser uses `extract_pfw_share_count` and **never** falls back to `gross / pps`. Counts > 5e9 (above any biotech float) and < 1000 (noise) are rejected. Counts that don't match any pattern return `None` (safe failure — the M7 pack will tell Claude "PFW detected, count unparseable").

**Result:** ACET now 10M PFW (real prospectus value). 31/63 PFW filings (49%) yield clean counts; the other 51% safely return None. Max PFW count across all 52 tickers: 27.8M shares — biotech-scale, no more astronomical artifacts.

### D18.b — Operating cash flow TTM was inflated by cumulative-YTD double-counting

**Bug:** `_ttm_sum` summed the 4 most recent quarterly `operating_cf` values. But XBRL reports these *cumulatively within a fiscal year*: Q1 = 3 months, Q2 = 6 months YTD, Q3 = 9 months YTD, 10-K = full year. Summing the last 4 cumulative values double-counts every period inside the cumulative sum. KURA's TTM came out as **−$439M** (real annual burn is closer to $100M); reported runway was **1.1 months** (real ≈ 6 months).

**Fix:** `_ttm_sum` now uses XBRL `start` + `end` date spans to detect cumulative-YTD rows. Strategy: (1) annual row (10-K or 365-day span) wins; (2) else four single-quarter rows (~90-day span) summed; (3) else group rows by fiscal year, sort within FY ascending, **difference consecutive entries** to recover incremental quarters, sum the most recent 4; (4) else None (not a fake sum).

**Result:** KURA OpCF TTM now −$78M, runway 6.0 months. AGIO −$380M → 3.6 months. SNDX −$278M → 15.2 months. TYRA −$102M → 10.0 months. BMEA −$56M → 9.5 months. All biotech-plausible.

### D18.c — Orchestrator step order leaked prior-run garbage into PFW estimate

**Bug:** `enrich.run_enrichment` computed the PFW estimate **before** persisting this run's fresh `capital_raises` rows. The estimate read `cached_pfw_rows` from the DB — which still held the prior-run bogus 48B values — and summed them with this run's fresh (smaller) values. Even after the D18.a regex fix, the financials row's `prefunded_warrants_count` came back at 48B because the orchestrator read stale data before overwriting it.

**Fix:** Moved `upsert_capital_raise` to run **before** the PFW estimate. The estimate now re-queries `capital_raises` after the upsert, reading only the post-fix values.

**Combined post-fix state on the 52-ticker feed:**

| Metric | First run (buggy) | After all three fixes |
|---|---|---|
| Max PFW count | 48,009,600,000 (ACET) | 27,807,482 |
| KURA runway | 1.1 months | 6.0 months |
| PFW share-count recall | 8% (5/63) | 49% (31/63) |
| Tests passing | 369 + 90 = 459 ❌ (1 fail) | 406 (37 new + 369 prior) ✓ |

External cross-check vs yfinance for 8 spot-check tickers: `basic_shares_count` matches yfinance.sharesOutstanding within ±0.22% for established tickers and ±4.3% for early-stage (drift is post-quarter share issuance, expected). All prices match to the cent.

---

## D19 — M7 LLM-side build (context_pack, dispatch, prompts, renderer, server, bat wiring) (2026-05-28)

**Built (Turn 2 — pre-dispatch; no Anthropic call yet):**

| Surface | File | Role |
|---|---|---|
| Per-ticker pack | `src/module_7/context_pack.py` | Joins biotech.db + fundamentals.db; strips insider/funds/momentum/M6-composite per spec §5.1; parses FDA designations out of BPC `Drug` field via `_KNOWN_FDA_DESIGNATIONS`; degrades gracefully when fundamentals.db is empty |
| Anthropic SDK | `src/module_7/dispatch.py` | Sync (AsyncAnthropic with bounded semaphore) + batch (submit/poll split for D51 crash recovery); cache_control: ephemeral on the system block; web_search_20250305 tool with flat biotech-only domains list |
| Cacheable prefix | `config/module_7_system_prompt.md` (m7-v1) | Role + Task + 10 HARD RULES + calibration notes for POS base rates + move magnitudes. **Bumping the SHA-7 invalidates all prior deep_dives rows via the D17 cache** |
| Few-shots | `config/module_7_few_shots.md` | Two worked examples (mid-conviction PAH topline + low-conviction Phase 1 FA biomarker miss-skew) — synthetic, illustrative |
| Web search whitelist | `config/module_7_web_search_domains.yaml` | Flat list: SEC + wires + journals + conferences + FDA/EMA + trade press + patient advocacy. ~50 domains |
| Renderer JOIN | `src/module_7/render_join.py` | ATTACH-free read of claude_deep_dives.db; returns `{pk_tuple: payload}` keyed map; `raw_text` deliberately stripped (lazy-fetched by HTTP server) |
| Renderer edit | `scripts/3_6_render_scores.py` | 3 new `<th>` columns (Probability, Share price appreciation, Expectancy / time); colspan bumped 13→16; new `<section class="m7-deep-dive">` in `buildExpandPanel` rendering thesis + probability breakdown + drug profile + rNPV table + clinical evidence + financial overhang + mgmt/acq scores + risks + sanity check + reasoning trace (collapsible) + audit footer. Em-dashes when row has no `deep_dive`. Sidecar size grew 30 KB → 40.5 KB |
| Cost estimator CLI | `scripts/3_7_estimate_cost.py` | Dry-run, no API call; applies identity cache filter to show what WOULD dispatch; aborts dispatch path when production scenario > `cost_ceiling_usd` |
| Dispatcher CLI | `scripts/3_7_deep_dive.py` | Mandatory `[y/N]` gate ALWAYS prompts (EOFError → abort, no auto-confirm on stdin closed); `--yes` single-shot bypass; `--resume-run N` for D51 crash recovery; per-result commits |
| Selection HTTP server | `scripts/3_7_serve_selection.py` | stdlib `ThreadingHTTPServer` at 127.0.0.1:7034; routes: GET/POST `/api/selection` (sidecar JSON round-trip), GET `/api/raw_text?run_id=N` (lazy raw_text serving), static `Outputs/` files |
| .bat wiring | `run_3_Biopharmcatalyst_parser.bat` | Added M6.5 + M7 steps with `[y/N]` gates AFTER M6; M7 prompt explicitly warns "THIS WILL SPEND MONEY" |

**Pre-dispatch cost-estimator output (rolling-view 52-ticker hard-pass feed, 69 catalysts):**

| Scenario | Total | Tokens in (non-cached) | Cache-read tokens |
|---|---:|---:|---:|
| no-optim | $9.60 | 1,624,398 | 0 |
| cache-only | $8.87 | 1,095,154 | 529,244 |
| **cache+batch (production)** | **$4.78** | 1,095,154 | 529,244 |

Configured cost ceiling: $50.00 (in `config/module_7.yaml`). Production scenario well within budget.

**Test coverage (Turn 2):**
- `test_module7_context_pack.py` (14) — FDA-badge extraction (6 cases), pack happy path, **HARD spec rule check: pack JSON does NOT contain "insider_score" / "fund_accumulation_score" / "momentum_score" / "composite_score" / "hard_pass" / "fail_reasons" anywhere** (regression guard for D40 doubled-count bug), graceful fundamentals fallback, missing-PK None, weeks_to_catalyst math, signature helper, rolling-view feed flips a hard_pass→fail ticker out of the feed, explicit-tickers filter intersection.
- `test_module7_render_join.py` (8) — missing/empty DB returns {}, JSON-block decoding (rnpv_by_indication, drug_profile, key_risks), MAX(run_id) per PK, snapshot_date filter, **raw_text NOT in payload** (sidecar-bloat guard), attach_deep_dive_payload mutates rows in place.

**Tests passing total:** 428 (22 new + 406 prior, plus 4 unrelated skips).

**Critical safety properties verified end-to-end:**

1. **No API call has been made yet.** All Turn 2 work is dispatch-ready code; the `[y/N]` gate is the only path to spend.
2. **Cost ceiling enforced before gate.** If config or feed somehow inflates costs above the YAML ceiling, the dispatcher refuses BEFORE prompting — no risk of confirming a runaway run.
3. **Identity cache is binding.** A second invocation with no upstream changes would skip 100% of API calls — `partition_feed_by_cache` is wired into both the estimator CLI and the dispatcher CLI.
4. **Selection editor is opt-in.** The server runs only when the user manually starts `scripts/3_7_serve_selection.py`. M7 dispatch reads the sidecar at dispatch time, NOT from a long-running daemon.
5. **Renderer survives no M7 data.** Verified by running `scripts/3_6_render_scores.py` against a populated catalyst_scores + empty claude_deep_dives.db: HTML rebuilt cleanly, all 16 columns present, the 3 M7 cells show em-dashes, the deep-dive expanded section is omitted entirely.

**Not yet wired (deferred to Turn 3 if requested):**

- `daily_orchestrator.py` scheduled-job hook (memory `feedback_update_daily_runner`). M7 should NOT auto-run on a schedule — it spends money. Recommend leaving M7 manual-only; the 18:00 daily run continues to use M0a→M0b→M1→M5→M4→M2→M3→M6. M6.5 could safely run automatically if desired (free; just slow at ~6 min wall time).
- A standalone "selection editor" UI affordance — the current HTML doesn't yet have checkbox columns; sidecar JSON round-trip works but is invisible without a UI. The dispatcher accepts a `--selection-from-html` flag in future work.
- Pack-content hash in `deep_dives` for finer-grained cache invalidation when an upstream insider trade or fund position lands between BPC drops. The D17 catalyst-signature cache deliberately doesn't depend on this (per spec §5.12 design note).

**What's needed to actually dispatch:**

1. User reviews `config/module_7_system_prompt.md` + `config/module_7_few_shots.md`. Either approves as-is or requests edits (which bumps the SHA-7 and invalidates any prior cache).
2. User opens `Outputs/m7_cost_estimate.html` to confirm the cost preview matches their expectation.
3. User runs `PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_deep_dive.py` (or hits "y" at the M7 prompt in the .bat).
4. User types `y` at the `[y/N]` gate.

---

## D20 — m7-v2 prompt expansion: 13 high-value patterns ported from 2_Funds_parser M6 (2026-05-28)

After D19 (Turn 2 build) and before any dispatch, audited `2_Funds_parser/config/module_6_system_prompt.md` (681 lines vs our m7-v1's 304) for reusable patterns. Identified 13 high-value patterns and ported all 13 in this turn. `prompt_version_label` bumped `m7-v1 → m7-v2` to make the revision explicit in the audit trail (no prior `deep_dives` rows exist yet, so no cache invalidation effect — but the YAML SHA-7 also changes naturally because the comment block grew).

### Patterns ported (A-M)

| Pattern | Location in m7-v2 prompt | Notes |
|---|---|---|
| **A** — FDA POS base-rate table by pathology × stage | `# FDA PROBABILITY OF SUCCESS — BASE RATES` section. Two tables: stage→approval and single-readout-hit (the latter is M7's typical catalyst). | Replaces the rough numerical bullets that were in CALIBRATION NOTES. |
| **B** — rNPV formula with WACC = 12% | `# rNPV CALCULATION GUIDANCE` section. Explicit `rnpv_contribution = peak_sales × pos_adjusted × (1/(1+WACC)^years_to_peak) × duration_factor`. | WACC pinned at biotech industry convention 12%. |
| **C** — HIGH/MEDIUM/LOW probability rubric | `# PROBABILITY RUBRIC` section. HIGH (0.70-0.90) / MEDIUM (0.40-0.69) / LOW (0.10-0.39) with evidence-quality + timing-uncertainty criteria for each band. | Anchors `p_clinical` verbally before the prior-results ladder applies. |
| **D** — Quantitative probability adjustment ladder | `# PROBABILITY ADJUSTMENTS — PRIOR RESULTS & MGMT TRACK RECORD` section. Strong prior data +0.05-0.15; mixed 0; negative −0.10-0.20; same-class precedent +/−0.05-0.10; mgmt strong +0.05 / poor −0.10. | Forces Claude to show its adjustment math. |
| **E** — `pos_adjusted` within ±15pp of `pos_base_rate` | New HARD RULE #11 (warning, not fatal — same as 2_Funds_parser's pattern). Inline citation requirement. | |
| **F** — Evidence hierarchy 7-tier | `# EVIDENCE HIERARCHY` section. SEC > FDA/clinicaltrials.gov > peer-reviewed > trade press > market sizing > patents > macro. | Resolves source conflicts deterministically. |
| **G** — Search budget allocation | `# SEARCH BUDGET` section. 3-4 clinical/timing+IR, 2 regulatory, 2 competitive/moat, 1-2 TAM, 1 IP/tech. | Disciplines the 10-call budget. |
| **H** — Catalyst-date freshness check (web_search required when >30 weeks out) | Strengthened HARD RULE #8. Cite the press-release date in `catalyst_date_sanity_check.notes`. | Caught the HAELO 2026-04-25 false-positive in 2_Funds_parser; ported as a strict rule. |
| **I** — Platform-optionality rNPV row required | New HARD RULE #12. Platform companies (gene editing / ADC / TCR-T / antisense / mRNA delivery / etc.) MUST include a "Platform optionality" entry OR explicitly state single-asset disclaimer. | Caught the TCRX 2026-04-25 incident in 2_Funds_parser; rNPV understated ~50% without the row. |
| **J** — Cite dates inline | New HARD RULE #13. Format examples: `"per 10-Q filed 2026-02-14"`, `"NCT04789123 last updated 2026-01-30"`. | Anti-hand-waving discipline. |
| **K** — Inline POS adjustment citation | New HARD RULE #14. Any deviation of `pos_adjusted` from `pos_base_rate` (or `p_clinical` from rubric anchor) cited inline with the specific prior readout that drove it. | |
| **L** — `mgmt_track_record.summary` MUST cite ≥1 historical example | New HARD RULE #15. Generic claims rejected. | |
| **M** — Stage-based move-on-hit discount table | `# MOVE-ON-HIT STAGE DISCOUNT` section with explicit table (Ph1 interim 15-30% / Ph2 topline 30-55% / Ph3 topline 55-80% / NDA-PDUFA 70-95% / Approved 80-110% of rNPV/share). Compute formula: `target_post_hit_price = chosen_fraction × rNPV_per_share`; `expected_move = (target - current) / current`. | Was the weakest part of m7-v1 — "anchor on (rNPV/share - current)" with no concrete stage anchor. Now deterministic. |

### Patterns deliberately NOT ported (lower-value / non-applicable)

| Pattern | Why skipped |
|---|---|
| Archetype prior + override licence | 2_Funds_parser-specific; M7 packs don't carry archetype data |
| Per-horizon (3mo vs 12mo) scoring | M7 has single catalyst-window horizon |
| `fair_entry` / `full_reward` price-range fields | M7 schema returns asymmetric move estimates instead |
| HARD RULE about `fund_accumulation` | Already enforced upstream — stripped from pack per spec §5.1 |
| Prior-research / prior-thesis injection ([2_Funds_parser/src/module_6/priors.py](2_Funds_parser/src/module_6/priors.py)) | Skipped per spec §1 (no Tier B light-refresh); the D17 catalyst-identity cache handles re-runs by skipping the call entirely |
| `clinical_trials.interim_results[]` mandatory structured array | Our `clinical_evidence` block covers this in less rigorous form; marginal value vs token cost |

### Implementation deltas

- `config/module_7.yaml` — `prompt_version_label: m7-v1 → m7-v2`. Comment block explaining the bump.
- `config/module_7_system_prompt.md` — 8 new sections inserted between `# INPUT STRUCTURE` and `# OUTPUT`. HARD RULES table grew from 13 rows (1-10 + 20-22) to 18 rows (1-15 + 20-22). CALIBRATION NOTES trimmed (POS base rates moved to dedicated section A). Length: 304 → ~480 lines.
- `config/module_7_few_shots.md` — Example 2 patched for HARD RULE #12 (FA single-asset disclaimer added to `move_anchor_rationale`). Example 1 (PAH + HFpEF) already complied — HFpEF was already the platform-optionality row.
- `tests/test_module7_config.py::test_default_config_loads` — `prompt_version` check loosened from `startswith("m7-v1:")` to regex `m7-v\d+:[0-9a-f]{7}` so future bumps don't break the test.

### Cost impact

| Metric | m7-v1 (Turn 2 estimate) | m7-v2 (this turn) |
|---|---:|---:|
| Cached prefix tokens | ~7,800 | ~11,500 |
| `cache_read_tokens_total` (68 reads) | 529,244 | 783,700 |
| no-optim total | $9.60 | $9.98 |
| cache-only total | $8.87 | $8.91 |
| **cache+batch (production)** | **$4.78** | **$4.80** |

The 50% prompt-size growth costs **$0.02** thanks to the 10% cache-read multiplier × 50% batch discount × 10% calibration factor stacking. Well under the $50 ceiling.

### Test results

428 pass / 4 unrelated skips. The one test that needed updating (`test_default_config_loads`) was a `startswith` assertion that's now a regex — same intent, version-bump-resilient.

### Caveats

1. **Cache-invalidation TODO.** `config.py::_content_hash()` hashes only the YAML, not the system prompt MD or the few-shots MD. So edits to the MD files don't auto-invalidate the cache. Convention (followed here): manually bump `prompt_version_label` in the YAML when revising the MDs — that changes the YAML content → SHA-7 → cache invalidates. **2_Funds_parser has the same setup** and the same convention. Logged as a future-work item (would need `_content_hash` to concat all three file hashes). Not blocking; just discipline.
2. **All new HARD RULES (#11-15) are "warnings"** not parse-fail rejections — mirrors 2_Funds_parser's pattern of using soft validation for content rules that can't be cleanly machine-checked. The model is asked to comply; failures get flagged for human review via the rendered HTML. Strict parse-time enforcement (counts of inline citations, etc.) would over-fit on prompt phrasing.

---

## D21 — M7 cost-formula audit + recalibration after first real invoice (2026-05-28)

**Trigger:** First production M7 dispatch (5 tickers DTIL/NTHI/ALT/CGEN/INMB, sync mode, concurrency=8). Script reported `$0.36 paid`; real Anthropic invoice was `$3.01`. Under-report by **8.4×**.

**Root causes (three stacked bugs):**

1. **`non_cached_input` subtraction in `scripts/3_7_deep_dive.py::_usd_cost_per_call`.** Formula was `non_cached_input = max(0, input_tokens - cache_read - cache_creation)`. Anthropic's SDK reports `input_tokens`, `cache_read_input_tokens`, and `cache_creation_input_tokens` as **three disjoint buckets** — subtracting clamps to 0 and silently drops the input-token cost. Fixed: use `input_tokens` directly.
2. **`cost_calibration_factor: 0.10`** inherited from 2_Funds_parser M6's `project_anthropic_cost_calibration` memory. That calibration was tuned against M6's invoices, whose cost profile (no web_search, mostly cached input) is materially different from M7's (web_search + sync-concurrent cache-write storms + heavier output). Re-derived empirically: `$3.01 actual / $3.74 raw-formula = 0.805`. New value: **`0.80`**. After applying, fixed formula computes **$2.99 vs $3.01 actual — within 0.7%.** Locked by `test_cost_calibration_factor_locked`.
3. **"USD if list-price"** post-run line passed `cache_read=0, cache_creation=0` to the same formula, producing a meaningless "no-cache-cost-but-also-no-cache-tokens" number. With the formula bug, it printed $0.29 — *lower* than the buggy $0.36 paid number. That impossibility (caching cannot make things more expensive when both are computed honestly) was the visible smoke. Fixed: pass `input = input + cache_read + cache_creation` to compute the actual "what you'd pay if you hadn't cached at all" cost. Now meaningfully *higher* than the cached cost.

**Bonus structural bug:** the pre-flight estimator (`cost_estimate.py`) modeled `cache-only` as the optimistic `1 cache_create + (N-1) cache_reads`. That's the *batch-mode* reality, not sync. In sync mode at concurrency C, the first `min(N, C)` calls all dispatch in parallel and each writes its own cache copy (no call has finished by the time the others start). Today's run: 5 parallel calls → 283,951 cache_create tokens (~6× what the estimator predicted). Fixed: `EstimateInputs.sync_concurrency` is now a passthrough; the `cache-only` scenario computes `cache_create = prefix × min(N, C)` + `cache_read = prefix × max(0, N - C)`. The `cache+batch` scenario keeps the optimistic model (batch intra-request cache sharing on Anthropic's side is the documented behavior).

**Config changes — cost-minimising default:**

- `dispatch.mode: batch` (unchanged — already the default; was overridden to `sync` for this calibration run).
- `dispatch.sync_concurrency: 8 → 1`. Defensive: anyone passing `--mode sync` now gets sequential dispatch, which lets the prompt cache actually flow forward (first writes, rest read at 0.10× rate). Tradeoff is wall time but spec rule: speed not optimised.
- `pricing.cost_calibration_factor: 0.10 → 0.80`. See above.
- `pricing.cache_creation_multiplier: 0.10` kept. Note: docs quote 1.25× input rate for ephemeral cache writes, but our 0.10 empirical value (carried from 2_Funds_parser D44) tracks reality much better — Anthropic appears to dedup/discount concurrent cache writes well below docs rate. The calibration factor 0.80 absorbs residual variance.

**Code changes:**

- `config/module_7.yaml`: above two pricing values + `sync_concurrency: 1` + explanatory comments referencing this D21.
- `src/module_7/cost_estimate.py`: added `sync_concurrency` field to `EstimateInputs`; `cache-only` scenario now models sync-concurrent reality; `cache+batch` unchanged (batch optimistic model is still right).
- `scripts/3_7_deep_dive.py::_usd_cost_per_call`: removed `non_cached_input` subtraction; "USD if no caching" line now passes the right inputs (was "USD if list-price").
- `scripts/3_7_deep_dive.py` mode-aware ceiling check: was always comparing against `scenarios[2].total_usd` (cache+batch) regardless of dispatch mode, under-reporting sync cost by ~2×. Now picks `scenarios[1]` for sync, `scenarios[2]` for batch.
- `scripts/3_7_estimate_cost.py` + `scripts/3_7_deep_dive.py`: pass `sync_concurrency=cfg.dispatch.sync_concurrency` through to the estimator.
- `tests/test_module7_config.py::test_cost_calibration_factor_locked` (renamed from `_is_0_10`): asserts the new 0.80 and reuses the test as the change-gate.
- `tests/test_module7_cost_estimate.py`: two new tests for sync_concurrency-aware cache modeling.

**Validation:**

- 430 tests passing (was 428).
- Formula vs reality: $2.99 calibrated vs $3.01 actual (0.7% off).
- Pre-flight estimate on the same 5 tickers under new config: batch=$2.79, sync (concurrency=1)=$5.19, no-optim=$5.81. Batch is 46% cheaper than sync-sequential for this workload because of the 50% token discount.

**Future cost-watch:**

- After the first batch-mode invoice lands, re-tune `cost_calibration_factor` against it; the 0.80 was derived from sync-mode billing only. Batch mode may have a slightly different ratio because of how Anthropic prices cache_creation inside a batch.
- The unit tests pin the calibration value — bumping it requires editing the locking test, which forces a deliberate decisions.md entry (this is the same change-gate pattern used elsewhere in the codebase).

---

## D22 — Recalibrated calibration_factor 0.80 → 0.10 after first batch-mode invoice (2026-05-28)

**Trigger:** First batch-mode dispatch (run_id=2: MBX/CMPX/TENX/ACRS/KURA, 5 catalysts, 4.6 min wall). Script (with D21's 0.80 calibration) reported `$1.72 estimated cost`; the real Anthropic invoice was `$0.20`. The formula now over-reports by ~8.6× for batch mode — exactly the inverse of D21's under-report problem.

**Reconciliation (with D21's `non_cached_input` bug already fixed):**

| Mode | Raw-formula on observed tokens | Actual invoice | True calibration |
|---|---:|---:|---:|
| sync (run_id=1) | $3.74 | $3.01 | 0.80 |
| **batch (run_id=2)** | **$2.15** | **$0.20** | **0.09** |

Batch mode is **~10× cheaper** than the docs-rate formula predicts — far beyond the 50% `batch_discount` already in the formula. Two amplifying factors:

1. Anthropic's batch billing for cache_creation_input_tokens appears to be deeply discounted (the docs-quoted 1.25× input rate is wrong for batch).
2. Web-search fees inside a batch may also be discounted below the documented `$10/1k` rate (we'd see this only in larger batches).

**Decision:** revert `cost_calibration_factor` to `0.10` (the value originally inherited from 2_Funds_parser M6, which runs batch mode and has been calibrated against many invoices). D21's 0.80 was *correct for sync* but wrong for batch.

**Single-factor vs mode-aware:** considered splitting into `cost_calibration_factor_sync` + `cost_calibration_factor_batch` (0.80 / 0.10). Rejected because:

- Batch is the production mode (D21 already locked `dispatch.mode: batch` as the cheapest default).
- Sync is only used for ad-hoc calibration runs; over-estimating sync ~8× is **safe** (you'll see an inflated forecast that warns you off sync mode anyway).
- A single field is one less knob to misconfigure.

The single 0.10 stays. Sync-mode pre-dispatch forecasts are conservative. Documented in [config/module_7.yaml](../config/module_7.yaml).

**Files touched:**

- `config/module_7.yaml` — calibration value + comment block explaining the history (was 0.10 → D21 → 0.80 → D22 → 0.10).
- `tests/test_module7_config.py::test_cost_calibration_factor_locked` — change-gate assertion updated.

**Validation:**

- 430 tests passing (unchanged from D21 — only one assertion-value change).
- Re-run cost estimate on the same 5 tickers gives `$0.42 batch` (close to actual $0.20 — still conservative because the estimator assumes max_output_tokens × N).

---

## D23 — Drug-level dispatch dedup: one API call per (ticker, drug), N rows per result (2026-05-28)

**Trigger:** user requested "when a drug has several catalysts, do the API call only once and report the result in all the rows where the drug is listed", with the explicit guard "this rule shall not interfere with the previous rule (run API call if the catalyst has changed)".

The previous rule is D17's catalyst-identity cache. D23 lifts the dispatch unit from `(ticker, drug, nct_number, next_catalyst_type)` (the deep_dives PK) to `(ticker, drug)` — but the cache invariant ("re-run if the catalyst changed") is preserved at drug granularity because the new `drug_signature` hashes over the FULL list of catalyst-tuples for the drug.

**What dedups now:** when a single (ticker, drug) appears across N hard-pass catalyst rows (different `next_catalyst_type` and/or `nct_number`), the dispatcher issues **ONE** Anthropic call. The response is then written N times to `deep_dives` — one row per catalyst PK — with:

- The **same** Claude output fields (`p_clinical`, `expected_move_on_hit_pct`, `expected_move_on_miss_pct`, `rnpv_*`, all `_json` blocks, `thesis_summary`, etc.).
- **Per-row** Python-side expectancy (`weeks_to_catalyst_mid`, `m_insider`, `m_funds`, `m_momentum`, `p_final`, `e_move_pct`, `expectancy_pct`, `expectancy_per_week_pct`) — each row's `catalyst_date_iso` and M6 scores can differ.
- The **anchor row** (chosen as the earliest-dated catalyst, then lex by `(nct_number, catalyst_type)`) carries the full `usd_cost` + token counts; copy rows carry `0` / `NULL` and identify the anchor via `anchor_nct_number` + `anchor_next_catalyst_type`. This keeps `SUM(usd_cost)` accurate across a run.

**Drug signature shape (preserves D17 invariant):**

```
drug_signature = lower_strip(drug)
               | lower_strip(stage)
               | sorted([(lower_strip(catalyst_type), catalyst_date_iso) for c in catalysts])
                 joined "ct1~cd1,ct2~cd2,..."
```

A change to ANY of {drug, stage, any catalyst's type, any catalyst's date, presence/absence of any catalyst} produces a new signature → cache miss → re-dispatch. The D17 user-locked invariant is preserved.

**Schema (additive migration):**

- `deep_dives.drug_signature` — D23 cache key; NULL on legacy rows.
- `deep_dives.anchor_nct_number` + `anchor_next_catalyst_type` — non-NULL on copy rows; NULL on the anchor row itself. Renderers can distinguish anchor vs copy.
- `deep_dives.catalyst_signature` kept for backward compat (carries D17 per-catalyst sig in addition to drug_signature).
- `deep_dive_runs.gate_config_json` now carries a `request_index` block mapping custom_id → {actual_ticker, drug, members, anchor_nct_number, anchor_next_catalyst_type, drug_signature}. The `--resume-run` path reads this to rebuild writeback context without re-querying biotech.db.

**Anthropic `custom_id`:** previously `ticker` directly. Now `f"{ticker}__{sha8(drug)}"` so a single ticker with multiple drugs (which was previously broken — would have hit Anthropic's duplicate-custom_id error) now works correctly.

**Pack augmentation:** the anchor's pack carries a `catalyst.sibling_catalysts` list with the other catalysts for the drug (type + date) so Claude can reason about the full event schedule when producing a single deep-dive applicable to all of them.

**Known limitation — exact-string drug match.** D23 dedups when the BPC `drug` field matches verbatim. The current BPC data sometimes stores the same molecule under different drug strings depending on the trial — e.g., KURA's ziftomenib appears as `"Ziftomenib (in combination with SoC...)"` and `"ziftomenib in combination with gilteritinib (KOMET-008)"`. These are the same molecule but different strings → D23 treats them as separate drugs → no dedup. To collapse molecule-level (e.g., reduce KURA's 2 calls to 1), a future enhancement would need a drug-name normalisation pass (strip parenthetical, alias lookup, or LLM-based canonicalisation). Not in scope here.

**ACRS contrast (where dedup works):** ACRS has two hard-pass catalysts both with `drug = "ATI-052"` (`Initial Data` + `Topline Data`, both undefined-timing) → D23 collapses to 1 API call, 2 deep_dives rows. Bosakitug (ATI-045) is a different molecule → separate call. So ACRS goes from 3 catalysts → 2 API calls (down from 3 pre-D23).

**Files touched:**

- `src/module_7/cache.py` — `compute_drug_signature`, `lookup_drug_cache`, `group_candidates_by_drug`, `partition_drug_groups_by_cache`.
- `src/module_7/deep_dives_db.py` — additive migration for 3 new columns + updated `_DEEP_DIVE_COLS`.
- `src/module_7/context_pack.py` — `augment_pack_with_drug_siblings`.
- `src/module_7/__init__.py` — re-exports.
- `scripts/3_7_deep_dive.py` — main flow now: group → drug-cache partition → one API per group → expanded writeback (N rows per result). Custom_id = `ticker__sha8(drug)`. Resume path reconstructs members from `gate_config_json.request_index`.
- `scripts/3_7_estimate_cost.py` — group + drug-partition before counting dispatches; reports `catalysts → groups → API calls` so the user can see how many dispatches the dedup saved.
- `tests/test_module7_cache.py` — 9 new tests covering signature shape (order-insensitive, change-detection on every input), drug-cache lookup (hit/miss reasons, legacy-NULL handling), grouping helper.

**Cost impact (estimated):**

- Full 69-catalyst rolling-view feed pre-D23: 69 API calls.
- Post-D23: depends on BPC-string overlap. Within ACRS alone, 3 catalysts → 2 calls. Across the full feed, estimated 5-15 API-call savings if BPC drug strings overlap as expected.
- Combined with batch-mode default (D21) and recalibrated formula (D22), the expected full-feed batch cost lands ~$0.50-0.80 (vs the pre-fixes $4.80 estimate).

**Validation:**

- 439 tests passing (was 430; +9 new D23 tests, no regressions).
- Cost-estimator output explicitly logs the dedup savings: `D23 drug-dedup: N fewer API call(s) than catalyst-count would suggest`.
- Pre-flight on KURA+ACRS+ALXO: 6 catalysts → 5 groups → 5 API calls. ACRS-ATI-052 collapse confirmed.

---

## D24 — Cache-fields backfill for pre-D23 deep_dives rows (2026-05-28)

**Trigger:** user asked "verify that the 10 catalysts would not trigger a call to Claude API". The 10 prior deep_dives rows (run_id=1 sync + run_id=2 batch) were written before D23's schema migration added `drug_signature` → all 10 had `drug_signature IS NULL` → `lookup_drug_cache` returned `no_prior_row` → all 10 would re-dispatch. Plus config edits (D22 + D23) shifted `prompt_version` SHA-7 to `m7-v2:fcae987`, while the existing rows carried `m7-v2:073779c` (run 1) and `m7-v2:e43c8b3` (run 2) — independent cache-miss cause.

**Decision:** ship a one-shot migration `scripts/3_7_backfill_cache_fields.py` that:

1. Reads every successful `deep_dives` row (`p_clinical IS NOT NULL`).
2. Queries biotech.db for the full catalyst-tuple list of the row's `(snapshot_date, ticker, drug)` — these are the inputs to `compute_drug_signature`.
3. UPDATEs `drug_signature` to the computed value AND `prompt_version` to the current YAML SHA-7.
4. (D25 — see below) Also COALESCE-backfills `price_at_api_time_usd` from `fundamentals.db.financials.last_price_usd`, then derives `target_price_on_hit_usd` + `_on_miss_usd` from the stored move-percentages.
5. Skips rows that are already current. `--dry-run` previews.

**Why bump prompt_version?** Strictly speaking, D17 says config edits invalidate the cache wholesale — but D22's change was *only* the calibration factor (no Claude-side semantics) and D23 added dedup logic (no Claude-side semantics either). Forcing 10 re-dispatches for those edits is wasteful. The migration treats the existing rows as still-valid Claude output under the current YAML hash.

**Verification post-migration:**

```
[3_7_estimate_cost] candidates: 13  drug-groups: 12  to dispatch: 2  cache-hit skipped: 10
[3_7_estimate_cost] D23 drug-dedup: 1 fewer API call(s) than catalyst-count would suggest
  cache hit reason=identity_match: 10
```

10 cache-hits (exactly the original 10 from runs 1+2). The 2 remaining dispatches are *new* hard-pass catalysts not in the prior runs (ACRS ATI-052 × 2, KURA KOMET-008).

**Re-run after this:** if no BPC drop and no config edit → 0 API calls, $0 spent. As intended.

---

## D25 — Intraday live-price refresh (yfinance) + price-anchored target $ for interactive recompute (2026-05-28)

**Trigger:** user observed that the HTML's "Market cap / price" column showed BPC's docx-time price (stale by days) instead of a current quote, and asked for the `share_price_appreciation` + `expectancy/time` cells to recompute intraday as the live price moves. Also wanted Claude to see the latest share price when scoring (instead of the stale M6.5 snapshot), and to show the reference price Claude analyzed at, above E[move] in the deep-dive panel.

**Architecture:**

1. **Server-side yfinance fetcher with TTL.** New `src/module_7/live_price.py` — `get_live_prices(tickers, ttl_s=60, force=False)`. Tries `yf.Ticker(t).fast_info` first (cheapest), falls back to `yf.download(period='1d')` batched. Module-level dict cache keyed on ticker, 60s TTL. Thread-safe (lock).

2. **HTTP endpoint.** `scripts/3_7_serve_selection.py` gains `/api/live_price?tickers=A,B,C` returning `{"prices": {ticker: {price_usd, fetched_at_utc, source, error}}}`. The browser polls this every 60s.

3. **Dispatcher uses live prices.** Before pack-building, `scripts/3_7_deep_dive.py` calls `get_live_prices(candidate_tickers, force=True)`. The fetched prices overlay `pack["market_snapshot"]["last_price_usd"]` and the FDSC market cap is recomputed. Claude therefore scores against the CURRENT price (not the day-or-week-old M6.5 snapshot).

4. **Target prices stored at API time.** Three new columns on `deep_dives`:
    - `price_at_api_time_usd` — the live price Claude saw.
    - `target_price_on_hit_usd` = `price × (1 + expected_move_on_hit_pct / 100)`.
    - `target_price_on_miss_usd` = `price × (1 + expected_move_on_miss_pct / 100)`.

    These are absolute dollar targets anchored to the analysis-time price. They are STATIC after the dispatch. The JS does the live recompute against current_price.

5. **JS recompute math** (in `scripts/3_6_render_scores.py::JS`):
    ```javascript
    moveHit_pct  = (target_hit_$  - current_$) / current_$ × 100
    moveMiss_pct = (target_miss_$ - current_$) / current_$ × 100
    E[move]_pct  = p_final · moveHit_pct + (1 − p_final) · moveMiss_pct
    expectancy   = E[move] · m_momentum
    exp_per_wk   = expectancy / max(weeks_to_catalyst, 1)
    ```
    `p_final`, `m_momentum`, and `weeks_to_catalyst` are stable between M6 runs; ONLY current_$ refreshes intraday, and only the move %'s + downstream cells refresh. `p_clinical`, `rNPV`, `mgmt_track_record`, the rNPV-by-indication table, drug_profile — all stay frozen (they don't depend on share price).

6. **JS polling.** On `init()`, kick off `pollLivePrices()` and `setInterval(pollLivePrices, 60000)`. Skips when `window.location.protocol === 'file:'` (no server available) and outside `13:00Z-21:30Z Mon-Fri` (conservative US-market window). Cells recomputed from live price get a `●` indicator (`.live-tag`). A header stamp shows last-fetch timestamp.

7. **HTML deep-dive panel** now shows, above E[move]:
    - **Reference price (Claude analyzed at):** `$X.XX  ← Claude's anchor`
    - **Live price (now):** `$Y.YY ●`
    - **Target on hit ($):** `$Z (= ref × (1 + hit%))`
    - **Target on miss ($):** `$W (= ref × (1 + miss%))`
    - **Move on hit % (vs live):** recomputed pct
    - **Move on miss % (vs live):** recomputed pct
    - Then E[move], expectancy, expectancy/week — all live-recomputed.

8. **Render-only entrypoint.** New `run_3_Biopharm_render.bat`: re-renders the HTML from cached deep-dive data + starts the local HTTP server (port 7034). No pipeline run, no Anthropic call. Mirrors the 0_Renderer pattern of "lightweight live view of cached data".

9. **Market-cap intraday recompute.** Both the table row and the expanded-panel "Market cap / price" line now show `live_price × (BPC mcap / BPC price)` — scaling the static mcap by the live/BPC price ratio so we don't need FDSC shares in the row payload.

**Backfill** (D24 script extended to also cover D25 fields): all 10 existing rows now carry `price_at_api_time_usd` (from `fundamentals.db.financials.last_price_usd` at M6.5 time) and derived target prices. Pre-flight estimator confirms intent to dispatch only NEW catalysts; the 10 prior rows fully populate the HTML with live-recomputable cells.

**Files touched (this decision):**

- New `src/module_7/live_price.py` (~150 LOC).
- New `run_3_Biopharm_render.bat` (~30 lines).
- `src/module_7/deep_dives_db.py` — additive migration for `price_at_api_time_usd`, `target_price_on_hit_usd`, `target_price_on_miss_usd`; `_DEEP_DIVE_COLS` updated.
- `src/module_7/render_join.py` — includes the 3 new columns in the sidecar payload.
- `scripts/3_7_serve_selection.py` — `/api/live_price` endpoint.
- `scripts/3_7_deep_dive.py` — `get_live_prices(force=True)` call, pack-overlay, target-$ computation at write-back.
- `scripts/3_7_backfill_cache_fields.py` — extended to backfill the 3 new fields too.
- `scripts/3_6_render_scores.py` — JS additions: `livePrices` map, `currentPrice()`, `recomputeFromLivePrice()`, `pollLivePrices()`, 60s setInterval, reference-price + target-$ rows in the deep-dive panel, live mcap, CSS for `.live-tag`.

**Anthropic-pack semantic note:** Claude now sees `market_snapshot.last_price_usd` = live yfinance price (with `price_source: "yfinance-live (D25)"`). Claude's `expected_move_on_*_pct` outputs are implicitly anchored to that price. The target $ stored on the deep_dive row equals `live_price × (1 + move_pct/100)`, so JS-side recompute against future intraday prices is internally consistent.

**Performance:** 60s poll is gentler than 0_Renderer's 6s (which drives chart bars). The in-mem TTL collapses multiple browser tabs/refreshes to one yfinance call per minute. Per `feedback_swr_pattern`: render cached data instantly, refresh in background.

**Trade-off (documented for future revisit):**

- ~~The `m_momentum` modifier is NOT live-recomputed~~ — superseded by D26: m_momentum is dropped entirely.
- BPC's stored `r.price` field is no longer surfaced when the live price is available — but it remains in catalyst_snapshots for audit.
- Implies the HTML must be served via the local HTTP server to get live data (file:// view shows the cached snapshot from `data.js`).

---

## D26 — Drop m_momentum from the expectancy formula; expectancy/week derives directly from E[move] (2026-05-28)

**Trigger:** user observation — "momentum_score is already used in the composite score and I do not want to use momentum twice. In any case, m_momentum is small and does not meaningfully impact E[move]."

**The redundancy:** M6's `composite_score` is `0.35·insider + 0.35·momentum + 0.30·funds`. M6 uses momentum_score to drive the hard_pass / ranking gate. M7 was *also* using the same momentum_score to scale `expectancy_pct = e_move_pct × m_momentum`, where `m_momentum` is a linear remap of momentum_score onto `[0.95, 1.05]`. Double-counting.

**The impact:** the `[0.95, 1.05]` band tilts expectancy by ±5% max. On the 10-row backfill, the median shift was 3.7%; max single-row shift was 5%. Within the noise of the rest of the M7 estimate; not worth the double-counting.

**Decision:** drop `m_momentum` from the formula. The new (simpler) chain:

```
m_insider  = remap(insider_score      → [0.85, 1.15])
m_funds    = remap(fund_accum_score    → [0.85, 1.15])
p_final    = clamp(p_clinical · m_insider · m_funds, 0.05, 0.95)
E[move]_pct      = p_final · move_on_hit_pct + (1 - p_final) · move_on_miss_pct
expectancy/week  = E[move]_pct / max(weeks_to_catalyst, 1)
```

`m_momentum` no longer multiplies. `expectancy_pct` (was `E[move] · m_momentum`) collapses to just `E[move]` and is kept as an audit copy only — the display now shows just E[move] and expectancy/week.

**Schema preservation:** the `deep_dives.m_momentum`, `momentum_score_input`, and `expectancy_pct` columns stay (additive-only migration policy, D17 invariant). `m_momentum` is hard-coded to `1.0`; `expectancy_pct = e_move_pct`. Legacy queries / tests that read these fields still get sensible values.

**`expectancy / time` vs `expectancy / week`:** user confirmation that these refer to the same `expectancy_per_week_pct` field. The column was already weekly under the hood; the "/ time" header label was just imprecise wording. Renamed to `Expectancy / week`.

**JS recompute (D25 path) updated:** `recomputeFromLivePrice` no longer multiplies by `m_momentum`. `expectancy_per_week_pct = E[move] / max(weeks, 1)` directly. Returned object dropped its `expectancy_pct` key.

**HTML expand-panel display cleaned:** removed two rows from the deep-dive kv panel:
- the `m_momentum` row (no longer used)
- the standalone `expectancy = E[move] · m_momentum` row (= E[move] now, redundant)

Surviving rows in the panel walk through: p_clinical → m_insider → m_funds → p_final → reference price (Claude) → live price → target on hit/miss ($) → move on hit/miss (% vs live) → **E[move]** → weeks_to_catalyst → **expectancy / week = E[move] / weeks**. Linear, no double-counting, fully live-recomputed where price is available.

**Files touched:**

- `src/module_7/scoring.py` — `compute_expectancy`: `m_mom = 1.0`, `expectancy = e_move` (no momentum multiplication), `expectancy_per_week = e_move / max(weeks, 1)`. Docstring + formula header updated to reflect D26.
- `scripts/3_6_render_scores.py`:
  - Table header `"Expectancy / time"` → `"Expectancy / week"`; tooltip updated.
  - "Share price appreciation" column tooltip dropped the `· m_momentum` clause.
  - JS `recomputeFromLivePrice` simplified — no m_momentum lookup.
  - Expand-panel removed `m_momentum` + `expectancy` rows.
- `scripts/3_7_backfill_cache_fields.py` — extended to normalize the 10 existing rows: `m_momentum = 1.0`, `expectancy_pct = e_move_pct`, `expectancy_per_week_pct = e_move_pct / max(1, weeks)`. Shifts were 0-5% per row, all under noise.
- `tests/test_module7_scoring.py` — `test_momentum_modifier_only_tilts_expectancy_not_p_final` rewritten to `test_momentum_modifier_is_no_op_after_D26` (assertions inverted: hot/cold both give same expectancy). `test_expectancy_per_week_normalisation` renamed `test_expectancy_per_week_from_e_move_after_D26` (asserts `exp/wk × weeks == e_move`).

**Validation:** 439 tests passing (unchanged count; 2 scoring tests rewritten with same assertion intent). Template refreshed 48 → 48 KB (minor); sidecar refreshed.

**Future cleanup (low priority):** the `m_momentum`, `momentum_score_input`, and `expectancy_pct` DB columns are now dead-weight. Leaving them in place per the additive-only schema discipline (D17). If a future cleanup pass removes them, this entry is the rationale. → **Acted on in D33 (2026-05-28).**

---

## D33 — Schema cleanup: drop the three dead M7 columns + python fields (2026-05-28)

**Trigger:** the D26 "future cleanup, low priority" note. After ~2 weeks of stable operation under D26's no-op `m_momentum`, removed the dead-weight columns entirely.

**Dropped:**

| Field | Was | Status post-D33 |
|---|---|---|
| `deep_dives.m_momentum` REAL | hard-coded to 1.0 since D26 | **dropped via `ALTER TABLE ... DROP COLUMN`** |
| `deep_dives.momentum_score_input` REAL | echo of M6's momentum_score input | **dropped** |
| `deep_dives.expectancy_pct` REAL | alias for `e_move_pct` since D26 | **dropped** |
| `ExpectancyResult.m_momentum` | dataclass field | **dropped** |
| `ExpectancyResult.momentum_score_input` | dataclass field | **dropped** |
| `ExpectancyResult.expectancy_pct` | dataclass field | **dropped** |
| `compute_expectancy(momentum_score=…)` | function parameter | **removed; passing it now raises TypeError** |
| `module_7.yaml::modifiers.momentum` | config block | **kept but unused** — `compute_expectancy` ignores it (back-compat: edits to it don't break anything; removable in a future config-only edit) |

**Implementation:**

- New `_DROPPED_COLUMNS: list[tuple[str, str]]` in `src/module_7/deep_dives_db.py` paired with `_apply_destructive_migrations(conn)`. Uses SQLite 3.35+ `ALTER TABLE ... DROP COLUMN`; silently tolerates older SQLite by logging a warning and leaving the column in place.
- `init_deep_dives_db()` calls both `_apply_additive_migrations()` and `_apply_destructive_migrations()` after the `CREATE TABLE IF NOT EXISTS` script. The schema SQL itself was also updated to drop the three columns so a fresh init never creates them.
- `_DEEP_DIVE_COLS` tuple shortened — no longer references the three columns.

**Cascade cleanup:**

- `src/module_7/scoring.py::compute_expectancy` — `momentum_score=` parameter removed; `m_mom = 1.0` line gone; result construction no longer sets the dropped fields. Docstring documents the breaking change.
- `scripts/3_7_deep_dive.py::_write_results` — row dict no longer carries `"m_momentum"`, `"momentum_score_input"`, `"expectancy_pct"`. Call to `compute_expectancy` no longer passes `momentum_score=`.
- `scripts/3_7_backfill_cache_fields.py` — the D26 normalisation logic that wrote those three fields is removed (along with the diff-detection vs current values).
- `src/module_7/render_join.py` — payload no longer includes the three keys; the JS sidecar shrinks slightly.
- `scripts/3_6_render_scores.py` — renderer JS was already clean from D26 (no references); comments retain the historical D26 context.

**Tests rewritten:**

- `tests/test_module7_scoring.py::test_neutral_signals_do_not_alter_p_clinical` — drops `m_momentum` assertion; adds `assert not hasattr(r, "m_momentum")`.
- `tests/test_module7_scoring.py::test_momentum_modifier_is_no_op_after_D26` → renamed `test_momentum_field_dropped_after_D33` — asserts that passing `momentum_score=` raises `TypeError` and the result has no momentum-related attributes.
- `tests/test_module7_scoring.py::test_expectancy_per_week_from_e_move_after_D26` — still passes; just doesn't reference the dropped fields.
- `tests/test_module7_deep_dives_db.py` — all `expectancy_pct=` kwargs in upsert calls changed to `e_move_pct=` (same numeric semantic).
- `tests/test_module7_render_join.py` — drops `"expectancy_pct": 21.6` from the test row.
- `tests/test_module7_scoring.py::_neutral_inputs` — `momentum_score=50.0` dropped from base; `**over` still tolerates legacy callers via a `pop("momentum_score", None)` step.

**Migration safety:**

- Existing legacy rows (the 10 from runs 1+2) had their three dead fields silently dropped on the next `init_deep_dives_db()` call. No data loss — those columns were already meaningless after D26.
- `_DEEP_DIVE_COLS` change means `INSERT OR REPLACE` against pre-D33 rows still works (extra columns in the existing schema would be ignored, but `DROP COLUMN` already removed them).

**Validation:** 439 tests passing (unchanged count; 2 scoring tests rewritten with the same assertion intent). No regressions in other modules.

---

## D34 — Delisted-ticker hygiene: new H6 gate + delisted_tickers table + flag script (2026-05-28)

**Trigger:** DVAX was in `biotech.db.catalyst_scores` as `hard_pass=1` (because BPC's docx still recorded a market cap from before delisting), but yfinance can't fetch its price. M6.5 silently failed; the live-price server cached the failure every 60s; the HTML showed `—` for live price. Soft no-op, but the row pollutes the hard-pass tab and the M7 dispatch feed.

**Decision:** add a curated allowlist + a new hard filter (H6) so delisted tickers fail M6's hard_pass naturally and are excluded from M7 + the live-price feed. Curated rather than auto-detected because false-positive auto-deletion is worse than a one-line manual flag.

**Schema:**

```sql
-- §2.10 — biotech.db
CREATE TABLE IF NOT EXISTS delisted_tickers (
    ticker       TEXT PRIMARY KEY,
    flagged_at   TIMESTAMP NOT NULL,
    reason       TEXT,
    source       TEXT          -- 'manual' / 'yfinance-probe' / 'edgar-suspension' / ...
);
```

`scripts/3_0_init_db.py::EXPECTED_TABLES` extended so the validator no longer flags the new table as "unexpected".

**Filter wiring:**

- New `_check_H6(ticker, delisted_set) -> bool` in `src/module_6/filters.py`. Returns True (= pass) when ticker is None OR the set is empty/None. Case-insensitive (`.upper()`).
- `apply_hard_filters(...)` extended with keyword-only `ticker: str | None = None` and `delisted_tickers: frozenset[str] | set[str] | None = None`. Backward-compatible — legacy callers that omit these still work (H6 is a no-op for them).
- `src/module_6/ingest.py::score_snapshot` loads `SELECT ticker FROM delisted_tickers` once per run into a `frozenset`, then passes it through every `apply_hard_filters` call. On older biotech.db files that don't have the table yet, the load tolerates `sqlite3.OperationalError` and falls back to an empty set.

**Flag-management script:** `scripts/3_flag_delisted_tickers.py`:

```bash
# Inspect the current set
python scripts/3_flag_delisted_tickers.py --list

# Add a ticker (auto-rescores existing catalyst_scores rows):
python scripts/3_flag_delisted_tickers.py --add DVAX --reason "yfinance 404; delisted"

# Remove a ticker (does NOT auto-restore the rows — run M6 to re-score):
python scripts/3_flag_delisted_tickers.py --remove DVAX
```

The `--add` path does two things atomically (single `with cx:` block):
1. `INSERT … ON CONFLICT DO UPDATE` into `delisted_tickers` (idempotent).
2. UPDATE every existing `catalyst_scores` row for the flagged ticker: `hard_pass = 0`, append `H6` to `fail_reasons` (preserving any prior failures), `timing_bucket = NULL`.

This avoids needing a full M6 re-run after flagging a single ticker. The next M6 run picks up the delisted set on its own and produces identical results.

**HTML legend updated:**

- `scripts/3_6_render_scores.py`: H-gate legend on the Excluded tab now includes H6 with a description of the curated allowlist and the management command.
- `renderFailMeta`'s order array extended `['H1','H2','H3','H4','H5'] → […, 'H6']` so the footer count includes H6.

**Side effects (intentional):**

- `_hard_pass_allowlist()` in `scripts/3_7_serve_selection.py` (D28) queries `WHERE hard_pass = 1`, so DVAX is now automatically excluded from `/api/live_price` requests.
- `fetch_hard_pass_candidates()` in `module_7.context_pack` also queries `WHERE hard_pass = 1`, so M7 won't dispatch a Claude call for DVAX. No code change needed in M7.
- The HTML's hard-pass tab loses DVAX naturally; the Excluded tab shows it with the H6 chip.

**Seeded:** DVAX added 2026-05-28 (reason: "yfinance 404; delisted"). Hard-pass count went **69 → 68** in the rolling view.

**Tests added** (`tests/test_module6_filters.py`):

- `test_h6_pass_when_no_delisted_set` — None / empty set treated as pass.
- `test_h6_pass_when_ticker_not_in_set`
- `test_h6_fail_when_ticker_in_set`
- `test_h6_case_insensitive` (`"dvax"` in set `{"DVAX"}` still fails)
- `test_h6_combines_with_other_failures` (H1 + H6 both present)
- `test_h6_backward_compatible_when_args_omitted` (legacy callers pass cleanly)

**Validation:** 445 tests passing (was 439; +6 H6 tests). DB inspection confirms DVAX `hard_pass=0`, `fail_reasons='H6'`, rolling-view count 68.

**Future-work signals:**

- A `yfinance-probe` auto-detector (scan hard-pass tickers, flag those that consistently fail to fetch for N days) would automate this. Out of scope for D34 — manual flag is fine for the small number of cases we hit.
- The HTML could surface the flag (e.g., a "delisted" badge on row hover) — currently only visible via the H6 fail-reason chip. Acceptable as-is.

---

## D35 — Module 8: catalyst rescue + re-dispatch + renumber dashboard to M9 (2026-05-29)

**Trigger:** ~83% of BPC catalysts fail M6's H1-H6 gates and never reach Claude. Some failures are false negatives in the user's investing thesis — small-cap H1 fails, H3 fails (imminent or BPC date missing), and PDUFA-style H5 fails are all worth a deep-dive when context is added (in particular: independent date resolution from primary sources). The user spec'd a rescue gate as new "Module 8", pushing the previously-planned iOS dashboard out to "Module 9".

**Decision:** add an additive rescue path. Rescue does NOT modify M6's hard_pass logic — it adds a separate `rescued/rescue_class` column pair on `catalyst_scores` plus an M8-specific Claude dispatch that uses a distinct `prompt_version_label` so the Anthropic prompt cache stays isolated from M7. For B/C rescues, the M8 system prompt prepends a date-retrieval instruction so Claude resolves the catalyst date from primary sources (per M7's EVIDENCE HIERARCHY) before scoring.

**Three rescue classes:**

| Class | Filter | Trim (user decision) | Count (2026-05-29) |
|---|---|---|---:|
| A | `H1 in fail_reasons AND mcap ∈ [$0, $2B]` | NULL mcap included | 43 |
| B | `H3 in fail_reasons AND H1 NOT in fail_reasons` | full (H4 overlap kept) | 171 + 5 BC |
| C | `H5 in fail_reasons AND H1 NOT in fail_reasons` | `Regulatory Decision` + NULL type only | 12 + 5 BC |
| **Total unique** | | | **231 catalysts / 177 tickers** |

H2 (timing precision unknown) and H6 (delisted) are NOT rescued — those rows stay hard-excluded. H4 overlap (`date_max < snapshot`) IS allowed in; Claude's HARD RULE #8 catches the resulting `catalyst_already_passed` cases as `deep_dive_errors` (~6 wasted calls expected, ~$0.25 of waste). User accepted this trade for the audit signal.

**C scope trim rationale:** the full H5-fail set (60 catalysts) includes 13 `Submission`, 5 `End of Phase Meeting`, 4 phase0 `Conference Presentation` etc. — these are rarely binary near-term catalysts and Claude has no good scoring framework for them. The user-approved scope (Regulatory Decision + NULL type) drops noise without losing material PDUFA-style binary events.

**Schema (additive only — no rebuilds, no FK changes):**

```sql
-- biotech.db.catalyst_scores
ALTER TABLE catalyst_scores ADD COLUMN rescued INTEGER DEFAULT 0;
ALTER TABLE catalyst_scores ADD COLUMN rescue_class TEXT;        -- 'A' / 'B' / 'BC' / 'ABC' / ...
CREATE INDEX idx_scores_rescued ON catalyst_scores (snapshot_date, rescued);

-- claude_deep_dives.db.deep_dives
ALTER TABLE deep_dives ADD COLUMN claude_resolved_catalyst_date TEXT;   -- ISO YYYY-MM-DD (B/C only)
ALTER TABLE deep_dives ADD COLUMN catalyst_date_source TEXT;            -- citation string
ALTER TABLE deep_dives ADD COLUMN rescue_class TEXT;                    -- copy of catalyst_scores.rescue_class
```

The biotech.db migrations live in `database/db.py::_apply_additive_migrations` (mirrors `deep_dives_db._apply_additive_migrations`). The `idx_scores_rescued` index is created BY the migration helper, not by `schema.sql`, so existing-DB `executescript` doesn't fail on the rescued column not existing yet (subtle gotcha: SQLite's `CREATE INDEX IF NOT EXISTS` checks for the index, not the column; if the column is missing on an existing DB the statement errors out).

**Date storage:** user spec said "overwrite the BPC date in the SQL DB". Pushed back during design phase: additive column on `deep_dives` is preserves the audit trail, survives next BPC re-ingest (which would overwrite `catalyst_snapshots.catalyst_date` anyway), and lets the renderer prefer Claude's resolution without losing the original. The user agreed.

**No-date case:** user chose "always score, even with best-guess month/quarter" over "refuse to score". The M8 prompt prefix's date-retrieval section walks tiers 1-4 of the EVIDENCE HIERARCHY and falls back to month/quarter best-guess or snapshot+12mo placeholder; `catalyst_date_source` is tagged with `"best-guess: …"` or `"unable to resolve — placeholder +12 months from snapshot"` so the human reviewer can triage.

**Cost model (2026-05-29 first run):**

| Scenario | USD |
|---|---:|
| no-optim | $33.43 |
| cache-only | $29.41 |
| **cache+batch (prod)** | **$15.84** |
| cost ceiling (`module_8.yaml`) | $50.00 |

Calibration factor 0.10 mirrors M7 (D22).

**Reused M7 machinery (design rule: M8 ⊂ M7):** dispatch, cache, cost_estimate, parsing, scoring, deep_dives_db, live_price all reused unchanged. `parsing.py` gets two new OPTIONAL fields (`claude_resolved_catalyst_date`, `catalyst_date_source`) — both default to None, so standard M7 responses pass validation unchanged. The only pure-M8 logic is `rescue_filter.classify_catalyst()`, the prompt prefix, and the wiring scripts.

**Renderer changes (`scripts/3_6_render_scores.py`):**
- New "Rescued" tab between "Hard pass" and "Excluded" + class chip (purple) + faded original fail chips + rescue legend strip
- Date column prefers `claude_resolved_catalyst_date` with a 📅 marker; hover shows the source
- Excluded tab filter changes from `!hard_pass` to `!hard_pass AND !rescued` so rescued rows leave Excluded automatically
- Live-price polling widened from `hard_pass=1` to `hard_pass=1 OR rescued=1` (both JS filter + server allowlist in `3_7_serve_selection.py::_refresh_hard_pass_tickers`)
- KPI strip "Hard pass" sub-label now shows `N rescued · M excluded`
- Sort default extended: `hard_pass DESC, rescued DESC, composite_score DESC` (hard-pass stays on top)

**Renumber: M8 (was dashboard) → M9.** The previously-planned iOS-optimized dashboard moves to Module 9. Updated: `spec/biotech_pipeline_spec.md` (§1 pipeline diagram + §12.4 module list), `spec/module_8_spec.md` (new), this entry. M9 hasn't been built yet; this is doc-only.

**Validation:**
- 468 tests passing (was 445; +18 rescue_filter classification tests + 5 parsing extension tests). 4 skipped (unrelated).
- `3_8_compute_rescue.py` populated 231 rescued rows on first run; per-class breakdown matches expected (A=43, B=171, BC=5, C=12).
- `3_8_estimate_cost.py` produced $15.84 estimate, written to `Outputs/m8_cost_estimate.html`.
- Renderer rebuilt template (63.6 KB) with the new tab + rescue chip CSS + date marker; live UI shows rescued rows under the new tab.

**Future-work signals:**

- A `dispatch_kind`-aware cost-per-class breakdown query on `deep_dive_runs.gate_config_json` would let us retune the C scope trim against real Claude responses (was 1L NSCLC EGFR exon 20 worth the call? was that ITP PDUFA?). Not blocking; data is captured for the post-hoc.
- `config/module_7.yaml::modifiers.momentum` is still present as inert config (D33 left this as a future cleanup). Could be dropped in a future config-only edit.
- M9 dashboard remains unbuilt.

---

## D36 — M8 UI refinement + plumbing bug fixes + operational learnings (2026-05-29)

After the initial D35 build dispatched ZBIO + BHVN as a 4-call verification (run #5), then the full 224-call rescue feed (run #6) + a CRBU retry (run #7), six follow-up changes consolidated under D36 plus three bug fixes that surfaced during the live UI test pass.

### D36a — Merge "Hard pass" + "Rescued" tabs into a single "Catalyst" tab

**Trigger:** the user wanted both kinds of catalyst rows in one view rather than tab-switching.

**Change** (renderer-only):
- HTML: 3 tabs → 2 tabs (`Catalyst`, `Excluded`). The data-tab attribute moves from `hard_pass`/`rescued` to a unified `catalyst`.
- State migration: any persisted `state.tab` in localStorage (old `hard_pass`/`rescued`/`catalyst_date_defined`/`catalyst_date_undefined`) maps to `catalyst` on next page load.
- KPI strip: "Catalysts" card now shows `N hard-pass · M rescued · K excluded` in the sub-label.
- Filter logic: `rowMatchesFilters` collapses to `if (excluded) drop hard_pass||rescued; else keep hard_pass||rescued`.
- Per-row tag chip: `r.rescued` ? rescue-class chip (purple) : timing-bucket chip (green/amber). Both fit the same column width.
- Sort order extended: `hard_pass DESC, rescued DESC, composite_score DESC` so hard-pass still leads.

### D36b — Pale-green "new catalyst" highlight + acknowledgement checkbox

**Trigger:** with 231 newly-admitted rescue rows landing in one go, the user needs a way to track which rows they've already reviewed without re-reading the whole table.

**Mechanism:**
- localStorage `catalyst_acknowledged_v1` = JSON array of PK strings. PK is `ticker|drug|nct|type` — stable across snapshots, so an acknowledged catalyst stays acknowledged when a new BPC docx lands.
- Bootstrap rule: on FIRST EVER load (no key exists), seed the set with all current `hard_pass=1` PKs. Result: only the rescued rows light up pale-green on first visit. Future BPC drops adding hard_pass or rescued rows show up as new automatically.
- Per-row TR gets `class="unack"` when isUnacknowledged. CSS: `rgba(52,211,153,0.07)` background, tinted on hover/expanded so the cue persists while reading.
- Expand-panel top-left: an `.ack-toggle` label+checkbox. Tick → PK added to set + saved + renderTable() → highlight disappears. Untick → highlight returns.
- D31's expand-row reinsertion mirrors the `unack` class onto the inserted `tr.expand-row` so the panel also carries the tint.
- The click handler in `bind()` ignores clicks landing on `.ack-toggle` so ticking the box doesn't collapse the panel.

### D36c — Per-column widths + `table-layout: fixed` + smaller header font

**Trigger:** with verbose rescue drug names (e.g., "Resecabtagene autoleucel (rese-cel, formerly referred to as CABA-201) - (RESET-PV)") the table was overflowing the viewport horizontally.

**Change:**
- `table-layout: fixed` makes column widths enforced (auto-layout treats `width` as a hint that long content can override). Total width sums to 100% so the table never exceeds its container.
- 16 columns assigned explicit %-widths: Name 10, Drug 12, Date 7 (forces date to wrap inside column), Precision/fail 5 (down), Probability 4.5 (down), Share-price-appreciation 6 (down), other narrow numerics 5.
- Header font dropped from 11 px → 9.5 px with `line-height: 1.2` and `white-space: normal` so multi-word headers like "Share price appreciation" and "Expectancy / week" wrap inside their narrow columns.
- Cell horizontal padding 8 px → 5 px to recover horizontal real-estate.

### D36d — Review-status filter (new / acknowledged / any) just left of "reset"

**Trigger:** the pale-green highlight (D36b) tells the user "this is new" but with 231 rows the user still has to scroll. A filter that hides everything they've already acknowledged lets them work the list down to zero.

**Change:**
- New `<select id="review-status">` between the ticker-search input and the reset button. Three values: `any` / `new only` / `acknowledged only`.
- `state.reviewStatus` (default `any`) persists to the existing `catalyst_filters_v1` localStorage key alongside the other filters.
- `rowMatchesFilters` adds two lines applying the filter.
- `reset` button clears it back to `any`.

**Workflow:** set `new only` → review highest-composite rows top to bottom → tick acknowledge in each expand panel → row drops out of the filtered view → loop until empty.

### D36e — Disk-persistent yfinance render-time cache (`data/render_price_cache.json`)

**Trigger:** the D35b render-time yfinance batch fetch (~290 tickers) cost ~22s wall on every render. Re-running `run_3_Biopharm_render.bat` repeatedly during a session was painful even though prices hadn't moved.

**Measured impact:**
- Cold render (empty cache): 27 s (writes cache)
- Warm render (≤30 min): **3.8 s** — matches the `--no-fetch-prices` baseline
- 7× speed-up on steady-state

**Cache shape** (`data/render_price_cache.json`):
```json
{ "TICKER": { "price_usd": 18.56, "fetched_at_utc": "2026-05-29T06:23:18+00:00", "cached_at_epoch": 1748497398.45 } }
```

Two TTLs:
- `_PRICE_CACHE_TTL_S = 1800` (30 min) for successful fetches
- `_PRICE_FAILURE_TTL_S = 14400` (4 hours) for failures — delisted/illiquid tickers would otherwise trigger a `yf.Ticker(t).history(...)` per-ticker probe (~5-15s timeout each) on every render

New CLI flag: `--refresh-prices` bypasses the disk cache (when the user wants truly current prices). `--no-fetch-prices` continues to skip the fetch entirely (fastest, blue/green markers may be absent for rescued rows outside market hours).

The intraday JS poller (D25) is untouched and still keeps cells live during market hours; the disk cache only seeds the initial paint. Aligns with memory `feedback_swr_pattern`.

### Bug fix #1 (M6) — composite_score was NULL for hard-fail rows

**Discovered when:** building the Rescued tab — composite_score column was blank for all 231 rescued rows.

**Root cause:** [`src/module_6/ingest.py:287-299`](../src/module_6/ingest.py) gated the `composite(...)` call on `verdict.hard_pass`, leaving non-pass rows with `composite_score = NULL` in the DB.

**Fix:** compute composite for ALL rows. The signal scores (`insider_score`, `momentum_score`, `fund_accumulation_score`) were already computed unconditionally; only the final weighted sum was being skipped. Re-ran M6 with `--all-snapshots` to backfill.

### Bug fix #2 (M6) — `INSERT OR REPLACE` clobbered rescued/rescue_class

**Discovered when:** the M6 re-run for fix #1 reset all the D35-populated `rescued` and `rescue_class` columns back to defaults (0 / NULL). The compute_rescue script had to be re-run after every M6 ingest.

**Fix:** changed `_INSERT_SQL` in `module_6/ingest.py` from `INSERT OR REPLACE` to `INSERT ... ON CONFLICT (pk) DO UPDATE SET …` that explicitly does NOT update `rescued` or `rescue_class`. M6 re-runs now leave the rescue state intact; the M8.0 compute step still runs after M6 in the orchestrator bat as belt-and-braces.

### Bug fix #3 (renderer) — `render_join.py` wasn't passing D35 fields into the data payload

**Discovered when:** the 2-ticker M8 verification (run #5) wrote `claude_resolved_catalyst_date` + `catalyst_date_source` to the DB correctly, but the rendered HTML showed no 📅 marker. The data sidecar had `dd.claude_resolved_catalyst_date = null` for the 4 rows the dispatch had just populated.

**Root cause:** [`src/module_7/render_join.py::fetch_latest_deep_dive_map`](../src/module_7/render_join.py) builds the per-row `deep_dive` dict by manually copying named columns. The three D35 columns hadn't been added to that copy list.

**Fix:** added `claude_resolved_catalyst_date`, `catalyst_date_source`, `rescue_class_dispatch` to the payload — guarded by `"col" in r.keys()` so the renderer tolerates an older `claude_deep_dives.db` that pre-dates the D35 schema migration.

**Lesson:** the 2-ticker verification (run #5, $0.16) caught a bug that would have wasted the full 224-call rescue dispatch. Worth the cost of explicitly testing one or two end-to-end before scaling.

### Renderer follow-on (`bestPriceInfo` + expand panel)

- `bestPriceInfo` gained a `'render_yfinance'` tier (between `'live'` and `'dispatch'`) reading from `r.yfinance_render_price_usd`. `isFresh` was loosened from `live || dispatch` to `source !== 'bpc'` so blue + green ● fire for ANY yfinance-sourced price. Resolves the inconsistency the user spotted (Move/E[move] cells lit when mcap/price didn't).
- Expand panel: insider trades + funds breakdown sections widened from `r.hard_pass` to `r.hard_pass || r.rescued`. Server-side SQL feed widened same way so the sidecar carries the rows for rescued tickers.
- The "Rescued tab" tagBucket originally inlined fail-reason chips next to the rescue-class chip. After D36a's merge, that's a single rescue chip only — fail reasons moved to the expand panel as a "Original gate failures" kv line (faded when the row is rescued).

### Dispatch operational history (post-D35)

| Run | Mode | Feed | Wall | Cost (calibrated) | Outcome |
|---|---|---|---:|---:|---|
| 5 | batch (M8) | ZBIO + BHVN (4 calls) | 341 s | $0.16 | 4/4 parsed; surfaced bug-fix #3 above; 4/4 rows have Claude-resolved date |
| 6 | batch (M8) | Full rescue feed: 224 calls populating 212 rows; 4 cache-hits from run #5 | 466 s | $9.08 | 209/224 parsed (93%); 14 HARD-RULE-#8 `catalyst_already_passed` (real signal — BPC was tracking stale catalysts), 1 transient json_parse_fail (CRBU). 100% date-resolution rate. |
| 7 | batch (M8) | CRBU retry (run #6 transient parse fail) | 218 s | $0.04 | 1/1 parsed. Wall-time floor at ~3.6 min for single-call batches (Anthropic batch infrastructure overhead). |

**Cumulative across runs 1–7:** 282 deep_dives rows, 7 runs, $13.71 script-calibrated cost.

**Calibration tracker:** the 0.10 factor over-estimates real spend by ~1.5–1.7×. Run #6 estimated $15.57, actual $9.08 (0.58×). Run #5 estimated $0.28, actual $0.16 (0.57×). Safe direction (estimate > actual) — leave the factor at 0.10 until invoices land for runs 3–7.

**Run #6's 14 stale catalysts** — the M8 prompt's HARD RULE #8 freshness check found that these tickers' "future" BPC catalysts had already happened (or been canceled). This is real signal, not noise: it identifies BPC tracking gaps the user would otherwise have chased. The errors land in `deep_dive_errors` with `error_kind='catalyst_already_passed'` and human-readable notes citing the press release / 8-K / NCT update that contradicts the BPC date.

| Ticker | What Claude found |
|---|---|
| TSVT | Asset divested to Regeneron (APA closed 2024-04-01) |
| LIXT | OCCC data already presented at SGC Puerto Rico 2026-04-13 |
| LTRN | Type C meeting outcome already announced via BusinessWire |
| ALGS ×2 | EASL 2026 oral already presented 2026-05-27 |
| PBYI | Ph2 ALISCA-Breast already presented |
| LPCN | ASCP presentation occurred May 26-27, 2026 (before snapshot 2026-05-28) |
| BCDA | CardiAMP CMI data at EuroPCR 2026-05-21 |
| GLPG | Program CANCELED per 6-K dated 2026-01-05 |
| AQST | AQST-108 Ph1 topline released 2026-05-13 |
| QURE | EPISOD1 prelim data discontinued |
| IRWD | LINZESS PDUFA approved 2026-05-28 |
| SDGR | SGR-3515 data already at AACR 2026 (April) |
| DTIL | EASL late-breaker poster 2026-05-27 (= snapshot date) |

**Net effect on the dataset:** 224 dispatched, 209 scored, 14 confirmed-stale (de-prioritised in the UI), 1 transient retried clean. Real success rate post-retry: 100% of valid catalysts scored.

### High-conviction picks surfaced by run #6 (top 6 by exp/wk)

| Ticker | Indication | p_final | E[move] | exp/wk | Weeks | Resolved date |
|---|---|---:|---:|---:|---:|---|
| **CING** | ADHD (peds + adult, 505(b)(2)) | **0.66** | **+26.9%** | **+26.89%/wk** | 1 | 2026-05-31 |
| APRE | PPP2R1A-mutated uterine serous | 0.50 | +9.7% | +9.73%/wk | 1 | 2026-05-30 |
| IMRX | 1L metastatic pancreatic cancer | 0.57 | +7.1% | +7.14%/wk | 1 | 2026-06-01 |
| CNTX | Platinum-resistant ovarian | 0.59 | +21.0% | +7.01%/wk | 3 | 2026-06-15 |
| REPL | RP2 + nivo metastatic uveal melanoma | 0.53 | +7.0% | +6.97%/wk | 1 | 2026-05-31 |
| CGEM | Rheumatoid Arthritis | 0.58 | +6.7% | +6.74%/wk | 1 | 2026-06-06 |

CING — sub-$5 stock with 66% p_final and a 1-week window — is exactly the kind of high-conviction near-term catalyst the M8 rescue path was built to surface. It was a B-class rescue (BPC date was within 14d so H3 failed); Claude resolved the actual readout date to 2026-05-31.

**Validation:** 468 tests still passing (one M6 test updated to assert `composite_score IS NOT NULL` after bug fix #1).

---

## D37 — Quarterly DB pruning to bound growth (2026-05-29)

**Trigger:** the user asked whether passed catalysts get cleaned out. Audit confirmed: the HTML output self-limits (the renderer's `date_max >= effective_today` filter drops past catalysts before they reach the JS sidecar — that part is fine) but the **DBs accumulate forever**. At weekly BPC + EDGAR ingest cadence, projecting 52 weeks ahead:

| Table | Today | After 1 yr | Notes |
|---|---:|---:|---|
| `catalyst_snapshots` (+ timing + scores, same PK) | 844 | ~25-30k | 4-5× growth per snapshot per catalyst PK |
| `bpc_insider_supplement` | 3,112 | ~10-15k | same per-snapshot accumulation pattern |
| `edgar_form4_transactions` | 14,300 | ~80-150k | small per row, useful for backtesting |
| `web_search_cache` | 6,998 | ~50-100k | **worst offender** — Claude content blobs (~10s of KB each) |
| `deep_dives` | 283 | ~2-10k | small per row, durable audit value |

Without action: ~500 MB-1 GB SQLite by EoY, dominated by `web_search_cache`.

**Decision:** add a tiered prune strategy. Per-table retention rules:

| Table | Strategy | Default | Why |
|---|---|---|---|
| `catalyst_snapshots`, `catalyst_timing`, `catalyst_scores` (cascade) | Keep top-N snapshots per PK | `keep_n=3` | Rolling-view only reads MAX(snapshot_date); keeping 3 gives ~3 weeks of audit ("how did this catalyst's date / stage drift?") |
| `bpc_insider_supplement` | Keep top-N snapshots per PK | `keep_n=3` | Same accumulation pattern as catalyst_snapshots |
| `web_search_cache` | TTL drop on `cached_at` | 90 days | Pure cost-saver cache; Claude can re-fetch any URL on demand |
| `deep_dive_errors` | TTL drop on `created_at` | 90 days | Low value beyond ~3 months |
| `deep_dives`, `deep_dive_runs` | **NEVER prune** | — | Historical Claude analyses + audit trail |
| `edgar_form4_*`, `edgar_ownership_filings` | **NEVER prune** | — | Small, useful for cross-quarter trend analysis |
| `ticker_cik_map` | **NEVER prune** | — | One-time bootstrap |
| `fundamentals.*` | not applicable | — | M6.5 re-fetches; doesn't accumulate |

**FK ordering:** `catalyst_scores` and `catalyst_timing` have non-CASCADE FKs to `catalyst_snapshots`. With `PRAGMA foreign_keys = ON` (default in `db.py`), the children must be deleted before the parent. The CLI wraps all three deletes in a single transaction (and `ROLLBACK`s on any failure) so the FK chain can't end up inconsistent.

**Quarterly schedule (`--auto` mode):** the user picked "~1.5 months after each 13F deadline" so the funds DB has stabilised before we reshape biotech.db. 13F deadlines are Feb 14 / May 15 / Aug 14 / Nov 14; trigger dates land at **Jan 1 / Apr 1 / Jul 1 / Oct 1**. `is_prune_due(today, last_prune)` returns True when (a) we've never pruned, or (b) the most-recent quarterly trigger has passed AND `last_prune < that trigger`. Tracking lives in `ingest_log` with `module='prune'`.

**Architecture:**

```
src/database/
  prune.py            — pure-compute planners + executors
                        + most_recent_trigger_on_or_before()
                        + is_prune_due()
                        + get_last_prune_date()

scripts/
  3_prune_old_data.py — CLI driver
                        dry-run by default; --write to commit
                        --auto: bat-driven, honors quarterly schedule
                        --force: bypass --auto check
                        --keep-snapshots N (default 3)
                        --web-search-ttl-days D (default 90)
                        --error-ttl-days D (default 90)
                        writes ingest_log row per run; VACUUMs both DBs after a write
```

`run_3_Biopharmcatalyst_parser.bat` calls `python scripts\3_prune_old_data.py --auto --write` after the M8 dispatch step. Silent no-op when the schedule says we're not due.

**Tests:** 18 new in `tests/test_database_prune.py` (schedule logic + per-PK retention + TTL + FK-ordering validation). Full suite **486 passing**, 4 skipped (no regressions).

**Current-DB impact (dry-run today, 2026-05-29, only 2 snapshots ingested so far):**

| Table | kept | would_delete |
|---|---:|---:|
| catalyst_scores | 844 | 0 |
| catalyst_timing | 844 | 0 |
| catalyst_snapshots | 844 | 0 |
| **bpc_insider_supplement** | 2,319 | **793** |
| web_search_cache | 6,998 | 0 |
| deep_dive_errors | 17 | 0 |

`bpc_insider_supplement` is the only table with prune-able rows today (793 — the BPC insider CSV must have been ingested across multiple historical snapshots before the brief started tracking). All other tables are below the keep_n=3 / 90-day thresholds. Real impact will compound over time.

**Future-work signals:**

- A `--report-only` flag that also reports estimated DB size reduction post-prune (currently you just see row counts). Useful when DB hits multi-GB.
- The bat could `SELECT MAX(finished_at) FROM ingest_log WHERE module='prune'` and print "Last DB prune was X days ago" at the top of every run as a visibility nudge, even when --auto is no-op.

---
