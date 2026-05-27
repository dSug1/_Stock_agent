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
