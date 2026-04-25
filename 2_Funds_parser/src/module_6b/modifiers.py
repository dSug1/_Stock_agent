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


def _component_crowding(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
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


def _component_financing(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
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


def _component_dilution(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
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


def _component_insider(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
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


def _component_mgmt(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("mgmt", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    score = (rb.get("mgmt_track_record_score") or {}).get("score")
    if score is None:
        factor = float(ccfg.get("missing_factor", 1.00))
        return factor, {"factor": factor, "score": None, "fallback": True}
    factor = _band_lookup_score_eq(float(score), ccfg["bands"])
    return factor, {"factor": factor, "score": float(score)}


def _component_acquisition(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("acquisition", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    score = (rb.get("acquisition_target") or {}).get("score")
    if score is None:
        factor = float(ccfg.get("missing_factor", 1.00))
        return factor, {"factor": factor, "score": None, "fallback": True}
    factor = _band_lookup_score_eq(float(score), ccfg["bands"])
    return factor, {"factor": factor, "score": float(score)}


def _component_moat(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
    ccfg = cfg.get("components", {}).get("moat", {})
    if not ccfg.get("enabled", True):
        return _disabled_result()
    score = (rb.get("moat") or {}).get("score")
    if score is None:
        factor = float(ccfg.get("missing_factor", 1.00))
        return factor, {"factor": factor, "score": None, "fallback": True}
    factor = _band_lookup_score_eq(float(score), ccfg["bands"])
    return factor, {"factor": factor, "score": float(score)}


def _component_failures(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
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


def _component_concentration(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:
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


# Component registry — order doesn't matter for the product but is fixed
# for stable JSON / report rendering / slider order in the UI.
_COMPONENTS: dict[str, Callable[[dict, dict, datetime], tuple[float, dict]]] = {
    "crowding":      _component_crowding,
    "financing":     _component_financing,
    "dilution":      _component_dilution,
    "insider":       _component_insider,
    "mgmt":          _component_mgmt,
    "acquisition":   _component_acquisition,
    "moat":          _component_moat,
    "failures":      _component_failures,
    "concentration": _component_concentration,
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
) -> dict:
    """Run all 9 components, multiply, clip, return the audit payload.

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

    Empty / missing research_brief → returns a 1.0 neutral modifier with all
    components in their `missing` / `none` state. Caller decides whether to
    skip writing the row.
    """
    now = _coerce_now(now)
    rb = research_brief or {}
    bounds = cfg.get("bounds", {}) or {}

    components: dict[str, dict] = {}
    factors_by_component: dict[str, float] = {}
    for name, fn in _COMPONENTS.items():
        try:
            factor, evidence = fn(rb, cfg, now)
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
