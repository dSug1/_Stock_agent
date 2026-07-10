"""M6 — GLEIF LEI enrich offline tests (injected search, no network)."""

from __future__ import annotations

import pytest

from early_detection.clients import gleif
from early_detection.config import Config
from early_detection.enrich import enrich_lei
from early_detection.models import Entity
from early_detection.store import Store


# ── search payload parsing ────────────────────────────────────────────────────
_PAYLOAD = {
    "data": [
        {"id": "LEI123ACME", "attributes": {
            "entity": {"legalName": {"name": "Acme Bio, Inc."},
                       "legalAddress": {"country": "US"}},
            "registration": {"status": "ISSUED"}}},
        {"id": "LEI999OTHER", "attributes": {
            "entity": {"legalName": {"name": "Acme Bio Holdings LLC"},
                       "legalAddress": {"country": "US"}},
            "registration": {"status": "LAPSED"}}},
    ]
}


def test_search_lei_parses_jsonapi(monkeypatch):
    monkeypatch.setattr(gleif, "_get", lambda url, **kw: _PAYLOAD)
    out = gleif.search_lei("Acme Bio", country="US")
    assert {c["lei"] for c in out} == {"LEI123ACME", "LEI999OTHER"}
    assert out[0]["status"] == "ISSUED"


def test_search_maps_uk_to_gb(monkeypatch):
    seen = {}
    def fake(url, **kw):
        seen["url"] = url
        return {"data": []}
    monkeypatch.setattr(gleif, "_get", fake)
    gleif.search_lei("Foo", country="UK")
    assert "country]=GB" in seen["url"]


# ── pick_lei precision ────────────────────────────────────────────────────────
def test_pick_lei_unique_exact_match():
    cands = [{"lei": "L1", "legal_name": "Acme Bio, Inc.", "status": "ISSUED"}]
    assert gleif.pick_lei("Acme Bio", cands) == "L1"           # normalized names match


def test_pick_lei_rejects_no_exact_match():
    cands = [{"lei": "L1", "legal_name": "Totally Different Corp", "status": "ISSUED"}]
    assert gleif.pick_lei("Acme Bio", cands) is None


def test_pick_lei_ambiguous_returns_none():
    cands = [{"lei": "L1", "legal_name": "Acme Bio", "status": "LAPSED"},
             {"lei": "L2", "legal_name": "Acme Bio", "status": "LAPSED"}]
    assert gleif.pick_lei("Acme Bio", cands) is None           # two LEIs, neither uniquely ISSUED


def test_pick_lei_breaks_tie_on_issued():
    cands = [{"lei": "L1", "legal_name": "Acme Bio", "status": "LAPSED"},
             {"lei": "L2", "legal_name": "Acme Bio", "status": "ISSUED"}]
    assert gleif.pick_lei("Acme Bio", cands) == "L2"


# ── enrich_lei end-to-end (injected search) ───────────────────────────────────
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "l.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "l.db")


def test_enrich_lei_backfills_and_orders_priority(store, cfg):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Acme Bio", ticker_primary="ACME",
                               jurisdiction="US"))
    store.upsert_entity(Entity(entity_id="tkc:X|JP", legal_name="Nippon Tx", ticker_primary="X",
                               jurisdiction="JP", in_existing_universe=True))
    fake = {
        "Acme Bio": [{"lei": "LEI_ACME", "legal_name": "Acme Bio", "status": "ISSUED"}],
        "Nippon Tx": [{"lei": "LEI_NIP", "legal_name": "Nippon Tx", "status": "ISSUED"}],
    }
    res = enrich_lei(store, cfg, concurrency=2, search=lambda name, **kw: fake.get(name, []))
    assert res.attempted == 2 and res.filled == 2 and res.misses == 0
    assert store.get_entity("cik:1").lei == "LEI_ACME"
    assert store.get_entity("tkc:X|JP").lei == "LEI_NIP"


def test_enrich_lei_collision_is_queued_not_set(store, cfg):
    # entity A already holds LEI_DUP; GLEIF returns the same LEI for a different entity B → queue
    store.upsert_entity(Entity(entity_id="lei:LEI_DUP", legal_name="Acme Bio", lei="LEI_DUP"))
    store.upsert_entity(Entity(entity_id="cik:2", legal_name="Acme Bio", ticker_primary="ACME",
                               jurisdiction="US"))
    res = enrich_lei(store, cfg, concurrency=2,
                     search=lambda name, **kw: [{"lei": "LEI_DUP", "legal_name": "Acme Bio",
                                                 "status": "ISSUED"}])
    assert res.collisions == 1 and res.filled == 0
    assert store.get_entity("cik:2").lei is None                # NOT set
    q = store.recon_queue()
    assert any(r["reason"] == "gleif_lei_collision" for r in q)


def test_enrich_lei_miss_leaves_null(store, cfg):
    store.upsert_entity(Entity(entity_id="cik:3", legal_name="Obscure Co", jurisdiction="US"))
    res = enrich_lei(store, cfg, concurrency=2, search=lambda name, **kw: [])
    assert res.misses == 1 and res.filled == 0
    assert store.get_entity("cik:3").lei is None
