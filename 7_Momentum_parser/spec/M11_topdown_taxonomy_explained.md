# M11 — Top-down signal taxonomy + store v7 (explained)

*First milestone of the v0.4 redesign (Decision L, spec §4b). Offline foundation for the top-down
market-perturbation layer. Built 2026-06-30.*

## What it is
The data + storage backbone for the top-down layer: a **taxonomy of market-perturbing signals** (the
things that move the whole market or cohorts of tickers — macro data, geopolitics, cross-asset regime,
factor rotation, plus idiosyncratic catalysts) and the four store tables that hold their **anticipated
state**, their **learned weights**, per-ticker **learned loadings**, and settled-move **attribution**.

## Why (the design)
The first real run was a low-value recap partly because it had **zero top-down context** (D-6). This
milestone lays the schema so the harvest (M12), the model term (M13) and the feedback loop (M16) have
somewhere to read/write. Two invariants are baked in from the start:
- **Priors ≠ live values.** `config/signals_taxonomy.yaml` holds *only* starting weights (`w_prior`).
  The **learned** `w_s` and `beta_{t,s}` live in the store and are owned by the feedback loop — the config
  is never rewritten by learning (spec §12).
- **Anticipate, never recense (§2.3).** `macro_signals.active` marks a signal as *currently anticipated*;
  once it prints it flips inactive and is attribution-only. The schema encodes the distinction.

## How it works
- **`config/signals_taxonomy.yaml`** — 18 signals across all 5 classes (monetary / geopolitical /
  cross_asset / rotation / ticker_catalyst), each with `{class, scope, schedule, anticipation, w_prior}`.
  Scope ∈ {market, sector, factor, ticker}; schedule ∈ {dated, continuous, probabilistic}.
- **`taxonomy.py`** — `load_taxonomy(cfg)` parses (`yaml.safe_load`) + validates (unique ids, enum
  membership, `w_prior ∈ [0,1]`, non-empty) → `[SignalSpec]`. Path is config-overridable
  (`topdown.taxonomy_file`) so tests never touch the shipped file. `regime_buckets()` = `['all'] + regimes`.
- **Store v7** (additive migration `user_version 6→7`) — four tables:
  - `macro_signals(asof, signal_id, active, surprise, regime, horizon_days, …)` — the anticipated state.
  - `signal_weights(signal_id, regime, w, w_prior, n)` — learned weight; `regime='all'` is the fallback.
  - `ticker_loadings(ticker, signal_id, beta, beta_prior, n)` — learned per-ticker sensitivity.
  - `attribution(ticker, asof, run_id, realized_return, market_comp, factor_comp, idio_comp, …)`.
  - `seed_signal_weights(taxonomy)` seeds priors with `ON CONFLICT DO NOTHING` — **re-seeding never
    clobbers a learned value**. `get_signal_weight(id, regime)` falls back to `'all'`.
- **`models.py`** — `MacroSignal / SignalWeight / TickerLoading / Attribution` dataclasses.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_taxonomy.py tests/test_store_v7.py -q
```
Full suite: **115 passing** (was 103; +12). The live `data/momentum.db` migrated 6→7 cleanly (37
predictions intact) and was seeded with 72 weight rows (18 signals × 4 buckets = all + 3 regimes).

## What's NOT here (next)
No data is harvested yet — `macro_signals` is empty until **M12** (top-down harvest / Stage 2b), which is
gated on the provider decisions (macro/consensus source, geopolitical arrival-odds scope, β-init,
surprise formula) in `.claude/7_Momentum_parser_v0.4_build_handoff.md` §2.
