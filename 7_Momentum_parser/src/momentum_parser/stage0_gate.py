"""Stage 0b — daily liquidity / volatility / price gate (spec §3, Decision C).

Prunes the candidate basket to names tradable for the desk's thesis: enough dollar-liquidity to move
$100k/session, enough volatility for an attention-driven move, and above the penny floor. **Flag, never
silently drop** (cardinal rule) — every name gets a status; only `pass` proceeds to scoring. Missing data →
`no_data` flag (kept, reversible). Pure `gate_ticker`; `run` persists to `universe_gate`.
"""

from __future__ import annotations

from .store import Store
from .targets import weekly_sigma


def gate_ticker(bars, cfg: dict) -> tuple[str, dict]:
    """Return ``(status, metrics)`` — status in {pass, penny, illiquid, placid, no_data}."""
    liq = cfg.get("liquidity", {})
    uni = cfg.get("universe", {})
    adv_w = int(liq.get("adv_window", 20))
    min_adv = float(liq.get("min_session_usd", 100_000)) * float(liq.get("safety_mult", 20))
    min_price = float(uni.get("min_price", 1.0))
    min_vol = float(uni.get("min_weekly_vol", 0.04))

    if len(bars) < adv_w:
        return "no_data", {}
    closes = [b.close for b in bars]
    window = bars[-adv_w:]
    adv = sum(b.close * b.volume for b in window) / len(window)
    sigma = weekly_sigma(closes)
    price = closes[-1]
    m = {"adv": adv, "sigma": sigma, "price": price}

    if price < min_price:
        return "penny", m
    if adv < min_adv:
        return "illiquid", m
    if sigma is None or sigma < min_vol:
        return "placid", m
    return "pass", m


def run(store: Store, tickers: list[str], cfg: dict, log=print) -> dict:
    passed: list[str] = []
    by_status: dict = {}
    for t in tickers:
        bars = store.get_bars(t)
        status, m = gate_ticker(bars, cfg)
        if bars:
            store.write_gate(t, bars[-1].date, status, m.get("adv"), m.get("sigma"), m.get("price"))
        by_status[status] = by_status.get(status, 0) + 1
        if status == "pass":
            passed.append(t)
        log(f"  [gate] {t}: {status}"
            + (f" (adv=${m['adv']:,.0f} σ={m['sigma']} px={m['price']})" if m else ""))
    return {"passed": len(passed), "tickers": passed, "by_status": by_status}
