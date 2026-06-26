"""Tests for Wave-4 ingest parsers (NIH RePORTER, NSF, SBIR, PatentsView). No network."""

from hype_parser.ingest import nih_reporter, nsf, patentsview, sbir


# ---------- NIH RePORTER ----------

NIH = ('{"meta":{"total":2},"results":['
       '{"appl_id":10567,"project_title":"CRISPR base editing for sickle cell",'
       '"abstract_text":"We develop in vivo CRISPR base editors.","fiscal_year":2023,'
       '"award_notice_date":"2023-04-15T00:00:00","award_amount":500000},'
       '{"project_num":"R01CA999","project_title":"KRAS vaccine","abstract_text":"mKRAS.",'
       '"fiscal_year":2022},'
       '{"project_title":"no id - skipped"}]}')


def test_nih_parse_results():
    docs, total = nih_reporter.parse_results(NIH)
    assert total == 2 and len(docs) == 2
    d = docs[0]
    assert d["doc_id"] == "nih:10567" and d["source_id"] == "nih"
    assert d["published_at"] == "2023-04-15"            # trimmed to 10 chars
    assert "base editing" in d["title"]
    assert docs[1]["doc_id"] == "nih:R01CA999"          # falls back to project_num
    assert docs[1]["published_at"] == "2022-01-01"      # synthesized from fiscal_year


def test_nih_fetch_windows_uses_post():
    seen = []

    def post(url, body):
        seen.append(body)
        return NIH if body["criteria"]["fiscal_years"] == [2023] else '{"results":[]}'

    docs = nih_reporter.fetch_windows("CRISPR", start_year=2022, end_year=2023, http_post=post)
    assert {d["doc_id"] for d in docs} == {"nih:10567", "nih:R01CA999"}
    assert seen[0]["criteria"]["advanced_text_search"]["search_text"] == "CRISPR"


def test_nih_failopen():
    docs = nih_reporter.fetch_windows(
        "q", start_year=2023, end_year=2023,
        http_post=lambda u, b: (_ for _ in ()).throw(IOError()))
    assert docs == []


# ---------- NSF ----------

NSF = ('{"response":{"award":['
       '{"id":"2012345","title":"RAG for science","abstractText":"retrieval augmented.",'
       '"date":"05/01/2023","fundsObligatedAmt":"400000"},'
       '{"title":"no id - skipped"}]}}')


def test_nsf_parse_awards():
    docs = nsf.parse_awards(NSF)
    assert len(docs) == 1
    d = docs[0]
    assert d["doc_id"] == "nsf:2012345" and d["source_id"] == "nsf"
    assert d["published_at"] == "2023-05-01"            # mm/dd/yyyy -> ISO
    assert nsf.parse_awards("bad") == []


def test_nsf_fetch_windows():
    seq = iter([NSF, '{"response":{"award":[]}}'])
    docs = nsf.fetch_windows("rag", start_year=2023, end_year=2023,
                             http_get=lambda u: next(seq))
    assert [d["doc_id"] for d in docs] == ["nsf:2012345"]


# ---------- SBIR ----------

SBIR = ('[{"agency_tracking_number":"AT-1","award_title":"CRISPR delivery",'
        '"abstract":"LNP for CRISPR.","proposal_award_date":"2021-09-10","award_year":2021},'
        '{"award_title":"no key - skipped"}]')


def test_sbir_parse_awards_array():
    docs = sbir.parse_awards(SBIR)
    assert len(docs) == 1
    d = docs[0]
    assert d["doc_id"] == "sbir:AT-1" and d["source_id"] == "sbir"
    assert d["published_at"] == "2021-09-10"
    assert sbir.parse_awards("bad") == []


def test_sbir_fetch_windows():
    seq = iter([SBIR, "[]"])
    docs = sbir.fetch_windows("crispr", start_year=2021, end_year=2021,
                              http_get=lambda u: next(seq))
    assert [d["doc_id"] for d in docs] == ["sbir:AT-1"]


# ---------- PatentsView ----------

PV = ('{"patents":[{"patent_id":"11111111","patent_title":"CRISPR editing system",'
      '"patent_abstract":"A CRISPR method.","patent_date":"2023-06-20"},'
      '{"patent_title":"no id - skipped"}]}')


def test_patentsview_parse():
    docs = patentsview.parse_patents(PV)
    assert len(docs) == 1
    d = docs[0]
    assert d["doc_id"] == "uspto:11111111" and d["source_id"] == "patentsview"
    assert d["published_at"] == "2023-06-20"
    assert patentsview.parse_patents("bad") == []


def test_patentsview_skips_without_key():
    # no api_key -> returns [] without calling http_get
    called = {"n": 0}

    def get(url):
        called["n"] += 1
        return PV

    docs = patentsview.fetch_windows("CRISPR", start_year=2023, end_year=2023,
                                     api_key=None, http_get=get)
    assert docs == [] and called["n"] == 0


def test_patentsview_fetch_with_key():
    docs = patentsview.fetch_windows("CRISPR", start_year=2023, end_year=2023,
                                     api_key="dummy", http_get=lambda u: PV)
    assert [d["doc_id"] for d in docs] == ["uspto:11111111"]
