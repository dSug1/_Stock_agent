"""Crude indicative panel builder — mechanical t0 + PIT feature reconstruction (Protocol 2.2/2.3).

CRUDE / INDICATIVE ONLY (decision D14, the user's "start crude, escalate" path). The point of this
module is to exercise the **mechanical-t0 + PIT-feature pipeline end-to-end** on a small price-only
panel and surface problems early — NOT to deliver a powered verdict. Several quantities are
deliberate placeholders for not-yet-built modules and are flagged unfit:

  - mispricing gate   := a price drawdown-from-trailing-high proxy. The real Module A is the
                         value / re-rating-headroom triple-lock (Features section 2).
  - Legibility (B1)   := log of the specialist-corpus level at t0. The real B1 is a Claude
                         one-sentence legibility score (Features section 3).
  - ThematicHeat (B2) := the diffusion beta_spec slope as-of t0 (this one is genuinely PIT).
  - narrative         := Legibility x ThematicHeat (the multiplicative composite the kill-switch
                         regresses on).
  - p_main gate       := DISABLED in crude. GDELT N_main coverage is uneven across themes (nonzero
                         for crispr, zero for the other three), so a p_main<=p_max gate would reject
                         the clean crispr cohort and wave through the rest. p_main is still recorded
                         as a feature; the full panel needs a consistent mainstream denominator.
  - time_to_catalyst  := DROPPED from the crude control set (no PIT catalyst calendar yet).
  - free_float, sector:= crude/current proxies (no PIT fundamentals provider yet).

Name->theme membership comes from EDGAR ticker linkage (theme_tickers), which is CURRENT, not PIT —
another crude shortcut. The escalation step (full rigorous panel) restores n>=100, m_share labels,
PIT fundamentals, the p_main gate, and the catalyst calendar.

All functions here are pure (no I/O); the orchestration + price fetch lives in scripts/5_panel.py.
"""

import math

from .diffusion import beta_spec, month_range, p_main

# Crude sector proxy: 1 = biotech/healthcare, 0 = software/tech. Used as a single 0/1 control.
_THEME_SECTOR = {
    "crispr_gene_editing": 1,
    "mkras_vaccine": 1,
    "rag": 0,
    "ssm_mamba": 0,
}
# Crude regime proxy by theme: biomedical themes are discrete-catalyst (A); software is continuous (B).
_THEME_REGIME = {
    "crispr_gene_editing": "A",
    "mkras_vaccine": "A",
    "rag": "B",
    "ssm_mamba": "B",
}


def theme_sector_code(theme_id: str) -> int:
    return _THEME_SECTOR.get(theme_id, 0)


def theme_regime(theme_id: str) -> str:
    return _THEME_REGIME.get(theme_id, "B")


def theme_horizon_weeks(horizon_years, cfg_crude):
    """Forward-return / hold horizon H (weeks) for a theme — **horizon-aware H** (Protocol; D22/D40).

    A nascent theme's re-rating plays out over its diffusion *runway*, not a fixed calendar window: a
    slow theme (drones, ~3.5yr) measured at a fixed 52w systematically under-counts the re-rating (and
    thus positives), while a fast one over-counts noise. A DISCOVERED theme carries that runway as the
    convergence `horizon_years` (D29); a hand-seeded theme has none, so it falls back to
    `default_horizon_weeks`. Result clamped to `[min_horizon_weeks, max_horizon_weeks]`.

    `horizon_mode: fixed` (or absent) reproduces the old single-horizon behaviour exactly — every theme
    gets `primary_horizon_weeks`. Pure (no I/O); the caller reads `horizon_years` from the themes row.
    """
    if cfg_crude.get("horizon_mode", "fixed") != "theme_aware":
        return int(cfg_crude["primary_horizon_weeks"])
    if horizon_years and float(horizon_years) > 0:
        h = int(round(float(horizon_years) * 52))
    else:
        h = int(cfg_crude.get("default_horizon_weeks", cfg_crude["primary_horizon_weeks"]))
    lo = int(cfg_crude.get("min_horizon_weeks", 13))
    hi = int(cfg_crude.get("max_horizon_weeks", 260))
    return max(lo, min(hi, h))


def candidate_tickers(edgar_tickers, track_a_tickers=None, *, max_n=None):
    """Per-theme candidate constituent set for the panel: the EDGAR ticker-linkage names FIRST
    (highest mention-count order is preserved by the caller), then any DISCOVERED-theme Track-A
    tickers (jury-surfaced firms that resolved to a ticker, spec §4a / D21 → §4 (c) step 2) not
    already present. Deduped, order-preserving, optionally capped to `max_n` after the union so a
    discovered theme with no EDGAR linkage still contributes its jury constituents.

    Pure (no I/O) so the union logic is unit-testable; the caller supplies both lists from the DB.
    """
    out, seen = [], set()
    for tk in list(edgar_tickers) + list(track_a_tickers or []):
        if not tk:
            continue
        u = tk.upper()
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if max_n and len(out) >= max_n:
            break
    return out


def contiguous_series(periods, n_spec_by, n_main_by):
    """Fill a (possibly gappy) monthly series into a contiguous YYYY-MM grid so slopes have no holes.

    Returns (full_periods, ns_full, nm_full) where ns_full/nm_full map every month to a count (0 if
    the source had no row that month). Mirrors how the live engine builds the series.
    """
    months = [p for p in periods if p]
    if not months:
        return [], {}, {}
    full = month_range(min(months), max(months))
    ns_full = {p: int(n_spec_by.get(p, 0)) for p in full}
    nm_full = {p: int(n_main_by.get(p, 0)) for p in full}
    return full, ns_full, nm_full


def nascency_as_of(full_periods, ns_full, nm_full, t, *, L, beta_min, p_max, use_p_main_gate,
                   min_history=2, min_n_spec=0):
    """Nascency signals as-of month `t`, using ONLY months <= t (point-in-time).

    Returns None if `t` is absent or there is < `min_history` months of history. `min_history` is an
    ESTIMABILITY guard (statistics, not tuning): a beta_spec slope over a near-empty left-edge series
    is meaningless. The gate requires the theme to genuinely EXIST and be ACCELERATING at t0:
      - `beta_spec > beta_min`  (strict: the specialist slope must be measurably positive, i.e. the
                                 theme is growing — this is the premise's own sign claim, not a swept
                                 threshold; with beta_min=0 a flat/zero birth-of-theme series fails);
      - `n_spec >= min_n_spec`  (a real specialist corpus exists at t0 — "a theme," not one stray doc);
      - `p_main <= p_max`       (only if use_p_main_gate).
    The strict-beta + corpus floor together fix the "narrative~0 at theme birth" clustering (D15).
    """
    if t not in ns_full:
        return None
    idx = full_periods.index(t)
    upto = full_periods[: idx + 1]
    if len(upto) < max(2, min_history):
        return None
    b = beta_spec(upto, ns_full, L)
    ns = ns_full[t]
    nm = nm_full[t]
    pm = p_main(ns, nm)
    gate = (b > beta_min and ns >= min_n_spec
            and (pm <= p_max if use_p_main_gate else True))
    return {"beta_spec": b, "p_main": pm, "n_spec": ns, "n_main": nm, "gate": gate}


def monthly_close(daily):
    """daily: [(YYYY-MM-DD, close), ...] ascending -> {YYYY-MM: last close of that month}."""
    out = {}
    for d, px in daily:
        if d and px is not None:
            out[d[:7]] = float(px)
    return out


def drawdown_as_of(month_close, t, *, lookback_months):
    """Drawdown of the t-month close vs its trailing high over `lookback_months` ending at t.

    Returns a value in (-1, 0] (more negative = more beaten-down) using only months <= t, or None
    if there is no price at/through t.
    """
    months = sorted(m for m in month_close if m <= t)
    if not months or months[-1] != t:
        return None
    window = months[-lookback_months:] if lookback_months and lookback_months > 0 else months
    high = max(month_close[m] for m in window)
    cur = month_close[t]
    if high <= 0:
        return None
    return cur / high - 1.0


def relative_drawdown_as_of(month_close, bench_close, t, *, lookback_months):
    """Benchmark-relative drawdown: the name's drawdown minus the benchmark's, both as-of t. A value
    in roughly (-1, 1]; more negative = the name fell MORE than its sector benchmark = idiosyncratic
    cheapness (vs a market-wide selloff that drags everything down together). None if either side
    lacks a price through t. This is the fix for blocker 1 (drawdown firing on market-wide selloffs).
    """
    name_dd = drawdown_as_of(month_close, t, lookback_months=lookback_months)
    bench_dd = drawdown_as_of(bench_close, t, lookback_months=lookback_months)
    if name_dd is None or bench_dd is None:
        return None
    return name_dd - bench_dd


def mechanical_t0(full_periods, ns_full, nm_full, month_close, *, L, beta_min, p_max,
                  use_p_main_gate, cheap_drawdown, dd_lookback, min_history=2, min_n_spec=0,
                  bench_close=None, use_relative_dd=False, relative_cheap=0.10):
    """First month at which BOTH gates fire (Protocol 2.2): nascency AND the mispricing proxy.
    Returns (t0_month, signals) or (None, None) if the name never qualifies.

    Mechanical = determined entirely by PIT features; the forward label is measured afterward.
    `min_history` blocks left-edge t0s where the slope is unestimable; `min_n_spec` requires a real
    corpus (see nascency_as_of). Mispricing gate: if `use_relative_dd` and a `bench_close` series is
    supplied, the name must be >= `relative_cheap` BELOW its benchmark (idiosyncratic cheapness);
    otherwise it falls back to the absolute `cheap_drawdown` (>= that far below its own trailing high).
    `signals` always carries both `drawdown` (absolute) and, when available, `relative_drawdown`.
    """
    for t in full_periods:
        if t not in month_close:
            continue
        nas = nascency_as_of(full_periods, ns_full, nm_full, t, L=L, beta_min=beta_min,
                             p_max=p_max, use_p_main_gate=use_p_main_gate, min_history=min_history,
                             min_n_spec=min_n_spec)
        if not nas or not nas["gate"]:
            continue
        dd = drawdown_as_of(month_close, t, lookback_months=dd_lookback)
        if dd is None:
            continue
        rel = (relative_drawdown_as_of(month_close, bench_close, t, lookback_months=dd_lookback)
               if bench_close else None)
        if use_relative_dd and bench_close is not None:
            if rel is None or rel > -relative_cheap:
                continue
        else:
            if dd > -cheap_drawdown:
                continue
        sig = {**nas, "drawdown": dd}
        if rel is not None:
            sig["relative_drawdown"] = rel
        return t, sig
    return None, None


def pit_features(theme_id, signals):
    """The PIT feature snapshot the kill-switch regresses on, as-of t0. `signals` is the dict
    returned by mechanical_t0 (nascency + drawdown). free_float is added by the caller (current
    proxy) because it needs a network lookup; everything here is derivable from already-PIT data.
    """
    legibility = math.log1p(signals["n_spec"])      # B1 proxy: specialist-corpus level
    heat = signals["beta_spec"]                      # B2 proxy: specialist slope (genuinely PIT)
    feats = {
        "narrative": legibility * heat,              # the multiplicative composite under test
        "legibility": legibility,
        "thematic_heat": heat,
        "drawdown": signals["drawdown"],
        "sector": float(theme_sector_code(theme_id)),
        "beta_spec_t0": signals["beta_spec"],
        "p_main_t0": signals["p_main"],
        "n_spec_t0": float(signals["n_spec"]),
    }
    if signals.get("relative_drawdown") is not None:
        feats["relative_drawdown"] = signals["relative_drawdown"]   # idiosyncratic cheapness (D15)
    return feats


def era_feature(t0_month: str) -> float:
    """Calendar era control: years since 2015 (PIT — depends only on t0)."""
    return float(int(t0_month[:4]) - 2015)


def crude_label(fwd_return_primary, *, hit_return):
    """Price-only label (no m_share — that needs PIT fundamentals, the escalation step). A name only
    reaches here if it PASSED both gates, so the binary is positive ("ran on hype") vs hard_negative
    ("looked like a setup, fizzled" — the discriminating class). Returns None if no forward data.
    """
    if fwd_return_primary is None:
        return None
    return "positive" if fwd_return_primary >= hit_return else "hard_negative"
