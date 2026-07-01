# M15 — Catalyst redefinition: forward fact + falsifiable hypothesis (explained)

*Fifth v0.4 milestone (spec §2.1-G revised). Turns a catalyst from a bare calendar date into two
anticipatory reads. Built 2026-06-30. Directly answers operator bullet 4.*

## What it is
A scheduled catalyst is now:
1. a **forward FACT** — `pre_event_accumulation`: is price/volume micro-structure *accumulating into* the
   known date right now? (rising price on rising volume = real positioning). This is happening pre-event, so
   it's anticipatory, not a recap of a result. For an earnings date it doubles as the expected-beat/miss lean.
2. a **falsifiable HYPOTHESIS** — `analog_drift`: a quantified predicted drift `expected_drift ± dispersion`,
   **recorded** in `catalyst_hypotheses` so the ledger can settle it vs realized and the M16 loop can learn.

## Why (the design)
The first run treated catalysts as public, priced calendar facts (recap). The operator's fix: a catalyst may
be a *forward fact* (accumulation, expected beat/miss) OR a *calculated, feedback-loopable guess* — always
forward. A bare known date is no longer a bullish signal by itself (that was the recap); the signal is the
**positioning into it** and a **falsifiable prediction** with a track record.

## How it works
- **`catalyst_signal.py` (pure)** — `pre_event_accumulation(bars, window, scale)` (price drift × volume
  confirmation → [-1,1]); `analog_drift(bars, horizon)` → `(expected_drift, dispersion)` from the ticker's
  forward-return distribution; `hypothesis(...)` assembles them for the soonest event inside the horizon
  (None otherwise — forward-only, drops past/beyond); `catalyst_score(hyp, cfg)` = accumulation
  (proximity-weighted) + a drift lean → the enriched `catalyst` evidence-dimension score.
- **Store v9** — `catalyst_hypotheses(ticker, asof, event_date, kind, days_to, accumulation, expected_drift,
  dispersion, horizon_days, realized_drift, settled_at)`; `upsert/settle/open_catalyst_hypotheses`.
- **`stage2_harvest.py`** — the catalyst block now computes the hypothesis, writes the enriched dimension
  (features carry accumulation/expected_drift/dispersion/days_to_catalyst/falsifiable), and **persists the
  hypothesis**. Falls back to the old proximity score only when nothing lands inside the horizon.
- The rubric bundle already carries the `catalyst` evidence, so Claude now sees the calculated guess + the
  accumulation fact (feeds the variant-perception read, M14).
- Config: `harvest.catalyst_accum_window` (5), `harvest.catalyst_accum_scale` (0.1).

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_catalyst_signal.py -q
```
Full suite: **139 passing** (was 133; +6). Covers accumulation sign + volume confirmation, analog drift,
forward-only hypothesis windowing, persistence/settlement, and the harvest wiring. The existing harvest test
was updated to the new semantics (a bare date now scores on accumulation/drift, not proximity). Live DB
migrated 8→9 (37 predictions intact).

## What's NOT here (next)
- **M16** — the feedback loop **settles** these hypotheses (realized_drift once bars exist) and, with
  `attribution`, learns `w_s`/`β_{t,s}`. M15 records the predictions; M16 closes the loop.
- A real **estimate-revision feed** for a true expected-beat/miss (today it's the accumulation/drift proxy) is
  a later provider, like the geopolitical stub.
- Ticker-scope catalyst signals still feed `p_claude` via the bundle (not the `macro_signals` basket table,
  which is market-level by design).
