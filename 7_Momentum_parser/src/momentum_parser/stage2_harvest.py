"""Stage 2 — harvest the media / search / forward-catalyst dimensions into the store (spec v0.3 §4).

For each ticker, fetch raw daily series via the (provider-isolated, fail-open) clients, compute the
quantified leading-attention features, and write one `evidence` row per dimension (`media`, `search`,
`catalyst`) plus the forward `catalysts` calendar. Clients are injectable (``clients=`` dict) so the full
write path is offline-testable with fakes. Rich/contextual reading is left to Claude's web_search at
Stage 3 — this stage produces *numbers only* (no untrusted text enters a prompt from here).
"""

from __future__ import annotations

from . import catalysts as catalyst_mod
from . import features
from .clients import catalysts_client, news as news_client, search_interest as search_client
from .store import Store


def run(store: Store, tickers: list[str], cfg: dict, asof: str | None = None,
        clients: dict | None = None, log=print) -> dict:
    h = cfg.get("harvest", {})
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

    n_media = n_search = n_cat = skipped = 0
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

        # --- catalysts: forward calendar + proximity ------------------------------------------
        events = cat_c.upcoming(t, a)                      # [(date, kind, note, source)]
        if events:
            store.upsert_catalysts(t, events)
            n_cat += 1
        days = catalyst_mod.days_to_next([e[0] for e in events], a)
        c_score, c_feats = catalyst_mod.proximity_score(days, h)
        store.upsert_evidence(t, a, "catalyst", c_score, c_feats)

        log(f"  [harvest] {t}@{a}: media={len(counts)} search={len(interest)} "
            f"catalyst_days={c_feats.get('days_to_catalyst')}")

    return {"tickers": len(tickers), "media": n_media, "search": n_search,
            "catalysts": n_cat, "skipped": skipped}
