"""M9 — founder-lineage extraction offline tests (fake Claude client; NO real API/spend)."""

from __future__ import annotations

import pytest

from early_detection import extraction
from early_detection.config import Config
from early_detection.extraction import SCHEMA, SYSTEM_PROMPT, extract_founders
from early_detection.models import Entity
from early_detection.store import Store


class FakeClient:
    """Stand-in for AnthropicClient — returns canned results, records calls, never hits the network."""

    def __init__(self, results: dict, *, fail_realtime: bool = False):
        self.results = results          # {entity_id: data}
        self.spent_usd = 0.0
        self.web_searches = 0
        self.submitted = None
        self.collected = None

    def submit_batch(self, model, system, schema, bundles, max_tokens, *, tools=None):
        self.submitted = dict(bundles)
        return "batch_test_1"

    def collect_batch(self, batch_id, model, *, validate=None, on_result=None, **kw):
        self.collected = batch_id
        out = {}
        for cid in (self.submitted or self.results):
            data = self.results.get(cid)
            if data is None:
                continue
            if validate:
                data = validate(data)
            out[cid] = data
            if on_result:
                on_result(cid, data)
        return out

    def run_realtime_many(self, model, system, schema, items, max_tokens, *, validate=None,
                          tools=None, concurrency=6, on_result=None, **kw):
        out = {}
        for cid in items:
            data = self.results.get(cid)
            if data is None:
                continue
            if validate:
                data = validate(data)
            out[cid] = data
            if on_result:
                on_result(cid, data)
        return out


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "x.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "x.db", extraction_prompt_version="v1", cost_calibration_factor=0.1)


def _seed(store):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Acrivon Therapeutics", ticker_primary="ACRV",
                               jurisdiction="US", in_existing_universe=True))
    store.upsert_entity(Entity(entity_id="cik:2", legal_name="Empty Co", ticker_primary="EMTY",
                               jurisdiction="US"))
    store.upsert_entity(Entity(entity_id="cik:3", legal_name="Below Co", ticker_primary="BLW",
                               jurisdiction="US", below_floor=True))   # excluded (below floor)


def test_schema_is_structured_output_shaped():
    assert SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == {"founders", "academic_affiliations", "confidence", "notes"}
    item = SCHEMA["properties"]["founders"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"name", "role", "institution", "is_company_officer"}


def test_batch_extract_persists_founders_and_flags(store, cfg):
    _seed(store)
    fake = FakeClient({
        "cik:1": {"founders": [{"name": "Peter Blume-Jensen", "role": "CEO/founder",
                                "institution": "Merck KGaA", "is_company_officer": True}],
                  "academic_affiliations": ["Dana-Farber"], "confidence": "high", "notes": ""},
        "cik:2": {"founders": [], "academic_affiliations": [], "confidence": "low", "notes": "none found"},
    })
    res = extract_founders(store, cfg, client=fake)
    assert res.attempted == 2                       # below-floor cik:3 excluded
    assert res.extracted == 2 and res.with_founders == 1 and res.total_founders == 1
    # persisted
    f = store.founders_for("cik:1")
    assert f[0]["name"] == "Peter Blume-Jensen" and f[0]["is_company_officer"] == 1
    got = store.get_entity("cik:1")
    assert got.founder_prompt_version == "v1" and got.academic_affiliations == ["Dana-Farber"]
    # empty result still stamps the version (won't be re-extracted)
    assert store.get_entity("cik:2").founder_prompt_version == "v1"
    assert store.founders_for("cik:2") == []


def test_batch_id_persisted_before_poll(store, cfg):
    _seed(store)
    fake = FakeClient({"cik:1": {"founders": [], "academic_affiliations": [], "confidence": "low", "notes": ""}})
    saved = {}
    extract_founders(store, cfg, client=fake, on_batch_id=lambda b: saved.setdefault("id", b))
    assert saved["id"] == "batch_test_1"            # id captured before collect (crash-resume)
    assert fake.collected == "batch_test_1"


def test_skip_cache_by_prompt_version(store, cfg):
    _seed(store)
    fake = FakeClient({
        "cik:1": {"founders": [{"name": "A", "role": "r", "institution": "i", "is_company_officer": False}],
                  "academic_affiliations": [], "confidence": "high", "notes": ""},
        "cik:2": {"founders": [], "academic_affiliations": [], "confidence": "low", "notes": ""},
    })
    extract_founders(store, cfg, client=fake)
    # second run at the SAME prompt version → nothing to do
    assert len(store.entities_for_extraction("v1")) == 0
    # a prompt bump re-opens everyone
    assert len(store.entities_for_extraction("v2")) == 2


def test_realtime_mode(store, cfg):
    _seed(store)
    fake = FakeClient({"cik:1": {"founders": [{"name": "X", "role": "r", "institution": "i",
                                               "is_company_officer": True}],
                                 "academic_affiliations": [], "confidence": "high", "notes": ""},
                       "cik:2": {"founders": [], "academic_affiliations": [], "confidence": "low", "notes": ""}})
    res = extract_founders(store, cfg, client=fake, use_batch=False)
    assert res.extracted == 2 and res.total_founders == 1


def test_estimate_scales_with_calibration(cfg):
    hi = extraction.estimate_usd(Config(cost_calibration_factor=1.0, extraction_model="claude-haiku-4-5"), 100)
    lo = extraction.estimate_usd(Config(cost_calibration_factor=0.1, extraction_model="claude-haiku-4-5"), 100)
    assert lo == pytest.approx(hi * 0.1)


def test_system_prompt_has_no_invent_discipline():
    assert "NEVER invent" in SYSTEM_PROMPT and "web_search" in SYSTEM_PROMPT
