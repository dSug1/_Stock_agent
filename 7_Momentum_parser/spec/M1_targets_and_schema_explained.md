# M1 — Move-target label + store schema v2 — explained

*Milestone M1 of the v0.3 build (2026-06-30). The bedrock everything else computes against and writes to.
Offline, fully tested. See `SPEC_momentum_parser.md` §2 F, §8–§10 and `decisions.md` D-2.*

## Why this first
Three later stages all depend on **one shared definition of "what counts as a one-week up-move"** and **one
place to persist multidimensional evidence and predictions**:
- Stage 4's `p_model` is calibrated *to* the label.
- Stage 3's Claude prompt asks for `p_up` *defined by* the label.
- The §9 backtest and forward ledger *score against* the label and read/write the new tables.

Building the label and the schema first means none of those stages re-invent (or, worse, disagree on) the
target. So M1 is `targets.py` (the label) + store schema **v2** (the tables). Both are pure/offline — no
network, no Claude — so they're trivially testable and unblock everything.

## The label — `targets.py` (Decision F)
A forward **5-trading-day** move is classified relative to the stock's **own weekly volatility**:

```
up    if  fwd_5d_return >  +band_mult · σ_week
down  if  fwd_5d_return <  −band_mult · σ_week
flat  otherwise
```

`band_mult` defaults to **0.5** (`config probability.target.vol_band_mult`); `σ_week` is the std-dev of
**non-overlapping** weekly simple returns over the trailing `vol_window_weeks` (default 12). Long-only
trading acts on `up`.

- **Why vol-normalized:** a +5% week is *signal* in a sleepy name and *noise* in a meme stock. A fixed
  percent threshold would over-fire on the volatile names this screen targets. Normalizing makes the label
  comparable across the basket and across regimes.
- **Why non-overlapping σ:** overlapping weekly windows are autocorrelated and understate the true spread;
  non-overlapping blocks give an honest σ (and the same discipline the backtest uses, §9.2).
- **Why simple (not log) returns:** so `σ_week` and the forward return share units and the comparison is
  exact. For the small moves in the dead-band the two are near-identical anyway.
- **Fail-open:** if history is too thin to estimate σ (fewer than two weekly returns) or the forward window
  runs off the end of the series, `label_move`/`label_series` return **`None`** — a missing label, never a
  silently-guessed one (spec §12).

Functions: `weekly_sigma(closes)`, `forward_return(closes, i)`, `label_move(fwd, σ, band_mult)`, and
`label_series(closes)` which labels every historical bar using **only data up to that bar** (point-in-time,
no look-ahead) — this is the history the empirical `p_model` leg buckets and the backtest scores.

## Store schema v2 — four additive tables
Migration runs on `PRAGMA user_version` (1 → 2), additive, leaving the v1 tables (`bars`/`signals`/
`predictions`) untouched. Verified: a fresh DB lands at v2; an existing v1 DB upgrades in place; reopening
is a no-op.

| Table | Holds | Written by | Read by |
|---|---|---|---|
| `evidence` | per `(ticker, asof, dimension)`: a `score` + `features_json` + note | Stage 2 harvest | Stage 3 bundle |
| `catalysts` | per `(ticker, event_date, kind)`: **forward/scheduled** dates only (PDUFA / readout / earnings) | Stage 2 | Stage 3 (`next_catalyst`) |
| `scores` | per `(ticker, asof, run_id)`: tiered Claude output (`p_up/p_down/p_flat`, expected_return, conviction, dimensions, memo) + `config_hash` + `evidence_fingerprint` + `prompt_version` | Stage 3 | Stage 5 blend |
| `ledger` | per `(ticker, asof, run_id)`: the open prediction (`p_up`, `p_final`, `predicted_label`, `σ_week`) then its realized outcome | Stage 5 (append) + daily settle | §9.2 validation |

DAO helpers added: `upsert_evidence`/`get_evidence`, `upsert_catalysts`/`next_catalyst` (soonest forward
event), `write_score`/`scores_for_run`, and `append_ledger`/`settle_ledger`/`open_ledger` (the
open→realized lifecycle). `config_hash` + `evidence_fingerprint` + `prompt_version` on `scores` are the
re-open keys (6_Biotech D11/D19 pattern): a retune or new evidence re-scores a name; a prediction is
always attributable to a prompt version.

## How to verify
```sh
../.venv/Scripts/python.exe -m pytest tests/test_targets.py tests/test_store.py -q   # 13 of the 31 pass here
```
`test_targets.py` covers σ on flat/volatile/thin series, the dead-band thresholds, fail-open `None`, and
PIT alignment of `label_series`. `test_store.py` covers the v2 version stamp, evidence overwrite, the
forward-only `next_catalyst`, score ranking, and the ledger open→settle lifecycle.

## What M1 unblocks (next)
- **Stage 2** writes `evidence` + `catalysts`.
- **Stage 3** writes `scores`.
- **Stage 4** re-targets `probability.py`'s empirical leg to `targets.label_series` (instead of raw
  `close>close`).
- **Stage 5 / §9** append to and settle the `ledger`, and the backtest scores against `label_series`.
