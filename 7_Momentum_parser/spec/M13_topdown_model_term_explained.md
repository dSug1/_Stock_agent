# M13 — Regime prior + p_model top-down term (explained)

*Third v0.4 milestone (Decision L, spec §4b.4). The top-down harvest (M12) now actually shifts the
code-side probability, and per-ticker loadings β are initialized. Built 2026-06-30.*

## What it is
The consumer of `macro_signals`: for each ticker it computes a single log-odds shift
`logit(p_model) += gain · Σ_s w_s · β_{t,s} · surprise_s` over the day's ANTICIPATED signals, so regime,
rates, and the AI-rotation read move `p_model` — the first place the top-down meat reaches the output.

## Why (the design)
`p_model` was a purely bottom-up (technical) estimate. M13 adds the top-down term as an additive shift in
**log-odds space** so a strong regime/rotation read nudges the probability without ever forcing a
certainty, and so it's a clean **no-op when there's no harvest** (`delta=0`) — which keeps the historical
PIT backtest (no macro archive) and all prior behaviour unchanged.

## How it works
- **β resolution.** MARKET-scope signals feel the regime uniformly → **β=1**. Factor/sector signals use a
  **learned loading** initialized by `loadings.py`: OLS regression of the ticker's trailing daily returns on
  the factor-spread returns (`ai_basket−market`, `growth−value`, `hibeta−lowvol`). No loading yet →
  `beta_default` = 0 (no tilt until learned). Ticker catalysts (M15) not wired here.
- **Weight resolution.** `w_s` = the learned `signal_weights` row for the current regime, falling back to
  the `'all'` bucket, then 0. (Seeded from priors in M11; learned by M16.)
- **The shift.** `topdown_model.topdown_score` (pure) sums `w·β·surprise` over active signals;
  `logit_for(store, ticker, cfg, taxonomy)` gathers the rows and returns `gain·score`
  (`topdown.model_gain`, default 1.5). `probability.apply_logit_delta` applies it; `model.p_up` gained a
  `topdown_logit=0.0` param.
- **Consumer + wiring.** `stage5_blend` computes the shift per ticker and passes it into `model.p_up`.
  `daily.py` now runs Stage 2b + `loadings.refresh_universe` after harvest — **continuous legs are free and
  always run; the paid dated econ-calendar call fires only on `--dispatch`** (shared, cached, inside $5).

## First-pass, learned later
The surprise signs (M12) and the seeded weights (M11 priors) are a starting point; the **M16 feedback loop
learns** the real `w_s` and `β_{t,s}` from settled outcomes. M13 is the plumbing that makes them bite.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_topdown_model.py -q
```
Full suite: **128 passing** (was 122; +6). Covers the logit apply (monotone, no-op at 0, clamp), the score
math (market β=1 vs factor loading, missing-surprise skip), the β regression (recovers slope; positive β
for a correlated name), and end-to-end (a seeded risk-on harvest lifts `p_model` vs the plain leg).

## What's NOT here (next)
- **M14** — the Claude rubric variant-perception fields + a top-down context block (the `p_claude` leg
  reading the same signals). The report currently shows the term only via `p_model`.
- Loadings refresh runs every fetched daily run (cheap); a weekly cadence + reusing Stage-2b's already-fetched
  proxy series (avoid the double fetch) is a noted optimization.
