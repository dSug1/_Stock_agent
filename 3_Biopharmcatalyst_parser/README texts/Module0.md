# Module 0 — Bootstrap (docx→csv + database)

**Plain-English purpose:** Module 0 does TWO things before the rest of
the pipeline can run:

- **Module 0a** — convert any new BPC `.docx` exports in `_csv_source/`
  into the 19-column `.csv` files M1 ingests. This is what makes "drop
  the docx in, run the pipeline" work; you no longer convert docx → csv
  by hand.
- **Module 0b** — create the empty SQLite file (`data/biotech.db`) and
  the eight tables every later module reads from and writes to.

If you think of the pipeline as a building, **Module 0a delivers the
raw materials and Module 0b pours the foundation and lays out the empty
rooms.** Modules 1–6 move furniture in. Nothing is "ingested" yet, no
network calls, no calculations — M0 is pure plumbing.

Both sub-modules are idempotent: re-running them on an already-prepared
project does nothing visible (the CSVs are skipped because they're
already fresh; the database is opened-and-closed without modifying
anything).

---

## Module 0a — `.docx` → `.csv` conversion

### Why this exists

The user gets catalyst data from BPC's website by selecting their data
table and pasting it into a Word document. The resulting `.docx`
contains the **raw HTML** of the table inside its paragraph text —
not a native Word table. Up until D11, we asked the user to also do
the `docx → csv` conversion by hand using Word's "Save as CSV" or a
script outside the pipeline. That manual step has two failure modes:

1. **Format drift.** Different conversion routes produce dates in
   different formats (DD/MM/YYYY vs ISO YYYY-MM-DD). This bit us in D11
   — the v3 CSV used DD/MM/YYYY but the v4 CSV switched to ISO,
   silently NULL-ing the `catalyst_date` column on the first v4
   ingest.
2. **Noise pollution.** The visible cell text contains UI cruft like
   `"$213.12 -2.58 -1.20%"` for prices, `"FTD"` (Fast Track
   Designation) badges appended to drug names, and `"… read more"`
   truncation on long catalyst texts. A naive text-extraction process
   picks up all of it.

M0a solves both: it always pulls from BPC's `blurred-text` HTML
attribute (the canonical sort/copy value used by their JS), so every
docx produces a structurally identical CSV regardless of when the
user exported it.

### What it actually does, step by step

When you run `scripts/3_0_convert_docx_to_csv.py`:

1. **Scans `_csv_source/` for `*.docx` files** (skipping Word's
   `~$lock` files). For each docx, decides whether to convert:
   - CSV missing → convert.
   - CSV newer than docx → skip (the CSV is already fresh).
   - CSV older than docx OR `--force` passed → reconvert.

2. **Opens the docx via `python-docx`** and concatenates the text of
   every paragraph. That concatenated string is the raw HTML BPC
   placed in the document.

3. **Parses the HTML with BeautifulSoup** and locates the first
   `<table>` and its `<tbody>`. Walks every `<tr>` and reads the 20
   `<td>` cells.

4. **For each `<td>`, applies one of four extraction modes** depending
   on the target CSV column:

   | Mode | Behaviour |
   |---|---|
   | `blurred_or_text` | If the cell contains a `<div blurred-text="…">`, return that attribute. Else, return the visible text. Used for Ticker, Name, Price, Drug, Stage, Catalyst Date, Catalyst, Historical LOA, Historical POP, Market Cap, Last Updated, No Of Shares. |
   | `text` | Plain visible text, whitespace collapsed. Used for NCT Number, Indication, Status, Next Catalyst, Conference. |
   | `price_history` | The 30-day price column's `blurred-text` is `"price,unix_ts,price,unix_ts,…"` — keep the even-indexed values (the prices) and join them with `"; "`. |
   | `sentiment` | Parse `"Community NN% NN% NN%"` from the visible text and format as `"Bull X% / Neutral Y% / Bear Z%"`. Defaults to `"Bull -% / Neutral -% / Bear -%"` when there's no community vote. |

5. **Drops docx column 9 ("Options")** — that's a "View" hyperlink
   column with no data, not in the M1 CSV schema. So 20 docx columns
   become 19 CSV columns.

6. **Writes the CSV** to the docx's sibling path (replacing the `.docx`
   extension with `.csv`). UTF-8, comma-separated, double-quoted where
   needed, with the same 19-column header M1 expects.

7. **Reports counts** — rows written and rows skipped (a row is
   skipped only if it has fewer than 20 cells; in practice this never
   happens for real BPC exports).

### Drug and Catalyst columns are special

These two columns **must** use `blurred-text` (not visible text):

- **`Drug`** — the visible text appends FDA-designation badges like
  `"FTD"` (Fast Track), `"BTD"` (Breakthrough Therapy), `"ODD"` (Orphan
  Drug), and the link text `"View Clinical Trial Data"`. Using the
  visible text breaks the M1 primary key — a row that was previously
  ingested as `Emraclidine (CVL-231)` would re-ingest as a NEW row
  `Emraclidine (CVL-231) FTD` after BPC's badge classification
  changes. The `blurred-text` attribute always carries just the
  drug name.

- **`Catalyst`** — the visible text is truncated by BPC's UI at a
  fixed length with a `"… read more"` suffix. Long catalyst texts
  lose their full body. The `blurred-text` attribute carries the
  complete unedited text.

This is why the converter ships defaults that favour `blurred-text`
heavily; an earlier draft that used visible text broke the PK on 129
of 600 rows because of FDA-badge changes.

### How to run M0a

```bash
# Auto-scan _csv_source/, convert anything new (called by the .bat)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_convert_docx_to_csv.py

# Reconvert everything even if CSVs are already fresh
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_convert_docx_to_csv.py --force

# Convert a single docx (e.g. dropped outside _csv_source/)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_convert_docx_to_csv.py \
    /path/to/biotech_catalysts_v5.docx --out /path/to/biotech_catalysts_v5.csv

# Verbose
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_convert_docx_to_csv.py -v
```

### Test coverage

`tests/test_docx_converter.py` — 22 tests:

- 8 unit tests on the cell-level extractors (`blurred_or_text`
  prefer/fallback, whitespace collapsing, price-history parsing,
  sentiment regex, no-vote default).
- 4 invariants on `COLUMN_MAP` (19 columns, docx col 9 dropped, valid
  modes, Drug+Catalyst use `blurred_or_text`).
- 6 row-level tests on a synthetic 20-cell row covering each special
  case (FDA-badge stripping, ISO dates, integer-string market cap,
  semicolon-joined price history, sentiment formatting).
- 4 end-to-end tests against the real `biotech_catalysts_v3.docx` and
  `biotech_catalysts_v4.docx` (600 + 100 rows, every Catalyst Date is
  ISO, no Drug contains a stripped FDA badge fragment).

### Acceptance against the live docx files

Running on `_csv_source/biotech_catalysts_v3.docx` produces a 600-row
CSV whose PKs (Ticker, Drug, NCT Number, Next Catalyst) match the
prior manually-converted v3.csv 572-for-572 (28 within-CSV dupes
coalesce in M1, as before). Same for v4: 100/100 PK match.

Field-level diffs against the prior CSVs are limited to known-equivalent
representations:

- `Price`: `"12.51"` vs `"12.5100"` (parsed identically by `float()`).
- `Catalyst Date`: `"31/12/2026"` (DD/MM/YYYY) vs `"2026-12-31"`
  (ISO). M1 accepts both formats per D11.
- `Last Updated`: `"26/05/2026 08:21"` vs `"2026-05-26 08:21:02"`
  (ISO + seconds). M1 accepts both per D11.

Going forward, all CSVs produced by M0a use ISO format consistently.

---

## Module 0b — Database bootstrap

**Plain-English purpose:** Module 0b creates the empty SQLite file that
every other module will read from and write to. It is the **only**
module that creates tables; every later module only inserts/updates rows
inside the structure M0b lays down.

---

### What M0b actually does, step by step

When you run `scripts/3_0_init_db.py`:

1. **Decides where the database file should live.**
   Default: `data/biotech.db` (resolved relative to the parser folder).
   You can override with `--db-path` for testing.

2. **Creates the `data/` folder** if it doesn't exist.
   So a fresh clone of the repo can run M0 without you having to
   `mkdir` first.

3. **Opens (or creates) the SQLite file** via Python's `sqlite3` stdlib
   module. SQLite creates the file automatically on first connect.

4. **Turns foreign-key enforcement on.**
   SQLite's default is *off* — a surprising and dangerous default. Two
   of our tables (`catalyst_timing`, `edgar_form4_transactions`)
   declare foreign keys back to their parents. Without `PRAGMA
   foreign_keys = ON`, those FKs are syntactically accepted but never
   enforced — you could insert orphan rows and SQLite would not
   complain. M0 sets this pragma on every connection.

5. **Runs `schema.sql`** which is a flat SQL file containing every
   `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`
   statement. The `IF NOT EXISTS` is what makes M0 idempotent — running
   it twice does not error and does not change anything the second
   time.

6. **Reports what it found / created**, table by table:

   ```
   [3_0_init_db] DB: .../data/biotech.db
   [3_0_init_db] created; 8 table(s) present
     [OK ] catalyst_snapshots
     [OK ] edgar_form4_filings
     [OK ] edgar_form4_transactions
     [OK ] edgar_ownership_filings
     [OK ] bpc_insider_supplement
     [OK ] ingest_log
     [OK ] ticker_cik_map
     [OK ] catalyst_timing
   ```

   On a re-run, the second line changes to `opened existing; 8 table(s) present`.

---

## What the 8 tables are for

These are the empty containers M0 creates. Module 0 itself never
populates them; the modules listed below do.

| Table | Holds | Filled by |
|---|---|---|
| `catalyst_snapshots` | Every row of every BPC catalyst CSV you've ever loaded, tagged with the snapshot date you loaded it on. The biological event itself: ticker, drug, NCT, stage, raw catalyst text, etc. | Module 1 |
| `catalyst_timing` | The derived `(date_min, date_max, precision_tier)` for every catalyst — the answer to "when is this actually expected?" after parsing the catalyst text and the BPC bucket placeholders. | Module 5 |
| `edgar_form4_filings` | One row per Form 4 filing (the parent) — issuer CIK, reporting insider, filing date. | Module 2 |
| `edgar_form4_transactions` | One row per non-derivative transaction inside a Form 4 — date, code (P/S/A/M/F/…), shares, price, `is_open_market` flag. | Module 2 |
| `edgar_ownership_filings` | Metadata for SC 13D/13G filings — who filed, what form type, when. (Percent-of-class parsing is deferred.) | Module 3 |
| `bpc_insider_supplement` | The manually-extracted BPC insider trading CSV, kept separate from EDGAR-derived data so disagreements stay auditable. | Module 4 |
| `ticker_cik_map` | Cached lookup of ticker → 10-digit CIK so we don't hit SEC for every resolution. | Module 2 (refreshed weekly) |
| `ingest_log` | One audit row per pipeline run — module name, row counts, status (`success` / `partial` / `failed`), runtime, source file. | Every module |

Plus **11 indexes** for query speed (ticker lookups, date ranges,
precision-tier filters, etc.). You never query the indexes directly;
SQLite uses them transparently.

---

## What it explicitly does NOT do

- ❌ It does not download anything from the internet.
- ❌ It does not validate the contents of any CSV.
- ❌ It does not ingest a single row of data.
- ❌ It does not run any business logic.
- ❌ It is **not destructive** — it never drops or recreates an existing
  table. Schema edits must be additive (see "Changing the schema"
  below) or done by hand.

---

## How to run it

From the parser folder, with the shared venv active:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_init_db.py
```

Or just run `run_3_Biopharmcatalyst_parser.bat` from the parser folder —
M0 is the first step and runs automatically (no y/N gate, because it's
free and idempotent).

To verify the schema matches the spec exactly, run the test suite:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_db.py -v
```

This checks all 8 tables exist, every column is in the order the spec
declares, every primary key (single and composite) is correct, the
foreign-key pragma is on, and a re-open doesn't error.

---

## The three files M0 is made of

```
src/database/
  __init__.py          # empty — makes 'database' an importable package
  db.py                # the get_connection() helper, ~30 lines
  schema.sql           # all CREATE TABLE / CREATE INDEX statements

scripts/
  3_0_init_db.py       # the CLI entry-point you actually run

tests/
  test_db.py           # 19 parametrized checks against the spec
```

**`db.py`** exports one function — `get_connection(db_path=None)` — that
every other module in the project will call. It does the four steps
above (path resolution, pragma, schema.sql, return). Mirrors the exact
pattern used by `2_Funds_parser/src/database/db.py` so anyone moving
between the two projects sees the same shape.

**`schema.sql`** is a flat file (not generated, not templated) where
the source of truth for the schema lives. Edit it directly to evolve
the schema — but additively. SQL is the source of truth here; pydantic
enters the picture later, only at the CSV-ingest boundary.

**`3_0_init_db.py`** is the user-facing script — it calls
`get_connection()`, inspects which tables actually came out, and prints
a per-table OK/MISSING report. Exits non-zero if any expected table is
missing (defensive: catches a `schema.sql` save that accidentally
dropped a `CREATE TABLE` statement).

---

## When you'll need to re-run M0

- **First setup of a fresh clone.** Mandatory.
- **After editing `schema.sql`** to add a new column or table — the
  `IF NOT EXISTS` guard means the new bits get applied without
  touching existing data.
- **Before running any other module** if `data/biotech.db` is missing
  (e.g., you deleted it to start clean). Any other module would fail
  with a "no such table" error otherwise.

You never have to re-run M0 between dispatches under normal operation.
The .bat runs it every time, but that just confirms the schema is
intact — it's a fast no-op.

---

## Changing the schema (the rules)

The schema is meant to evolve **additively only.** Specifically:

- ✅ **Adding a new column** is fine — append `ALTER TABLE ... ADD
  COLUMN ...` logic in `db.py` (see how `2_Funds_parser`'s
  `_apply_additive_migrations()` does it). Or, for a brand-new column
  on an empty DB, just edit `schema.sql` directly.
- ✅ **Adding a new table** — just add a `CREATE TABLE IF NOT EXISTS`
  block to `schema.sql`. Then update `EXPECTED_TABLES` in
  `3_0_init_db.py` and the `EXPECTED` dict in `tests/test_db.py`.
- ✅ **Adding a new index** — add `CREATE INDEX IF NOT EXISTS` to
  `schema.sql`.
- ⚠️ **Renaming a column** — requires a real migration. SQLite's
  `ALTER TABLE ... RENAME COLUMN` works on 3.25+, but the pattern is
  not yet wired here. Open a decision-log entry first.
- ⚠️ **Dropping a column** or table — same. Don't quietly delete
  things; the spec then disagrees with reality. Either edit the spec
  first and add a `DROP COLUMN` migration, or leave the column unused.
- ❌ **Changing a column's type** is the worst case in SQLite — needs a
  table rebuild. Avoid; if it's unavoidable, write a one-off migration
  script.

---

## Why these design choices (and not others)

A few of them are worth knowing because they came up in design
discussions:

- **One SQLite file, not one per module.** All eight tables share
  `data/biotech.db`. Cross-table joins (which Module 5 and downstream
  modules will lean on) are trivial when everything is in one file.
  Splitting into multiple `.db` files would force `ATTACH DATABASE`
  gymnastics for no real benefit at our scale.

- **No SQLAlchemy, no Alembic, no ORM.** Plain `sqlite3` stdlib with
  hand-written SQL. Reasons: 8 tables and a single developer don't
  justify the ORM tax; the spec is in SQL anyway; migrations are
  rare. If we ever ship to multiple environments, revisit.

- **No pydantic models for the schema.** Pydantic enters at the CSV
  boundary in Modules 1 and 4 where row validation actually happens.
  Layering pydantic over the DDL would duplicate the schema with no
  added safety.

- **`PRAGMA foreign_keys = ON` on every connect.** SQLite's off-by-
  default behaviour has bitten people. Every project-managed
  connection enables FKs so the FK declarations in `schema.sql` are
  not just decoration.

- **`data/biotech.db` is gitignored** (via the repo-wide `*.db` rule).
  You'll never accidentally commit it. A fresh clone always starts
  with an empty schema; M0 builds it from the SQL.

---

## Verifying everything is healthy

Quick one-liner to inspect the database from the shell:

```bash
../.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('data/biotech.db')
for t in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\"):
    n = c.execute(f'SELECT COUNT(*) FROM {t[0]}').fetchone()[0]
    print(f'  {t[0]:30s} {n:>6d} rows')
"
```

On a fresh M0 run with nothing ingested yet, every count is 0 except
possibly `ingest_log` (which has one row per run, including failed
attempts). After Module 1 has ingested the reference CSV, you should
see `catalyst_snapshots: 572 rows`.

---

## TL;DR

Module 0 makes sure `data/biotech.db` exists with the right empty
tables. It is idempotent, free, fast, and the first step of every
pipeline run. Everything downstream assumes it has already happened.
