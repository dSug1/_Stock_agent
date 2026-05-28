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
