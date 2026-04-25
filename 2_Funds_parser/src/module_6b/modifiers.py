"""Module 6b Part (a) — composite score modifier (D47).

Pure-Python, deterministic. No API calls. Computes a per-ticker
multiplier from the LLM's research_brief JSON and applies it to
``score_at_current_pct_per_month`` to produce the adjusted ranking key.

Public API::

    cfg = load_modifier_config()                         # config/scoring_modifier.yaml
    result = compute_modifier(rb_dict, cfg, now=...)     # weights default to 1.0
    # result = {"modifier": float, "raw_product": float, "clipped_to": float|None,
    #           "components": {name: {"factor": ..., **evidence}},
    #           "applied_at": "<iso>"}

D50 — user-overridable per-component weights are applied via
``apply_weights(per_component_factors, weights, bounds)`` which mirrors
the JS-side recompute in the rendered HTML. Pipeline-side computes the
*model* factor (weight=1.0). The HTML re-applies user weights live in JS.

Each component is a pure function ``(rb, cfg, now=None) -> (factor, evidence)``.
``evidence`` is a small dict that captures why the factor is what it is —
serialised into ``llm_scores.score_modifier_json`` for audit and the
detail-panel breakdown table.

Spec: 2_Funds_parser/spec/module_6b_spec.md.
Decision: 2_Funds_parser/spec/decisions.md § D47, D50.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import yaml

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_modifier_config(path: Path | str | None = None) -> dict:
    """Load ``config/scoring_modifier.yaml``. Defaults to the project copy."""
    if path is None:
        # Resolve relative to this file: src/module_6b/modifiers.py
        # → ../../config/scoring_modifier.yaml
        here = Path(__file__).resolve()
        path = here.parents[2] / "config" / "scoring_modifier.yaml"
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helpers (date parsing, band lookup)
# ---------------------------------------------------------------------------


_ISO_FULL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_ISO_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_ISO_YEAR_RE = re.compile(r"^(\d{4})$")
# D52 — half / quarter notation used by the LLM for forward catalysts:
# "2026-H1", "2027-H2", "2027-Q1", "2026-Q2 (estimated)" …
_ISO_HALF_QTR_RE = re.compile(r"^(\d{4})-([HQ])(\d)", re.IGNORECASE)


def parse_iso_date(s: str | None) -> datetime | None:
    """Tolerant ISO-8601 date parser.

    Accepts ``YYYY-MM-DD``, ``YYYY-MM`` (treated as the 1st), or ``YYYY``
    (treated as Jan 1). Returns a tz-aware UTC datetime so windowing
    against ``now`` is consistent. Returns None on any failure.
    """
    if not isinstance(s, str) or not s:
        return None
    s = s.strip()
    m = _ISO_FULL_RE.match(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)),
                            int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            return None
    m = _ISO_MONTH_RE.match(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1,
                            tzinfo=timezone.utc)
        except ValueError:
            return None
    m = _ISO_YEAR_RE.match(s)
    if m:
        try:
            return datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def parse_loose_date(s: str | None) -> datetime | None:
    """Like ``parse_iso_date`` but also accepts ``YYYY-H1`` / ``YYYY-Q3``
    style fuzzy quarter-/half-year dates the LLM emits for forward-looking
    catalyst readouts. Trailing parentheticals (``(estimated)``) are
    stripped. Returns the *middle* of the named period.

    - ``YYYY-H1`` → Mar 15, YYYY  (mid-H1)
    - ``YYYY-H2`` → Sep 15, YYYY  (mid-H2)
    - ``YYYY-Q1`` → Feb 15, YYYY  (mid-Q1)
    - ``YYYY-Q2`` → May 15, YYYY  (mid-Q2)
    - ``YYYY-Q3`` → Aug 15, YYYY  (mid-Q3)
    - ``YYYY-Q4`` → Nov 15, YYYY  (mid-Q4)
    """
    if not isinstance(s, str) or not s:
        return None
    s = s.split("(")[0].strip()                          # drop "(estimated)" trailers
    d = parse_iso_date(s)
    if d is not None:
        return d
    m = _ISO_HALF_QTR_RE.match(s)
    if not m:
        return None
    year, kind, n = int(m.group(1)), m.group(2).upper(), int(m.group(3))
    if kind == "H":
        month = {1: 3, 2: 9}.get(n)
    else:                                                 # 'Q'
        month = {1: 2, 2: 5, 3: 8, 4: 11}.get(n)
    if month is None:
        return None
    try:
        return datetime(year, month, 15, tzinfo=timezone.utc)
    except ValueError:
        return None


def _within_lookback(d: datetime | None, now: datetime, days: int) -> bool:
    if d is None:
        return False
    return d >= (now - timedelta(days=days))


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _band_lookup_count(count: int, bands: list[dict]) -> float:
    """Count-based bands: first band whose ``max_count >= count`` wins.

    Bands are expected sorted ascending by max_count in the YAML.
    """
    for band in bands:
        if int(count) <= int(band["max_count"]):
            return float(band["factor"])
    return float(bands[-1]["factor"])


def _band_lookup_min_months(months: float, bands: list[dict]) -> float:
    """Threshold bands for runway: first band whose ``min_months <= months`` wins.

    Bands are expected sorted descending by min_months in the YAML.
    """
    for band in bands:
        if float(months) >= float(band["min_months"]):
            return float(band["factor"])
    return float(bands[-1]["factor"])


def _band_lookup_score_eq(score: float, bands: list[dict]) -> float:
    """Snap score to the nearest configured 3-band point and return factor."""
    candidates = [(abs(float(score) - float(b["score_eq"])), float(b["factor"]))
                  for b in bands]
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _band_lookup_max_pct(pct: float, bands: list[dict]) -> float:
    """Pct-based bands: first band whose ``max_pct >= pct`` wins (pct in [0,1])."""
    for band in bands:
        if float(pct) <= float(band["max_pct"]):
            return float(band["factor"])
    return float(bands[-1]["factor"])


def _disabled_result() -> tuple[float, dict]:
    return 1.0, {"disabled": True}


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


# What `stage` strings count as "Ph3+/Approved/incumbent SOC".
# Matched as case-insensitive substring search inside the stage text.
_PH3_PLUS_NEEDLES = (
    "ph3", "phase 3", "phase iii",
    "approved",
    "nda filed", "ndafile", "nda submit",
    "bla", "marketed",
    "incumbent", "soc", "standard",       # SOC / standard-of-care
)


def _component_crowding(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("crowding", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    entries = list(rb.get("competitive_landscape") or [])
    counted = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        stage = (e.get("stage") or "").lower()
        # Per spec: missing stage → conservative count (count it).
        if not stage:
            counted.append(e); continue
        if any(needle in stage for needle in _PH3_PLUS_NEEDLES):
            counted.append(e)
    factor = _band_lookup_count(len(counted), ccfg["bands"])
    return factor, {
        "factor": factor,
        "ph3plus_count": len(counted),
        "competitors": [e.get("competitor") or e.get("program") or "?"
                        for e in counted][:8],
    }


def _component_financing(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("financing", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    fin = rb.get("financials") or {}
    runway = fin.get("runway_months")
    if runway is None or float(runway) <= 0:
        factor = float(ccfg.get("missing_factor", 0.85))
        return factor, {"factor": factor, "runway_months": runway, "fallback": True}
    factor = _band_lookup_min_months(float(runway), ccfg["bands"])
    return factor, {"factor": factor, "runway_months": float(runway)}


def _component_dilution(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("dilution", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    days = int(cfg.get("windows", {}).get("dilution_lookback_days", 180))
    raises = list((rb.get("financials") or {}).get("recent_capital_raises") or [])
    in_window = []
    for r in raises:
        if not isinstance(r, dict):
            continue
        d = parse_iso_date(r.get("date_iso"))
        if _within_lookback(d, now, days):
            in_window.append(r)
    factor = _band_lookup_count(len(in_window), ccfg["bands"])
    return factor, {
        "factor": factor,
        "raises_in_window": len(in_window),
        "lookback_days": days,
        "raises": [{"date_iso": r.get("date_iso"),
                    "amount_usd": r.get("amount_usd"),
                    "type": r.get("type")} for r in in_window][:5],
    }


def _component_insider(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("insider", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    days = int(cfg.get("windows", {}).get("insider_lookback_days", 180))
    qual_roles = {r.lower() for r in ccfg.get("qualifying_roles", [])}
    qual_types = {t.lower() for t in ccfg.get("qualifying_types", ["buy"])}
    txns = list((rb.get("insider_activity") or {}).get("recent_transactions") or [])
    qualifying = []
    for t in txns:
        if not isinstance(t, dict):
            continue
        ttype = (t.get("type") or "").strip().lower()
        if ttype not in qual_types:
            continue
        role = (t.get("role") or "").strip().lower()
        if role not in qual_roles:
            continue
        d = parse_iso_date(t.get("date_iso"))
        if not _within_lookback(d, now, days):
            continue
        qualifying.append(t)
    detected = bool(qualifying)
    factor = (float(ccfg["detected_factor"]) if detected
              else float(ccfg["none_factor"]))
    return factor, {
        "factor": factor,
        "qualifying_buys_in_window": len(qualifying),
        "lookback_days": days,
        "buys": [{"date_iso": t.get("date_iso"), "role": t.get("role"),
                  "shares": t.get("shares"), "price_usd": t.get("price_usd")}
                 for t in qualifying][:5],
    }


def _component_mgmt(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("mgmt", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    score = (rb.get("mgmt_track_record_score") or {}).get("score")
    if score is None:
        factor = float(ccfg.get("missing_factor", 1.00))
        return factor, {"factor": factor, "score": None, "fallback": True}
    factor = _band_lookup_score_eq(float(score), ccfg["bands"])
    return factor, {"factor": factor, "score": float(score)}


def _component_acquisition(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("acquisition", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    score = (rb.get("acquisition_target") or {}).get("score")
    if score is None:
        factor = float(ccfg.get("missing_factor", 1.00))
        return factor, {"factor": factor, "score": None, "fallback": True}
    factor = _band_lookup_score_eq(float(score), ccfg["bands"])
    return factor, {"factor": factor, "score": float(score)}


def _component_moat(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("moat", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    score = (rb.get("moat") or {}).get("score")
    if score is None:
        factor = float(ccfg.get("missing_factor", 1.00))
        return factor, {"factor": factor, "score": None, "fallback": True}
    factor = _band_lookup_score_eq(float(score), ccfg["bands"])
    return factor, {"factor": factor, "score": float(score)}


def _component_failures(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("failures", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    days = int(cfg.get("windows", {}).get("failures_lookback_days", 365))
    qual_events = {e.lower() for e in ccfg.get("qualifying_events", [])}
    failures = list(rb.get("past_failures") or [])
    triggered = []
    for f in failures:
        if not isinstance(f, dict):
            continue
        evt = (f.get("event") or "").strip().lower()
        if evt not in qual_events:
            continue
        d = parse_iso_date(f.get("date_iso"))
        if not _within_lookback(d, now, days):
            continue
        triggered.append(f)
    detected = bool(triggered)
    factor = (float(ccfg["detected_factor"]) if detected
              else float(ccfg["none_factor"]))
    return factor, {
        "factor": factor,
        "triggered_count": len(triggered),
        "lookback_days": days,
        "events": [{"date_iso": f.get("date_iso"), "event": f.get("event"),
                    "program": f.get("program")} for f in triggered][:5],
    }


def _component_concentration(rb: dict, cfg: dict, now: datetime, ctx: dict) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("concentration", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    indications = list(rb.get("rnpv_by_indication") or [])
    excludes = [s.lower() for s in ccfg.get("exclude_indication_substrings", [])]

    def is_excluded(item: dict) -> bool:
        name = (item.get("indication") or "").lower()
        return any(s in name for s in excludes)

    named = []
    for it in indications:
        if not isinstance(it, dict):
            continue
        if is_excluded(it):
            continue
        contrib = it.get("rnpv_contribution_usd")
        if contrib is None:
            continue
        try:
            named.append((float(contrib), it))
        except (TypeError, ValueError):
            continue

    if not named:
        factor = float(ccfg.get("missing_factor", 1.00))
        return factor, {"factor": factor, "lead_pct": None, "fallback": True,
                        "named_count": 0}

    total = sum(c for c, _ in named)
    if total <= 0:
        factor = float(ccfg.get("missing_factor", 1.00))
        return factor, {"factor": factor, "lead_pct": None, "fallback": True,
                        "named_count": len(named)}

    lead_contrib, lead_item = max(named, key=lambda x: x[0])
    lead_pct = lead_contrib / total
    factor = _band_lookup_max_pct(lead_pct, ccfg["bands"])
    return factor, {
        "factor": factor,
        "lead_pct": lead_pct,
        "lead_indication": lead_item.get("indication"),
        "lead_contribution_usd": lead_contrib,
        "named_total_usd": total,
        "named_count": len(named),
        "excluded_substrings": excludes,
    }


# ── 5 new components (D52) ───────────────────────────────────────────────


def _component_big_pharma_validation(
    rb: dict, cfg: dict, now: datetime, ctx: dict,
) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("big_pharma_validation", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    needles = [n.lower() for n in ccfg.get("big_pharma_substrings", [])]
    matched: list[dict] = []
    seen_partners: set[str] = set()
    for p in (rb.get("partnerships") or []):
        if not isinstance(p, dict):
            continue
        partner = (p.get("partner") or "").lower()
        if not partner:
            continue
        if any(needle in partner for needle in needles):
            # Dedupe by partner name (e.g., GLUE has 2 separate Novartis deals;
            # both still count as "1 big-pharma partner").
            key = next((n for n in needles if n in partner), partner)
            if key in seen_partners:
                continue
            seen_partners.add(key)
            matched.append(p)
    factor = _band_lookup_count(len(matched), ccfg["bands"])
    return factor, {
        "factor": factor,
        "matched_count": len(matched),
        "matched_partners": [p.get("partner") for p in matched][:5],
    }


def _component_catalyst_density(
    rb: dict, cfg: dict, now: datetime, ctx: dict,
) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("catalyst_density", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    horizon_days = int(ccfg.get("horizon_days", 365))
    cutoff = now + timedelta(days=horizon_days)
    ct = rb.get("clinical_trials") or {}
    candidates: list[dict] = []
    for key in ("interim_readouts_expected", "final_readouts_expected"):
        for r in (ct.get(key) or []):
            if isinstance(r, dict):
                candidates.append({**r, "_kind": key.split("_")[0]})
    in_window: list[dict] = []
    for r in candidates:
        d = parse_loose_date(r.get("expected_date_iso"))
        if d is None:
            continue                                     # undated entries don't count
        if now <= d <= cutoff:
            in_window.append(r)
    factor = _band_lookup_count(len(in_window), ccfg["bands"])
    return factor, {
        "factor": factor,
        "in_window_count": len(in_window),
        "horizon_days": horizon_days,
        "readouts": [{"program": (r.get("program") or "")[:40],
                      "kind":    r.get("_kind"),
                      "expected_date_iso": r.get("expected_date_iso")}
                     for r in in_window][:6],
    }


def _component_tech_uniqueness(
    rb: dict, cfg: dict, now: datetime, ctx: dict,
) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("tech_uniqueness", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    score = (rb.get("technology") or {}).get("uniqueness_score")
    if score is None:
        f = float(ccfg.get("missing_factor", 1.00))
        return f, {"factor": f, "score": None, "fallback": True}
    factor = _band_lookup_score_eq(float(score), ccfg["bands"])
    return factor, {"factor": factor, "score": float(score)}


def _component_dilution_overhang(
    rb: dict, cfg: dict, now: datetime, ctx: dict,
) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("dilution_overhang", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    fin = rb.get("financials") or {}
    shelf = fin.get("shelf_registration_usd_capacity")
    fd_mcap = ctx.get("fully_diluted_market_cap_usd")
    if (shelf is None or fd_mcap is None
            or float(fd_mcap) <= 0):
        f = float(ccfg.get("missing_factor", 1.00))
        return f, {"factor": f, "shelf_usd": shelf,
                   "fd_mcap_usd": fd_mcap, "fallback": True}
    ratio = float(shelf) / float(fd_mcap)
    factor = _band_lookup_max_pct(ratio, ccfg["bands"])
    return factor, {"factor": factor, "shelf_usd": float(shelf),
                    "fd_mcap_usd": float(fd_mcap),
                    "shelf_to_fd_mcap_ratio": ratio}


def _component_cash_floor(
    rb: dict, cfg: dict, now: datetime, ctx: dict,
) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("cash_floor", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    fin = rb.get("financials") or {}
    cash = fin.get("cash_and_equivalents_usd")
    fd_mcap = ctx.get("fully_diluted_market_cap_usd")
    if (cash is None or fd_mcap is None
            or float(fd_mcap) <= 0):
        f = float(ccfg.get("missing_factor", 1.00))
        return f, {"factor": f, "cash_usd": cash,
                   "fd_mcap_usd": fd_mcap, "fallback": True}
    ratio = float(cash) / float(fd_mcap)
    factor = _band_lookup_max_pct(ratio, ccfg["bands"])
    return factor, {"factor": factor, "cash_usd": float(cash),
                    "fd_mcap_usd": float(fd_mcap),
                    "cash_to_fd_mcap_ratio": ratio}


# Component registry — order is the slider drawer order. New components (D52)
# appended at the end. Slider grouping (penalty / tailwind) is driven by the
# `category` field in the YAML, applied at renderer level.
_COMPONENTS: dict[str, Callable[[dict, dict, datetime, dict], tuple[float, dict]]] = {
    "crowding":               _component_crowding,
    "financing":              _component_financing,
    "dilution":               _component_dilution,
    "insider":                _component_insider,
    "mgmt":                   _component_mgmt,
    "acquisition":            _component_acquisition,
    "moat":                   _component_moat,
    "failures":               _component_failures,
    "concentration":          _component_concentration,
    "big_pharma_validation":  _component_big_pharma_validation,   # D52
    "catalyst_density":       _component_catalyst_density,        # D52
    "tech_uniqueness":        _component_tech_uniqueness,         # D52
    "dilution_overhang":      _component_dilution_overhang,       # D52
    "cash_floor":             _component_cash_floor,              # D52
}

COMPONENT_NAMES: tuple[str, ...] = tuple(_COMPONENTS.keys())


# ---------------------------------------------------------------------------
# D50 — weight application (mirrors the JS-side recompute in reports.py)
# ---------------------------------------------------------------------------


def apply_weights(
    factors_by_component: dict[str, float],
    weights: dict[str, float] | None,
    bounds: dict | None = None,
) -> tuple[float, dict[str, float]]:
    """Apply per-component user weights to model factors and return the
    final modifier + the effective factor per component.

    ``effective_factor = 1 + weight × (model_factor − 1)``

    weight = 0 disables the component (effective factor → 1.0).
    weight = 1 keeps the model value unchanged.
    weight = 2 doubles the deviation from neutral.

    The JS in the rendered HTML uses the same formula so server- and
    client-side modifiers are bit-identical for any given input.

    Returns ``(modifier, effective_factors)``. The modifier is clipped
    to the configured ``bounds``.
    """
    bounds = bounds or {}
    bmin = float(bounds.get("min", 0.50))
    bmax = float(bounds.get("max", 1.50))
    weights = weights or {}
    eff: dict[str, float] = {}
    product = 1.0
    for name in COMPONENT_NAMES:
        f = float(factors_by_component.get(name, 1.0))
        w = float(weights.get(name, 1.0))
        e = 1.0 + w * (f - 1.0)
        eff[name] = e
        product *= e
    modifier = max(bmin, min(bmax, product))
    return modifier, eff


# ---------------------------------------------------------------------------
# Combine + top-level entry point
# ---------------------------------------------------------------------------


def compute_modifier(
    research_brief: dict | None,
    cfg: dict,
    *,
    now: datetime | None = None,
    weights: dict[str, float] | None = None,
    current_price_usd: float | None = None,
) -> dict:
    """Run all components, multiply, clip, return the audit payload.

    Returns::

        {
          "modifier":          float,           # final clipped value (sort key multiplier)
          "raw_product":       float,           # before clip
          "clipped_to":        float | None,    # bounds.min/max if clipped, else None
          "components":        {name: {factor, …evidence}},        # MODEL factors + evidence
          "effective_factors": {name: float},                        # MODEL × weight
          "applied_at":        "ISO-8601",
          "weights":           {name: float},                        # what was used
        }

    ``weights`` is optional; defaults to all-1.0 (= use model values).
    The pipeline always computes with weights=None (model values only)
    and persists those to ``llm_scores`` / ``final_rankings``. The HTML
    re-applies user weights live in JS without touching the DB.

    ``current_price_usd`` is optional but required by the D52 ratio
    components (`dilution_overhang`, `cash_floor`) which need
    ``fully_diluted_market_cap_usd = current_price_usd × fully_diluted_shares_count``.
    Components that don't have it fall back to their `missing_factor`.

    Empty / missing research_brief → returns a 1.0 neutral modifier with all
    components in their `missing` / `none` state. Caller decides whether to
    skip writing the row.
    """
    now = _coerce_now(now)
    rb = research_brief or {}
    bounds = cfg.get("bounds", {}) or {}

    # D52 — context dict passed to every component. The pre-computed
    # fully_diluted_market_cap_usd lets the ratio components avoid
    # re-deriving it (and stays consistent with the renderer's mkt-cap cell).
    ctx: dict = {"current_price_usd": current_price_usd}
    fin = rb.get("financials") or {}
    fd_shares = fin.get("fully_diluted_shares_count")
    try:
        if (current_price_usd is not None and fd_shares is not None
                and float(current_price_usd) > 0 and float(fd_shares) > 0):
            ctx["fully_diluted_market_cap_usd"] = (
                float(current_price_usd) * float(fd_shares)
            )
    except (TypeError, ValueError):
        pass

    components: dict[str, dict] = {}
    factors_by_component: dict[str, float] = {}
    for name, fn in _COMPONENTS.items():
        try:
            factor, evidence = fn(rb, cfg, now, ctx)
        except Exception as e:                           # one bad component shouldn't kill all
            factor, evidence = 1.0, {"factor": 1.0, "error": str(e)}
        evidence = dict(evidence)
        evidence.setdefault("factor", factor)
        components[name] = evidence
        factors_by_component[name] = float(factor)

    modifier, effective = apply_weights(factors_by_component, weights, bounds)
    raw_product = 1.0
    for v in effective.values():
        raw_product *= v
    clipped: float | None = None
    bmin = float(bounds.get("min", 0.50))
    bmax = float(bounds.get("max", 1.50))
    if raw_product < bmin:
        clipped = bmin
    elif raw_product > bmax:
        clipped = bmax

    return {
        "modifier":          modifier,
        "raw_product":       raw_product,
        "clipped_to":        clipped,
        "components":        components,
        "effective_factors": effective,
        "weights":           {n: float((weights or {}).get(n, 1.0))
                              for n in COMPONENT_NAMES},
        "applied_at":        now.isoformat(timespec="seconds"),
    }
