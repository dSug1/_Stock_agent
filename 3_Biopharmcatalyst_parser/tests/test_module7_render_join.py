"""Module 7 — render_join unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.deep_dives_db import (  # noqa: E402
    db_connect, init_deep_dives_db, upsert_deep_dive_row,
)
from module_7.render_join import (  # noqa: E402
    attach_deep_dive_payload, fetch_latest_deep_dive_map,
)


def _write(cx, **fields):
    base = {
        "snapshot_date": "2026-05-28", "ticker": "TCRX",
        "drug": "TX45", "nct_number": "NCT99000001",
        "next_catalyst_type": "Topline Data",
        "run_id": 1,
        "p_clinical": 0.42, "p_clinical_low": 0.30, "p_clinical_high": 0.55,
        "expected_move_on_hit_pct": 115.0,
        "expected_move_on_miss_pct": -68.0,
        "rnpv_total_usd": 2_000_000_000,
        "rnpv_per_share_usd": 46.84,
        "lead_indication": "PAH",
        "p_final": 0.49, "e_move_pct": 21.2,
        "weeks_to_catalyst_mid": 12,
        "expectancy_per_week_pct": 1.8,
        "prompt_version": "m7-v1:abc1234",
        "model": "claude-opus-4-7",
        "raw_text": "raw",
        "rnpv_by_indication_json": '[{"indication": "PAH", "pos_base_rate": 0.28, "pos_adjusted": 0.42}]',
        "drug_profile_json": '{"moa": "ligand trap"}',
        "key_risks_json": '["risk 1", "risk 2"]',
    }
    base.update(fields)
    upsert_deep_dive_row(cx, base)


def test_returns_empty_when_db_missing(tmp_path):
    out = fetch_latest_deep_dive_map(tmp_path / "missing.db")
    assert out == {}


def test_returns_empty_when_db_present_but_empty(tmp_path):
    init_deep_dives_db(tmp_path / "dd.db")
    out = fetch_latest_deep_dive_map(tmp_path / "dd.db")
    assert out == {}


def test_returns_one_row(tmp_path):
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        _write(cx)
    out = fetch_latest_deep_dive_map(tmp_path / "dd.db")
    # D38 bug fix (2026-06-04) — key dropped snapshot_date.
    key = ("TCRX", "TX45", "NCT99000001", "Topline Data")
    assert key in out
    assert out[key]["p_final"] == 0.49
    assert out[key]["expectancy_per_week_pct"] == 1.8


def test_decodes_json_blocks(tmp_path):
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        _write(cx)
    out = fetch_latest_deep_dive_map(tmp_path / "dd.db")
    key = ("TCRX", "TX45", "NCT99000001", "Topline Data")
    dd = out[key]
    # rnpv_by_indication must be a parsed list, not raw JSON text
    assert isinstance(dd["rnpv_by_indication"], list)
    assert dd["rnpv_by_indication"][0]["indication"] == "PAH"
    assert isinstance(dd["drug_profile"], dict)
    assert dd["drug_profile"]["moa"] == "ligand trap"
    assert dd["key_risks"] == ["risk 1", "risk 2"]


def test_picks_max_run_id_per_pk(tmp_path):
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        _write(cx, run_id=1, p_final=0.40)
        _write(cx, run_id=3, p_final=0.50)
        _write(cx, run_id=2, p_final=0.45)
    out = fetch_latest_deep_dive_map(tmp_path / "dd.db")
    key = ("TCRX", "TX45", "NCT99000001", "Topline Data")
    assert out[key]["run_id"] == 3
    assert out[key]["p_final"] == 0.50


def test_only_snapshot_date_filter(tmp_path):
    """The only_snapshot_date arg still filters the SOURCE rows; the
    resulting map's keys no longer carry snapshot_date though (D38 fix).
    """
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        _write(cx, snapshot_date="2026-05-27")
        _write(cx, snapshot_date="2026-05-28")
    only = fetch_latest_deep_dive_map(
        tmp_path / "dd.db", only_snapshot_date="2026-05-28",
    )
    assert len(only) == 1
    # Key is a 4-tuple now; snapshot_date isn't in it but the underlying
    # row IS from 2026-05-28 per the filter.
    key = next(iter(only.keys()))
    assert len(key) == 4


def test_pk_match_works_across_different_snapshot_dates(tmp_path):
    """D38 bug-fix regression test (2026-06-04).

    A deep_dive written with snapshot_date X must be discoverable from a
    catalyst_scores rolling-view row at snapshot_date Y, as long as the
    (ticker, drug, nct, type) PK matches. Reproduces the v6-ingest bug
    where prior snapshots' deep_dives went dark after the new snapshot
    rolled the rolling-view forward.
    """
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        _write(cx, snapshot_date="2026-05-28", run_id=4, p_final=0.50)
    dd_map = fetch_latest_deep_dive_map(tmp_path / "dd.db")
    # Render-time row at a DIFFERENT snapshot_date but same PK.
    rows = [{
        "snapshot_date": "2026-06-01",  # newer snapshot
        "ticker": "TCRX", "drug": "TX45",
        "nct_number": "NCT99000001",
        "next_catalyst_type": "Topline Data",
    }]
    attach_deep_dive_payload(rows, dd_map)
    assert rows[0]["deep_dive"] is not None, "deep_dive should follow PK regardless of snapshot_date"
    assert rows[0]["deep_dive"]["p_final"] == 0.50


def test_raw_text_NOT_in_payload(tmp_path):
    """raw_text would bloat the sidecar — must be excluded.
    (HTTP server serves it on demand via run_id.)"""
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        _write(cx)
    out = fetch_latest_deep_dive_map(tmp_path / "dd.db")
    payload = next(iter(out.values()))
    assert "raw_text" not in payload
    assert "raw_text_id" in payload                      # surrogate kept


def test_attach_deep_dive_payload_mutates_rows(tmp_path):
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        _write(cx, ticker="TCRX")
    dd_map = fetch_latest_deep_dive_map(tmp_path / "dd.db")
    rows = [
        {"snapshot_date": "2026-05-28", "ticker": "TCRX", "drug": "TX45",
         "nct_number": "NCT99000001", "next_catalyst_type": "Topline Data"},
        {"snapshot_date": "2026-05-28", "ticker": "OTHER", "drug": "Z",
         "nct_number": "NCT0", "next_catalyst_type": "Topline Data"},
    ]
    attach_deep_dive_payload(rows, dd_map)
    assert rows[0]["deep_dive"] is not None
    assert rows[0]["deep_dive"]["p_final"] == 0.49
    assert rows[1]["deep_dive"] is None
