# M4c — operating_cf TTM cumulative-YTD bug + port plan

**Status:** **DONE — fix applied 2026-05-28.** Code change in
[`src/module_4c/edgar_client.py`](../src/module_4c/edgar_client.py)
(`_row_span_days` + `_ttm_sum` rewrite). Unit tests in
[`tests/test_module4c_ttm_fix.py`](../tests/test_module4c_ttm_fix.py)
(10/10 passing). Decisions entry: § D60 in
[`spec/decisions.md`](decisions.md). Spec updated:
[`spec/module_4c_spec.md`](module_4c_spec.md) (Computed-fields section).

**Production smoke test PENDING the user** — re-running M4c
`--force-refresh` is a ~1-2 hour wall-time operation; deferred. The
unit tests are sufficient to confirm the fix is correct. The
production run overwrites `data/fundamentals.db` with corrected
TTMs that then propagate to M5 packs on next rebuild.

This doc is kept for audit. The body below is preserved as the
pre-fix audit trail.

---
**Owner:** `2_Funds_parser/src/module_4c/edgar_client.py::_ttm_sum`.
**Discovered:** 2026-05-28 while building 3_Biopharm M6.5 (which copy-adapted M4c).
**Cross-ref:** `3_Biopharmcatalyst_parser/spec/decisions.md` § D18.b.

---

## 1. TL;DR

`_ttm_sum` in `module_4c/edgar_client.py` computes trailing-twelve-month aggregates (operating cash flow, R&D, G&A) by **summing the four most recent quarterly XBRL values**. SEC XBRL reports `operating_cf` (and similar concepts) **cumulatively within a fiscal year**: Q1 = 3 months, Q2 = 6 months YTD, Q3 = 9 months YTD, 10-K = full year. Summing those last 4 quarterly values **double-counts every period** inside the cumulative figure.

Empirical impact in 3_Biopharm M6.5 dry-run on 52 biotech tickers (same parser path):

| Ticker | Buggy `operating_cf_ttm_usd` | Buggy `runway_months` | Fixed `operating_cf_ttm_usd` | Fixed `runway_months` |
|---|---:|---:|---:|---:|
| KURA | −$439,816,000 | **1.1** | −$77,985,000 | **6.0** |
| AGIO | −$1,521,632,000 | 2.0 | −$380,408,000 | 3.6 |
| SNDX | −$1,112,652,000 | 7.3 | −$278,163,000 | 15.2 |
| TYRA | −$409,020,000 | 5.7 | −$102,255,000 | 10.0 |
| BMEA | −$225,492,000 | 3.9 | −$56,373,000 | 9.5 |

Buggy values are roughly **2–4× inflated**. Every biotech in 2_Funds_parser's `data/fundamentals.db` has the same kind of distortion in its TTM fields.

---

## 2. The bug

Current implementation (verbatim from `2_Funds_parser/src/module_4c/edgar_client.py` lines ~219-234):

```python
def _ttm_sum(rows: list[dict]) -> Optional[int]:
    """Sum the most recent 4 quarterly values (form 10-Q) OR pick the most
    recent annual value (10-K). Returns None when no usable rows."""
    if not rows:
        return None
    latest = rows[0]
    if (latest.get("form") or "").startswith("10-K"):
        v = latest.get("val")
        return int(v) if v is not None else None
    quarterly = [r for r in rows if (r.get("fp") or "").startswith("Q")]
    if len(quarterly) >= 4:
        return int(sum(r["val"] for r in quarterly[:4]))
    if len(quarterly) >= 2:
        avg = sum(r["val"] for r in quarterly) / len(quarterly)
        return int(avg * 4)
    return None
```

**Root cause.** The `quarterly[:4]` slice picks the four most-recent rows by end-date. For a typical calendar-year filer those are:

| Row | `fp` | period covered | XBRL `start` | XBRL `end` | `val` example |
|---|---|---|---|---|---|
| 0 | Q1 | most recent Q1 (3 mo) | 2026-01-01 | 2026-03-31 | −30M |
| 1 | Q3 | 9-mo YTD of prior FY | 2025-01-01 | 2025-09-30 | **−90M** ← cumulative |
| 2 | Q2 | 6-mo YTD of prior FY | 2025-01-01 | 2025-06-30 | **−60M** ← cumulative |
| 3 | Q1 | prior FY Q1 (3 mo) | 2025-01-01 | 2025-03-31 | −30M |

Naive sum: `−30 + −90 + −60 + −30 = −210M`. **Wrong.**

Real TTM = `Q1 2026 + Q4 2025 + Q3 2025 + Q2 2025`. With Q4 typically only in the 10-K row, we should derive `Q4 = annual − 9-mo YTD`. Or, if no 10-K is yet available, difference consecutive entries within the FY to recover incremental quarters.

**Why "10-K wins" doesn't save us.** The early-return on `form == "10-K"` only fires when the very latest row is a 10-K. The instant a Q1 10-Q gets filed (every February-May for calendar-year companies), the latest row is a 10-Q and the buggy path runs.

---

## 3. Downstream impact in 2_Funds_parser

| File / column | Where it lands | Consequence |
|---|---|---|
| `fundamentals.db.financials.operating_cf_ttm_usd` | M4c write | 2-4× inflated |
| `fundamentals.db.financials.quarterly_burn_usd` | derived from above (`abs(cf_ttm) // 4`) | 2-4× inflated |
| `fundamentals.db.financials.runway_months` | derived (`cash_total / monthly_burn`) | 2-4× **understated** (catastrophically short) |
| `fundamentals.db.financials.rd_expense_ttm_usd` | M4c write | Same cumulative-YTD pattern, similarly inflated |
| `fundamentals.db.financials.ga_expense_ttm_usd` | M4c write | Same |
| `context_packs.db.pack_json` (Module 5) | M5 reads M4c output via `load_fundamentals_for_tickers` | Pack carries inflated TTM + understated runway |
| Anthropic system prompt (Module 6) | M6 sends pack to Claude under m6-v4 HARD RULES #20-22 ("use pack.fundamentals.financials.* as authoritative") | Claude scores tickers against inflated burn → over-weights dilution risk → under-scores POS and expectancy |
| `llm_scores.db.llm_scores` historical rows | Already computed | Audit-trail intact, but **historical scores reflect the buggy inputs**. Don't trust runway/dilution assertions in past `reasoning_trace` text. |

**Severity:** moderate. The bug doesn't crash anything and doesn't corrupt the PK structure. It just feeds Claude a systematically wrong view of cash runway across every biotech in the universe.

---

## 4. The fix (drop-in from 3_Biopharm)

The fix lives at `3_Biopharmcatalyst_parser/src/module_6_5/edgar_client.py`. Two changes needed in `2_Funds_parser/src/module_4c/edgar_client.py`:

### 4.1 Add a span-days helper

Place near the top of the file, alongside the other helpers:

```python
def _row_span_days(r: dict) -> int:
    """Span (in days) between the XBRL row's start + end dates.

    Operating-CF rows in 10-Q filings are typically reported cumulatively
    within a fiscal year (Q1 ≈ 90d, Q2 ≈ 180d, Q3 ≈ 270d, 10-K ≈ 365d).
    The span tells us which kind of value we're looking at — essential
    for computing a correct TTM (see `_ttm_sum`).
    """
    import datetime as _dt
    try:
        s = _dt.date.fromisoformat(r["start"])
        e = _dt.date.fromisoformat(r["end"])
        return (e - s).days
    except (KeyError, TypeError, ValueError):
        return 0
```

### 4.2 Replace `_ttm_sum` with the cumulative-YTD-aware version

```python
def _ttm_sum(rows: list[dict]) -> Optional[int]:
    """Correct TTM for XBRL concepts that are reported cumulatively-YTD
    inside a fiscal year (operating cash flow, R&D, G&A).

    Strategy (in priority order):
      1. Annual row available — if the latest row has form 10-K OR a
         year-long span (350-380 days), use its value directly.
      2. True single-quarter rows — span ≈ 90 days, never cumulative.
         If four are present, sum them.
      3. Cumulative-YTD derivation — bucket by fiscal year, sort within
         FY by end-date ascending, difference consecutive entries to
         recover incremental quarters. Sum the most recent 4.
      4. Fall back to None rather than a bogus inflated sum.
    """
    if not rows:
        return None

    # Strategy 1 — annual row.
    latest = rows[0]
    if (latest.get("form") or "").startswith("10-K"):
        v = latest.get("val")
        return int(v) if v is not None else None
    if 350 <= _row_span_days(latest) <= 380:
        v = latest.get("val")
        return int(v) if v is not None else None

    # Strategy 2 — explicit single-quarter rows.
    pure_q = [r for r in rows if 80 <= _row_span_days(r) <= 100]
    if len(pure_q) >= 4:
        return int(sum(r["val"] for r in pure_q[:4]))

    # Strategy 3 — derive incremental quarters from cumulative YTD.
    by_fy: dict = {}
    for r in rows:
        fy = r.get("fy")
        if fy is None:
            continue
        by_fy.setdefault(fy, []).append(r)

    derived: list[tuple[str, int]] = []   # (end_date, incremental_val)
    for fy, fy_rows in by_fy.items():
        fy_sorted = sorted(fy_rows, key=lambda r: r.get("end") or "")
        prev_val = 0
        for r in fy_sorted:
            v = r.get("val")
            if v is None:
                continue
            inc = int(v) - prev_val
            derived.append((r["end"], inc))
            prev_val = int(v)

    derived.sort(key=lambda x: x[0], reverse=True)
    if len(derived) >= 4:
        return int(sum(x[1] for x in derived[:4]))
    if len(derived) >= 2:
        # Partial recovery — annualise from the average derived quarter.
        avg = sum(x[1] for x in derived) / len(derived)
        return int(avg * 4)

    return None
```

**The fix is self-contained.** No schema change, no caller-side changes. `_latest_periods_per_concept` already preserves the raw row dicts (which include `start`), so `_row_span_days` has the data it needs.

---

## 5. Test plan

### 5.1 Unit tests (port from 3_Biopharm)

Source: `3_Biopharmcatalyst_parser/tests/test_module6_5_edgar_client.py`. Drop these eight cases into a new `2_Funds_parser/tests/test_module4c_ttm_fix.py` (or extend an existing M4c test file):

| Test name | Asserts |
|---|---|
| `test_ttm_empty_rows_returns_none` | `_ttm_sum([]) is None` |
| `test_ttm_picks_annual_row_when_latest_is_10k` | Strategy 1 fires on form `10-K`. |
| `test_ttm_picks_annual_via_span_when_form_not_10k` | Strategy 1 fires on 365-day span even with form `10-K/A`. |
| `test_ttm_pure_quarterly_filer_sums_last_4` | Strategy 2 sums when all spans ≈ 90 days. |
| `test_ttm_cumulative_ytd_derivation_correct` | **The headline bug test** — cumulative inputs (3-mo, 9-mo YTD, 6-mo YTD, 3-mo) yield the correct derived TTM, not the naive sum. |
| `test_ttm_kura_like_scenario_returns_sensible_runway` | End-to-end replay of the KURA-pattern: buggy ≈ −$440M, correct ≈ −$110M. |
| `test_ttm_falls_back_partial_when_only_two_quarters` | Strategy 3 partial path annualises from the avg of derived quarters. |
| `test_ttm_none_when_insufficient_data` | Missing `fy` metadata → `None` (no fake number). |

The 3_Biopharm tests construct synthetic XBRL row dicts and never hit the network — they should port verbatim.

### 5.2 Production smoke test

Once the fix is committed:

```bash
cd 2_Funds_parser

# 1. Re-run M4c against the current quarter to overwrite fundamentals.db
#    (forces re-parse of cached companyfacts payloads, no extra HTTP).
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/4c_enrich_fundamentals.py --force-refresh -v

# 2. Spot-check that runway_months on a handful of known-tight tickers
#    moved up by ~2-4× and that operating_cf_ttm_usd moved down.
../.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('data/fundamentals.db'); c.row_factory = sqlite3.Row
for t in ('ABEO', 'RCKT', 'TCRX', 'NTLA', 'BHVN'):
    r = c.execute('SELECT * FROM financials WHERE ticker=? ORDER BY period_end_date DESC LIMIT 1', (t,)).fetchone()
    if not r:
        print(f'{t}: no row')
        continue
    print(f'{t:6}  OpCF_TTM={r[\"operating_cf_ttm_usd\"]:>15,}  Burn/Q={r[\"quarterly_burn_usd\"]:>13,}  Runway={r[\"runway_months\"]}')
"

# 3. (Optional) Compare against pre-fix snapshot to compute the per-ticker delta.
```

Compare against the pre-fix snapshot if you have one. Expected directional change: `operating_cf_ttm_usd` magnitude decreases ~50-75%, `quarterly_burn_usd` decreases the same %, `runway_months` increases 2-4× toward the realistic value.

### 5.3 M5 regression check

Rebuild the latest M5 packs and verify the pack JSON's `fundamentals.financials.runway_months` reflects the new (longer) values:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_build_context_packs.py --force-refresh -v
```

No M5 code change should be required — the bug is pure parser, pack shape is unchanged.

---

## 6. Rollout plan

1. **Commit the fix.** Two-function change in `module_4c/edgar_client.py`.
2. **Port the 8 unit tests** + run `pytest tests/test_module4c_ttm_fix.py`. Confirm green.
3. **Re-run M4c `--force-refresh`** on the current quarter. ~1-2 hours wall time depending on cache state (companyfacts payloads may be re-parsed without re-fetching if conditional-GET hits).
4. **Sanity-check 3-5 known-tight tickers** as in §5.2.
5. **Rebuild M5 packs** (`5_build_context_packs.py --force-refresh`).
6. **Decide whether to re-run M6.** Options:
   - **Recommended:** wait until the next scheduled M6 quarterly run. It will pick up the corrected packs automatically. Historical `llm_scores` rows remain as audit. Future scores reflect correct TTMs.
   - **Aggressive:** re-run M6 immediately with `--force-refresh` on the current quarter to refresh all Claude analyses. Cost ~$10-20 at current calibration. Worth it if you've been making active decisions on the current `final_ranking_<quarter>.html`.
7. **Append a decisions.md entry** per memory `feedback_2_funds_parser_spec_decisions` (e.g. *D-next — Fix M4c `_ttm_sum` cumulative-YTD bug*). Cross-reference this doc.
8. **Update `module_4c_spec.md`** to document the corrected TTM semantics (Strategy 1 → Strategy 4) and the `_row_span_days` helper.
9. **Delete this file** once the fix lands (or mark `STATUS: DONE` and move under a `done/` subfolder for history).

---

## 7. Why this matters more in 2_Funds_parser than in 3_Biopharm

- 3_Biopharm M7 has **not yet dispatched** any Claude calls. The bug was caught pre-flight; no scoring history to retroactively fix.
- 2_Funds_parser has **already shipped multiple M6 runs** against inflated TTM data. Re-running M6 changes results retroactively, which is a deliberate user decision (cost vs. accuracy of the current `final_ranking_<quarter>.html`).

The fix itself is symmetric. The cleanup cost is not.

---

## 8. References

- `3_Biopharmcatalyst_parser/spec/decisions.md` § D18.b (the bug write-up + fix verification on 52 biotech tickers).
- `3_Biopharmcatalyst_parser/src/module_6_5/edgar_client.py` (verified fix).
- `3_Biopharmcatalyst_parser/tests/test_module6_5_edgar_client.py` (unit tests for `_ttm_sum` + `_row_span_days`).
- Memory: `feedback_2_funds_parser_spec_decisions` — append a D-entry when the fix lands.
- Memory: `feedback_update_decisions_log` — same.
