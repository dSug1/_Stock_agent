"""Module 6.5 — SEC EDGAR fetchers (companyfacts XBRL + capital raises).

Copy-adapted from `2_Funds_parser/src/module_4c/edgar_client.py` per
spec §1.7 (3_Biopharm style: copy-adapt, don't cross-project import).
HTTP underlay delegates to `module_2.edgar_client.http_get` so M2, M3,
M6.5 all share the same process-global rate limiter (9.5 req/s under
SEC's 10/s fair-use cap).

Two entry points:
  fetch_companyfacts(cik) -> CompanyFactsResult
      Parses companyfacts XBRL into 8 most recent (period_end_date, value,
      form, fy, fp, accn) rows for cash / R&D / G&A / op-CF / shares /
      total assets / total liabilities. Computes TTM aggregates for the
      latest period and per-quarter burn + runway.

  fetch_capital_raise(cik, filing) -> dict | None
      Parses one 8-K/S-3/424B5 filing body for gross proceeds, price-per-
      share, shares issued, and classifies raise_type. Detects PFWs by
      "pre-funded" / "prefunded" in the body.

Fail-open: any HTTP / parse failure returns a result with
`status='failed'` or `status='partial'` and an `error` string; the
caller logs to fetch_log and continues.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional

# Reuse the existing 3_Biopharm M2 HTTP layer — single process-global
# rate limiter shared across M2, M3, M6.5.
from module_2.edgar_client import (  # type: ignore
    ARCHIVES_BASE,
    SUBMISSIONS_URL,
    HttpError,
    http_get,
)

log = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# US-GAAP concepts we pull. First alias that exists in the payload wins.
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


# ───────────────────────── result containers ─────────────────────────


@dataclass
class CompanyFactsResult:
    status: str                           # 'ok' | 'partial' | 'failed'
    error: Optional[str]
    rows: list[dict]
    raw_payload: Optional[str] = None      # JSON text of the full XBRL response


@dataclass
class FilingsResult:
    status: str
    error: Optional[str]
    filings: list[dict]


# ───────────────────────── companyfacts ──────────────────────────────


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
        # USD preferred; share-count concepts use 'shares'.
        unit_key = unit if unit in units else next(iter(units.keys()), None)
        if not unit_key:
            continue
        rows = units.get(unit_key, []) or []
        clean = [r for r in rows if r.get("end") and r.get("val") is not None]
        clean.sort(key=lambda r: r.get("end"), reverse=True)
        # Dedup by period_end_date keeping latest-filed.
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


def fetch_companyfacts(cik: str) -> CompanyFactsResult:
    """Fetch + parse SEC companyfacts XBRL.

    Returns up to 8 most recent periods (most recent first). TTM aggregates
    populate only the latest row (rd_expense_ttm_usd, ga_expense_ttm_usd,
    operating_cf_ttm_usd, quarterly_burn_usd, runway_months).
    """
    url = COMPANYFACTS_URL.format(cik=str(cik).zfill(10))
    try:
        body = http_get(url, accept="application/json")
    except HttpError as e:
        if e.code == 404:
            return CompanyFactsResult(status="partial", error="no companyfacts (404)", rows=[])
        return CompanyFactsResult(status="failed", error=f"HTTP {e.code}: {e.reason}", rows=[])
    except Exception as e:                                       # noqa: BLE001
        return CompanyFactsResult(status="failed", error=f"{type(e).__name__}: {e}", rows=[])

    try:
        facts = json.loads(body)
    except json.JSONDecodeError as e:
        return CompanyFactsResult(status="failed", error=f"JSON: {e}", rows=[])

    raw_payload = body.decode("utf-8", errors="replace")

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
        return CompanyFactsResult(status="partial", error="no cash concept", rows=[],
                                  raw_payload=raw_payload)

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
            "period":                   period_label,
            "period_end_date":          end,
            "form":                     slot.get("form"),
            "cash_and_equivalents_usd": cash or None,
            "short_term_investments_usd": sti or None,
            "cash_total_usd":           cash_total,
            "total_assets_usd":         slot.get("total_assets_usd"),
            "total_liabilities_usd":    slot.get("total_liabilities_usd"),
            "accounts_receivable_usd":  slot.get("accounts_receivable_usd"),
            "ppe_net_usd":              slot.get("ppe_net_usd"),
            "rd_expense_ttm_usd":       rd_use,
            "ga_expense_ttm_usd":       ga_use,
            "quarterly_burn_usd":       burn_q,
            "runway_months":            runway_m,
            "operating_cf_ttm_usd":     cf_use,
            "basic_shares_count":       slot.get("_shares_outstanding"),
            "diluted_shares_count":     None,            # XBRL diluted not standard
            "prefunded_warrants_count": None,            # filled by pfw_estimator
            "fully_diluted_shares_count": None,          # computed at write time
        })

    return CompanyFactsResult(status="ok", error=None, rows=out_rows,
                              raw_payload=raw_payload)


# ───────────────────────── submissions index ─────────────────────────


def fetch_recent_filings(
    cik: str, *, since_date_iso: str, forms: Iterable[str],
) -> FilingsResult:
    """Pull the submissions.recent filings list and filter."""
    url = SUBMISSIONS_URL.format(cik=str(cik).zfill(10))
    try:
        body = http_get(url, accept="application/json")
    except HttpError as e:
        return FilingsResult(status="failed", error=f"HTTP {e.code}: {e.reason}", filings=[])
    except Exception as e:                                       # noqa: BLE001
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
            "form":             form,
            "filing_date":      date,
            "primary_doc":      (recent.get("primaryDocument") or [None] * n)[i],
            "report_date":      (recent.get("reportDate") or [None] * n)[i],
            "items":            (recent.get("items") or [""] * n)[i] if recent.get("items") else "",
        })
    return FilingsResult(status="ok", error=None, filings=out)


# ───────────────────────── capital raises ────────────────────────────


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
# PFW-specific share-count patterns. The naive `gross / pps` heuristic blows
# up for PFWs because `pps` matches the warrant **exercise** price ($0.0001)
# instead of the offering price, yielding billions of shares. These patterns
# look for explicit share-count language in PFW prospectuses.
_RE_PFW_SHARES_BY_WARRANTS = re.compile(
    r"([\d,]+)\s+(?:pre-funded|prefunded)\s+warrants",
    re.IGNORECASE,
)
_RE_PFW_SHARES_TO_PURCHASE = re.compile(
    r"(?:pre-funded|prefunded)\s+warrants?\s+to\s+purchase\s+(?:up\s+to\s+)?"
    r"(?:an\s+aggregate\s+of\s+)?([\d,]+)\s+(?:additional\s+)?shares",
    re.IGNORECASE,
)
_RE_PFW_AGGREGATE_OF_SHARES = re.compile(
    r"(?:aggregate\s+of\s+)?([\d,]+)\s+shares?\s+(?:of\s+common\s+stock\s+)?"
    r"(?:underlying|issuable\s+upon\s+exercise\s+of)\s+(?:our\s+|the\s+)?"
    r"(?:pre-funded|prefunded)\s+warrants",
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


def extract_pfw_share_count(text: str) -> Optional[int]:
    """Best-effort share-count extractor for PFW filings.

    Tries three patterns in order of specificity:
      1. "X pre-funded warrants"                        (most reliable cover-page count)
      2. "pre-funded warrants to purchase up to X shares"
      3. "aggregate of X shares … underlying our pre-funded warrants"

    Returns None when no pattern matches. We deliberately do NOT fall back
    to `gross / pps` because for PFWs `pps` matches the warrant exercise
    price ($0.0001) — yielding billions of bogus shares.

    Heuristic guards:
      - Reject matches with absurdly large counts (> 5e9 = ~500% of any
        biotech's float). These are almost always parser artifacts (the
        regex caught a multi-digit string from elsewhere in the filing).
      - Require >= 1000 shares to filter low-noise matches like "1 share".
    """
    if not text:
        return None
    for rx in (_RE_PFW_SHARES_BY_WARRANTS,
               _RE_PFW_SHARES_TO_PURCHASE,
               _RE_PFW_AGGREGATE_OF_SHARES):
        for m in rx.finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                n = int(raw)
            except ValueError:
                continue
            if 1000 <= n <= 5_000_000_000:
                return n
    return None


def classify_raise_type(form: str, items: str, body_excerpt: str) -> str:
    """'pfw' | 'shelf' | 'equity' | 'debt' | 'unknown'.

    PFW classification fires when the body text contains 'pre-funded' or
    'prefunded' — the heuristic spec'd in module_7_spec.md §4.3.
    """
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
    """Best-effort parse of one filing into a capital_raises row.

    Always returns a row (even on partial parse) so the caller can persist
    the audit trail. `fetch_status` field carries the success indicator.
    """
    accession = filing["accession_number"]
    form = filing["form"]
    accn_raw = accession.replace("-", "")
    primary = filing.get("primary_doc") or ""
    primary_url = (
        f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}/{primary}" if primary
        else f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}/"
    )

    body_excerpt = ""
    try:
        body = http_get(primary_url, accept="text/html,*/*")
        body_excerpt = body[:max_body_bytes].decode("utf-8", errors="replace")
    except Exception as e:                                       # noqa: BLE001
        log.debug("capital_raise: body fetch failed for %s: %s", accession, e)

    text = re.sub(r"<[^>]+>", " ", body_excerpt)
    text = re.sub(r"\s+", " ", text)

    gross: Optional[int] = None
    pps: Optional[float] = None
    shares_issued: Optional[int] = None

    raise_type = classify_raise_type(form, filing.get("items", ""), text[:5000])

    if form.upper().startswith("S-3"):
        m = _RE_SHELF_CAPACITY.search(text)
        if m:
            gross = _scale_amount(m.group(1), m.group(2))
    elif raise_type == "pfw":
        # PFW-specific path: extract share count directly from prospectus
        # language. Never compute shares = gross / pps for PFWs — `pps`
        # matches the warrant exercise price ($0.0001), not the offering
        # price, and would yield billions of bogus shares.
        shares_issued = extract_pfw_share_count(text)
        m = _RE_GROSS_PROCEEDS.search(text)
        if m:
            gross = _scale_amount(m.group(1), m.group(2))
        # pps left as None for PFW filings — meaningless without the
        # offering price (which the regex can't disambiguate from
        # exercise price in the body excerpt).
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
    description = text[:200].strip() or None
    status = "ok" if (gross is not None or form.upper().startswith("S-3")) else "partial"

    return {
        "form":                  form,
        "filing_date":            filing["filing_date"],
        "event_date":             filing.get("report_date"),
        "accession_number":       accession,
        "raise_type":             raise_type,
        "gross_proceeds_usd":     gross,
        "net_proceeds_usd":       None,
        "shares_issued":          shares_issued,
        "price_per_share_usd":    pps,
        "discount_to_market_pct": None,
        "description":            description,
        "raw_filing_url":         primary_url,
        "fetch_status":           status,
    }
