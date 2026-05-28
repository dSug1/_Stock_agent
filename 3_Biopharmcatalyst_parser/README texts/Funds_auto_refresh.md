# Funds auto-refresh — cross-project pre-pipeline check

**Plain-English purpose:** before the biopharmcatalyst pipeline starts,
the orchestrator looks at the calendar and the state of the sibling
`2_Funds_parser` database. If today is within a window where fresh
13F fund-holdings data should be available — or if you've slipped past
that window and the funds DB is still on a stale quarter — the
3_Biopharmcatalyst pipeline auto-runs `2_Funds_parser` modules 0–5 and
regenerates the consensus-builds HTML report **before** Module 0 of
3_Biopharm starts.

The point is to keep the Module 6 fund-accumulation signal honest
without making you remember to run `run_2_Funds_parser.bat` manually
four times a year.

This is **not** a numbered Module of the biopharmcatalyst pipeline. It's
a cross-project trigger that fires conditionally and exits in under a
second when there's nothing to do.

---

## Why this exists (the 13F calendar problem)

Specialist biotech funds are required by the SEC to file Form 13F-HR
within **45 calendar days** of each quarter end:

| Quarter end | 13F filing deadline | What it means |
|---|---|---|
| March 31    | **May 15**          | Q1 holdings are public |
| June 30     | **August 14**       | Q2 holdings are public |
| September 30| **November 14**     | Q3 holdings are public |
| December 31 | **February 14** (next year) | Q4 holdings are public |

Between deadlines, the funds DB is stable — the auto-refresh has nothing
to do. But during the ~2 weeks straddling each deadline, fund managers
are uploading their filings to EDGAR, and we want to pick them up as
they land. Outside those windows, the data is published; you're just
waiting for the next quarter.

Module 6 of the biopharmcatalyst pipeline uses the per-fund position
deltas from `2_Funds_parser/2_fundparser.db` as its third soft signal
(see [Module6.md](Module6.md)). Stale funds data quietly degrades that
signal's value. Auto-refreshing during the filing window means a
biopharmcatalyst run during that window picks up newly-filed funds
without you having to remember.

---

## The two trigger rules

When you start `run_3_Biopharmcatalyst_parser.bat`, the very first thing
the orchestrator does is call `scripts/3_auto_refresh_funds.py`. The
script reads two inputs:

1. **Today's date** (configurable via `--today` for testing).
2. **`MAX(period_of_report)` from `2_Funds_parser/2_fundparser.db::holdings`** — the most recent quarter the funds DB has been refreshed for.

Then it applies two rules:

### Rule 1 — within filing window (calendar-based)

If today falls within `[deadline − 7 days, deadline + 7 days]` of any of
the four quarterly deadlines, **trigger a refresh.**

Practical: roughly 8 weeks a year (2 weeks × 4 quarters) you'll see the
funds pipeline auto-run at the top of the biopharm run.

### Rule 2 — past the window, DB still on old quarter (catch-up)

If today is past the most recent deadline window AND the funds DB has
NOT been refreshed yet for that target quarter, **trigger a refresh.**

This is the "I missed the window last week, I'm running the pipeline
today" safety net. Without this rule, skipping a window once would
leave the funds DB stale for ~3 months until the next window.

### Outside both rules → skip

The script reports `skip` with a reason and exits 0. The biopharm
pipeline continues with the existing funds data.

---

## What runs when it triggers

The runner (`src/funds_refresh/runner.py`) subprocesses these
`2_Funds_parser` scripts in order, with the cwd set to
`2_Funds_parser/` and `PYTHONPATH=2_Funds_parser/src`:

| # | Step | Script |
|---:|---|---|
| 1 | M2 ingest 13F-HR | `2_ingest_13f.py` |
| 2 | M2 build report | `2_build_report.py` |
| 3 | M3 build universe | `3_build_universe.py -v` |
| 4 | M4a hard filters | `4_run_hard_filters.py -v` |
| 5 | M4b ranking | `4_rank.py -v` |
| 6 | M4c fundamentals (SEC EDGAR) | `4c_enrich_fundamentals.py -v` |
| 7 | M5 context packs | `5_build_context_packs.py -v` |
| 8 | Consensus builds HTML | `_q1_consensus_report.py --quarter <target> --prev-quarter <prev>` |

Steps 1–7 are the "essential" path. The first fatal exit code aborts
the chain. Step 8 (the consensus delta report) is **fail-open**: a
failure logs a warning but doesn't abort.

### What does NOT run

Anything beyond `2_Funds_parser` Module 5:
- `6_estimate_cost.py` — billing preview for the paid API
- `6_score.py` — calls the Anthropic API (costs money)
- `6_serve_report.py` — local HTTP server for the selection editor
- `7_track_outcomes.py` — forward-price tracking

These remain user-gated in `2_Funds_parser/run_2_Funds_parser.bat`
because they involve cost or interactivity. Per the standing
cost-approval rule, no auto-run path may invoke a billed API.

---

## What "consensus_builds" is

For each refresh, the runner also regenerates
`2_Funds_parser/Outputs/<YYYYQn>_consensus_builds.html` (e.g.
`2026Q1_consensus_builds.html`). This HTML is a delta report comparing
the latest quarter to the prior quarter: which biotech tickers have
the **most new specialist funds entering** in the latest quarter
vs the previous one, ranked by a `Δfunds × magnitude` signal.

Top 50 names from this report often map directly onto the
biopharmcatalyst pipeline's Module 6 fund-accumulation winners — the
two views are complementary.

The underlying script (`2_Funds_parser/scripts/_q1_consensus_report.py`)
keeps its legacy filename for backwards-compatibility but is now fully
parameterized by `--quarter` and `--prev-quarter` ISO-date arguments.
Run with no args, it auto-picks the two most-recent
`period_of_report` values in `holdings`.

---

## How to run / test it

The script is wired into `run_3_Biopharmcatalyst_parser.bat` as the
first step — you don't need to invoke it manually. But you CAN, for
testing or one-off catch-up:

```bash
# Default (called by the .bat): read today, check DB, decide.
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_auto_refresh_funds.py

# Dry-run: print the decision but skip the subprocess calls.
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_auto_refresh_funds.py --dry-run

# Test with a specific date (e.g., simulate the May 15 window).
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_auto_refresh_funds.py \
    --today 2026-05-15 --dry-run

# Ignore the calendar; run unconditionally. Target = most recent completed
# quarter as of today. Useful when you want a fresh consensus_builds report
# even though no filing window is active.
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_auto_refresh_funds.py --force
```

Test suite:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_funds_refresh_decision.py -v
```

21 tests covering all four quarters' window boundaries, catch-up
scenarios, DB-empty / DB-up-to-date / DB-ahead-of-target cases, and
between-window dates.

---

## What it explicitly does NOT do

- ❌ It does not modify `2_Funds_parser/2_fundparser.db` itself — only
  the subprocessed `2_ingest_13f.py` writes to it.
- ❌ It does not call the Anthropic API or any paid service.
- ❌ It does not block the biopharm pipeline on failure. If the funds
  refresh fails fatally, the .bat prints a warning and continues —
  funds data is enrichment, not a hard dependency for the biopharm
  pipeline to complete (M6 has a `--skip-funds` fallback).
- ❌ It does not check whether new filings have actually appeared on
  EDGAR. The calendar rule fires on date alone; the catch-up rule
  fires on DB freshness. Combined, these cover the practical case
  without needing to scrape EDGAR for new accession numbers.
- ❌ It does not silently bypass the funds DB if the refresh fails.
  The biopharm Module 6 still tries to ATTACH the funds DB at scoring
  time and will use whatever data is present (stale or fresh).

---

## The five files this feature is made of

```
src/funds_refresh/
  __init__.py
  decision.py        # pure logic: decide(today, latest_period_in_db)
  runner.py          # subprocess wrapper around 2_Funds_parser scripts

scripts/
  3_auto_refresh_funds.py   # CLI entrypoint, called by the .bat

tests/
  test_funds_refresh_decision.py    # 21 acceptance tests
```

`decision.py` is pure — no DB or filesystem I/O. Trivially unit-testable;
all 21 acceptance tests run in <1 second.

`runner.py` is the subprocess plumbing. Reads `latest_period_in_db()`
via SQLite read-only attach to `2_Funds_parser/2_fundparser.db`; calls
each step with the venv's Python interpreter and the correct cwd / env.

---

## Why these design choices

- **Pure-function decision logic.** Calendar math is finicky (45-day
  lag, year-boundary on the Q4 deadline). Putting `decide()` in a
  separate file with full unit tests means we caught the "Q4 deadline
  is in February of the next year" edge case before it shipped.

- **±7 day window.** The user spec is "within 1 week before to 1 week
  after the legal date." Tighter than ±3 days would miss the long tail
  of late filers (many funds file on the deadline day; some file 2–5
  days late). Wider than ±7 risks firing the auto-refresh in the slow
  middle weeks between quarters when nothing's happening. ±7 is the
  goldilocks zone.

- **Effective today = `date.today()`, not a snapshot anchor.** This is
  ONE place where we deliberately break the snapshot-anchored rule that
  applies elsewhere (M5/D3, M6 query plans). The auto-refresh is an
  IRL "what's the calendar say right now" decision — it would be
  nonsense to anchor it on a stored snapshot. Pass `--today` for tests.

- **Subprocess rather than in-process import.** `2_Funds_parser`'s
  scripts assume cwd = `2_Funds_parser/` and read various sibling files
  (`config/*.yaml`, `Input/list_of_funds.xlsx`, `2_fundparser.db`).
  Importing them in-process would require monkey-patching cwd; cleaner
  to subprocess with the right environment.

- **Fail-open on the consensus report but fail-hard on M2..M5.** The
  consensus report is a derived view — its failure leaves the rest of
  the funds pipeline complete and Module 6 of biopharm can still use
  the refreshed `2_fundparser.db`. An M2..M5 failure means the funds
  data wasn't actually refreshed, which IS load-bearing for Module 6.

- **The legacy filename `_q1_consensus_report.py` is kept** even though
  the script is now generic. The user has external references to that
  filename (it's been in the project for weeks). Output filename is
  now generic (`<YYYYQn>_consensus_builds.html`); script filename is
  not.

---

## Reading the decision after a run

After every pipeline start, the orchestrator prints one of:

```
[auto-refresh-funds] skip: today YYYY-MM-DD is past the YYYY-MM-DD filing window
                            (deadline YYYY-MM-DD); 2_Funds_parser already holds YYYY-MM-DD
```

```
[auto-refresh-funds] trigger: today YYYY-MM-DD is within the 13F filing window
                              [start, end] for quarter ending YYYY-MM-DD
[auto-refresh-funds] running 2_Funds_parser M2..M5 for target quarter YYYY-MM-DD …
  [OK            ] M2 ingest 13F-HR              (2_ingest_13f.py)
  [OK            ] M2 build report               (2_build_report.py)
  [OK            ] M3 build universe             (3_build_universe.py)
  [OK            ] M4a hard filters              (4_run_hard_filters.py)
  [OK            ] M4b ranking                   (4_rank.py)
  [OK            ] M4c fundamentals              (4c_enrich_fundamentals.py)
  [OK            ] M5 context packs              (5_build_context_packs.py)
  [OK            ] Consensus builds HTML         (_q1_consensus_report.py)
[auto-refresh-funds] consensus HTML: …/2_Funds_parser/Outputs/2026Q1_consensus_builds.html
[auto-refresh-funds] complete.
```

If any M2..M5 step fails, the corresponding line shows `[FAIL exit=N]`
and the stderr/stdout tail is printed for diagnosis. The biopharm
pipeline then continues regardless.

---

## TL;DR

The 3_Biopharmcatalyst pipeline knows about the 13F filing calendar.
When today is within ±7 days of a quarterly 13F deadline — or when
the funds DB is stale because you skipped a window — the biopharm
orchestrator auto-runs 2_Funds_parser modules 2–5 and regenerates the
consensus-builds HTML before its own Module 0 starts. Outside those
conditions it reports `skip` and exits in under a second. The Anthropic
API steps in 2_Funds_parser (Module 6) remain user-gated.

This keeps Module 6 of the biopharmcatalyst pipeline's
fund-accumulation signal fresh quarter-over-quarter without you having
to remember to run two pipelines manually.
