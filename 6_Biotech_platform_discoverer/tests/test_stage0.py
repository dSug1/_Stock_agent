"""M2 tests — Stage 0a union-of-nets + Stage 0b hard cuts.

The recall assertion (``test_all_seed_positives_survive_stage0``) is the spec's intent made
executable: if Stage 0 ever drops a known positive, that's a spec-level failure to fix before
trusting the run (§13). Stage 0b is also where we re-prove the cardinal rule end-to-end: missing cap
is kept+flagged, and the only deletions carry an allowed reason.
"""

from pathlib import Path

import pytest

from platform_discoverer import config as cfg
from platform_discoverer import stage0a, stage0b
from platform_discoverer.listings import SeedCSVProvider
from platform_discoverer.models import ListingRecord
from platform_discoverer.store import IllegalDeletionError, Store

ROOT = Path(__file__).parent.parent
# Synthetic fixture with in-band caps + edge cases — deterministic, independent of live market caps
# (which drift daily). The real-data band tension is surfaced in the report, not asserted here.
CSV = Path(__file__).parent / "fixtures" / "universe_fixture.csv"
CONFIG = cfg.load_config(ROOT / "config" / "config.yaml")

SEED_POSITIVES = ["ACRV", "TNGX", "IDYA", "BOLD", "RXRX", "SDGR", "RLAY"]


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db", config=CONFIG)
    yield s
    s.close()


def _seed(store) -> dict:
    recs = SeedCSVProvider([CSV]).fetch()
    return stage0a.run(store, recs, CONFIG, run_id="r1")


# ── Stage 0a: union of nets ──────────────────────────────────────────────────

def test_match_nets_union_any_hit_passes():
    nets = CONFIG["stage0a_nets"]
    # sector-only (mis-coded name, no keyword, no seed provenance)
    sector_only = ListingRecord(name="Zeta Holdings", sic="2836", provenance=[])
    assert stage0a.match_nets(sector_only, nets) == ["sector"]
    # name-keyword-only
    kw_only = ListingRecord(name="Cure Oncology Corp", sic="9999", provenance=[])
    assert "name_keyword" in stage0a.match_nets(kw_only, nets)
    # nothing hits
    miss = ListingRecord(name="Zeta Holdings", sic="9999", provenance=[])
    assert stage0a.match_nets(miss, nets) == []


def test_stage0a_admits_all_seed_members_and_dedups(store):
    summary = _seed(store)
    assert summary["admitted"] == 14          # 15 rows, DUAL pair collapsed
    assert store.count_companies() == 14
    # a mis-coded name (Schrodinger: sic=software, no keyword) still admitted via seed_list net
    sdgr = next(c for c in store.all_companies() if c.primary_ticker == "SDGR")
    assert "seed_list" in sdgr.source_nets


# ── Stage 0b: the only deletions ─────────────────────────────────────────────

def test_stage0b_hard_cuts_counts(store):
    _seed(store)
    summary = stage0b.run(store, CONFIG, run_id="r1")
    assert summary["deleted_not_live"] == 1            # DEADX
    assert summary["deleted_mktcap_out_of_band"] == 2  # MEGA (too big), TINY (too small)
    assert summary["flagged_mktcap_unknown"] == 1      # UNKN
    assert store.count_companies() == 11               # 14 - 3 deleted


def test_near_band_tolerance_routes_to_review_queue_not_delete(store):
    from platform_discoverer.models import Company
    # config near_band_tolerance = 0.15 over [50M, 3B]
    store.upsert_company(Company("over", "Ideaya-like", primary_ticker="OVR",
                                 mktcap_usd_fd=3.2e9, source_nets=["seed_list"]))   # 6.7% over -> review
    store.upsert_company(Company("wayover", "Tango-like", primary_ticker="WAY",
                                 mktcap_usd_fd=5.12e9, source_nets=["seed_list"]))  # 70% over -> delete
    store.upsert_company(Company("nearfloor", "Edge", primary_ticker="EDG",
                                 mktcap_usd_fd=45e6, source_nets=["seed_list"]))    # 10% under -> review
    store.upsert_company(Company("wayunder", "Micro", primary_ticker="MIC",
                                 mktcap_usd_fd=32.13e6, source_nets=["seed_list"]))  # 36% under -> delete
    summary = stage0b.run(store, CONFIG, run_id="r1")

    survivors = {c.primary_ticker for c in store.all_companies()}
    assert {"OVR", "EDG"} <= survivors            # near-band kept, not deleted
    assert "WAY" not in survivors and "MIC" not in survivors   # beyond tolerance -> deleted
    rq = {r["company_id"] for r in store.review_queue_dump()}
    assert {"over", "nearfloor"} <= rq
    assert summary["near_band_review"] == 2
    assert summary["deleted_mktcap_out_of_band"] == 2


def test_all_seed_positives_survive_stage0(store):
    _seed(store)
    stage0b.run(store, CONFIG, run_id="r1")
    survivors = {c.primary_ticker for c in store.all_companies()}
    missing = [t for t in SEED_POSITIVES if t not in survivors]
    assert not missing, f"Stage 0 lost known positives: {missing}"


def test_missing_cap_company_kept_and_flagged(store):
    _seed(store)
    stage0b.run(store, CONFIG, run_id="r1")
    unkn = next(c for c in store.all_companies() if c.primary_ticker == "UNKN")
    assert unkn.mktcap_unknown is True                 # kept + flagged, NOT deleted


def test_stage0b_deletions_all_carry_allowed_reason(store):
    _seed(store)
    stage0b.run(store, CONFIG, run_id="r1")
    reasons = {r["reason"] for r in store.conn.execute(
        "SELECT DISTINCT reason FROM audit_log WHERE action='deleted'").fetchall()}
    assert reasons <= {"mktcap_out_of_band", "not_live"}   # cardinal rule, end-to-end


def test_enricher_fills_unknown_cap_then_band_applies(store):
    _seed(store)

    def enricher(company):
        if company.primary_ticker == "UNKN":
            return {"mktcap_usd_fd": 500_000_000, "is_live": True}   # now in-band
        return None

    stage0b.run(store, CONFIG, run_id="r1", enricher=enricher)
    unkn = next((c for c in store.all_companies() if c.primary_ticker == "UNKN"), None)
    assert unkn is not None and unkn.mktcap_usd_fd == 500_000_000
    assert unkn.mktcap_unknown is False


def test_enricher_marking_dead_triggers_not_live_delete(store):
    _seed(store)
    acrv_id = next(c.company_id for c in store.all_companies() if c.primary_ticker == "ACRV")

    def enricher(company):
        if company.primary_ticker == "ACRV":
            return {"is_live": False}
        return None

    stage0b.run(store, CONFIG, run_id="r1", enricher=enricher)
    assert not any(c.primary_ticker == "ACRV" for c in store.all_companies())
    row = store.conn.execute(
        "SELECT reason FROM audit_log WHERE action='deleted' AND company_id=?",
        (acrv_id,)).fetchone()
    assert row["reason"] == "not_live"


def test_guardrail_still_blocks_illegal_delete_after_stage0(store):
    _seed(store)
    stage0b.run(store, CONFIG, run_id="r1")
    victim = store.all_companies()[0].company_id
    with pytest.raises(IllegalDeletionError):
        store.delete_company(victim, reason="low_score")
