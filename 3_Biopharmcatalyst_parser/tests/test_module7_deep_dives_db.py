"""Module 7 — claude_deep_dives.db schema + write helpers unit tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.deep_dives_db import (  # noqa: E402
    close_run,
    db_connect,
    init_deep_dives_db,
    latest_deep_dive_per_catalyst,
    open_run,
    update_run_batch_id,
    upsert_deep_dive_row,
    upsert_web_search_cache_row,
    write_error_row,
)


def test_schema_creates_four_tables(tmp_path: Path):
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    with db_connect(db) as cx:
        rows = cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r["name"] for r in rows}
    expected = {"deep_dives", "deep_dive_runs", "deep_dive_errors", "web_search_cache"}
    assert expected.issubset(names)


def test_init_is_idempotent(tmp_path: Path):
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    init_deep_dives_db(db)        # second call must not raise


def test_open_run_returns_increasing_ids(tmp_path: Path):
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    with db_connect(db) as cx:
        r1 = open_run(cx, snapshot_date="2026-08-04", prompt_version="m7-v1:abc",
                      model="claude-opus-4-7", mode="sync", feed_size=3,
                      gate_config={"hard_pass_only": True}, batch_id=None)
        r2 = open_run(cx, snapshot_date="2026-08-04", prompt_version="m7-v1:abc",
                      model="claude-opus-4-7", mode="batch", feed_size=10,
                      gate_config={"hard_pass_only": True}, batch_id="batch_42")
    assert r2 == r1 + 1


def test_update_run_batch_id(tmp_path: Path):
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    with db_connect(db) as cx:
        rid = open_run(cx, snapshot_date="2026-08-04", prompt_version="m7-v1:abc",
                       model="claude-opus-4-7", mode="batch", feed_size=3,
                       gate_config={}, batch_id=None)
        update_run_batch_id(cx, run_id=rid, batch_id="batch_99")
    with db_connect(db) as cx:
        row = cx.execute("SELECT batch_id FROM deep_dive_runs WHERE run_id=?",
                         (rid,)).fetchone()
    assert row["batch_id"] == "batch_99"


def test_close_run_writes_totals(tmp_path: Path):
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    with db_connect(db) as cx:
        rid = open_run(cx, snapshot_date="2026-08-04", prompt_version="m7-v1:abc",
                       model="claude-opus-4-7", mode="sync", feed_size=5,
                       gate_config={}, batch_id=None)
        close_run(cx, run_id=rid, wall_time_s=42.5,
                  input_tokens_total=120_000, output_tokens_total=5_000,
                  cache_read_tokens_total=80_000, cache_creation_tokens_total=10_000,
                  web_search_calls_total=30,
                  usd_cost_total=1.25, usd_cost_list_price=12.50)
    with db_connect(db) as cx:
        row = cx.execute("SELECT * FROM deep_dive_runs WHERE run_id=?", (rid,)).fetchone()
    assert row["wall_time_s"] == 42.5
    assert row["usd_cost_total"] == 1.25
    assert row["usd_cost_list_price"] == 12.50
    assert row["closed_at"] is not None


def test_upsert_deep_dive_replaces_on_pk_collision(tmp_path: Path):
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    pk = dict(snapshot_date="2026-08-04", ticker="TCRX",
              drug="TX45", nct_number="NCT06234567",
              next_catalyst_type="Topline Data", run_id=1)
    with db_connect(db) as cx:
        upsert_deep_dive_row(cx, dict(pk, p_clinical=0.4, p_final=0.40,
                                       expectancy_pct=5.0,
                                       expectancy_per_week_pct=0.5,
                                       prompt_version="m7-v1:abc",
                                       model="claude-opus-4-7",
                                       raw_text="raw#1"))
        upsert_deep_dive_row(cx, dict(pk, p_clinical=0.5, p_final=0.50,
                                       expectancy_pct=10.0,
                                       expectancy_per_week_pct=1.0,
                                       prompt_version="m7-v1:abc",
                                       model="claude-opus-4-7",
                                       raw_text="raw#2"))
        rows = cx.execute("SELECT * FROM deep_dives WHERE ticker='TCRX'").fetchall()
    assert len(rows) == 1
    assert rows[0]["p_final"] == 0.50
    assert rows[0]["raw_text"] == "raw#2"


def test_write_error_row(tmp_path: Path):
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    with db_connect(db) as cx:
        rid = open_run(cx, snapshot_date="2026-08-04", prompt_version="m7-v1:abc",
                       model="claude-opus-4-7", mode="sync", feed_size=1,
                       gate_config={}, batch_id=None)
        write_error_row(cx, run_id=rid, ticker="HAELO",
                        snapshot_date="2026-08-04",
                        error_kind="catalyst_already_passed",
                        error_detail="model reports catalyst_passed_already=true",
                        raw_text="```json\n{...}\n```")
        rows = cx.execute("SELECT * FROM deep_dive_errors").fetchall()
    assert len(rows) == 1
    assert rows[0]["error_kind"] == "catalyst_already_passed"


def test_upsert_web_search_cache_coalesces_nulls(tmp_path: Path):
    """COALESCE behavior in ON CONFLICT must NOT clobber a populated field with NULL."""
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    with db_connect(db) as cx:
        upsert_web_search_cache_row(
            cx, url="https://example.com/x", ticker="TCRX",
            snapshot_date="2026-08-04", run_id=1,
            search_query="TX45 PAH phase 2", title="Phase 2 readout",
            content="Body text…", domain="example.com",
            published_date="2026-05-12",
        )
        # Second upsert with NULL ticker should NOT clobber existing 'TCRX'.
        upsert_web_search_cache_row(
            cx, url="https://example.com/x", ticker=None,
            snapshot_date="2026-08-04", run_id=2,
            search_query=None, title=None, content=None,
            domain=None, published_date=None,
        )
        row = cx.execute("SELECT * FROM web_search_cache WHERE url=?",
                         ("https://example.com/x",)).fetchone()
    assert row["ticker"] == "TCRX"
    assert row["title"] == "Phase 2 readout"
    assert row["run_id"] == 2                  # run_id WAS updated


def test_latest_deep_dive_per_catalyst_picks_max_run(tmp_path: Path):
    db = tmp_path / "deep_dives.db"
    init_deep_dives_db(db)
    base = dict(snapshot_date="2026-08-04", ticker="TCRX",
                drug="TX45", nct_number="NCT06234567",
                next_catalyst_type="Topline Data",
                prompt_version="m7-v1:abc", model="claude-opus-4-7")
    with db_connect(db) as cx:
        upsert_deep_dive_row(cx, dict(base, run_id=1, p_final=0.40,
                                       expectancy_pct=1.0,
                                       expectancy_per_week_pct=0.1))
        upsert_deep_dive_row(cx, dict(base, run_id=3, p_final=0.50,
                                       expectancy_pct=5.0,
                                       expectancy_per_week_pct=0.5))
        upsert_deep_dive_row(cx, dict(base, run_id=2, p_final=0.45,
                                       expectancy_pct=2.0,
                                       expectancy_per_week_pct=0.2))
        out = latest_deep_dive_per_catalyst(cx, "2026-08-04")
    key = ("2026-08-04", "TCRX", "TX45", "NCT06234567", "Topline Data")
    assert key in out
    assert out[key]["run_id"] == 3
    assert out[key]["p_final"] == 0.50
