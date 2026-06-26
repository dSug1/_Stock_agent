"""Tests for the OD-2 forward archive (db migration v2 + archive module).

Uses an injected http_get; no network. Run from inside 5_Hype_parser/:
    PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/ -q
"""

import pytest

from hype_parser import archive, db, registry


def _conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def _seed(conn):
    registry.seed_from_config(conn, {"sources": [
        {"source_id": "award_fwd", "name": "Award", "edge_type": "awards",
         "diffusion_position": "leading", "signal_type": "threshold_event",
         "history_availability": "forward_only", "url": "https://example.com/award"},
        {"source_id": "fda_nourl", "name": "FDA BTD", "edge_type": "fda",
         "diffusion_position": "bridge", "signal_type": "threshold_event",
         "history_availability": "forward_only", "url": ""},
        {"source_id": "arxiv", "name": "arXiv", "edge_type": "science",
         "diffusion_position": "leading", "signal_type": "volume",
         "history_availability": "queryable", "url": "https://arxiv.org"},
        {"source_id": "disabled_fwd", "name": "Off", "edge_type": "awards",
         "diffusion_position": "leading", "signal_type": "threshold_event",
         "history_availability": "forward_only", "url": "https://x", "enabled": 0},
    ]})


def test_schema_v2(tmp_path):
    conn = _conn(tmp_path)
    assert db.current_version(conn) >= 2          # source_snapshots landed at v2
    cols = {r[1] for r in conn.execute("PRAGMA table_info(source_snapshots)")}
    assert {"source_id", "content_hash", "content", "changed", "error"} <= cols
    conn.close()


def test_first_snapshot_stores_content(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    src = dict(conn.execute("SELECT * FROM sources WHERE source_id='award_fwd'").fetchone())
    res = archive.snapshot_source(conn, src, http_get=lambda url: (200, b"<html>v1</html>"))
    assert res["status"] == "ok" and res["changed"] is True
    row = conn.execute("SELECT * FROM source_snapshots WHERE source_id='award_fwd'").fetchone()
    assert row["content"] == "<html>v1</html>"
    assert row["http_status"] == 200 and row["bytes"] == 15
    conn.close()


def test_unchanged_snapshot_skips_content(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    src = dict(conn.execute("SELECT * FROM sources WHERE source_id='award_fwd'").fetchone())
    get = lambda url: (200, b"same-body")
    archive.snapshot_source(conn, src, http_get=get)
    res2 = archive.snapshot_source(conn, src, http_get=get)
    assert res2["changed"] is False
    rows = conn.execute(
        "SELECT content, changed FROM source_snapshots WHERE source_id='award_fwd' "
        "ORDER BY snapshot_id"
    ).fetchall()
    assert len(rows) == 2                       # cadence row still written
    assert rows[0]["content"] == "same-body" and rows[0]["changed"] == 1
    assert rows[1]["content"] is None and rows[1]["changed"] == 0
    conn.close()


def test_changed_content_stored_again(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    src = dict(conn.execute("SELECT * FROM sources WHERE source_id='award_fwd'").fetchone())
    archive.snapshot_source(conn, src, http_get=lambda url: (200, b"v1"))
    res = archive.snapshot_source(conn, src, http_get=lambda url: (200, b"v2-new-taxonomy"))
    assert res["changed"] is True
    last = conn.execute(
        "SELECT content FROM source_snapshots WHERE source_id='award_fwd' "
        "ORDER BY snapshot_id DESC LIMIT 1"
    ).fetchone()
    assert last["content"] == "v2-new-taxonomy"
    conn.close()


def test_no_url_recorded(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    src = dict(conn.execute("SELECT * FROM sources WHERE source_id='fda_nourl'").fetchone())
    res = archive.snapshot_source(conn, src)        # no http_get needed; short-circuits
    assert res["status"] == "skipped_no_url"
    row = conn.execute("SELECT * FROM source_snapshots WHERE source_id='fda_nourl'").fetchone()
    assert row["error"] == "no url registered" and row["content"] is None
    conn.close()


def test_fetch_error_is_failopen(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    src = dict(conn.execute("SELECT * FROM sources WHERE source_id='award_fwd'").fetchone())

    def boom(url):
        raise TimeoutError("slow")

    res = archive.snapshot_source(conn, src, http_get=boom)
    assert res["status"] == "error"
    row = conn.execute("SELECT * FROM source_snapshots WHERE source_id='award_fwd'").fetchone()
    assert "TimeoutError" in row["error"] and row["content"] is None
    conn.close()


def test_archive_forward_only_scopes_and_failopen(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    calls = {"n": 0}

    def get(url):
        calls["n"] += 1
        if "award" in url:
            raise ConnectionError("down")
        return (200, b"ok")

    summary = archive.archive_forward_only(conn, http_get=get)
    # only enabled forward_only sources: award_fwd (err) + fda_nourl (no url). arxiv excluded
    # (queryable); disabled_fwd excluded (enabled=0).
    assert summary["attempted"] == 2
    assert summary["errors"] == 1 and summary["skipped_no_url"] == 1
    assert calls["n"] == 1                      # fda_nourl short-circuits before http_get
    touched = {r["source_id"] for r in summary["results"]}
    assert touched == {"award_fwd", "fda_nourl"}
    conn.close()


def test_forward_only_excludes_queryable(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    ids = {r["source_id"] for r in archive.forward_only_sources(conn)}
    assert "arxiv" not in ids and "disabled_fwd" not in ids
    assert ids == {"award_fwd", "fda_nourl"}
    conn.close()


def test_snapshot_stats(tmp_path):
    conn = _conn(tmp_path)
    _seed(conn)
    src = dict(conn.execute("SELECT * FROM sources WHERE source_id='award_fwd'").fetchone())
    archive.snapshot_source(conn, src, http_get=lambda url: (200, b"a"))
    archive.snapshot_source(conn, src, http_get=lambda url: (200, b"b"))
    stats = {r["source_id"]: r for r in archive.snapshot_stats(conn)}
    assert stats["award_fwd"]["n_snapshots"] == 2
    assert stats["award_fwd"]["n_revisions"] == 2
    conn.close()
