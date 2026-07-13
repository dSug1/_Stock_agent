"""OpenAlex credit-budget guard tests (offline). Covers the 2026 credit/USD quota model:
header parsing, exhaustion semantics, the ``_get`` hard-floor short-circuit, and the per-founder
budget gate that stops the literature/independence sweeps before the quota is gone (leaving the
backlog UNSTAMPED for a lossless re-run)."""

from __future__ import annotations

import pytest

from early_detection.clients import _net, openalex
from early_detection.config import Config
from early_detection.models import Entity
from early_detection.signals.independence import refine_independence
from early_detection.signals.literature import ingest_literature
from early_detection.store import Store


@pytest.fixture(autouse=True)
def _reset_credits():
    """CREDITS is process-global shared state — reset around every test so budget doesn't leak."""
    openalex.CREDITS.reset_state()
    yield
    openalex.CREDITS.reset_state()


# ── header parsing + exhaustion semantics ─────────────────────────────────────
def test_tracker_parses_quota_headers():
    openalex.CREDITS.note({
        "X-RateLimit-Remaining": "990", "X-RateLimit-Limit": "1000",
        "X-RateLimit-Reset": "77726", "X-RateLimit-Remaining-USD": "0.099",
    })
    st = openalex.CREDITS.status()
    assert st == {"remaining": 990, "limit": 1000, "reset_s": 77726, "remaining_usd": 0.099}


def test_tracker_ignores_missing_or_garbage_headers():
    openalex.CREDITS.note({"X-RateLimit-Remaining": "500"})
    openalex.CREDITS.note({"X-RateLimit-Remaining": "not-a-number"})  # must not clobber the good value
    openalex.CREDITS.note(None)                                        # a 429 with no headers
    assert openalex.CREDITS.remaining == 500


def test_exhausted_semantics():
    # unknown budget (first call of a run) never blocks
    assert openalex.CREDITS.exhausted(40) is False
    openalex.CREDITS.note({"X-RateLimit-Remaining": "41"})
    assert openalex.CREDITS.exhausted(40) is False       # above reserve
    openalex.CREDITS.note({"X-RateLimit-Remaining": "40"})
    assert openalex.CREDITS.exhausted(40) is True        # at reserve
    openalex.CREDITS.note({"X-RateLimit-Remaining": "0"})
    assert openalex.CREDITS.exhausted(0) is True         # hard floor


def test_notify_headers_swallows_callback_errors():
    def boom(_headers):
        raise RuntimeError("callback blew up")
    # must not propagate into the fetch path
    _net._notify_headers(boom, {"X-RateLimit-Remaining": "1"})


# ── _get short-circuits when the budget is gone (no network) ──────────────────
def test_get_returns_none_when_exhausted(monkeypatch):
    openalex.CREDITS.note({"X-RateLimit-Remaining": "0"})

    def _should_not_be_called(*a, **k):  # pragma: no cover - asserts no network
        raise AssertionError("safe_json_retry must not run when credits are exhausted")

    monkeypatch.setattr(_net, "safe_json_retry", _should_not_be_called)
    # search_authors → _get → None (treated as a throttle → founder left for retry, not a no-match [])
    assert openalex.search_authors("Stuart Rich", mailto="x@y.z") is None


# ── budget gate in the signal sweeps ──────────────────────────────────────────
def _seed_two_founders(store):
    for i in (1, 2):
        store.upsert_entity(Entity(entity_id=f"cik:{i}", legal_name=f"Co {i}",
                                   cik=f"000000000{i}", jurisdiction="US"))
        store.save_founders(f"cik:{i}", founders=[{"name": f"Founder {i}", "role": "CSO",
                                                   "institution": "MIT", "is_company_officer": True}],
                            affiliations=[], prompt_version="v1")


def test_literature_stops_on_credit_reserve(tmp_path):
    store = Store(tmp_path / "c.db")
    _seed_two_founders(store)
    cfg = Config(db_path=tmp_path / "c.db", openalex_credit_reserve=40)
    openalex.CREDITS.note({"X-RateLimit-Remaining": "10", "X-RateLimit-Reset": "3600"})  # below reserve

    def _no_call(*a, **k):  # pragma: no cover
        raise AssertionError("no OpenAlex call should happen once the reserve is hit")

    res = ingest_literature(store, cfg, search_authors=_no_call,
                            author_works=_no_call, citing_works=_no_call)
    assert res.stopped_early is True
    assert res.processed == 0 and res.budget_left == 2
    # nothing stamped → both founders resume on the next window
    assert len(store.founders_for_literature(only_missing=True)) == 2
    store.close()


def test_literature_runs_when_budget_unknown(tmp_path):
    store = Store(tmp_path / "u.db")
    _seed_two_founders(store)
    cfg = Config(db_path=tmp_path / "u.db", openalex_credit_reserve=40)
    # CREDITS is reset (None) → the gate must NOT block; injected callables return no-match

    res = ingest_literature(store, cfg, search_authors=lambda name, **kw: [],
                            author_works=lambda *a, **k: [], citing_works=lambda *a, **k: [])
    assert res.stopped_early is False and res.processed == 2
    store.close()


def test_independence_stops_on_credit_reserve(tmp_path):
    store = Store(tmp_path / "i.db")
    _seed_two_founders(store)
    # promote both founders to "resolved" so they enter the independence work-list
    for f in store.founders_for_literature(only_missing=True):
        store.set_founder_literature(f["id"], author_id="A1", foundational_work_id="Wf")
    cfg = Config(db_path=tmp_path / "i.db", openalex_credit_reserve=40)
    openalex.CREDITS.note({"X-RateLimit-Remaining": "5", "X-RateLimit-Reset": "3600"})

    def _no_call(*a, **k):  # pragma: no cover
        raise AssertionError("no OpenAlex call should happen once the reserve is hit")

    res = refine_independence(store, cfg, coauthor_ids=_no_call, citing_works=_no_call)
    assert res.stopped_early is True and res.refined == 0 and res.budget_left == 2
    assert len(store.founders_for_independence(only_missing=True)) == 2
    store.close()
