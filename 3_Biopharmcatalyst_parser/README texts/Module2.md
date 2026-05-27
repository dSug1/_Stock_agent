# Module 2 — EDGAR Form 4 ingest

**Plain-English purpose:** Module 2 reaches out to SEC EDGAR over HTTP,
finds every Form 4 ("insider transaction report") filed for each ticker
in our catalyst universe over the lookback window (365 days by
default), downloads the underlying XML, and parses out the actual
trades into two database tables.

It's the only network-bound module in the pipeline so far — every
other module reads from disk or from `data/biotech.db`. M2 also runs
significantly longer than the others (9.5 req/sec to SEC × hundreds
of filings = minutes of wall time on a full universe run) and is the
only module that needs SEC fair-use compliance (rate limit, real
User-Agent).

Output: **the EDGAR side of the insider-signal picture**. Combined
with Module 4's BPC insider data via the `v_insider_signal_combined`
view, you can spot insider buying/selling activity across both feeds
and surface disagreements between them.

---

## Why M2 + M4 both exist (recap)

Already covered in [Module4.md](Module4.md), but worth repeating:

- **EDGAR Form 4 (M2)** is the legal record — filed within 2 business
  days of every insider transaction. **Noisy**: includes options
  grants, tax-withholding sales, derivative exercises, and several
  other transaction types that aren't "an insider made a discretionary
  trade." Comprehensive but you have to filter.
- **BPC's insider feed (M4)** is human-curated — Andre's view is that
  BPC strips the noise and only ships the trades that actually
  signal conviction. Fewer rows, higher signal, but only as recent
  as the last manual extraction.

M2 and M4 stay in separate tables (`edgar_form4_transactions` vs
`bpc_insider_supplement`). When the cross-validation view unions
them, the `source` column makes any disagreement visible rather than
silently averaged away.

---

## The 5 sub-components

M2 is much larger than M1/M4/M5 because the work splits naturally
across separate concerns. Each file is small and tightly scoped.

| File | What it owns |
|---|---|
| `codes.py` | The SEC Form 4 transaction-code table. Single source of truth for `transaction_code_meaning` (the human label) and the `is_open_market` derivation. |
| `edgar_client.py` | Everything HTTP-related: the rate limiter, the keep-alive session, the User-Agent header, the retry/backoff policy, the submissions-index walker, the Form 4 XML fetcher. |
| `ticker_cik.py` | Ticker → 10-digit CIK resolution, backed by the cached `ticker_cik_map` table and refreshed weekly from SEC's `company_tickers.json`. |
| `form4_parser.py` | The XML parser. Pure function: bytes in, `(Form4Filing, list[Form4Txn])` out. No I/O. |
| `ingest.py` | The orchestrator: loops over tickers, resolves CIKs, fetches submissions, skips already-stored accessions, fetches + parses XML, upserts both tables, writes one `ingest_log` row. |

---

## SEC fair-use compliance (read this first)

This is what makes M2 different from every other module. SEC publishes
explicit rules for programmatic access:

1. **You must send a real `User-Agent`** identifying you (name + email
   or company + email). SEC will block requests without one (HTTP 403
   "Forbidden"). The User-Agent is read from the repo-root `.env` via
   `USER_AGENT=Your Name your@email.com`. M2 raises loudly at request
   time (not import time) if the env var is empty.
2. **Hard cap of 10 requests/sec per IP.** We target **9.5/sec** —
   matches `2_Funds_parser`'s setting and leaves a thin margin for
   clock jitter without leaving throughput on the table. (Spec §4.3.2
   originally said 5/sec; bumped 2026-05-27 for cross-project
   consistency — see decisions.md D5 update.) Override via
   `EDGAR_RATE_LIMIT_PER_SEC=X` in `.env` if you ever need to throttle
   down. The `_RateLimiter` in `edgar_client.py` is a process-global
   token bucket gated through a single lock, so even multi-threaded
   callers can't accidentally burst past the rate.
3. **Retry policy:** exponential backoff on 429 ("Too Many Requests")
   and 5xx, max 3 retries. **Hard fail** on 403 (almost always
   indicates the User-Agent is missing or invalid). No silent retry
   on 403 — it'd just waste the rate budget.
4. **No caching of huge files locally.** The submissions JSONs are
   small (~50–100 KB each) and Form 4 XMLs are tiny (~1–7 KB each).
   The cost of the rate-limited fetch dominates wall time, not bytes
   transferred.

If you ever see 403s from SEC, the first thing to check is the
User-Agent. Not present? Edit the repo-root `.env`.

---

## The two-table output

M2 fills two tables with a parent-child relationship:

### `edgar_form4_filings` (one row per Form 4 filing)

| Column | Source |
|---|---|
| `accession_number` (PK) | SEC's globally unique filing identifier (e.g., `0001193125-26-237103`) |
| `cik_issuer` | The issuer company's 10-digit CIK (zero-padded) |
| `ticker` | Resolved at fetch time from our `ticker_cik_map` |
| `issuer_name` | From the `<issuer>` block in the XML |
| `reporting_owner_cik`, `reporting_owner_name` | From the `<reportingOwner>` block (the insider) |
| `is_director`, `is_officer`, `is_ten_percent_owner` | Relationship flags from `<reportingOwnerRelationship>` |
| `officer_title` | E.g., "CEO", "Chief Business Officer" — when applicable |
| `filed_date` | When SEC stamped the filing (from the submissions index, not the XML body) |
| `fetched_at` | UTC timestamp of when M2 wrote this row |

### `edgar_form4_transactions` (zero or more rows per filing)

| Column | Source |
|---|---|
| `transaction_id` (PK) | Autoincrement |
| `accession_number` | FK to the parent filing |
| `transaction_date` | The trade day (usually 1-2 days before `filed_date`) |
| `transaction_code` | Single letter — P, S, A, M, F, D, G, X, C, etc. |
| `transaction_code_meaning` | Human label, denormalized so reports don't need a JOIN |
| `acquired_disposed` | `A` (acquired) or `D` (disposed) |
| `shares` | Float — number of shares in this transaction |
| `price_per_share` | Float — $0 for grants/exercises with no strike disclosed |
| `shares_owned_following` | Insider's post-trade total position |
| **`is_open_market`** | **TRUE iff `transaction_code IN ('P','S')`** — the only voluntary capital-commitment signals |
| `direct_or_indirect` | `D` (direct) or `I` (indirect via trust/family) |

Note: **only non-derivative transactions become rows.** Derivative
transactions (option exercises, conversions) are dropped at parse
time per spec §4.6 — they inflate share counts misleadingly when
treated as equivalent to common-stock trades. A Form 4 with only
derivative transactions produces a parent filing row and zero child
transaction rows; that's the correct behavior.

---

## The transaction-code semantics (THE most important rule)

The single most important business-logic decision in M2 is:

> **`is_open_market = TRUE` if and only if `transaction_code IN ('P','S')`.**

Why so strict? Because the SEC Form 4 transaction codes describe a
mix of fundamentally different things:

| Code | Meaning | Open-market? |
|---|---|---|
| **P** | Open-market or private purchase | **✅ YES** |
| **S** | Open-market or private sale | **✅ YES** |
| A | Grant/award (compensation) | ❌ no — not the insider's choice |
| M | Exercise of derivative (option exercise) | ❌ no — mechanical conversion |
| F | Tax withholding (auto-sell at vest) | ❌ no — payroll mechanics |
| D | Disposition to issuer (buyback tender) | ❌ no — corporate action |
| G | Bona fide gift | ❌ no — not an economic trade |
| X | Exercise of in-the-money derivative | ❌ no — mechanical |
| C | Conversion of derivative | ❌ no — mechanical |

Only **P** and **S** represent "the insider voluntarily put capital
to work (or pulled it out) at a market price they accepted." Every
other code is either compensation plumbing, derivative mechanics, or
involuntary. Downstream scoring should treat them as noise, not
signal.

This is why `v_insider_signal_combined` filters the EDGAR side to
`is_open_market = 1`. After running M2 against the 5 acceptance
tickers, the cross-validation view holds **123 EDGAR rows** — the
93 sales + 30 purchases extracted from 230 total transactions. The
other 107 transactions (grants, exercises, tax withholding) are
sitting in `edgar_form4_transactions` for full-fidelity audit, but
don't flow into the signal view.

---

## What it actually does, step by step

When you run `scripts/3_2_ingest_edgar_form4.py`:

1. **Picks the ticker universe.**
   `--tickers AAPL,BMY,...` if you pass an explicit subset. Otherwise
   the distinct ticker list from the most recent `snapshot_date` in
   `catalyst_snapshots` — i.e., "every ticker BPC is currently
   tracking for catalysts."

2. **Refreshes the ticker → CIK map** if the cache (`ticker_cik_map`
   table) is more than 7 days old. One HTTP call to SEC's
   `company_tickers.json` (~3 MB → ~12k rows). Subsequent runs hit
   the SQLite cache instantly.

3. **For each resolved ticker** (unresolved ones log a warning and
   skip — common for delisted, foreign, or ETF tickers):
   1. **Compute the per-ticker `since_floor`** (D5 optimization):
      - If we already have Form 4 rows for this CIK →
        `floor = max(today - lookback_days, MAX(filed_date) for this CIK)`.
        Mode = `incremental`.
      - If no rows yet → `floor = today - lookback_days`. Mode = `new`.
      - If `--full-refresh` was passed → drop existing rows first,
        then `floor = today - lookback_days`. Mode = `full_refresh`.
   2. Fetch the submissions index JSON for the CIK. Walk
      `filings.recent` for Form 4 (and 4/A) filings whose
      `filingDate >= floor`.
   3. Pre-load the set of accession numbers we already have for
      this CIK from `edgar_form4_filings`.
   4. For each Form 4 in the window:
      - **If accession is already in the DB → skip entirely.** No
        XML fetch, no parsing, no DB write. (The incremental floor
        usually means there are no such filings to skip — but the
        check is still there as a belt-and-braces.)
      - Otherwise: fetch the primary XML document (with the
        basename-first → `index.json`-fallback trick that handles
        SEC's `xslF345X05/` prefix quirk).
      - Parse the XML into a `(Form4Filing, list[Form4Txn])` tuple.
      - In a single SQLite transaction: `INSERT OR IGNORE` the
        filing row, then `INSERT` every transaction.
   5. Log a per-ticker summary line: `mode=… since=… in_window=N
      already=M fetched=K failed=F txns=T`.

4. **Per-ticker fail-open.** If any ticker's submissions or parse
   step fails, the error is captured in `TickerStats.error` and we
   move on. The whole run never aborts because of one bad ticker.

5. **Write one `ingest_log` row** at the end with `module =
   'edgar_form4'`, `input_ref = '<N> tickers'`, and the aggregate
   counts.

6. **Print a summary** to stdout: tickers requested, unresolved,
   processed, filings inserted, transactions inserted.

---

## The three kinds of failure

| Severity | When | Effect |
|---|---|---|
| 🔴 **Schema / setup** — hard fail | `USER_AGENT` is empty in `.env`; HTTP 403 ("Forbidden"); a Python-level exception in the orchestrator | Raises immediately, the whole run aborts, `ingest_log.status = 'failed'`. Almost always means User-Agent or network config. |
| 🟡 **Per-ticker fetch failure** — degraded, run continues | Submissions JSON 404s; submissions returns malformed JSON; Form 4 XML 404s after all fallbacks; XML is malformed | Recorded in `TickerStats.error` for the affected ticker; other tickers continue. Run finishes with `ingest_log.status = 'success'` if any tickers succeeded. |
| 🟢 **Soft data quirk** — row-level skip | A single `nonDerivativeTransaction` lacks `transactionDate` (mandatory for the DB); an unknown transaction code | Skipped row logged as a warning; parent filing row still written. Unknown codes still parse — they just get a generic `Other (X)` label. |

Notable: **M2 has no equivalent of M1/M4's "row rejected because the
data is malformed."** A Form 4 either parses or it doesn't. The
fine-grained "soft" handling only applies inside the XML, not across
filings.

---

## What it explicitly does NOT do

- ❌ It does not merge EDGAR data with BPC data. They live in separate
  tables; the cross-validation view does on-demand union at query
  time.
- ❌ It does not store derivative transactions (per spec §4.6).
  Filing-level rows are still written for derivative-only Form 4s
  but they produce zero transaction rows.
- ❌ It does not paginate beyond `filings.recent`. The submissions
  index keeps the most recent ~1000 filings in `recent`; older
  filings move to numbered paginated files. For biotech small-caps
  in a 365-day window this is rarely an issue. Documented as a known
  limitation for v1.
- ❌ It does not amend / update an existing filing. `INSERT OR IGNORE`
  on `accession_number` means once a Form 4 is in the DB, M2 will
  never overwrite it. If SEC corrects a filing post-hoc, you need
  `--full-refresh` (which deletes and re-fetches all filings for
  the targeted CIKs).
- ❌ It does not chase Form 4/A amendments to dedupe against the
  original — both versions are stored, keyed by their own
  accession_number. Downstream code can `ORDER BY filed_date DESC`
  if it wants latest-only.
- ❌ It does not auto-trigger from M1 or M4. You run it explicitly.

---

## How to run it

From the parser folder, with the shared venv active:

```bash
# Default: every ticker in the most recent catalyst snapshot, 365-day lookback, incremental
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_2_ingest_edgar_form4.py

# Explicit ticker subset (the 5 spec acceptance tickers)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_2_ingest_edgar_form4.py \
    --tickers CRBP,DTIL,STTK,TRDA,VSTM

# Shorter lookback (faster)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_2_ingest_edgar_form4.py \
    --lookback-days 90

# Full refresh — drop existing rows for these tickers first, then re-fetch
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_2_ingest_edgar_form4.py \
    --tickers CRBP --full-refresh

# Verbose — show per-ticker error summaries at the end
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_2_ingest_edgar_form4.py -v
```

Or run `run_3_Biopharmcatalyst_parser.bat` — M2 is wired in with a
y/N gate after Module 4. The gate prompt explicitly mentions
"~9.5 req/sec to SEC" so you know it's network-bound and might take
a few minutes on a full-universe run.

To verify the parser still works offline:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_form4_parser.py tests/test_edgar_codes.py -v
```

27 tests: 18 for the code → meaning + is_open_market table, 9 for
the XML parser (one real-data fixture from CRBP plus 8 synthetic
edge cases).

---

## How long an M2 run takes

The wall time is dominated by the rate limit, not the network. At
9.5 req/sec:

- **Refresh `company_tickers.json`** (only if cache > 7 days old): 1
  request, ~0.1s.
- **Per ticker** (resolved, first-time `new` mode): 1 submissions JSON
  + N Form 4 XMLs + occasional `index.json` fallback. For biotech
  small-caps, N typically 5–50 per 365-day window.
- **Per ticker** (`incremental` mode, ticker we've seen before): 1
  submissions JSON + the (usually small) number of Form 4 XMLs filed
  since the last sweep. Most weekly re-runs will fetch **zero** XMLs
  per ticker because insider filings don't appear daily.
- **Per Form 4** (already in DB): 0 requests. Free.
- **Per Form 4** (first-time fetch): 1–3 requests depending on
  whether the basename guess works first or we need the
  `index.json` fallback.

For the 5-ticker spec acceptance set (175 filings on first run):
~180 HTTP calls / 9.5 per sec ≈ **20 seconds**. A full-universe
**first run** (~300 tickers, 1500–6000 filings): **3–10 minutes**.

After that, **subsequent runs are near-instant** even when the
universe is unchanged: roughly `N_tickers / 9.5 + a few XML fetches
for whatever's new` — typically under a minute for the full 296-ticker
universe when nothing has happened (just the per-ticker submissions
JSON checks), or a couple of minutes if there's a fresh week of
filings to pick up. The incremental floor is what makes this work —
without it, every re-run would still iterate through ~5,000 filings
client-side just to confirm "yep, already have it."

---

## The five files M2 is made of

```
src/module_2/
  __init__.py            # empty — makes 'module_2' importable
  codes.py               # SEC Form 4 transaction-code table + is_open_market
  edgar_client.py        # rate limiter, HTTP, submissions, Form 4 XML fetch
  ticker_cik.py          # ticker → CIK resolver + weekly refresh
  form4_parser.py        # XML → (Form4Filing, list[Form4Txn])
  ingest.py              # orchestrator + per-ticker stats + ingest_log

scripts/
  3_2_ingest_edgar_form4.py  # CLI entry-point you actually run

tests/
  test_edgar_codes.py    # 18 tests pinning P/S → TRUE, everything else → FALSE
  test_form4_parser.py   # 9 tests — 1 real fixture + 8 synthetic edge cases
  fixtures/
    form4_sample.xml     # real CRBP grant filing, checked in (~7 KB)
    form4_sample_meta.txt
```

**`codes.py`** is the file you edit when SEC adds a new transaction
code or you discover a code in the wild that we haven't catalogued.
Adding a new code without bumping `is_open_market` to `True` for it
is safe — the default for unknown codes is `False` and the parser
won't crash.

**`edgar_client.py`** is the file you almost never need to touch.
The rate limit is per spec; the User-Agent is from `.env`; the
retry/backoff is conservative. The one moving piece is the Form 4
XML filename heuristic (`_pick_form4_xml_name`) — if SEC changes
their archive layout, that's the function to update.

**`form4_parser.py`** is a pure function. It takes XML bytes plus
the accession_number and filed_date the caller knows from the
submissions index (the XML itself doesn't carry those), and returns
the parsed result. Trivially unit-testable; the test file uses
synthetic XML strings for everything except the one real-data
fixture.

**`ingest.py`** is the orchestrator. The interesting trick is the
pre-load of existing accession numbers per CIK — one cheap SELECT
per ticker, then every "is this filing already in the DB?" check
is in-memory. Without this we'd be doing 5–50 small SELECTs per
ticker, which would dwarf the SEC HTTP cost.

**`tests/fixtures/form4_sample.xml`** is a real Form 4 from CRBP
(Corbus Pharmaceuticals — a CBO grant filing, transaction code A).
Checked into the repo so the parser tests don't need network access.
About 7 KB. If you ever re-download it from a different filing,
update the assertions in `test_real_form4_fixture_parses`
accordingly.

---

## When you'll need to re-run M2

- **After every new M1 ingest.** The catalyst-snapshot ticker
  universe drives M2's default scope; a new snapshot might bring
  in tickers we haven't seen before.
- **Re-running on the same universe** is cheap (just the per-ticker
  submissions check, no XML fetches) and idempotent.
- **`--full-refresh`** when SEC has corrected old filings or you
  suspect stale data. Drops + re-fetches everything for the
  targeted CIKs. Don't use this on a full universe without a good
  reason — it's an expensive operation.
- **Never** if the universe hasn't changed and you ran M2 less than
  a week ago. Insider filings appear within 2 business days; over
  longer gaps you'll miss recent activity.

---

## Why these design choices

A few that aren't obvious from the code:

- **Rate limit is 9.5 req/sec, just under SEC's 10/sec cap.** Matches
  `2_Funds_parser`'s setting so the two projects don't double-clip
  each other if they happen to run concurrently. The 0.5 req/sec
  margin absorbs clock jitter and minor request bursts. Spec §4.3.2
  originally said 5/sec; D5 update bumped it for cross-project
  consistency and faster full-universe runs. Override via
  `EDGAR_RATE_LIMIT_PER_SEC=X` in `.env` if you ever need to throttle
  down (e.g., for a long-running batch where SEC has been flaky).

- **User-Agent from `.env`, not hardcoded.** SEC's terms require
  real contact info. Hardcoding a fake address (as `2_Funds_parser`
  currently does) technically violates the policy and could get the
  IP blocked. M2 reads from `.env` (per `USER_AGENT=Name
  email@example.com`) and raises loudly at request time if it's
  unset.

- **Two tables instead of one denormalized table.** A Form 4 filing
  has 0–N transactions plus a fixed set of filing-level metadata
  (issuer, insider, relationship flags). Splitting at this boundary
  avoids repeating the metadata N times per filing and makes the
  "which insider made which transactions?" join trivial. Spec
  §2.2 + §2.3 mandates the split.

- **`INSERT OR IGNORE` on filings, not `INSERT OR REPLACE`.** SEC
  doesn't issue corrections to Form 4s — instead they file a Form
  4/A with a separate accession_number. So once we've parsed a
  Form 4, its content is immutable. `INSERT OR IGNORE` makes the
  re-run a no-op; `INSERT OR REPLACE` would re-parse and re-write
  unnecessarily.

- **Per-ticker incremental floor at `MAX(filed_date)`** (D5
  optimization, added 2026-05-27). Without it, every re-run of M2
  iterates through every Form 4 in the 365-day window per ticker
  client-side, even though `INSERT OR IGNORE` would just skip the
  ones we have. With it, the submissions-list scan is narrowed to
  "filings filed since the last sweep" — usually zero on a weekly
  cadence. Chose `MAX(filed_date)` over a separate "previous run
  date" table because it uses data we already have and survives
  partial-universe runs gracefully. The one v1 limitation: a
  ticker with 0 Form 4s in the window can't be distinguished from
  "never queried" — it pays one cheap submissions JSON per run
  forever. Cost is negligible (~0.1s); not worth a separate
  audit table.

- **Derivative transactions dropped at parse time.** They're real
  insider events but they don't carry the same signal as
  common-stock trades. Counting "1000 option exercises at $0" the
  same way you'd count "1000 share purchase at $25" would corrupt
  any aggregate. Dropping them in the parser (not at query time)
  keeps `edgar_form4_transactions` honest as a "real trades" table.

- **Form 4/A amendments treated as separate filings.** They have
  their own accession_number. Trying to dedupe against the original
  Form 4 would be heuristic and error-prone. Downstream code that
  wants latest-only can `ORDER BY filed_date DESC` and pick the
  first match per insider × transaction_date.

- **Per-ticker fail-open.** Network failures are routine when
  hitting hundreds of endpoints. Aborting the whole run because
  one ticker's submissions JSON timed out would be terrible UX —
  you'd lose all the work for the tickers that succeeded. Instead,
  the failure is captured in `TickerStats.error`, the orchestrator
  moves to the next ticker, and the user sees the failure summary
  with `-v` at the end.

- **`ticker_cik_map` lives in the SQLite DB, not a JSON file.**
  Per D32 (the SQL-only storage convention), the cache is in
  `data/biotech.db.ticker_cik_map`. The 7-day TTL is checked by
  reading `MAX(last_refreshed)` from the table. Keeps everything
  in one place; no risk of `.json` and `.db` drifting apart.

---

## Reading the production DB after a run

```bash
../.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('data/biotech.db'); c.row_factory = sqlite3.Row

# Biggest open-market BUYS in our universe
print('--- top 10 EDGAR open-market BUYS by total dollar value ---')
for r in c.execute('''
    SELECT f.ticker, f.reporting_owner_name, f.officer_title,
           t.transaction_date, t.shares, t.price_per_share,
           (t.shares * t.price_per_share) AS gross_usd
    FROM edgar_form4_transactions t
    JOIN edgar_form4_filings f USING (accession_number)
    WHERE t.is_open_market = 1 AND t.transaction_code = 'P'
      AND t.price_per_share > 0
    ORDER BY gross_usd DESC LIMIT 10
'''):
    title = (r['officer_title'] or '')[:25]
    print(f'  {r[\"ticker\"]:5s} {r[\"reporting_owner_name\"]:30s} ({title:25s}) '
          f'{r[\"transaction_date\"]}  {r[\"shares\"]:>8.0f} @ \${r[\"price_per_share\"]:.2f}  '
          f'gross=\${r[\"gross_usd\"]:>14,.0f}')

# The cross-validation view: are EDGAR and BPC agreeing?
print()
print('--- v_insider_signal_combined rows per source ---')
for r in c.execute(\"SELECT source, COUNT(*) AS n FROM v_insider_signal_combined GROUP BY source\"):
    print(f'  {r[\"source\"]:6s} {r[\"n\"]:>4d}')

# Disagreement spotter: tickers where one feed shows activity the other doesn't
print()
print('--- 5 tickers where one feed shows activity the other does not ---')
for r in c.execute('''
    SELECT ticker,
           SUM(CASE WHEN source = 'edgar' THEN 1 ELSE 0 END) AS edgar,
           SUM(CASE WHEN source = 'bpc'   THEN 1 ELSE 0 END) AS bpc
    FROM v_insider_signal_combined
    GROUP BY ticker
    HAVING (edgar = 0 AND bpc > 0) OR (bpc = 0 AND edgar > 0)
    LIMIT 5
'''):
    print(f'  {r[\"ticker\"]:5s} edgar={r[\"edgar\"]:>3d}  bpc={r[\"bpc\"]:>3d}')
```

After the 5-ticker spec acceptance run you'll see 175 filings and
230 transactions in the EDGAR tables, and `v_insider_signal_combined`
will show 123 EDGAR (open-market subset of 230) alongside the 1,075
BPC stock rows. Disagreement-spotter rows are common — they're
exactly the rows where Andre will want to investigate.

---

## TL;DR

Module 2 fetches SEC EDGAR Form 4 filings for every ticker in our
catalyst universe, parses the non-derivative transactions, and
writes them to a parent-child table pair. Network-bound (9.5 req/sec
to SEC, real User-Agent in `.env`), incremental by default (already-
stored accessions skip the fetch entirely), per-ticker fail-open
(one bad ticker doesn't abort the run). The most important rule is
`is_open_market = TRUE if and only if transaction_code IN ('P','S')`
— everything else is compensation mechanics, not signal.
Combined with Module 4's BPC feed via `v_insider_signal_combined`,
M2 closes the loop on the insider-trading picture for our universe.
