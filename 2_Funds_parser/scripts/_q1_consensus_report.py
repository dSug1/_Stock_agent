"""Render biotech consensus-build top-50 HTML report.

Compares two quarters of 13F-HR holdings (default: the two most recent
``period_of_report`` values in ``2_fundparser.db``) and emits an HTML
table of biotech names where specialist funds have been adding /
initiating positions.

Originally a one-off Q1-2026-vs-Q4-2025 script; generalized 2026-05-28 so
the auto-refresh runner in ``3_Biopharmcatalyst_parser`` can keep this
report current quarter-over-quarter (see ``3_Biopharmcatalyst_parser/spec/decisions.md`` D13).

Reads ``2_fundparser.db`` (M2 holdings) + ``data/prices.db`` (M4a ticker
snapshots). Writes ``Outputs/<quarter_label>_consensus_builds.html`` where
``quarter_label`` follows the project's ``YYYYQn`` convention (e.g.
``2026Q1``).
"""
from __future__ import annotations

import argparse
import datetime
import html
import math
import sqlite3
import sys


def _quarter_label(iso_date: str) -> str:
    """'2026-03-31' -> '2026Q1'."""
    d = datetime.date.fromisoformat(iso_date)
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


def _two_latest_quarters(conn: sqlite3.Connection) -> tuple[str, str] | None:
    rows = conn.execute(
        "SELECT DISTINCT period_of_report FROM holdings "
        "WHERE period_of_report IS NOT NULL "
        "ORDER BY period_of_report DESC LIMIT 2"
    ).fetchall()
    if len(rows) < 2:
        return None
    return rows[0][0], rows[1][0]


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quarter", default=None,
        help="ISO quarter-end of the LATER quarter (e.g. 2026-03-31). "
             "Default: most recent period_of_report in holdings.",
    )
    parser.add_argument(
        "--prev-quarter", default=None,
        help="ISO quarter-end of the EARLIER comparison quarter (e.g. 2025-12-31). "
             "Default: second-most-recent period_of_report in holdings.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output HTML path. Default: Outputs/<later_quarter_label>_consensus_builds.html",
    )
    args = parser.parse_args()

    H = sqlite3.connect('2_fundparser.db'); H.row_factory = sqlite3.Row
    P = sqlite3.connect('data/prices.db');   P.row_factory = sqlite3.Row

    # Resolve quarters
    if args.quarter and args.prev_quarter:
        later, earlier = args.quarter, args.prev_quarter
    else:
        latest_pair = _two_latest_quarters(H)
        if latest_pair is None:
            print("error: holdings table has fewer than 2 quarters; cannot diff.",
                  file=sys.stderr)
            sys.exit(2)
        later, earlier = latest_pair
        if args.quarter:
            later = args.quarter
        if args.prev_quarter:
            earlier = args.prev_quarter

    later_label = _quarter_label(later)
    earlier_label = _quarter_label(earlier)
    output_path = args.output or f"Outputs/{later_label}_consensus_builds.html"

    # CUSIP -> ticker fallback (history + canonical map)
    cusip_to_ticker: dict[str, str] = {}
    try:
        for r in H.execute("SELECT cusip, ticker FROM cusip_ticker_map WHERE ticker IS NOT NULL AND ticker != ''"):
            cusip_to_ticker[r['cusip']] = r['ticker']
    except Exception:
        pass
    for r in H.execute("SELECT DISTINCT cusip, ticker FROM holdings WHERE ticker IS NOT NULL AND ticker != ''"):
        cusip_to_ticker.setdefault(r['cusip'], r['ticker'])

    # Ticker -> snapshot (industry, sector, name, market_cap)
    snap: dict[str, dict] = {}
    for r in P.execute("SELECT ticker, industry, sector, COALESCE(short_name, long_name) AS nm, market_cap FROM ticker_snapshot"):
        snap[r['ticker']] = {
            'industry': r['industry'] or '',
            'sector':   r['sector'] or '',
            'name':     r['nm'] or '',
            'mcap':     r['market_cap'],
        }

    # Aggregate by (quarter, CUSIP) across the funds (common stock only)
    sql = """
    SELECT period_of_report, cusip, MAX(name_of_issuer) AS name_of_issuer,
           SUM(shares) AS total_shares,
           SUM(market_value) AS total_mv,
           COUNT(DISTINCT fund_id) AS n_funds
    FROM holdings
    WHERE period_of_report IN (?, ?)
      AND (put_call IS NULL OR put_call='' OR LOWER(put_call) NOT IN ('put','call'))
      AND cusip IS NOT NULL AND cusip != ''
    GROUP BY period_of_report, cusip
    """
    agg = {(r['period_of_report'], r['cusip']): dict(r) for r in H.execute(sql, (later, earlier))}

    # Fund-count metadata for the meta strip
    n_funds_total = H.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
    n_funds_later = H.execute(
        "SELECT COUNT(DISTINCT fund_id) FROM holdings WHERE period_of_report = ?",
        (later,),
    ).fetchone()[0]
    # Unresolved-ticker percentages for the caveat
    def _unresolved_pct(q: str) -> float:
        total = H.execute(
            "SELECT COUNT(*) FROM holdings WHERE period_of_report = ?",
            (q,),
        ).fetchone()[0]
        if not total:
            return 0.0
        unresolved = H.execute(
            "SELECT COUNT(*) FROM holdings WHERE period_of_report = ? "
            "AND (ticker IS NULL OR ticker = '')",
            (q,),
        ).fetchone()[0]
        return 100.0 * unresolved / total
    pct_unresolved_later = _unresolved_pct(later)
    pct_unresolved_earlier = _unresolved_pct(earlier)

    rows: list[dict] = []
    for c in sorted({k[1] for k in agg}):
        prev_q = agg.get((earlier, c))
        cur_q = agg.get((later, c))
        s_prev = (prev_q['total_shares'] or 0) if prev_q else 0
        s_cur = (cur_q['total_shares'] or 0) if cur_q else 0
        mv_prev = (prev_q['total_mv'] or 0) if prev_q else 0
        mv_cur = (cur_q['total_mv'] or 0) if cur_q else 0
        fn_prev = prev_q['n_funds'] if prev_q else 0
        fn_cur = cur_q['n_funds'] if cur_q else 0
        t = cusip_to_ticker.get(c, '')
        s = snap.get(t, {})
        raw_name = ((cur_q['name_of_issuer'] if cur_q else (prev_q['name_of_issuer'] if prev_q else '')) or '')
        name = s.get('name') or raw_name.title()
        rows.append({
            'ticker':   t or '—',
            'cusip':    c,
            'name':     name,
            'industry': s.get('industry', ''),
            'mcap':     s.get('mcap'),
            's_prev': s_prev, 's_cur': s_cur, 'd_shares': s_cur - s_prev,
            'mv_prev': mv_prev, 'mv_cur': mv_cur, 'd_mv': mv_cur - mv_prev,
            'fn_prev': fn_prev, 'fn_cur': fn_cur, 'd_fn': fn_cur - fn_prev,
            'pct': (100.0 * (s_cur - s_prev) / s_prev) if s_prev else None,
        })

    def is_biotech(r: dict) -> bool:
        indu = (r['industry'] or '').lower()
        if 'biotech' in indu: return True
        if 'drug manufacturers - specialty' in indu: return True
        if 'drug manufacturers - general' in indu: return True
        n = (r['name'] or '').lower()
        kws = ('therapeut','biosci','pharma','biotech','biolog','genomic','oncol','peptide',
               'immun','vaxc','gene therap','medicin','rna','crispr')
        bad = ('etf','spdr','ishares','vanguard','fund ',' lp')
        if any(k in n for k in kws) and not any(x in n for x in bad):
            return True
        return False

    bio = [r for r in rows if is_biotech(r)]

    builds: list[dict] = []
    for r in bio:
        new_pos = r['fn_prev'] == 0 and r['fn_cur'] > 0
        grew = r['d_shares'] > 0 and r['d_fn'] >= 0
        if new_pos or grew:
            if r['fn_cur'] >= 2 or r['mv_cur'] >= 25_000_000:
                builds.append(r)

    def signal(r: dict) -> float:
        fn_score = 200 * max(r['d_fn'], 0)
        new_funds_bonus = 100 * r['fn_cur'] if r['fn_prev'] == 0 else 0
        mv_score = math.log10(max(r['d_mv'], 1) / 1e6 + 1) * 30 if r['d_mv'] > 0 else 0
        return fn_score + new_funds_bonus + mv_score

    builds.sort(key=signal, reverse=True)
    top50 = builds[:50]

    def make_note(r: dict) -> str:
        notes: list[str] = []
        if r['fn_prev'] == 0:
            notes.append(f"{r['fn_cur']}-fund cluster on fresh name")
        elif r['d_fn'] >= 3:
            notes.append(f"+{r['d_fn']} new funds entering")
        elif r['d_fn'] == 2:
            notes.append("2 new funds entering")
        elif r['d_fn'] == 1:
            notes.append("+1 fund entering")
        if r['pct'] is not None and r['pct'] >= 100:
            notes.append(f"more than doubled ({r['pct']:+,.0f}%)")
        elif r['pct'] is not None and r['pct'] >= 50:
            notes.append(f"strong add ({r['pct']:+,.0f}%)")
        elif r['d_shares'] > 0 and r['fn_prev'] > 0 and r['pct'] is not None:
            notes.append(f"{r['pct']:+,.0f}% size")
        return "; ".join(notes)

    def fmt_money(v: float | int | None) -> str:
        if v is None: return ""
        if abs(v) >= 1e9: return f"${v/1e9:,.2f}B"
        if abs(v) >= 1e6: return f"${v/1e6:,.1f}M"
        if abs(v) >= 1e3: return f"${v/1e3:,.0f}K"
        return f"${v:,.0f}"

    def fmt_shares(v: float | int | None) -> str:
        if v is None: return ""
        if abs(v) >= 1e6: return f"{v/1e6:,.2f}M"
        if abs(v) >= 1e3: return f"{v/1e3:,.0f}K"
        return f"{v:,}"

    def fmt_pct(v: float | None) -> str:
        if v is None: return "new"
        return f"{v:+,.0f}%"

    def fmt_mcap(v: float | int | None) -> str:
        if not v: return ""
        if v >= 1e9: return f"${v/1e9:.1f}B"
        if v >= 1e6: return f"${v/1e6:.0f}M"
        return f"${v:,.0f}"

    rows_html: list[str] = []
    for i, r in enumerate(top50, 1):
        ind_short = (r['industry'][:24] + '…') if len(r['industry']) > 25 else r['industry']
        if not ind_short:
            ind_short = "<span class='unknown'>(unresolved)</span>"
        else:
            ind_short = html.escape(ind_short)
        note = html.escape(make_note(r))
        fresh_cls = 'fresh-name' if r['fn_prev'] == 0 else ''
        d_fn = r['d_fn']
        dfn_str = ('+' + str(d_fn)) if d_fn > 0 else str(d_fn)
        d_mv = r['d_mv']
        d_mv_sign = '+' if d_mv > 0 else ('' if d_mv == 0 else '')
        d_mv_cls = 'pos' if d_mv > 0 else ('neg' if d_mv < 0 else '')
        d_shares_str = fmt_shares(r['d_shares']) if r['d_shares'] > 0 else '—'
        rows_html.append(
            f'<tr class="{fresh_cls}">'
            f'<td class="rank">{i}</td>'
            f'<td class="ticker">{html.escape(r["ticker"])}</td>'
            f'<td class="name">{html.escape(r["name"][:55])}</td>'
            f'<td class="industry">{ind_short}</td>'
            f'<td class="num">{fmt_mcap(r["mcap"])}</td>'
            f'<td class="funds">{r["fn_prev"]} &rarr; <b>{r["fn_cur"]}</b> '
            f'<span class="dfn">{dfn_str}</span></td>'
            f'<td class="num">{fmt_money(r["mv_prev"]) if r["mv_prev"] else "—"}</td>'
            f'<td class="num">{fmt_money(r["mv_cur"])}</td>'
            f'<td class="num delta {d_mv_cls}">{d_mv_sign}{fmt_money(d_mv)}</td>'
            f'<td class="num">{d_shares_str}</td>'
            f'<td class="num pct">{fmt_pct(r["pct"])}</td>'
            f'<td class="note">{note}</td>'
            f'</tr>'
        )

    n_fresh = sum(1 for r in top50 if r['fn_prev'] == 0)
    n_add_2plus = sum(1 for r in top50 if r['d_fn'] >= 2)
    total_mv_in = sum(r['d_mv'] for r in top50 if r['d_mv'] > 0)
    total_new_mv = sum(r['mv_cur'] for r in top50 if r['fn_prev'] == 0)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Biotech consensus builds — {later_label} (top 50)</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #fafafa; color: #1a1a1a; margin: 0; padding: 24px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  h2 {{ font-size: 0.95rem; color: #555; font-weight: 500; margin: 0 0 18px; }}
  .meta {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 6px;
           padding: 12px 16px; margin-bottom: 16px; font-size: 0.85rem;
           display: flex; gap: 24px; flex-wrap: wrap; }}
  .meta b {{ color: #0a4d8c; }}
  .caveat {{ background: #fff7e6; border-left: 3px solid #d48806; padding: 10px 14px;
             font-size: 0.82rem; border-radius: 4px; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border: 1px solid #e0e0e0; border-radius: 6px; overflow: hidden;
           font-size: 0.82rem; }}
  th {{ background: #f5f5f5; padding: 8px 10px; text-align: left;
        font-weight: 600; font-size: 0.78rem; color: #444;
        border-bottom: 2px solid #ddd; position: sticky; top: 0; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #eee; vertical-align: middle; }}
  tr:hover {{ background: #f9fbff; }}
  tr.fresh-name {{ background: #f0f9ff; }}
  tr.fresh-name:hover {{ background: #e6f4ff; }}
  .rank {{ color: #999; font-variant-numeric: tabular-nums; width: 28px; }}
  .ticker {{ font-weight: 700; font-family: "SF Mono", Consolas, monospace; color: #0a4d8c; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .delta.pos {{ color: #237804; font-weight: 600; }}
  .delta.neg {{ color: #a8071a; }}
  .funds {{ font-variant-numeric: tabular-nums; }}
  .funds b {{ color: #0a4d8c; }}
  .dfn {{ color: #237804; font-weight: 600; margin-left: 4px; }}
  .pct {{ color: #237804; font-weight: 500; }}
  .note {{ font-size: 0.78rem; color: #555; }}
  .industry {{ font-size: 0.78rem; color: #666; }}
  .unknown {{ color: #aaa; font-style: italic; }}
  .legend {{ font-size: 0.78rem; color: #666; margin-top: 12px; }}
  .legend .swatch {{ display: inline-block; width: 12px; height: 12px;
                     background: #f0f9ff; border: 1px solid #cfe7ff;
                     vertical-align: middle; margin-right: 4px; }}
</style>
</head>
<body>

<h1>Biotech consensus builds — {later_label} vs {earlier_label}</h1>
<h2>Top 50 across {n_funds_total} specialist biotech / healthcare funds (13F-HR holdings, ranked by fund-count momentum + $ added)</h2>

<div class="meta">
  <div>Generated: <b>{datetime.date.today().isoformat()}</b></div>
  <div>Funds tracked: <b>{n_funds_total}</b></div>
  <div>Filings ingested for {later_label}: <b>{n_funds_later} / {n_funds_total}</b></div>
  <div>Biotech universe seen this period: <b>{len(bio)}</b> CUSIPs</div>
  <div>Top-50 fresh-name entries: <b>{n_fresh}</b></div>
  <div>Top-50 with &ge;2 new funds entering: <b>{n_add_2plus}</b></div>
  <div>$ added in top-50 (gross): <b>{fmt_money(total_mv_in)}</b></div>
  <div>$ in fresh-name top-50: <b>{fmt_money(total_new_mv)}</b></div>
</div>

<div class="caveat">
  <b>Data quality note:</b> {later_label} has ~{pct_unresolved_later:.1f}% rows with unresolved tickers
  (vs {pct_unresolved_earlier:.1f}% in {earlier_label}), mostly foreign-domiciled biotechs (CUSIPs starting with G/N/Y)
  and recent IPOs/de-SPACs. Aggregation here is by CUSIP, so position deltas are correct,
  but some tickers display as &mdash; until the OpenFIGI resolver is refreshed.
  Industry comes from <code>data/prices.db.ticker_snapshot</code>; rows showing
  <i>(unresolved)</i> need a Module-4a snapshot refresh to populate industry/market-cap.
</div>

<table>
<thead>
<tr>
  <th>#</th>
  <th>Ticker</th>
  <th>Name</th>
  <th>Industry</th>
  <th class="num">Mkt Cap</th>
  <th>Funds {earlier_label}&rarr;{later_label}</th>
  <th class="num">{earlier_label} $MV</th>
  <th class="num">{later_label} $MV</th>
  <th class="num">&Delta; $MV</th>
  <th class="num">&Delta; shares</th>
  <th class="num">% shares</th>
  <th>Note</th>
</tr>
</thead>
<tbody>
{''.join(rows_html)}
</tbody>
</table>

<div class="legend">
  <span class="swatch"></span> Highlighted rows = fresh names (not held by any tracked fund in {earlier_label}).
  &nbsp;&middot;&nbsp; "Mkt Cap" is from cached ticker snapshots; blank = ticker not yet snapshotted.
  &nbsp;&middot;&nbsp; Ranking signal = 200&times;(&Delta;funds) + 100&times;({later_label} funds, fresh-name bonus) + log10($M added).
</div>

</body>
</html>
"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_doc)
    print(f"Wrote: {output_path}")
    print(f"  comparing {earlier_label} -> {later_label}")
    print(f"  rows: {len(top50)}, fresh-name entries: {n_fresh}, $ added gross: {fmt_money(total_mv_in)}")


if __name__ == '__main__':
    main()
