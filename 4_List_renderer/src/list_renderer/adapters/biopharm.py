"""Adapter: 3_Biopharmcatalyst_parser/data/biotech.db -> list_renderer payload.

Selects the top-N distinct tickers by composite_score and maps each ticker's
best catalyst row onto the generic result schema that the stable
Outputs/list_results.html template renders. Same template, different source —
an example of adaptive HTML display.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from list_renderer.render import build_payload

# One row per ticker: its highest-composite catalyst (ties -> latest snapshot),
# joined to the snapshot for the human-readable catalyst text + context.
_TOP_SQL = """
WITH ranked AS (
    SELECT sc.ticker, sc.composite_score, sc.drug, sc.nct_number,
           sc.next_catalyst_type, sc.snapshot_date, sc.hard_pass, sc.rescued,
           ROW_NUMBER() OVER (
               PARTITION BY sc.ticker
               ORDER BY sc.composite_score DESC, sc.snapshot_date DESC
           ) AS rn
    FROM catalyst_scores sc
    WHERE sc.composite_score IS NOT NULL
)
SELECT r.ticker, r.composite_score, r.drug, r.next_catalyst_type,
       r.hard_pass, r.rescued,
       s.name, s.stage, s.status, s.indication, s.catalyst_date,
       s.catalyst_text, s.historical_pop, s.market_cap_usd
FROM ranked r
LEFT JOIN catalyst_snapshots s
  ON  s.snapshot_date = r.snapshot_date
  AND s.ticker = r.ticker
  AND s.drug = r.drug
  AND IFNULL(s.nct_number, '') = IFNULL(r.nct_number, '')
  AND s.next_catalyst_type = r.next_catalyst_type
WHERE r.rn = 1
ORDER BY r.composite_score DESC
LIMIT ?
"""


def _pretty_stage(stage: str | None) -> str:
    if not stage:
        return ""
    s = str(stage).strip()
    if s.lower().startswith("phase"):
        return "Phase " + s[5:].strip()
    return s[:1].upper() + s[1:]


def _fmt_mcap(value) -> str | None:
    if not value:
        return None
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if value >= div:
            return f"${value / div:.1f}{unit}"
    return f"${value:,.0f}"


def _result_from_row(row: sqlite3.Row) -> dict:
    ticker = row["ticker"]
    name = row["name"] or ticker
    drug = (row["drug"] or "Catalyst").strip()
    cat_type = (row["next_catalyst_type"] or "Catalyst").strip()
    stage = _pretty_stage(row["stage"])
    score = row["composite_score"]
    passed = bool(row["hard_pass"] or row["rescued"])
    catalyst_text = (row["catalyst_text"] or "").strip()
    indication = (row["indication"] or "").strip()
    cdate = row["catalyst_date"]
    pop = row["historical_pop"]
    mcap = _fmt_mcap(row["market_cap_usd"])

    # Breadcrumb mimics a URL path: TICKER › Stage › Catalyst type.
    crumb_parts = [ticker] + [p for p in (stage, cat_type) if p]
    breadcrumb = " › ".join(crumb_parts)

    # Snippet leads with the BPC catalyst text, then context + the score.
    sentences = []
    if catalyst_text:
        sentences.append(catalyst_text.rstrip(". ") + ".")
    if indication:
        sentences.append(f"Indication: {indication}.")
    tail = [f"Composite score {score:.1f}"]
    if cdate:
        tail.append(f"{cat_type.lower()} expected {cdate}")
    if pop is not None:
        tail.append(f"historical PoP {pop:.0f}%")
    if mcap:
        tail.append(f"market cap {mcap}")
    sentences.append("; ".join(tail) + ".")
    snippet = " ".join(sentences)

    bold = [ticker, drug, f"{score:.1f}"]
    if cdate:
        bold.append(str(cdate))

    quote_url = f"https://finance.yahoo.com/quote/{ticker}"
    return {
        "site_name": name,
        "url_breadcrumb": breadcrumb,
        "favicon": None,  # -> letter avatar (ticker initial)
        "title": f"{ticker} — {drug}",
        "title_url": quote_url,
        "verified": passed,
        "snippet": snippet,
        "bold_terms": bold,
        "read_more_url": quote_url,
    }


def build_payload_from_biotech_db(db_path: Path, top_n: int = 10) -> dict:
    """Read biotech.db and return the window.LIST_DATA payload."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"biotech.db not found: {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(_TOP_SQL, (top_n,)).fetchall()
    finally:
        conn.close()

    results = [_result_from_row(r) for r in rows]
    return build_payload(
        query=f"top {len(results)} tickers by composite score",
        brand="Biopharm Catalysts",
        results=results,
    )
