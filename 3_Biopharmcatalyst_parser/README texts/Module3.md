# Module 3 — EDGAR Schedule 13D/13G metadata ingest

**Plain-English purpose:** Module 3 records every time an
institutional investor crosses the 5% ownership threshold (or amends
their position) in one of our catalyst-universe tickers. SEC requires
investors holding 5%+ of any registered class of voting securities to
file a **Schedule 13D** (activist intent) or **Schedule 13G** (passive
intent) within 10 days. Subsequent material changes get filed as
amendments — `13D/A` and `13G/A`. M3 catches all four form types,
records the metadata, and gives you a direct URL to the filing.

It's the smallest module in the pipeline. By design — v1 only records
metadata, not the form body. That's enough to know "Citadel just took a
5% stake in CRBP on May 12th" with a click-through to the filing
itself.

---

## SEC form-name gotcha (read this if you ever edit the filter)

SEC's submissions API uses **two coexisting form-name conventions** for
ownership filings, mixed inside the same `recent[]` array:

| Format | Examples |
|---|---|
| Modern `SC` | `SC 13D`, `SC 13G`, `SC 13D/A`, `SC 13G/A` |
| Older/alternative `SCHEDULE` | `SCHEDULE 13D`, `SCHEDULE 13G`, `SCHEDULE 13D/A`, `SCHEDULE 13G/A` |

They are **not era-stratified** — for Pfizer, the most recent 6
ownership filings (2026-Q1) are all `SCHEDULE 13X` while 2022–2024
filings are `SC 13X`. Filtering only on the `SC` variants silently
drops a large fraction of real filings — when M3 first ran against
the full universe it returned **0 filings across 287 tickers** for
exactly this reason.

The filter (`OWNERSHIP_FORMS` in `src/module_3/ingest.py`) covers
all 8 form names. Don't shrink the list. See decisions.md D6
calibration update for the diagnosis.

---

## What 13D/13G filings actually mean (background)

The four form types and what they signal:

| Form | Trigger | Signal |
|---|---|---|
| **SC 13D** | Initial 5%+ acquisition with **activist** intent ("we may seek changes at the issuer") | High — activist filer thinks the company is mispriced or mismanaged and intends to do something about it |
| **SC 13G** | Initial 5%+ acquisition **without** activist intent (index funds, long-only mutuals) | Medium — passive accumulation; can still indicate that a sophisticated allocator likes the thesis |
| **SC 13D/A** | Amendment to an earlier 13D | Material change: stake grew/shrunk, intent shifted, or the activist took a specific action |
| **SC 13G/A** | Amendment to an earlier 13G | Position change. **A 13G/A reporting <5% is an EXIT signal** — the holder has dropped below the disclosure threshold |

For our catalyst-driven biotech pipeline, this matters because:

- A fresh **13D** in a small-cap biotech often correlates with an
  inflection point — someone with research firepower has decided
  this story is worth a public position.
- A **13G/A reporting <5%** can flag "smart money is walking away
  before the readout."
- **Stacked 13D filings from multiple unrelated holders** within a
  short window is a strong cluster signal.

v1 just records that the filing happened. Acting on these signals
(detecting the exit case, clustering, etc.) is downstream-module
work — M3's job is to make sure the data is *available*.

---

## How M3 relates to M2

M3 is essentially **M2 minus the XML parser**.

| Concern | M2 (Form 4) | M3 (13D/G) |
|---|---|---|
| HTTP plumbing | `module_2.edgar_client` | **Imports from M2** |
| Ticker → CIK resolution | `module_2.ticker_cik` | **Imports from M2** |
| Rate limit | 9.5 req/sec via `_EDGAR_LIMITER` | **Same `_EDGAR_LIMITER`** |
| User-Agent | From `.env` | **Same `.env` value** |
| Per-ticker incremental floor | `MAX(filed_date) FROM edgar_form4_filings` | **Same pattern**, but against `edgar_ownership_filings` |
| Form 4 XML fetch + parse | Yes — 1 fetch per filing | **No** — metadata only |
| Tables touched | `edgar_form4_filings`, `edgar_form4_transactions` | `edgar_ownership_filings` (single table) |
| `ingest_log.module` tag | `'edgar_form4'` | `'edgar_13dg'` |

The two modules' incremental floors are **independent** — a ticker
that's been in `edgar_form4_filings` for a year can still be `new` to
M3 the first time we run it. They share no state beyond the HTTP
plumbing.

---

## The output (one table)

### `edgar_ownership_filings`

| Column | Source |
|---|---|
| `accession_number` (PK) | SEC's globally unique filing identifier |
| `cik_issuer` | The issuer's 10-digit CIK (the company being acquired into, NOT the filer) |
| `ticker` | Resolved at fetch time from `ticker_cik_map` |
| `issuer_name` | From `ticker_cik_map.name` (cached at ticker resolution) |
| `form_type` | One of `SC 13D`, `SC 13G`, `SC 13D/A`, `SC 13G/A` |
| `filed_date` | When SEC stamped the filing |
| `filer_name` | **NULL in v1** — lives in the form body, deferred per spec §5.5 |
| `filing_url` | Direct link to the filing's primary document. Click to see the filer + their disclosed intent + stake size |
| `percent_of_class` | **NULL in v1** — lives in "Item 11" of the form body, deferred |
| `fetched_at` | UTC timestamp of when M3 wrote this row |

The most useful column for v1 work is `filing_url`. Open it in a
browser and the form's filer + percentage are right there in the
human-readable HTML. The pipeline records "there is a 13G for CRBP
filed 2026-05-12 at this URL" — the analyst clicks through to read
the details.

---

## What v1 records vs what's deferred

**Records:**
- The fact of the filing (accession, form_type, filed_date)
- A working URL to the form body
- The issuer (company being acquired into) + ticker
- An audit timestamp

**Deferred to v2** (all listed in spec §5.5):

1. **`filer_name`** — the institutional investor's name. Lives in the
   `<filer><filerInfo><filerCompany>` block of the form body, not
   the submissions index. Recording it costs N extra HTTP fetches
   per ticker (one per filing). v1 trades that cost for NULLs.
2. **`percent_of_class`** — the actual stake percentage. Lives in
   "Item 11" / cover page of the form body. Same cost trade-off as
   filer_name.
3. **Distinguishing initial filings from amendments** — schema
   captures `form_type` so this is queryable with a SQL `LIKE`, but
   no derived column.
4. **Exit-signal detection** (13G/A reporting <5%) — needs
   `percent_of_class` parsed first.
5. **Filer-CIK extraction** — would let us aggregate "who is buying
   biotech this quarter?" Same form-body parse problem.

These are all "fetch + parse the form HTML" problems. Doable, just
N more requests per filing and a chunk of HTML parsing work. v1
ships without them because the URL is enough for manual
investigation.

---

## What it actually does, step by step

When you run `scripts/3_3_ingest_edgar_13dg.py`:

1. **Picks the ticker universe.** Same as M2: `--tickers` if you pass
   them, otherwise distinct tickers from the latest
   `catalyst_snapshots`.

2. **Resolves ticker → CIK** via `module_2.ticker_cik.resolve_tickers`
   (refreshes the cache if > 7 days old). Unresolved tickers log a
   warning and skip.

3. **For each resolved ticker:**
   1. Compute the per-ticker `since_floor`:
      - If we already have rows in `edgar_ownership_filings` for this
        CIK → `floor = max(today - lookback_days, MAX(filed_date))`.
        Mode = `incremental`.
      - If no rows yet → full lookback. Mode = `new`.
      - If `--full-refresh` → drop existing rows first; mode =
        `full_refresh`.
   2. Fetch the submissions JSON for the CIK; filter
      `filings.recent` to forms in `{SC 13D, SC 13G, SC 13D/A,
      SC 13G/A}` with `filingDate >= floor`.
   3. Pre-load existing `accession_number`s for the CIK.
   4. For each matched filing:
      - If accession already in DB → skip (`already_in_db++`).
      - Otherwise build the `filing_url` (basename of `primary_doc`,
        with `xslSCHEDULE_13G_X01/` prefix stripped if present;
        falls back to the directory URL if `primary_doc` is missing).
      - `INSERT OR IGNORE` the row with `filer_name = NULL` and
        `percent_of_class = NULL`.
   5. Log a per-ticker summary line: `mode=… since=… in_window=N
      already=M inserted=K`.

4. **Per-ticker fail-open** — same as M2. A failure on one ticker
   doesn't abort the run.

5. **Write one `ingest_log` row** with `module = 'edgar_13dg'`,
   `input_ref = '<N> tickers'`, and the aggregate counts.

---

## The three kinds of failure

| Severity | When | Effect |
|---|---|---|
| 🔴 **Schema / setup** — hard fail | `USER_AGENT` empty in `.env`; HTTP 403 from SEC; Python-level exception in the orchestrator | Aborts; `ingest_log.status = 'failed'` |
| 🟡 **Per-ticker fetch failure** — degraded, run continues | Submissions JSON 404s, malformed JSON, etc. | Captured in `TickerStats.error`; other tickers continue; `ingest_log.status = 'success'` |
| 🟢 **Soft data quirk** — row-level | An existing accession (already-stored filing) | Silently skipped; counted in `filings_already_in_db` |

M3 has fewer failure modes than M2 because there's no XML to parse —
the only file we touch is the submissions JSON, which is well-formed
by construction.

---

## What it explicitly does NOT do

- ❌ It does not fetch the form body. v1 records metadata only.
- ❌ It does not extract the filer's name or stake percentage.
  Both are in the form body; deferred per spec §5.5.
- ❌ It does not distinguish initial filings from amendments
  beyond storing `form_type`. A query `WHERE form_type LIKE '%/A'`
  surfaces amendments; we don't bake the distinction into a derived
  column.
- ❌ It does not detect exit signals (13G/A with <5%). That requires
  `percent_of_class`, which we don't parse.
- ❌ It does not paginate beyond `filings.recent` (same limitation
  as M2).
- ❌ It does not auto-trigger from M1, M2, or M4. Run it explicitly.

---

## How to run it

From the parser folder, with the shared venv active:

```bash
# Default: every ticker in the latest catalyst snapshot, 365-day lookback, incremental
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_3_ingest_edgar_13dg.py

# Explicit subset (the 5 spec-acceptance tickers from §4.5)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_3_ingest_edgar_13dg.py \
    --tickers CRBP,DTIL,STTK,TRDA,VSTM

# Shorter lookback
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_3_ingest_edgar_13dg.py \
    --lookback-days 90

# Full refresh — drop existing rows for the target CIKs first
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_3_ingest_edgar_13dg.py \
    --tickers CRBP --full-refresh

# Verbose — show per-ticker error summaries
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_3_ingest_edgar_13dg.py -v
```

Or run `run_3_Biopharmcatalyst_parser.bat` — M3 is the final y/N gate
in the chain, after M2.

To verify the offline tests:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_module3_ingest.py -v
```

9 tests: form-type allow-list (1), URL construction (4), per-ticker
floor (4 including a cross-module independence check).

---

## How long an M3 run takes

Significantly faster than M2 because there's no XML fetch per filing
— just the submissions JSON walk.

At 9.5 req/sec:

- **Per ticker** (new): 1 submissions JSON. That's it. No subsequent
  archive calls.
- **Per ticker** (incremental): 1 submissions JSON. Filter already
  narrowed to filings since `MAX(filed_date)`; everything in that
  filtered list is also dedup'd via `INSERT OR IGNORE`.

For the 5-ticker acceptance set: ~5 HTTP calls ≈ **0.5 seconds**.
Full-universe (~300 tickers): ~300 calls ≈ **32 seconds** on a
first run. Subsequent runs are the same — the work is dominated by
the per-ticker submissions check, not by archive fetches.

**Compared to M2 on the same universe:** M2 fetches the submissions
JSON + one XML per Form 4 (often 5–50 XMLs per ticker), so a full
universe is 3–10 minutes. M3 is effectively just the submissions
walk, so it runs in seconds.

⚠️ **Don't run M2 and M3 simultaneously.** Each Python process has
its own `_EDGAR_LIMITER` instance at 9.5 req/sec. Running both in
parallel can burst above SEC's 10/sec fair-use cap and risk a soft
block.

---

## The two files M3 is made of

```
src/module_3/
  __init__.py            # empty — makes 'module_3' importable
  ingest.py              # the whole module — orchestrator, floor, URL builder

scripts/
  3_3_ingest_edgar_13dg.py  # CLI entry-point

tests/
  test_module3_ingest.py    # 9 offline tests
```

**`ingest.py`** is the entire module. ~190 lines covering the
orchestrator, the per-ticker floor helper, the URL builder, the
issuer-name lookup, and the `ingest_log` writer. No separate parser
file because there's no parsing.

**`3_3_ingest_edgar_13dg.py`** is the thin CLI. Same arg shape as
M2's: `--lookback-days / --tickers / --full-refresh / -v`. Same
default-from-latest-snapshot behavior.

There's no `fixtures/` directory for M3 — the offline tests don't
need any sample data because there's no parser. URL construction is
unit-testable directly; floor logic uses a tmp SQLite DB seeded with
synthetic rows.

---

## When you'll need to re-run M3

- **After every M1 ingest** (same as M2 — the catalyst-snapshot
  ticker universe drives the default scope).
- **Re-running on the same universe** is cheap and idempotent. The
  per-ticker incremental floor means each ticker pays just one
  cheap submissions JSON.
- **`--full-refresh`** when you suspect stale or corrupted data.
  Cheap relative to M2 because there's no XML re-fetch.
- **Once v2 lands** (filer_name + percent_of_class parsing), you'll
  want to re-fetch everything to backfill the new columns — that's
  when `--full-refresh` on the full universe finally becomes
  expensive.

---

## Why these design choices

- **Reuse M2 wholesale.** M3's HTTP plumbing, rate limit,
  User-Agent, and ticker resolver are all imported from M2. No
  duplication; no risk of two clients drifting out of sync. If we
  ever add a third EDGAR-touching module (e.g., a future M3-v2 for
  form-body parsing), it'll do the same — one shared client.

- **Metadata only in v1.** Spec §5.3 lets us leave `filer_name` and
  `percent_of_class` NULL. We take that option because adding form-
  body fetches would multiply the HTTP cost by N (one per filing),
  and the `filing_url` is sufficient for manual investigation. The
  cost of parsing 13D/G form HTML is also non-trivial — they're not
  XBRL-tagged like financial statements.

- **`issuer_name` from `ticker_cik_map`**, not the submissions JSON.
  Same data, no extra request. The `name` column in
  `ticker_cik_map` was populated by `resolve_tickers` at the start
  of the run from SEC's `company_tickers.json`.

- **`filing_url` strips xsl prefixes.** Modern SEC filings put the
  SEC-rendered HTML at `xslSCHEDULE_13G_X01/<name>.htm` and the
  raw form at `<name>.htm`. We point at the raw form so the URL is
  cleaner and stable across SEC's rendering updates.

- **`build_filing_url` always returns non-empty.** The schema
  declares `filing_url NOT NULL`. If `primary_doc` is missing for
  some reason, we fall back to the directory URL
  (`<accn>/index.htm` lives there). The NOT NULL constraint is
  never violated.

- **Per-ticker incremental floor against `edgar_ownership_filings`,
  not `edgar_form4_filings`.** Same shape as M2's D5 optimization
  but using M3's table. The two are independent — a ticker can be
  `incremental` in M2 and `new` in M3 if M3 just got its first run.
  Verified by `test_m2_and_m3_floors_are_independent`.

- **No XML fixture in `tests/fixtures/`.** Nothing to parse. The
  9 offline tests cover everything M3 actually does that's testable
  without network.

---

## Reading the production DB after a run

```bash
../.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('data/biotech.db'); c.row_factory = sqlite3.Row

# Recent 13D/G activity, newest first
print('--- 13D/G filings, most recent 10 ---')
for r in c.execute('''
    SELECT ticker, issuer_name, form_type, filed_date, filing_url
    FROM edgar_ownership_filings
    ORDER BY filed_date DESC LIMIT 10
'''):
    issuer = (r['issuer_name'] or r['ticker'])[:30]
    print(f'  {r[\"ticker\"]:5s} {issuer:30s} {r[\"form_type\"]:10s} {r[\"filed_date\"]}')
    print(f'         {r[\"filing_url\"]}')

# Tickers with most 13D/G traffic
print()
print('--- top 10 tickers by 13D/G filing count ---')
for r in c.execute('''
    SELECT ticker, COUNT(*) AS n,
           SUM(CASE WHEN form_type LIKE '%/A' THEN 1 ELSE 0 END) AS amendments
    FROM edgar_ownership_filings
    GROUP BY ticker ORDER BY n DESC LIMIT 10
'''):
    print(f'  {r[\"ticker\"]:5s} {r[\"n\"]:>3d} filings  ({r[\"amendments\"]} amendments)')

# Form-type mix
print()
print('--- form-type mix across the whole DB ---')
for r in c.execute(
    'SELECT form_type, COUNT(*) AS n FROM edgar_ownership_filings GROUP BY form_type ORDER BY n DESC'
):
    print(f'  {r[\"form_type\"]:10s} {r[\"n\"]:>4d}')
"
```

After the first full-universe run against the 296-ticker catalyst
universe (287 resolved), the table holds **2,230 filings across 273
tickers** (95% of the resolved universe — most catalyst-stage
biotechs have at least one 5%+ holder). Form-type mix is dominated
by amendments: 61% `SCHEDULE 13G/A`, 27% `SCHEDULE 13G`, 10%
`SCHEDULE 13D/A`, 1.5% `SCHEDULE 13D`. (Note: 0% `SC` variants in
this snapshot — SEC's recent biotech filings have entirely flipped
to the `SCHEDULE` prefix. The 8-form filter future-proofs us if they
ever flip back.) Top tickers by filing count are small/mid-cap
biotechs with heavy amendment activity (TENX=39, TNGX=29, VSTM=29,
LXEO=28, PRAX=25) — exactly the "smart money is actively re-
positioning around the catalyst" signal M3 is supposed to capture.

---

## TL;DR

Module 3 walks SEC's submissions index for every ticker in our
catalyst universe and records every Schedule 13D/13G filing (5%+
ownership disclosures) into `edgar_ownership_filings`. Metadata only
in v1 — the `filing_url` points at the form body where the filer and
the stake size live, but parsing those out is deferred to v2 per
spec §5.5. Reuses M2's HTTP client and ticker resolver wholesale; the
whole module is one file. Per-ticker incremental floor (same shape
as M2's D5 optimization) keeps re-runs near-instant. The smallest,
cheapest module in the pipeline — but the one that surfaces "smart
money is taking a 5% stake" signals you can't get from price data
alone.
