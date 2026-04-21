"""Build the per-fund HTML report (Outputs/2_funds_report.html).

One tab per fund in the registry. Each tab shows the latest 13F-HR
filing's date plus a table of positions (company, ticker, CUSIP,
shares, market value). Funds with no filings yet render an empty
placeholder so the file is still structurally complete.

Caching: a sidecar `2_funds_report.cache.json` stores a signature
{fund_id -> (latest_filing_date, holdings_count)}. The HTML is
regenerated only when this signature changes. Delete the cache file
to force a rebuild.

Usage (from repo root):
    python 2_Funds_parser/scripts/2_build_report.py
    python 2_Funds_parser/scripts/2_build_report.py --force
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402

OUTPUTS_DIR = PROJECT_ROOT / "Outputs"
HTML_PATH = OUTPUTS_DIR / "2_funds_report.html"
CACHE_PATH = OUTPUTS_DIR / "2_funds_report.cache.json"


def compute_signature(conn) -> dict:
    """Per-fund latest-filing signature used for cache invalidation.

    Includes `latest_holding_updated_at` so a backfill that populates
    previously-NULL name_of_issuer values triggers a rebuild even
    though the filing count and filing date are unchanged.
    """
    sig: dict = {}
    for row in conn.execute(
        "SELECT f.id, f.name, "
        "       MAX(fl.filing_date) AS latest_date, "
        "       COUNT(fl.id) AS n_filings "
        "FROM funds f "
        "LEFT JOIN filings_log fl ON fl.fund_id = f.id "
        "GROUP BY f.id ORDER BY f.id"
    ):
        latest_updated = None
        if row["latest_date"]:
            ru = conn.execute(
                "SELECT MAX(updated_at) AS u FROM holdings "
                "WHERE fund_id = ? AND filing_date = ?",
                (row["id"], row["latest_date"]),
            ).fetchone()
            latest_updated = ru["u"]
        sig[str(row["id"])] = {
            "name": row["name"],
            "latest_filing_date": row["latest_date"],
            "n_filings": row["n_filings"],
            "latest_holding_updated_at": latest_updated,
        }
    return sig


def load_cached_signature() -> dict | None:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_cache_signature(sig: dict) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(sig, indent=2, sort_keys=True), encoding="utf-8"
    )


def fetch_fund_rows(conn) -> list[dict]:
    """All funds plus their latest filing + holdings (if any)."""
    funds = conn.execute(
        "SELECT id, cik, name, legal_name FROM funds ORDER BY id"
    ).fetchall()

    out = []
    for f in funds:
        latest = conn.execute(
            "SELECT filing_date, period_of_report, accession_number, "
            "       document_url, parse_status "
            "FROM filings_log WHERE fund_id = ? "
            "ORDER BY filing_date DESC LIMIT 1",
            (f["id"],),
        ).fetchone()

        holdings: list = []
        if latest is not None:
            holdings = conn.execute(
                "SELECT name_of_issuer, ticker, ticker_source, cusip, "
                "       shares, market_value "
                "FROM holdings "
                "WHERE fund_id = ? AND filing_date = ? "
                "ORDER BY "
                "  CASE WHEN name_of_issuer IS NULL OR name_of_issuer = '' "
                "       THEN 1 ELSE 0 END, "
                "  name_of_issuer COLLATE NOCASE ASC, "
                "  cusip",
                (f["id"], latest["filing_date"]),
            ).fetchall()

        out.append({
            "id": f["id"],
            "cik": f["cik"],
            "name": f["name"],
            "legal_name": f["legal_name"],
            "latest": dict(latest) if latest else None,
            "holdings": [dict(h) for h in holdings],
        })
    return out


def _fmt_int(n: int | None) -> str:
    return f"{n:,}" if n is not None else ""


def _fmt_usd(n: int | None) -> str:
    return f"${n:,}" if n is not None else ""


def render_html(funds: list[dict], generated_at: str) -> str:
    tabs_html = []
    panels_html = []

    for idx, fund in enumerate(funds):
        active = " active" if idx == 0 else ""
        tab_label = html.escape(fund["name"])
        tabs_html.append(
            f'<button class="tab{active}" data-target="panel-{fund["id"]}">'
            f'{tab_label}</button>'
        )

        latest = fund["latest"]
        holdings = fund["holdings"]
        meta_rows = [
            ("CIK", html.escape(fund["cik"])),
            ("Legal name", html.escape(fund["legal_name"] or "")),
        ]
        if latest:
            total_value = sum(
                h["market_value"] or 0 for h in holdings
            )
            meta_rows.extend([
                ("Latest filing date", html.escape(latest["filing_date"])),
                ("Period of report",
                 html.escape(latest["period_of_report"] or "")),
                ("Accession",
                 html.escape(latest["accession_number"] or "")),
                ("Positions", str(len(holdings))),
                ("Total reported value", _fmt_usd(total_value)),
            ])
            if latest.get("document_url"):
                url = html.escape(latest["document_url"])
                meta_rows.append(
                    ("Source", f'<a href="{url}" target="_blank">EDGAR XML</a>')
                )
        else:
            meta_rows.append((
                "Latest filing date",
                '<em>no filings ingested yet — run '
                '<code>2_ingest_13f.py</code></em>',
            ))

        meta_html = "\n".join(
            f"<dt>{k}</dt><dd>{v}</dd>" for k, v in meta_rows
        )

        if holdings:
            body_rows = []
            for h in holdings:
                ticker = h.get("ticker") or ""
                source = h.get("ticker_source")
                if ticker and source == "sec_name":
                    ticker_cell = (
                        f"<span class='ticker-name-matched' "
                        f"title='Resolved via SEC name match, not OpenFIGI "
                        f"— may refer to common stock when the CUSIP is "
                        f"preferred/warrant/etc.'>"
                        f"{html.escape(ticker)}*</span>"
                    )
                else:
                    ticker_cell = html.escape(ticker)
                body_rows.append(
                    "<tr>"
                    f"<td>{html.escape(h.get('name_of_issuer') or '')}</td>"
                    f"<td class='mono'>{html.escape(h.get('cusip') or '')}</td>"
                    f"<td>{ticker_cell}</td>"
                    f"<td class='num'>{_fmt_int(h.get('shares'))}</td>"
                    f"<td class='num'>{_fmt_usd(h.get('market_value'))}</td>"
                    "</tr>"
                )
            # Column order mirrors the SEC 13F information table:
            # Name of Issuer, CUSIP, ... so name sits directly in front
            # of CUSIP. Sorted ascending by name_of_issuer (blanks last).
            table_html = (
                "<table class='holdings'>"
                "<thead><tr>"
                "<th>Name of Issuer</th><th>CUSIP</th><th>Ticker</th>"
                "<th class='num'>Shares</th>"
                "<th class='num'>Market value (USD)</th>"
                "</tr></thead>"
                f"<tbody>{''.join(body_rows)}</tbody>"
                "</table>"
            )
        else:
            table_html = (
                "<p class='empty'>No holdings to display. "
                "Fields will populate after the next successful ingest.</p>"
            )

        panels_html.append(
            f'<section class="panel{active}" id="panel-{fund["id"]}">'
            f'<h2>{tab_label}</h2>'
            f'<dl class="meta">{meta_html}</dl>'
            f'{table_html}'
            '</section>'
        )

    style = """
    :root { --bg:#fafafa; --panel:#fff; --border:#ddd; --accent:#1a73e8;
            --muted:#666; --row:#f5f5f5; }
    * { box-sizing: border-box; }
    body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,
           "Segoe UI",Roboto,sans-serif; background:var(--bg); color:#222; }
    header { padding:16px 24px; background:var(--panel);
             border-bottom:1px solid var(--border); }
    header h1 { margin:0; font-size:18px; }
    header .generated { color:var(--muted); font-size:12px; }
    .tabs { display:flex; flex-wrap:wrap; gap:4px; padding:8px 16px;
            background:var(--panel); border-bottom:1px solid var(--border);
            position:sticky; top:0; z-index:10; }
    .tab { padding:6px 12px; border:1px solid var(--border);
           background:#f0f0f0; border-radius:4px; cursor:pointer;
           font-size:13px; color:#333; }
    .tab:hover { background:#e6e6e6; }
    .tab.active { background:var(--accent); color:#fff;
                  border-color:var(--accent); }
    main { padding:16px 24px; }
    .panel { display:none; background:var(--panel);
             border:1px solid var(--border); border-radius:6px;
             padding:16px 20px; }
    .panel.active { display:block; }
    .panel h2 { margin:0 0 12px; font-size:16px; }
    dl.meta { display:grid; grid-template-columns:auto 1fr;
              gap:4px 12px; margin:0 0 16px; font-size:13px; }
    dl.meta dt { color:var(--muted); }
    dl.meta dd { margin:0; }
    table.holdings { border-collapse:collapse; width:100%;
                     font-size:12px; }
    table.holdings th, table.holdings td { padding:4px 8px;
                     border-bottom:1px solid var(--border);
                     text-align:left; }
    table.holdings th { background:var(--row); font-weight:600; }
    table.holdings td.num, table.holdings th.num { text-align:right;
                     font-variant-numeric:tabular-nums; }
    table.holdings td.mono { font-family:ui-monospace,Menlo,Consolas,
                     monospace; }
    .empty { color:var(--muted); font-style:italic; }
    .ticker-name-matched { color:#b45309; font-style:italic; cursor:help; }
    code { background:#f0f0f0; padding:1px 4px; border-radius:3px; }
    """

    script = """
    document.querySelectorAll('.tab').forEach(function(btn){
      btn.addEventListener('click', function(){
        var target = btn.getAttribute('data-target');
        document.querySelectorAll('.tab').forEach(function(b){
          b.classList.toggle('active', b === btn);
        });
        document.querySelectorAll('.panel').forEach(function(p){
          p.classList.toggle('active', p.id === target);
        });
      });
    });
    """

    return (
        "<!doctype html>\n"
        "<html lang='en'><head><meta charset='utf-8'>"
        "<title>2_Funds_parser — fund positions</title>"
        f"<style>{style}</style></head><body>"
        f"<header>"
        f"<h1>2_Funds_parser &mdash; latest 13F-HR positions</h1>"
        f"<div class='generated'>generated {html.escape(generated_at)} "
        f"&middot; {len(funds)} funds tracked "
        f"&middot; tickers in "
        f"<span class='ticker-name-matched'>orange italic*</span> were "
        f"resolved via SEC name match (not OpenFIGI-verified)</div>"
        f"</header>"
        f"<nav class='tabs'>{''.join(tabs_html)}</nav>"
        f"<main>{''.join(panels_html)}</main>"
        f"<script>{script}</script>"
        "</body></html>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="Rebuild the HTML even if the signature is unchanged.",
    )
    args = parser.parse_args()

    conn = get_connection()
    try:
        current = compute_signature(conn)
        cached = load_cached_signature()

        if not args.force and cached == current and HTML_PATH.exists():
            print(
                f"No new filings since last build; {HTML_PATH.name} "
                f"is up to date."
            )
            return 0

        funds = fetch_fund_rows(conn)
    finally:
        conn.close()

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    html_text = render_html(funds, generated_at)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html_text, encoding="utf-8")
    write_cache_signature(current)

    total_positions = sum(len(f["holdings"]) for f in funds)
    with_filings = sum(1 for f in funds if f["latest"])
    print(
        f"Built {HTML_PATH.name}: {len(funds)} funds "
        f"({with_filings} with filings), {total_positions} positions."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
