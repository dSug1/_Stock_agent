#!/usr/bin/env python
"""Labeled point-in-time panel + kill-switch CLI (Protocol sections 2-4).

    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --seed-anchors
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --returns -v     # yfinance fwd returns
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --list
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --killswitch     # run the premise test

The panel here is the 7 pre-registered anchors only (sanity rails). A valid kill-switch needs
n>=100 PIT-reconstructed names (Protocol 2.3) + their PIT features; until then --killswitch reports
underpowered.
"""

import argparse
import logging
import math
import sys
from collections import Counter
from datetime import date

import yaml

from hype_parser import (db, fundamentals, killswitch, mshare, panel, panel_builder, panel_strata,
                         prices, themes)

DEFAULT_DB = "data/hype.db"
ANCHORS_CFG = "config/panel_anchors.yaml"
PANEL_CFG = "config/panel.yaml"

log = logging.getLogger("5_panel")


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _default_fetch_prices(ticker, start, today):
    return prices._default_fetch(ticker, start, today or date.today().isoformat())


def _default_fetch_float(ticker):
    """Current shares/float proxy (NON-PIT — crude only). Tries fast_info then get_info."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        try:
            fi = t.fast_info
            s = getattr(fi, "shares", None)
            if s:
                return float(s)
        except Exception:
            pass
        info = t.get_info()
        for k in ("floatShares", "sharesOutstanding"):
            v = info.get(k)
            if v:
                return float(v)
    except Exception as exc:
        log.info("float lookup failed for %s: %s", ticker, exc)
    return None


def _has_facts(conn, ticker):
    return conn.execute("SELECT 1 FROM company_facts WHERE ticker=? LIMIT 1",
                        (ticker.upper(),)).fetchone() is not None


def build_crude(conn, cfg, *, fetch_prices=None, fetch_float=None, themes_filter=None,
                today=None, rebuild=False, log_fn=print):
    """Build the crude indicative panel (decision D14): mechanical t0 + PIT feature proxies for every
    EDGAR-linked candidate, price-only labels, then the crude kill-switch. Returns
    (rows_built, skips, verdict). Fetchers are injectable so this runs offline in tests."""
    fetch_prices = fetch_prices or _default_fetch_prices
    fetch_float = fetch_float or _default_fetch_float
    today = today or date.today().isoformat()
    cr = cfg["crude"]
    horizons = cfg["horizons_weeks"]
    prim = cr["primary_horizon_weeks"]

    if rebuild:
        conn.execute("DELETE FROM panel_features WHERE panel_id IN "
                     "(SELECT panel_id FROM panel WHERE label_source='crude_derived')")
        conn.execute("DELETE FROM panel_returns WHERE panel_id IN "
                     "(SELECT panel_id FROM panel WHERE label_source='crude_derived')")
        conn.execute("DELETE FROM panel WHERE label_source='crude_derived'")
        conn.commit()

    # Benchmark price series per sector ETF (fetched once, reused across that sector's themes) — the
    # relative-drawdown denominator. A missing benchmark falls back to absolute drawdown per theme.
    bench_cache = {}

    def _bench_close(ticker):
        if ticker not in bench_cache:
            try:
                bench_cache[ticker] = panel_builder.monthly_close(
                    fetch_prices(ticker, cr["price_start"], today))
            except Exception as exc:
                log.info("benchmark fetch failed for %s: %s", ticker, exc)
                bench_cache[ticker] = {}
        return bench_cache[ticker]

    rows_built, skips = [], []
    for th in themes.list_themes(conn):
        tid = th["theme_id"]
        if themes_filter and tid not in themes_filter:
            continue
        series = themes.read_series(conn, tid)
        periods = [r["period"] for r in series]
        ns_by = {r["period"]: r["n_spec"] for r in series}
        nm_by = {r["period"]: r["n_main"] for r in series}
        full, nsf, nmf = panel_builder.contiguous_series(periods, ns_by, nm_by)
        if not full:
            continue
        bench_ticker = cr.get("benchmarks", {}).get(tid)
        bench_mc = _bench_close(bench_ticker) if (cr.get("use_relative_drawdown")
                                                  and bench_ticker) else None
        for tk_row in themes.read_theme_tickers(conn, tid, limit=cr["max_per_theme"]):
            ticker = tk_row["ticker"]
            try:
                daily = fetch_prices(ticker, cr["price_start"], today)
            except Exception as exc:
                skips.append((ticker, tid, f"price fetch failed: {exc}"))
                continue
            if not daily or len(daily) < 30:
                skips.append((ticker, tid, "no/short price history"))
                continue
            mc = panel_builder.monthly_close(daily)
            t0m, sig = panel_builder.mechanical_t0(
                full, nsf, nmf, mc, L=cr["L"], beta_min=cr["beta_min"], p_max=cr["p_max"],
                use_p_main_gate=cr["use_p_main_gate"], cheap_drawdown=cr["cheap_drawdown"],
                dd_lookback=cr["dd_lookback_months"], min_history=cr["min_history_months"],
                min_n_spec=cr["min_n_spec_level"], bench_close=bench_mc,
                use_relative_dd=cr.get("use_relative_drawdown", False),
                relative_cheap=cr.get("relative_cheap", 0.10))
            if not t0m:
                skips.append((ticker, tid, "never passed both gates"))
                continue
            t0_date = next((d for d, _ in daily if d[:7] == t0m), None)
            if not t0_date:
                skips.append((ticker, tid, f"no trading day in t0 month {t0m}"))
                continue

            def _slice(_tk, start, end, _daily=daily):
                return [(d, p) for d, p in _daily if start <= d < end]

            rr = prices.forward_returns(ticker, t0_date, horizons, fetch=_slice, today=today)
            prim_stats = rr.get(prim, {})
            fwd_prim = prim_stats.get("fwd_return")
            if fwd_prim is None:
                skips.append((ticker, tid, f"t0={t0m} too recent for {prim}w horizon"))
                continue

            # m_share label clause (Stage B): split the run into re-rating vs fundamental growth,
            # using PIT fundamentals at t0 and t0+H. Falls back to price-only when no fundamentals
            # are loaded for this ticker (run scripts/5_fundamentals.py --fetch to enable).
            ms = {"m_share": None, "mode": "no_fundamentals"}
            if cr.get("use_mshare", True) and _has_facts(conn, ticker):
                tH = prices._add_weeks(t0_date, prim)
                f0 = fundamentals.fundamentals_as_of(conn, ticker, t0_date)
                fH = fundamentals.fundamentals_as_of(conn, ticker, tH)
                ms = mshare.decompose_mshare(
                    prim_stats.get("start_price"), prim_stats.get("end_price"),
                    f0["revenue_ttm"], f0["shares"], fH["revenue_ttm"], fH["shares"],
                    min_revenue=cr.get("min_revenue_usd", 25_000_000))
            label = mshare.mshare_label(fwd_prim, ms["m_share"], hit_return=cr["hit_return"],
                                        m_share_min=cr.get("m_share_min", 0.50))

            feats = panel_builder.pit_features(tid, sig)
            feats["era"] = panel_builder.era_feature(t0m)
            if ms["m_share"] is not None:
                feats["m_share"] = ms["m_share"]
            ff = None
            try:
                ff = fetch_float(ticker)
            except Exception as exc:
                log.info("float fetch failed for %s: %s", ticker, exc)
            feats["free_float"] = math.log1p(ff) if ff else None

            pid = panel.upsert_panel_row(conn, {
                "name": ticker, "ticker": ticker, "t0_date": t0_date,
                "regime": panel_builder.theme_regime(tid), "theme": tid,
                "mispricing_mode": "value", "label": label, "label_source": "crude_derived",
                "notes": f"crude t0={t0m}; dd={sig['drawdown']:+.0%}; mshare={ms['mode']}"})
            for h, st in rr.items():
                panel.write_returns(conn, pid, h, st)
            for k, v in feats.items():
                if v is not None:
                    panel.set_feature(conn, pid, k, v)
            rows_built.append({"pid": pid, "ticker": ticker, "theme": tid, "t0": t0_date,
                               "label": label, "fwd": fwd_prim, "narrative": feats["narrative"],
                               "free_float": feats["free_float"], "m_share": ms["m_share"],
                               "ms_mode": ms["mode"]})

    # Impute missing free_float with the panel median so those rows aren't dropped from the crude
    # regression (current-float lookups are flaky; this is a crude control anyway).
    ffs = sorted(r["free_float"] for r in rows_built if r["free_float"] is not None)
    if ffs:
        med = ffs[len(ffs) // 2]
        for r in rows_built:
            if r["free_float"] is None:
                panel.set_feature(conn, r["pid"], "free_float", med)
                r["free_float"] = med

    # Crude kill-switch: continuous forward return ~ narrative + crude controls, over the crude rows.
    ks_rows = []
    for r in rows_built:
        feats = panel.read_features(conn, r["pid"])
        rets = {row["horizon_weeks"]: row["fwd_return"]
                for row in panel.read_returns(conn, r["pid"])}
        ks_rows.append({"fwd_return": rets.get(prim), "narrative": feats.get("narrative"),
                        **{c: feats.get(c) for c in cr["controls"]}})
    ksw = cfg["kill_switch"]
    verdict = killswitch.run(ks_rows, narrative_key="narrative", control_keys=tuple(cr["controls"]),
                             min_n=ksw["min_n"], t_threshold=ksw["t_threshold"],
                             require_sign=ksw["narrative_coef_sign"])
    return rows_built, skips, verdict


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hype Parser labeled panel + kill-switch.")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--seed-anchors", action="store_true", help="load config/panel_anchors.yaml")
    p.add_argument("--returns", action="store_true", help="fetch forward returns (yfinance)")
    p.add_argument("--ticker", action="append", help="limit --returns to ticker(s)")
    p.add_argument("--killswitch", action="store_true", help="run the premise regression")
    p.add_argument("--build-crude", action="store_true",
                   help="build the crude indicative panel (mechanical t0 + PIT proxies; D14)")
    p.add_argument("--theme", action="append", help="limit --build-crude to theme id(s)")
    p.add_argument("--rebuild", action="store_true", help="clear prior crude_derived rows first")
    p.add_argument("--list", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = _load(PANEL_CFG)
    horizons = cfg["horizons_weeks"]
    conn = db.connect(args.db)
    try:
        acted = False
        if args.seed_anchors:
            n = panel.seed_anchors(conn, _load(ANCHORS_CFG)["anchors"])
            print(f"seeded {n} anchors")
            acted = True

        if args.returns:
            rows = panel.list_panel(conn)
            if args.ticker:
                want = set(args.ticker)
                rows = [r for r in rows if r["ticker"] in want]
            for r in rows:
                try:
                    res = prices.forward_returns(r["ticker"], r["t0_date"], horizons)
                except Exception as exc:
                    logging.warning("returns failed for %s: %s", r["ticker"], exc)
                    continue
                for h, stats in res.items():
                    panel.write_returns(conn, r["panel_id"], h, stats)
                got = ", ".join(f"{h}w={res[h]['fwd_return']*100:+.0f}%" for h in sorted(res))
                print(f"{r['ticker']:6} {r['label']:13} {got or '(no data)'}")
            acted = True

        if args.build_crude:
            rows, skips, verdict = build_crude(
                conn, cfg, themes_filter=set(args.theme) if args.theme else None,
                rebuild=args.rebuild)
            print(f"\nCRUDE PANEL built: {len(rows)} rows  ({len(skips)} candidates skipped)")
            n_pos = sum(1 for r in rows if r["label"] == "positive")
            print(f"  labels: {n_pos} positive / {len(rows) - n_pos} hard_negative")
            modes = Counter(r["ms_mode"] for r in rows)
            print(f"  m_share modes: {dict(modes)}")
            for r in sorted(rows, key=lambda r: (r["theme"], r["ticker"])):
                fwd = f"{r['fwd']*100:+.0f}%" if r["fwd"] is not None else "n/a"
                ms = f"{r['m_share']:+.2f}" if r["m_share"] is not None else "  -  "
                print(f"  {r['ticker']:6} {r['theme']:20} t0={r['t0']} {r['label']:13} "
                      f"{cfg['crude']['primary_horizon_weeks']}w={fwd:>6}  "
                      f"narrative={r['narrative']:+.3f}  m_share={ms}")
            if args.verbose and skips:
                print("\n  skipped:")
                for tk, th, why in skips:
                    print(f"    {tk:6} {th:20} {why}")
            print("\nCRUDE KILL-SWITCH (Protocol 4; controls=%s):" % cfg["crude"]["controls"])
            for k, v in verdict.items():
                print(f"  {k}: {v}")
            if verdict.get("underpowered"):
                print("  -> NOT a verdict (crude/indicative). Escalate to n>=100 + m_share for power.")
            acted = True

        if args.killswitch:
            ks = cfg["kill_switch"]
            rows = []
            for r in panel.list_panel(conn):
                feats = panel.read_features(conn, r["panel_id"])
                rets = {row["horizon_weeks"]: row["fwd_return"]
                        for row in panel.read_returns(conn, r["panel_id"])}
                rows.append({"fwd_return": rets.get(ks["primary_horizon_weeks"]),
                             "narrative": feats.get(ks["narrative_key"]),
                             **{c: feats.get(c) for c in ks["controls"]}})
            verdict = killswitch.run(
                rows, narrative_key="narrative", control_keys=tuple(ks["controls"]),
                min_n=ks["min_n"], t_threshold=ks["t_threshold"],
                require_sign=ks["narrative_coef_sign"])
            print("KILL-SWITCH (Protocol 4):")
            for k, v in verdict.items():
                print(f"  {k}: {v}")
            if verdict.get("underpowered"):
                print("  -> NOT a verdict. Build the panel to n>=100 with PIT features first.")
            acted = True

        if args.list or not acted:
            print(f"{'ticker':6} {'label':13} {'regime':5} {'t0':11} theme")
            for r in panel.list_panel(conn):
                rets = panel.read_returns(conn, r["panel_id"])
                rr = " | ".join(f"{x['horizon_weeks']}w {x['fwd_return']*100:+.0f}%" for x in rets)
                print(f"{r['ticker']:6} {r['label']:13} {r['regime'] or '':5} "
                      f"{r['t0_date']:11} {r['theme'] or ''}")
                if rr:
                    print(f"       returns: {rr}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
