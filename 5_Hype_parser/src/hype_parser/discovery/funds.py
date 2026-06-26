"""Specialist-fund cross-reference — smart-money confirmation on a theme's *listed* names (spec §4b).

A specialist fund taking a **new** 13F position in a theme constituent while the theme is still early
is a *capital jury* — an independent expert vote, in money. This module reads the curated universe
(``config/specialist_funds.yaml``: per-sector specialist/crossover 13F filers + thematic-ETF
fallbacks, D25/D26) and crosses {specialist new buys ∪ ETF additions} against a theme's Track-A
tickers (from ``resolve.track_a_tickers``).

The new-position *data* is sourced upstream: biotech reuses ``2_Funds_parser``'s already-fetched
13F holdings/new_positions (D3); other sectors use EDGAR 13F for the listed CIKs here, or ETF
holdings deltas where no specialist files (most of semis/cyber/fintech/energy/space/materials). That
sourcing is wired later; the scorer below is pure so it can be tested + driven by any provider.
"""

import logging

log = logging.getLogger(__name__)


def load_specialist_funds(path: str) -> dict:
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _sectors(cfg: dict) -> dict:
    """Sector -> {funds:[...], etf_fallback:[...]}, skipping the YAML comment scalars."""
    return {k: v for k, v in cfg.items() if isinstance(v, dict)}


def fund_ciks(cfg: dict) -> dict:
    """{CIK: {name, type, sector}} for every fund with a (non-blank) CIK — the EDGAR 13F worklist."""
    out = {}
    for sector, block in _sectors(cfg).items():
        for f in block.get("funds", []) or []:
            cik = (f.get("cik") or "").strip()
            if cik:
                out[cik] = {"name": f.get("name"), "type": f.get("type"), "sector": sector}
    return out


def sector_etfs(cfg: dict) -> dict:
    """{sector: [ETF tickers]} — the holdings-delta fallback where no specialist 13F exists."""
    return {s: list(b.get("etf_fallback", []) or []) for s, b in _sectors(cfg).items()}


def cross_reference(theme_tickers, new_buys: dict, cfg_weights: dict) -> list[dict]:
    """Confirmation signals for one theme.

    ``theme_tickers``: the theme's Track-A tickers (iterable).
    ``new_buys``: {TICKER: [{name, type}, ...]} — funds/ETFs that just opened a position in that name.
    Returns, per intersecting ticker, a weighted smart-money score (pure specialists > crossover/ETF).
    """
    spec_w = cfg_weights.get("specialist_weight", 1.0)
    cross_w = cfg_weights.get("crossover_weight", 0.5)
    wanted = {t.upper() for t in theme_tickers}
    out = []
    for tk, buyers in new_buys.items():
        u = tk.upper()
        if u not in wanted:
            continue
        score = 0.0
        for b in buyers:
            score += spec_w if b.get("type") == "specialist" else cross_w
        out.append({
            "ticker": u,
            "n_buyers": len(buyers),
            "buyers": [b.get("name") for b in buyers],
            "smart_money_score": round(score, 3),
        })
    out.sort(key=lambda r: (-r["smart_money_score"], r["ticker"]))
    return out
