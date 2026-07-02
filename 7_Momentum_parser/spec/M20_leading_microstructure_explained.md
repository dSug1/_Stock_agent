# M20 — Leading microstructure signals (explained)

*Second v0.5 milestone. The Tier-1 slice of the "leading/forward INPUT signals" lever — gives the M19
forward-driver rubric real *forward* data to reason from. Built 2026-07-01.*

## Why
M19 gave the rubric the mandate to GENERATE forward drivers, but it still stood on a mostly backward input
set (past price, published media, scheduled catalysts). This adds genuinely **leading, pre-move** inputs so
the forward reasoning has something real to stand on — the difference between "invent a forward story" and
"read a coil that's about to release."

## Feasibility scoping (no paid data)
- **Tier 1 — OHLCV-only (BUILT):** coil / vol-compression, accumulation-distribution (CMF), breakout
  pressure. Computed from bars we already cache. Zero new provider, zero ToS risk.
- **RS-vs-benchmark:** needs SPY cached in the `bars` table — easy follow-up (fetch+store the benchmark).
- **Tier 2 (deferred, feasible-but-caveated):** short interest / days-to-cover (`yfinance.info`, stale
  ~2×/month), options-implied move / IV (`yfinance.option_chain`, delayed). Free but unreliable/ToS-gray.
- **Tier 3 (out):** borrow rates, real-time options flow, live short — paid.

## What it computes (`microstructure.py`, pure)
- **`coil(bars, short, long)`** → 0..1 volatility compression: 1 = current short-window realized vol is at
  the low end of its own recent range (tightly wound → energy building for expansion).
- **`cmf(bars, window)`** → Chaikin Money Flow in [-1,1]: volume-weighted buying(+)/selling(-) pressure
  (accumulation vs distribution), bounded and self-normalizing.
- **`bullish_divergence`** → accumulating (CMF>0) while price drifts down = quiet accumulation into weakness.
- **`breakout_pressure(bars, window)`** → 0..1 range position (1 = pressing the high), discounted when recent
  volume isn't confirming.
- **`leading_features`** → the dict above + a combined `leading_score` (first-pass; the rubric reasons over
  the components). `no_data` when too few bars (absent, not bearish).

## How it's wired
- `rubric.build_bundle` now includes a `leading` block (`rubric._leading`, computed from bars — pure, no
  network, no store change).
- The system prompt gains a **LEADING SETUP** section instructing Claude to treat these as *anticipatory* and
  to turn them into high-novelty `forward_drivers` (e.g. "tight coil + positive accumulation + no known
  catalyst = pre-breakout"). This is leading, explicitly not a run-up recap.
- Config: a `leading:` block (coil/cmf/breakout windows, divergence threshold).

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_microstructure.py -q
```
Full suite: **164 passing** (+6). Live check on real data: CLOV `coil 0.80 / cmf +0.10 / breakout 0.66` — a
coiled, mildly-accumulating setup, forward and independent of its stale catalysts.

## RS inflection (M20b — DONE)
Relative strength vs a cached benchmark (SPY): `stage1_prices.ensure_benchmark` fetches+stores the benchmark
in `bars` (wired into the daily price step), and `microstructure.relative_strength` adds `rs_momentum`
(established RS — contextual, can reflect a past run), `rs_inflection` (RS slope **accelerating** up = the
genuinely *leading* leadership turn), and `rs_slope`. Folded into `leading_features(..., bench_closes)` and
the bundle (omitted, not bearish, if the benchmark isn't cached). Config `leading.benchmark/rs_window/rs_scale`.
Live: CLOV `rs_momentum +0.49` (a strong outperformer) with `rs_inflection false` (no fresh turn) — the split
lets the rubric down-weight the run (low novelty) while still reading a genuine inflection when it appears.

## What's NOT here (next)
- **Tier 2** — short-interest/days-to-cover + options-implied move, with staleness/ToS caveats surfaced.
- **p_model integration** — the leading setup currently feeds only the rubric; a leading term in `p_model`
  is an optional later addition.
- **Driver-type feedback learning** — categorize the generated drivers and learn which types (incl.
  microstructure-setup drivers) actually precede moves (extends M16).
