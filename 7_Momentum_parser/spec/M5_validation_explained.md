# M5 — §9 validation (ledger settle + PIT backtest) — explained

*Milestone M5 of the v0.3 build (2026-06-30). The last core piece: turns "INDICATIVE" from a label into a
measurement. Two validators (Decision E) — both offline, both proven on real data. See
`SPEC_momentum_parser.md` §9 and `decisions.md` D-2 (E).*

## Why two validators
The Claude leg can't be backtested historically (no archived web state to reconstruct), but the code
`p_model` leg *can* (OHLCV is point-in-time clean). So §9 splits, exactly as the spec requires:

| Part | What | Validates | File |
|---|---|---|---|
| **a — historical PIT backtest** | recompute `p_model` on past non-overlapping bars using only prior data; score vs realized label | the `p_model` leg, now | `backtest.py` + `scripts/7_backtest.py` |
| **b — forward live ledger** | settle each real daily prediction once its 5-day horizon elapses; score the track record | the full hybrid (Claude leg), over time | `validation.py` (`settle_pass`) |

## Metrics (`metrics.py`, pure)
Shared by both: **Brier** (mean (p_up − outcome)²; 0.25 = always-0.5), **base rate** (P realized=up),
**up-call hit-rate** (precision of acting on 'up' — long-only), **reliability** (per-bin mean-predicted vs
observed up-rate), and an **underpowered** flag when n < `validation.min_samples` (100 — the 5_Hype lesson).

## Part a — backtest (`backtest.py`)
For each non-overlapping decision bar (`step = horizon`, no overlapping windows → no autocorrelation
inflation), recompute `p_model` from **only past data** and score against the realized vol-normalized label.
Honest PIT throughout: σ_week uses data ≤ i, and the empirical leg only counts outcomes realized *before*
i (`j + horizon ≤ i`). Efficient: composites + PIT labels are precomputed **once** per ticker; the formula
mirrors `model.p_up`.

**Real result (MRNA, 87 decisions):** Brier **0.2507 ≈ 0.25** → the *uncalibrated* model has ~no skill yet;
the reliability curve shows it's **overconfident on the upside** (predicts 0.68–0.86 in the top bins,
realizes 0.2–0.38). This is the harness working — it tells you calibration is needed and the screen must
stay INDICATIVE, instead of pretending skill that isn't there.

## Part b — ledger settle (`validation.py`)
`settle_pass` walks open `ledger` rows; when a row's `asof + horizon` bar now exists, it computes the
realized return, labels it with the **same** dead-band (`targets.label_move`, using the σ_week stored at
prediction time), and writes it back via `store.settle_ledger`. Rows whose week hasn't elapsed stay open.
`build_report` scores the settled rows → `Outputs/validation.md`. This is the only validator that ever sees
the Claude leg, so it's the real arbiter — and it accrues slowly (non-overlapping weekly outcomes; n≥100 is
weeks-to-months of live running).

## How to run / verify
```sh
../.venv/Scripts/python.exe -m pytest tests/test_validation.py -q
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_backtest.py --tickers MRNA     # -> Outputs/backtest.md (free)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --settle           # -> Outputs/validation.md
```
Tests cover Brier known-values, the summary (beats-base + underpowered + reliability), the settle pass
(resolves an elapsed prediction to the right label; leaves an un-elapsed one open + writes the report), and
the backtest (uptrend → high up-rate, non-empty). Verified on real MRNA (87 decisions, Brier 0.25).

## Status / what's left to make it *trusted*
INDICATIVE remains correct: the backtest shows the raw `p_model` is at baseline + overconfident. To move to
trusted:
1. **Calibrate** `p_model` (isotonic/Platt on the backtest reliability curve) — wire into `blend.calibrate`.
2. **Accrue forward ledger** outcomes (n≥100) to validate the full Claude-blended `p_final`.
3. **Earn `w`** by comparing `p_claude`/`p_model`/`p_final` on the forward track record.
4. **Economic eval** — paper P&L net of costs/slippage on the $100k clip (the up-call hit-rate is the seed).
5. Wire the deferred media/search **PIT archives** so the multidimensional legs can be backtested too.

## What M5 completes
The core v0.3 pipeline is now whole: discover-gate (stub) → harvest → tiered Claude → blend → **measured**.
Remaining work is calibration + Stage 0a discovery + provider wiring + the orchestrator/daily-runner hookup,
not new architecture.
