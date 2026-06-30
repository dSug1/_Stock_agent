# M9 — out-of-sample (walk-forward) calibration validation — explained

*Milestone M9 of the v0.3 build (2026-06-30). Resolves the M6 honesty caveat (its Brier improvement was
in-sample) and is the biggest single lever on trust. Pure, offline, validated on a real 16-name universe.
See `SPEC_momentum_parser.md` §9 and `decisions.md`.*

## Why
M6 fit the isotonic calibrator and scored it on the **same** backtest data — an in-sample number that
could be pure overfitting. The only honest question is: does the calibration improve **held-out** data?
M9 answers it with time-ordered cross-validation.

## The harness (`cv.py`, pure)
`walk_forward(rows, n_folds, min_cal)`: sort the backtest decisions by date, split into chronological
folds, and for each fold **fit the calibrator only on the decisions that preceded it**, then apply it to
the held-out fold. Aggregate the out-of-sample Brier (raw vs calibrated). No look-ahead — a fold is never
calibrated on its own or future data. `7_calibrate.py` now reports **both** the in-sample and the
walk-forward OOS Brier on every run, with a `GENERALIZES / DOES NOT generalize` verdict. (Backtest rows now
carry their `asof` date so they can be ordered.)

## Real-data result (16 discovered names, 1,392 non-overlapping decisions)
```
IN-SAMPLE      Brier 0.2467 -> 0.2040
OUT-OF-SAMPLE  Brier 0.237  -> 0.204   (GENERALIZES, n_oos=1114)
```
The OOS improvement matches the in-sample one → **calibration is not overfitting; it generalizes.** The
backtest is no longer underpowered (n=1392):
- `base_rate` 0.292 (≈29% of weeks clear the +0.5σ up-band);
- `up_call_hit_rate` **0.330 > base 0.292** — a small but real directional edge;
- reliability shows consistent **overconfidence** (top bin predicts 0.85, realizes 0.44) that calibration
  corrects across the whole universe.

**Honest read:** modest skill (Brier ~0.20 vs the 0.25 always-0.5 baseline), measured *before* the
media/search dimensions are wired (still neutral stubs) and *before* the Claude leg's forward validation —
so there's headroom. But the `p_model` leg is now **validated out-of-sample**.

## How to run / verify
```sh
../.venv/Scripts/python.exe -m pytest tests/test_cv.py -q
PYTHONPATH=src python scripts/7_calibrate.py --tickers <universe>   # prints in-sample + OOS Brier (free)
```
Tests: insufficient-data guard, persistent-overconfidence **generalizes** (OOS Brier drops), already-
calibrated **no-harm**, and order-independence (rows sorted by date → no look-ahead from shuffling).

## Status / what's still pending for TRUSTED
- **`p_model` leg: out-of-sample validated** (this milestone). Edge is modest; expect lift once the
  media/search/catalyst dimensions are real (M2 seams) rather than neutral.
- **Full `p_final` (with Claude leg): still forward-only** — the live `ledger` must accrue n≥100 settled
  weekly outcomes; the Claude leg has no historical web state to OOS-backtest.
- **`w` (blend weight)**: earned once both legs have a forward track record.
- **Economic eval**: the up-call hit-rate (0.33) is the seed; add P&L net of costs/slippage on the $100k clip.

## What M9 completes
The screen's code-side leg now has an **honest, out-of-sample, generalizing** calibration — a real move
from "INDICATIVE, in-sample" toward trusted. Remaining is forward-ledger accrual + provider wiring +
economic eval + the v2 feedback loop — no new architecture.
