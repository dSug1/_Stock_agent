# M4 — Stage 4/5: code-side `p_model` + hybrid blend — explained

*Milestone M4 of the v0.3 build (2026-06-30). Closes the prediction loop: `p_claude` (M3) × `p_model`
(this) → `p_final`, the long-only ranked deliverable + the forward `ledger`. Pure/offline; validated
end-to-end. See `SPEC_momentum_parser.md` §8 (Decisions A/H/J) and §5 (K) + `decisions.md` D-2.*

## What M4 produces
The blended one-week up-probability per ticker, ranked long-only, written to `predictions` + `signals.md`,
with an open row in the `ledger` for forward scoring (§9). Example output:

| Rank | Ticker | p_final | p_claude | p_model | Δ | Conf. | Review |
|---|---|---|---|---|---|---|---|
| 1 | AAA | 82% | 70% | 99% | 0.29 | 0.14 | ⚠ |
| 2 | BBB | 17% | 15% | 20% | 0.05 | 0.19 | |

`p_final = 0.6·p_claude + 0.4·p_model`. AAA's legs disagree (Δ 0.29 > 0.25) → **review** flag + confidence
docked; BBB agrees → clean.

## The two legs, one target
Both legs estimate **P(up per the vol-normalized dead-band)** (M1 label), so they're comparable:
- **`p_model`** (`model.py`) — logistic over the signal composite + standardized momentum, blended with the
  ticker's own **historical label-up-rate** among comparable-composite days (`targets.label_series`, the
  re-target from the v0.1 raw-`close>close` leg). Pure, free.
- **`p_claude`** (M3) — read from `scores` via `store.latest_score(ticker, asof)`.

## Blend + confidence (`blend.py`, pure — Decisions A/H/J)
- `blend(p_claude, p_model, w, disagree_threshold)` → `(p_final, disagreement, review)`. With **no Claude
  leg** the model stands alone (no review flag, lower base conviction). A `calibrate` hook is threaded
  through (identity default) so isotonic/Platt calibration plugs in once the §9 backtest fits it — until
  then the blend is **INDICATIVE** (the report says so).
- `confidence = conviction × data_coverage × (1 − disagreement) × history_depth` (clamped [0,1]).
  `data_coverage` = fraction of the four dimensions actually populated (technical / media / search /
  catalyst) — a signal you can't find docks confidence rather than counting as evidence (6_Biotech rule 10).

## Stage 5 orchestration (`stage5_blend.py`)
Per live ticker: compute `p_model` → read `p_claude` → blend → confidence → write the blended `Prediction`
(schema **v4** added `p_claude`/`p_model`/`disagreement`/`review` to `predictions` via guarded, idempotent
ALTERs) → **append an open `ledger` row** with the directional call (`p_final ≥ up_call_threshold` → `up`)
and `σ_week`. Export (`stage4_export.py`) ranks long-only by `p_final` and shows the leg breakdown +
review flag, labelled INDICATIVE.

## How to run / verify
```sh
../.venv/Scripts/python.exe -m pytest tests/test_model_blend.py tests/test_stage5_blend.py -q
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --blend --tickers AAA,BBB   # blend + export
```
`test_model_blend.py` covers the label-aware `p_model` (bullish on uptrend, bearish on downtrend) and the
pure blend/confidence (weighting, clamp, model-only, agreement, disagreement). `test_stage5_blend.py`
covers the write-path: prediction row with leg components, ledger append + directional label, model-only
when unscored, and the review flag on disagreement. Verified end-to-end offline (the table above) and the
v3→v4 migration is idempotent on reopen.

## Status / follow-ups
- **INDICATIVE until §9.** The blend weight `w` (0.6) and the leg calibrators are placeholders — the
  historical backtest + the forward ledger settle pass (next milestone) earn them.
- The `ledger` rows are *opened* here; a daily **settle pass** (compute realized 5-day return → label →
  `store.settle_ledger`) and the Brier/reliability scoring are M5/§9.
- Orchestrator stage-renumber to v0.3 still deferred; M4 is reached via `--blend` (like `--harvest`/`--score`).

## What M4 unblocks
The pipeline now produces its actual deliverable (ranked long-only signals + report). §9 validation is the
last core piece: settle the ledger and run the PIT backtest to move the screen from INDICATIVE to trusted.
