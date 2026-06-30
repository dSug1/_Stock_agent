#!/usr/bin/env python
"""Fit the p_model isotonic calibrator from the historical PIT backtest and persist it (spec §9, M6).

  PYTHONPATH=src python scripts/7_calibrate.py [--tickers AAPL,MSFT]

Re-run as more bars accrue — the seed of the v2 self-growing feedback loop. The Claude leg is calibrated
forward off the ledger (not here — no historical web state).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from momentum_parser import backtest, calibration, cv, metrics
from momentum_parser.config import db_path, load_config
from momentum_parser.store import Store
from momentum_parser.universe import load_universe


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fit + persist the p_model isotonic calibrator")
    ap.add_argument("--tickers", help="comma-separated subset (default: universe + whatever has bars)")
    args = ap.parse_args(argv)

    cfg = load_config()
    store = Store(db_path(cfg))
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = [u.ticker for u in load_universe(cfg)] or \
                  [r["ticker"] for r in store.conn.execute("SELECT DISTINCT ticker FROM bars")]

    rows = backtest.backtest_universe(store, tickers, cfg)
    pairs = [(r["p_up"], 1.0 if r["realized_label"] == "up" else 0.0) for r in rows]
    min_n = int(cfg.get("validation", {}).get("min_calibration_samples", 50))
    cal = calibration.fit_isotonic(pairs, min_n=min_n)

    before = metrics.brier(pairs)
    after = metrics.brier([(cal.apply(p), o) for p, o in pairs]) if pairs else None
    fitted = (before is not None and cal.bp)
    if pairs:
        store.save_calibrator("model", cal, len(pairs), datetime.now(timezone.utc).isoformat())
    # honest out-of-sample check (M9) — does the calibration generalize, or is it overfitting?
    oos = cv.walk_forward(rows, min_cal=min_n)
    store.close()

    if not pairs:
        print("[7] calibrate p_model: no backtest data (fetch more bars)")
        return 0
    print(f"[7] calibrate p_model: n={len(pairs)} | IN-SAMPLE Brier {before:.4f} -> {after:.4f} "
          + ("(fitted)" if fitted else "(IDENTITY — n<%d)" % min_n))
    if oos.get("insufficient"):
        print(f"[7]   out-of-sample: insufficient data (n={oos['n']}) — re-run on the full universe")
    else:
        verdict = "GENERALIZES" if oos["generalizes"] else "DOES NOT generalize (overfit)"
        print(f"[7]   OUT-OF-SAMPLE (walk-forward, n_oos={oos['n_oos']}): Brier "
              f"{oos['brier_raw_oos']} -> {oos['brier_cal_oos']} ({verdict})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
