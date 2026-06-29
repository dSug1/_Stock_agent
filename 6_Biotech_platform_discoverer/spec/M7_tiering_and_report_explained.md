# M7 explained — tiering (upstream of Claude) + the interactive report

*Companion to `SPEC_acrivon_pattern_screener.md` §5.6 and `decisions.md` D5.*

---

## In one sentence

M7 turns the IPO-date signal into a **hard, operator-chosen filter applied before the Claude call** —
companies are bucketed into four market-cap × age tiers (+ an untiered bucket for missing data), the
operator picks which tiers to spend scoring budget on, and the results render in an interactive report
(tier tabs, seen-tracking, collapsible Claude memos, new-items filter).

## The four tiers (`tiering.compute_tier`)

Two cheap, already-captured signals — market cap (vs `$400M`) and years since IPO (vs `20yr`):

| tier | market cap | age since IPO | read |
|---|---|---|---|
| **1** | < $400M | < 20yr | small & young — the "early but real" priority zone |
| **2** | ≥ $400M | < 20yr | large & young |
| **3** | ≥ $400M | ≥ 20yr | large & old |
| **4** | < $400M | ≥ 20yr | small & old — the ship has likely sailed |
| **0** | cap or IPO date missing | — | **untiered** — surfaced, never hidden (recall-safe) |

Boundary: `cap < threshold` = small; `age < threshold` = young (so a value exactly at the threshold
counts as large / old). Thresholds live in `config.yaml → tiers`. Tier is computed on the fly, never
persisted, so it always reflects the current cap/IPO data.

**Tier 0 is deliberate.** The analyst named four tiers; missing data still needs a home. Rather than
guess a tier (which could hide a real candidate in a deprioritized bucket), an unknown cap or IPO date
lands in Tier 0 — recall-safe, and the operator can still choose to score it.

## The gate is upstream of Claude (`stage4`)

`stage4._candidates(..., tiers=…)` filters the DUE set by tier **before any API call**, so the
expensive Claude scorer only ever sees the tiers the operator selected. `due_tier_breakdown()` powers
the prompt; `run(..., tiers=…)` records `selected_tiers` in the estimate, the dispatch summary, and the
audit log.

## How to run

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_screen.py --stage 4            # interactive
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_screen.py --stage 4 --tiers 1,2 --dispatch
```

With no `--tiers`, Stage 4 prints due-candidate counts per tier and asks which to run:

```
Due candidates by tier (market cap × years-since-IPO):
  [1] Tier 1 · small & young          42
  [2] Tier 2 · large & young          11
  [3] Tier 3 · large & old             8
  [4] Tier 4 · small & old             5
  [0] Untiered · missing cap/IPO     515
Which tiers to run the Claude scorer on? (e.g. 1,2 / all):
```

`--yes` with no `--tiers` defaults to **all** tiers (non-interactive). The dispatch aborts cleanly if
the chosen tiers have zero due candidates.

> **Tiers need `ipo_date`.** It populates on the next Stage-0b `--enrich-yf` (yfinance
> `firstTradeDateEpochUtc`). Until then every company is **Tier 0**. The run `.bat` already enriches,
> so the next full run fills the tiers.

## The interactive report (`render.py`)

Rewritten from a single self-contained HTML into a **template + data-sidecar** pair (repo convention):

* `Outputs/screener_report.html` — stable template (CSS + JS + skeleton), rewritten only when its
  content hash changes.
* `Outputs/screener_report_data.js` — `window.__DATA = {…}`, rewritten every run. The only file the
  pipeline touches. Loaded via a sibling `<script src>` so it works under `file://` (double-click).

Features ported from `3_Biopharmcatalyst_parser`'s catalyst-scores report:

| feature | how |
|---|---|
| **tier tabs** | Tier 1 (default) → 4, Untiered, All — each with a live count |
| **green highlight + acknowledge** | every company starts NEW (pale-green); tick the checkbox in its expanded panel to mark reviewed. Persisted in `localStorage` (`pd_screener_acknowledged_v1`, PK = `company_id`). Unlike 3_, there is **no bootstrap-seed** — the screener has no "prior batch", so everything is new until you tick it |
| **collapsible Claude results** | click a row → panel with the rubric memo, A–E axes, moat + rationale, mechanisms, disconfirming evidence; unscored rows say which tier to re-run |
| **new-items filter** | review-status select: any / new only / acknowledged only — plus ticker search, min-composite, scored-only |

Duplicate company-ids collapse to one row via the Stage-5 shared-signal union (D4). Security: the
sidecar is JSON, the template escapes everything client-side, no external resources/hrefs.

## Tiering vs the Stage-5 lifecycle multiplier (D2)

They are complementary, not redundant: **tiering** is a hard upstream filter the operator controls
(*what gets scored*); the Stage-5 **`lifecycle_weight`** multiplier (off by default) is a downstream
re-ranking tilt (*ordering of what was scored*).

## Tests

`tests/test_tiering.py` (23): the four tiers + untiered + boundary, `age_years`, breakdown, the
selection parser, the Stage-4 tier gate + due breakdown + estimate, and the renderer payload (tier
assignment, dedup, scored flags, template-stable/sidecar-rewrites). Whole suite: **137 pass**.
