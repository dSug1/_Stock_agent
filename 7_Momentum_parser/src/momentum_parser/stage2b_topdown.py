"""Stage 2b — top-down market-perturbation harvest (spec §4b, Decision L, M12).

Assembles the day's ANTICIPATED top-down signals into `macro_signals` (shared across the whole basket):
  * continuous cross-asset + rotation signals from yfinance proxies (regime, rates, commodities,
    AI-crowding, style, sector) — always active, surprise = the recent SHIFT (§2.3);
  * dated macro signals (FOMC/CPI/NFP/…) via ONE shared econ-calendar web_search call (cost amortized),
    active only while the event lands inside the forward window;
  * geopolitical signals seeded active=0 + flagged (provider wired in a later milestone).
Clients + scorer are injectable so the whole write path is offline-testable. Fail-open throughout:
a missing proxy / failed call never aborts the run (absence ≠ negative evidence).
"""

from __future__ import annotations

from . import topdown
from .clients import econ_calendar
from .clients import market as market_client
from .store import Store
from .taxonomy import load_taxonomy


def _default_series(ticker: str) -> list[float]:
    return [b.close for b in market_client.fetch_daily_bars(ticker)]


def run(store: Store, cfg: dict, asof: str, clients: dict | None = None,
        scorer=None, dispatch: bool = False, log=print) -> dict:
    td = cfg.get("topdown", {})
    clients = clients or {}
    fetch = clients.get("macro_series", _default_series)
    proxies = td.get("proxies", {})

    # --- fetch proxy series (fail-open per role) --------------------------------------------------
    series: dict[str, list] = {}
    for role, tk in proxies.items():
        try:
            series[role] = fetch(tk) or []
        except Exception:                                  # pragma: no cover - provider error -> fail open
            series[role] = []
    n_series = sum(1 for v in series.values() if v)

    # --- continuous cross-asset + rotation signals -----------------------------------------------
    surp = topdown.continuous_surprises(series, td)
    regime = surp.pop("_regime", "neutral")
    n_cont = 0
    for sig, s in surp.items():
        store.upsert_macro_signal(asof, sig, active=True, surprise=s, regime=regime,
                                  horizon_days=None, features={"regime": regime}, note="continuous shift")
        n_cont += 1

    # --- dated macro signals via the shared econ-calendar call (needs a scorer; else skipped) ----
    ec = td.get("econ_calendar", {})
    scorer = scorer or clients.get("scorer")
    n_dated = 0
    if ec.get("enabled", True) and scorer is not None:
        try:
            req = econ_calendar.build_request(cfg)
            res = scorer.complete(req["params"], timeout=float(ec.get("timeout_s", 120)))
            if res.get("error"):
                log(f"  [2b] econ-calendar failed (fail-open): {res['error']}")
            else:
                for row in econ_calendar.parse_events(res.get("parsed", {}), asof, cfg):
                    store.upsert_macro_signal(asof, row["signal_id"], active=True, surprise=row["surprise"],
                                              regime=regime, horizon_days=row["days_out"], note=row["note"])
                    n_dated += 1
        except Exception as e:                             # pragma: no cover - fail open
            log(f"  [2b] econ-calendar exception (fail-open): {e}")
    elif ec.get("enabled", True):
        log("  [2b] econ-calendar skipped (no scorer / dry) — dated macro signals not harvested this run")

    # --- geopolitical: stub active=0 + flagged (wire later) --------------------------------------
    n_stub = 0
    if td.get("geopolitical_stub", True):
        for spec in load_taxonomy(cfg)["signals"]:
            if spec.cls == "geopolitical":
                store.upsert_macro_signal(asof, spec.id, active=False, surprise=None, regime=regime,
                                          horizon_days=None, features={"stub": True},
                                          note="stub — geopolitical provider not wired (M12)")
                n_stub += 1

    funnel = {"asof": asof, "regime": regime, "series_ok": n_series,
              "continuous": n_cont, "dated": n_dated, "geopolitical_stub": n_stub}
    log(f"  [2b] regime={regime} · {n_cont} continuous · {n_dated} dated · {n_stub} geopolitical-stub")
    return funnel
