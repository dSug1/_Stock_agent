"""Point-in-time first-print fundamentals from SEC EDGAR companyfacts (Stage A; decisions D16/D17).

For `m_share` labelling we need each panel name's **revenue** and **share count** as they stood at
`t0` — first-print (the value as originally filed ≤ t0, never a later restatement; Protocol §1) and
survivorship-free (present even after a delisting). SEC companyfacts gives exactly this for free.

Why a 5_Hype-local module rather than importing `2_Funds_parser/module_4c`:
  - module_4c returns only the **8 most-recent** periods and drops the per-fact **`filed`** date — but
    the panel needs the FULL history WITH filing dates so an as-of-t0 lookup is leak-free;
  - module_4c hard-imports `layer_1.*`, which would break 5_Hype's standalone architecture (D3).
We reuse module_4c's proven GAAP-concept knowledge (and add the Revenues tags), but keep this client
self-contained, stdlib-only, injectable (offline tests) and fail-open — the 5_Hype house style.
"""

import json
import logging
import urllib.request
from datetime import date

from .db import now_iso
from .nethttp import capped_read

log = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
USER_AGENT = "HypeParser/0.1 research (admin@hypeparser.local)"

# Revenue: the common US-GAAP top-line tags (filers migrated from SalesRevenueNet -> Revenues ->
# RevenueFromContractWithCustomer... over 2016-2019, so we union them). Shares: us-gaap + the dei
# entity tag. (Concept knowledge ported from 2_Funds_parser/module_4c, plus the Revenues tags.)
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]
SHARES_CONCEPTS = ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"]


# ─── HTTP (injectable) ───────────────────────────────────────────────────────

def _default_http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return capped_read(resp)        # 64 MiB size guard (audit S10 / D37) — fail-open per house style


def load_ticker_cik_map(http_get=None) -> dict:
    """{TICKER: 10-digit zero-padded CIK} from SEC's company_tickers.json. {} on failure."""
    http_get = http_get or _default_http_get
    try:
        raw = json.loads(http_get(TICKERS_URL))
    except Exception as exc:
        log.warning("ticker->cik map fetch failed: %s", exc)
        return {}
    out = {}
    for entry in (raw.values() if isinstance(raw, dict) else raw):
        tk, cik = entry.get("ticker"), entry.get("cik_str")
        if tk and cik is not None:
            out[str(tk).upper()] = str(cik).zfill(10)
    return out


# ─── companyfacts extraction (FULL history + filed dates) ────────────────────

def _iter_concept_rows(facts: dict, aliases: list):
    """Yield every reported fact row across us-gaap + dei namespaces and all units, for the given
    concept aliases. Each row keeps its `filed` date (the PIT key) and period span."""
    facts_node = facts.get("facts", {}) or {}
    for ns in ("us-gaap", "dei"):
        ns_node = facts_node.get(ns, {}) or {}
        for alias in aliases:
            concept = ns_node.get(alias)
            if not concept:
                continue
            for unit, rows in (concept.get("units") or {}).items():
                for r in rows or []:
                    if r.get("end") and r.get("filed") and r.get("val") is not None:
                        yield {
                            "unit": unit,
                            "period_start": r.get("start"),
                            "period_end": r["end"],
                            "val": float(r["val"]),
                            "filed": r["filed"],
                            "form": r.get("form"),
                            "fy": r.get("fy"),
                            "fp": r.get("fp"),
                        }


def extract_facts(facts: dict) -> list:
    """Companyfacts JSON -> flat rows tagged concept='revenue'|'shares'. Dedups by
    (concept, period_end, filed) keeping the first (an end can appear under several aliases)."""
    out, seen = [], set()
    for concept, aliases in (("revenue", REVENUE_CONCEPTS), ("shares", SHARES_CONCEPTS)):
        for r in _iter_concept_rows(facts, aliases):
            key = (concept, r["period_end"], r["filed"])
            if key in seen:
                continue
            seen.add(key)
            out.append({**r, "concept": concept})
    return out


def fetch_company_facts(ticker: str, cik: str, http_get=None):
    """Returns (status, rows, error). status: ok | partial(no facts/404) | failed."""
    http_get = http_get or _default_http_get
    url = COMPANYFACTS_URL.format(cik=str(cik).zfill(10))
    try:
        body = http_get(url)
    except Exception as exc:
        msg = str(exc)
        if "404" in msg:
            return "partial", [], "no companyfacts (404)"
        return "failed", [], f"{type(exc).__name__}: {exc}"
    try:
        facts = json.loads(body)
    except (json.JSONDecodeError, TypeError) as exc:
        return "failed", [], f"JSON: {exc}"
    rows = extract_facts(facts)
    return ("ok" if rows else "partial"), rows, (None if rows else "no revenue/shares concepts")


# ─── storage ─────────────────────────────────────────────────────────────────

def upsert_facts(conn, ticker: str, cik, rows: list) -> int:
    conn.executemany(
        """
        INSERT INTO company_facts
            (ticker, cik, concept, unit, period_start, period_end, val, filed, form, fy, fp)
        VALUES (:ticker,:cik,:concept,:unit,:period_start,:period_end,:val,:filed,:form,:fy,:fp)
        ON CONFLICT(ticker, concept, period_end, filed) DO UPDATE SET
            val=excluded.val, form=excluded.form, unit=excluded.unit,
            period_start=excluded.period_start, fy=excluded.fy, fp=excluded.fp
        """,
        [{"ticker": ticker.upper(), "cik": str(cik).zfill(10) if cik else None, **r} for r in rows],
    )
    conn.commit()
    return len(rows)


def log_fetch(conn, ticker, cik, status, error, n_rows):
    conn.execute(
        "INSERT INTO company_facts_log (ticker, cik, status, error, n_rows, fetched_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
        "cik=excluded.cik, status=excluded.status, error=excluded.error, "
        "n_rows=excluded.n_rows, fetched_at=excluded.fetched_at",
        (ticker.upper(), str(cik).zfill(10) if cik else None, status, error, n_rows, now_iso()))
    conn.commit()


def read_facts(conn, ticker: str, concept: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM company_facts WHERE ticker=? AND concept=? ORDER BY period_end, filed",
        (ticker.upper(), concept))]


# ─── point-in-time as_of lookups ─────────────────────────────────────────────

def _span_days(r) -> int:
    try:
        s = date.fromisoformat(r["period_start"])
        e = date.fromisoformat(r["period_end"])
        return (e - s).days
    except (KeyError, TypeError, ValueError):
        return 0


def _is_annual(r) -> bool:
    return (r.get("form") or "").startswith("10-K") or 350 <= _span_days(r) <= 380


def shares_as_of(rows: list, as_of: str):
    """Share count as known at `as_of`: among rows FILED <= as_of, the most-recently-ended period
    (tie-break latest filed). None if nothing was filed yet."""
    avail = [r for r in rows if r["filed"] <= as_of]
    if not avail:
        return None
    avail.sort(key=lambda r: (r["period_end"], r["filed"]))
    return avail[-1]["val"]


def revenue_ttm_as_of(rows: list, as_of: str):
    """Trailing-twelve-month revenue as first-known at `as_of`, using only rows FILED <= as_of.
    Annual (10-K / ~365d) preferred; else derive TTM from quarterly rows (true single-quarter sum,
    else cumulative-YTD differencing — ported from module_4c). None if no revenue is known yet
    (=> pre-revenue at this date, per D16 -> m_share=1)."""
    avail = [r for r in rows if r["filed"] <= as_of]
    if not avail:
        return None

    annual = [r for r in avail if _is_annual(r)]
    if annual:
        annual.sort(key=lambda r: (r["period_end"], r["filed"]))
        return annual[-1]["val"]

    # True single-quarter rows (~90d): sum the latest 4 by period_end.
    pure_q = sorted((r for r in avail if 80 <= _span_days(r) <= 100),
                    key=lambda r: r["period_end"], reverse=True)
    if len(pure_q) >= 4:
        return sum(r["val"] for r in pure_q[:4])

    # Cumulative-YTD: difference consecutive periods within each fiscal year, sum the latest 4.
    by_fy = {}
    for r in avail:
        if r.get("fy") is not None:
            by_fy.setdefault(r["fy"], []).append(r)
    derived = []
    for fy_rows in by_fy.values():
        prev = 0.0
        for r in sorted(fy_rows, key=lambda r: r["period_end"]):
            derived.append((r["period_end"], r["val"] - prev))
            prev = r["val"]
    derived.sort(reverse=True)
    if len(derived) >= 4:
        return sum(v for _, v in derived[:4])
    if derived:                       # partial: annualise from the average derived quarter
        return sum(v for _, v in derived) / len(derived) * 4
    return None


def fundamentals_as_of(conn, ticker: str, as_of: str) -> dict:
    """PIT fundamentals snapshot for `ticker` as-of `as_of` (YYYY-MM-DD). `pre_revenue` is True when
    no positive revenue is derivable yet -> Stage B sets m_share=1 (D16)."""
    rev_rows = read_facts(conn, ticker, "revenue")
    sh_rows = read_facts(conn, ticker, "shares")
    revenue_ttm = revenue_ttm_as_of(rev_rows, as_of)
    shares = shares_as_of(sh_rows, as_of)
    pre_revenue = revenue_ttm is None or revenue_ttm <= 0
    return {
        "ticker": ticker.upper(),
        "as_of": as_of,
        "revenue_ttm": revenue_ttm,
        "shares": shares,
        "pre_revenue": pre_revenue,
    }
