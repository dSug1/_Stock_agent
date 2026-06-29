"""SEC EDGAR full-text — the 10-K Item 1 "Business" section (spec §6, B5 / decisions D17).

yfinance's ``longBusinessSummary`` is a one-paragraph blurb; the 10-K **Item 1 ("Business")** is the
company's own multi-page description of its technology, platform, and pipeline — by far the richest free
text signal for the data-engine judgment (axes A/B). This harvester fetches a US filer's latest annual
report (10-K / 20-F / 40-F), extracts the Business section, and stores a capped excerpt as Stage-2
evidence (source ``edgar``). ``build_bundle`` then feeds an excerpt to the scorer.

Pure parsers (`_strip_html`, `_extract_item1`, `_latest_annual_report`) are unit-tested; the fetch is
fail-open (→ None) and uses the SEC ``User-Agent`` loaded from .env by ``_net`` (SEC fair-access). US
filers only — a non-US ticker has no CIK and returns None. All reads go through ``_net``'s 64MB cap.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Optional

from . import _net

log = logging.getLogger(__name__)

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

# Forms that carry an Item-1-style "Business" narrative, best first.
_ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")

# Max chars of Business text persisted to evidence (durable) and excerpted into the bundle.
MAX_BUSINESS_CHARS = 16000
BUNDLE_EXCERPT_CHARS = 2800

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"[ \t ]+")
_MULTINL_RE = re.compile(r"\n{3,}")
# "Item 1." Business … up to "Item 1A." (Risk Factors) or "Item 2." — tolerant of &nbsp;/ markup-stripped
# spacing and an optional "Business" caption. Anchored to start-of-line to avoid table-of-contents hits.
_ITEM1_RE = re.compile(
    r"item\s*1\.?\s*(?:business)?\b(.*?)(?=item\s*1a\.?\s|item\s*2\.?\s)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_html(raw: str) -> str:
    """HTML/SGML → readable plain text: drop script/style, unescape entities, strip tags, tidy WS."""
    if not raw:
        return ""
    txt = _SCRIPT_STYLE_RE.sub(" ", raw)
    txt = re.sub(r"<br\b[^>]*>", "\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"</(p|div|tr|h[1-6]|li)\s*>", "\n", txt, flags=re.IGNORECASE)
    txt = _TAG_RE.sub(" ", txt)
    txt = html.unescape(txt)
    txt = _WS_RE.sub(" ", txt)
    txt = _MULTINL_RE.sub("\n\n", txt)
    return txt.strip()


def _extract_item1(text: str) -> str:
    """Extract the Item 1 'Business' narrative from stripped 10-K text; '' if not locatable.

    Picks the LONGEST Item-1 match (a 10-K repeats the heading in its table of contents — the real
    section is the long one). Caps to ``MAX_BUSINESS_CHARS``."""
    if not text:
        return ""
    matches = [m.group(1).strip() for m in _ITEM1_RE.finditer(text)]
    body = max(matches, key=len) if matches else ""
    if len(body) < 400:                       # too short -> heading-only / ToC hit; not the real section
        return ""
    return body[:MAX_BUSINESS_CHARS]


def _latest_annual_report(payload: dict) -> Optional[dict]:
    """From a submissions payload, return the most recent annual report's
    {form, filing_date, accession, primary_doc}, or None. Recent filings only (the proxy convention)."""
    recent = (((payload or {}).get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accs = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    best: Optional[dict] = None
    for i, form in enumerate(forms):
        if form not in _ANNUAL_FORMS:
            continue
        if i >= len(dates) or i >= len(accs) or i >= len(docs):
            continue
        date = dates[i]
        if best is None or (isinstance(date, str) and date > best["filing_date"]):
            best = {"form": form, "filing_date": date,
                    "accession": accs[i], "primary_doc": docs[i]}
    return best


def fetch(company, *, ticker_cik_map: dict[str, int],
          limiter: Optional[_net.RateLimiter] = None) -> Optional[tuple[dict, str]]:
    """Harvest the latest 10-K Item 1 Business text for ``company``. Returns (summary, cursor) | None.

    ``ticker_cik_map`` (from ``sec_submissions.build_ticker_cik_map``) is passed so the cik↔ticker file
    is fetched once per run. Fail-open: any miss/error → None (the company simply gets no edgar evidence,
    which ``build_bundle`` reports as ABSENT DATA, never a penalty)."""
    cik = ticker_cik_map.get((getattr(company, "primary_ticker", "") or "").strip().upper())
    if cik is None:
        return None
    payload = _net.safe_json(SUBMISSIONS.format(cik=int(cik)), limiter=limiter)
    rpt = _latest_annual_report(payload) if payload else None
    if not rpt or not rpt.get("primary_doc"):
        return None
    acc_nodash = str(rpt["accession"]).replace("-", "")
    url = ARCHIVE.format(cik=int(cik), acc=acc_nodash, doc=rpt["primary_doc"])
    try:
        raw = _net.get_text(url, limiter=limiter)
    except Exception as exc:                                  # fail-open
        log.debug("edgar fetch failed for %s: %s", getattr(company, "primary_ticker", "?"), exc)
        return None
    business = _extract_item1(_strip_html(raw or ""))
    if not business:
        return None
    summary = {
        "form": rpt["form"], "filing_date": rpt["filing_date"], "accession": rpt["accession"],
        "source_url": url, "item1_business": business, "chars": len(business),
    }
    return summary, str(rpt["accession"])
