from __future__ import annotations

import logging
import sqlite3
from typing import Callable, Optional

from database.db import now_iso
from layer_minus1 import holdings_store

log = logging.getLogger(__name__)


CHANGE_MOMENTUM_FACTORS = {
    "new_position":         2.0,
    "significant_increase": 1.5,
    "moderate_increase":    1.2,
    "flat":                 1.0,
    "decrease":             0.7,
    "exit":                 0.0,
}

_TIER1 = frozenset({"1A", "1B"})


PriceFetcher = Callable[[str, str], Optional[float]]
SharesFetcher = Callable[[str], Optional[int]]


def compute_QoQ_change(
    current_shares: Optional[int],
    prior_shares: Optional[int],
    institution_tier: str,
) -> dict:
    """Classify QoQ position change per spec -1.2 table.

    current_shares=None means ticker not in current filing.
    prior_shares=None  means ticker not in prior filing.
    """
    has_current = current_shares is not None and current_shares > 0
    has_prior = prior_shares is not None and prior_shares > 0

    if has_current and not has_prior:
        return {
            "change_type": "new_position",
            "change_momentum_factor": CHANGE_MOMENTUM_FACTORS["new_position"],
            "change_pct": None,
        }
    if has_prior and not has_current:
        return {
            "change_type": "exit",
            "change_momentum_factor": CHANGE_MOMENTUM_FACTORS["exit"],
            "change_pct": -1.0,
        }
    if not has_current and not has_prior:
        return {
            "change_type": "flat",
            "change_momentum_factor": CHANGE_MOMENTUM_FACTORS["flat"],
            "change_pct": 0.0,
        }

    change_pct = (current_shares - prior_shares) / prior_shares

    if abs(change_pct) < 0.05:
        ctype = "flat"
    elif change_pct < 0:
        ctype = "decrease"
    else:
        sig_threshold = 0.10 if institution_tier in _TIER1 else 0.15
        if change_pct > sig_threshold:
            ctype = "significant_increase"
        else:
            ctype = "moderate_increase"

    return {
        "change_type": ctype,
        "change_momentum_factor": CHANGE_MOMENTUM_FACTORS[ctype],
        "change_pct": change_pct,
    }


def _aggregate_signal(contributions: list[dict]) -> str:
    """Reduce per-institution change_types to a ticker-level signal."""
    if not contributions:
        return "flat"
    types = [c["change_type"] for c in contributions]
    priority = [
        "new_position",
        "significant_increase",
        "moderate_increase",
        "flat",
        "decrease",
        "exit",
    ]
    for p in priority:
        if p in types:
            return p
    return "flat"


def assign_processing_tier(twos_score: float, signals: dict) -> str:
    """Map TWOS + signal set to a processing tier label.

    signals expected keys:
      tier1_new_position:           bool
      tier1_significant_increase:   bool
      any_significant_increase:     bool
      single_tier3_flat_or_decrease: bool
    """
    active = (
        twos_score >= 0.5
        or signals.get("tier1_new_position", False)
        or signals.get("tier1_significant_increase", False)
        or (
            signals.get("any_significant_increase", False)
            and twos_score >= 0.1
        )
    )
    if active:
        return "active"
    if twos_score >= 0.05:
        return "passive"
    if signals.get("single_tier3_flat_or_decrease", False):
        return "watchlist"
    if twos_score > 0:
        return "watchlist"
    return "not_tracked"


def _load_institution_rows(
    conn: sqlite3.Connection,
) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT id, name, tier, multiplier FROM institutions"
    ).fetchall()
    return {
        r["id"]: {
            "id": r["id"],
            "name": r["name"],
            "tier": r["tier"],
            "multiplier": r["multiplier"],
        }
        for r in rows
    }


def compute_TWOS(
    ticker: str,
    run_date: str,
    conn: sqlite3.Connection,
    *,
    price_fetcher: Optional[PriceFetcher] = None,
    shares_outstanding_fetcher: Optional[SharesFetcher] = None,
    total_shares_outstanding: Optional[int] = None,
) -> dict:
    institutions = _load_institution_rows(conn)

    contributions: list[dict] = []
    current_shares_total = 0
    earliest_filing_date: Optional[str] = None

    for inst_id, inst in institutions.items():
        current = holdings_store.get_holdings_as_of(
            inst_id, run_date, conn
        )
        current_for_ticker = next(
            (h for h in current if h.get("ticker") == ticker), None
        )
        current_filing_date = (
            current[0]["filing_date"] if current else None
        )

        prior_for_ticker = None
        if current_filing_date is not None:
            prior = holdings_store.get_prior_quarter_holdings(
                inst_id, current_filing_date, conn
            )
            prior_for_ticker = next(
                (h for h in prior if h.get("ticker") == ticker), None
            )

        cur_shares = (
            current_for_ticker["shares"] if current_for_ticker else None
        )
        prior_shares = (
            prior_for_ticker["shares"] if prior_for_ticker else None
        )

        if cur_shares is None and prior_shares is None:
            continue

        qoq = compute_QoQ_change(cur_shares, prior_shares, inst["tier"])
        contributions.append({
            "institution_id": inst_id,
            "institution_name": inst["name"],
            "tier": inst["tier"],
            "multiplier": inst["multiplier"],
            "current_shares": cur_shares,
            "prior_shares": prior_shares,
            "change_type": qoq["change_type"],
            "change_pct": qoq["change_pct"],
            "change_momentum_factor": qoq["change_momentum_factor"],
        })
        if cur_shares is not None:
            current_shares_total += cur_shares

        for h in current:
            if h.get("ticker") != ticker:
                continue
            fd = h.get("filing_date")
            if fd and (
                earliest_filing_date is None or fd < earliest_filing_date
            ):
                earliest_filing_date = fd

    total_so: Optional[int] = total_shares_outstanding
    if total_so is None:
        total_so = current_shares_total if current_shares_total > 0 else None
    if total_so is None and shares_outstanding_fetcher is not None:
        try:
            total_so = shares_outstanding_fetcher(ticker)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "shares_outstanding fetch failed for %s: %s",
                ticker, exc,
            )
            total_so = None
    if total_so is None or total_so <= 0:
        log.warning(
            "no shares_outstanding for %s; using market_value proxy",
            ticker,
        )
        total_shares_proxy = sum(
            (c.get("current_shares") or 0) for c in contributions
        )
        total_so = total_shares_proxy if total_shares_proxy > 0 else 1

    twos = 0.0
    for c in contributions:
        if c["current_shares"] is None:
            ownership_pct = 0.0
        else:
            ownership_pct = c["current_shares"] / total_so
        twos += (
            ownership_pct
            * float(c["multiplier"])
            * float(c["change_momentum_factor"])
        )

    crowding_flag = 0
    if len(contributions) > 4 and price_fetcher is not None \
            and earliest_filing_date is not None:
        try:
            earliest_price = price_fetcher(ticker, earliest_filing_date)
            current_price = price_fetcher(ticker, run_date)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "price fetch failed for %s: %s", ticker, exc,
            )
            earliest_price = current_price = None
        if (
            earliest_price is not None
            and current_price is not None
            and earliest_price > 0
        ):
            appreciation = (current_price - earliest_price) / earliest_price
            if appreciation > 0.50:
                crowding_flag = 1
                twos *= 0.60

    qoq_signal = _aggregate_signal(contributions)

    return {
        "ticker": ticker,
        "run_date": run_date,
        "twos_score": twos,
        "institution_count": len(contributions),
        "qoq_change_signal": qoq_signal,
        "crowding_flag": crowding_flag,
        "contributing_institutions": contributions,
        "earliest_filing_date": earliest_filing_date,
        "total_shares_outstanding": total_so,
    }


def _derive_signals(contributions: list[dict]) -> dict:
    tier1_contribs = [
        c for c in contributions if c["tier"] in _TIER1
    ]
    tier3_contribs = [
        c for c in contributions if c["tier"] == "3"
    ]
    return {
        "tier1_new_position": any(
            c["change_type"] == "new_position" for c in tier1_contribs
        ),
        "tier1_significant_increase": any(
            c["change_type"] == "significant_increase"
            for c in tier1_contribs
        ),
        "any_significant_increase": any(
            c["change_type"] == "significant_increase"
            for c in contributions
        ),
        "single_tier3_flat_or_decrease": (
            len(contributions) == 1
            and len(tier3_contribs) == 1
            and tier3_contribs[0]["change_type"] in {"flat", "decrease"}
        ),
    }


def score_ticker(
    ticker: str,
    run_date: str,
    conn: sqlite3.Connection,
    *,
    price_fetcher: Optional[PriceFetcher] = None,
    shares_outstanding_fetcher: Optional[SharesFetcher] = None,
    total_shares_outstanding: Optional[int] = None,
) -> dict:
    """Compute TWOS, assign processing tier, and persist twos_scores."""
    twos_result = compute_TWOS(
        ticker, run_date, conn,
        price_fetcher=price_fetcher,
        shares_outstanding_fetcher=shares_outstanding_fetcher,
        total_shares_outstanding=total_shares_outstanding,
    )
    signals = _derive_signals(twos_result["contributing_institutions"])
    tier = assign_processing_tier(twos_result["twos_score"], signals)
    ts = now_iso()
    conn.execute(
        "INSERT INTO twos_scores "
        "(ticker, run_date, twos_score, processing_tier, crowding_flag, "
        " institution_count, qoq_change_signal, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(ticker, run_date) DO UPDATE SET "
        " twos_score = excluded.twos_score, "
        " processing_tier = excluded.processing_tier, "
        " crowding_flag = excluded.crowding_flag, "
        " institution_count = excluded.institution_count, "
        " qoq_change_signal = excluded.qoq_change_signal, "
        " updated_at = excluded.updated_at",
        (
            ticker, run_date, twos_result["twos_score"], tier,
            twos_result["crowding_flag"],
            twos_result["institution_count"],
            twos_result["qoq_change_signal"], ts, ts,
        ),
    )
    conn.commit()
    twos_result["processing_tier"] = tier
    twos_result["signals"] = signals
    return twos_result


def run_quarterly_update(
    run_date: str,
    conn: sqlite3.Connection,
    from_date: str = "2020-01-01",
    to_date: Optional[str] = None,
    *,
    price_fetcher: Optional[PriceFetcher] = None,
    shares_outstanding_fetcher: Optional[SharesFetcher] = None,
    http_get: Optional[Callable[[str], bytes]] = None,
    skip_ingest: bool = False,
) -> list[dict]:
    """Ingest 13Fs in [from_date, to_date] then score every held ticker.

    from_date / to_date are passed through to
    edgar_13f_parser.ingest_all_institutions. to_date defaults to today
    (resolved inside the ingestor). Set skip_ingest=True to score only
    what is already in institution_holdings.
    """
    if not skip_ingest:
        from layer_minus1.edgar_13f_parser import ingest_all_institutions
        ingest_all_institutions(
            conn,
            from_date=from_date,
            to_date=to_date,
            http_get=http_get,
        )
    tickers = holdings_store.get_tickers_held_as_of(run_date, conn)
    results = []
    ts = now_iso()
    for ticker in tickers:
        r = score_ticker(
            ticker, run_date, conn,
            price_fetcher=price_fetcher,
            shares_outstanding_fetcher=shares_outstanding_fetcher,
        )
        conn.execute(
            "INSERT INTO companies (ticker, processing_tier, "
            " twos, crowding_flag, qoq_change_signal, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            " processing_tier = excluded.processing_tier, "
            " twos = excluded.twos, "
            " crowding_flag = excluded.crowding_flag, "
            " qoq_change_signal = excluded.qoq_change_signal, "
            " updated_at = excluded.updated_at",
            (
                ticker,
                r["processing_tier"] if r["processing_tier"] != "not_tracked"
                else "watchlist",
                r["twos_score"],
                r["crowding_flag"],
                r["qoq_change_signal"],
                ts, ts,
            ),
        )
        results.append(r)
    conn.commit()
    return results
