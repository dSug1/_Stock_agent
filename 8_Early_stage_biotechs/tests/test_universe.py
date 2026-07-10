"""M4 — universe orchestrator offline tests (injected provider listings; no network)."""

from __future__ import annotations

import pytest

from early_detection.config import Config
from early_detection.models import Listing
from early_detection.store import Store
from early_detection.universe import build_universe


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "u.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "u.db", m6_store_path=tmp_path / "nope.db",
                  mktcap_floor_usd=10_000_000.0, markets=("US", "CA"))


def _listings():
    return {
        "m6": [
            Listing(name="Acrivon Therapeutics", ticker="ACRV", exchange="NASDAQ", country="US",
                    lei="LEIACRV", mktcap_usd=7.1e7, in_existing_universe=True, provenance=["m6"]),
        ],
        "edgar_us": [
            # same company as the M6 seed but discovered via CIK — union-find MERGES it with the M6
            # seed through the shared ticker+country signal (M6 has lei+ticker, edgar has cik+ticker).
            Listing(name="Acrivon Therapeutics, Inc.", ticker="ACRV", exchange="Nasdaq", country="US",
                    cik="0001", sic="2836", sector_normalized="therapeutics", provenance=["edgar_us"]),
            Listing(name="Tiny Bio Inc", ticker="TINY", exchange="NASDAQ", country="US", cik="0002",
                    sic="2834", sector_normalized="therapeutics", mktcap_usd=5.0e6,  # below $10M floor
                    provenance=["edgar_us"]),
            Listing(name="Keyless Co", ticker=None, exchange=None, country="US", provenance=["edgar_us"]),
        ],
        "edgar_canada": [
            Listing(name="Maple Bio", ticker="MAPL", exchange="NASDAQ", country="CA", cik="0003",
                    sic="2836", sector_normalized="therapeutics", filer_type="FPI",
                    mktcap_unknown=True, provenance=["edgar_canada"]),
        ],
    }


def test_build_persists_entities_and_funnel(store, cfg):
    res = build_universe(store, cfg, provider_listings=_listings())
    # entities: ACRV (M6+EDGAR merged via ticker+country), TINY, MAPL = 3; Keyless Co → queue
    assert store.count_entities() == 3
    assert res.counts["queued"] == 1
    assert store.count_recon() == 1
    # the merged ACRV carries both the priority flag and the EDGAR-sourced CIK
    acrv = store.find_entity_by_key(lei="LEIACRV")
    assert acrv.in_existing_universe is True and acrv.cik == "0000000001"
    # run_meta recorded
    assert store.conn.execute("SELECT COUNT(*) FROM run_meta").fetchone()[0] == 1


def test_below_floor_is_flagged_not_deleted(store, cfg):
    build_universe(store, cfg, provider_listings=_listings())
    tiny = store.find_entity_by_key(cik="0002")
    assert tiny is not None and tiny.is_live is True            # NOT deleted
    flag = store.conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='flagged' AND reason='below_cap_floor'").fetchone()[0]
    assert flag == 1


def test_unknown_cap_not_floored(store, cfg):
    build_universe(store, cfg, provider_listings=_listings())
    mapl = store.find_entity_by_key(cik="0003")
    assert mapl.mktcap_unknown is True and mapl.is_live is True
    # unknown cap must NOT be counted as below-floor
    floored = store.conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE reason='below_cap_floor' AND entity_id=?",
        (mapl.entity_id,)).fetchone()[0]
    assert floored == 0


def test_priority_tier_flag_persists(store, cfg):
    build_universe(store, cfg, provider_listings=_listings())
    acrv = store.find_entity_by_key(lei="LEIACRV")
    assert acrv.in_existing_universe is True


def test_dry_run_writes_nothing(store, cfg):
    res = build_universe(store, cfg, provider_listings=_listings(), dry_run=True)
    assert store.count_entities() == 0
    assert store.count_recon() == 0
    assert res.counts["entities"] == 3     # computed, just not persisted


def test_listing_rows_recorded(store, cfg):
    build_universe(store, cfg, provider_listings=_listings())
    tiny = store.find_entity_by_key(cik="0002")
    rows = store.listings_for(tiny.entity_id)
    assert any(r["ticker"] == "TINY" for r in rows)
