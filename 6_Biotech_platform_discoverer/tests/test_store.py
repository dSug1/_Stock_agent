"""Milestone 1 tests — the cardinal rule (spec §0.2) and the store DAO.

The load-bearing test is ``test_illegal_delete_raises_and_preserves_row``: it proves Stage 0 cannot
lose a candidate for any reason other than the two allowed ones, and that a refused delete leaves no
trace of removal. Everything else in the pipeline scores companies it has already seen, so this is
the one boundary where a false negative would be invisible.
"""

import pytest

from platform_discoverer import config as cfg
from platform_discoverer.models import Company, Evidence, Score
from platform_discoverer.store import IllegalDeletionError, Store


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db")
    yield s
    s.close()


def _co(company_id="c1", name="Acrivon Therapeutics", **kw) -> Company:
    return Company(company_id=company_id, name=name, **kw)


# ── Schema / migrations ─────────────────────────────────────────────────────

def test_schema_version_set(store):
    assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 4


def test_all_seven_tables_exist(store):
    names = {r["name"] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "evidence", "scores", "audit_log",
            "review_queue", "seed_labels", "run_meta"} <= names


def test_migrations_idempotent(tmp_path):
    path = tmp_path / "store.db"
    Store.open(path).close()
    s2 = Store.open(path)            # reopening must not re-run or error
    assert s2.conn.execute("PRAGMA user_version").fetchone()[0] == 4
    s2.close()


# ── The cardinal rule ───────────────────────────────────────────────────────

def test_illegal_delete_raises_and_preserves_row(store):
    store.upsert_company(_co())
    with pytest.raises(IllegalDeletionError):
        store.delete_company("c1", reason="no_ta_tag")          # not an allowed reason
    # row survives ...
    assert store.get_company("c1") is not None
    # ... and NO 'deleted' audit row was written
    deleted = store.conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='deleted'").fetchone()[0]
    assert deleted == 0


@pytest.mark.parametrize("reason", ["mktcap_out_of_band", "not_live"])
def test_allowed_deletions_succeed_and_audit(store, reason):
    store.upsert_company(_co())
    store.delete_company("c1", reason=reason)
    assert store.get_company("c1") is None
    row = store.conn.execute(
        "SELECT action, reason FROM audit_log WHERE action='deleted'").fetchone()
    assert row["action"] == "deleted" and row["reason"] == reason


def test_config_can_narrow_but_not_widen_allowed_reasons(tmp_path):
    # config tries to ADD an illegal reason; it must be intersected away
    bad_cfg = {"stage1_filters": {"deletion_allowed_reasons":
                                  ["mktcap_out_of_band", "no_ta_tag", "low_score"]}}
    s = Store.open(tmp_path / "store.db", config=bad_cfg)
    assert s.allowed_delete_reasons == frozenset({"mktcap_out_of_band"})
    s.upsert_company(_co())
    with pytest.raises(IllegalDeletionError):
        s.delete_company("c1", reason="no_ta_tag")
    s.close()


def test_missing_config_falls_back_to_cardinal_set(store):
    assert store.allowed_delete_reasons == frozenset({"mktcap_out_of_band", "not_live"})


# ── Missing data is KEPT + flagged, never dropped (spec §0.2) ────────────────

def test_missing_cap_kept_and_flagged(store):
    store.upsert_company(_co(mktcap_usd_fd=None, mktcap_unknown=True))
    c = store.get_company("c1")
    assert c is not None and c.mktcap_unknown is True and c.mktcap_usd_fd is None


def test_flag_not_live_sets_flag_without_deleting(store):
    store.upsert_company(_co())
    store.flag_company("c1", "not_live")
    c = store.get_company("c1")
    assert c is not None            # flagged, NOT deleted
    assert c.is_live is False


# ── Stage-1 exclusion is reversible (row survives) ───────────────────────────

def test_stage1_exclusion_is_reversible(store):
    store.upsert_company(_co())
    store.exclude_stage1("c1", reason="no_ta_tag")
    c = store.get_company("c1")
    assert c is not None and c.stage1_excluded is True
    audits = store.why_excluded("c1")
    assert any(a.action == "excluded" and a.reason == "no_ta_tag" for a in audits)


# ── Round-trips ──────────────────────────────────────────────────────────────

def test_company_json_columns_round_trip(store):
    store.upsert_company(_co(source_nets=["sic", "name_keywords"],
                             ta_tags=["yap_taz_hippo_tead", "tyk2_jak"]))
    c = store.get_company("c1")
    assert c.source_nets == ["sic", "name_keywords"]
    assert c.ta_tags == ["yap_taz_hippo_tead", "tyk2_jak"]


def test_upsert_preserves_first_seen(store):
    store.upsert_company(_co())
    first = store.get_company("c1").first_seen
    store.upsert_company(_co(name="Acrivon Therapeutics Inc"))   # update
    again = store.get_company("c1")
    assert again.name == "Acrivon Therapeutics Inc"
    assert again.first_seen == first        # preserved across updates


def test_evidence_cursor_round_trip(store):
    store.upsert_company(_co())
    store.upsert_evidence(Evidence(company_id="c1", source="openalex", cursor="2025-01-01",
                                   payload={"works": 42}))
    assert store.get_cursor("c1", "openalex") == "2025-01-01"


def test_score_round_trip_and_audit(store):
    store.upsert_company(_co())
    store.record_score(Score(company_id="c1", run_id="r1", model="claude-opus-4-8",
                             A=0.9, B=0.6, C=0.95, D=0.8, E=0.9, composite=0.86, confidence=0.7,
                             json={"memo": "platform"}))
    row = store.conn.execute(
        "SELECT composite FROM scores WHERE company_id='c1' AND run_id='r1'").fetchone()
    assert row["composite"] == pytest.approx(0.86)
    assert store.conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='scored'").fetchone()[0] == 1


# ── §11 query helpers ────────────────────────────────────────────────────────

def test_removed_at_stage_shows_what_and_why(store):
    store.upsert_company(_co("c1"))
    store.upsert_company(_co("c2", name="Shell AI Pharma"))
    store.delete_company("c2", reason="mktcap_out_of_band")
    removed = store.removed_at_stage("stage0b", reason="mktcap_out_of_band")
    assert [a.company_id for a in removed] == ["c2"]


def test_review_queue_dump(store):
    store.upsert_company(_co())
    store.add_to_review_queue("c1", reason="no_ta_tag")
    dump = store.review_queue_dump()
    assert len(dump) == 1 and dump[0]["company_id"] == "c1"


def test_seed_label_validation(store):
    store.set_seed_label("c1", "positive")
    assert store.conn.execute(
        "SELECT label FROM seed_labels WHERE company_id='c1'").fetchone()["label"] == "positive"
    with pytest.raises(ValueError):
        store.set_seed_label("c1", "maybe")
