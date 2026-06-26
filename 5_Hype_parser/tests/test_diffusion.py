"""Tests for the diffusion engine: embedder, diffusion math, ingest parsers, storage.

No network (ingest parsers take canned payloads). Run from inside 5_Hype_parser/:
    PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/ -q
"""

import numpy as np
import pytest

from hype_parser import db, diffusion as D, embed as E, themes as T
from hype_parser.ingest import arxiv, gdelt, wikipedia


# ---------- embedder ----------

def test_hashing_embedder_deterministic_normalized():
    emb = E.HashingEmbedder(dim=64)
    a = emb.encode(["state space model mamba"])
    b = emb.encode(["state space model mamba"])
    assert a.shape == (1, 64)
    np.testing.assert_allclose(a, b)                       # deterministic
    np.testing.assert_allclose(np.linalg.norm(a, axis=1), 1.0, atol=1e-5)  # L2 normalized


def test_hashing_embedder_cosine_orders_sensibly():
    emb = E.HashingEmbedder(dim=512)
    v = emb.encode([
        "retrieval augmented generation for language models",
        "retrieval augmented generation grounding llms",   # similar
        "photosynthesis in tropical plants",               # unrelated
    ])
    same = float(np.dot(v[0], v[1]))
    diff = float(np.dot(v[0], v[2]))
    assert same > diff


def test_centroid_is_normalized():
    emb = E.HashingEmbedder(dim=128)
    v = emb.encode(["alpha beta", "beta gamma", "gamma delta"])
    c = E.centroid(v)
    assert abs(np.linalg.norm(c) - 1.0) < 1e-5


def test_blob_roundtrip():
    v = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    out = E.from_blob(E.to_blob(v), 3)
    np.testing.assert_allclose(v, out)


def test_get_embedder_returns_valid_embedder():
    # contract holds whether or not sentence-transformers is installed (semantic vs hashing fallback)
    emb = E.get_embedder()
    assert hasattr(emb, "encode") and hasattr(emb, "name") and hasattr(emb, "semantic")
    v = emb.encode(["state space model", "state space model"])
    assert v.shape[0] == 2
    # identical text -> cosine ~1 (exact-equality check is too strict for float32 ST output)
    assert float(np.dot(v[0], v[1])) == pytest.approx(1.0, abs=1e-4)


# ---------- diffusion math ----------

def test_month_key_and_range():
    assert D.month_key("2024-03-17T08:00:00Z") == "2024-03"
    assert D.month_key("") is None
    assert D.month_range("2023-11", "2024-02") == ["2023-11", "2023-12", "2024-01", "2024-02"]


def test_beta_spec_positive_on_rising_series():
    periods = ["2024-01", "2024-02", "2024-03", "2024-04"]
    rising = {"2024-01": 1, "2024-02": 2, "2024-03": 4, "2024-04": 8}
    flat = {p: 5 for p in periods}
    assert D.beta_spec(periods, rising, 4) > 0
    assert abs(D.beta_spec(periods, flat, 4)) < 1e-9
    assert D.beta_spec(periods, {}, 4) == 0.0           # all zero -> flat


def test_beta_spec_uses_last_L_only():
    periods = [f"2023-{m:02d}" for m in range(1, 13)]
    counts = {p: 1 for p in periods}
    counts["2023-12"] = 100                              # only the last 2 months matter
    assert D.beta_spec(periods, counts, 2) > 0
    assert abs(D.beta_spec(periods, counts, 1)) < 1e-9  # single point -> 0


def test_p_main_and_ratio_bounds():
    assert D.p_main(0, 0) == 0.0
    assert D.p_main(10, 10) == 0.5
    assert D.p_main(0, 5) == 1.0
    assert D.diffusion_ratio(10, 0) == float("inf")
    assert D.diffusion_ratio(0, 0) == 0.0
    assert D.diffusion_ratio(4, 2) == 2.0


def test_nascency_gate():
    assert D.nascency_gate(0.5, 0.2, beta_min=0.0, p_max=0.5) is True
    assert D.nascency_gate(-0.1, 0.2, beta_min=0.0, p_max=0.5) is False  # slope too low
    assert D.nascency_gate(0.5, 0.9, beta_min=0.0, p_max=0.5) is False   # too mainstream


def test_summarize_shape():
    periods = ["2024-01", "2024-02", "2024-03"]
    ns = {"2024-01": 1, "2024-02": 3, "2024-03": 9}
    nm = {"2024-03": 50}
    s = D.summarize(periods, ns, nm, L=3, tau_member=0.3, beta_min=0.0, p_max=0.5)
    assert s["latest_period"] == "2024-03"
    assert s["n_spec_latest"] == 9 and s["n_main_latest"] == 50
    assert s["n_spec_total"] == 13
    assert s["beta_spec"] > 0


# ---------- ingest parsers (canned payloads) ----------

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.01234v2</id>
    <title>A Great Paper on
      Retrieval</title>
    <summary>We study retrieval augmented generation.</summary>
    <published>2024-01-15T00:00:00Z</published>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2312.09999v1</id>
    <title>Another</title>
    <summary>Mamba state space.</summary>
    <published>2023-12-02T00:00:00Z</published>
  </entry>
</feed>"""


def test_arxiv_parse_atom():
    docs = arxiv.parse_atom(ATOM)
    assert len(docs) == 2
    d = docs[0]
    assert d["doc_id"] == "arxiv:2401.01234"            # version + /abs/ stripped
    assert d["source_id"] == "arxiv"
    assert "\n" not in d["title"] and "Great Paper" in d["title"]
    assert d["published_at"].startswith("2024-01-15")


def test_arxiv_fetch_dedup_and_failopen():
    calls = {"n": 0}

    def fake_get(url):
        calls["n"] += 1
        return ATOM                                      # same page -> dedup, then stop (<page_size)

    docs = arxiv.fetch("q", max_results=400, page_size=100, http_get=fake_get, sleep_s=0)
    assert {d["doc_id"] for d in docs} == {"arxiv:2401.01234", "arxiv:2312.09999"}
    assert calls["n"] == 1

    def boom(url):
        raise ConnectionError("down")

    assert arxiv.fetch("q", http_get=boom, sleep_s=0) == []   # fail-open


def test_gdelt_parse_timeline_monthly_aggregation():
    payload = ('{"timeline":[{"data":['
               '{"date":"20240105120000","value":3},'
               '{"date":"20240120120000","value":4},'
               '{"date":"20240210120000","value":10}]}]}')
    monthly = gdelt.parse_timeline(payload)
    assert monthly == {"2024-01": 7, "2024-02": 10}
    assert gdelt.parse_timeline("not json") == {}


def test_wikipedia_parse_pageviews():
    payload = ('{"items":[{"timestamp":"2024010100","views":1200},'
               '{"timestamp":"2024020100","views":1500}]}')
    out = wikipedia.parse_pageviews(payload)
    assert out == {"2024-01": 1200, "2024-02": 1500}
    assert wikipedia.parse_pageviews("bad") == {}


# ---------- storage + membership integration ----------

def _conn(tmp_path):
    return db.connect(tmp_path / "test.db")


def test_theme_and_document_storage(tmp_path):
    conn = _conn(tmp_path)
    T.upsert_theme(conn, {"id": "rag", "label": "RAG", "keywords": ["rag", "retrieval"],
                          "descriptor": "retrieval augmented generation",
                          "arxiv_query": "q", "wiki_article": "RAG"})
    assert T.get_theme(conn, "rag")["label"] == "RAG"
    n = T.upsert_documents(conn, arxiv.parse_atom(ATOM))
    assert n == 2
    row = conn.execute("SELECT published_month FROM documents WHERE doc_id='arxiv:2401.01234'").fetchone()
    assert row["published_month"] == "2024-01"          # derived on store
    conn.close()


def test_membership_link_and_series(tmp_path):
    conn = _conn(tmp_path)
    emb = E.HashingEmbedder(dim=256)
    T.upsert_theme(conn, {"id": "rag", "label": "RAG", "keywords": ["retrieval"],
                          "descriptor": "retrieval augmented generation"})
    T.upsert_documents(conn, arxiv.parse_atom(ATOM))
    # embed both docs
    for did, text in [("arxiv:2401.01234", "retrieval augmented generation"),
                      ("arxiv:2312.09999", "mamba state space")]:
        v = emb.encode([text])[0]
        T.store_embedding(conn, did, E.to_blob(v), emb.name, 256)
    # link with a centroid that should favour the retrieval doc
    centroid = E.centroid(emb.encode(["retrieval augmented generation"]))
    rows = []
    for did in ["arxiv:2401.01234", "arxiv:2312.09999"]:
        r = conn.execute("SELECT embedding, embed_dim FROM documents WHERE doc_id=?", (did,)).fetchone()
        cos = float(np.dot(E.from_blob(r["embedding"], r["embed_dim"]), centroid))
        rows.append((did, cos, cos >= 0.2))
    T.link_theme_documents(conn, "rag", rows)
    members = T.member_docs(conn, "rag")
    assert any(m["doc_id"] == "arxiv:2401.01234" for m in members)

    T.write_series(conn, "rag", {"2024-01": {"n_spec": 1, "n_main": 50, "wiki_views": 999}})
    s = T.read_series(conn, "rag")
    assert len(s) == 1 and s[0]["n_spec"] == 1 and s[0]["n_main"] == 50
    conn.close()
