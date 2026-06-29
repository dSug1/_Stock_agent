"""Render data-sidecar tests — focus on the triaged-out flag (red + sorted-to-bottom in the HTML)."""

from pathlib import Path

import pytest

from platform_discoverer import config as cfg
from platform_discoverer import render
from platform_discoverer.models import Company, Score
from platform_discoverer.store import Store, now_iso

ROOT = Path(__file__).parent.parent
CONFIG = cfg.load_config(ROOT / "config" / "config.yaml")


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db", config=CONFIG)
    yield s
    s.close()


def _company(cid, ticker):
    return Company(company_id=cid, name=f"{ticker} Co", primary_ticker=ticker,
                   mktcap_usd_fd=2e8, ipo_date="2023-01-01", source_nets=["sector"], is_live=True)


def test_triaged_out_flag_and_reason(store):
    store.upsert_company(_company("k1", "KILL"))
    store.audit(stage="stage4", action="cut", company_id="k1", reason="triage_kill",
                run_id=now_iso(), detail={"why": "single-asset clinical biotech, no data engine"})
    rows = {r["company_id"]: r for r in render.build_data(store, CONFIG)["rows"]}
    assert rows["k1"]["triaged_out"] is True
    assert "single-asset" in rows["k1"]["triage_reason"]
    assert rows["k1"]["scored"] is False


def test_scored_company_is_not_marked_triaged(store):
    """Scored takes precedence — a company killed in an early run but later scored is NOT triaged_out."""
    store.upsert_company(_company("s1", "SCOR"))
    store.audit(stage="stage4", action="cut", company_id="s1", reason="triage_kill",
                run_id=now_iso(), detail={"why": "early kill"})
    store.record_score(Score(company_id="s1", run_id=now_iso(), model="claude-sonnet-4-6",
                             composite=0.7, confidence=0.6), run_id=now_iso())
    row = next(r for r in render.build_data(store, CONFIG)["rows"] if r["company_id"] == "s1")
    assert row["scored"] is True and row["triaged_out"] is False


def test_never_scored_company_is_not_triaged(store):
    store.upsert_company(_company("n1", "NONE"))
    row = next(r for r in render.build_data(store, CONFIG)["rows"] if r["company_id"] == "n1")
    assert row["triaged_out"] is False and row["scored"] is False
