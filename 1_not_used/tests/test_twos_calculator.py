from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from database.db import init_db, get_connection
from layer_minus1 import holdings_store
from layer_minus1 import cusip_resolver
from layer_minus1.twos_calculator import (
    TIER1_MIN_MARKET_VALUE_USD,
    _derive_signals,
    assign_processing_tier,
    compute_QoQ_change,
    compute_TWOS,
    run_quarterly_update,
    score_ticker,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "stockpicker.db"
    init_db(db_path)
    return get_connection(db_path)


def _inst_id_by_name(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM institutions WHERE name = ?", (name,)
    ).fetchone()
    assert row is not None, f"institution not seeded: {name}"
    return int(row["id"])


def _save(
    conn: sqlite3.Connection,
    inst_id: int,
    filing_date: str,
    period_of_report: str,
    ticker: str,
    cusip: str,
    shares: int,
    market_value: int = 1,
) -> None:
    holdings_store.save_holdings(
        inst_id, filing_date, period_of_report,
        [{
            "cusip": cusip, "ticker": ticker,
            "shares": shares, "market_value": market_value,
        }],
        conn,
    )


# ---------------------------------------------------------------------
# compute_QoQ_change — classification table
# ---------------------------------------------------------------------

def test_qoq_new_position() -> None:
    r = compute_QoQ_change(None, None, "1A")
    # both none → flat
    assert r["change_type"] == "flat"

    r = compute_QoQ_change(1000, None, "1A")
    assert r["change_type"] == "new_position"
    assert r["change_momentum_factor"] == 2.0


def test_qoq_exit() -> None:
    r = compute_QoQ_change(None, 1000, "1A")
    assert r["change_type"] == "exit"
    assert r["change_momentum_factor"] == 0.0


def test_qoq_significant_increase_tier1a_threshold_10pct() -> None:
    # 10.1% rise → significant for Tier 1A (10% threshold)
    r = compute_QoQ_change(1101, 1000, "1A")
    assert r["change_type"] == "significant_increase"
    assert r["change_momentum_factor"] == 1.5


def test_qoq_significant_increase_tier3_threshold_15pct() -> None:
    # 10.1% rise → moderate for Tier 3 (15% threshold)
    r = compute_QoQ_change(1101, 1000, "3")
    assert r["change_type"] == "moderate_increase"
    assert r["change_momentum_factor"] == 1.2


def test_qoq_flat_near_zero() -> None:
    r = compute_QoQ_change(1020, 1000, "1A")
    assert r["change_type"] == "flat"


def test_qoq_decrease() -> None:
    r = compute_QoQ_change(800, 1000, "1A")
    assert r["change_type"] == "decrease"
    assert r["change_momentum_factor"] == 0.7


# ---------------------------------------------------------------------
# compute_TWOS — formula and tier handling
# ---------------------------------------------------------------------

def test_twos_formula_two_institutions(tmp_path: Path) -> None:
    """Manual calculation:
    - Baker Bros (Tier 1A, mult 4.0): 1000 shares current, none prior → new_position (×2.0)
    - Greenlight (Tier 2A, mult 2.5): 4000 shares current, 3000 prior → 33.3% rise → significant (×1.5, Tier 2A threshold 15%)
    total_so = 1000 + 4000 = 5000
    TWOS = (1000/5000)*4.0*2.0 + (4000/5000)*2.5*1.5
         = 0.2*8.0 + 0.8*3.75
         = 1.6 + 3.0 = 4.6
    """
    conn = _fresh_db(tmp_path)
    try:
        baker = _inst_id_by_name(conn, "Baker Bros. Advisors")
        greenlight = _inst_id_by_name(conn, "Greenlight Capital")

        _save(conn, baker, "2024-05-15", "2024-03-31",
              "ACME", "C1", 1000)
        _save(conn, greenlight, "2024-02-14", "2023-12-31",
              "ACME", "C1", 3000)
        _save(conn, greenlight, "2024-05-15", "2024-03-31",
              "ACME", "C1", 4000)

        r = compute_TWOS("ACME", "2024-05-15", conn)
        assert r["institution_count"] == 2
        assert r["twos_score"] == pytest.approx(4.6, rel=1e-3)
    finally:
        conn.close()


def test_twos_tier4_contributes_zero(tmp_path: Path) -> None:
    conn = _fresh_db(tmp_path)
    try:
        vanguard = _inst_id_by_name(conn, "Vanguard index")
        _save(conn, vanguard, "2024-05-15", "2024-03-31",
              "ACME", "C1", 1000)
        r = compute_TWOS("ACME", "2024-05-15", conn)
        assert r["institution_count"] == 1
        assert r["twos_score"] == pytest.approx(0.0, abs=1e-9)
    finally:
        conn.close()


def test_twos_tier4_still_counted_in_institution_count(
    tmp_path: Path,
) -> None:
    conn = _fresh_db(tmp_path)
    try:
        baker = _inst_id_by_name(conn, "Baker Bros. Advisors")
        vanguard = _inst_id_by_name(conn, "Vanguard index")
        _save(conn, baker, "2024-05-15", "2024-03-31",
              "ACME", "C1", 1000)
        _save(conn, vanguard, "2024-05-15", "2024-03-31",
              "ACME", "C1", 5000)
        r = compute_TWOS("ACME", "2024-05-15", conn)
        assert r["institution_count"] == 2
    finally:
        conn.close()


def test_twos_exit_factor_zero_but_counted(tmp_path: Path) -> None:
    conn = _fresh_db(tmp_path)
    try:
        baker = _inst_id_by_name(conn, "Baker Bros. Advisors")
        _save(conn, baker, "2024-02-14", "2023-12-31",
              "ACME", "C1", 1000)
        # No filing for ACME in the later filing → exit
        _save(conn, baker, "2024-05-15", "2024-03-31",
              "OTHR", "C2", 500)

        r = compute_TWOS("ACME", "2024-05-15", conn)
        assert r["institution_count"] == 1
        # exit contributes momentum 0.0 → TWOS must be 0.0
        assert r["twos_score"] == pytest.approx(0.0, abs=1e-9)
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Crowding penalty
# ---------------------------------------------------------------------

def _five_tier1_shares(conn: sqlite3.Connection, ticker: str) -> None:
    """Seed 5 institutions for crowding tests.

    First institution files only at the earlier date so earliest
    filing_date across institutions is 2024-02-14.
    """
    early = _inst_id_by_name(conn, "Baker Bros. Advisors")
    _save(conn, early, "2024-02-14", "2023-12-31",
          ticker, "C1", 1000)
    for name in [
        "RA Capital Management",
        "Perceptive Advisors",
        "Boxer Capital (Tavistock)",
        "Deerfield Management",
    ]:
        iid = _inst_id_by_name(conn, name)
        _save(conn, iid, "2024-05-15", "2024-03-31",
              ticker, "C1", 1000)


def test_crowding_triggers_over_four_and_over_50pct(
    tmp_path: Path,
) -> None:
    conn = _fresh_db(tmp_path)
    try:
        _five_tier1_shares(conn, "ACME")
        prices = {"2024-02-14": 10.0, "2024-05-15": 20.0}  # +100%
        r = compute_TWOS(
            "ACME", "2024-05-15", conn,
            price_fetcher=lambda t, d: prices.get(d),
        )
        assert r["crowding_flag"] == 1
    finally:
        conn.close()


def test_crowding_does_not_trigger_at_exactly_four(
    tmp_path: Path,
) -> None:
    """Four institutions is not > 4, so crowding never triggers."""
    conn = _fresh_db(tmp_path)
    try:
        early = _inst_id_by_name(conn, "Baker Bros. Advisors")
        _save(conn, early, "2024-02-14", "2023-12-31",
              "ACME", "C1", 1000)
        for name in [
            "RA Capital Management",
            "Perceptive Advisors",
            "Boxer Capital (Tavistock)",
        ]:
            iid = _inst_id_by_name(conn, name)
            _save(conn, iid, "2024-05-15", "2024-03-31",
                  "ACME", "C1", 1000)
        prices = {"2024-02-14": 10.0, "2024-05-15": 100.0}  # +900%
        r = compute_TWOS(
            "ACME", "2024-05-15", conn,
            price_fetcher=lambda t, d: prices.get(d),
        )
        assert r["institution_count"] == 4
        assert r["crowding_flag"] == 0
    finally:
        conn.close()


def test_crowding_does_not_trigger_at_exactly_50pct(
    tmp_path: Path,
) -> None:
    conn = _fresh_db(tmp_path)
    try:
        _five_tier1_shares(conn, "ACME")
        prices = {"2024-02-14": 10.0, "2024-05-15": 15.0}  # exactly +50%
        r = compute_TWOS(
            "ACME", "2024-05-15", conn,
            price_fetcher=lambda t, d: prices.get(d),
        )
        assert r["crowding_flag"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------
# assign_processing_tier
# ---------------------------------------------------------------------

def test_processing_tier_active_on_tier1_new_position() -> None:
    tier = assign_processing_tier(
        twos_score=0.0,
        signals={"tier1_new_position": True},
    )
    assert tier == "active"


def test_processing_tier_active_on_high_twos() -> None:
    assert assign_processing_tier(3.5, {}) == "active"


def test_processing_tier_passive_on_midrange_twos() -> None:
    tier = assign_processing_tier(
        twos_score=1.0,
        signals={},
    )
    assert tier == "passive"


def test_processing_tier_watchlist_single_tier3_flat() -> None:
    tier = assign_processing_tier(
        twos_score=0.0,
        signals={"single_tier3_flat_or_decrease": True},
    )
    assert tier == "watchlist"


def test_processing_tier_not_tracked_when_empty() -> None:
    assert assign_processing_tier(0.0, {}) == "not_tracked"


def _tier1_contrib(
    change_type: str, market_value: int, tier: str = "1A",
) -> dict:
    return {
        "institution_id": 1,
        "institution_name": "Test Fund",
        "tier": tier,
        "multiplier": 4.0 if tier == "1A" else 3.5,
        "current_shares": 1000,
        "prior_shares": 500 if change_type == "significant_increase" else None,
        "market_value": market_value,
        "change_type": change_type,
        "change_pct": 1.0 if change_type == "significant_increase" else None,
        "change_momentum_factor": 2.0 if change_type == "new_position" else 1.5,
    }


def test_tier1_new_position_gate_blocks_below_threshold() -> None:
    """$4M (4_000_000 USD) is below the $5M gate — signal must not fire."""
    signals = _derive_signals(
        [_tier1_contrib("new_position", market_value=4_000_000)]
    )
    assert signals["tier1_new_position"] is False


def test_tier1_new_position_gate_fires_at_threshold() -> None:
    """$5M (5_000_000 USD) is exactly at the gate — signal must fire."""
    signals = _derive_signals(
        [_tier1_contrib(
            "new_position", market_value=TIER1_MIN_MARKET_VALUE_USD
        )]
    )
    assert signals["tier1_new_position"] is True


def test_tier1_new_position_gate_fires_well_above_threshold() -> None:
    """$50M position — signal must fire."""
    signals = _derive_signals(
        [_tier1_contrib("new_position", market_value=50_000_000)]
    )
    assert signals["tier1_new_position"] is True


def test_tier1_significant_increase_gate_blocks_below_threshold() -> None:
    signals = _derive_signals(
        [_tier1_contrib("significant_increase", market_value=4_000_000)]
    )
    assert signals["tier1_significant_increase"] is False


def test_tier1_significant_increase_gate_fires_at_threshold() -> None:
    signals = _derive_signals(
        [_tier1_contrib(
            "significant_increase",
            market_value=TIER1_MIN_MARKET_VALUE_USD,
        )]
    )
    assert signals["tier1_significant_increase"] is True


def test_run_quarterly_update_matches_compute_TWOS(
    tmp_path: Path,
) -> None:
    """Bulk path must produce the same twos_score / processing_tier /
    institution_count / crowding_flag as compute_TWOS called per ticker.
    Seeds 5 tickers across 2 institutions with a mix of new positions,
    prior-only exits, increases, and flat holds.
    """
    conn = _fresh_db(tmp_path)
    try:
        baker = _inst_id_by_name(conn, "Baker Bros. Advisors")
        greenlight = _inst_id_by_name(conn, "Greenlight Capital")

        # AAA — Baker new position, Greenlight exit
        _save(conn, greenlight, "2024-02-14", "2023-12-31",
              "AAA", "C-AAA", 2000)
        _save(conn, baker, "2024-05-15", "2024-03-31",
              "AAA", "C-AAA", 1000)

        # BBB — Baker significant increase, Greenlight moderate increase
        _save(conn, baker, "2024-02-14", "2023-12-31",
              "BBB", "C-BBB", 1000)
        _save(conn, baker, "2024-05-15", "2024-03-31",
              "BBB", "C-BBB", 1500)
        _save(conn, greenlight, "2024-02-14", "2023-12-31",
              "BBB", "C-BBB", 1000)
        _save(conn, greenlight, "2024-05-15", "2024-03-31",
              "BBB", "C-BBB", 1100)

        # CCC — Baker flat
        _save(conn, baker, "2024-02-14", "2023-12-31",
              "CCC", "C-CCC", 1000)
        _save(conn, baker, "2024-05-15", "2024-03-31",
              "CCC", "C-CCC", 1020)

        # DDD — Greenlight only, new position
        _save(conn, greenlight, "2024-05-15", "2024-03-31",
              "DDD", "C-DDD", 5000)

        # EEE — Baker exit only (held prior, not in current filing)
        _save(conn, baker, "2024-02-14", "2023-12-31",
              "EEE", "C-EEE", 500)
        # Baker's current filing already exists (from BBB/CCC/AAA) so EEE is an exit

        run_date = "2024-05-15"
        bulk_results = run_quarterly_update(
            run_date, conn,
            skip_ingest=True,
        )
        bulk_by_ticker = {r["ticker"]: r for r in bulk_results}

        for ticker in ("AAA", "BBB", "CCC", "DDD", "EEE"):
            single = compute_TWOS(ticker, run_date, conn)
            bulk = bulk_by_ticker[ticker]

            assert bulk["twos_score"] == pytest.approx(
                single["twos_score"], rel=1e-9, abs=1e-12
            ), f"{ticker}: twos mismatch"
            assert bulk["institution_count"] == single["institution_count"], (
                f"{ticker}: institution_count mismatch"
            )
            assert bulk["crowding_flag"] == single["crowding_flag"], (
                f"{ticker}: crowding_flag mismatch"
            )
            assert bulk["qoq_change_signal"] == single["qoq_change_signal"], (
                f"{ticker}: qoq_change_signal mismatch"
            )

            row = conn.execute(
                "SELECT twos_score, processing_tier, institution_count, "
                "       crowding_flag, qoq_change_signal "
                "FROM twos_scores WHERE ticker = ? AND run_date = ?",
                (ticker, run_date),
            ).fetchone()
            assert row is not None, f"{ticker}: no twos_scores row"
            assert row["twos_score"] == pytest.approx(
                single["twos_score"], rel=1e-9, abs=1e-12
            )
            assert row["institution_count"] == single["institution_count"]
    finally:
        conn.close()


def test_score_ticker_persists_row(tmp_path: Path) -> None:
    conn = _fresh_db(tmp_path)
    try:
        baker = _inst_id_by_name(conn, "Baker Bros. Advisors")
        _save(conn, baker, "2024-05-15", "2024-03-31",
              "ACME", "C1", 1000)
        r = score_ticker("ACME", "2024-05-15", conn)
        assert r["processing_tier"] == "active"  # Tier 1A new position
        row = conn.execute(
            "SELECT twos_score, processing_tier, institution_count "
            "FROM twos_scores WHERE ticker = ? AND run_date = ?",
            ("ACME", "2024-05-15"),
        ).fetchone()
        assert row is not None
        assert row["processing_tier"] == "active"
        assert row["institution_count"] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------
# cusip_resolver caching
# ---------------------------------------------------------------------

class _Counter:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.calls = 0

    def __call__(self, url: str, body: bytes, headers: dict) -> bytes:
        self.calls += 1
        return self.response


def test_cusip_resolver_cache_hits_api_once(tmp_path: Path) -> None:
    conn = _fresh_db(tmp_path)
    try:
        import json
        response = json.dumps([
            # securityType is required by the resolver's US-equity allow-list
            # (_record_is_equity); this test only checks caching (one API call).
            {"data": [{"ticker": "ACME", "exchCode": "US",
                       "securityType": "Common Stock"}]},
        ]).encode("utf-8")
        counter = _Counter(response)
        first = cusip_resolver.resolve_cusip(
            "000360206", conn, http_post=counter,
        )
        assert first == "ACME"
        assert counter.calls == 1

        second = cusip_resolver.resolve_cusip(
            "000360206", conn, http_post=counter,
        )
        assert second == "ACME"
        assert counter.calls == 1  # no extra call
    finally:
        conn.close()


def test_cusip_resolver_caches_null_on_no_match(tmp_path: Path) -> None:
    conn = _fresh_db(tmp_path)
    try:
        import json
        response = json.dumps([{"warning": "No identifier found"}]).encode(
            "utf-8"
        )
        counter = _Counter(response)
        result = cusip_resolver.resolve_cusip(
            "999999999", conn, http_post=counter,
        )
        assert result is None
        # Second call should hit cache, not API
        result2 = cusip_resolver.resolve_cusip(
            "999999999", conn, http_post=counter,
        )
        assert result2 is None
        assert counter.calls == 1
    finally:
        conn.close()
