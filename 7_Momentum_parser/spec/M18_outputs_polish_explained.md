# M18 — Outputs polish (explained)

*Eighth and final v0.4 milestone (spec §11). Surfaces the new v0.4 signals in the deliverables. Built
2026-06-30. v0.4 is now feature-complete.*

## What it is
The v0.4 layers (top-down regime, variant perception, forward catalysts, the learning ledger) are now
visible in the two user-facing outputs.

## How it works
- **`signals.md`** (`stage4_export`) — two new columns via `_row_extras`:
  - **Days→cat** — days to the next scheduled catalyst (from `next_catalyst`), closing the §11 gap.
  - **Our view (variant)** — the rubric's `our_view` (from `scores.variant_json`), truncated + table-safe, so
    the differentiated thesis is on the ranked line, not just buried in the HTML.
- **`momentum_report.html`** (`render`) — two new header panels:
  - **Regime banner** (`_regime_banner`) — the macro regime the run was scored under + the anticipated
    signals/surprises from the latest harvest (empty when no harvest has run).
  - **Forward-ledger panel** (`_ledger_panel`) — the live running Brier / base-rate / up-call hit-rate from
    `metrics.summary(settled_ledger)`, with the honest `UNDERPOWERED (n<min_samples)` flag; graceful
    "no settled outcomes yet" when the ledger is empty.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_m18_outputs.py -q
```
Full suite: **153 passing** (was 151; +2). Verified on the live run (`run_20260630T160212Z`): the new columns
render (Days→cat / Our view show "—" for that pre-M14/M15 run, as expected) and the panels degrade gracefully
(no harvest → no regime banner; empty ledger → "no settled outcomes yet").

## v0.4 is feature-complete — what's next (not a milestone)
- **Live `--dispatch` run** — the natural real-world step: exercise the new prompt/schema end-to-end, harvest
  a real top-down read, and start filling the forward ledger the M16 loop learns from. First run is slow
  (yfinance + GDELT cold cache); cost gated at `max_usd_per_run`.
- Deferred provider work (unchanged): geopolitical arrival-odds, estimate-revision feed (true beat/miss),
  β re-learning from settled outcomes, PIT macro archive for backtesting the top-down legs, licensed data
  provider before any public deploy.
