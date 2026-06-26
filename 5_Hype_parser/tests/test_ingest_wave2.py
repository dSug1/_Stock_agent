"""Tests for Wave-2 ingest parsers (Europe PMC + ClinicalTrials.gov). No network."""

from hype_parser.ingest import clinicaltrials, europepmc

EPMC = ('{"hitCount":2,"nextCursorMark":"AoJ123",'
        '"resultList":{"result":['
        '{"id":"PPR777","source":"PPR","title":"A KRAS vaccine preprint",'
        '"abstractText":"We test an off-the-shelf KRAS vaccine.",'
        '"firstPublicationDate":"2023-05-10","doi":"10.1101/2023.05.10.123"},'
        '{"id":"998877","source":"MED","title":"Published KRAS study",'
        '"abstractText":"Cohort analysis.","pubYear":"2022"},'
        '{"source":"PPR","title":"no id - skipped"}'
        ']}}')


def test_europepmc_parse_page():
    docs, cursor = europepmc.parse_page(EPMC)
    assert cursor == "AoJ123"
    assert len(docs) == 2                                  # the id-less row is skipped
    d = docs[0]
    assert d["doc_id"] == "epmc:PPR:PPR777" and d["source_id"] == "europepmc"
    assert d["published_at"] == "2023-05-10"
    assert d["url"] == "https://doi.org/10.1101/2023.05.10.123"
    # pubYear-only row gets a synthesized Jan date + the europepmc article url
    assert docs[1]["published_at"] == "2022-01-01"
    assert docs[1]["url"] == "https://europepmc.org/article/MED/998877"


def test_europepmc_parse_bad_payload():
    assert europepmc.parse_page("not json") == ([], None)


def test_europepmc_fetch_paging_and_dedup():
    pages = [
        '{"nextCursorMark":"c2","resultList":{"result":['
        '{"id":"1","source":"PPR","title":"a","firstPublicationDate":"2023-01-01"}]}}',
        '{"nextCursorMark":"c2","resultList":{"result":['          # same cursor -> stop after this
        '{"id":"1","source":"PPR","title":"a-dup","firstPublicationDate":"2023-01-01"},'
        '{"id":"2","source":"PPR","title":"b","firstPublicationDate":"2023-02-01"}]}}',
    ]
    seq = iter(pages)
    docs = europepmc.fetch("q", max_results=100, http_get=lambda url: next(seq))
    assert {d["doc_id"] for d in docs} == {"epmc:PPR:1", "epmc:PPR:2"}   # dedup on id


def test_europepmc_failopen():
    def boom(url):
        raise ConnectionError("down")
    assert europepmc.fetch("q", http_get=boom) == []


CTGOV = ('{"nextPageToken":"tok2","studies":['
         '{"protocolSection":{'
         '"identificationModule":{"nctId":"NCT01","briefTitle":"KRAS vaccine trial"},'
         '"descriptionModule":{"briefSummary":"A phase 1 trial."},'
         '"statusModule":{"studyFirstPostDateStruct":{"date":"2021-03-15"},'
         '"startDateStruct":{"date":"2021-06-01"}}}},'
         '{"protocolSection":{"identificationModule":{"briefTitle":"no nct - skipped"}}}'
         ']}')


def test_ctgov_parse_page():
    docs, token = clinicaltrials.parse_page(CTGOV)
    assert token == "tok2"
    assert len(docs) == 1                                  # the nct-less study is skipped
    d = docs[0]
    assert d["doc_id"] == "ctgov:NCT01" and d["source_id"] == "clinicaltrials"
    assert d["title"] == "KRAS vaccine trial"
    assert d["published_at"] == "2021-03-15"               # first-post date preferred over start
    assert d["url"] == "https://clinicaltrials.gov/study/NCT01"


def test_ctgov_falls_back_to_start_date():
    payload = ('{"studies":[{"protocolSection":{'
               '"identificationModule":{"nctId":"NCT9"},'
               '"statusModule":{"startDateStruct":{"date":"2020-02"}}}}]}')
    docs, token = clinicaltrials.parse_page(payload)
    assert token is None
    assert docs[0]["published_at"] == "2020-02"


def test_ctgov_fetch_paging_stops_without_token():
    pages = ['{"nextPageToken":"t2","studies":[{"protocolSection":{'
             '"identificationModule":{"nctId":"NCT1"}}}]}',
             '{"studies":[{"protocolSection":{'              # no nextPageToken -> stop
             '"identificationModule":{"nctId":"NCT2"}}}]}']
    seq = iter(pages)
    docs = clinicaltrials.fetch("q", max_results=100, http_get=lambda url: next(seq))
    assert {d["doc_id"] for d in docs} == {"ctgov:NCT1", "ctgov:NCT2"}


def test_ctgov_failopen():
    def boom(url):
        raise TimeoutError("slow")
    assert clinicaltrials.fetch("q", http_get=boom) == []
