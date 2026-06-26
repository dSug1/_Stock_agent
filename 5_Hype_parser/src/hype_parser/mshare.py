"""m_share — multiple-expansion share of return (Stage B; decisions D16/D18).

The label clause that separates **hype** (a re-rating) from **value-realization** (the business
growing into its price) — Protocol §2.1 calls it non-negotiable. Pure functions; no I/O.

A stock's per-share price is identically **multiple × per-share fundamental**:

    price  =  M  ×  F        with  F = revenue_per_share = revenue_ttm / shares,
                                   M = price / F   ( = the P/S multiple)

so over [t0, t0+H] the (log) return splits exactly:

    log(price_H / price_0)  =  log(M_H / M_0)        ← RE-RATING (narrative/attention)
                            +  log(F_H / F_0)        ← FUNDAMENTAL growth (revenue per share)

    m_share = log(M_H / M_0) / log(price_H / price_0)

m_share≈1 ⇒ the move was almost all re-rating (hype); ≈0 ⇒ the business grew into it. Pre-revenue (or
immaterial-revenue) names have no fundamental to grow, so any run IS a re-rating ⇒ m_share := 1 (D16).
Prices here are the same adjusted closes used for the forward return, so the decomposition is an exact
identity in the inputs (dilution shows up in F via the share count).
"""

import math


def decompose_mshare(price_t0, price_tH, rev_t0, sh_t0, rev_tH, sh_tH, *, min_revenue):
    """Return {m_share, mode, ...}. m_share is None when it cannot be computed (caller falls back to
    a price-only label). `min_revenue` is the materiality floor (USD): at/below it the name is treated
    as pre-revenue (milestone/collaboration revenue isn't a real fundamental) ⇒ m_share=1 (D17 flag)."""
    # Pre-revenue or immaterial revenue at t0 -> pure narrative by construction.
    if rev_t0 is None or rev_t0 < min_revenue or not sh_t0:
        return {"m_share": 1.0, "mode": "pre_revenue"}
    if price_t0 is None or price_tH is None or price_t0 <= 0 or price_tH <= 0:
        return {"m_share": None, "mode": "no_price"}
    if not (rev_tH and sh_tH and rev_tH > 0):
        return {"m_share": None, "mode": "incomplete_end"}

    f0 = rev_t0 / sh_t0          # revenue per share at t0
    fH = rev_tH / sh_tH          # revenue per share at t0+H
    if f0 <= 0 or fH <= 0:
        return {"m_share": None, "mode": "bad_fundamental"}

    m0 = price_t0 / f0           # P/S multiple at t0
    mH = price_tH / fH           # P/S multiple at t0+H
    total = math.log(price_tH / price_t0)
    if abs(total) < 1e-6:        # flat name -> decomposition is ill-conditioned (not a positive anyway)
        return {"m_share": None, "mode": "flat"}

    m_share = math.log(mH / m0) / total
    return {"m_share": m_share, "mode": "decomposed",
            "ps_t0": m0, "ps_tH": mH, "rps_t0": f0, "rps_tH": fH}


def mshare_label(fwd_return, m_share, *, hit_return, m_share_min):
    """The real panel label. A name only reaches here having passed the gates, so the binary is
    positive ("ran on hype") vs hard_negative. Positive needs a big return AND, where m_share is
    known, the re-rating to dominate (m_share >= m_share_min). When m_share can't be computed we fall
    back to the price-only bar (graceful — matches the crude panel without fundamentals)."""
    if fwd_return is None:
        return None
    if fwd_return < hit_return:
        return "hard_negative"                 # didn't clear the return bar
    if m_share is None:
        return "positive"                      # cleared it; no decomposition available -> price-only
    return "positive" if m_share >= m_share_min else "hard_negative"   # hype vs value-realization
