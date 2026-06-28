"""M3 tests — taxonomy tagging + Stage 1 (reversible, recall-safe, no deletions)."""

from pathlib import Path

import pytest

from platform_discoverer import config as cfg
from platform_discoverer import stage1
from platform_discoverer.models import Company
from platform_discoverer.store import Store
from platform_discoverer.taxonomy import TaxonomyTagger

ROOT = Path(__file__).parent.parent
CONFIG = cfg.load_config(ROOT / "config" / "config.yaml")
TAXONOMY = cfg.load_taxonomy(ROOT / "config" / "taxonomy.yaml")
TAGGER = TaxonomyTagger(TAXONOMY)


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db", config=CONFIG)
    yield s
    s.close()


def _co(cid, desc, name="Co") -> Company:
    return Company(company_id=cid, name=name, primary_ticker=cid.upper(),
                   business_description=desc, source_nets=["sector"])


# ── tagger ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("An allosteric TYK2 inhibitor for psoriasis", "tyk2_jak"),
    ("PARP inhibitor exploiting DNA-damage repair", "adp_ribosylation_parp_tankyrase"),
    ("targeting the menin-KMT2A interaction in leukemia", "menin_kmt2a"),
    ("a TEAD inhibitor blocking the Hippo pathway", "yap_taz_hippo_tead"),
])
def test_tagger_matches_known_mechanisms(text, expected):
    assert expected in TAGGER.tag_ids(text)


def test_tagger_no_match_on_generic_text():
    assert TAGGER.tag_ids("A cloud software company selling subscriptions") == []
    assert TAGGER.tag_ids(None) == []


def test_tagger_reports_terms_and_branch():
    [hit] = [h for h in TAGGER.tag("allosteric TYK2 program") if h["id"] == "tyk2_jak"]
    assert hit["branch"] == "autoimmune" and any("tyk2" in t for t in hit["terms"])


# ── stage 1 ──────────────────────────────────────────────────────────────────

def test_tagged_company_gets_tags_not_excluded(store):
    store.upsert_company(_co("c1", "A PARP inhibitor for ovarian cancer"))
    stage1.run(store, TAGGER, CONFIG, run_id="r")
    c = store.get_company("c1")
    assert "adp_ribosylation_parp_tankyrase" in c.ta_tags
    assert c.stage1_excluded is False
    assert "c1" not in {r["company_id"] for r in store.review_queue_dump()}


def test_untagged_company_kept_excluded_and_reviewed_not_deleted(store):
    store.upsert_company(_co("c2", "A diversified industrial holding company"))
    stage1.run(store, TAGGER, CONFIG, run_id="r")
    c = store.get_company("c2")
    assert c is not None                       # KEPT, never deleted
    assert c.stage1_excluded is True and c.ta_tags == []
    assert "c2" in {r["company_id"] for r in store.review_queue_dump()}
    # the exclusion is logged + reversible
    assert any(a.reason == "no_ta_tag" for a in store.why_excluded("c2"))


def test_no_deletions_in_stage1(store):
    store.upsert_company(_co("c1", "TYK2 inhibitor"))
    store.upsert_company(_co("c2", "industrial holding"))
    stage1.run(store, TAGGER, CONFIG, run_id="r")
    assert store.count_companies() == 2        # both retained
    assert store.conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='deleted'").fetchone()[0] == 0


def test_reversibility_retag_readmits_when_evidence_arrives(store):
    # first pass: no mechanism in text -> excluded
    store.upsert_company(_co("c3", "Stealth-mode biotech, details undisclosed"))
    stage1.run(store, TAGGER, CONFIG, run_id="r1")
    assert store.get_company("c3").stage1_excluded is True
    # later (e.g. M4 evidence) the description reveals the mechanism -> re-tag re-admits it
    c = store.get_company("c3")
    store.upsert_company(Company(company_id="c3", name=c.name, primary_ticker="C3X",
                                 business_description="develops a SUMO-activating enzyme (SAE) inhibitor",
                                 source_nets=["sector"], stage1_excluded=True))
    summary = stage1.run(store, TAGGER, CONFIG, run_id="r2")
    c = store.get_company("c3")
    assert c.stage1_excluded is False and "sumoylation" in c.ta_tags
    assert summary["readmitted"] == 1


def test_stage1_tags_from_harvested_evidence(store):
    # description names no mechanism, but an OpenAlex concept whose term is in the vocab does
    # (note: concepts must use a taxonomy synonym term, e.g. 'ubiquitin' — spelled-out names like
    # 'histone deacetylase' won't match the abbreviation 'hdac'; a known limitation)
    from platform_discoverer.models import Evidence
    store.upsert_company(_co("c5", "A precision oncology company", name="Degrader-like"))
    store.upsert_evidence(Evidence(company_id="c5", source="openalex",
                                   payload={"top_concepts": ["Ubiquitin", "Proteolysis"]}))
    stage1.run(store, TAGGER, CONFIG, run_id="r")
    c = store.get_company("c5")
    assert "ubiquitination_degradation" in c.ta_tags and c.stage1_excluded is False


def test_require_ta_tag_false_does_not_exclude(store):
    cfg_lax = {**CONFIG, "stage1_filters": {**CONFIG["stage1_filters"], "require_ta_tag": False}}
    store.upsert_company(_co("c4", "industrial holding"))
    stage1.run(store, TAGGER, cfg_lax, run_id="r")
    assert store.get_company("c4").stage1_excluded is False   # flagged for review, not excluded
