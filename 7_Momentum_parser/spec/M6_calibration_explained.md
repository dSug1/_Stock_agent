# M6 — probability calibration (isotonic) — explained

*Milestone M6 of the v0.3 build (2026-06-30). The first concrete step from INDICATIVE toward trusted — and
the static seed of the operator's self-growing feedback loop (roadmap v2). Pure, offline, proven on real
data. See `SPEC_momentum_parser.md` §9 and `decisions.md` D-2 (H).*

## Why
M5's backtest measured the raw `p_model` as **overconfident**: its reliability curve predicted 0.68–0.86 in
the top bins but only realized 0.2–0.38. A calibrator maps raw probabilities to the **empirically-observed**
up-rate, so a "0.86" becomes whatever 0.86-predictions actually do. M4 already threaded a `calibrate` hook
through `blend`; M6 fills it with a fitted calibrator instead of the identity.

## The calibrator (`calibration.py`, pure)
**Isotonic regression via Pool-Adjacent-Violators (PAV)** — a monotonic, shape-free fit (no logistic/Platt
assumption, no sklearn; PAV is ~15 lines). `fit_isotonic(pairs)` takes `(p_raw, outcome01)` pairs, sorts by
`p_raw`, runs PAV on the outcomes, and compresses the step function to breakpoints. **Stays the identity
until `min_calibration_samples` (50)** — never calibrate on noise. `Calibrator.apply(p)` is a monotone
step lookup, clamped [0.01,0.99]. Serializable (`to_json`/`from_json`).

## Persistence + wiring
- Schema **v5** `calibration` table (one row per leg: method, params_json, n, fitted_at).
- `store.save_calibrator` / `load_calibrator` (returns identity if none fitted).
- `stage5_blend.py` loads `model` + `claude` calibrators and passes a `calibrate(p, leg)` dispatcher into
  `blend.blend` — so every blended `p_final` is calibrated before the weighting.
- `scripts/7_calibrate.py` fits the **model** leg from the backtest and persists it; re-run as bars accrue.
  The **claude** leg has no historical web state to backtest, so it stays identity and is calibrated
  *forward* off the ledger once n is large (and ultimately by the v2 feedback loop).

## Real-data proof (MRNA, 87 decisions)
`7_calibrate.py --tickers MRNA` → **Brier 0.2507 → 0.2013**; the overconfident 0.86 prediction maps to
**0.43** (its region's observed up-rate). The breakpoints cap the model's bullishness at ~0.43 — it's never
as confident as it claimed. ⚠ **In-sample** (fit + scored on the same data, n=87, one ticker) — out-of-sample
cross-validation on a real universe is the rigorous test; this proves the mechanism, not yet the edge.

## How to run / verify
```sh
../.venv/Scripts/python.exe -m pytest tests/test_calibration.py -q
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_calibrate.py --tickers MRNA   # fit + persist (free)
# subsequent --blend runs automatically apply the persisted calibrator
```
Tests: PAV monotonicity + total-preservation, identity-until-min-n, overconfidence-fix lowers Brier,
apply monotone+clamped, store round-trip, and `blend` applying the calibrator.

## Status / link to the feedback loop (roadmap v2)
M6 is **static** — fit once on the backtest. The operator-requested **self-growing feedback loop** is the
living version: periodic a-posteriori checks, per-dimension weight learning (which signals actually move the
market vs the calculated probability), regime-conditioned reweighting, and re-fitting as the ledger
accumulates. M6's `calibration` table + the forward `ledger` are the substrate it will grow on. Build it
once the forward ledger has real volume (n≥100) and the data providers are wired (`ROADMAP_remaining.md`).

## What M6 completes
The hybrid now self-corrects its known overconfidence. The screen stays INDICATIVE (out-of-sample edge +
forward ledger n≥100 still pending), but the math is honest and improving. Remaining: forward accrual +
economic eval + Stage 0a + providers + the v2 loop — not new architecture.
