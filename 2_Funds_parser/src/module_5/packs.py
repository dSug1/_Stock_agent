"""Module 5 — context pack assembly.

Pure, deterministic functions:
- `compute_source_rank_hash(row)` — sha1 over pack-relevant fields for cache
  invalidation. Cosmetic column reorders don't bust the cache.
- `build_context_pack(row, prices_extremes, templates, pack_cfg)` — assemble the
  blob (dual-horizon sections + metadata) per spec § Context pack JSON blob schema.
- `load_narrative_templates(path)` — parse the per-archetype, per-horizon prose.
- `fetch_prices_extremes(conn, tickers, reference_date)` — bulk-load 52w
  min/max adjusted close per ticker from `data/prices.db`.

No network, no yfinance. Reads `data/prices.db` only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from module_1 import ConfigError

log = logging.getLogger(__name__)


# ─── Hash key (drives D24 cache invalidation) ────────────────────────────────

_HASH_FIELDS: tuple[str, ...] = (
    "ticker", "name_of_issuer", "cusip", "quarter",
    "archetype", "match_confidence",
    "score_3mo", "score_12mo", "composite_3mo", "composite_12mo",
    "best_horizon", "composite_best",
    "R_4", "R_12", "R_26", "R_52",
    "R_4_over_R_12", "R_4_over_R_26", "R_4_over_R_52",
    "R_12_over_R_26", "R_12_over_R_52", "R_26_over_R_52",
    "price_today",
    "price_source_date_4w", "price_source_date_12w",
    "price_source_date_26w", "price_source_date_52w",
    "weeks_of_history_used", "young_ticker_flag",
    "fund_count", "total_shares", "total_market_value",
    "qoq_share_change", "qoq_fund_count_change",
    "new_positions", "increased_positions", "decreased_positions", "exited_positions",
    "ticker_is_verified", "has_prefunded_warrants", "has_regular_warrants",
    "has_options", "has_unknown_class",
    "sector", "industry", "exchange", "currency",
    "market_cap", "shares_out", "last_close", "adv_30d",
)


def compute_source_rank_hash(
    row: pd.Series, *, fundamentals_fingerprint: str = "",
) -> str:
    """sha1 over the pack-relevant subset of a ranked row, plus an optional
    M4c fundamentals fingerprint string (keeps M5 cache fresh when M4c
    refreshes — D54)."""
    payload = {k: _to_hashable(row.get(k)) for k in _HASH_FIELDS}
    if fundamentals_fingerprint:
        payload["__fundamentals_fingerprint"] = fundamentals_fingerprint
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


# ─── Narrative templates ─────────────────────────────────────────────────────

def load_narrative_templates(path: Path) -> dict:
    """Load enrichment_narratives.yaml. Returns a {archetype: {horizon: str}} map.

    Must include a `default` archetype key as the fallback.
    """
    if not path.exists():
        raise ConfigError(f"narrative templates not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        loc = f"{path}:{mark.line + 1}:{mark.column + 1} " if mark else f"{path} "
        raise ConfigError(f"{loc}YAML syntax error: {e}") from e
    if not isinstance(raw, dict) or "templates" not in raw:
        raise ConfigError(f"{path}: top-level 'templates' key required")
    templates = raw["templates"]
    if not isinstance(templates, dict) or "default" not in templates:
        raise ConfigError(f"{path}: templates must include a 'default' entry")
    return templates


# ─── 52w extremes (one bulk query, avoids per-ticker DB round-trips) ─────────

def fetch_prices_extremes(
    conn: sqlite3.Connection,
    tickers: list[str],
    reference_date: pd.Timestamp,
) -> dict[str, dict]:
    """Return {ticker: {'low_52w': float|None, 'high_52w': float|None}} over
    the 52w trailing window ending at `reference_date`.

    One query, one pass. Missing tickers get {'low_52w': None, 'high_52w': None}.
    """
    if not tickers:
        return {}
    cutoff = (reference_date - pd.Timedelta(days=365)).date().isoformat()
    # SQLite has IN-list limit (~999 on older builds); chunk defensively.
    out: dict[str, dict] = {t: {"low_52w": None, "high_52w": None} for t in tickers}
    CHUNK = 500
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i : i + CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        sql = (
            f"SELECT ticker, MIN(adjusted_close), MAX(adjusted_close) "
            f"FROM prices "
            f"WHERE ticker IN ({placeholders}) AND date >= ? "
            f"GROUP BY ticker"
        )
        for tkr, lo, hi in conn.execute(sql, (*chunk, cutoff)).fetchall():
            out[tkr] = {
                "low_52w": float(lo) if lo is not None else None,
                "high_52w": float(hi) if hi is not None else None,
            }
    return out


# ─── Pack builder ────────────────────────────────────────────────────────────

def build_context_pack(
    row: pd.Series,
    *,
    prices_extremes: dict,
    templates: dict,
    pack_config: dict,
    source_rank_hash: str,
    built_at: str,
    fundamentals: Optional[dict] = None,
) -> dict:
    """Assemble one context pack dict from a ranked row + prices extremes.

    Pure function. Does no I/O. Deterministic given the same inputs.

    `fundamentals` (D54): M4c-supplied dict (financials + recent capital raises
    + recent insider transactions). When non-None AND `available=True`, the
    pack gains a top-level `fundamentals` key. When None or unavailable, the
    key is omitted (back-compat with packs built before M4c).
    """
    pack_version: str = str(pack_config.get("pack_version", "m5-v1"))

    ticker = _str(row.get("ticker"))
    currency = _str(row.get("currency"), default="USD") or "USD"
    currency_note_tail = (
        f" Priced in {currency}; convert before reasoning about USD appreciation."
        if currency and currency != "USD"
        else ""
    )

    identity = {
        "ticker": ticker,
        "name_of_issuer": _str(row.get("name_of_issuer")),
        "cusip": _str(row.get("cusip")),
        "quarter": _str(row.get("quarter")),
        "sector": _str(row.get("sector")),
        "industry": _str(row.get("industry")),
        "exchange": _str(row.get("exchange")),
    }

    market_snapshot = {
        "market_cap_usd": _int_or_none(row.get("market_cap")),
        "shares_out": _int_or_none(row.get("shares_out")),
        "last_close_usd": _float_or_none(row.get("last_close")),
        "price_today_usd": _float_or_none(row.get("price_today")),
        "adv_30d_usd": _float_or_none(row.get("adv_30d")),
        "currency": currency,
        "snapshot_fetched_at": _str(row.get("snapshot_fetched_at")),
    }

    # Price trajectory — all 10 ratios + anchor dates + history length.
    price_trajectory: dict = {}
    for k in (
        "R_4", "R_12", "R_26", "R_52",
        "R_4_over_R_12", "R_4_over_R_26", "R_4_over_R_52",
        "R_12_over_R_26", "R_12_over_R_52", "R_26_over_R_52",
    ):
        price_trajectory[k] = _float_or_none(row.get(k))
    for k in (
        "price_source_date_4w", "price_source_date_12w",
        "price_source_date_26w", "price_source_date_52w",
    ):
        price_trajectory[k] = _str(row.get(k))
    price_trajectory["weeks_of_history_used"] = _int_or_none(
        row.get("weeks_of_history_used")
    )
    price_trajectory["young_ticker_flag"] = bool(
        row.get("young_ticker_flag") or False
    )
    # Flag partial data when history is thin or extremes are missing.
    extremes = prices_extremes.get(ticker, {"low_52w": None, "high_52w": None})
    weeks = price_trajectory["weeks_of_history_used"] or 0
    if weeks < 52 or extremes["low_52w"] is None:
        price_trajectory["data_quality"] = "partial"
    else:
        price_trajectory["data_quality"] = "ok"

    fund_accumulation = {
        "fund_count": _int_or_none(row.get("fund_count")),
        "total_shares": _int_or_none(row.get("total_shares")),
        "total_market_value_usd": _float_or_none(row.get("total_market_value")),
        "qoq_share_change": _int_or_none(row.get("qoq_share_change")),
        "qoq_fund_count_change": _int_or_none(row.get("qoq_fund_count_change")),
        "new_positions": _int_or_none(row.get("new_positions")),
        "increased_positions": _int_or_none(row.get("increased_positions")),
        "decreased_positions": _int_or_none(row.get("decreased_positions")),
        "exited_positions": _int_or_none(row.get("exited_positions")),
        "ticker_is_verified": _bool(row.get("ticker_is_verified")),
        "has_prefunded_warrants": _bool(row.get("has_prefunded_warrants")),
        "has_regular_warrants": _bool(row.get("has_regular_warrants")),
        "has_options": _bool(row.get("has_options")),
        "has_unknown_class": _bool(row.get("has_unknown_class")),
    }

    archetype = _str(row.get("archetype")) or "unclassified"
    archetype_verdict = {
        "archetype": archetype,
        "description": _archetype_description(archetype),
        "match_confidence": _float_or_none(row.get("match_confidence")),
        "score_3mo": _int_or_none(row.get("score_3mo")),
        "score_12mo": _int_or_none(row.get("score_12mo")),
        "composite_3mo": _float_or_none(row.get("composite_3mo")),
        "composite_12mo": _float_or_none(row.get("composite_12mo")),
        "best_horizon": _str(row.get("best_horizon")),
        "composite_best": _float_or_none(row.get("composite_best")),
    }

    emphasis_3mo, emphasis_12mo = _resolve_emphasis(
        best_horizon=archetype_verdict["best_horizon"],
        weights=pack_config.get("emphasis_weights", {}) or {},
    )

    price_today = market_snapshot["price_today_usd"]
    near_term = _build_horizon_section(
        horizon_label="3mo",
        horizon_months=3,
        anchor_window="12w",
        score=archetype_verdict["score_3mo"],
        composite=archetype_verdict["composite_3mo"],
        emphasis=emphasis_3mo,
        ratio=price_trajectory["R_12"],
        price_today=price_today,
        extremes=extremes,
        archetype=archetype,
        templates=templates,
        row=row,
        currency_note_tail=currency_note_tail,
    )
    long_term = _build_horizon_section(
        horizon_label="12mo",
        horizon_months=12,
        anchor_window="52w",
        score=archetype_verdict["score_12mo"],
        composite=archetype_verdict["composite_12mo"],
        emphasis=emphasis_12mo,
        ratio=price_trajectory["R_52"],
        price_today=price_today,
        extremes=extremes,
        archetype=archetype,
        templates=templates,
        row=row,
        currency_note_tail=currency_note_tail,
    )

    pack: dict = {
        "pack_version": pack_version,
        "pack_built_at": built_at,
        "source_rank_hash": source_rank_hash,
        "identity": identity,
        "market_snapshot": market_snapshot,
        "price_trajectory": price_trajectory,
        "fund_accumulation": fund_accumulation,
        "archetype_verdict": archetype_verdict,
        "near_term_3mo": near_term,
        "long_term_12mo": long_term,
    }
    # D54: include the M4c-supplied fundamentals block when present + available.
    if fundamentals and fundamentals.get("available"):
        pack["fundamentals"] = fundamentals
    return pack


# ─── Horizon-section builder ─────────────────────────────────────────────────

def _build_horizon_section(
    *,
    horizon_label: str,
    horizon_months: int,
    anchor_window: str,
    score,
    composite,
    emphasis: float,
    ratio,
    price_today,
    extremes: dict,
    archetype: str,
    templates: dict,
    row: pd.Series,
    currency_note_tail: str,
) -> dict:
    anchor_price = None
    pct_off_anchor = None
    if price_today is not None and ratio is not None and ratio > 0:
        anchor_price = float(price_today) / float(ratio)
        pct_off_anchor = (float(price_today) - anchor_price) / anchor_price * 100.0

    lo = extremes.get("low_52w")
    hi = extremes.get("high_52w")
    pct_off_low = None
    pct_off_high = None
    if price_today is not None and lo is not None and lo > 0:
        pct_off_low = (float(price_today) - lo) / lo * 100.0
    if price_today is not None and hi is not None and hi > 0:
        pct_off_high = (float(price_today) - hi) / hi * 100.0

    narrative = _render_narrative(
        archetype=archetype,
        horizon_label=horizon_label,
        horizon_months=horizon_months,
        row=row,
        templates=templates,
        pct_off_anchor=pct_off_anchor,
        pct_off_low=pct_off_low,
        pct_off_high=pct_off_high,
    )
    narrative = narrative + currency_note_tail

    return {
        "horizon_months": horizon_months,
        "pattern_score": score,
        "composite": composite,
        "research_emphasis": emphasis,
        "entry_price_context": {
            "anchor_window": anchor_window,
            "anchor_price_usd": _round_or_none(anchor_price, 4),
            "pct_off_anchor": _round_or_none(pct_off_anchor, 2),
            "pct_off_52w_low": _round_or_none(pct_off_low, 2),
            "pct_off_52w_high": _round_or_none(pct_off_high, 2),
        },
        "narrative_summary": narrative,
    }


def _resolve_emphasis(
    best_horizon: Optional[str], weights: dict,
) -> tuple[float, float]:
    """Return (emphasis_3mo, emphasis_12mo). Summing to ~1.0."""
    best_w = float(weights.get("best", 0.7))
    other_w = float(weights.get("other", 0.3))
    equal_w = float(weights.get("equal", 0.5))
    if best_horizon == "3mo":
        return best_w, other_w
    if best_horizon == "12mo":
        return other_w, best_w
    # "equal" or anything unexpected (e.g. unclassified) -> 50/50 split
    return equal_w, 1.0 - equal_w


# ─── Narrative rendering ─────────────────────────────────────────────────────

class _SafeVal:
    """Wraps a value so that `format(value, spec)` never raises.

    - If value is None, renders as 'n/a' for any spec.
    - If the spec is incompatible with the value's type, falls back to str(value).
    This lets narrative templates use e.g. `{qoq_fund_count_change:+d}` without
    caring whether the underlying int is present or missing.
    """

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __format__(self, spec: str) -> str:
        if self.value is None:
            return "n/a"
        # Sensible default for floats — templates often write `{R_26}` expecting
        # 2 decimals of precision rather than the raw 16-digit repr.
        if spec == "" and isinstance(self.value, float):
            return f"{self.value:.2f}"
        try:
            return format(self.value, spec)
        except (TypeError, ValueError):
            return str(self.value)

    def __str__(self) -> str:
        return "n/a" if self.value is None else str(self.value)


class _SafeDict(dict):
    """format_map dict: unknown placeholders render as 'n/a' for any spec."""

    def __missing__(self, key):
        return _SafeVal(None)


def _render_narrative(
    *,
    archetype: str,
    horizon_label: str,
    horizon_months: int,
    row: pd.Series,
    templates: dict,
    pct_off_anchor: Optional[float],
    pct_off_low: Optional[float],
    pct_off_high: Optional[float],
) -> str:
    block = templates.get(archetype) or templates["default"]
    tmpl = block.get(horizon_label) or templates["default"].get(horizon_label, "")
    params = _SafeDict(
        ticker=_SafeVal(_str(row.get("ticker"))),
        archetype=_SafeVal(archetype),
        horizon_months=_SafeVal(horizon_months),
        sector=_SafeVal(_str(row.get("sector"))),
        industry=_SafeVal(_str(row.get("industry"))),
        fund_count=_SafeVal(_int_or_none(row.get("fund_count"))),
        qoq_fund_count_change=_SafeVal(_int_or_none(row.get("qoq_fund_count_change"))),
        new_positions=_SafeVal(_int_or_none(row.get("new_positions"))),
        increased_positions=_SafeVal(_int_or_none(row.get("increased_positions"))),
        composite_best=_SafeVal(_float_or_none(row.get("composite_best"))),
        score_3mo=_SafeVal(_int_or_none(row.get("score_3mo"))),
        score_12mo=_SafeVal(_int_or_none(row.get("score_12mo"))),
        match_confidence=_SafeVal(_float_or_none(row.get("match_confidence"))),
        pct_off_52w_low=_SafeVal(pct_off_low),
        pct_off_52w_high=_SafeVal(pct_off_high),
        pct_off_anchor=_SafeVal(pct_off_anchor),
        R_4=_SafeVal(_float_or_none(row.get("R_4"))),
        R_12=_SafeVal(_float_or_none(row.get("R_12"))),
        R_26=_SafeVal(_float_or_none(row.get("R_26"))),
        R_52=_SafeVal(_float_or_none(row.get("R_52"))),
    )
    try:
        return tmpl.format_map(params)
    except Exception as e:  # malformed template -> fallback rather than crash
        log.warning("Narrative template error for %s/%s: %s", archetype, horizon_label, e)
        return f"Pattern: {archetype}. Over {horizon_months} months, evaluate the setup."


# ─── Archetype description passthrough (mirrors archetypes.yaml) ─────────────

_ARCHETYPE_DESCRIPTIONS: dict[str, str] = {
    "fresh_awakening": "Flat-to-down over year, awakening in recent 12 weeks",
    "deep_base_breakout": "Deep intra-year crash, multi-month base, now breaking out (VCP-style)",
    "post_crash_rebase": "Down sharply over year, bottomed and stabilising",
    "early_breakout": "Recent move starting, not yet parabolic",
    "v_recovery": "Recent strength after mid-period dip; long-term positive",
    "mature_uptrend": "Sustained uptrend across all windows",
    "stage2_pullback": "Weinstein Stage 2B / Livermore continuation — year strong, minor recent pullback",
    "shallow_rebase": "Mild decline over year with recent turn-up",
    "quiet_compression": "Flat across all windows, coiled spring (no directional edge without catalyst)",
    "extended_uptrend": "Strong uptrend past mature_uptrend's cap — train has left but not parabolic",
    "late_stage_extension": "Minervini late-stage climax run — R_52 extended AND R_4 hot",
    "broken_trend": "Uptrend broken, recent weakness",
    "sustained_decline": "Falling across all windows — Minervini Stage 4 'dead money'",
    "parabolic_blowoff": "Train has left — recent move extreme",
    "unclassified": "No archetype matched above confidence threshold",
}


def _archetype_description(name: str) -> str:
    return _ARCHETYPE_DESCRIPTIONS.get(name, "")


# ─── Value coercion helpers (pandas NaN -> None; safe for JSON) ──────────────

def _to_hashable(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):  # numpy scalars
        try:
            v = v.item()
        except Exception:
            pass
    if isinstance(v, (pd.Timestamp, dt.date, dt.datetime)):
        return str(v)
    return v


def _str(v, default: Optional[str] = None) -> Optional[str]:
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    return str(v)


def _float_or_none(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _int_or_none(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _bool(v) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and math.isnan(v):
        return False
    return bool(v)


def _round_or_none(v: Optional[float], ndigits: int) -> Optional[float]:
    return round(v, ndigits) if v is not None else None


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    return str(v)


def _fmt_round(v: Optional[float], ndigits: int) -> str:
    if v is None:
        return "n/a"
    return f"{round(float(v), ndigits):.{ndigits}f}"


def _fmt_signed(v: Optional[int]) -> str:
    if v is None:
        return "n/a"
    return f"{int(v):+d}"
