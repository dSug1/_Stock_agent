# Module 4 — BPC insider supplement ingest

**Plain-English purpose:** Module 4 loads BPC's pre-cleaned insider-
trading CSV (the file Andre extracts by hand on a manual cadence) into
the `bpc_insider_supplement` table, where it lives **deliberately
separate** from the EDGAR Form 4 data that Module 2 will eventually
produce. The two feeds describe the same underlying truth — what
insiders bought and sold — but from different sources. Keeping them
side-by-side instead of merging at ingest time means any disagreement
between BPC and EDGAR is auditable, not silently resolved.

The module also creates the **two cross-validation SQL views** that
let downstream code read either feed alone or both together as a
unified stream.

---

## Why M4 exists alongside the (future) M2 EDGAR Form 4

The two feeds are not redundant:

- **EDGAR Form 4** is the legal record. Filed within 2 business days
  of every insider transaction. Comprehensive but noisy: includes
  derivative grants, tax-withholding sales, indirect-ownership
  reclassifications, and 6–8 other transaction codes that aren't
  open-market trades.
- **BPC's insider feed** is human-curated. Andre's view is that BPC
  drops the noise and ships only the rows that actually represent
  "an insider chose to buy/sell at a price." Fewer rows, higher
  signal — but only as recent as the last manual extraction.

When they agree, that's confirmation. When they disagree (e.g., BPC
shows a buy that EDGAR doesn't, or vice versa), the disagreement is
itself information — possibly a BPC oversight, possibly an EDGAR
late-filing, possibly a transaction that BPC chose to categorise
differently. The `source` column in the combined view lets analysts
spot these.

Hence: never merge at ingest time. Always keep both tables raw and
union at query time via `v_insider_signal_combined`.

---

## What it actually does, step by step

When you run `scripts/3_4_ingest_bpc_insider.py`:

1. **Picks the CSV file.**
   You can pass a path explicitly. If you don't, M4 picks the most
   recent `*insider*.csv` (case-insensitive substring) at the top of
   `_csv_source/`. The substring narrows the default away from
   Module 1's `*catalyst*.csv` so the two modules can never
   accidentally swap inputs.

2. **Picks the snapshot date.**
   `--snapshot-date YYYY-MM-DD` if you pass one, otherwise today
   (UTC). The date is stamped into every inserted row.

3. **Opens the database** (creating tables and views if they don't
   yet exist — M0's bootstrap re-applies on every connect).

4. **Reads the CSV with UTF-8-SIG**, consuming any leading BOM.

5. **Validates the header — hard fail mode.**
   The header must contain exactly the 13 columns from spec §6.3.
   Any missing or unexpected column → `SchemaValidationError` is
   raised, no rows are written, a `failed` row is logged to
   `ingest_log` (`module = 'bpc_insider'`).

6. **Pre-loads the existing PKs** for this snapshot date — same
   trick as Module 1, so `rows_inserted` vs `rows_updated` is
   tracked cleanly.

7. **For every CSV row**, in order:
   - Renames the CSV columns to model fields via a static dict.
   - Hands the row to pydantic v2 (`InsiderRow`, strict +
     extra-forbid).
   - The `Buy/Sell` and `Stock/Option` fields use `Literal` types
     so any value outside the allowed set raises immediately.
   - Per-field `mode='before'` validators do the type coercion
     (strip, uppercase ticker, parse ISO date, parse floats and
     ints, tolerate comma-separated thousands).
   - Failed rows are logged with row number + ticker + reason,
     then skipped.

8. **Writes valid rows in a single SQLite transaction.**
   `INSERT OR REPLACE INTO bpc_insider_supplement` against the
   **8-column PK** (see "The PK widening" below). Per row, the
   pre-loaded PK set bumps either `rows_inserted` or `rows_updated`.

9. **Writes the `ingest_log` row** with `module = 'bpc_insider'`,
   the source filename as `input_ref`, and the row counts.

10. **Copies the CSV to the archive** at
    `_csv_source/archive/<snapshot_date>_<original_filename>`. Same
    convention as Module 1; skipped on dry-run; skipped on `failed`
    status.

11. **Prints a summary** to stdout: `rows_in`, `rows_inserted`,
    `rows_updated`, `rows_rejected`, status. With `-v`, also the
    first 20 rejection reasons.

---

## The three kinds of failure (same taxonomy as M1)

| Severity | When | Effect | `ingest_log.status` |
|---|---|---|---|
| 🔴 **Header-level** — hard fail | Missing or unexpected CSV column | Raises `SchemaValidationError`. **Zero rows written.** | `failed` |
| 🟡 **Row-level** — row-skipping | `Buy/Sell` not Buy/Sell; `Stock/Option` not Stock/Option; blank `Insider Name`; unparseable `Filing Date`; non-numeric `Shares`/`Trade Price`/etc. | Row dropped with a warning; other rows continue. | `partial` (if any rejected) else `success` |
| 🟢 **Cell-level** — soft normalisation | Blank `Insider Position` (~28% of real rows); blank `Name` | Cell stored as NULL. **Row is still ingested.** | unaffected |

In contrast to M1, M4's `Filing Date` is **required** and `Buy/Sell` /
`Stock/Option` are **strict enums**. That's because the BPC insider
feed is pre-cleaned by a human — these fields should never be
ambiguous, and silent NULL-on-fail would mask a real data-quality
problem.

---

## What gets normalised on its way in

Per spec §6.4:

| CSV column | DB column | Rule |
|---|---|---|
| `Ticker` | `ticker` | upper-cased, whitespace stripped, required |
| `Name` | `name` | company name; blank → NULL |
| `Insider Name` | `insider_name` | required, non-empty |
| `Insider Position` | `insider_position` | blank → NULL (real data: ~28% blank) |
| `Filing Date` | `filing_date` | parsed `YYYY-MM-DD` → ISO; **unparseable rejects the row** |
| `Buy/Sell` | `buy_sell` | literal `Buy` or `Sell`; nothing else |
| `Stock/Option` | `stock_or_option` | literal `Stock` or `Option`; nothing else |
| `Shares` | `shares` | float (BPC ships 8 decimal places of precision) |
| `Shares Change` | `shares_change_pct` | float, already a percentage in source |
| `Trade Price` | `trade_price` | float; `0` allowed (option grants/exercises) |
| `Cost` | `cost` | float; `0` allowed (same reason as Trade Price) |
| `Final Share` | `final_shares` | int; **required** — it's now in the PK |
| `No Of Shares` | `no_of_shares` | int (issuer's total shares outstanding) |

---

## The PK widening (the most important spec deviation)

The original spec PK was the 7-column tuple
`(snapshot_date, ticker, insider_name, filing_date, buy_sell, stock_or_option, shares)`.

When loaded against the real 1,608-row `insider_data3.csv`, two
patterns of within-CSV PK collision surfaced:

| Pattern | Buckets | Rows total | What's the right behaviour? |
|---|---:|---:|---|
| Bit-for-bit duplicates — every field identical | 52 | 104 | Coalesce (same as M1's catalyst-CSV pattern) |
| **Distinct rows where only `final_shares` differs** | 7 | 14 | **Preserve** |

Concrete examples of the second pattern:

- **INM / ADAR Capital Management LLC** — bought 200,000 shares on
  2026-05-19 in two transactions at different prices (`$1.5604` and
  `$1.499`), ending at different positions (600k and 800k shares).
  Same insider, same day, same total shares, but two legitimately
  distinct trades.
- **STAA / Warren Foust** — multiple same-day same-price stock-grant
  exercises that finished at different post-trade positions (21,993,
  29,324, 81,450, 85,051 after a 7,331-share Buy at $0). Likely
  different grant tranches all settling on the same day.

The fix: add `final_shares` to the PK. The 7 distinct buckets now
survive; the 52 bit-for-bit dupes still collapse (they're identical
on `final_shares` too).

Spec §2.5 + §6.5 + §6.6 were updated to reflect the 8-column PK.
`final_shares` is now `NOT NULL` since PK columns can't be NULL.

**Net contract:** 1,608 CSV rows → **1,556 distinct DB rows** + 52
counted as `rows_updated` (the within-CSV dupes).

---

## The two cross-validation views (M4's other deliverable)

Spec §6.7 — both defined in `schema.sql` so M0's bootstrap creates
them on every connect. Always re-created via `DROP VIEW IF EXISTS`
then `CREATE VIEW`, since SQLite views have no body-altering ALTER.

### `v_latest_catalysts`

Returns one row per `(ticker, drug, nct_number, next_catalyst_type)`
tuple — specifically the row from the most recent `snapshot_date`.

```sql
SELECT s.*
FROM catalyst_snapshots s
JOIN (
    SELECT ticker, drug, nct_number, next_catalyst_type,
           MAX(snapshot_date) AS latest
    FROM catalyst_snapshots
    GROUP BY ticker, drug, nct_number, next_catalyst_type
) m USING (ticker, drug, nct_number, next_catalyst_type)
WHERE s.snapshot_date = m.latest;
```

Downstream code that wants "the best current understanding of each
catalyst" reads this instead of writing `GROUP BY MAX(snapshot_date)`
itself.

### `v_insider_signal_combined`

UNION of EDGAR Form 4 (open-market only) and BPC insider supplement
(stock only), tagged with a `source` column so disagreements are
visible:

```sql
SELECT 'edgar' AS source, ..., f.accession_number AS source_ref
FROM edgar_form4_transactions t
JOIN edgar_form4_filings f USING (accession_number)
WHERE t.is_open_market = 1

UNION ALL

SELECT 'bpc' AS source, ..., CAST(snapshot_date AS TEXT) AS source_ref
FROM bpc_insider_supplement
WHERE stock_or_option = 'Stock';
```

Three things to know about this view:

1. **Why `is_open_market = 1` on the EDGAR side.** Form 4 transaction
   codes include `P` (purchase) and `S` (sale) as open-market trades,
   but also `M` (option exercise), `F` (tax withholding), `A`
   (grant), and others — none of which are voluntary capital
   commitments. The view restricts to `P` and `S` so the EDGAR side
   matches BPC's editorial slant.

2. **Why `stock_or_option = 'Stock'` on the BPC side.** BPC's option
   rows often have `Trade Price = 0` and `Cost = 0` because the
   option strike isn't disclosed — they don't represent a price
   signal. The view drops them; if you want them, query the raw
   table.

3. **Date column harmonisation.** EDGAR keeps both `filed_date` (the
   SEC-stamped legal record) and `transaction_date` (the trade day —
   usually 1-2 days earlier). BPC only captures `filing_date`. The
   view exposes both EDGAR columns; for BPC, `transaction_date` is
   `NULL`.

4. **`source_ref` lets you trace back.** For EDGAR rows it's the
   accession number; for BPC rows it's the snapshot_date that
   supplied the row. Useful for "where did this exact entry come
   from?" debugging.

After M4 runs against the reference CSV (and before any EDGAR data
lands), the view returns **1,075 rows** — the BPC-stock subset of
1,556 total BPC rows.

---

## What it explicitly does NOT do

- ❌ It does not fetch anything from EDGAR. That's Module 2's job;
  M4 only handles the manually-curated BPC CSV.
- ❌ It does not merge BPC and EDGAR data. The two tables stay
  separate at all times; the view does on-demand unioning at query
  time.
- ❌ It does not deduplicate across snapshots. Loading the same CSV
  with a new `--snapshot-date` creates a new historical record on
  purpose — date-drift between BPC pulls becomes visible.
- ❌ It does not validate that the ticker exists in `catalyst_snapshots`.
  Insider activity in tickers that aren't on the BPC catalyst feed is
  still recorded; you can join later if you want to filter.
- ❌ It does not call `auto-render` or trigger any other module. Run
  it explicitly so each run's `ingest_log` is self-contained.

---

## How to run it

From the parser folder, with the shared venv active:

```bash
# Default: most recent *insider*.csv in _csv_source/, snapshot = today UTC
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_4_ingest_bpc_insider.py

# Explicit path + back-dated snapshot
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_4_ingest_bpc_insider.py \
    _csv_source/insider_data3.csv --snapshot-date 2026-05-20

# Dry-run: validate + count but don't write to DB or archive
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_4_ingest_bpc_insider.py \
    --dry-run -v
```

Or just run `run_3_Biopharmcatalyst_parser.bat` — M4 is gated with a
y/N prompt after Module 5 (per spec §9 build order).

To verify the ingest still meets spec:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_ingest_insider.py -v
```

16 tests: synthetic fixtures for the edge cases (missing header
column, extra column, bad `Buy/Sell`, bad `Stock/Option`, blank
`Insider Name`, unparseable `Filing Date`, option grant with `$0`
trade price, blank `Insider Position`, ingest_log emission, archive
copy) plus two real-CSV tests pinning the §6.6 contract (1,556
distinct after dedup, idempotent re-run). The view's stock-only
filter also has a dedicated test.

---

## The four files M4 is made of

```
src/module_4/
  __init__.py                # empty — makes 'module_4' importable
  csv_schema.py              # pydantic InsiderRow + per-field validators
  ingest.py                  # header check, transaction, archive, ingest_log

scripts/
  3_4_ingest_bpc_insider.py  # CLI entry-point you actually run

tests/
  test_ingest_insider.py     # 16 acceptance tests

src/database/
  schema.sql                 # the two views live here, alongside the tables
```

**`csv_schema.py`** is a deliberate clone of `module_1/csv_schema.py`
with the column set + validator rules swapped for the insider CSV.
The pattern is the same; if you've read M1's explainer, you've
already read M4's. The only structural difference is the use of
`Literal` types for the two enum fields.

**`ingest.py`** is also a near-copy of M1's. Different table name,
different PK shape, different `ingest_log.module` tag (`'bpc_insider'`
instead of `'catalysts'`). Same transactional semantics, same
log-on-failure-too policy.

**`3_4_ingest_bpc_insider.py`** is the thin CLI. Argparse, default-CSV
picker (filters on `insider` substring), basic logging, prints the
summary.

**`tests/test_ingest_insider.py`** uses `tmp_path` fixtures throughout
— no test ever touches `data/biotech.db`. The two real-CSV tests skip
automatically if `_csv_source/insider_data3.csv` is missing.

---

## When you'll need to re-run M4

- **Whenever Andre publishes a fresh BPC insider extract.** The new
  snapshot lands alongside the old ones; nothing is overwritten.
- **Re-loading the same file with the same `--snapshot-date`** is a
  safe no-op (0 inserts, N updates).
- **Re-loading the same file with a different `--snapshot-date`**
  intentionally creates a second historical record — useful for
  back-filling from an archived file.
- **After a schema change** (e.g. the PK widening that happened in
  D4), the old `data/biotech.db` may be incompatible. In dev, the
  cleanest path is to delete `data/biotech.db` and re-run M0 → M1 →
  M5 → M4. In production (once it exists), a proper migration in
  `_apply_additive_migrations` would handle it.

---

## Why these design choices

- **Two separate tables instead of a merged one.** The whole point
  of M4 is that BPC and EDGAR can disagree. Merging at ingest time
  would erase the disagreement; merging at query time (via the view)
  exposes it.

- **PK includes `final_shares`.** Spec §6.5 originally had a 7-column
  PK. Real data showed 7 legitimate distinct buckets where the only
  discriminator was `final_shares` (the post-trade position). Adding
  it preserved those rows; bit-for-bit dupes still collapse because
  they're also identical on `final_shares`. The full rationale and
  the migration story are in `spec/decisions.md` D4.

- **`Buy/Sell` and `Stock/Option` use `Literal` types, not free
  strings.** Anything outside the allowed set is rejected with a
  clear error. The BPC feed is human-curated; if a value drifts,
  that's a real change worth investigating, not a silent
  normalisation.

- **`Filing Date` rejects unparseable values instead of soft-NULLing.**
  Different from M1's `Catalyst Date` (which soft-NULLs). The
  reason: insider rows without a filing date have no analytic value
  — date-anchored queries can't reach them — so silently storing
  NULL would create ghost rows that look real. M1's catalyst rows,
  by contrast, often still carry useful information in the
  conference/text columns even when `Catalyst Date` is missing.

- **`Trade Price = 0` is allowed.** Option grants and exercises
  routinely report a $0 trade price (the strike isn't disclosed
  here, or there's no strike at all). The check is whether the
  value parses as a float; zero counts.

- **Views in `schema.sql`, not a separate file.** Keeps the schema's
  source of truth in one place. The `DROP VIEW IF EXISTS` + `CREATE
  VIEW` pattern means schema changes always re-create the view body
  from the latest definition.

- **View filters: open-market (EDGAR) + stock (BPC).** These are
  editorial choices, not technical constraints. They mirror what
  the downstream scoring will care about (voluntary capital
  commitments only) and match BPC's own implicit filter. The raw
  tables are always available if you want the un-filtered data.

---

## Reading the production DB after a run

```bash
../.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('data/biotech.db'); c.row_factory = sqlite3.Row

# Recent buys, biggest first
print('--- 10 biggest BPC insider buys, last 14 days ---')
for r in c.execute('''
    SELECT ticker, insider_name, insider_position, filing_date,
           shares, trade_price, cost
    FROM bpc_insider_supplement
    WHERE buy_sell = ? AND stock_or_option = ?
      AND filing_date >= date((SELECT MAX(snapshot_date) FROM bpc_insider_supplement), ?)
    ORDER BY cost DESC LIMIT 10
''', ('Buy', 'Stock', '-14 days')):
    pos = r['insider_position'] or '—'
    print(f'  {r[\"ticker\"]:5s} {r[\"insider_name\"]:30s} ({pos[:18]:18s}) '
          f'{r[\"filing_date\"]}  {r[\"shares\"]:>10.0f} @ \${r[\"trade_price\"]:.2f}  '
          f'cost=\${r[\"cost\"]:>12,.0f}')

# Cross-validation view sample (will be all 'bpc' until M2 lands)
print()
print('--- v_insider_signal_combined sample (5 most recent) ---')
for r in c.execute('''
    SELECT source, ticker, insider_name, filing_date, buy_sell, shares
    FROM v_insider_signal_combined
    ORDER BY filing_date DESC LIMIT 5
'''):
    print(f'  [{r[\"source\"]}] {r[\"ticker\"]:5s} {r[\"insider_name\"]:30s} '
          f'{r[\"filing_date\"]} {r[\"buy_sell\"]:4s} {r[\"shares\"]}')

# Audit log
print()
print('--- ingest_log entries for bpc_insider ---')
for r in c.execute('''
    SELECT run_id, status, rows_in, rows_inserted, rows_updated, rows_rejected, input_ref
    FROM ingest_log WHERE module = ? ORDER BY run_id
''', ('bpc_insider',)):
    print(f'  run {r[\"run_id\"]:2d} {r[\"status\"]:8s}  '
          f'in={r[\"rows_in\"]:>4d} ins={r[\"rows_inserted\"]:>4d} '
          f'upd={r[\"rows_updated\"]:>4d} rej={r[\"rows_rejected\"]:>3d}  '
          f'{r[\"input_ref\"]}')
"
```

After loading `insider_data3.csv` once, you'll see ~1,556 rows in
`bpc_insider_supplement` and 1,075 rows in `v_insider_signal_combined`
(BPC-stock subset). Once Module 2 lands and EDGAR data starts flowing,
the view will grow without any change to the underlying tables.

---

## TL;DR

Module 4 ingests BPC's human-curated insider trading CSV into a
table that lives **alongside** (not merged with) the future EDGAR
Form 4 data. Strict 13-column header check, tolerant cell-level
normalisation, fully idempotent on re-run. The PK is 8 columns
(spec was 7, real data forced widening by adding `final_shares` to
preserve legitimate same-day partial fills). Two SQL views are M4's
other deliverable: `v_latest_catalysts` (most-recent-snapshot
projection over catalysts) and `v_insider_signal_combined` (the
audited UNION of EDGAR + BPC insider feeds with disagreements
preserved as separate `source`-tagged rows). 1,608 CSV rows → 1,556
distinct DB rows. Run it whenever Andre ships a fresh BPC extract.
