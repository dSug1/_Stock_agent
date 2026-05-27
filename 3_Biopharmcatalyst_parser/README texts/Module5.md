# Module 5 — Catalyst timing extraction

**Plain-English purpose:** Module 5 answers the question "when is each
catalyst *actually* expected?" by deriving a `(date_min, date_max,
precision_tier)` tuple for every row in `catalyst_snapshots` and
storing it in a new table called `catalyst_timing`.

This is the single most important piece of business logic in the whole
ingest layer. Without it, every downstream filter that says "show me
catalysts in the next 60 days" would be hopelessly distorted — because
**BPC's `Catalyst Date` column is a lie**, and the real timing has to
be recovered from elsewhere in the row.

---

## The bucket-placeholder problem (why M5 has to exist)

When BPC doesn't know the precise day a catalyst will occur, they use
their own internal "bucket" convention to encode the granularity they
*do* know. A drug whose data is expected "sometime in 2026" gets dated
`31/12/2026`. A drug expected in the first half of the year gets
`30/06/YYYY`. A Q1 drug gets `31/03/YYYY`. Etc.

Five placeholder dates carry roughly 70% of BPC's date field:

| BPC `Catalyst Date` | Implied meaning |
|---|---|
| `31/12/YYYY` | full year — anywhere in the year |
| `30/06/YYYY` | 1H — first half (Jan–Jun) |
| `31/03/YYYY` | Q1 (Jan–Mar) |
| `30/09/YYYY` | Q3 (Jul–Sep) |
| `31/08/YYYY` | "end of summer" — Jul–Aug |

If you naïvely treat `Catalyst Date = 31/12/2026` as "this readout is
expected on December 31st," your downstream filtering will:

- Miss every catalyst whose real timing falls inside `T+14..T+60`
  (they all look like end-of-year events).
- Cluster a huge fraction of the universe into the last week of
  December, the last week of June, etc., as artificial spikes.
- Get the rate-of-appreciation math wrong, because the duration to
  the catalyst is hugely overstated.

The actual timing information is in two other places:

1. **The `Conference` column** when the readout is tied to a specific
   conference (BPC writes the conference's full date range there).
2. **The `Catalyst` text column** — free-form prose where the company's
   own guidance lives ("topline data expected 2H 2026", "Phase 1
   readout in Q3 2026", "PDUFA date set for May 24, 2026").

M5 reads those two columns, parses out the real timing, and writes
the result to a clean derived table that every later module can
range-query.

---

## What it actually does, step by step

When you run `scripts/3_5_compute_timing.py`:

1. **Picks the target snapshot(s).**
   Default: the most recent `snapshot_date` in `catalyst_snapshots`.
   Override with `--snapshot-date YYYY-MM-DD` for one specific
   snapshot, or `--all-snapshots` to recompute every historical
   snapshot (used after a `RULES_VERSION` bump — see §7.12 of the
   spec).

2. **Reads every row** of `catalyst_snapshots` at the target
   snapshot date, pulling only the columns the resolver needs:
   `(conference, catalyst_date, catalyst_text)` plus the five PK
   columns.

3. **Pre-loads the existing `catalyst_timing` PKs** for that snapshot
   — exactly like Module 1 does — so `rows_inserted` vs
   `rows_updated` can be tracked cleanly without having to interpret
   SQLite's INSERT-OR-REPLACE row counts.

4. **Per row, runs the three-lane resolver** (described in detail
   below). Every row produces exactly one `TimingResult`. There is
   no row-rejection path — rows that the resolver can't classify get
   `precision_tier = 'unknown'` instead.

5. **Writes everything in a single transaction.**
   `INSERT OR REPLACE INTO catalyst_timing`. On exception the whole
   batch rolls back; on success it commits.

6. **Stamps each row with `rules_version`** (default `"v1.0"` — the
   constant lives in `src/module_5/timing_rules.py`). When the rules
   change, you bump the constant and re-run with `--all-snapshots`;
   the `rules_version` column then tells you which rows were computed
   under which rules.

7. **Writes an `ingest_log` row** with `module = 'compute_timing'`,
   `input_ref = <snapshot_date>`, and the row counts. Same pattern
   as Module 1.

8. **Prints a precision-tier and source-lane breakdown** so you can
   eyeball whether the rules picked up something weird this run.

---

## The four-lane priority system

The resolver tries four lanes in strict priority order. **The first
lane that produces a result wins.** This is the single most important
behavioural rule of the module.

### Lane 1 — Conference-tied

**Trigger:** The `Conference` column contains a date range matching
`DD/MM/YYYY ET - DD/MM/YYYY ET` (BPC's exact format).

**Output:** `date_min` and `date_max` are the conference's start and
end days. `precision_tier = 'conference'`. `source_lane = 'conference'`.
`matched_phrase` records the raw text that matched (audit trail).

**Why Lane 1 beats Lane 2 even when both are present:** BPC defaults
the `Catalyst Date` for a conference-tied row to the conference's
**last** day. That's a false-specific — the presentation could be any
day of the conference. The full conference window is more accurate.

### Lane 2 — Specific company-disclosed date

**Trigger:** `Catalyst Date` parses as a valid date AND is **not** one
of the five known BPC placeholders.

**Output:** `date_min = date_max = catalyst_date`. `precision_tier =
'specific'`. `source_lane = 'catalyst_date_specific'`.

This catches things like PDUFA dates, M&A close dates, scheduled
investor days — events where the company has publicly committed to a
specific calendar day.

### Lane 3 — Text-parsed

**Trigger:** Lanes 1 and 2 both failed.

**What happens:** The `Catalyst` text column is run through 12 regex
patterns (see "The pattern set" below). Every match is computed into
a `(date_min, date_max, tier, matched_phrase)` tuple. Past matches
(where `date_max < today`) are discarded. The **earliest future
match wins** (smallest `date_min`).

The earliest-future rule reflects the empirical pattern: catalyst
text often mentions multiple events (e.g., "First patient dosed May
13, 2025. Data due in 2H 2026, with a pivotal trial due in early
2027"). The earliest *still-future* reference is almost always the
next catalyst.

### Lane 3b — Bucket fallback

**Trigger:** Text parsing produced no future matches AND `Catalyst
Date` IS one of the five placeholders.

**Output:** Maps the placeholder to its implied range:

| Placeholder | `date_min` | `date_max` | `precision_tier` |
|---|---|---|---|
| `YYYY-12-31` | `YYYY-01-01` | `YYYY-12-31` | `year` |
| `YYYY-06-30` | `YYYY-01-01` | `YYYY-06-30` | `half` |
| `YYYY-03-31` | `YYYY-01-01` | `YYYY-03-31` | `quarter` |
| `YYYY-09-30` | `YYYY-07-01` | `YYYY-09-30` | `quarter` |
| `YYYY-08-31` | `YYYY-07-01` | `YYYY-08-31` | `quarter` |

`source_lane = 'catalyst_date_bucket'`.

### Lane 4 — Unknown

**Trigger:** All lanes above failed.

**Output:** `date_min = date_max = NULL`, `precision_tier =
'unknown'`, `source_lane = 'unknown'`. Downstream filters exclude
these from window-membership tests.

This catches catalysts whose timing was never recoverable: empty
`Catalyst Date` AND empty `Conference` AND empty `Catalyst` text, or
text whose only temporal references are already in the past.

---

## The precision tier hierarchy

Each row's `precision_tier` describes how tight the resulting window
is. Module 6 will gate downstream filters by tier — for example, the
narrower **execution window** (T+14 to T+60) only admits rows with
high-precision tiers; the broader **discovery window** (T+14 to
T+180) admits all non-`unknown` tiers.

| Tier | Range width | Set by |
|---|---|---|
| `specific` | 1 day | Lane 2 (PDUFA-like dates) + occasionally Lane 3 |
| `conference` | 1–7 days | Lane 1 only |
| `month` | ~30 days | Lane 3 |
| `quarter` | ~91 days | Lane 3 + Lane 3b |
| `half` | ~183 days | Lane 3 + Lane 3b |
| `year` | ~365 days | Lane 3 + Lane 3b |
| `unknown` | NULL/NULL | Lane 4 |

---

## The pattern set (12 regexes, ordered)

The Lane-3 text parser runs these in order. Higher-precision patterns
**claim their text spans first**; lower-precision patterns whose match
overlaps a claimed span are skipped. This is what prevents `"May 24,
2026"` from also yielding a separate `"May 2026"` match.

| # | Pattern | Matches | Resolves to |
|---|---|---|---|
| 1 | `Month Day, Year` | "May 24, 2026", "Sept 4, 2026" | `specific` |
| 2 | `Day Month Year` | "24 May 2026" | `specific` |
| 3 | `Q3 2026` (or `Q3/2026`, `Q3-2026`) | quarter shorthand | `quarter` |
| 4 | `3Q 2026` | quarter shorthand reversed | `quarter` |
| 5 | `third quarter 2026` | quarter long-form | `quarter` |
| 6 | `1H 2026` (or `2H 2027`) | half shorthand | `half` |
| 7 | `H1 2026` | half shorthand reversed | `half` |
| 8 | `first half of 2026` | half long-form | `half` |
| 9 | `early/mid/late 2026` | relative-in-year | early=Q1, mid=Apr–Sep (half), late=Q4 |
| 10 | `YE 2026` / `FY 2026` / `by end of 2026` | year-end family | `year` |
| 11 | `May 2026` (any month) | month-year | `month` |
| 12 | `in 2026` / `during 2026` / `expected in 2026` | last-resort year | `year` |

**Why pattern 12 has narrow triggers** (`in`/`during`/`expected in`
rather than bare `\d{4}`): otherwise a sentence like "as we noted in
2024, the program continues" would match a year. The `in` requirement
plus the "future" filter (date_max ≥ snapshot_date) keeps stale
references out.

The `RULES_VERSION` constant in `src/module_5/timing_rules.py` is
`"v1.0"`. Bump it (and re-run with `--all-snapshots`) whenever the
patterns or the placeholder map changes.

---

## The output

### `catalyst_timing` (one row per catalyst_snapshots row)

| Column | Type | What it holds |
|---|---|---|
| `snapshot_date` | DATE | matches the source `catalyst_snapshots` row |
| `ticker` | TEXT | (PK) |
| `drug` | TEXT | (PK) |
| `nct_number` | TEXT | (PK) |
| `next_catalyst_type` | TEXT | (PK) |
| `date_min` | DATE | the earliest possible day this catalyst could happen. NULL only when `precision_tier = 'unknown'`. |
| `date_max` | DATE | the latest possible day. NULL only when `precision_tier = 'unknown'`. |
| `precision_tier` | TEXT | one of `specific / conference / month / quarter / half / year / unknown` |
| `source_lane` | TEXT | one of `conference / catalyst_date_specific / text_parse / catalyst_date_bucket / unknown` — tells you which lane produced the answer |
| `matched_phrase` | TEXT | the raw substring that matched (audit trail). NULL for Lane 2 and Lane 3b. |
| `computed_at` | TIMESTAMP | when this row was written (ISO UTC) |
| `rules_version` | TEXT | the `RULES_VERSION` constant active at compute time |

### `ingest_log` (one row per `compute_timing` run)

The standard audit row: `module = 'compute_timing'`, `input_ref =
<snapshot_date>`, `rows_in`, `rows_inserted`, `rows_updated`,
`rows_rejected` (always 0 — M5 doesn't reject), `status` (`success`
or `failed`), `started_at`, `finished_at`.

### Production results (snapshot 2026-05-27, 572 rows)

After running M5 against the M1-populated reference snapshot:

| `source_lane` | rows | typical content |
|---|---:|---|
| `conference` | 153 | ASCO + EHA + other June 2026 conferences |
| `catalyst_date_specific` | 63 | PDUFA dates, M&A closes, investor days |
| `text_parse` | 351 | quarter/half/year guidance from `Catalyst` text |
| `catalyst_date_bucket` | 5 | placeholder Catalyst Date + empty text |
| `unknown` | 0 | — |

572 rows in, 572 rows out, no row dropped. Conference count is higher
than the original spec's prediction (~30–60) because this snapshot
sits right before ASCO and EHA — a snapshot in November would have a
much smaller conference share. Spec §7.13 updated to "100–200 in
conference-heavy weeks."

---

## What it explicitly does NOT do

- ❌ It does not reach out to the internet.
- ❌ It does not check window membership (`date_min ≤ T+180 AND
  date_max ≥ T+14`). That's deliberately Module 6's job — it lets
  the same `catalyst_timing` row answer "is this in the discovery
  window?" *and* "is this in the execution window?" without
  re-classification.
- ❌ It does not detect date-slip across snapshots. If Drug X was
  classified `quarter` ending 2026-03-31 on the 2026-02-15 snapshot
  and now shows up as `quarter` ending 2026-06-30 on the 2026-05-27
  snapshot, that's a slip. Module 6 detects it by joining
  `catalyst_timing` to itself across snapshots; M5 only produces
  the per-snapshot timings.
- ❌ It does not reject rows. Every input row produces exactly one
  output row. `unknown` is the catch-all.
- ❌ It does not auto-trigger from Module 1. You run it explicitly so
  the `RULES_VERSION` of each run is auditable.
- ❌ It does not modify `catalyst_snapshots`. M5 is a pure derived
  layer over M1's data.

---

## How to run it

From the parser folder, with the shared venv active:

```bash
# Default: process the most recent snapshot_date in catalyst_snapshots
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py

# Specific snapshot
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py \
    --snapshot-date 2026-05-20

# Recompute every historical snapshot (after a RULES_VERSION bump)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py \
    --all-snapshots

# Pin a non-default version label, useful in dev
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py \
    --rules-version-override v1.1-dev
```

Or run `run_3_Biopharmcatalyst_parser.bat` — M5 is wired in with a
y/N gate immediately after the M1 ingest step.

To verify the rules still meet spec, run the test suite:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_compute_timing.py -v
```

39 tests: the 8 spec §7.10 reference fixtures (parametrized), the 9
§7.14 edge cases (lane precedence, multi-ref text, past-stripping,
unknown fallback), 18 pattern-coverage checks (one per regex/resolver
pair plus span-overlap protection), and 3 DB-pipeline checks
(5-lane synthetic snapshot, idempotent re-run, `ingest_log` emission).

---

## The five files M5 is made of

```
src/module_5/
  __init__.py              # empty — makes 'module_5' importable
  timing_rules.py          # RULES_VERSION + PATTERNS + BPC_PLACEHOLDER_DATES
  compute.py               # 3-lane resolver (pure functions, no DB)
  ingest.py                # DB orchestrator + ComputeStats + ingest_log

scripts/
  3_5_compute_timing.py    # CLI entry-point you actually run

tests/
  test_compute_timing.py   # 39 acceptance tests
```

**`timing_rules.py`** is the file you edit when you want to change
behaviour. It exports the 12 regex/resolver pairs as `PATTERNS`, the
`BPC_PLACEHOLDER_DATES` set, the `CONFERENCE_DATE_RANGE` regex, the
`bucket_from_placeholder()` helper, and `RULES_VERSION`. Each
resolver is a small function that takes a regex match and returns a
`TextMatch` dataclass.

**`compute.py`** exports two pure functions:
- `compute_timing_for_row(*, conference, catalyst_date, catalyst_text, today)`
  — the 4-lane resolver. Returns a `TimingResult`.
- `extract_text_match(text, today)` — the Lane 3 inner loop with
  span-overlap protection. Returns the earliest future `TextMatch`
  or `None`.

Both are trivially unit-testable because they don't touch the DB.

**`ingest.py`** is the orchestration: reads `catalyst_snapshots`,
loops calling `compute_timing_for_row`, transactional upsert into
`catalyst_timing`, writes `ingest_log`. Exports
`compute_timing_for_snapshot()`, plus convenience helpers
`latest_snapshot_date()` and `all_snapshot_dates()`.

**`3_5_compute_timing.py`** is the thin CLI: argparse, target-snapshot
selection, calls into `ingest.py`, prints the per-tier and per-lane
breakdowns.

---

## When you'll need to re-run M5

- **Every time Module 1 ingests a new snapshot.** The .bat wires this
  up with a y/N gate after M1.
- **Re-running on the same snapshot** is a safe no-op
  (`INSERT OR REPLACE` on the same PK; `rows_inserted = 0`,
  `rows_updated = N`).
- **After bumping `RULES_VERSION`** (because you added a new pattern,
  tightened an existing one, or changed the placeholder map), run
  with `--all-snapshots` to recompute every historical snapshot
  under the new rules. The `rules_version` column then makes it easy
  to query "which rows were computed under v1.0 vs v1.1" for
  diff-style debugging.
- **Never** as a side-effect of something else. M5 is always
  explicit.

---

## Why these design choices

A handful that aren't obvious from reading the code:

- **`today` is anchored on `snapshot_date`, not actual today.**
  Spec §7.6 says "filter to matches where `date_max >= today`"
  without defining today. The orchestrator passes `snapshot_date` as
  today, so re-running M5 on an old snapshot reproduces the same
  classification a user would have seen *on the day*. Anchoring on
  actual-today would silently reclassify old snapshots to `unknown`
  as time passes, destroying audit-replayability. The resolver
  accepts a `today=` parameter for tests, which is why the §7.10
  fixtures can use `today = 2026-05-27` deterministically.

- **Lane 1 strictly beats Lane 2 even when both are present.**
  Counter-intuitive at first — Lane 2's specific date *looks* tighter
  than Lane 1's 1–7 day range. But that "specific" date is BPC's
  fabrication (they default it to the conference's last day for any
  conference-tied row), whereas the conference range is the real
  truth.

- **Span-overlap protection on the regex set.** Without it, the input
  `"May 24, 2026"` would yield both a `specific` match (May 24) AND
  a separate `month` match (May 2026). The earliest-future tiebreak
  could then prefer the month match (date_min = 2026-05-01), giving
  a wider window than warranted. The fix: claim each match's text
  span; lower-precision patterns skip claimed spans.

- **`unknown` is a *catch-all*, not a *failure*.** Every input row
  produces exactly one output row. This is unlike Module 1 where bad
  rows are skipped and counted as `rejected`. Here, the schema
  guarantees a one-to-one mapping from `catalyst_snapshots` to
  `catalyst_timing`. Downstream code can `JOIN` without worrying
  about missing rows.

- **`rules_version` is a per-row column, not a per-snapshot column.**
  Because `--all-snapshots` might be interrupted partway through a
  bump. With the column on each row, a query like
  `SELECT rules_version, COUNT(*) FROM catalyst_timing GROUP BY 1`
  surfaces a partial rollout immediately.

- **`compute_timing_for_row` takes the *four fields it needs*, not a
  whole row.** Decouples it from `sqlite3.Row` and makes the tests
  call sites much cleaner — every §7.10 fixture is just four named
  keyword arguments.

- **No row rejection means no `rows_rejected` accounting on the
  happy path.** The field is still in `ComputeStats` for log-shape
  parity with Module 1 but is always `0` in practice. If you see a
  non-zero `rows_rejected` for `compute_timing`, something is wrong
  upstream — probably a `catalyst_snapshots` row with a malformed
  `catalyst_date` that broke `date.fromisoformat`.

---

## Reading the production DB after a run

Quick sanity dump showing the lane distribution + a discovery-window
preview (this is the join Module 6 will eventually do for real):

```bash
../.venv/Scripts/python.exe -c "
import sqlite3
from datetime import date, timedelta

c = sqlite3.connect('data/biotech.db'); c.row_factory = sqlite3.Row
snap = c.execute('SELECT MAX(snapshot_date) FROM catalyst_timing').fetchone()[0]
print(f'Latest snapshot: {snap}')
print()

# Lane breakdown
print('--- source_lane breakdown ---')
for r in c.execute('SELECT source_lane, COUNT(*) AS n FROM catalyst_timing WHERE snapshot_date = ? GROUP BY 1 ORDER BY n DESC', (snap,)):
    print(f'  {r[\"source_lane\"]:25s} {r[\"n\"]:>4d}')

# Discovery window preview: T+14..T+180 from snapshot
ref = date.fromisoformat(snap)
lo, hi = ref + timedelta(days=14), ref + timedelta(days=180)
print()
print(f'--- catalysts in discovery window {lo}..{hi} ---')
n = c.execute('''
  SELECT COUNT(*) FROM catalyst_timing
  WHERE snapshot_date = ?
    AND precision_tier != 'unknown'
    AND date_min <= ?
    AND date_max >= ?
''', (snap, hi.isoformat(), lo.isoformat())).fetchone()[0]
print(f'  {n} of 572 rows fall in the window')
"
```

For the 2026-05-27 snapshot you should see roughly 400+ rows in the
discovery window (T+14 to T+180) — most of the universe with the
unknowns and the past-already-happened catalysts filtered out.

---

## TL;DR

Module 5 turns BPC's deceptive `Catalyst Date` placeholders into honest
`(date_min, date_max, precision_tier)` ranges by reading the
`Conference` and `Catalyst` text columns and falling back to bucket-
implied ranges where neither helps. Four-lane priority resolver:
conference > specific > text-parse > bucket-fallback > unknown. Pure
functions with no DB I/O underneath; orchestrator handles the
transaction, the `ingest_log`, and the per-row upsert. Every input row
produces exactly one output row. Snapshot-anchored "today" makes
re-runs reproducible across time. Run it after every Module 1 load,
and after any `RULES_VERSION` bump.
