#!/usr/bin/env python
"""Historical PIT backtest of the p_model leg → Outputs/backtest.md (spec §9.2).

  PYTHONPATH=src python scripts/7_backtest.py [--tickers AAPL,MSFT]
"""

from __future__ import annotations

import argparse
import sys

from momentum_parser import backtest, metrics
from momentum_parser.config import db_path, load_config, outputs_dir
from momentum_parser.store import Store
from momentum_parser.universe import UniverseRow, load_universe


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PIT backtest of the code-side p_model leg")
    ap.add_argument("--tickers", help="comma-separated subset (default: config universe + whatever has bars)")
    args = ap.parse_args(argv)

    cfg = load_config()
    store = Store(db_path(cfg))
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = [u.ticker for u in load_universe(cfg)] or \
                  [r["ticker"] for r in store.conn.execute("SELECT DISTINCT ticker FROM bars")]

    rows = backtest.backtest_universe(store, tickers, cfg)
    summ = metrics.summary(rows, min_n=int(cfg.get("validation", {}).get("min_samples", 100)))

    lines = ["# p_model backtest — historical PIT (OHLCV only)  (**INDICATIVE**)", ""]
    if summ.get("n", 0) == 0:
        lines += ["_Not enough history to backtest. Fetch more bars (Stage 1)._", ""]
    else:
        flag = " ⚠ UNDERPOWERED" if summ["underpowered"] else ""
        lines += [
            f"_{summ['n']} non-overlapping decisions across {len(tickers)} tickers{flag}._", "",
            f"- **Brier**: {summ['brier']}  _(0.25 = always-0.5; lower is better)_",
            f"- **Base rate**: {summ['base_rate']}",
            f"- **Up-call hit-rate**: {summ['up_call_hit_rate']} → "
            f"**{'beats' if summ['beats_base_rate'] else 'does NOT beat'}** base rate",
            "", "| Pred. bin | n | mean pred | observed up-rate |", "|---|---|---|---|",
        ]
        lines += [f"| {b['bin']} | {b['n']} | {b['mean_pred']} | {b['obs_rate']} |"
                  for b in summ["reliability"]]
        lines.append("")
    path = outputs_dir(cfg) / "backtest.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    store.close()
    print(f"[7] backtest -> {path} · {summ}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
