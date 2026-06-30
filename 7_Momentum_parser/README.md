# 7_Momentum_parser

Daily trade-signal screener that computes a **probability of weekly (~5 trading-day) price change** per
ticker. Quantitative, zero-LLM, offline-testable. Spec: [`spec/SPEC_momentum_parser.md`](spec/SPEC_momentum_parser.md).

## Run
```sh
# from this folder, with the shared repo venv (..\.venv)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py            # full daily run
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --no-fetch # recompute from cache
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --stage 3  # one stage
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_render.py --open-browser
```
Or on Windows: `run_7_Momentum_parser.bat` (fetch → signals → probability → export → render).

## Pipeline
`Stage 0` universe (`config/universe.csv`) → `1` fetch OHLCV → `2` signals → `3` weekly probability →
`4` rank/export `Outputs/signals.md` → render `Outputs/momentum_report.html`.

## Tests
```sh
../.venv/Scripts/python.exe -m pytest tests/ -q   # 20 passing, all offline
```

## Status
**Scaffold (v0.1).** Probabilities are **INDICATIVE** until the backtest calibrates the model
(see `spec/decisions.md` D1). yfinance is the price provider — **local-only** per ToS; swap for a
licensed feed before any public deploy.
