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
