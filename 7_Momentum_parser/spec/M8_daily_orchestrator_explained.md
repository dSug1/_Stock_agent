# M8 — daily orchestrator + runner reconcile — explained

*Milestone M8 of the v0.3 build (2026-06-30). Turns the seven discrete stage-flags into one daily command
(spec Objective #1: "an automated pipeline that runs daily"). See `SPEC_momentum_parser.md` §5 and the
project memory `feedback_update_daily_runner`.*

## What it does
`daily.run` sequences the whole pipeline in order, with the right cadence and the cost gate wired for
automation:

```
0a discover (WEEKLY, paid)  ->  1 prices  ->  0b gate  ->  2 harvest  ->  3 score (gated)  ->
5 blend + export  ->  §9 settle  ->  render
```

- **Discovery is release-calendar gated** — `discovery_due` runs Stage 0a only when the last run is older
  than `universe.discovery.cadence_days` (7). So the weekly Claude universe call fires ~once/week, not daily.
- **Scoring is automation-gated** — it runs only with `dispatch=True`, protected by `max_usd_per_run` ($5).
  This is the unattended replacement for the interactive `[y/N]`: the operator opts in once (the scheduled
  task passes `--dispatch`), and the hard cap prevents overspend. A **dry** daily run still produces a
  free, model-only `signals.md`.
- Everything else (gate, harvest, blend, export, settle, render) is free and runs every day.

## Wiring
- CLI: `scripts/7_momentum.py --daily` (DRY) / `--daily --dispatch` (live). Renders the HTML report after.
- `run_7_Momentum_parser.bat` now runs `--daily %*` — **DRY by default** (no spend); the scheduled live
  run is `run_7_Momentum_parser.bat --dispatch`. This is module 7's daily runner, analogous to module 1's
  `run_1_Stock_Picker.bat` (the repo's per-module daily-runner convention; the `1_not_used/` `daily_
  orchestrator.py` is module-1-specific, not a master driver, so it's left untouched).
- The pipeline now feeds itself: discovery → `discovery` table → gate → `universe_gate` →
  `investable_tickers()` → harvest/score/blend. No `--tickers` needed.

## Verified
Offline `--daily --no-fetch` dry run (seeded recent discovery + bars for 2 names): 0a correctly **skipped**
(not due), gate → 2 pass, harvest, **model-only blend** (`p_claude` None), `signals.md` ranked long-only,
ledger opened, settle (0 settled — horizon not elapsed), and all four artifacts written (`signals.md`,
`momentum_report.html`, `validation.md` exists). 90 offline tests pass (cadence gate + full dry sequence).

## Notes / what's still manual
- **First run** must discover (so there are candidates) — `--daily --dispatch` does it automatically when
  due, or run `--discover --dispatch` once.
- The legacy `--stage 1..4` flags remain for manual/debug use but are superseded by the named flags
  (`--harvest`/`--score`/`--blend`/`--settle`) and `--daily`; `stage3_probability.py` is superseded by
  `model.py`.
- Fetching all candidate bars daily via yfinance is the slow step (minutes for ~40 names) — fixed when the
  licensed provider lands.

## What M8 completes
The module is now a **single daily command** that discovers its universe, scores it, blends, ranks
long-only, and validates itself — DRY-safe by default, billed only on explicit `--dispatch` under the $5
cap. Remaining to production: out-of-sample calibration + forward-ledger accrual (trust), provider wiring,
yfinance→licensed, and the v2 feedback loop — no new architecture.
