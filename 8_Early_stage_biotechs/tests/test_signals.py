"""M7 — capital-markets signal ingestion offline tests (injected fetch/payload, no network)."""

from __future__ import annotations

import pytest

from early_detection.clients import edgar_signals
from early_detection.config import Config
from early_detection.models import Entity
from early_detection.signals.capital_markets import ingest_capital_markets
from early_detection.store import Store

# submissions-shaped payload (parallel arrays)
_SUBMISSIONS = {
    "filings": {"recent": {
        "form":            ["SC 13D", "8-K", "10-K", "4", "424B5"],
        "filingDate":      ["2026-07-01", "2026-06-15", "2026-05-01", "2026-07-05", "2020-01-01"],
        "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3", "0001-26-4", "0001-20-9"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm", "e.htm"],
    }}
}


def test_parse_recent_filings_zips_arrays():
    out = edgar_signals.parse_recent_filings(_SUBMISSIONS)
    assert len(out) == 5
    assert out[0] == {"form": "SC 13D", "date": "2026-07-01",
                      "accession": "0001-26-1", "primary_doc": "a.htm"}


def test_recent_material_filings_filters_form_and_window(monkeypatch):
    monkeypatch.setattr(edgar_signals._net, "safe_json_retry", lambda url, **kw: _SUBMISSIONS)
    material = {"SC 13D", "8-K", "4", "424B5"}     # note: 10-K excluded
    got = edgar_signals.recent_material_filings("0001", material_forms=material,
                                                lookback_days=180, today="2026-07-10")
    forms = sorted(f["form"] for f in got)
    # SC 13D (7-01), 8-K (6-15), 4 (7-05) are material + in-window; 10-K excluded (not material);
    # 424B5 excluded (2020, outside 180d)
    assert forms == ["4", "8-K", "SC 13D"]


def test_recent_material_filings_matches_fpi_and_modernized_forms(monkeypatch):
    # Regression: cross-listed FPIs (e.g. Satellos) file 6-K / Form-D / F-10 / SCHEDULE 13G, and SEC's
    # modernized ownership labels are SCHEDULE 13x. All must be captured by the default material_forms.
    fpi = {"filings": {"recent": {
        "form":       ["6-K", "D", "F-10", "SCHEDULE 13G", "40-F"],
        "filingDate": ["2026-07-08", "2026-06-20", "2026-06-01", "2026-05-15", "2026-04-01"],
        "accessionNumber": ["a1", "a2", "a3", "a4", "a5"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm", "e.htm"]}}}
    monkeypatch.setattr(edgar_signals._net, "safe_json_retry", lambda url, **kw: fpi)
    material = set(Config().material_forms)
    got = {f["form"] for f in edgar_signals.recent_material_filings(
        "0001421642", material_forms=material, lookback_days=180, today="2026-07-10")}
    assert {"6-K", "D", "F-10", "SCHEDULE 13G"} <= got     # FPI + modernized labels captured
    assert "40-F" not in got                                # routine annual deliberately excluded


def test_recent_material_filings_bad_cik_or_empty(monkeypatch):
    monkeypatch.setattr(edgar_signals._net, "safe_json_retry", lambda url, **kw: None)
    assert edgar_signals.recent_material_filings("0001", material_forms={"8-K"}, lookback_days=90) == []
    assert edgar_signals.recent_material_filings(None, material_forms={"8-K"}, lookback_days=90) == []


# ── end-to-end ingester ───────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "s.db")


def _seed(store):
    # active (cik, above floor), below-floor (skipped), no-cik (skipped)
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Active Bio", cik="0000000001",
                               jurisdiction="US"))
    store.upsert_entity(Entity(entity_id="cik:2", legal_name="Tiny Bio", cik="0000000002",
                               jurisdiction="US", below_floor=True))
    store.upsert_entity(Entity(entity_id="tkc:X|JP", legal_name="No Cik Co", jurisdiction="JP"))


def test_ingest_writes_signals_for_active_only(store, cfg):
    _seed(store)
    fake = {"0000000001": [
        {"form": "SC 13D", "date": "2026-07-01", "accession": "A1", "primary_doc": "a.htm"},
        {"form": "8-K", "date": "2026-06-01", "accession": "A2", "primary_doc": "b.htm"},
    ]}
    res = ingest_capital_markets(store, cfg, concurrency=2, fetch=lambda cik: fake.get(cik, []))
    assert res.entities == 1                      # only the active-with-cik entity
    assert res.signals == 2 and res.with_filings == 1
    assert res.by_form == {"SC 13D": 1, "8-K": 1}
    sigs = store.signals_for("cik:1")
    assert {s["raw_payload"]["form"] for s in sigs} == {"SC 13D", "8-K"}
    assert all(s["signal_type"] == "capital_markets" for s in sigs)


def test_ingest_is_idempotent(store, cfg):
    _seed(store)
    fake = {"0000000001": [{"form": "SC 13D", "date": "2026-07-01", "accession": "A1", "primary_doc": "a.htm"}]}
    ingest_capital_markets(store, cfg, concurrency=1, fetch=lambda cik: fake.get(cik, []))
    ingest_capital_markets(store, cfg, concurrency=1, fetch=lambda cik: fake.get(cik, []))
    assert store.count_signals("capital_markets") == 1     # stable signal_id → upsert, no dup


def test_entities_for_signals_excludes_belowfloor_and_nocik(store, cfg):
    _seed(store)
    ids = {e.entity_id for e in store.entities_for_signals()}
    assert ids == {"cik:1"}
