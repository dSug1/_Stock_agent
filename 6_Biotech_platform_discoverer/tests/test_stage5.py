"""M6 (Stage 5) tests — lifecycle age weighting, presentation-layer dedup, ranking, review-queue
refresh, the house-format export, and the ipo_date schema-v3 round-trip."""

import copy
from datetime import date
from pathlib import Path

import pytest

from platform_discoverer import config as cfg
from platform_discoverer import stage5
from platform_discoverer.clients import market
from platform_discoverer.models import Company, Score
from platform_discoverer.store import Store, now_iso

ROOT = Path(__file__).parent.parent
CONFIG = cfg.load_config(ROOT / "config" / "config.yaml")
TODAY = date(2026, 6, 29)


def _lifecycle_on():
    c = copy.deepcopy(CONFIG)
    c["stage5"]["lifecycle"]["enabled"] = True
    return c


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db", config=CONFIG)
    yield s
    s.close()


def _rubric(ticker="ACRV", moat="data", substance="substantive", a=5, c=5):
    return {
        "company": ticker, "ticker": ticker,
        "A_proprietary_data": {"score": a, "citation": ""},
        "B_compute_engine": {"score": 3, "citation": ""},
        "C_validation": {"score": c, "citation": ""},
        "D_mechanism": {"score": 4, "mechanism_ids": ["menin_kmt2a"], "branch": "oncology",
                        "disruption_rationale": ""},
        "E_translation": {"score": 5, "citation": ""},
        "moat_location": {"data_vs_architecture": moat, "rationale": "withheld dataset"},
        "substance_check": {"verdict": substance, "disconfirming_evidence": "company-authored"},
        "memo": f"{ticker} memo [V] strong platform.",
    }


def _seed(store, cid, ticker, *, name=None, composite=0.8, confidence=0.8, isin=None,
          ipo_date=None, rubric=None):
    store.upsert_company(Company(company_id=cid, name=name or f"{ticker} Therapeutics",
                                 primary_ticker=ticker, exchange="NASDAQ", country="US",
                                 isin=isin, ipo_date=ipo_date, mktcap_usd_fd=5e8))
    r = rubric or _rubric(ticker)
    store.record_score(Score(company_id=cid, run_id=now_iso(), model="claude-sonnet-4-6",
                             json=r, A=r["A_proprietary_data"]["score"], composite=composite,
                             confidence=confidence), run_id=now_iso())


# ── ipo_date schema v3 round-trip ─────────────────────────────────────────────

def test_ipo_date_roundtrip(store):
    store.upsert_company(Company(company_id="c1", name="X", primary_ticker="X",
                                 ipo_date="2021-05-10"))
    assert store.get_company("c1").ipo_date == "2021-05-10"


def test_epoch_to_iso_date():
    assert market._epoch_to_iso_date(1620604800) == "2021-05-10"
    assert market._epoch_to_iso_date(None) is None
    assert market._epoch_to_iso_date(0) is None


def test_first_trade_date_field_fallbacks():
    # current yfinance exposes firstTradeDateMilliseconds (ms), not firstTradeDateEpochUtc (s)
    assert market._first_trade_date({"firstTradeDateMilliseconds": 1668522600000}) == "2022-11-15"
    assert market._first_trade_date({"firstTradeDateEpochUtc": 1620604800}) == "2021-05-10"
    assert market._first_trade_date({"ipoExpectedDate": "2022-11-15"}) == "2022-11-15"
    # epoch fields win over the expected-date string
    assert market._first_trade_date(
        {"firstTradeDateMilliseconds": 1668522600000, "ipoExpectedDate": "1999-01-01"}) == "2022-11-15"
    assert market._first_trade_date({}) is None
    assert market._first_trade_date({"ipoExpectedDate": "not-a-date"}) is None


# ── lifecycle weight ──────────────────────────────────────────────────────────

def test_lifecycle_disabled_is_always_one():
    c = Company(company_id="c1", name="Old Co", primary_ticker="O", ipo_date="1990-01-01")
    assert stage5.lifecycle_weight(c, CONFIG, today=TODAY) == 1.0   # OFF by default


def test_lifecycle_unknown_age_is_full_weight():
    c = Company(company_id="c1", name="No IPO", primary_ticker="N", ipo_date=None)
    assert stage5.lifecycle_weight(c, _lifecycle_on(), today=TODAY) == 1.0


def test_lifecycle_young_full_old_floor_mid_decays():
    on = _lifecycle_on()
    young = Company(company_id="y", name="Y", ipo_date="2024-01-01")      # ~2.5 yr
    old = Company(company_id="o", name="O", ipo_date="2000-01-01")        # ~26 yr
    mid = Company(company_id="m", name="M", ipo_date="2008-06-29")        # ~18 yr (between 15 and 20)
    assert stage5.lifecycle_weight(young, on, today=TODAY) == 1.0
    assert stage5.lifecycle_weight(old, on, today=TODAY) == 0.5          # floor
    w = stage5.lifecycle_weight(mid, on, today=TODAY)
    assert 0.5 < w < 1.0                                                 # linear decay band


def test_age_years():
    assert stage5._age_years("2016-06-29", TODAY) == pytest.approx(10.0, abs=0.05)
    assert stage5._age_years(None, TODAY) is None
    assert stage5._age_years("garbage", TODAY) is None


# ── dedup + ranking ───────────────────────────────────────────────────────────

def test_rank_orders_by_composite(store):
    _seed(store, "a1", "ACRV", composite=0.9)
    _seed(store, "b1", "BBBB", composite=0.4)
    ranked = stage5.rank(store, CONFIG, today=TODAY)
    assert [e["company"].primary_ticker for e in ranked] == ["ACRV", "BBBB"]
    assert ranked[0]["rank"] == 1 and ranked[0]["rank_score"] == 0.9


def test_dedup_merges_by_ticker_despite_name_suffix(store):
    # the real bug: seed-CSV name vs SEC name share a ticker but normalize differently ("Inc.")
    _seed(store, "id_seed", "ACRV", name="Acrivon Therapeutics", composite=0.7)
    _seed(store, "id_sec", "ACRV", name="Acrivon Therapeutics, Inc.", composite=0.9)
    ranked = stage5.rank(store, CONFIG, today=TODAY)
    assert len(ranked) == 1 and ranked[0]["composite"] == 0.9   # ticker key collapses them


def test_dedup_name_fallback_strips_legal_suffix(store):
    # no ticker, no ISIN → name fallback; "Inc"/"Corporation" legal suffixes are stripped
    store.upsert_company(Company(company_id="n1", name="Foobar Therapeutics", mktcap_usd_fd=5e8))
    store.upsert_company(Company(company_id="n2", name="Foobar Therapeutics Inc.", mktcap_usd_fd=5e8))
    for cid in ("n1", "n2"):
        r = _rubric("FOO")
        store.record_score(Score(company_id=cid, run_id=now_iso(), model="m", json=r,
                                 composite=0.6, confidence=0.7), run_id=now_iso())
    assert len(stage5.rank(store, CONFIG, today=TODAY)) == 1


def test_dedup_name_fallback_keeps_distinct_industry_words(store):
    # "Therapeutics" vs "Pharmaceuticals" are NOT legal suffixes → must stay distinct
    store.upsert_company(Company(company_id="d1", name="Foobar Therapeutics", mktcap_usd_fd=5e8))
    store.upsert_company(Company(company_id="d2", name="Foobar Pharmaceuticals", mktcap_usd_fd=5e8))
    for cid in ("d1", "d2"):
        r = _rubric("FOO")
        store.record_score(Score(company_id=cid, run_id=now_iso(), model="m", json=r,
                                 composite=0.6, confidence=0.7), run_id=now_iso())
    assert len(stage5.rank(store, CONFIG, today=TODAY)) == 2


def test_dedup_merges_when_identifiers_are_asymmetric(store):
    # the live ACRV case: one row has an ISIN, its duplicate does not, but both share ticker+country.
    # A per-row "strongest key" would split them (isin: vs tkr:); shared-signal union merges them.
    _seed(store, "id_seed", "ACRV", name="Acrivon Therapeutics", composite=0.7,
          isin="US00489L1098")
    _seed(store, "id_sec", "ACRV", name="Acrivon Therapeutics, Inc.", composite=0.9, isin=None)
    ranked = stage5.rank(store, CONFIG, today=TODAY)
    assert len(ranked) == 1 and ranked[0]["merged_ids"] == ["id_seed"]


def test_dedup_by_isin_isolated(tmp_path):
    # different tickers (ADR/dual-listing), same ISIN → only the ISIN signal can merge them
    s = Store.open(tmp_path / "s.db", config=CONFIG)
    _seed(s, "id_us", "FOOB", name="Foobar A", composite=0.7, isin="US00400X1000")
    _seed(s, "id_eu", "FOO", name="Foobar B", composite=0.9, isin="US00400X1000")
    ranked = stage5.rank(s, CONFIG, today=TODAY)
    assert len(ranked) == 1
    rep = ranked[0]
    assert rep["composite"] == 0.9               # kept the better-scored representative
    assert rep["merged_ids"] == ["id_us"]        # the other id recorded, not deleted
    assert s.get_company("id_us") is not None     # cardinal rule: nothing deleted
    s.close()


def test_lifecycle_reorders_ranking(tmp_path):
    on = _lifecycle_on()
    s = Store.open(tmp_path / "s.db", config=on)
    _seed(s, "old", "OLDX", composite=0.85, ipo_date="1998-01-01")   # high score, ancient → ×0.5
    _seed(s, "young", "NEWX", composite=0.70, ipo_date="2024-01-01")  # lower score, young → ×1.0
    ranked = stage5.rank(s, on, today=TODAY)
    # without lifecycle OLDX would lead (0.85 > 0.70); with it, 0.85×0.5=0.425 < 0.70×1.0
    assert ranked[0]["company"].primary_ticker == "NEWX"
    assert ranked[0]["rank_score"] == pytest.approx(0.70)
    s.close()


# ── review-queue refresh (§11) ────────────────────────────────────────────────

def test_review_queue_flags_marketing_and_low_conf(store, tmp_path):
    _seed(store, "mk", "MKTG", composite=0.5, rubric=_rubric("MKTG", substance="marketing"))
    _seed(store, "lc", "LOWC", composite=0.7, confidence=0.3)        # high comp, low conf
    _seed(store, "ok", "GOOD", composite=0.8, confidence=0.9)        # clean → not flagged
    stage5.run(store, CONFIG, run_id="r1", out_path=tmp_path / "_t.md")
    rq = {r["company_id"] for r in store.review_queue_dump()}
    assert {"mk", "lc"} <= rq and "ok" not in rq


# ── export + orchestrator ─────────────────────────────────────────────────────

def test_run_exports_shortlist_and_records_run_meta(store, tmp_path):
    _seed(store, "a1", "ACRV", composite=0.9)
    _seed(store, "b1", "BBBB", composite=0.4)
    out = tmp_path / "shortlist.md"
    summary = stage5.run(store, CONFIG, run_id="r1", out_path=out, today=TODAY)
    assert summary["shortlist_rows"] == 2 and out.exists()
    md = out.read_text(encoding="utf-8")
    assert "Ranked shortlist" in md and "ACRV" in md and "[V]" in md      # memo labels preserved
    # run_meta persisted
    row = store.conn.execute("SELECT metrics_json FROM run_meta WHERE run_id='r1'").fetchone()
    assert row is not None and "shortlist_rows" in row["metrics_json"]


def test_run_empty_store_is_safe(store, tmp_path):
    out = tmp_path / "empty.md"
    summary = stage5.run(store, CONFIG, run_id="r0", out_path=out, today=TODAY)
    assert summary["shortlist_rows"] == 0 and out.exists()
    assert "No scored companies yet" in out.read_text(encoding="utf-8")
