"""Module 4c — SEC EDGAR client.

Fetches:
  - Ticker → CIK map (reuses Outputs/sec_company_tickers.json cache from M2)
  - companyfacts XBRL (cash, R&D, G&A, op CF, shares)
  - submissions (recent filings list, filtered to forms of interest)
  - Form 4 OWNERSHIP DOCUMENT XML → insider transactions
  - 8-K Items 1.01/3.02 + S-3/S-3/A + 424B5 → capital_raises rows

Rate-limit reuses `src/layer_1/edgar_13f._EDGAR_LIMITER` (process-global
token bucket at 9 req/s under SEC's 10 req/s cap). User-Agent is the same
constant from edgar_13f to keep SEC fair-use compliance unified.

Fail-open semantics: any single failure (404 / 5xx / parse error) returns
a result with `status='failed'` or `status='partial'` and an `error` string.
The caller logs to fetch_log and continues.

Per D54: financials only — no clinical_trials.
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests
from requests.adapters import HTTPAdapter

# Reuse the existing M2 EDGAR limiter — single process-global bucket, shared
# across M2 (layer_1/edgar_13f) and M4c (this file). Importing the module
# attribute gives us the same instance in any caller.
from layer_1.edgar_13f import _EDGAR_LIMITER, EDGAR_USER_AGENT
from layer_1.sec_ticker_resolver import (
    DEFAULT_CACHE_PATH as SEC_TICKER_CACHE,
    download_sec_tickers,
)

log = logging.getLogger(__name__)


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# D56 — module-level requests.Session with a pool sized for the M4c worker
# count. requests.Session is thread-safe (urllib3 PoolManager underneath),
# so a single shared session is correct here. Keep-alive eliminates the
# ~30 ms TCP+TLS handshake per request that raw urllib paid before.
_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://",  _ADAPTER)
_SESSION.headers.update({"User-Agent": EDGAR_USER_AGENT})


@dataclass
class _HttpResponse:
    body: bytes
    status: int                      # 200 / 304 / etc.
    etag: Optional[str]
    last_modified: Optional[str]


class _HttpError(Exception):
    """Raised for non-2xx, non-304 responses. Carries `code` for caller fork."""
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"HTTP {code}: {reason}")
        self.code = code
        self.reason = reason

# US-GAAP concepts we extract per company.
_GAAP_CONCEPTS = {
    "cash_and_equivalents_usd": [
        "CashAndCashEquivalentsAtCarryingValue",
        "Cash",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "short_term_investments_usd": [
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
    ],
    "total_assets_usd":          ["Assets"],
    "total_liabilities_usd":     ["Liabilities"],
    "accounts_receivable_usd":   ["AccountsReceivableNetCurrent"],
    "ppe_net_usd":               ["PropertyPlantAndEquipmentNet"],
    "rd_expense":                ["ResearchAndDevelopmentExpense"],
    "ga_expense":                ["GeneralAndAdministrativeExpense"],
    "operating_cf":              ["NetCashProvidedByUsedInOperatingActivities"],
    "common_shares_outstanding": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
    ],
}


# ─── HTTP helper ─────────────────────────────────────────────────────────────

def _http_request(
    url: str,
    *,
    accept: str = "application/json",
    if_none_match: Optional[str] = None,
    if_modified_since: Optional[str] = None,
) -> _HttpResponse:
    """Rate-limited GET via the shared keep-alive session. Returns the body
    plus ETag / Last-Modified for callers that want conditional re-fetches.
    Raises _HttpError for non-2xx, non-304. 304 returns body=b'' status=304."""
    _EDGAR_LIMITER.acquire()
    headers = {"Accept": accept}
    if if_none_match:
        headers["If-None-Match"] = if_none_match
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    resp = _SESSION.get(url, headers=headers, timeout=30)
    if resp.status_code == 304:
        return _HttpResponse(b"", 304, resp.headers.get("ETag"),
                             resp.headers.get("Last-Modified"))
    if not (200 <= resp.status_code < 300):
        raise _HttpError(resp.status_code, resp.reason or "")
    return _HttpResponse(
        resp.content, resp.status_code,
        resp.headers.get("ETag"),
        resp.headers.get("Last-Modified"),
    )


def _http_get(url: str, *, accept: str = "application/json") -> bytes:
    """Backward-compat wrapper used by sources that don't care about ETags."""
    return _http_request(url, accept=accept).body


# ─── Ticker → CIK map (inverse of layer_1/sec_ticker_resolver) ───────────────

def load_ticker_cik_map(
    cache_path: Path = SEC_TICKER_CACHE, *, refresh: bool = False
) -> dict[str, str]:
    """{ticker: 10-digit-padded-cik}. Refreshes the cache on disk if stale."""
    download_sec_tickers(cache_path, force=refresh)
    if not cache_path.exists():
        return {}
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for entry in raw.values():
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        if not ticker or cik is None:
            continue
        out[str(ticker).upper()] = str(cik).zfill(10)
    return out


# ─── Result containers ───────────────────────────────────────────────────────

@dataclass
class CompanyFactsResult:
    status: str                          # 'ok' | 'partial' | 'failed' | 'not_modified'
    error: Optional[str]
    rows: list[dict]
    etag: Optional[str] = None
    last_modified: Optional[str] = None                     # one per period


@dataclass
class FilingsResult:
    status: str
    error: Optional[str]
    filings: list[dict]                  # {accession_number, form, filing_date, primary_doc}


@dataclass
class Form4Result:
    status: str
    error: Optional[str]
    transactions: list[dict]


# ─── companyfacts ────────────────────────────────────────────────────────────

def _latest_periods_per_concept(
    facts: dict, concept_aliases: list[str], unit: str = "USD",
    *, n_periods: int = 8,
) -> list[dict]:
    """Return the n most recent (period_end_date, value, form, fy, fp, accn)
    rows for whichever concept alias is present.
    """
    for alias in concept_aliases:
        node = facts.get("facts", {}).get("us-gaap", {}).get(alias)
        if node is None:
            node = facts.get("facts", {}).get("dei", {}).get(alias)
        if not node:
            continue
        units = node.get("units", {}) or {}
        # Prefer USD; some share-count concepts use 'shares' instead.
        unit_key = unit if unit in units else next(iter(units.keys()), None)
        if not unit_key:
            continue
        rows = units.get(unit_key, []) or []
        clean = [r for r in rows if r.get("end") and r.get("val") is not None]
        clean.sort(key=lambda r: r.get("end"), reverse=True)
        # Dedup by `end` keeping the latest-filed entry.
        seen: set[str] = set()
        unique: list[dict] = []
        for r in clean:
            end = r["end"]
            if end in seen:
                continue
            seen.add(end)
            unique.append(r)
            if len(unique) >= n_periods:
                break
        return unique
    return []


def _row_span_days(r: dict) -> int:
    """Span (in days) between the XBRL row's start + end dates.

    Operating-CF rows in 10-Q filings are typically reported cumulatively
    within a fiscal year (Q1 ≈ 90d, Q2 ≈ 180d, Q3 ≈ 270d, 10-K ≈ 365d).
    The span tells us which kind of value we're looking at — essential
    for computing a correct TTM (see `_ttm_sum`).

    Spec: spec/m4c_ttm_cumulative_ytd_bugfix.md §4.1 (ported from
    3_Biopharmcatalyst_parser/src/module_6_5/edgar_client.py 2026-05-28).
    """
    import datetime as _dt
    try:
        s = _dt.date.fromisoformat(r["start"])
        e = _dt.date.fromisoformat(r["end"])
        return (e - s).days
    except (KeyError, TypeError, ValueError):
        return 0


def _ttm_sum(rows: list[dict]) -> Optional[int]:
    """Correct TTM for XBRL concepts that are reported cumulatively-YTD
    inside a fiscal year (operating cash flow, R&D, G&A).

    The naive ``sum(last 4 quarterly values)`` is wrong because Q1 covers
    3 months while Q3 covers 9 months — summing them double-counts every
    period inside Q3. Strategy here (in priority order):

      1. **Annual row available** — if the latest row has form 10-K OR a
         year-long span (350-380 days), use its value directly.
      2. **True single-quarter rows** — some filers report incremental
         quarterly figures (span ≈ 90 days, never cumulative). If we have
         four of those covering the last 12 months, sum them.
      3. **Cumulative-YTD derivation** — bucket rows by fiscal year, sort
         within FY by end-date ascending, and difference consecutive
         entries to recover incremental quarters. Sum the most recent 4.
      4. **Fallback** — `None` rather than a bogus inflated sum.

    Caller can detect Strategy 4 (None) and treat runway/etc as
    "fundamentals incomplete".

    Spec: spec/m4c_ttm_cumulative_ytd_bugfix.md §4.2 (ported from
    3_Biopharmcatalyst_parser/src/module_6_5/edgar_client.py 2026-05-28).
    """
    if not rows:
        return None

    # Strategy 1 — annual row.
    latest = rows[0]
    if (latest.get("form") or "").startswith("10-K"):
        v = latest.get("val")
        return int(v) if v is not None else None
    if 350 <= _row_span_days(latest) <= 380:
        v = latest.get("val")
        return int(v) if v is not None else None

    # Strategy 2 — explicit single-quarter rows.
    pure_q = [r for r in rows if 80 <= _row_span_days(r) <= 100]
    if len(pure_q) >= 4:
        return int(sum(r["val"] for r in pure_q[:4]))

    # Strategy 3 — derive incremental quarters from cumulative YTD.
    # Group by fiscal year + sort within FY by end-date ascending.
    by_fy: dict = {}
    for r in rows:
        fy = r.get("fy")
        if fy is None:
            continue
        by_fy.setdefault(fy, []).append(r)

    derived: list[tuple[str, int]] = []   # (end_date, incremental_val)
    for fy, fy_rows in by_fy.items():
        fy_sorted = sorted(fy_rows, key=lambda r: r.get("end") or "")
        prev_val = 0
        for r in fy_sorted:
            v = r.get("val")
            if v is None:
                continue
            inc = int(v) - prev_val
            derived.append((r["end"], inc))
            prev_val = int(v)

    derived.sort(key=lambda x: x[0], reverse=True)
    if len(derived) >= 4:
        return int(sum(x[1] for x in derived[:4]))
    if len(derived) >= 2:
        # Partial recovery — annualise from the average derived quarter.
        avg = sum(x[1] for x in derived) / len(derived)
        return int(avg * 4)

    return None


def fetch_companyfacts(
    ticker: str, cik: str, *, now_iso: str,
    if_none_match: Optional[str] = None,
    if_modified_since: Optional[str] = None,
) -> CompanyFactsResult:
    """Fetch + parse SEC companyfacts XBRL into per-period financials rows.
    Returns up to 8 most recent periods (most recent first). TTM aggregates
    are populated only on the latest row.

    D56: pass `if_none_match`/`if_modified_since` from the prior fetch_log
    row to use SEC's CDN conditional-GET. A 304 response returns
    status='not_modified' (caller skips the parse + write entirely).
    """
    url = COMPANYFACTS_URL.format(cik=cik)
    try:
        resp = _http_request(url, if_none_match=if_none_match,
                             if_modified_since=if_modified_since)
    except _HttpError as e:
        if e.code == 404:
            return CompanyFactsResult(status="partial", error="no companyfacts (404)", rows=[])
        return CompanyFactsResult(status="failed", error=f"HTTP {e.code}: {e.reason}", rows=[])
    except Exception as e:  # noqa: BLE001
        return CompanyFactsResult(status="failed", error=f"{type(e).__name__}: {e}", rows=[])

    if resp.status == 304:
        return CompanyFactsResult(
            status="not_modified", error=None, rows=[],
            etag=if_none_match, last_modified=if_modified_since,
        )

    try:
        facts = json.loads(resp.body)
    except json.JSONDecodeError as e:
        return CompanyFactsResult(status="failed", error=f"JSON: {e}", rows=[])

    cash_rows = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["cash_and_equivalents_usd"])
    sti_rows  = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["short_term_investments_usd"])
    ta_rows   = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["total_assets_usd"])
    tl_rows   = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["total_liabilities_usd"])
    ar_rows   = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["accounts_receivable_usd"])
    ppe_rows  = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["ppe_net_usd"])
    rd_rows   = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["rd_expense"])
    ga_rows   = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["ga_expense"])
    cf_rows   = _latest_periods_per_concept(facts, _GAAP_CONCEPTS["operating_cf"])
    sh_rows   = _latest_periods_per_concept(
        facts, _GAAP_CONCEPTS["common_shares_outstanding"], unit="shares",
    )

    if not cash_rows:
        return CompanyFactsResult(status="partial", error="no cash concept", rows=[])

    by_end: dict[str, dict] = {}
    for src, key in [
        (cash_rows, "cash_and_equivalents_usd"),
        (sti_rows,  "short_term_investments_usd"),
        (ta_rows,   "total_assets_usd"),
        (tl_rows,   "total_liabilities_usd"),
        (ar_rows,   "accounts_receivable_usd"),
        (ppe_rows,  "ppe_net_usd"),
        (sh_rows,   "_shares_outstanding"),
    ]:
        for r in src:
            end = r["end"]
            slot = by_end.setdefault(end, {"period_end_date": end, "form": r.get("form")})
            slot[key] = int(r["val"]) if r["val"] is not None else None
            if not slot.get("form"):
                slot["form"] = r.get("form")

    rd_ttm = _ttm_sum(rd_rows)
    ga_ttm = _ttm_sum(ga_rows)
    cf_ttm = _ttm_sum(cf_rows)

    out_rows: list[dict] = []
    for end, slot in sorted(by_end.items(), reverse=True):
        cash = slot.get("cash_and_equivalents_usd") or 0
        sti = slot.get("short_term_investments_usd") or 0
        cash_total = cash + sti if (cash or sti) else None

        is_latest = (len(out_rows) == 0)
        rd_use = rd_ttm if is_latest else None
        ga_use = ga_ttm if is_latest else None
        cf_use = cf_ttm if is_latest else None
        burn_q = abs(cf_use) // 4 if (cf_use is not None and cf_use < 0) else None
        runway_m = None
        if cash_total and burn_q and burn_q > 0:
            monthly_burn = burn_q / 3.0
            runway_m = round(cash_total / monthly_burn, 1) if monthly_burn > 0 else None

        pmark = next(
            (r for src in [cash_rows, ta_rows, ar_rows] for r in src if r["end"] == end),
            None,
        )
        period_label = end
        if pmark:
            fy = pmark.get("fy")
            fp = pmark.get("fp")
            if fy and fp:
                period_label = f"{fy}-{fp}"

        out_rows.append({
            "period": period_label,
            "period_end_date": end,
            "form": slot.get("form"),
            "cash_and_equivalents_usd": cash or None,
            "short_term_investments_usd": sti or None,
            "cash_total_usd": cash_total,
            "total_assets_usd": slot.get("total_assets_usd"),
            "total_liabilities_usd": slot.get("total_liabilities_usd"),
            "accounts_receivable_usd": slot.get("accounts_receivable_usd"),
            "ppe_net_usd": slot.get("ppe_net_usd"),
            "rd_expense_ttm_usd": rd_use,
            "ga_expense_ttm_usd": ga_use,
            "quarterly_burn_usd": burn_q,
            "runway_months": runway_m,
            "operating_cf_ttm_usd": cf_use,
            "basic_shares_count": slot.get("_shares_outstanding"),
            "diluted_shares_count": None,        # XBRL diluted not standard
            "prefunded_warrants_count": None,    # 10-Q footnote heuristic — v2
            "fully_diluted_shares_count": None,  # v2
        })

    status = "ok" if out_rows else "partial"
    return CompanyFactsResult(
        status=status, error=None, rows=out_rows,
        etag=resp.etag, last_modified=resp.last_modified,
    )


# ─── submissions (filings list) ──────────────────────────────────────────────

def fetch_recent_filings(
    cik: str, *, since_date_iso: str, forms: Iterable[str],
) -> FilingsResult:
    """Pull the recent filings list and filter by form + filing_date."""
    url = SUBMISSIONS_URL.format(cik=cik)
    try:
        body = _http_get(url)
    except Exception as e:  # noqa: BLE001
        return FilingsResult(status="failed", error=f"{type(e).__name__}: {e}", filings=[])

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        return FilingsResult(status="failed", error=f"JSON: {e}", filings=[])

    recent = payload.get("filings", {}).get("recent", {})
    n = len(recent.get("accessionNumber", []) or [])
    forms_set = {f.upper() for f in forms}
    out: list[dict] = []
    for i in range(n):
        form = (recent.get("form") or [None] * n)[i]
        date = (recent.get("filingDate") or [None] * n)[i]
        if not form or not date:
            continue
        if (form or "").upper() not in forms_set:
            continue
        if date < since_date_iso:
            continue
        out.append({
            "accession_number": (recent.get("accessionNumber") or [None] * n)[i],
            "form": form,
            "filing_date": date,
            "primary_doc": (recent.get("primaryDocument") or [None] * n)[i],
            "report_date": (recent.get("reportDate") or [None] * n)[i],
            "items": (recent.get("items") or [""] * n)[i] if recent.get("items") else "",
        })
    return FilingsResult(status="ok", error=None, filings=out)


# ─── Form 4 parser ───────────────────────────────────────────────────────────

_FORM4_TXN_BUY_CODES = {"P"}
_FORM4_TXN_SELL_CODES = {"S"}
_FORM4_TXN_OPTION_EXERCISE_CODES = {"M"}
_FORM4_TXN_OPTION_GRANT_CODES = {"A"}
_FORM4_TXN_GIFT_CODES = {"G"}


def _classify_txn(code: str, acquired_disposed: str) -> str:
    code = (code or "").upper()
    if code in _FORM4_TXN_BUY_CODES and acquired_disposed == "A":
        return "buy"
    if code in _FORM4_TXN_SELL_CODES and acquired_disposed == "D":
        return "sell"
    if code in _FORM4_TXN_OPTION_EXERCISE_CODES:
        return "option_exercise"
    if code in _FORM4_TXN_OPTION_GRANT_CODES:
        return "option_grant"
    if code in _FORM4_TXN_GIFT_CODES:
        return "gift"
    return "other"


def _classify_role(is_director: bool, is_officer: bool, is_ten_percent: bool,
                   officer_title: str) -> str:
    title_l = (officer_title or "").lower()
    if "chief executive" in title_l or " ceo" in f" {title_l}":
        return "CEO"
    if "chief financial" in title_l or " cfo" in f" {title_l}":
        return "CFO"
    if is_ten_percent:
        return "10% owner"
    if is_director:
        return "Director"
    if is_officer:
        return "Officer"
    return "Other"


def _build_form4_url(cik: str, accession: str, doc: Optional[str]) -> str:
    accn_raw = accession.replace("-", "")
    if doc:
        return f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}/{doc}"
    return f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}/"


def _parse_form4_xml(xml_bytes: bytes) -> tuple[list[dict], Optional[str]]:
    """Returns ([txn_rows], parse_error_or_None)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        return [], f"xml parse: {e}"

    rep = root.find("reportingOwner")
    insider_name: str = ""
    insider_cik: Optional[str] = None
    is_director = False
    is_officer = False
    is_ten_percent = False
    officer_title = ""
    if rep is not None:
        rid = rep.find("reportingOwnerId")
        if rid is not None:
            insider_name = (rid.findtext("rptOwnerName") or "").strip()
            insider_cik = (rid.findtext("rptOwnerCik") or "").strip() or None
        rrel = rep.find("reportingOwnerRelationship")
        if rrel is not None:
            is_director = (rrel.findtext("isDirector") or "").strip() in ("1", "true")
            is_officer = (rrel.findtext("isOfficer") or "").strip() in ("1", "true")
            is_ten_percent = (rrel.findtext("isTenPercentOwner") or "").strip() in ("1", "true")
            officer_title = (rrel.findtext("officerTitle") or "").strip()

    role = _classify_role(is_director, is_officer, is_ten_percent, officer_title)
    out: list[dict] = []

    def _push_txn(txn, is_derivative: bool) -> None:
        txn_date = (txn.findtext("transactionDate/value") or "").strip() or None
        code = (txn.findtext("transactionCoding/transactionCode") or "").strip()
        ad = (txn.findtext("transactionAmounts/transactionAcquiredDisposedCode/value") or "").strip()
        shares_raw = (txn.findtext("transactionAmounts/transactionShares/value") or "").strip()
        price_raw = (txn.findtext("transactionAmounts/transactionPricePerShare/value") or "").strip()
        try:
            shares = int(float(shares_raw)) if shares_raw else None
        except ValueError:
            shares = None
        try:
            price = float(price_raw) if price_raw else None
        except ValueError:
            price = None
        total = int(shares * price) if (shares is not None and price is not None) else None
        out.append({
            "insider_name": insider_name,
            "insider_cik": insider_cik,
            "role": role,
            "txn_type": _classify_txn(code, ad),
            "shares": shares,
            "price_usd": price,
            "total_value_usd": total,
            "transaction_date": txn_date,
        })

    for txn in root.iterfind("nonDerivativeTable/nonDerivativeTransaction"):
        _push_txn(txn, is_derivative=False)
    for txn in root.iterfind("derivativeTable/derivativeTransaction"):
        _push_txn(txn, is_derivative=True)

    return out, None


def _list_accession_files(cik: str, accn_raw: str) -> list[str]:
    """D56 — fetch the EDGAR index.json directory listing for an accession.
    Returns the list of file basenames in that accession directory, or [] on
    any failure. One cheap (~5 KB) call per filing — replaces the previous
    guess-and-404 chain when the basename heuristic misses."""
    url = f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}/index.json"
    try:
        body = _http_get(url, accept="application/json")
    except Exception:  # noqa: BLE001 — fail-open; caller falls back further
        return []
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return []
    return [
        (it.get("name") or "").strip()
        for it in obj.get("directory", {}).get("item", [])
        if it.get("name")
    ]


def _pick_form4_xml_name(files: list[str]) -> Optional[str]:
    """Choose the Form 4 ownership XML from an accession's file list.
    Skips index/header artifacts; prefers conventional names."""
    xmls = [
        f for f in files
        if f.lower().endswith(".xml") and "index" not in f.lower()
    ]
    if not xmls:
        return None
    for preferred in ("primary_doc.xml", "ownership.xml"):
        if preferred in xmls:
            return preferred
    for f in xmls:
        if f.lower().startswith("wf-form4_"):
            return f
    return xmls[0]


def fetch_form4(cik: str, accession_number: str, primary_doc: Optional[str]) -> Form4Result:
    """Fetch one Form 4 filing's primary XML and parse out transactions.

    Strategy (D56):
      1. Optimistic: try the basename of `primary_doc` (modern filings put
         the XSL stylesheet under `xslF345X05/<name>` and the raw XML at
         `<name>` — strip the prefix and try that first).
      2. On 404, fetch `<accession>/index.json` once and pick the right .xml.
      3. Fetch that exact URL.

    Best case: 1 HTTP call. Worst case: 3 (basename 404 → index → real XML).
    Replaces the previous guess-up-to-4-URLs chain that averaged 2–3 calls
    even when the file existed."""
    accn_raw = accession_number.replace("-", "")
    archive_root = f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}"
    tried: list[str] = []

    def _attempt(url: str) -> Optional[Form4Result]:
        tried.append(url)
        try:
            body = _http_get(url, accept="application/xml,text/xml,*/*")
        except _HttpError as e:
            if e.code == 404:
                return None  # fall through to next candidate
            return Form4Result(status="failed", error=f"HTTP {e.code}", transactions=[])
        except Exception as e:  # noqa: BLE001
            return Form4Result(status="failed", error=f"{type(e).__name__}: {e}", transactions=[])
        rows, err = _parse_form4_xml(body)
        if err:
            return Form4Result(status="partial", error=err, transactions=rows)
        return Form4Result(status="ok", error=None, transactions=rows)

    # Step 1 — optimistic basename try.
    if primary_doc and primary_doc.lower().endswith(".xml"):
        basename = primary_doc.rsplit("/", 1)[-1]
        result = _attempt(f"{archive_root}/{basename}")
        if result is not None:
            return result

    # Step 2 — directory discovery.
    files = _list_accession_files(cik, accn_raw)
    name = _pick_form4_xml_name(files)
    if name:
        result = _attempt(f"{archive_root}/{name}")
        if result is not None:
            return result

    return Form4Result(
        status="failed",
        error=f"no Form 4 XML found (tried: {tried})",
        transactions=[],
    )


# ─── Capital raises (8-K Items 1.01/3.02 + S-3 + 424B5) ──────────────────────

_RE_GROSS_PROCEEDS = re.compile(
    r"\$?\s*([\d,]+(?:\.\d+)?)\s*(million|billion|m\b|bn\b)?\s*(?:in\s+)?(?:gross|aggregate)?\s*"
    r"(?:offering|proceeds|aggregate offering price)",
    re.IGNORECASE,
)
_RE_SHELF_CAPACITY = re.compile(
    r"up\s+to\s+\$?\s*([\d,]+(?:\.\d+)?)\s*(million|billion)?",
    re.IGNORECASE,
)
_RE_PRICE_PER_SHARE = re.compile(
    r"\$\s*(\d+(?:\.\d+)?)\s*per\s*share",
    re.IGNORECASE,
)


def _scale_amount(num_str: str, unit: Optional[str]) -> Optional[int]:
    try:
        n = float(num_str.replace(",", ""))
    except ValueError:
        return None
    u = (unit or "").lower()
    if u.startswith("b"):
        return int(n * 1_000_000_000)
    if u.startswith("m"):
        return int(n * 1_000_000)
    return int(n)


def _classify_raise_type(form: str, items: str, body_excerpt: str) -> str:
    items_l = (items or "").lower()
    body_l = (body_excerpt or "").lower()
    if "pre-funded" in body_l or "prefunded" in body_l:
        return "pfw"
    if form.upper().startswith("S-3"):
        return "shelf"
    if form.upper() == "424B5":
        return "equity"
    if "1.01" in items_l:
        return "debt" if "credit" in body_l or "note" in body_l else "equity"
    if "3.02" in items_l:
        return "equity"
    return "unknown"


def fetch_capital_raise(
    cik: str, filing: dict, *, max_body_bytes: int = 200_000,
) -> Optional[dict]:
    """Best-effort parse of one filing into a capital_raises row."""
    accession = filing["accession_number"]
    form = filing["form"]
    accn_raw = accession.replace("-", "")
    primary = filing.get("primary_doc") or ""
    primary_url = f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}/{primary}" if primary else \
                  f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}/"

    body_excerpt = ""
    try:
        body = _http_get(primary_url, accept="text/html,*/*")
        body_excerpt = body[:max_body_bytes].decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        log.debug("capital_raise: body fetch failed for %s: %s", accession, e)

    text = re.sub(r"<[^>]+>", " ", body_excerpt)
    text = re.sub(r"\s+", " ", text)

    gross: Optional[int] = None
    pps: Optional[float] = None
    shares_issued: Optional[int] = None

    if form.upper().startswith("S-3"):
        m = _RE_SHELF_CAPACITY.search(text)
        if m:
            gross = _scale_amount(m.group(1), m.group(2))
    else:
        m = _RE_GROSS_PROCEEDS.search(text)
        if m:
            gross = _scale_amount(m.group(1), m.group(2))
        m = _RE_PRICE_PER_SHARE.search(text)
        if m:
            try:
                pps = float(m.group(1))
            except ValueError:
                pps = None
        if gross and pps:
            shares_issued = int(gross / pps) if pps > 0 else None

    raise_type = _classify_raise_type(form, filing.get("items", ""), text[:5000])
    description = text[:200].strip() or None
    status = "ok" if (gross is not None or form.upper().startswith("S-3")) else "partial"

    return {
        "form": form,
        "filing_date": filing["filing_date"],
        "accession_number": accession,
        "raise_type": raise_type,
        "gross_proceeds_usd": gross,
        "net_proceeds_usd": None,
        "shares_issued": shares_issued,
        "price_per_share_usd": pps,
        "discount_to_market_pct": None,
        "description": description,
        "raw_filing_url": primary_url,
        "fetch_status": status,
    }
