"""Module 5 acceptance — catalyst timing extraction.

§7.10 reference fixtures (today=2026-05-27) plus the edge cases from
§7.14, plus DB-level idempotency.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_compute_timing.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402
from module_5.compute import compute_timing_for_row, extract_text_match  # noqa: E402
from module_5.ingest import compute_timing_for_snapshot  # noqa: E402
from module_5.timing_rules import RULES_VERSION  # noqa: E402


TODAY = date(2026, 5, 27)


# ===== §7.10 fixtures =====================================================

@pytest.mark.parametrize(
    "fixture_id, conference, catalyst_date, catalyst_text, "
    "exp_min, exp_max, exp_tier, exp_lane",
    [
        # 1. Specific company-disclosed PDUFA date (Lane 2).
        (
            "1_specific_pdufa",
            None,
            date(2026, 5, 24),
            "PDUFA date set for May 24, 2026",
            date(2026, 5, 24), date(2026, 5, 24),
            "specific", "catalyst_date_specific",
        ),
        # 2. Conference-tied (Lane 1 beats Lane 2 even when Catalyst Date present).
        (
            "2_conference_eha26",
            "European Hematology Association Congress (EHA26) 11/06/2026 ET - 14/06/2026 ET Conference Calendar",
            date(2026, 6, 13),
            "Data presentation at EHA26",
            date(2026, 6, 11), date(2026, 6, 14),
            "conference", "conference",
        ),
        # 3. YE 2026 text + placeholder date (Lane 3 text wins, not Lane 3b).
        (
            "3_year_end_text",
            None,
            date(2026, 12, 31),
            "Phase 1 SAD & MAD data due by YE 2026",
            date(2026, 1, 1), date(2026, 12, 31),
            "year", "text_parse",
        ),
        # 4. Two halves in text — earliest future wins (2H 2026 < early 2027).
        (
            "4_half_then_relative",
            None,
            date(2026, 12, 31),
            "Pilot trial topline results due in 2H 2026, with a pivotal trial due in early 2027",
            date(2026, 7, 1), date(2026, 12, 31),
            "half", "text_parse",
        ),
        # 5. Quarter from text "4Q 2026".
        (
            "5_quarter_4Q",
            None,
            date(2026, 12, 31),
            "Phase 1 study ongoing with data expected 4Q 2026",
            date(2026, 10, 1), date(2026, 12, 31),
            "quarter", "text_parse",
        ),
        # 6. Past date stripped, year-only future kept (Lane 3 year).
        (
            "6_past_specific_then_year",
            None,
            date(2026, 12, 31),
            "First patient dosed May 13, 2025. Data due in 2026.",
            date(2026, 1, 1), date(2026, 12, 31),
            "year", "text_parse",
        ),
        # 7. Conference empty, text empty, placeholder 30/06/2026 → Lane 3b half.
        (
            "7_placeholder_30jun_no_text",
            None,
            date(2026, 6, 30),
            "",
            date(2026, 1, 1), date(2026, 6, 30),
            "half", "catalyst_date_bucket",
        ),
        # 8. Month-year "June 2026" beats Lane 3b (text wins over bucket).
        (
            "8_month_text_beats_bucket",
            None,
            date(2026, 6, 30),
            "Topline data expected June 2026",
            date(2026, 6, 1), date(2026, 6, 30),
            "month", "text_parse",
        ),
    ],
)
def test_spec_fixtures(
    fixture_id, conference, catalyst_date, catalyst_text,
    exp_min, exp_max, exp_tier, exp_lane,
):
    result = compute_timing_for_row(
        conference=conference,
        catalyst_date=catalyst_date,
        catalyst_text=catalyst_text,
        today=TODAY,
    )
    assert result.date_min == exp_min, f"{fixture_id}: date_min"
    assert result.date_max == exp_max, f"{fixture_id}: date_max"
    assert result.precision_tier == exp_tier, f"{fixture_id}: tier"
    assert result.source_lane == exp_lane, f"{fixture_id}: lane"


# ===== §7.14 edge cases ===================================================


def test_multiple_temporal_refs_earliest_future_wins():
    # Past + two futures; earliest future = "Q3 2026"
    text = "First patient May 2025. Topline Q3 2026, BLA filed in Q1 2027."
    tm = extract_text_match(text, TODAY)
    assert tm is not None
    assert tm.date_min == date(2026, 7, 1)
    assert tm.date_max == date(2026, 9, 30)
    assert tm.precision_tier == "quarter"


def test_only_past_dates_returns_none():
    text = "Phase 1 data reported January 2024. Phase 2 enrollment closed in 2023."
    assert extract_text_match(text, TODAY) is None


def test_conference_unparseable_falls_through_to_lane2():
    # Conference text present but no DD/MM/YYYY range → Lane 2 wins.
    result = compute_timing_for_row(
        conference="ASCO Genitourinary Cancers Symposium",
        catalyst_date=date(2026, 7, 15),
        catalyst_text="",
        today=TODAY,
    )
    assert result.source_lane == "catalyst_date_specific"
    assert result.date_min == date(2026, 7, 15)


def test_all_lanes_fail_unknown():
    result = compute_timing_for_row(
        conference=None,
        catalyst_date=None,
        catalyst_text="",
        today=TODAY,
    )
    assert result.precision_tier == "unknown"
    assert result.source_lane == "unknown"
    assert result.date_min is None
    assert result.date_max is None


def test_placeholder_date_plus_text_match_text_wins():
    # Catalyst Date is placeholder; text has a real quarter match.
    result = compute_timing_for_row(
        conference=None,
        catalyst_date=date(2026, 12, 31),
        catalyst_text="Q3 2026 data expected",
        today=TODAY,
    )
    assert result.source_lane == "text_parse"
    assert result.precision_tier == "quarter"
    assert result.date_min == date(2026, 7, 1)


def test_specific_date_beats_earlier_text():
    # Lane 2 wins over Lane 3 even if text mentions an earlier date.
    result = compute_timing_for_row(
        conference=None,
        catalyst_date=date(2026, 7, 15),
        catalyst_text="Earlier guidance referenced June 2026",
        today=TODAY,
    )
    assert result.source_lane == "catalyst_date_specific"
    assert result.date_min == date(2026, 7, 15)


def test_year_only_does_not_match_context_phrase():
    # "as we noted in 2024" — YEAR_ONLY trigger 'in' is present, but 2024 is past.
    text = "As we noted in 2024, the program continues."
    assert extract_text_match(text, TODAY) is None


def test_two_halves_different_years_picks_earlier_future():
    text = "Phase 2 readout 1H 2026 and Phase 3 readout 2H 2027"
    tm = extract_text_match(text, TODAY)
    assert tm is not None
    assert tm.date_min == date(2026, 1, 1)
    assert tm.date_max == date(2026, 6, 30)
    assert tm.precision_tier == "half"


def test_distant_past_catalyst_with_placeholder_falls_to_bucket():
    # Past date stripped from text matches → Lane 3 returns None → Lane 3b applies.
    result = compute_timing_for_row(
        conference=None,
        catalyst_date=date(2026, 12, 31),
        catalyst_text="Data reported January 2024",  # past
        today=TODAY,
    )
    assert result.source_lane == "catalyst_date_bucket"
    assert result.precision_tier == "year"


# ===== regex coverage smoke tests ========================================
#
# These use EARLY_TODAY = 2026-01-01 so every 2026-anchored match stays
# in the "future" set (the §7.10 fixtures' TODAY=2026-05-27 would strip
# Q1 and early-May matches as past, which is the right *resolver*
# behaviour but the wrong test setup for pattern coverage).

EARLY_TODAY = date(2026, 1, 1)


@pytest.mark.parametrize("text, exp_tier, exp_min, exp_max", [
    ("Data expected Q3 2026", "quarter", date(2026, 7, 1), date(2026, 9, 30)),
    ("Data expected 3Q 2026", "quarter", date(2026, 7, 1), date(2026, 9, 30)),
    ("Data expected Q3/2026", "quarter", date(2026, 7, 1), date(2026, 9, 30)),
    ("Data expected third quarter 2026", "quarter", date(2026, 7, 1), date(2026, 9, 30)),
    ("Data expected 1H 2026", "half", date(2026, 1, 1), date(2026, 6, 30)),
    ("Data expected H1 2026", "half", date(2026, 1, 1), date(2026, 6, 30)),
    ("Data expected first half of 2026", "half", date(2026, 1, 1), date(2026, 6, 30)),
    ("Data expected 2H 2026", "half", date(2026, 7, 1), date(2026, 12, 31)),
    ("Data expected early 2026", "quarter", date(2026, 1, 1), date(2026, 3, 31)),
    ("Data expected mid 2026", "half", date(2026, 4, 1), date(2026, 9, 30)),
    ("Data expected late 2026", "quarter", date(2026, 10, 1), date(2026, 12, 31)),
    ("Data expected YE 2026", "year", date(2026, 1, 1), date(2026, 12, 31)),
    ("Data expected FY 2026", "year", date(2026, 1, 1), date(2026, 12, 31)),
    ("Data expected by end of 2026", "year", date(2026, 1, 1), date(2026, 12, 31)),
    ("Topline data expected May 2026", "month", date(2026, 5, 1), date(2026, 5, 31)),
    ("Topline data expected May 24, 2026", "specific", date(2026, 5, 24), date(2026, 5, 24)),
    ("Topline data expected 24 May 2026", "specific", date(2026, 5, 24), date(2026, 5, 24)),
    ("Topline data expected in 2026", "year", date(2026, 1, 1), date(2026, 12, 31)),
])
def test_pattern_coverage(text, exp_tier, exp_min, exp_max):
    tm = extract_text_match(text, EARLY_TODAY)
    assert tm is not None, f"no match for {text!r}"
    assert tm.precision_tier == exp_tier
    assert tm.date_min == exp_min
    assert tm.date_max == exp_max


def test_specific_date_does_not_double_match_month_year():
    # "May 24, 2026" must NOT also produce a separate "May 2026" match
    # — span-overlap protection skips the lower-precision pattern.
    # Use EARLY_TODAY so the May 24 match isn't filtered as past.
    text = "PDUFA May 24, 2026"
    tm = extract_text_match(text, EARLY_TODAY)
    assert tm is not None
    assert tm.precision_tier == "specific"
    assert tm.date_min == date(2026, 5, 24)
    assert tm.date_max == date(2026, 5, 24)


# ===== DB-level integration ==============================================


def _seed_one_snapshot(conn, snapshot: date) -> None:
    """Seed a tiny catalyst_snapshots fixture: 3 rows covering all 5 lanes."""
    conn.executescript(f"""
        INSERT INTO catalyst_snapshots (
            snapshot_date, ticker, drug, nct_number, next_catalyst_type,
            stage, conference, catalyst_date, catalyst_text
        ) VALUES
        -- Lane 1: conference
        ('{snapshot}', 'AAA', 'DrugA', 'NCT1', 'Conference Presentation',
         'phase2',
         'EHA26 11/06/2026 ET - 14/06/2026 ET Conference Calendar',
         '2026-06-13',
         'Data at EHA26'),
        -- Lane 2: specific date
        ('{snapshot}', 'BBB', 'DrugB', 'NCT2', 'Regulatory Decision',
         'phase3', NULL, '2026-05-24', 'PDUFA approaches'),
        -- Lane 3: text-parsed quarter
        ('{snapshot}', 'CCC', 'DrugC', 'NCT3', 'Interim Data',
         'phase1', NULL, '2026-12-31', 'Topline data Q3 2026'),
        -- Lane 3b: bucket only
        ('{snapshot}', 'DDD', 'DrugD', 'NCT4', 'Initial Data',
         'phase1', NULL, '2026-06-30', ''),
        -- Unknown: nothing
        ('{snapshot}', 'EEE', 'DrugE', 'NCT5', 'Initial Data',
         'phase1', NULL, NULL, '');
    """)
    conn.commit()


def test_db_pipeline_5_rows_5_lanes(tmp_path):
    db = tmp_path / "m5.db"
    c = get_connection(db)
    try:
        _seed_one_snapshot(c, date(2026, 5, 27))
        stats = compute_timing_for_snapshot(date(2026, 5, 27), c)
        assert stats.status == "success"
        assert stats.rows_in == 5
        assert stats.rows_inserted == 5
        assert stats.rows_updated == 0

        rows = list(c.execute(
            "SELECT ticker, source_lane, precision_tier, date_min, date_max, rules_version "
            "FROM catalyst_timing ORDER BY ticker"
        ))
        by_ticker = {r["ticker"]: r for r in rows}
        assert by_ticker["AAA"]["source_lane"] == "conference"
        assert by_ticker["BBB"]["source_lane"] == "catalyst_date_specific"
        assert by_ticker["CCC"]["source_lane"] == "text_parse"
        assert by_ticker["DDD"]["source_lane"] == "catalyst_date_bucket"
        assert by_ticker["EEE"]["source_lane"] == "unknown"
        assert by_ticker["EEE"]["date_min"] is None
        assert by_ticker["EEE"]["date_max"] is None

        # rules_version persisted
        for r in rows:
            assert r["rules_version"] == RULES_VERSION
    finally:
        c.close()


def test_db_pipeline_idempotent_rerun(tmp_path):
    db = tmp_path / "m5_idemp.db"
    c = get_connection(db)
    try:
        _seed_one_snapshot(c, date(2026, 5, 27))
        compute_timing_for_snapshot(date(2026, 5, 27), c)
        stats2 = compute_timing_for_snapshot(date(2026, 5, 27), c)
        assert stats2.rows_inserted == 0
        assert stats2.rows_updated == 5
        assert c.execute(
            "SELECT COUNT(*) FROM catalyst_timing"
        ).fetchone()[0] == 5
    finally:
        c.close()


def test_db_pipeline_writes_ingest_log(tmp_path):
    db = tmp_path / "m5_log.db"
    c = get_connection(db)
    try:
        _seed_one_snapshot(c, date(2026, 5, 27))
        compute_timing_for_snapshot(date(2026, 5, 27), c)
        log = c.execute(
            "SELECT module, status, rows_in, rows_inserted, input_ref "
            "FROM ingest_log WHERE module='compute_timing'"
        ).fetchone()
        assert log is not None
        assert log["status"] == "success"
        assert log["rows_in"] == 5
        assert log["rows_inserted"] == 5
        assert log["input_ref"] == "2026-05-27"
    finally:
        c.close()
