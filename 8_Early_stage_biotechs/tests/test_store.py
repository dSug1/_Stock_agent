"""M1 — store/DAO offline tests (no network). Run: PYTHONPATH=src pytest tests/ -q."""

from __future__ import annotations

import sqlite3

import pytest

from early_detection.models import AuditEntry, Entity, ReconRow, SignalRecord
from early_detection.store import SCHEMA_VERSION, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _entity(**kw) -> Entity:
    base = dict(entity_id="cik:0001", legal_name="Acme Bio, Inc.", ticker_primary="ACME",
                exchange_primary="NASDAQ", cik="0000000001", jurisdiction="US",
                sector_code_normalized="therapeutics")
    base.update(kw)
    return Entity(**base)


def test_migration_sets_schema_version_and_tables(store):
    assert store.user_version == SCHEMA_VERSION == 2
    names = {r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"entity", "listing", "signal", "reconciliation_queue", "audit_log", "run_meta"} <= names
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(entity)")}
    assert {"below_floor", "mktcap_ccy", "ipo_date", "enriched_at"} <= cols   # migration 2


def test_migration_is_idempotent(tmp_path):
    p = tmp_path / "t.db"
    Store(p).close()
    s2 = Store(p)          # reopening applies no migrations, does not error
    assert s2.user_version == 2
    s2.close()


def test_apply_cap_sets_floor_and_clears_unknown(store):
    store.upsert_entity(_entity(mktcap_unknown=True))
    below = store.apply_cap("cik:0001", market_cap_usd=5e6, currency="USD", floor_usd=1e7)
    assert below is True
    got = store.get_entity("cik:0001")
    assert got.market_cap_usd == 5e6 and got.mktcap_unknown is False and got.below_floor is True
    assert got.enriched_at is not None


def test_apply_cap_miss_keeps_unknown_and_not_floored(store):
    store.upsert_entity(_entity(mktcap_unknown=True))
    below = store.apply_cap("cik:0001", market_cap_usd=None, currency=None, floor_usd=1e7)
    assert below is None
    got = store.get_entity("cik:0001")
    assert got.mktcap_unknown is True and got.below_floor is False   # missing ≠ small
    assert got.enriched_at is not None                                # stamped so we don't retry forever


def test_recompute_floors(store):
    store.upsert_entity(_entity(entity_id="cik:0001", market_cap_usd=5e6))
    store.upsert_entity(_entity(entity_id="cik:0002", market_cap_usd=5e8))
    store.upsert_entity(_entity(entity_id="cik:0003", mktcap_unknown=True))
    n = store.recompute_floors(1e7)
    assert n == 1                                              # only the $5M one
    assert store.get_entity("cik:0001").below_floor is True
    assert store.get_entity("cik:0002").below_floor is False
    assert store.get_entity("cik:0003").below_floor is False  # unknown cap not floored


def test_entities_needing_cap(store):
    store.upsert_entity(_entity(entity_id="cik:0001", ticker_primary="A", mktcap_unknown=True))
    store.upsert_entity(_entity(entity_id="cik:0002", ticker_primary="B", market_cap_usd=5e8))
    store.upsert_entity(_entity(entity_id="cik:0003", ticker_primary=None, mktcap_unknown=True))
    need = {e.entity_id for e in store.entities_needing_cap()}
    assert need == {"cik:0001"}   # known-cap B excluded; tickerless C excluded


def test_upsert_and_get_roundtrip(store):
    e = _entity(source_provenance=["edgar_us"])
    store.upsert_entity(e)
    got = store.get_entity("cik:0001")
    assert got is not None
    assert got.legal_name == "Acme Bio, Inc."
    assert got.cik == "0000000001"
    assert got.source_provenance == ["edgar_us"]
    assert got.first_seen is not None and got.last_seen is not None


def test_upsert_preserves_first_seen_and_unions_provenance(store):
    store.upsert_entity(_entity(source_provenance=["edgar_us"]))
    first = store.get_entity("cik:0001").first_seen
    # second write from a different provider must keep first_seen and accumulate provenance
    store.upsert_entity(_entity(source_provenance=["m6"], common_name="Acme"))
    got = store.get_entity("cik:0001")
    assert got.first_seen == first
    assert set(got.source_provenance) == {"edgar_us", "m6"}
    assert got.common_name == "Acme"


def test_upsert_does_not_null_out_existing_fields(store):
    store.upsert_entity(_entity(lei="LEI123", isin="US123"))
    # a later sparse write (no lei/isin) must not clobber previously-known keys
    store.upsert_entity(_entity(lei=None, isin=None, market_cap_usd=5e7))
    got = store.get_entity("cik:0001")
    assert got.lei == "LEI123"
    assert got.isin == "US123"
    assert got.market_cap_usd == 5e7


def test_in_existing_universe_flag_is_sticky(store):
    store.upsert_entity(_entity(in_existing_universe=True))
    store.upsert_entity(_entity(in_existing_universe=False))   # a non-M6 refresh must not clear the tier
    assert store.get_entity("cik:0001").in_existing_universe is True


def test_find_entity_by_key_cascade(store):
    store.upsert_entity(_entity(lei="LEI9", isin="ISIN9"))
    assert store.find_entity_by_key(lei="LEI9").entity_id == "cik:0001"
    assert store.find_entity_by_key(isin="ISIN9").entity_id == "cik:0001"
    assert store.find_entity_by_key(cik="0000000001").entity_id == "cik:0001"
    assert store.find_entity_by_key(ticker="ACME", exchange="NASDAQ").entity_id == "cik:0001"
    assert store.find_entity_by_key(lei="NOPE") is None


def test_listing_upsert_unique(store):
    store.upsert_entity(_entity())
    store.add_listing("cik:0001", ticker="ACME", exchange="NASDAQ", is_primary=True, provenance=["edgar_us"])
    store.add_listing("cik:0001", ticker="ACME", exchange="NASDAQ", country="US")  # same key → update
    store.add_listing("cik:0001", ticker="ACM", exchange="TSX")                     # dual listing → new row
    rows = store.listings_for("cik:0001")
    assert len(rows) == 2
    primary = [r for r in rows if r["ticker"] == "ACME"][0]
    assert primary["country"] == "US"


def test_reconciliation_queue(store):
    rid = store.queue_recon(ReconRow(candidate={"name": "Ambiguous Bio"}, reason="no_key_match"))
    assert isinstance(rid, int)
    q = store.recon_queue()
    assert len(q) == 1
    assert q[0]["reason"] == "no_key_match"
    assert q[0]["candidate"] == {"name": "Ambiguous Bio"}
    assert store.count_recon() == 1


def test_signal_insert_ready_for_phase2(store):
    store.insert_signal(SignalRecord(signal_id="s1", signal_type="literature", source="openalex",
                                     entity_id=None, raw_payload={"k": "v"}))
    row = store.conn.execute("SELECT * FROM signal WHERE signal_id='s1'").fetchone()
    assert row["signal_type"] == "literature"
    assert row["entity_id"] is None   # nullable — signal may precede entity match


def test_audit_and_run_meta(store):
    store.log(stage="universe", action="admitted", entity_id="cik:0001", reason="edgar_us")
    n = store.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert n == 1
    store.record_run(run_id="r1", started="t0", finished="t1", market="US",
                     counts={"admitted": 1}, config_hash="h")
    row = store.conn.execute("SELECT * FROM run_meta WHERE run_id='r1'").fetchone()
    assert row["market"] == "US"
