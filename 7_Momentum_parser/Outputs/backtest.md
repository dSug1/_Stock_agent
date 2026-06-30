# p_model backtest — historical PIT (OHLCV only)  (**INDICATIVE**)

_1392 non-overlapping decisions across 16 tickers._

- **Brier**: 0.2467  _(0.25 = always-0.5; lower is better)_
- **Base rate**: 0.2917
- **Up-call hit-rate**: 0.3303 → **beats** base rate

| Pred. bin | n | mean pred | observed up-rate |
|---|---|---|---|
| 0.0-0.2 | 81 | 0.155 | 0.321 |
| 0.2-0.4 | 602 | 0.305 | 0.264 |
| 0.4-0.6 | 455 | 0.49 | 0.292 |
| 0.6-0.8 | 197 | 0.669 | 0.32 |
| 0.8-1.0 | 57 | 0.85 | 0.439 |
