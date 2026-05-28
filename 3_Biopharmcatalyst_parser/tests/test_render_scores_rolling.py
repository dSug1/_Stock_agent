"""Rolling-view behaviour for the M6 catalyst_scores renderer."""
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
        "render_scores", SCRIPTS / "3_6_render_scores.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(db_path, snap, ticker, *, date_min=None, date_max=None, hard_pass=1,
          fail_reasons=None, composite=50.0, drug="DrugX", nct="NCT01",
          next_type="Interim Data"):
    from database.db import get_connection
    conn = get_connection(db_path)
    snap_iso = snap.isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO catalyst_snapshots
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
         name, stage, market_cap_usd)
        VALUES (?, ?, ?, ?, ?, ?, 'phase2', 5e8)""",
        (snap_iso, ticker, drug, nct, next_type, ticker + " Co"),
    )
    if date_min and date_max:
        conn.execute(
            """INSERT OR REPLACE INTO catalyst_timing
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
             date_min, date_max, precision_tier, source_lane,
             computed_at, rules_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'conference', 'conference', ?, 'v1.0')""",
            (snap_iso, ticker, drug, nct, next_type, date_min, date_max,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
    conn.execute(
        """INSERT OR REPLACE INTO catalyst_scores
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
         hard_pass, fail_reasons, timing_bucket, composite_score,
         insider_score, momentum_score, fund_accumulation_score,
         computed_at, rules_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, 'v1.0:test')""",
        (snap_iso, ticker, drug, nct, next_type,
         hard_pass, fail_reasons,
         "catalyst_date_defined" if hard_pass else None,
         composite if hard_pass else None,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def test_rolling_returns_latest_score_per_catalyst(tmp_path, monkeypatch):
    r = _load_renderer()
    db_path = tmp_path / "biotech.db"
    # Catalyst SAMEROW: scored in both snapshots, latest should win
    _seed(db_path, date(2026, 5, 27), "SAMEROW",
          date_min="2026-07-01", date_max="2026-07-31", composite=40.0)
    _seed(db_path, date(2026, 5, 28), "SAMEROW",
          date_min="2026-07-01", date_max="2026-07-31", composite=80.0)
    # Catalyst V3ONLY: only in v3, window still in future
    _seed(db_path, date(2026, 5, 27), "V3ONLY",
          date_min="2026-08-01", date_max="2026-08-31", composite=65.0)
    # Materialized: window entirely past
    _seed(db_path, date(2026, 5, 27), "MAT",
          date_min="2026-05-20", date_max="2026-05-24", composite=70.0)

    import database.db as db_mod
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", db_path)

    out_dir = tmp_path / "Outputs"
    _, data_path, _ = r.render(out_dir=out_dir)
    payload = json.loads(
        data_path.read_text(encoding="utf-8")
        [len("window.__DATA = "):].rstrip().rstrip(";")
    )
    by_ticker = {row["ticker"]: row for row in payload["rows"]}
    assert set(by_ticker) == {"SAMEROW", "V3ONLY"}, "MAT materialized → excluded"
    assert by_ticker["SAMEROW"]["composite_score"] == 80.0
    assert by_ticker["SAMEROW"]["snapshot_date"] == "2026-05-28"
    assert by_ticker["V3ONLY"]["composite_score"] == 65.0
    assert by_ticker["V3ONLY"]["snapshot_date"] == "2026-05-27"
    assert payload["view_mode"] == "rolling"
    assert payload["materialized_dropped"] == 1
    assert payload["snapshots_covered"] == ["2026-05-27", "2026-05-28"]


def test_single_snapshot_mode(tmp_path, monkeypatch):
    r = _load_renderer()
    db_path = tmp_path / "biotech.db"
    _seed(db_path, date(2026, 5, 27), "A",
          date_min="2026-07-01", date_max="2026-07-31")
    _seed(db_path, date(2026, 5, 28), "B",
          date_min="2026-07-01", date_max="2026-07-31")

    import database.db as db_mod
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", db_path)

    _, data_path, _ = r.render(snapshot_date=date(2026, 5, 27),
                               out_dir=tmp_path / "Outputs")
    payload = json.loads(
        data_path.read_text(encoding="utf-8")
        [len("window.__DATA = "):].rstrip().rstrip(";")
    )
    assert payload["view_mode"] == "single"
    assert {row["ticker"] for row in payload["rows"]} == {"A"}


def test_hard_pass_zero_with_h4_excluded_when_materialized(tmp_path, monkeypatch):
    """A hard_pass=0 row with date_max in the past should NOT appear
    in the rolling view (materialized)."""
    r = _load_renderer()
    db_path = tmp_path / "biotech.db"
    _seed(db_path, date(2026, 5, 28), "ALIVE",  # most recent snap
          date_min="2026-07-01", date_max="2026-07-31",
          hard_pass=0, fail_reasons="H1", composite=None)
    _seed(db_path, date(2026, 5, 27), "DEAD",
          date_min="2026-05-20", date_max="2026-05-24",
          hard_pass=0, fail_reasons="H1,H4", composite=None)
    # need to ensure ALIVE's snapshot is the max so effective_today=2026-05-28
    _seed(db_path, date(2026, 5, 28), "ALIVE",
          date_min="2026-07-01", date_max="2026-07-31",
          hard_pass=0, fail_reasons="H1", composite=None)

    import database.db as db_mod
    monkeypatch.setattr(db_mod, "DEFAULT_DB_PATH", db_path)

    _, data_path, _ = r.render(out_dir=tmp_path / "Outputs")
    payload = json.loads(
        data_path.read_text(encoding="utf-8")
        [len("window.__DATA = "):].rstrip().rstrip(";")
    )
    tickers = {row["ticker"] for row in payload["rows"]}
    # ALIVE stays (hard_pass=0 but future window — still in scope for excluded tab)
    # DEAD drops (materialized)
    assert "ALIVE" in tickers
    assert "DEAD" not in tickers
