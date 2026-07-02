"""Stage 2 — harvest the media / search / forward-catalyst dimensions into the store (spec v0.3 §4).

For each ticker, fetch raw daily series via the (provider-isolated, fail-open) clients, compute the
quantified leading-attention features, and write one `evidence` row per dimension (`media`, `search`,
`catalyst`) plus the forward `catalysts` calendar. Clients are injectable (``clients=`` dict) so the full
write path is offline-testable with fakes. Rich/contextual reading is left to Claude's web_search at
Stage 3 — this stage produces *numbers only* (no untrusted text enters a prompt from here).
"""

from __future__ import annotations

from . import catalyst_signal
from . import catalysts as catalyst_mod
from . import features
from .clients import catalysts_client, news as news_client, search_interest as search_client
from .store import Store


def run(store: Store, tickers: list[str], cfg: dict, asof: str | None = None,
        clients: dict | None = None, log=print) -> dict:
    h = cfg.get("harvest", {})
    horizon = int(cfg.get("probability", {}).get("horizon_days", 5))   # for the catalyst drift hypothesis (M15)
    clients = clients or {}
    # media provider: real GDELT (429-safe) when configured, else the fail-open stub. Injected client wins.
    if "news" in clients:
        news_c = clients["news"]
    elif cfg.get("sources", {}).get("media_provider", "none") == "gdelt":
        from .clients import gdelt
        names = {r["ticker"]: (r["name"] or "") for r in store.latest_discovery()}
        gdelt.OPTS = {**cfg.get("gdelt", {}), "names": {k: v for k, v in names.items() if v}}
        news_c = gdelt
    else:
        news_c = news_client
    search_c = clients.get("search", search_client)
    cat_c = clients.get("catalysts", catalysts_client)
    # Tier-2 leading inputs (M22) — OPT-IN (yfinance short/options are stale/delayed, ToS-gray). Injectable.
    so_enabled = cfg.get("sources", {}).get("short_options", False)
    short_c = clients.get("short") or (_short_options() if so_enabled else None)
    opt_c = clients.get("options") or (_short_options() if so_enabled else None)
    so_cfg = cfg.get("short_options", {})

    n_media = n_search = n_cat = n_pos = skipped = 0
    for t in tickers:
        a = asof or store.latest_bar_date(t)
        if not a:
            skipped += 1
            log(f"  [skip] {t}: no asof (no bars cached)")
            continue

        # --- media: article volume surge + tone ----------------------------------------------
        counts = news_c.daily_counts(t, a)
        tones = news_c.daily_tone(t, a)
        m_feats, m_score = features.media_features(counts, tones, h)
        store.upsert_evidence(t, a, "media", m_score, m_feats)
        if counts:
            n_media += 1

        # --- search: interest surge vs baseline ----------------------------------------------
        interest = search_c.daily_interest(t, a)
        s_feats, s_score = features.search_features(interest, h)
        store.upsert_evidence(t, a, "search", s_score, s_feats)
        if interest:
            n_search += 1

        # --- catalysts: forward calendar + FORWARD-FACT accumulation + FALSIFIABLE hypothesis (M15) ---
        events = cat_c.upcoming(t, a)                      # [(date, kind, note, source)]
        if events:
            store.upsert_catalysts(t, events)
            n_cat += 1
        nxt = store.next_catalyst(t, a)                    # soonest scheduled date on/after asof
        bars = store.get_bars(t)
        hyp = catalyst_signal.hypothesis(bars, nxt["event_date"] if nxt else None, a, h, horizon) if nxt else None
        if hyp:
            c_score = catalyst_signal.catalyst_score(hyp, h)
            store.upsert_evidence(t, a, "catalyst", c_score, hyp)
            store.upsert_catalyst_hypothesis(t, a, nxt["event_date"], nxt["kind"], hyp)
            c_days = hyp["days_to_catalyst"]
        else:                                              # none inside horizon -> proximity fallback (0)
            days = catalyst_mod.days_to_next([e[0] for e in events], a)
            c_score, c_feats = catalyst_mod.proximity_score(days, h)
            store.upsert_evidence(t, a, "catalyst", c_score, c_feats)
            c_days = c_feats.get("days_to_catalyst")

        # --- positioning (Tier-2, opt-in): short-squeeze fuel + options-implied move (M22) ----------
        if short_c is not None or opt_c is not None:
            from . import microstructure
            if short_c is not None:
                sf = microstructure.short_features(short_c.fetch_short_stats(t), so_cfg)
                store.upsert_evidence(t, a, "short", sf.get("squeeze_setup"), sf)
            if opt_c is not None:
                of = microstructure.options_features(
                    opt_c.fetch_options_iv(t, int(so_cfg.get("horizon_days", horizon))), so_cfg)
                store.upsert_evidence(t, a, "options", of.get("implied_move_pct"), of)
            n_pos += 1

        log(f"  [harvest] {t}@{a}: media={len(counts)} search={len(interest)} catalyst_days={c_days}")

    return {"tickers": len(tickers), "media": n_media, "search": n_search,
            "catalysts": n_cat, "positioning": n_pos, "skipped": skipped}


def _short_options():
    from .clients import short_options
    return short_options
