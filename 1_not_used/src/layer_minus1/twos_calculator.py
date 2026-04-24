from __future__ import annotations

import logging
import sqlite3
import time
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

# Minimum position market_value (raw USD, post-migration v5 all filings
# are stored in whole dollars) for a Tier 1A/1B new_position or
# significant_increase signal to override the TWOS >= 3.0 threshold and
# force active monitoring. Positions below this are treated as clerical,
# tracking, or warrant positions; they still contribute to TWOS but do
# not alone trigger an active override.
# Calibrated April 2026.
TIER1_MIN_MARKET_VALUE_USD: int = 5_000_000


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
        twos_score >= 3.0
        or signals.get("tier1_new_position", False)
        or signals.get("tier1_significant_increase", False)
        or (
            signals.get("any_significant_increase", False)
            and twos_score >= 0.3
        )
    )
    if active:
        return "active"
    if twos_score >= 0.5:
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
        cur_market_value = (
            current_for_ticker["market_value"]
            if current_for_ticker else None
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
            "market_value": cur_market_value,
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
    # Exit-only / stale-ticker: no institution currently holds the
    # ticker (numerator is 0 for every contribution, and there may be
    # no contributions at all if the ticker is long-exited). Denominator
    # is irrelevant — skip the fetcher and the proxy-fallback warning.
    exit_only = total_so is None and current_shares_total == 0
    if total_so is None and not exit_only and shares_outstanding_fetcher is not None:
        try:
            total_so = shares_outstanding_fetcher(ticker)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "shares_outstanding fetch failed for %s: %s",
                ticker, exc,
            )
            total_so = None
    if total_so is None or total_so <= 0:
        if not exit_only:
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
            c["change_type"] == "new_position"
            and (c.get("market_value") or 0) >= TIER1_MIN_MARKET_VALUE_USD
            for c in tier1_contribs
        ),
        "tier1_significant_increase": any(
            c["change_type"] == "significant_increase"
            and (c.get("market_value") or 0) >= TIER1_MIN_MARKET_VALUE_USD
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

    Bulk implementation: two aggregate SQL queries load all current and
    prior-quarter holdings; TWOS is computed in memory; writes to
    twos_scores and companies are batched via executemany. For
    per-ticker refresh (Layer 3 registry), use score_ticker() /
    compute_TWOS() instead — those remain the canonical single-ticker
    path and produce identical results.
    """
    if not skip_ingest:
        from layer_minus1.edgar_13f_parser import ingest_all_institutions
        ingest_all_institutions(
            conn,
            from_date=from_date,
            to_date=to_date,
            http_get=http_get,
        )

    start = time.time()
    institutions = _load_institution_rows(conn)

    # --- STEP 1: all current holdings (most-recent filing <= run_date
    # per institution), in one query.
    current_rows = conn.execute(
        """
        SELECT
            ih.ticker, ih.shares, ih.market_value,
            ih.institution_id, ih.filing_date
        FROM institution_holdings ih
        WHERE ih.ticker IS NOT NULL
          AND ih.filing_date = (
              SELECT MAX(ih2.filing_date)
              FROM institution_holdings ih2
              WHERE ih2.institution_id = ih.institution_id
                AND ih2.filing_date <= ?
          )
        """,
        (run_date,),
    ).fetchall()

    current_fd_by_inst: dict[int, str] = {}
    # {ticker: {inst_id: {shares, market_value}}}
    current_by_ticker: dict[str, dict[int, dict]] = {}
    for r in current_rows:
        inst_id = r["institution_id"]
        current_fd_by_inst[inst_id] = r["filing_date"]
        current_by_ticker.setdefault(r["ticker"], {})[inst_id] = {
            "shares": r["shares"],
            "market_value": r["market_value"],
        }

    # --- STEP 2: all prior-quarter holdings (filing immediately before
    # each institution's current filing), in one query.
    prior_rows = conn.execute(
        """
        WITH current_filings AS (
            SELECT institution_id, MAX(filing_date) AS cur_fd
            FROM institution_holdings
            WHERE filing_date <= ?
            GROUP BY institution_id
        ),
        prior_filings AS (
            SELECT ih.institution_id, MAX(ih.filing_date) AS prior_fd
            FROM institution_holdings ih
            JOIN current_filings cf
              ON cf.institution_id = ih.institution_id
            WHERE ih.filing_date < cf.cur_fd
            GROUP BY ih.institution_id
        )
        SELECT ih.ticker, ih.shares, ih.institution_id
        FROM institution_holdings ih
        JOIN prior_filings pf
          ON pf.institution_id = ih.institution_id
          AND pf.prior_fd = ih.filing_date
        WHERE ih.ticker IS NOT NULL
        """,
        (run_date,),
    ).fetchall()

    # {ticker: {inst_id: prior_shares}}
    prior_by_ticker: dict[str, dict[int, int]] = {}
    for r in prior_rows:
        prior_by_ticker.setdefault(r["ticker"], {})[r["institution_id"]] = (
            r["shares"]
        )

    # --- STEP 3: compute TWOS in memory for every ticker that appears
    # in either the current or prior quarter (so exits are captured).
    all_tickers: set[str] = set(current_by_ticker) | set(prior_by_ticker)

    ts = now_iso()
    twos_rows: list[tuple] = []
    companies_rows: list[tuple] = []
    results: list[dict] = []
    processed = 0

    for ticker in sorted(all_tickers):
        cur_map = current_by_ticker.get(ticker, {})
        prior_map = prior_by_ticker.get(ticker, {})
        inst_ids = set(cur_map) | set(prior_map)

        contributions: list[dict] = []
        current_shares_total = 0
        earliest_filing_date: Optional[str] = None

        for inst_id in inst_ids:
            inst = institutions.get(inst_id)
            if inst is None:
                continue
            cur = cur_map.get(inst_id)
            cur_shares = cur["shares"] if cur else None
            cur_market_value = cur["market_value"] if cur else None
            prior_shares = prior_map.get(inst_id)
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
                "market_value": cur_market_value,
                "change_type": qoq["change_type"],
                "change_pct": qoq["change_pct"],
                "change_momentum_factor": qoq["change_momentum_factor"],
            })
            if cur_shares is not None:
                current_shares_total += cur_shares
                fd = current_fd_by_inst.get(inst_id)
                if fd and (
                    earliest_filing_date is None or fd < earliest_filing_date
                ):
                    earliest_filing_date = fd

        # --- denominator selection (mirrors compute_TWOS) ---
        total_so: Optional[int] = (
            current_shares_total if current_shares_total > 0 else None
        )
        exit_only = total_so is None and current_shares_total == 0
        if (
            total_so is None
            and not exit_only
            and shares_outstanding_fetcher is not None
        ):
            try:
                total_so = shares_outstanding_fetcher(ticker)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "shares_outstanding fetch failed for %s: %s",
                    ticker, exc,
                )
                total_so = None
        if total_so is None or total_so <= 0:
            if not exit_only:
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
        if (
            len(contributions) > 4
            and price_fetcher is not None
            and earliest_filing_date is not None
        ):
            try:
                earliest_price = price_fetcher(ticker, earliest_filing_date)
                current_price = price_fetcher(ticker, run_date)
            except Exception as exc:  # noqa: BLE001
                log.warning("price fetch failed for %s: %s", ticker, exc)
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
        signals = _derive_signals(contributions)
        tier = assign_processing_tier(twos, signals)

        twos_rows.append((
            ticker, run_date, twos, tier, crowding_flag,
            len(contributions), qoq_signal, ts, ts,
        ))
        companies_rows.append((
            ticker,
            tier if tier != "not_tracked" else "watchlist",
            twos, crowding_flag, qoq_signal, ts, ts,
        ))
        results.append({
            "ticker": ticker,
            "run_date": run_date,
            "twos_score": twos,
            "institution_count": len(contributions),
            "qoq_change_signal": qoq_signal,
            "crowding_flag": crowding_flag,
            "contributing_institutions": contributions,
            "earliest_filing_date": earliest_filing_date,
            "total_shares_outstanding": total_so,
            "processing_tier": tier,
            "signals": signals,
        })

        processed += 1
        if processed % 500 == 0:
            print(".", end="", flush=True)

    # --- STEP 4: bulk writes ---
    conn.executemany(
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
        twos_rows,
    )
    conn.executemany(
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
        companies_rows,
    )
    conn.commit()

    elapsed = time.time() - start
    print(f"\nScored {processed} tickers in {elapsed:.1f}s")
    return results
