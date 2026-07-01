# M16 — The feedback loop (explained)

*Sixth v0.4 milestone (spec §4b.3). The keystone: the top-down priors become a self-improving system.
Built 2026-06-30. Directly answers the operator's "feedback loop those weights."*

## What it is
Once outcomes settle, the loop (a) settles each open catalyst hypothesis, (b) records attribution, and
(c) **learns the signal weights `w_s`** from whether each signal actually anticipated the realized move — so
the model grows as the ledger fills, instead of running on fixed priors forever.

## Why (the design)
The operator asked for weights that are *assigned and then feedback-looped*. M11–M15 assigned priors and
made them bite; M16 closes the loop. A signal earns weight only by demonstrably anticipating moves —
regime-conditionally, and shrinkage-regularized so it stays at its prior until it has real evidence.

## How it works
- **Settlement** — `settle_catalyst_hypotheses`: for each open `catalyst_hypotheses` row whose horizon has
  elapsed, fill `realized_drift` (forward return over the horizon), closing the M15 falsifiable predictions.
- **Attribution** — `attribute` (pure): split a settled move into market / factor / idiosyncratic predicted
  contributions (`Σ w·β·surprise` by scope; idio = residual). Recorded to `attribution` as a diagnostic.
- **Weight learning** — for every settled ledger prediction, look up the top-down read archived at its
  `asof`; for each active signal, score a **directional hit** (`sign(β·surprise) == sign(realized)`).
  Accumulate hits/n per `(signal, regime)` and per `(signal, 'all')`, then:
  - `signal_skill = 2·(shrunk_hit_rate − 0.5) ∈ [-1,1]` (shrinkage pseudocount = `learning.shrinkage_prior_n`);
  - `w_s = clamp(w_prior · (1 + skill), 0, topdown.weight_max)`.
  A reliable anticipator earns weight (up to the cap); a coin-flip stays at prior; an anti-signal collapses
  toward 0. Written via `set_signal_weight` — and read straight back by `topdown_model` (M13), so the model
  self-improves. β loadings stay the M13 regression (return-based β learning noted as a later refinement).
- **Wiring** — `feedback.run` is called in the daily `--settle`/`--daily` pass (after the ledger settle) and
  by `scripts/7_momentum.py --settle`. Graceful no-op when nothing has settled or no top-down read is
  archived for that day.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_feedback.py -q
```
Full suite: **145 passing** (was 139; +6). Covers the skill/weight math (shrinkage, cap, anti-signal→0),
attribution split, catalyst settlement, end-to-end learning (a settled prediction updates the regime weight +
records attribution), and the graceful empty no-op. Verified `feedback.run` on the live DB is a clean no-op
(no harvest archived yet).

## The loop, end to end (what now grows)
harvest (M12) → `p_model` term + `p_claude` rubric (M13/M14) → predictions → **ledger settles** → **M16 learns
`w_s`** → next run's `topdown_model` reads the new weights. Trust still accrues via the forward ledger (n≥100,
earn `w`, economic P&L) before the screen leaves INDICATIVE.

## What's NOT here (next)
- **M17** — fold the post-mortem fixes (stub-dims-as-negative, GDELT artifacts, Opus band, discovery
  retail-gate) now that the rubric/model are rewritten.
- **M18** — outputs polish (days-to-catalyst + our_view in signals.md, ledger Brier + regime header in HTML).
- β re-learning from settled outcomes (return regression) — currently the M13 price-history regression.
