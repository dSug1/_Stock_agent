"""Tiering tests — the market-cap × age buckets, the untiered (missing-data) bucket, the boundary
semantics, and the selection parser. Plus the Stage-4 tier gate (the IPO-date filter upstream of
the Claude call) and the renderer's data payload."""

import copy
from datetime import date
from pathlib import Path

import pytest

from platform_discoverer import config as cfg
from platform_discoverer import render, stage4, tiering
from platform_discoverer.models import Company, Score
from platform_discoverer.store import Store, now_iso

ROOT = Path(__file__).parent.parent
CONFIG = cfg.load_config(ROOT / "config" / "config.yaml")
TODAY = date(2026, 6, 29)

SMALL = 100_000_000      # < 400M threshold
LARGE = 1_000_000_000    # >= 400M
YOUNG = "2024-01-01"     # ~2.5 yr < 20
OLD = "1995-01-01"       # ~31 yr >= 20


def _co(cap, ipo, cid="c", ticker="T", country="US"):
    return Company(company_id=cid, name=f"{cid} Co", primary_ticker=ticker, country=country,
                   mktcap_usd_fd=cap, ipo_date=ipo)


# ── compute_tier ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cap,ipo,expected", [
    (SMALL, YOUNG, 1),   # small & young
    (LARGE, YOUNG, 2),   # large & young
    (LARGE, OLD, 3),     # large & old
    (SMALL, OLD, 4),     # small & old
    (None, YOUNG, 0),    # missing cap → untiered
    (SMALL, None, 0),    # missing IPO → untiered
    (None, None, 0),
])
def test_compute_tier(cap, ipo, expected):
    assert tiering.compute_tier(_co(cap, ipo), CONFIG, today=TODAY) == expected


def test_tier_boundary_threshold_is_large_and_old():
    # exactly at the thresholds: cap == 400M counts as "large", age == 20yr counts as "old"
    at_cap = _co(400_000_000, YOUNG)
    twenty = _co(SMALL, "2006-06-29")    # exactly ~20.0 yr before TODAY
    assert tiering.compute_tier(at_cap, CONFIG, today=TODAY) == 2     # large & young
    assert tiering.compute_tier(twenty, CONFIG, today=TODAY) == 4     # small & old


def test_age_years():
    assert tiering.age_years("2016-06-29", TODAY) == pytest.approx(10.0, abs=0.05)
    assert tiering.age_years(None, TODAY) is None
    assert tiering.age_years("nonsense", TODAY) is None


def test_tier_breakdown_covers_all_tiers():
    cos = [_co(SMALL, YOUNG, "a"), _co(LARGE, YOUNG, "b"), _co(SMALL, None, "c")]
    bd = tiering.tier_breakdown(cos, CONFIG, today=TODAY)
    assert bd == {1: 1, 2: 1, 3: 0, 4: 0, 0: 1}


@pytest.mark.parametrize("text,expected", [
    ("1,2", {1, 2}),
    ("1 3 4", {1, 3, 4}),
    ("all", {0, 1, 2, 3, 4}),
    ("", {0, 1, 2, 3, 4}),
    ("9,2", {2}),          # out-of-range ignored
    ("garbage", set()),
])
def test_parse_tier_selection(text, expected):
    assert tiering.parse_tier_selection(text) == expected


# ── Stage-4 tier gate ──────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db", config=CONFIG)
    yield s
    s.close()


def _seed(store, cid, ticker, cap, ipo):
    store.upsert_company(Company(company_id=cid, name=f"{ticker} Co", primary_ticker=ticker,
                                 country="US", mktcap_usd_fd=cap, ipo_date=ipo,
                                 business_description="platform"))


def test_stage4_candidates_filtered_by_tier(store):
    _seed(store, "t1", "AAA", SMALL, YOUNG)   # tier 1
    _seed(store, "t2", "BBB", LARGE, YOUNG)   # tier 2
    _seed(store, "t4", "CCC", SMALL, OLD)     # tier 4
    _seed(store, "t0", "DDD", None, YOUNG)    # tier 0 (untiered)
    got = {c.company_id for c in stage4._candidates(store, CONFIG, tiers={1, 2})}
    assert got == {"t1", "t2"}
    got0 = {c.company_id for c in stage4._candidates(store, CONFIG, tiers={0})}
    assert got0 == {"t0"}
    # no tier gate → everyone due
    assert len({c.company_id for c in stage4._candidates(store, CONFIG)}) == 4


def test_due_tier_breakdown(store):
    _seed(store, "t1", "AAA", SMALL, YOUNG)
    _seed(store, "t1b", "EEE", SMALL, YOUNG)
    _seed(store, "t3", "CCC", LARGE, OLD)
    bd = stage4.due_tier_breakdown(store, CONFIG)
    assert bd[1] == 2 and bd[3] == 1 and bd[2] == 0


def test_estimate_respects_tier_selection(store):
    _seed(store, "t1", "AAA", SMALL, YOUNG)
    _seed(store, "t3", "CCC", LARGE, OLD)
    est = stage4.run(store, CONFIG, dispatch=False, tiers={1})
    assert est["candidates"] == 1 and est["selected_tiers"] == [1]


# ── renderer data payload ──────────────────────────────────────────────────────

def test_build_data_assigns_tiers_and_dedups(store):
    _seed(store, "t1", "AAA", SMALL, YOUNG)
    # duplicate company-id for one real company (same ticker+country) → one row
    _seed(store, "acrv_seed", "ACRV", SMALL, YOUNG)
    _seed(store, "acrv_sec", "ACRV", SMALL, YOUNG)
    data = render.build_data(store, CONFIG, today=TODAY)
    tickers = sorted(r["ticker"] for r in data["rows"])
    assert tickers == ["AAA", "ACRV"]                       # ACRV collapsed
    assert all(r["tier"] == 1 for r in data["rows"])        # all small & young
    assert data["tier_breakdown"]["1"] == 2
    assert data["funnel"]["companies_shown"] == 2


def test_build_data_marks_scored(store):
    _seed(store, "t1", "AAA", SMALL, YOUNG)
    r = {"company": "AAA", "ticker": "AAA",
         "A_proprietary_data": {"score": 5}, "B_compute_engine": {"score": 3},
         "C_validation": {"score": 4}, "D_mechanism": {"score": 4, "mechanism_ids": ["menin_kmt2a"]},
         "E_translation": {"score": 5},
         "moat_location": {"data_vs_architecture": "data", "rationale": "withheld"},
         "substance_check": {"verdict": "substantive", "disconfirming_evidence": "thin"},
         "memo": "strong"}
    store.record_score(Score(company_id="t1", run_id=now_iso(), model="claude-sonnet-4-6", json=r,
                             composite=0.8, confidence=0.7), run_id=now_iso())
    row = next(x for x in render.build_data(store, CONFIG, today=TODAY)["rows"] if x["ticker"] == "AAA")
    assert row["scored"] and row["composite"] == 0.8
    assert row["axes"] == {"A": 5, "B": 3, "C": 4, "D": 4, "E": 5}
    assert row["moat"] == "data" and row["mechanism_ids"] == ["menin_kmt2a"]


def test_write_report_writes_template_and_sidecar(store, tmp_path):
    _seed(store, "t1", "AAA", SMALL, YOUNG)
    out = tmp_path / "screener_report.html"
    render.write_report(store, out, CONFIG)
    sidecar = tmp_path / "screener_report_data.js"
    assert out.exists() and sidecar.exists()
    assert "window.__DATA" in sidecar.read_text(encoding="utf-8")
    assert 'data-tab="1"' in out.read_text(encoding="utf-8")


def test_write_report_template_stable_sidecar_rewrites(store, tmp_path):
    _seed(store, "t1", "AAA", SMALL, YOUNG)
    out = tmp_path / "screener_report.html"
    render.write_report(store, out, CONFIG)
    mtime1 = out.stat().st_mtime_ns
    _seed(store, "t2", "BBB", LARGE, YOUNG)
    render.write_report(store, out, CONFIG)        # data changed, template did not
    assert out.stat().st_mtime_ns == mtime1        # HTML untouched (hash unchanged)
    sidecar = tmp_path / "screener_report_data.js"
    assert "BBB" in sidecar.read_text(encoding="utf-8")    # sidecar refreshed
