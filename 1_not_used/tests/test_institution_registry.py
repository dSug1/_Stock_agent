from __future__ import annotations

from pathlib import Path

import pytest

from database.db import CURRENT_SCHEMA_VERSION, get_connection, init_db
from layer_minus1.institution_registry import (
    TIER_MULTIPLIERS,
    get_all_institutions,
    get_institution_by_name,
    get_institutions_by_tier,
    get_multiplier_for_institution,
    seed_institutions,
)


EXPECTED_TOTAL = 34

EXPECTED_BY_TIER = {
    "1A": 15,
    "1B": 5,
    "2A": 4,
    "2B": 4,
    "3":  4,
    "4":  2,
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "picker.db"
    init_db(p)
    return p


def test_institutions_table_exists(db_path: Path) -> None:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='institutions'"
    ).fetchone()
    assert row is not None


def test_all_institutions_present_after_seed(db_path: Path) -> None:
    conn = get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM institutions"
    ).fetchone()["c"]
    assert count == EXPECTED_TOTAL


def test_tier_1a_contains_exactly_fifteen(db_path: Path) -> None:
    assert len(get_institutions_by_tier("1A")) == 15


def test_tier_counts_match_spec(db_path: Path) -> None:
    for tier, expected in EXPECTED_BY_TIER.items():
        actual = len(get_institutions_by_tier(tier))
        assert actual == expected, (
            f"tier {tier}: expected {expected}, got {actual}"
        )


def test_tier_1a_multiplier_is_four_point_zero(db_path: Path) -> None:
    for inst in get_institutions_by_tier("1A"):
        assert inst["multiplier"] == 4.0


def test_tier_4_multiplier_is_exactly_zero(db_path: Path) -> None:
    for inst in get_institutions_by_tier("4"):
        assert inst["multiplier"] == 0.0


def test_all_multipliers_match_tier_table(db_path: Path) -> None:
    for inst in get_all_institutions():
        assert inst["multiplier"] == TIER_MULTIPLIERS[inst["tier"]]


def test_seed_is_idempotent(db_path: Path) -> None:
    conn = get_connection(db_path)
    seed_institutions(conn)
    seed_institutions(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM institutions"
    ).fetchone()["c"]
    assert count == EXPECTED_TOTAL


def test_get_institution_by_name_known(db_path: Path) -> None:
    inst = get_institution_by_name("Baker Bros. Advisors")
    assert inst is not None
    assert inst["tier"] == "1A"
    assert inst["multiplier"] == 4.0


def test_get_institution_by_name_unknown_returns_none(
    db_path: Path,
) -> None:
    assert get_institution_by_name("Nonexistent Fund Ltd.") is None


def test_get_multiplier_for_known(db_path: Path) -> None:
    assert get_multiplier_for_institution("Vanguard index") == 0.0
    assert (
        get_multiplier_for_institution("Berkshire Hathaway") == 3.5
    )


def test_get_multiplier_for_unknown_raises_keyerror(
    db_path: Path,
) -> None:
    with pytest.raises(KeyError):
        get_multiplier_for_institution("Nonexistent Fund Ltd.")


def test_get_institutions_by_tier_invalid_tier_raises(
    db_path: Path,
) -> None:
    with pytest.raises(ValueError):
        get_institutions_by_tier("1C")


def test_get_all_returns_total(db_path: Path) -> None:
    assert len(get_all_institutions()) == EXPECTED_TOTAL


def test_db_rows_match_registry(db_path: Path) -> None:
    conn = get_connection(db_path)
    db_rows = conn.execute(
        "SELECT name, tier, multiplier FROM institutions"
    ).fetchall()
    db_set = {(r["name"], r["tier"], r["multiplier"]) for r in db_rows}
    reg_set = {
        (i["name"], i["tier"], i["multiplier"])
        for i in get_all_institutions()
    }
    assert db_set == reg_set


def test_institution_names_unique(db_path: Path) -> None:
    names = [i["name"] for i in get_all_institutions()]
    assert len(names) == len(set(names))


def test_migration_on_existing_v1_database(tmp_path: Path) -> None:
    p = tmp_path / "picker.db"
    init_db(p)
    conn = get_connection(p)
    conn.execute("DELETE FROM schema_version")
    conn.execute(
        "INSERT INTO schema_version(version, applied_at) VALUES (1, ?)",
        ("2020-01-01T00:00:00+00:00",),
    )
    conn.execute("DELETE FROM institutions")
    conn.commit()
    conn.close()

    init_db(p)

    conn = get_connection(p)
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM institutions"
    ).fetchone()["c"]
    assert count == EXPECTED_TOTAL
    v = conn.execute(
        "SELECT MAX(version) AS v FROM schema_version"
    ).fetchone()["v"]
    assert v == CURRENT_SCHEMA_VERSION
