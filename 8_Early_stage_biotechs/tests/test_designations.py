"""M17 — regulatory-designation signal (§3.4) offline tests (injected efts search; no network)."""

from __future__ import annotations

import pytest

from early_detection.config import Config
from early_detection.models import Entity
from early_detection.signals.designations import ingest_designations
from early_detection.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "d.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "d.db",
                  designation_phrases={"Breakthrough Therapy Designation": "breakthrough",
                                       "Orphan Drug Designation": "orphan"})


def _seed(store):
    # cik:1 is in the universe; cik:9 is NOT (a filing on it must be ignored)
    store.upsert_entity(Entity(entity_id="cik:0000000001", legal_name="Uni Bio", ticker_primary="UNI",
                               cik="0000000001", jurisdiction="US"))


def _hit(cik, adsh, form="8-K"):
    return {"form": form, "adsh": adsh, "file_date": "2025-06-01",
            "ciks": [cik], "display_names": [f"Co ({cik})"]}


def test_matches_universe_cik_and_tags_type(store, cfg):
    _seed(store)
    def search(phrase):
        if phrase == "Breakthrough Therapy Designation":
            return [_hit("0000000001", "A1"), _hit("0000000009", "A2")]   # A2 not in universe
        return [_hit("0000000001", "A3")]                                 # orphan
    res = ingest_designations(store, cfg, search=search)
    assert res.signals == 2 and res.by_type == {"breakthrough": 1, "orphan": 1}
    types = {s["raw_payload"]["designation"] for s in store.signals_for("cik:0000000001")}
    assert types == {"breakthrough", "orphan"}


def test_idempotent_and_evidence_aggregate(store, cfg):
    _seed(store)
    search = lambda phrase: [_hit("0000000001", "A1")] if "Breakthrough" in phrase else [_hit("0000000001", "A2")]
    ingest_designations(store, cfg, search=search)
    ingest_designations(store, cfg, search=search)                        # re-run → stable ids
    assert store.count_signals("regulatory_designation") == 2
    des = store.evidence_summary("cik:0000000001")["regulatory_designations"]
    assert des["count"] == 2 and des["types"] == ["breakthrough", "orphan"]


def test_no_universe_match_writes_nothing(store, cfg):
    _seed(store)
    res = ingest_designations(store, cfg, search=lambda phrase: [_hit("0000000099", "Z1")])
    assert res.signals == 0 and store.count_signals("regulatory_designation") == 0
