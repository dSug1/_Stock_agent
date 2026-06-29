"""Seed-eval (§13) tests — CSV parsing, per-stage survival, precision/recall over scored pos/neg,
the lost-positive spec-failure flag, and the report/run_meta side effects."""

from pathlib import Path

import pytest

from platform_discoverer import config as cfg
from platform_discoverer import seed_eval
from platform_discoverer.models import Company, Score
from platform_discoverer.store import Store, now_iso

ROOT = Path(__file__).parent.parent
CONFIG = cfg.load_config(ROOT / "config" / "config.yaml")


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db", config=CONFIG)
    yield s
    s.close()


def _co(store, cid, ticker):
    store.upsert_company(Company(company_id=cid, name=f"{ticker} Co", primary_ticker=ticker,
                                 country="US", mktcap_usd_fd=5e8, business_description="x"))


def _score(store, cid, composite):
    store.record_score(Score(company_id=cid, run_id=now_iso(), model="m",
                             json={"memo": "x"}, composite=composite, confidence=0.7),
                       run_id=now_iso())


def _labels():
    L = seed_eval.SeedLabel
    return [
        L("PWIN", "positive"), L("PLOW", "positive"), L("PDEL", "positive"), L("PUNS", "positive"),
        L("NHI", "negative"), L("NLO", "negative"),
        L("BRD", "borderline"),
    ]


def _seed_world(store):
    _co(store, "pwin", "PWIN"); _score(store, "pwin", 0.9)     # TP
    _co(store, "plow", "PLOW"); _score(store, "plow", 0.3)     # FN
    _co(store, "puns", "PUNS")                                 # positive, unscored
    _co(store, "nhi", "NHI"); _score(store, "nhi", 0.8)        # FP
    _co(store, "nlo", "NLO"); _score(store, "nlo", 0.2)        # TN
    _co(store, "brd", "BRD"); _score(store, "brd", 0.65)       # borderline (excluded from P/R)
    # a positive DELETED at the hard cut → spec-level failure
    _co(store, "pdel", "PDEL")
    store.delete_company("pdel", reason="mktcap_out_of_band",
                         detail={"ticker": "PDEL", "name": "PDEL Co"})


# ── CSV ────────────────────────────────────────────────────────────────────────

def test_load_seed_labels_real_file():
    labels = seed_eval.load_seed_labels(ROOT / "config" / "seed_labels.csv")
    by_t = {s.ticker: s.label for s in labels}
    assert by_t["ACRV"] == "positive" and by_t["CRL"] == "negative" and by_t["RXRX"] == "borderline"


def test_load_seed_labels_skips_bad(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("ticker,label,name\nAAA,positive,A\n,positive,blank\nBBB,bogus,B\n",
                 encoding="utf-8")
    labels = seed_eval.load_seed_labels(p)
    assert [s.ticker for s in labels] == ["AAA"]


# ── evaluation ───────────────────────────────────────────────────────────────

def test_precision_recall_and_confusion(store):
    _seed_world(store)
    res = seed_eval.evaluate(store, CONFIG, _labels(), threshold=0.6)
    m = res["metrics"]
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)
    assert m["precision"] == pytest.approx(0.5) and m["recall"] == pytest.approx(0.5)
    assert m["f1"] == pytest.approx(0.5)


def test_lost_positive_flags_spec_failure(store):
    _seed_world(store)
    m = seed_eval.evaluate(store, CONFIG, _labels(), threshold=0.6)["metrics"]
    assert m["spec_failure"] is True
    assert m["stage0_deleted_positives"] == ["PDEL"]
    assert m["positives_lost_pre_scoring"] == 1


def test_graduated_positive_is_not_a_spec_failure(store):
    # a positive deleted for re-rating ABOVE the ceiling is accepted (D7), not a failure
    store.upsert_company(Company(company_id="grad", name="Grad Co", primary_ticker="GRAD",
                                 mktcap_usd_fd=5e9))
    store.delete_company("grad", reason="mktcap_out_of_band",
                         detail={"ticker": "GRAD", "name": "Grad Co",
                                 "mktcap_usd_fd": 5e9, "band": [50_000_000, 3_000_000_000]})
    res = seed_eval.evaluate(store, CONFIG, [seed_eval.SeedLabel("GRAD", "positive")], threshold=0.6)
    m = res["metrics"]
    assert m["spec_failure"] is False
    assert m["graduated_positives"] == ["GRAD"]
    assert m["stage0_deleted_positives"] == []
    assert res["per_seed"][0]["disposition"] == "graduated"


def test_below_floor_positive_is_a_spec_failure(store):
    store.upsert_company(Company(company_id="tiny", name="Tiny Co", primary_ticker="TINY",
                                 mktcap_usd_fd=1e7))
    store.delete_company("tiny", reason="mktcap_out_of_band",
                         detail={"ticker": "TINY", "name": "Tiny Co",
                                 "mktcap_usd_fd": 1e7, "band": [50_000_000, 3_000_000_000]})
    m = seed_eval.evaluate(store, CONFIG, [seed_eval.SeedLabel("TINY", "positive")])["metrics"]
    assert m["spec_failure"] is True and m["stage0_deleted_positives"] == ["TINY"]


def test_survival_counts(store):
    _seed_world(store)
    surv = seed_eval.evaluate(store, CONFIG, _labels(), threshold=0.6)["survival"]
    # 4 positives: pwin/plow/puns in store, pdel deleted
    assert surv["positive"]["n"] == 4
    assert surv["positive"]["in_store"] == 3 and surv["positive"]["deleted"] == 1
    assert surv["positive"]["scored"] == 2          # pwin + plow (puns unscored)
    assert surv["negative"]["scored"] == 2
    assert surv["borderline"]["n"] == 1


def test_triage_killed_counts_as_predicted_negative(store):
    # a triage-kill is the pipeline's negative verdict: killed positive → FN, killed negative → TN
    _co(store, "pk", "PK")
    store.audit(stage="stage4", action="cut", company_id="pk", reason="triage_kill")
    _co(store, "nk", "NK")
    store.audit(stage="stage4", action="cut", company_id="nk", reason="triage_kill")
    res = seed_eval.evaluate(store, CONFIG,
                             [seed_eval.SeedLabel("PK", "positive"),
                              seed_eval.SeedLabel("NK", "negative")], threshold=0.6)
    m = res["metrics"]
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (0, 0, 1, 1)
    assert {x["ticker"]: x["status"] for x in res["per_seed"]} == \
        {"PK": "triage_killed", "NK": "triage_killed"}


def test_threshold_changes_predictions(store):
    _seed_world(store)
    # at 0.85, PWIN(0.9) still TP but NHI(0.8) no longer predicted positive → FP drops to 0
    m = seed_eval.evaluate(store, CONFIG, _labels(), threshold=0.85)["metrics"]
    assert m["fp"] == 0 and m["precision"] == pytest.approx(1.0)


def test_unscored_positive_not_a_failure(store):
    # a positive that is in-band + retained but simply unscored is NOT counted as lost
    _co(store, "puns", "PUNS")
    res = seed_eval.evaluate(store, CONFIG, [seed_eval.SeedLabel("PUNS", "positive")], threshold=0.6)
    assert res["metrics"]["spec_failure"] is False
    assert res["metrics"]["positives_lost_pre_scoring"] == 0
    assert res["per_seed"][0]["status"] == "unscored"


def test_absent_seed_reported(store):
    res = seed_eval.evaluate(store, CONFIG, [seed_eval.SeedLabel("ZZZZ", "positive")], threshold=0.6)
    assert res["per_seed"][0]["status"] == "absent"


# ── report + orchestrator ─────────────────────────────────────────────────────

def test_run_writes_report_and_run_meta(store, tmp_path):
    _seed_world(store)
    out = tmp_path / "seed_eval.md"
    summary = seed_eval.run(store, CONFIG, run_id="r1", out_path=out, labels=_labels())
    assert out.exists()
    md = out.read_text(encoding="utf-8")
    assert "Seed Validation" in md and "SPEC-LEVEL FAILURE" in md and "PWIN" in md
    row = store.conn.execute("SELECT metrics_json FROM run_meta WHERE run_id='r1'").fetchone()
    assert row is not None and "precision" in row["metrics_json"]


def test_persist_labels_writes_seed_table(store, tmp_path):
    _seed_world(store)
    res = seed_eval.run(store, CONFIG, run_id="r3", out_path=tmp_path / "seed_eval.md",
                        labels=_labels(), persist_labels=True)
    assert res["mode"] == "seed_eval"
    # PWIN/PLOW/PUNS (positive) + NHI/NLO (negative) exist in the store → 5 pos/neg labels persisted
    # (PDEL deleted so absent; BRD is borderline → table accepts only positive|negative)
    n = store.conn.execute("SELECT COUNT(*) FROM seed_labels").fetchone()[0]
    assert n == 5
