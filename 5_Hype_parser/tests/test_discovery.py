"""Tests for the theme-discovery jury-convergence module (schema v8; D22-D26). Offline + deterministic
— canned API payloads, the HashingEmbedder (no torch), and synthetic vectors for the clustering math.
"""

from pathlib import Path

import numpy as np
import pytest

from hype_parser import db
from hype_parser.discovery import convergence, funds, parsers, resolve, signals
from hype_parser.embed import HashingEmbedder
from hype_parser.registry import load_config

CFG = load_config(str(Path(__file__).resolve().parents[1] / "config" / "discovery.yaml"))


def _conn(tmp_path):
    return db.connect(tmp_path / "d.db")


# ─── schema ──────────────────────────────────────────────────────────────────

def test_schema_v8(tmp_path):
    conn = _conn(tmp_path)
    assert db.current_version(conn) >= 8
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(themes)")}
    assert {"horizon_years", "horizon_confidence", "discovered_from"} <= cols
    for tbl in ("jury_signals", "theme_convergence", "theme_orgs"):
        conn.execute(f"SELECT * FROM {tbl} LIMIT 1")        # exists


# ─── parsers ─────────────────────────────────────────────────────────────────

def test_batch_year_variants():
    assert parsers._batch_year("Winter 2021") == 2021
    assert parsers._batch_year("W21") == 2021
    assert parsers._batch_year("S20") == 2020
    assert parsers._batch_year(None) is None


def test_parse_yc_fields_and_since_year():
    payload = [
        {"name": "Acme Robotics", "batch": "W21", "one_liner": "warehouse robots", "tags": ["Robotics"]},
        {"name": "OldCo", "batch": "W10", "one_liner": "legacy"},
        {"name": "", "batch": "W22"},                       # dropped (no name)
    ]
    rows = parsers.parse_yc(payload, since_year=2016)
    assert len(rows) == 1
    r = rows[0]
    assert r["entity"] == "Acme Robotics" and r["entity_type"] == "company"
    assert r["diffusion_position"] == "leading" and r["year"] == 2021
    assert "warehouse robots" in r["item_text"] and "Robotics" in r["item_text"]


def test_fetch_yc_fail_open():
    def boom(url, timeout=30):
        raise OSError("network down")
    assert parsers.fetch_yc(http_get=boom) == []


def test_parse_nobel_is_denominator():
    payload = {"nobelPrizes": [{
        "awardYear": "2020", "category": {"en": "Chemistry"},
        "laureates": [{"fullName": {"en": "X Y"},
                       "motivation": {"en": "development of a method for genome editing"}}],
    }]}
    rows = parsers.parse_nobel(payload)
    assert len(rows) == 1
    assert rows[0]["diffusion_position"] == "denominator"
    assert rows[0]["entity_type"] == "person" and rows[0]["year"] == 2020
    assert "genome editing" in rows[0]["item_text"]


def test_extract_named_entities_from_html_filters_noise():
    html = ("<ul><li>Quantum Widgets Inc</li><li>Subscribe to our newsletter</li>"
            "<li>" + "x" * 200 + "</li></ul><h3>Photon Labs</h3>")
    items = parsers.extract_named_entities_from_html(html)
    assert "Quantum Widgets Inc" in items and "Photon Labs" in items
    assert not any("newsletter" in i.lower() for i in items)
    assert all(len(i) <= 140 for i in items)


def test_parse_snapshot_source_uses_latest(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO sources (source_id, name, edge_type, diffusion_position, "
                 "jury_credibility, add_date, created_at, updated_at) "
                 "VALUES ('rd_100','R&D 100','awards','leading','high','d','c','u')")
    conn.execute("INSERT INTO source_snapshots (source_id, fetched_at, content, changed) "
                 "VALUES ('rd_100','t','<ul><li>Nano Membrane</li></ul>',1)")
    conn.commit()
    rows = parsers.parse_snapshot_source(conn, "rd_100", year=2024)
    assert rows and rows[0]["entity"] == "Nano Membrane"
    assert rows[0]["diffusion_position"] == "leading" and rows[0]["year"] == 2024


# ─── signals store ───────────────────────────────────────────────────────────

def test_upsert_signals_dedups(tmp_path):
    conn = _conn(tmp_path)
    base = {"source_id": "yc_batch_rfs", "year": 2021, "item_text": "Acme robots", "entity": "Acme"}
    assert signals.upsert_signals(conn, [base])["inserted"] == 1
    again = signals.upsert_signals(conn, [dict(base, item_text="Acme robots reworded")])
    assert again["inserted"] == 0 and again["skipped"] == 1        # same entity+year+source

def test_annotate_and_embed(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO sources (source_id, name, edge_type, diffusion_position, "
                 "jury_credibility, add_date, created_at, updated_at) "
                 "VALUES ('rd_100','R&D 100','awards','leading','high','d','c','u')")
    conn.commit()
    signals.upsert_signals(conn, [{"source_id": "rd_100", "year": 2024, "item_text": "Nano Membrane",
                                   "entity": "Nano"}])
    assert signals.annotate_from_registry(conn) == 1
    row = conn.execute("SELECT diffusion_position, jury_credibility FROM jury_signals").fetchone()
    assert row["diffusion_position"] == "leading" and row["jury_credibility"] == "high"
    emb = HashingEmbedder(dim=64)
    assert signals.embed_pending(conn, emb) == 1
    assert signals.embed_pending(conn, emb) == 0                   # idempotent
    loaded = signals.load_embedded_signals(conn, emb.name)
    assert len(loaded) == 1 and loaded[0]["vec"].shape == (64,)


# ─── convergence math ────────────────────────────────────────────────────────

def _sig(sid, source, vec, pos="leading", cred="high", entity=None, et="company"):
    return {"signal_id": sid, "source_id": source, "diffusion_position": pos,
            "jury_credibility": cred, "entity": entity, "entity_type": et,
            "item_text": entity or f"s{sid}", "vec": np.asarray(vec, dtype=np.float32)}


def test_cluster_signals_separates_groups():
    sigs = [
        _sig(1, "a", [1, 0, 0, 0]), _sig(2, "b", [0.95, 0.05, 0, 0]),
        _sig(3, "c", [0, 1, 0, 0]), _sig(4, "d", [0, 0.96, 0.05, 0]),
    ]
    groups = convergence.cluster_signals(sigs, tau=0.55)
    assert len(groups) == 2
    assert {len(g) for g in groups} == {2}


def test_score_group_counts_distinct_leading_juries():
    members = [_sig(1, "mit_tr_10", [1, 0]), _sig(2, "darpa_eri", [1, 0]),
               _sig(3, "mit_tr_10", [1, 0])]                       # same source -> counts once
    diag = convergence.score_group(members, CFG)
    assert diag["n_sources"] == 2 and diag["n_leading_juries"] == 2
    assert diag["score"] == pytest.approx(2.0)                     # 2 leading-high sources


def test_denominator_adds_no_discovery_score_but_sets_horizon():
    members = [_sig(1, "nobel_prize", [1, 0], pos="denominator")]
    diag = convergence.score_group(members, CFG)
    assert diag["score"] == 0.0
    h = convergence.estimate_horizon({"denominator"}, CFG)
    assert h["years"] == CFG["horizon"]["denominator_fired"]["years"]
    h2 = convergence.estimate_horizon({"leading"}, CFG)
    assert h2["years"] == CFG["horizon"]["leading_only"]["years"]


def test_build_convergence_eligibility_and_promote(tmp_path):
    conn = _conn(tmp_path)
    # one eligible group: 3 signals from 3 distinct leading juries, same direction
    sigs = [
        _sig(1, "mit_tr_10", [1, 0, 0], entity="Alpha"),
        _sig(2, "darpa_eri", [0.97, 0.03, 0], entity="Beta"),
        _sig(3, "rd_100", [0.95, 0.05, 0], entity="Gamma"),
        # a lone signal -> not eligible (min_signals)
        _sig(9, "mit_tr_10", [0, 0, 1], entity="Zeta"),
    ]
    for s in sigs:
        conn.execute("INSERT INTO jury_signals (signal_id,source_id,item_text,entity,entity_type,"
                     "item_hash,ingested_at) VALUES (?,?,?,?,?,?,?)",
                     (s["signal_id"], s["source_id"], s["item_text"], s["entity"], "company",
                      f"h{s['signal_id']}", "t"))
    conn.commit()
    cands = convergence.build_convergence(sigs, CFG)
    assert len(cands) == 1
    assert cands[0]["diag"]["n_leading_juries"] == 3
    ids = convergence.promote(conn, cands)
    assert len(ids) == 1
    t = conn.execute("SELECT discovered_from, horizon_years FROM themes WHERE theme_id=?",
                     (ids[0],)).fetchone()
    assert t["discovered_from"] == "jury_convergence"
    n = conn.execute("SELECT COUNT(*) FROM theme_convergence WHERE theme_id=?", (ids[0],)).fetchone()[0]
    assert n == 3


# ─── org resolution (listed vs private) ──────────────────────────────────────

INDEX = {"acme robotics": {"ticker": "ACME", "cik": "0000000001"},
         "vertex pharmaceuticals": {"ticker": "VRTX", "cik": "0000875320"}}


def test_normalize_strips_suffixes():
    assert resolve._normalize("Acme Robotics, Inc.") == "acme robotics"
    assert resolve._normalize("Vertex Pharmaceuticals Incorporated") == "vertex pharmaceuticals"


def test_match_org_listed_private_unknown():
    assert resolve.match_org("Acme Robotics Inc", INDEX)["listing_status"] == "listed"
    assert resolve.match_org("Acme Robotics Inc", INDEX)["ticker"] == "ACME"
    assert resolve.match_org("Stealthy Newco", INDEX)["listing_status"] == "private"
    assert resolve.match_org("", INDEX)["listing_status"] == "unknown"


def test_resolve_theme_orgs_and_listing_watch(tmp_path):
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO themes (theme_id,label,created_at,updated_at,discovered_from) "
                 "VALUES ('disc:x','x','c','u','jury_convergence')")
    sigs = [_sig(1, "mit_tr_10", [1, 0], entity="Acme Robotics"),
            _sig(2, "darpa_eri", [1, 0], entity="Stealthy Newco")]
    for s in sigs:
        conn.execute("INSERT INTO jury_signals (signal_id,source_id,item_text,entity,entity_type,"
                     "item_hash,ingested_at) VALUES (?,?,?,?,?,?,?)",
                     (s["signal_id"], s["source_id"], s["item_text"], s["entity"], "company",
                      f"h{s['signal_id']}", "t"))
        conn.execute("INSERT INTO theme_convergence (theme_id,signal_id,similarity) VALUES ('disc:x',?,1.0)",
                     (s["signal_id"],))
    conn.commit()
    counts = resolve.resolve_theme_orgs(conn, "disc:x", INDEX)
    assert counts == {"listed": 1, "private": 1, "unknown": 0}
    assert resolve.track_a_tickers(conn, "disc:x") == ["ACME"]
    watch = resolve.listing_watch_orgs(conn)
    assert len(watch) == 1 and watch[0]["org_name"] == "Stealthy Newco"
    resolve.mark_listed(conn, watch[0]["org_id"], "NEWC", "0000000099")
    assert resolve.listing_watch_orgs(conn) == []                 # flipped private -> listed
    assert "NEWC" in resolve.track_a_tickers(conn)


# ─── specialist-fund cross-reference ─────────────────────────────────────────

def test_fund_config_readers():
    cfg = funds.load_specialist_funds(
        str(Path(__file__).resolve().parents[1] / "config" / "specialist_funds.yaml"))
    ciks = funds.fund_ciks(cfg)
    assert any(v["name"] == "Baker Brothers Advisors" for v in ciks.values())
    etfs = funds.sector_etfs(cfg)
    assert "SMH" in etfs["semiconductors"]


def test_cross_reference_weights_specialists_above_crossover():
    new_buys = {"NVDA": [{"name": "Light Street", "type": "crossover"}],
                "ACME": [{"name": "Whale Rock", "type": "specialist"},
                         {"name": "Tiger", "type": "crossover"}],
                "MSFT": [{"name": "X", "type": "specialist"}]}            # not in theme -> excluded
    out = funds.cross_reference(["ACME", "NVDA"], new_buys, CFG["funds"])
    assert [r["ticker"] for r in out] == ["ACME", "NVDA"]            # ACME scores higher
    acme = next(r for r in out if r["ticker"] == "ACME")
    assert acme["smart_money_score"] == pytest.approx(1.5)          # 1.0 specialist + 0.5 crossover
