"""M5 — market-cap enrich offline tests (injected fetch, no network/yfinance)."""

from __future__ import annotations

import pytest

from early_detection.config import Config
from early_detection.enrich import enrich_caps
from early_detection.models import Entity
from early_detection.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "e.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "e.db", mktcap_floor_usd=10_000_000.0)


def _seed(store):
    for eid, tkr in [("cik:1", "BIGCAP"), ("cik:2", "SMALL"), ("cik:3", "DEAD"), ("cik:4", "CADCO")]:
        store.upsert_entity(Entity(entity_id=eid, legal_name=tkr, ticker_primary=tkr,
                                   jurisdiction="US", mktcap_unknown=True))


def test_enrich_fills_caps_converts_fx_and_flags_floor(store, cfg):
    _seed(store)
    fake = {
        "BIGCAP": {"mktcap_native": 5e8, "currency": "USD", "is_live": True},
        "SMALL": {"mktcap_native": 5e6, "currency": "USD", "is_live": True},   # below $10M
        "DEAD": None,                                                          # no data → miss
        "CADCO": {"mktcap_native": 20e6, "currency": "CAD", "is_live": True},  # 20M CAD ≈ 14.6M USD
    }
    res = enrich_caps(store, cfg, per_sec=0, fetch=lambda t: fake.get(t))
    assert res.attempted == 4
    assert res.filled == 3 and res.misses == 1
    assert res.below_floor == 1                                   # only SMALL

    big = store.get_entity("cik:1")
    assert big.market_cap_usd == 5e8 and big.mktcap_unknown is False and big.below_floor is False
    small = store.get_entity("cik:2")
    assert small.below_floor is True
    cad = store.get_entity("cik:4")
    assert cad.mktcap_ccy == "CAD" and 1.4e7 < cad.market_cap_usd < 1.5e7   # FX-converted to USD
    dead = store.get_entity("cik:3")
    assert dead.mktcap_unknown is True and dead.enriched_at is not None      # kept, stamped


def test_enrich_is_resumable_skips_already_capped(store, cfg):
    _seed(store)
    enrich_caps(store, cfg, per_sec=0, fetch=lambda t: {"mktcap_native": 5e8, "currency": "USD"})
    # second pass: only the misses (none here) remain — everything now has a cap
    res2 = enrich_caps(store, cfg, per_sec=0, fetch=lambda t: pytest.fail("should not refetch"))
    assert res2.attempted == 0


def test_enrich_limit(store, cfg):
    _seed(store)
    res = enrich_caps(store, cfg, per_sec=0, limit=2, fetch=lambda t: {"mktcap_native": 5e8, "currency": "USD"})
    assert res.attempted == 2
