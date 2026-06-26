"""Tests for the M1 source registry (db + registry + the real seed config).

Run from inside 5_Hype_parser/:
    PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/ -q
"""

import json
from pathlib import Path

import pytest

from hype_parser import db, registry

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"


def _conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def _sample():
    return {
        "sources": [
            {"source_id": "arxiv", "name": "arXiv", "edge_type": "science",
             "tier": "tier0", "diffusion_position": "leading", "signal_type": "volume",
             "history_availability": "queryable"},
            {"source_id": "fda_btd", "name": "FDA BTD", "edge_type": "fda",
             "tier": "tier0", "diffusion_position": "bridge",
             "signal_type": "threshold_event", "history_availability": "forward_only",
             "jury_credibility": "high"},
            {"source_id": "gdelt", "name": "GDELT", "edge_type": "denominator",
             "diffusion_position": "denominator", "signal_type": "volume",
             "history_availability": "queryable", "enabled": 0},
        ]
    }


def test_schema_version(tmp_path):
    conn = _conn(tmp_path)
    assert db.current_version(conn) == db.SCHEMA_VERSION
    conn.close()


def test_seed_inserts_and_defaults(tmp_path):
    conn = _conn(tmp_path)
    res = registry.seed_from_config(conn, _sample())
    assert res == {"inserted": 3, "updated": 0, "skipped": 0}
    row = conn.execute("SELECT * FROM sources WHERE source_id='arxiv'").fetchone()
    assert row["scrapeability_verified"] == 0      # default, claimed-not-verified
    assert row["enabled"] == 1                       # default
    assert row["add_date"] and row["created_at"]     # stamped
    conn.close()


def test_seed_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, _sample())
    res2 = registry.seed_from_config(conn, _sample())
    assert res2["inserted"] == 0 and res2["skipped"] == 3
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 3
    conn.close()


def test_seed_update_mode(tmp_path):
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, _sample())
    cfg = _sample()
    cfg["sources"][0]["name"] = "arXiv (renamed)"
    res = registry.seed_from_config(conn, cfg, update=True)
    assert res["updated"] == 3
    name = conn.execute("SELECT name FROM sources WHERE source_id='arxiv'").fetchone()[0]
    assert name == "arXiv (renamed)"
    conn.close()


def test_list_filters(tmp_path):
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, _sample())
    assert len(registry.list_sources(conn)) == 2          # gdelt disabled -> excluded
    assert len(registry.list_sources(conn, enabled_only=False)) == 3
    sci = registry.list_sources(conn, edge_type="science")
    assert [r["source_id"] for r in sci] == ["arxiv"]
    conn.close()


def test_set_verified(tmp_path):
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, _sample())
    registry.set_verified(conn, "arxiv", True)
    assert conn.execute(
        "SELECT scrapeability_verified FROM sources WHERE source_id='arxiv'"
    ).fetchone()[0] == 1
    with pytest.raises(KeyError):
        registry.set_verified(conn, "nope", True)
    conn.close()


def test_invalid_enum_rejected(tmp_path):
    conn = _conn(tmp_path)
    bad = {"sources": [{"source_id": "x", "edge_type": "science",
                        "diffusion_position": "sideways"}]}
    with pytest.raises(ValueError):
        registry.seed_from_config(conn, bad)
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0  # all-or-nothing
    conn.close()


def test_missing_edge_type_rejected(tmp_path):
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        registry.seed_from_config(conn, {"sources": [{"source_id": "x"}]})
    conn.close()


def test_freeze_snapshots_enabled(tmp_path):
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, _sample())
    v = registry.freeze(conn, notes="first")
    assert v["label"] == "v1"
    assert v["n_sources"] == 2                        # only enabled sources
    got = registry.get_version(conn, "v1")
    assert {s["source_id"] for s in got["manifest"]} == {"arxiv", "fda_btd"}
    conn.close()


def test_freeze_is_immutable(tmp_path):
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, _sample())
    registry.freeze(conn, label="snap")
    # mutate a source after freezing
    cfg = _sample()
    cfg["sources"][0]["name"] = "CHANGED"
    registry.seed_from_config(conn, cfg, update=True)
    snap = registry.get_version(conn, "snap")
    names = {s["source_id"]: s["name"] for s in snap["manifest"]}
    assert names["arxiv"] == "arXiv"                  # snapshot unchanged
    conn.close()


def test_freeze_autolabels_increment(tmp_path):
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, _sample())
    assert registry.freeze(conn)["label"] == "v1"
    assert registry.freeze(conn)["label"] == "v2"
    assert [v["label"] for v in registry.list_versions(conn)] == ["v1", "v2"]
    conn.close()


def test_freeze_hash_deterministic_and_distinct(tmp_path):
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, _sample())
    h1 = registry.freeze(conn, label="a")["content_hash"]
    h2 = registry.freeze(conn, label="b")["content_hash"]
    assert h1 == h2                                   # same content -> same hash
    registry.set_verified(conn, "arxiv", True)
    h3 = registry.freeze(conn, label="c")["content_hash"]
    assert h3 != h1                                   # content changed -> hash changed
    conn.close()


# ---- integration: the real seed config loads and is internally consistent ----

def test_real_config_seeds_clean(tmp_path):
    conn = _conn(tmp_path)
    cfg = registry.load_config(REPO_CONFIG)
    res = registry.seed_from_config(conn, cfg)
    assert res["inserted"] >= 50            # the registry is the moat; expect a full seed
    assert res["skipped"] == 0 and res["updated"] == 0
    # no duplicate source_ids in the YAML
    ids = [s["source_id"] for s in cfg["sources"]]
    assert len(ids) == len(set(ids))
    # every source carries the load-bearing classification fields
    for r in registry.list_sources(conn, enabled_only=False):
        assert r["diffusion_position"] in {"leading", "bridge", "denominator"}
        assert r["signal_type"] in {"threshold_event", "volume"}
        assert r["history_availability"] in {"queryable", "forward_only"}
    conn.close()


def test_real_config_has_both_history_classes(tmp_path):
    """OD-2: the registry must distinguish panel-reconstructable from forward-only sources."""
    conn = _conn(tmp_path)
    registry.seed_from_config(conn, registry.load_config(REPO_CONFIG))
    rows = registry.list_sources(conn, enabled_only=False)
    hist = {r["history_availability"] for r in rows}
    assert hist == {"queryable", "forward_only"}
    # denominator sources must exist (diffusion_ratio needs a bottom)
    assert any(r["diffusion_position"] == "denominator" for r in rows)
    conn.close()
