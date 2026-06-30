# 7_Momentum_parser — remaining-work roadmap

*Newest priorities first. Core pipeline M1–M5 is built; this is the path to TRUSTED + production + v2.*

## To reach TRUSTED
- **M6 calibration (in progress)** — static isotonic on the backtest reliability curve → `blend.calibrate`.
- Accrue the forward ledger to n≥100; earn the blend weight `w`; economic P&L (costs/slippage, $100k clip).

## Productionization
- Stage 0a weekly Claude discovery + 0b gate; reconcile orchestrator to v0.3 stage numbering; wire the
  daily runner. Swap yfinance → licensed provider. Wire GDELT/Trends/catalyst providers + PIT archives.

## v2 — self-growing feedback loop (operator-requested, 2026-06-30)
The living version of calibration. Periodic **a-posteriori checks** confirm/infirm past calculations; the
system learns **per-parameter / per-dimension weights** from where a signal actually moved the market vs the
calculated probability (which dimensions earn their weight), and **accumulates** this feedback so the model
**grows**: calibration that updates as the ledger fills, dimension-weight learning, regime-conditioned
reweighting. M6's static isotonic is the seed. Build once the forward ledger has real volume and providers
are wired.
