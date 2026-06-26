"""Tests for Wave-3 ingest parsers (Hacker News + EDGAR full-text search) and ticker storage."""

from hype_parser import db, themes as T
from hype_parser.ingest import edgar_fts, hackernews

# ---------- Hacker News ----------

HN = ('{"nbPages":1,"hits":['
      '{"objectID":"111","title":"Show HN: a RAG library","story_text":"retrieval augmented gen",'
      '"url":"https://example.com/rag","created_at":"2023-07-01T10:00:00Z"},'
      '{"objectID":"222","title":"Mamba beats transformers","created_at":"2024-01-15T00:00:00Z"},'
      '{"title":"no id - skipped"}]}')


def test_hn_parse_hits():
    docs, nb = hackernews.parse_hits(HN)
    assert nb == 1 and len(docs) == 2
    d = docs[0]
    assert d["doc_id"] == "hn:111" and d["source_id"] == "hackernews"
    assert d["abstract"] == "retrieval augmented gen"
    assert d["url"] == "https://example.com/rag"
    assert d["published_at"].startswith("2023-07-01")
    # no url -> falls back to the HN item link
    assert docs[1]["url"] == "https://news.ycombinator.com/item?id=222"


def test_hn_fetch_paging_and_failopen():
    calls = {"n": 0}

    def get(url):
        calls["n"] += 1
        return HN                                       # nbPages=1 -> stop after page 0

    docs = hackernews.fetch("rag", http_get=get, sleep_s=0)
    assert {d["doc_id"] for d in docs} == {"hn:111", "hn:222"}
    assert calls["n"] == 1

    assert hackernews.fetch("rag", http_get=lambda u: (_ for _ in ()).throw(IOError())) == []


def test_hn_windows_uses_numeric_filter():
    seen = []

    def get(url):
        seen.append(url)
        return '{"nbPages":1,"hits":[]}'

    hackernews.fetch_windows("q", start_year=2022, end_year=2023, http_get=get)
    assert len(seen) == 2                                # one window per year
    assert any("created_at_i" in u for u in seen)


# ---------- EDGAR full-text search ----------

EFTS = ('{"hits":{"total":{"value":42},"hits":['
        '{"_source":{"display_names":["Intellia Therapeutics, Inc. (NTLA) (CIK 0001652130)"]}},'
        '{"_source":{"display_names":["CRISPR Therapeutics AG (CRSP) (CIK 0001674416)",'
        ' "Some Subsidiary (no ticker)"]}}'
        ']}}')


def test_edgar_parse_response_count_and_tickers():
    total, tickers = edgar_fts.parse_response(EFTS)
    assert total == 42
    assert tickers == ["NTLA", "CRSP"]                  # parsed from display_names
    assert edgar_fts.parse_response("bad json") == (0, [])


def test_edgar_fetch_yearly_aggregates():
    # one request per year; return the same payload so counts/tickers accumulate
    calls = {"n": 0}

    def get(url):
        calls["n"] += 1
        return EFTS

    yearly, tickers = edgar_fts.fetch_yearly(
        "CRISPR", start_year=2020, end_year=2023, http_get=get, sleep_s=0)
    assert calls["n"] == 4                               # 2020..2023
    assert yearly == {2020: 42, 2021: 42, 2022: 42, 2023: 42}
    assert tickers["NTLA"] == 4 and tickers["CRSP"] == 4


def test_edgar_fetch_yearly_paginates_to_broaden_constituents():
    # Stage C (D20): ticker_pages>1 pages through hits via `from`, harvesting MORE filers per year.
    PAGES = {
        0: '{"hits":{"total":{"value":42},"hits":[{"_source":{"display_names":["A Co (AAA) (CIK 1)"]}}]}}',
        10: '{"hits":{"total":{"value":42},"hits":[{"_source":{"display_names":["B Co (BBB) (CIK 2)"]}}]}}',
        20: '{"hits":{"total":{"value":42},"hits":[{"_source":{"display_names":["C Co (CCC) (CIK 3)"]}}]}}',
    }

    def get(url):
        frm = 0
        if "from=" in url:
            frm = int(url.split("from=")[1].split("&")[0])
        return PAGES[frm]

    yearly, tickers = edgar_fts.fetch_yearly(
        "q", start_year=2022, end_year=2022, http_get=get, sleep_s=0, ticker_pages=3)
    assert yearly == {2022: 42}
    assert set(tickers) == {"AAA", "BBB", "CCC"}          # all three pages harvested


def test_edgar_fetch_yearly_paging_stops_past_total():
    # total=5 < page size 10 -> no extra page requests even with ticker_pages=5
    calls = {"n": 0}

    def get(url):
        calls["n"] += 1
        return '{"hits":{"total":{"value":5},"hits":[{"_source":{"display_names":["X (XXX) (CIK 1)"]}}]}}'

    edgar_fts.fetch_yearly("q", start_year=2022, end_year=2022, http_get=get, sleep_s=0,
                           ticker_pages=5)
    assert calls["n"] == 1                                # only page 0 (total < page size)


def test_edgar_fetch_yearly_failopen():
    def boom(url):
        raise ConnectionError("down")
    yearly, tickers = edgar_fts.fetch_yearly("q", start_year=2022, end_year=2023,
                                             http_get=boom, sleep_s=0, retries=0)
    assert yearly == {} and tickers == {}


def test_edgar_fetch_yearly_retries_then_succeeds():
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("HTTP Error 500")             # first attempt fails
        return EFTS                                      # retry succeeds
    yearly, tickers = edgar_fts.fetch_yearly(
        "q", start_year=2022, end_year=2023, http_get=flaky, sleep_s=0, backoff_s=0)
    assert len(yearly) == 2 and tickers["NTLA"] == 2


# ---------- storage: edgar_filings column + theme_tickers ----------

def test_schema_v4_and_edgar_series(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert db.current_version(conn) >= 4
    T.upsert_theme(conn, {"id": "x", "label": "X"})
    T.write_series(conn, "x", {"2024-01": {"n_spec": 2, "n_main": 5, "wiki_views": 9,
                                           "edgar_filings": 7}})
    row = T.read_series(conn, "x")[0]
    assert row["edgar_filings"] == 7 and row["n_spec"] == 2
    conn.close()


def test_theme_tickers_storage(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    T.upsert_theme(conn, {"id": "crispr", "label": "C"})
    T.write_theme_tickers(conn, "crispr", {"NTLA": 12, "CRSP": 30, "BEAM": 5})
    top = T.read_theme_tickers(conn, "crispr", limit=2)
    assert [r["ticker"] for r in top] == ["CRSP", "NTLA"]   # ordered by mentions desc
    # idempotent replace
    T.write_theme_tickers(conn, "crispr", {"NTLA": 1})
    assert {r["ticker"] for r in T.read_theme_tickers(conn, "crispr")} == {"NTLA"}
    conn.close()


def test_theme_edgar_yearly_storage(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    assert db.current_version(conn) >= 5
    T.upsert_theme(conn, {"id": "crispr", "label": "C"})
    T.write_theme_edgar(conn, "crispr", {2023: 100, 2022: 60, 2024: 350})
    rows = T.read_theme_edgar(conn, "crispr")
    assert [r["year"] for r in rows] == [2022, 2023, 2024]   # ordered by year
    assert rows[2]["n_filings"] == 350
    T.write_theme_edgar(conn, "crispr", {2024: 1})           # idempotent replace
    assert [r["year"] for r in T.read_theme_edgar(conn, "crispr")] == [2024]
    conn.close()
