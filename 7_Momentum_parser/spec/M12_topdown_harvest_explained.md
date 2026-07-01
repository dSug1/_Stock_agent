# M12 — Top-down harvest / Stage 2b (explained)

*Second v0.4 milestone (Decision L, spec §4b). Populates `macro_signals` with the day's ANTICIPATED
top-down read. Built 2026-06-30. Provider decisions: yfinance proxies · shared cached web_search
calendar+consensus · geopolitical stubbed (handoff §2).*

## What it is
The daily, basket-shared harvest of the top-down layer. It turns market data + one macro-calendar call
into a set of **anticipated, forward-looking** signal surprises written to `macro_signals` — the input the
model term (M13) and rubric (M14) will consume.

## Why (the design)
The screen was blind to macro/regime/rotation — the forces that dominate a 1-week move in a high-beta
basket (D-6). This stage supplies them, honouring **anticipate-never-recense (§2.3)**: continuous signals
read the recent *shift* (not the level), and dated signals are kept only while the event is still *ahead*
inside the forward window.

## How it works
- **`topdown.py` (pure)** — `classify_regime(vix, hy, ig)` → risk_on/neutral/risk_off from VIX level + HY/IG
  credit-spread trend; `continuous_surprises(series, cfg)` → a signed surprise in [-1,1] for each of
  `risk_regime, rates_usd, commodities, ai_crowding, style_factors, sector_flows`. First-pass signs
  (rising rates → headwind; AI basket outrunning market → tailwind; …) — **the M16 loop learns the real
  weights/signs**; this just gives a real, forward starting read. Missing proxy → 0.0 (absence ≠ signal).
- **`clients/econ_calendar.py`** — ONE shared `web_search` call/day (`build_request` + `parse_events`):
  upcoming FOMC/CPI/NFP/GDP with consensus → a signed `surprise` per `dated` monetary signal. `parse_events`
  enforces forward-only (drops past + beyond-horizon events) and collapses to the soonest per signal. Cost
  is amortized across the whole basket (cached), inside the $5/day gate. Untrusted web text = data (§4).
- **`stage2b_topdown.py`** — orchestrates: fetch proxies (injectable, fail-open per role) → write the 6
  continuous signals (active, regime-tagged) → if a scorer is present, run the econ call → write dated
  signals (active, `horizon_days`) → seed geopolitical signals `active=0` + flagged. Returns a funnel.
- **Config** — `topdown.proxies` (role→ticker), `topdown.regime` thresholds, `topdown.econ_calendar`,
  `topdown.geopolitical_stub`. All tunables in `config.yaml` (no magic numbers).

## Provider choices (operator, handoff §2)
1. **yfinance proxies** for cross-asset/rates (^VIX, ^TNX, DX-Y, CL=F, GC=F, HYG/LQD, BOTZ, IWF/IWD,
   SPHB/SPLV, SPY) — no key, no cost; swap to a licensed feed pre-deploy (`data_provider_switch`).
2. **Shared cached web_search** for the calendar + consensus (the only net-new spend; amortized).
3. **Geopolitical stubbed + flagged** (`active=0`) — prediction-market odds + news scan wired later.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_topdown.py -q
```
Full suite: **122 passing** (was 115; +7). Covers regime classification, surprise signs, forward-only
calendar parse (past/beyond-horizon dropped, soonest-per-signal, clamp), and the Stage-2b write path
(continuous + dated + stub; dry-run skips the paid call; fail-open on a failed proxy).

## What's NOT here (next)
- **Not yet wired into `daily.py`** — deliberately. `macro_signals` has no consumer until **M13** (the
  `p_model` top-down term) + **M14** (rubric context). Wiring the paid econ call into the daily command
  before a consumer exists would spend for nothing; daily wiring lands with M13/M14.
- **Per-ticker loadings `β_{t,s}` not initialized** — M13 needs them (factor regression over trailing
  returns is the planned init; market-scope signals default β=1). That's the first task of M13.
- Ticker catalysts (`catalyst_date/earnings_surprise/index_lockup`) are M15, not here.
