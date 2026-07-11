"""M12 — cold-discovery prioritization + two-way export (§2.4) offline tests."""

from __future__ import annotations

import csv

import pytest

from early_detection.config import Config
from early_detection.models import Entity
from early_detection.scoring import write_watchlist
from early_detection.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "c.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "c.db", scoring_prompt_version="v1")


def test_cold_first_ordering(store):
    store.upsert_entity(Entity(entity_id="cik:m6", legal_name="M6 Co", jurisdiction="US",
                               in_existing_universe=True))
    store.upsert_entity(Entity(entity_id="cik:cold", legal_name="Cold Co", jurisdiction="US",
                               in_existing_universe=False))
    default = [e.entity_id for e in store.entities_for_extraction("v1")]
    cold = [e.entity_id for e in store.entities_for_extraction("v1", cold_first=True)]
    assert default[0] == "cik:m6"       # default: M6 priority tier first
    assert cold[0] == "cik:cold"        # cold_first: under-recognized names first


def test_write_watchlist_exports_scored_candidates(store, cfg, tmp_path):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Deep Co", ticker_primary="DEEP",
                               exchange_primary="NASDAQ", jurisdiction="US", in_existing_universe=False))
    store.upsert_entity(Entity(entity_id="cik:2", legal_name="Surveil Co", ticker_primary="SURV",
                               exchange_primary="NYSE", jurisdiction="US"))
    store.upsert_entity(Entity(entity_id="cik:3", legal_name="Depri Co", ticker_primary="DEPR",
                               jurisdiction="US"))
    store.save_score(entity_id="cik:1", run_id="r", model="m", conviction_flag="deep-dive-candidate",
                     conviction_score=82, data={"independent_validation_status": "multi-lab-independent"},
                     prompt_version="v1")
    store.save_score(entity_id="cik:2", run_id="r", model="m", conviction_flag="surveil",
                     conviction_score=55, data={"independent_validation_status": "none"}, prompt_version="v1")
    store.save_score(entity_id="cik:3", run_id="r", model="m", conviction_flag="deprioritize",
                     conviction_score=20, data={}, prompt_version="v1")

    out = tmp_path / "watchlist.csv"
    n = write_watchlist(store, cfg, out)
    assert n == 2                        # deprioritize excluded

    with open(out, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["ticker"] for r in rows] == ["DEEP", "SURV"]     # deep-dive first
    assert rows[0]["conviction_flag"] == "deep-dive-candidate"
    assert rows[0]["exchange"] == "NASDAQ"
    assert rows[0]["in_existing_universe"] == "0"
    assert rows[0]["independent_validation"] == "multi-lab-independent"
