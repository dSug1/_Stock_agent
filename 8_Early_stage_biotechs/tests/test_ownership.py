"""M8 — ownership-crossing signal (EDGAR full-text) offline tests (injected search, no network)."""

from __future__ import annotations

import pytest

from early_detection.clients import edgar_fts
from early_detection.config import Config
from early_detection.models import Entity
from early_detection.signals.ownership import ingest_ownership
from early_detection.store import Store

# efts-shaped payload (one 13D by RA Capital on our universe co, one on a non-universe co)
_EFTS = {
    "hits": {"total": {"value": 2}, "hits": [
        {"_id": "0001144204-26-000001:doc.htm", "_source": {
            "file_type": "SC 13D", "root_forms": ["SC 13D"], "file_date": "2026-06-01",
            "ciks": ["0000000001", "0001346824"],
            "display_names": ["Active Bio, Inc.  (CIK 0000000001)",
                              "RA CAPITAL MANAGEMENT, LLC  (CIK 0001346824)"]}},
        {"_id": "0001144204-26-000002:doc.htm", "_source": {
            "file_type": "SC 13G", "root_forms": ["SC 13G"], "file_date": "2026-06-10",
            "ciks": ["0009999999", "0001346824"],   # subject not in our universe
            "display_names": ["Some Other Co  (CIK 0009999999)",
                              "RA CAPITAL MANAGEMENT, LLC  (CIK 0001346824)"]}},
    ]}
}


def test_parse_hits():
    hits = edgar_fts.parse_hits(_EFTS)
    assert len(hits) == 2
    assert hits[0]["adsh"] == "0001144204-26-000001"
    assert hits[0]["form"] == "SC 13D"
    assert hits[0]["ciks"] == ["0000000001", "0001346824"]
    assert edgar_fts.total_hits(_EFTS) == 2


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "o.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "o.db", specialist_funds=("RA Capital",))


def _seed(store):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Active Bio", cik="0000000001",
                               jurisdiction="US"))
    store.upsert_entity(Entity(entity_id="cik:2", legal_name="Below Floor Co", cik="0000000002",
                               jurisdiction="US", below_floor=True))


def test_ingest_matches_subject_cik_and_attributes_fund(store, cfg):
    _seed(store)
    res = ingest_ownership(store, cfg, search=lambda q: edgar_fts.parse_hits(_EFTS))
    assert res.signals == 1                       # only the universe-matched 13D (cik 1); other subject not in universe
    assert res.by_fund == {"RA Capital": 1}
    sig = store.signals_for("cik:1")[0]
    assert sig["signal_type"] == "ownership_crossing"
    assert sig["raw_payload"]["fund"] == "RA Capital"
    assert sig["raw_payload"]["form"] == "SC 13D"
    assert sig["event_date"] == "2026-06-01"


def test_requires_fund_in_display_names(store, cfg):
    _seed(store)
    # a hit that involves our universe cik but does NOT name the fund → rejected (stray body mention)
    payload = {"hits": {"hits": [{"_id": "x:doc", "_source": {
        "file_type": "SC 13D", "root_forms": ["SC 13D"], "file_date": "2026-06-01",
        "ciks": ["0000000001"], "display_names": ["Active Bio, Inc.  (CIK 0000000001)"]}}]}}
    res = ingest_ownership(store, cfg, search=lambda q: edgar_fts.parse_hits(payload))
    assert res.signals == 0


def test_below_floor_cik_not_matched(store, cfg):
    _seed(store)
    payload = {"hits": {"hits": [{"_id": "y:doc", "_source": {
        "file_type": "SC 13D", "root_forms": ["SC 13D"], "file_date": "2026-06-01",
        "ciks": ["0000000002", "0001346824"],
        "display_names": ["Below Floor Co  (CIK 0000000002)", "RA CAPITAL MANAGEMENT, LLC"]}}]}}
    res = ingest_ownership(store, cfg, search=lambda q: edgar_fts.parse_hits(payload))
    assert res.signals == 0                        # below-floor entity excluded from active map


def test_ingest_idempotent(store, cfg):
    _seed(store)
    s = lambda q: edgar_fts.parse_hits(_EFTS)
    ingest_ownership(store, cfg, search=s)
    ingest_ownership(store, cfg, search=s)
    assert store.count_signals("ownership_crossing") == 1   # stable signal_id → upsert
