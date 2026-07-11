"""M13 — HTML digest render offline tests (template + sidecar split, no network)."""

from __future__ import annotations

import json

import pytest

from early_detection import render
from early_detection.config import Config
from early_detection.models import Entity
from early_detection.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "r.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "r.db", scoring_prompt_version="v1", scoring_model="claude-sonnet-5")


def _seed(store):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Cold Bio", ticker_primary="COLD",
                               jurisdiction="US", market_cap_usd=1.1e7, in_existing_universe=False))
    store.save_founders("cik:1", founders=[{"name": "Jane Sci", "role": "CSO", "institution": "MIT",
                                            "is_company_officer": True}], affiliations=[], prompt_version="v1")
    store.save_score(entity_id="cik:1", run_id="r", model="m", conviction_flag="deep-dive-candidate",
                     conviction_score=80, prompt_version="v1",
                     data={"mechanism_summary": "Novel <MOA> & \"stuff\"",
                           "independent_validation_status": "multi-lab-independent",
                           "stack_convergence_dimensions": ["citations"], "base_rate_context": "most fail",
                           "confidence_caveats": ["small n"]})


def test_render_writes_template_and_sidecar(store, cfg, tmp_path):
    _seed(store)
    out = tmp_path / "Outputs" / "digest.html"
    p = render.write_report(store, cfg, out)
    assert p == out and out.exists()
    sidecar = out.with_name("digest_data.js")
    assert sidecar.exists()

    html = out.read_text(encoding="utf-8")
    js = sidecar.read_text(encoding="utf-8")
    # template loads the sidecar via a sibling <script src>, works under file://
    assert '<script src="digest_data.js">' in html
    # NO external resources (no http/https/cdn) — self-contained + injection-safe
    assert "http://" not in html and "https://" not in html
    # sidecar is JSON on window.__DATA
    assert js.startswith("window.__DATA = ")
    data = json.loads(js[len("window.__DATA = "):js.rstrip().rstrip(";")]) if False else \
        json.loads(js.split("=", 1)[1].rsplit(";", 1)[0].strip())
    assert data["meta"]["count"] == 1
    row = data["rows"][0]
    assert row["ticker"] == "COLD" and row["flag"] == "deep-dive-candidate"
    assert row["evidence"]["founders"][0]["name"] == "Jane Sci"
    # untrusted text is carried as JSON data (escaped client-side by escapeHtml), not injected into HTML
    assert "Novel <MOA>" not in html


def test_render_stable_template_versioned(store, cfg, tmp_path):
    _seed(store)
    p = render.write_report(store, cfg, tmp_path / "digest.html")
    html = p.read_text(encoding="utf-8")
    assert "<!-- template " in html                 # hash-versioned stable template marker
    assert "escapeHtml" in html or "const esc=" in html
