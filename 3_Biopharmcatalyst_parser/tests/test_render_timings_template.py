"""Smoke test for the templated M5 catalyst_timings renderer."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
SRC = PROJECT_ROOT / "src"
for p in (str(SCRIPTS), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_renderer():
    spec = importlib.util.spec_from_file_location(
        "render_timings", SCRIPTS / "3_5_render_timings.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_template_version_is_stable():
    r = _load_renderer()
    assert r._template_version() == r._template_version()


def test_template_html_uses_sidecar(monkeypatch):
    r = _load_renderer()
    html = r._build_template_html()
    assert '<script src="catalyst_timings_data.js"></script>' in html
    assert "window.__DATA = {" not in html, "no inline data assignment in template"
    assert "template-version" in html


def test_template_version_changes_on_edit(monkeypatch):
    r = _load_renderer()
    v1 = r._template_version()
    monkeypatch.setattr(r, "_HTML_TEMPLATE", r._HTML_TEMPLATE + "\n<!-- trivial -->")
    v2 = r._template_version()
    assert v1 != v2


def _seed_db(db_path: Path, snap: date, *, ticker: str, drug: str = "DrugX",
             nct: str = "NCT01", next_type: str = "Interim Data",
             date_min: str = "2026-07-01", date_max: str = "2026-07-05",
             precision: str = "conference", lane: str = "conference"):
    from database.db import get_connection
    conn = get_connection(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO catalyst_snapshots
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
         name, stage, market_cap_usd)
        VALUES (?, ?, ?, ?, ?, ?, 'phase2', 5e8)""",
        (snap.isoformat(), ticker, drug, nct, next_type, ticker + " Co"),
    )
    conn.execute(
        """INSERT OR REPLACE INTO catalyst_timing
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
         date_min, date_max, precision_tier, source_lane, computed_at, rules_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'v1.0')""",
        (snap.isoformat(), ticker, drug, nct, next_type,
         date_min, date_max, precision, lane,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def test_render_rolling_default_writes_both_files(tmp_path, monkeypatch):
    r = _load_renderer()
    db_path = tmp_path / "biotech.db"
    snap = date(2026, 5, 27)
    _seed_db(db_path, snap, ticker="TST")
    import database.db as db_mod
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", db_path)

    out_dir = tmp_path / "Outputs"
    html_path, data_path, action, summary = r.render(out_dir=out_dir)
    assert html_path.exists() and data_path.exists()
    assert action == "created"

    data_text = data_path.read_text(encoding="utf-8")
    assert data_text.startswith("window.__DATA = ")
    payload = json.loads(data_text[len("window.__DATA = "):].rstrip().rstrip(";"))
    assert payload["view_mode"] == "rolling"
    assert payload["effective_today"] == "2026-05-27"
    assert len(payload["rows"]) == 1

    # Second invocation: template stays up-to-date
    _, _, action2, _ = r.render(out_dir=out_dir)
    assert action2 == "up-to-date"


def test_rolling_view_unions_multiple_snapshots(tmp_path, monkeypatch):
    """Rolling view should return latest row per catalyst across snapshots."""
    r = _load_renderer()
    db_path = tmp_path / "biotech.db"
    # Two snapshots, same catalyst at both, with date_min slipping
    _seed_db(db_path, date(2026, 5, 27), ticker="TST",
             date_min="2026-07-01", date_max="2026-07-05")
    _seed_db(db_path, date(2026, 5, 28), ticker="TST",
             date_min="2026-07-10", date_max="2026-07-15")
    # Another catalyst only in v3 → still actionable
    _seed_db(db_path, date(2026, 5, 27), ticker="OLD",
             date_min="2026-08-01", date_max="2026-08-31")
    # A materialized one — only in v3, window already passed
    _seed_db(db_path, date(2026, 5, 27), ticker="MAT",
             date_min="2026-05-20", date_max="2026-05-24")

    import database.db as db_mod
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", db_path)

    out_dir = tmp_path / "Outputs"
    _, data_path, _, _ = r.render(out_dir=out_dir)
    payload = json.loads(
        data_path.read_text(encoding="utf-8")
        [len("window.__DATA = "):].rstrip().rstrip(";")
    )
    tickers = {row["ticker"] for row in payload["rows"]}
    assert tickers == {"TST", "OLD"}, "MAT (materialized) should be filtered out"
    tst_row = next(r for r in payload["rows"] if r["ticker"] == "TST")
    # Latest TST row should be from 2026-05-28
    assert tst_row["snapshot_date"] == "2026-05-28"
    assert tst_row["date_min"] == "2026-07-10"
    assert payload["materialized_dropped"] == 1


def test_single_snapshot_mode_preserved(tmp_path, monkeypatch):
    """--snapshot-date returns only that snapshot's rows (legacy)."""
    r = _load_renderer()
    db_path = tmp_path / "biotech.db"
    _seed_db(db_path, date(2026, 5, 27), ticker="A")
    _seed_db(db_path, date(2026, 5, 28), ticker="B")

    import database.db as db_mod
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", db_path)

    _, data_path, _, _ = r.render(snapshot_date=date(2026, 5, 27),
                                  out_dir=tmp_path / "Outputs")
    payload = json.loads(
        data_path.read_text(encoding="utf-8")
        [len("window.__DATA = "):].rstrip().rstrip(";")
    )
    assert payload["view_mode"] == "single"
    assert {row["ticker"] for row in payload["rows"]} == {"A"}
