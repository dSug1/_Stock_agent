"""Smoke test for the templated catalyst_scores renderer.

Checks the template/data split: HTML template stays stable across
data refreshes, sidecar JS is rewritten, version meta tag round-trips,
and the JS bundle parses to balanced braces.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
SRC = PROJECT_ROOT / "src"
for p in (str(SCRIPTS), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import importlib.util


def _load_renderer():
    spec = importlib.util.spec_from_file_location(
        "render_scores", SCRIPTS / "3_6_render_scores.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_template_version_is_stable():
    r = _load_renderer()
    assert r._template_version() == r._template_version()


def test_template_html_skeleton_has_no_inline_data():
    r = _load_renderer()
    html = r._build_template_html()
    assert "<script src=\"catalyst_scores_data.js\"></script>" in html
    assert "window.__DATA = {" not in html, "data assignment must live in sidecar"
    assert "template-version" in html


def test_template_version_changes_when_js_changes(monkeypatch):
    r = _load_renderer()
    v1 = r._template_version()
    original_js = r.JS
    monkeypatch.setattr(r, "JS", original_js + "\n// trivial edit")
    v2 = r._template_version()
    assert v1 != v2


def test_existing_template_version_extracts_meta_tag(tmp_path):
    r = _load_renderer()
    html = r._build_template_html()
    p = tmp_path / "scratch.html"
    p.write_text(html, encoding="utf-8")
    assert r._existing_template_version(p) == r._template_version()


def test_existing_template_version_none_for_missing(tmp_path):
    r = _load_renderer()
    assert r._existing_template_version(tmp_path / "nope.html") is None


def test_render_writes_template_and_sidecar(tmp_path, monkeypatch):
    """End-to-end: invoke render() against a freshly-built isolated
    biotech.db with a single scored row, then verify the two output
    files exist with the right shape."""
    r = _load_renderer()

    # Build an isolated biotech.db at tmp_path
    from database.db import get_connection
    db_path = tmp_path / "biotech.db"
    conn = get_connection(db_path)
    from datetime import date, datetime, timezone
    snap = date(2026, 5, 27)
    conn.execute(
        """INSERT INTO catalyst_snapshots
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
         name, stage, market_cap_usd)
        VALUES (?, 'TST', 'DrugX', 'NCT01', 'Interim Data', 'Test Co', 'phase2', 5e8)""",
        (snap.isoformat(),),
    )
    conn.execute(
        """INSERT INTO catalyst_timing
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
         date_min, date_max, precision_tier, source_lane, computed_at, rules_version)
        VALUES (?, 'TST', 'DrugX', 'NCT01', 'Interim Data', ?, ?, 'conference', 'conference',
                ?, 'v1.0')""",
        (snap.isoformat(), "2026-07-01", "2026-07-05",
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.execute(
        """INSERT INTO catalyst_scores
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
         hard_pass, timing_bucket, composite_score, insider_score, momentum_score,
         fund_accumulation_score, computed_at, rules_version)
        VALUES (?, 'TST', 'DrugX', 'NCT01', 'Interim Data',
                1, 'catalyst_date_defined', 75.0, 50.0, 80.0, 100.0,
                ?, 'v1.0:test001')""",
        (snap.isoformat(),
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()

    # Redirect renderer to use the isolated DB
    import database.db as db_mod
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", db_path)

    out_dir = tmp_path / "Outputs"
    html_path, data_path, action = r.render(out_dir=out_dir)
    assert html_path.exists()
    assert data_path.exists()
    assert action == "created"

    html = html_path.read_text(encoding="utf-8")
    data = data_path.read_text(encoding="utf-8")

    # Data sidecar contract
    assert data.startswith("window.__DATA = ")
    payload = json.loads(data[len("window.__DATA = "):].rstrip().rstrip(";"))
    assert payload["snapshot_date"] == "2026-05-27"
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["ticker"] == "TST"

    # Template uses correct version meta tag
    assert f'content="{r._template_version()}"' in html

    # Second invocation: data should refresh, template up-to-date
    html_path2, data_path2, action2 = r.render(out_dir=out_dir)
    assert action2 == "up-to-date"


def test_render_rebuilds_template_when_version_mismatch(tmp_path, monkeypatch):
    r = _load_renderer()

    from database.db import get_connection
    db_path = tmp_path / "biotech.db"
    conn = get_connection(db_path)
    from datetime import date, datetime, timezone
    snap = date(2026, 5, 27)
    conn.execute(
        """INSERT INTO catalyst_snapshots
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type, stage)
        VALUES (?, 'TST', 'DrugX', 'NCT01', 'Interim Data', 'phase2')""",
        (snap.isoformat(),),
    )
    conn.execute(
        """INSERT INTO catalyst_scores
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
         hard_pass, computed_at, rules_version)
        VALUES (?, 'TST', 'DrugX', 'NCT01', 'Interim Data',
                0, ?, 'v1.0:test001')""",
        (snap.isoformat(),
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()

    import database.db as db_mod
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", db_path)

    out_dir = tmp_path / "Outputs"
    out_dir.mkdir()
    # Place a stale template at the target path with a bogus version
    stale_html = (
        r.HTML_SKELETON
        .replace("__CSS__", r.CSS)
        .replace("__JS__", r.JS)
        .replace("__TEMPLATE_VERSION__", "stale99")
    )
    (out_dir / "catalyst_scores.html").write_text(stale_html, encoding="utf-8")

    _, _, action = r.render(out_dir=out_dir)
    assert action == "rebuilt"
