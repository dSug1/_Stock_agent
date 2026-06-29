"""§15 run-summary / observability tests — funnel reconstruction, top-movers delta, and the export."""

from pathlib import Path

import pytest

from platform_discoverer import config as cfg
from platform_discoverer import observability
from platform_discoverer.models import Company, Score
from platform_discoverer.store import Store

ROOT = Path(__file__).parent.parent
CONFIG = cfg.load_config(ROOT / "config" / "config.yaml")
SMALL, YOUNG = 1e8, "2024-01-01"   # tier 1


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db", config=CONFIG)
    yield s
    s.close()


def _co(store, cid, ticker, cap=SMALL, ipo=YOUNG):
    store.upsert_company(Company(company_id=cid, name=f"{ticker} Co", primary_ticker=ticker,
                                 country="US", mktcap_usd_fd=cap, ipo_date=ipo))


def _rubric(moat="data", subst="substantive"):
    return {"company": "x", "ticker": "x", "A_proprietary_data": {"score": 5},
            "moat_location": {"data_vs_architecture": moat},
            "substance_check": {"verdict": subst}, "memo": "m"}


def test_movers_two_runs(store):
    _co(store, "c1", "AAA")
    store.record_score(Score(company_id="c1", run_id="2026-01-01T00:00:00+00:00", model="m",
                             json=_rubric(), composite=0.50), run_id="2026-01-01T00:00:00+00:00")
    store.record_score(Score(company_id="c1", run_id="2026-02-01T00:00:00+00:00", model="m",
                             json=_rubric(), composite=0.80), run_id="2026-02-01T00:00:00+00:00")
    mv = observability.movers(store)
    assert len(mv["up"]) == 1 and mv["up"][0]["ticker"] == "AAA"
    assert mv["up"][0]["delta"] == pytest.approx(0.30)
    assert mv["down"] == []


def test_movers_needs_two_scores(store):
    _co(store, "c1", "AAA")
    store.record_score(Score(company_id="c1", run_id="r1", model="m", json=_rubric(),
                             composite=0.5), run_id="r1")
    assert observability.movers(store) == {"up": [], "down": []}


def test_build_run_summary_sections(store):
    _co(store, "c1", "AAA")
    store.record_score(Score(company_id="c1", run_id="r1", model="m", json=_rubric(),
                             composite=0.9, confidence=0.8), run_id="r1")
    store.audit(stage="stage0b", action="flagged", reason="hard_cuts_done",
                detail={"kept": 1, "deleted_mktcap_out_of_band": 2, "deleted_not_live": 0})
    store.audit(stage="stage4", action="scored", reason="scoring_done",
                detail={"scored": 1, "triage_killed": 0, "web_searches": 4, "cost_usd": 0.12})
    md = observability.build_run_summary(store, CONFIG)
    assert "Run Summary" in md and "## Funnel" in md and "## Shortlist" in md
    assert "Top movers" in md and "Tier breakdown" in md
    assert "AAA" in md                                    # the scored company appears in the shortlist
    assert "web searches / cost" in md and "0.12" in md   # D9 web-search line


def test_write_run_summary_emits_stamped_and_stable(store, tmp_path):
    _co(store, "c1", "AAA")
    store.record_score(Score(company_id="c1", run_id="r1", model="m", json=_rubric(),
                             composite=0.9), run_id="r1")
    stamped = observability.write_run_summary(store, tmp_path, CONFIG,
                                              run_id="2026-06-29T12:00:00+00:00")
    assert stamped.exists() and (tmp_path / "run_summary.md").exists()
    assert ":" not in stamped.name                       # colons stripped from the stamped filename
