# Module 1 — BPC catalyst CSV ingest

**Plain-English purpose:** Module 1 loads one BPC FDA-catalyst CSV
download into the database, tagged with the date you loaded it. It is
the **entry-point** for every other module — without M1 having
populated `catalyst_snapshots`, M2 has no tickers to fetch Form 4s
for, M5 has no rows to compute timing on, etc.

The most important thing M1 introduces is the concept of a **snapshot**.
Every BPC download becomes its own historical record, tagged with the
day it was loaded. Old snapshots are never deleted. This is what lets
later modules detect *date slip* — when BPC's "Q2 2026" guidance for a
drug quietly becomes "Q3 2026" three weeks later, the comparison across
snapshots makes the slip visible.

---

## What it actually does, step by step

When you run `scripts/3_1_ingest_catalysts.py`:

1. **Picks the CSV file.**
   You can pass a path explicitly. If you don't, M1 picks the most
   recent file matching `*catalyst*.csv` (case-insensitive substring)
   at the top of `_csv_source/`. The substring filter is on purpose —
   the same folder also holds `insider_data3.csv` for Module 4, and
   the previous "most recent of any .csv" rule kept accidentally
   grabbing that one.

2. **Picks the snapshot date.**
   `--snapshot-date YYYY-MM-DD` if you pass one. Otherwise today
   (UTC). This date is stamped into every row inserted by this run.

3. **Opens the database** (creating tables if they don't yet exist —
   so M0 is implicitly re-applied on every connect; idempotent).

4. **Reads the CSV with UTF-8-SIG** so a Byte-Order-Mark at the start
   of the file (Excel-saved CSVs often have one) is silently consumed
   instead of getting fed into the first column name as garbage.

5. **Validates the header — hard fail mode.**
   The header must contain exactly the 19 columns from spec §3.3
   (case-sensitive, order-independent). Any missing or unexpected
   column → `SchemaValidationError` is raised, no rows are written, a
   `failed` row is logged to `ingest_log`. This is what prevents
   M1 from chewing on a wrong-shaped file (we already have an
   `insider_data3.csv` rejection in `ingest_log` proving the guard
   works).

6. **Pre-loads the existing PKs** for this snapshot date.
   One quick `SELECT` gathers every `(ticker, drug, nct_number,
   next_catalyst_type)` tuple already in the table at this date.
   This is how M1 cleanly separates "new row" from "row that was
   already there" without trying to interpret SQLite's `INSERT OR
   REPLACE` row counts.

7. **For every CSV row**, in order:
   - Renames the CSV column names to model field names via a static
     dict (`Next Catalyst` → `next_catalyst_type`, etc.).
   - Hands the row to **pydantic v2** (`CatalystRow`, strict +
     extra-forbid).
   - Per-field `mode='before'` validators do the type coercion: strip
     whitespace, uppercase the ticker, parse `5.82485E+11` to float,
     parse `24/05/2026` to a `date`, etc.
   - If anything fails (e.g. `Stage = "phase99"`, or
     `Historical LOA = "150"`), the row is logged with its row number
     + ticker + the pydantic error message, then **skipped** — other
     rows continue.

8. **Writes valid rows in a single SQLite transaction.**
   `INSERT OR REPLACE INTO catalyst_snapshots ...`. The whole thing
   either commits or rolls back together. Per row, the pre-loaded PK
   set is consulted to bump either `rows_inserted` or `rows_updated`.

9. **Writes the `ingest_log` row** (always — success, partial, or
   failed). This happens outside the main transaction so the audit
   trail survives even when the inserts rolled back.

10. **Copies the CSV to the archive** at
    `_csv_source/archive/<snapshot_date>_<original_filename>`. Only
    runs on `success` or `partial` status — and skipped entirely on
    `--dry-run`. The source artifact is preserved in case BPC takes
    the file down or replaces it silently.

11. **Prints a summary** to stdout: `rows_in`, `rows_inserted`,
    `rows_updated`, `rows_rejected`, status. With `-v`, also prints
    the first 20 rejection reasons.

---

## The three kinds of failure (very important distinction)

The spec is strict in one place and tolerant in two others. Knowing
which is which saves a lot of confusion when something looks off in
`ingest_log`.

| Severity | When | What happens | `ingest_log` status |
|---|---|---|---|
| 🔴 **Header-level** — hard fail | Missing or unexpected CSV column | Raises `SchemaValidationError`. **Zero rows written.** The whole CSV is rejected. | `failed` |
| 🟡 **Row-level** — row-skipping | A field fails a non-tolerated validator: bad `Stage`, malformed number that isn't a recognised sentinel, etc. | The single row is dropped with a warning. Other rows continue. | `partial` (if any row rejected) or `success` (if none) |
| 🟢 **Cell-level** — soft normalisation | A field that's *expected* to sometimes be messy: unparseable `Catalyst Date`, em dash in `Historical LOA`, blank `Next Catalyst` | The cell is set to NULL (or `''` for the PK string fields), a warning is logged. **The row is still ingested.** | unaffected |

The cell-level tolerances are deliberate calibrations against real
BPC data — see decisions.md D2 for the list and the rationale.

---

## What gets normalised on its way in

Per spec §3.4, with the real-data tweaks documented in decisions.md D2:

| CSV column | Becomes | Normalisation rule |
|---|---|---|
| `Ticker` | `ticker` | upper-cased, whitespace stripped, required |
| `Drug` | `drug` | blank → `''` (PK column, can't be NULL) |
| `NCT Number` | `nct_number` | blank → `''` (PK column) |
| `Next Catalyst` | `next_catalyst_type` | blank → `''` (PK column; spec deviation, see D2) |
| `Stage` | `stage` | lower-cased; must match `^phase[0-5]$` else row rejected |
| `Catalyst Date` | `catalyst_date` | parsed `DD/MM/YYYY` → ISO; unparseable → NULL + warn |
| `Historical LOA`, `Historical POP` | (same names) | 0–100 float; **em dash `—`**, `-`, `n/a`, `N/A`, `NA` → NULL |
| `Market Cap` | `market_cap_usd` | `5.82485E+11` parses via plain `float()` |
| `Last Updated` | `bpc_last_updated` | `DD/MM/YYYY HH:MM` → ISO timestamp |
| `No Of Shares` | `no_of_shares` | int; tolerates commas (`1,000,000`) |
| `30 Day Price Change` | `price_history_30d` | preserved verbatim as a semicolon-separated string |
| (other text columns) | (mapped) | blank → NULL |

The two key things to internalise:

- **`Stage` is the only string field that can reject a row.** Everything
  else either coerces cleanly or NULLs out softly.
- **The em dash `—` is BPC's "not applicable" sentinel** for big-pharma
  rows where historical base rates don't make sense. M1 recognises it
  alongside ASCII `-`, `n/a`, `N/A`, `NA` and `''`. Do not add new
  sentinels casually; that list should change only when you've
  confirmed with BPC what a new value actually means.

---

## What it explicitly does NOT do

- ❌ It does not reach out to the internet at all.
- ❌ It does not compute the actual catalyst timing (that's Module 5's
  whole purpose).
- ❌ It does not enrich with insider data, Form 4 filings, or fund
  holdings.
- ❌ It does not validate that NCT numbers actually exist on
  ClinicalTrials.gov.
- ❌ It does not deduplicate across snapshots. Loading the same file
  with a new `--snapshot-date` deliberately creates a new historical
  snapshot — that's how date-slip tracking will work later.
- ❌ It does not auto-trigger Module 5. You run timing extraction as a
  separate explicit step so failures stay isolated.

---

## How to run it

From the parser folder, with the shared venv active:

```bash
# Default: most recent *catalyst*.csv in _csv_source/, snapshot = today UTC
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_1_ingest_catalysts.py

# Explicit path + back-dated snapshot
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_1_ingest_catalysts.py \
    _csv_source/biotech_catalysts_v3.csv --snapshot-date 2026-05-20

# Dry-run: validate + count, but don't write to DB or archive
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_1_ingest_catalysts.py --dry-run -v
```

Or just run `run_3_Biopharmcatalyst_parser.bat` — M1 is wired in with a
y/N gate immediately after the M0 schema bootstrap.

To verify the loader still meets the spec, run the test suite:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_ingest_catalysts.py -v
```

16 tests: synthetic CSV fixtures for the edge cases (missing header
column, bad Stage, em dash, blank NCT, etc.) plus two acceptance tests
against the real `biotech_catalysts_v3.csv` pinning the 600-in →
572-distinct contract.

---

## The four files M1 is made of

```
src/module_1/
  __init__.py            # empty — makes 'module_1' importable
  csv_schema.py          # pydantic CatalystRow + per-field validators
  ingest.py              # header check, transaction, archive, ingest_log

scripts/
  3_1_ingest_catalysts.py  # CLI entry-point you actually run

tests/
  test_ingest_catalysts.py # 16 acceptance tests
```

**`csv_schema.py`** holds three exports: the expected column set
(`EXPECTED_COLUMNS`), the CSV-column-to-field-name map
(`CSV_TO_FIELD`), and the `CatalystRow` pydantic model itself. The
field validators are all `mode='before'` because pydantic's strict mode
refuses silent type coercion — we do every string→native conversion
ourselves so the failure modes are explicit.

**`ingest.py`** is the orchestration: header validation,
existing-PK pre-load, the per-row pydantic loop with rejection
collection, the transactional upsert, the post-success archive copy,
and the always-runs `ingest_log` write. Exposes `ingest_catalyst_csv()`
as the testable entry-point.

**`3_1_ingest_catalysts.py`** is the thin CLI: argparse, default-CSV
picker, basic logging setup, calls into `ingest.py`, prints the stats
summary, picks the exit code.

**`tests/test_ingest_catalysts.py`** uses pytest `tmp_path` fixtures so
no test ever touches `data/biotech.db`. Each test gets a fresh
throwaway SQLite file. The two real-CSV tests are skipped automatically
if `_csv_source/biotech_catalysts_v3.csv` is missing.

---

## When you'll need to re-run M1

- **Weekly**, when BPC publishes a fresh FDA-catalyst CSV. The new
  snapshot lands alongside old ones; nothing is overwritten.
- **Re-loading the same file with the same `--snapshot-date`** is a
  safe no-op — 0 inserts, N updates. Useful if you want to confirm
  the file hasn't drifted since last load.
- **Re-loading the same file with a *different* `--snapshot-date`**
  intentionally creates a second historical snapshot. Use this if
  you want to back-fill from an archived CSV that you forgot to load
  on the day.
- **After updating any cell-level normaliser** (e.g., adding a new
  blank-sentinel string). Re-run all snapshots? Probably not worth
  it for a sentinel tweak; the affected rows will already have NULL
  values that re-running can't improve. But for a real schema change,
  re-load every archived file in chronological order.

---

## Why these design choices

A few that aren't obvious from the code:

- **Pydantic v2 with `strict=True`, validators in `mode='before'`.**
  Strict mode disallows silent coercion: `int` field rejects `"5"`.
  That's the safety. The before-validators do the explicit string-to-
  native conversion, raising clear ValueErrors with the offending
  value. The result is: a row either passes with fully-typed fields,
  or fails with a precise message about which field and why.

- **Header check happens *before* any DB write.**
  Once-and-for-all rejection of a wrong-shaped file is far more
  helpful than partial inserts you have to clean up later. The
  `ingest_log` row for the failure is still written, so misaimed runs
  show up in the audit trail.

- **`INSERT OR REPLACE`, not `INSERT OR IGNORE`.**
  BPC fixes typos in their CSV between weeks (a stale catalyst date,
  a corrected drug name). With `OR REPLACE`, re-loading the same
  snapshot date with the corrected file overwrites the old row. With
  `OR IGNORE`, the bad data would be sticky. The cost is that a row
  ingested under buggy normalisation rules is silently re-written
  with new values on the next run — generally desirable, but worth
  knowing.

- **`ingest_log` written even on failure.**
  Mandatory audit trail. The first row in our production DB is a
  `failed` entry for someone (me) accidentally pointing M1 at
  `insider_data3.csv` — proves the header guard works and gives the
  pattern for diagnosing future misaims.

- **Archive copy on success only.**
  A `failed`-status run did not produce any row in the DB, so
  preserving the source file would just create archive noise. A
  `partial` run did insert some rows, so the source IS archived — you
  need it to investigate what went wrong with the rejected ones.

- **Default CSV picker filters on filename substring `catalyst`.**
  The wider rule "most recent .csv" picked up `insider_data3.csv` and
  got a hard fail (correct, but annoying UX). The substring narrows
  the no-arg default to what M1 actually wants without forcing the
  user to type a path every time.

---

## Reading the production DB after a run

After `scripts/3_1_ingest_catalysts.py --snapshot-date 2026-05-27`,
`data/biotech.db` should look like this:

```bash
../.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('data/biotech.db'); c.row_factory = sqlite3.Row

# How many rows for the latest snapshot, broken out by stage
print('--- catalyst_snapshots, latest snapshot, by stage ---')
for r in c.execute(\"\"\"
  SELECT stage, COUNT(*) AS n
  FROM catalyst_snapshots
  WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM catalyst_snapshots)
  GROUP BY stage ORDER BY n DESC
\"\"\"):
    print(f'  {r[\"stage\"]:10s} {r[\"n\"]:>4d}')

# The audit trail
print('--- ingest_log ---')
for r in c.execute('SELECT run_id, status, rows_in, rows_inserted, rows_updated, rows_rejected, input_ref FROM ingest_log ORDER BY run_id'):
    print(f'  run {r[\"run_id\"]:2d} {r[\"status\"]:8s}  in={r[\"rows_in\"]:>4d} ins={r[\"rows_inserted\"]:>4d} upd={r[\"rows_updated\"]:>4d} rej={r[\"rows_rejected\"]:>3d}  {r[\"input_ref\"]}')
"
```

On a fresh-from-`biotech_catalysts_v3.csv` load you'll see ~572 rows
split across `phase1` (the biggest bucket), `phase2`, `phase3`, etc.,
and the latest `ingest_log` row showing
`in=600 ins=572 upd=28 rej=0` with status `success`.

---

## TL;DR

Module 1 turns a 600-row BPC catalyst CSV into 572 distinct
`catalyst_snapshots` rows (28 are within-CSV duplicates, correctly
deduped on the PK). Strict header check, tolerant cell-level
normalisation, fully idempotent on re-run, always logs to `ingest_log`,
archives the source file on success. Run it weekly when BPC publishes
a new file. Every other module reads from what M1 wrote.
