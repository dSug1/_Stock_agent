# M22 — Tier-2 leading inputs: short interest + options-implied (explained)

*Fourth v0.5 milestone. The Tier-2 slice of the leading-input lever — positioning signals (squeeze fuel +
options-implied move) that anticipate a move without a catalyst. **Opt-in**, with staleness/ToS caveats.
Built 2026-07-01.*

## Why
M20 gave the forward-driver rubric OHLCV/RS leading inputs. M22 adds **positioning**: short-squeeze fuel and
options-implied expectations — two of the most genuinely forward, milestone-free drivers of hype-stock moves
(a squeeze or an outsized implied move is a setup, not a recap). These need external data, so they're gated.

## What it computes
- **Short interest** (`microstructure.short_features`): `squeeze_setup` ∈ [0,1] = high short-%-float ×
  high days-to-cover; `short_building` = short interest rising vs the prior settlement (pressure building).
- **Options-implied** (`options_features`): `implied_move_pct` (ATM straddle / spot to the nearest expiry ≥
  horizon), `atm_iv`, and `skew` (OTM put IV − call IV; positive = downside hedging, negative = call demand).
- Absent data → `no_data` (omitted, never bearish).

## How it's wired
- **`clients/short_options.py`** — provider-isolated, fail-open, lazy yfinance: `fetch_short_stats`
  (`Ticker.info`) + `fetch_options_iv` (`Ticker.option_chain`). Any failure → `{}`.
- **`stage2_harvest`** harvests them **only when `sources.short_options: true`** (default off; injectable
  clients for offline tests), storing to the `evidence` table as dims `short`/`options` (no schema change).
- **`rubric._leading`** folds the cached `short`/`options` evidence into the `leading` block; the LEADING
  SETUP prompt cites `squeeze_setup`/`skew` so Claude turns them into forward drivers (e.g. "high squeeze_setup
  + short_building = a squeeze driver").
- Config: `short_options:` (horizon, squeeze thresholds) + the `sources.short_options` opt-in flag.

## ⚠ Caveats (surfaced in code + config)
- **yfinance short interest is STALE** (~2×/month exchange settlement) and **options data is DELAYED** — both
  ToS-gray, like the OHLCV provider. This is a scaffold source: **swap for a licensed short/options feed before
  relying on it** (`data_provider_switch`). That's why it's opt-in and off by default.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_short_options.py -q
```
Full suite: **175 passing** (+4). Covers the pure squeeze/options features (no_data on absent), the opt-in
harvest via injected fakes (writes `short`/`options` evidence, folds into the bundle), and that it's **off by
default**. The yfinance parsing itself is fail-open and not unit-tested (network), like `market.py`/`gdelt.py`.

## Where the forward lever now stands
- ✅ Tier 1 (OHLCV + RS, M20) · ✅ **Tier 2 (short + options, M22, opt-in)** · ✅ driver-type/novelty validation (M21)
- ❌ Tier 3 (borrow rate / live options flow) — paid, out of scope.
- **Next:** once the ledger fills, couple M21's per-type / novelty skill back into conviction/ranking (it's
  diagnostic-only today).
