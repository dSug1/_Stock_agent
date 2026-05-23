"""Render Q1 2026 vs Q4 2025 biotech consensus-build top-50 HTML report.

One-off ad-hoc analysis script. Reads 2_fundparser.db (M2 holdings) + data/prices.db
(M4a ticker snapshots). Writes Outputs/q1_2026_consensus_builds.html.
"""
from __future__ import annotations

import datetime
import html
import math
import sqlite3
import sys


def main() -> None:
    sys.stdout.reconfigure(encoding='utf-8')
    H = sqlite3.connect('2_fundparser.db'); H.row_factory = sqlite3.Row
    P = sqlite3.connect('data/prices.db');   P.row_factory = sqlite3.Row

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

    # Aggregate by (quarter, CUSIP) across the 21 funds (common stock only)
    sql = """
    SELECT period_of_report, cusip, MAX(name_of_issuer) AS name_of_issuer,
           SUM(shares) AS total_shares,
           SUM(market_value) AS total_mv,
           COUNT(DISTINCT fund_id) AS n_funds
    FROM holdings
    WHERE period_of_report IN ('2025-12-31','2026-03-31')
      AND (put_call IS NULL OR put_call='' OR LOWER(put_call) NOT IN ('put','call'))
      AND cusip IS NOT NULL AND cusip != ''
    GROUP BY period_of_report, cusip
    """
    agg = {(r['period_of_report'], r['cusip']): dict(r) for r in H.execute(sql)}

    rows: list[dict] = []
    for c in sorted({k[1] for k in agg}):
        q4 = agg.get(('2025-12-31', c))
        q1 = agg.get(('2026-03-31', c))
        s_q4 = (q4['total_shares'] or 0) if q4 else 0
        s_q1 = (q1['total_shares'] or 0) if q1 else 0
        mv_q4 = (q4['total_mv'] or 0) if q4 else 0
        mv_q1 = (q1['total_mv'] or 0) if q1 else 0
        fn_q4 = q4['n_funds'] if q4 else 0
        fn_q1 = q1['n_funds'] if q1 else 0
        t = cusip_to_ticker.get(c, '')
        s = snap.get(t, {})
        raw_name = ((q1['name_of_issuer'] if q1 else (q4['name_of_issuer'] if q4 else '')) or '')
        name = s.get('name') or raw_name.title()
        rows.append({
            'ticker':   t or '—',
            'cusip':    c,
            'name':     name,
            'industry': s.get('industry', ''),
            'mcap':     s.get('mcap'),
            's_q4': s_q4, 's_q1': s_q1, 'd_shares': s_q1 - s_q4,
            'mv_q4': mv_q4, 'mv_q1': mv_q1, 'd_mv': mv_q1 - mv_q4,
            'fn_q4': fn_q4, 'fn_q1': fn_q1, 'd_fn': fn_q1 - fn_q4,
            'pct': (100.0 * (s_q1 - s_q4) / s_q4) if s_q4 else None,
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
        new_pos = r['fn_q4'] == 0 and r['fn_q1'] > 0
        grew = r['d_shares'] > 0 and r['d_fn'] >= 0
        if new_pos or grew:
            if r['fn_q1'] >= 2 or r['mv_q1'] >= 25_000_000:
                builds.append(r)

    def signal(r: dict) -> float:
        fn_score = 200 * max(r['d_fn'], 0)
        new_funds_bonus = 100 * r['fn_q1'] if r['fn_q4'] == 0 else 0
        mv_score = math.log10(max(r['d_mv'], 1) / 1e6 + 1) * 30 if r['d_mv'] > 0 else 0
        return fn_score + new_funds_bonus + mv_score

    builds.sort(key=signal, reverse=True)
    top50 = builds[:50]

    def make_note(r: dict) -> str:
        notes: list[str] = []
        if r['fn_q4'] == 0:
            notes.append(f"{r['fn_q1']}-fund cluster on fresh name")
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
        elif r['d_shares'] > 0 and r['fn_q4'] > 0 and r['pct'] is not None:
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
        fresh_cls = 'fresh-name' if r['fn_q4'] == 0 else ''
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
            f'<td class="funds">{r["fn_q4"]} &rarr; <b>{r["fn_q1"]}</b> '
            f'<span class="dfn">{dfn_str}</span></td>'
            f'<td class="num">{fmt_money(r["mv_q4"]) if r["mv_q4"] else "—"}</td>'
            f'<td class="num">{fmt_money(r["mv_q1"])}</td>'
            f'<td class="num delta {d_mv_cls}">{d_mv_sign}{fmt_money(d_mv)}</td>'
            f'<td class="num">{d_shares_str}</td>'
            f'<td class="num pct">{fmt_pct(r["pct"])}</td>'
            f'<td class="note">{note}</td>'
            f'</tr>'
        )

    n_fresh = sum(1 for r in top50 if r['fn_q4'] == 0)
    n_add_2plus = sum(1 for r in top50 if r['d_fn'] >= 2)
    total_mv_in = sum(r['d_mv'] for r in top50 if r['d_mv'] > 0)
    total_new_mv = sum(r['mv_q1'] for r in top50 if r['fn_q4'] == 0)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Biotech consensus builds — Q1 2026 (top 50)</title>
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

<h1>Biotech consensus builds — Q1 2026 vs Q4 2025</h1>
<h2>Top 50 across 21 specialist biotech / healthcare funds (13F-HR holdings, ranked by fund-count momentum + $ added)</h2>

<div class="meta">
  <div>Generated: <b>{datetime.date.today().isoformat()}</b></div>
  <div>Funds tracked: <b>21</b></div>
  <div>Filings ingested for 2026Q1: <b>21 / 21</b></div>
  <div>Biotech universe seen this period: <b>{len(bio)}</b> CUSIPs</div>
  <div>Top-50 fresh-name entries: <b>{n_fresh}</b></div>
  <div>Top-50 with &ge;2 new funds entering: <b>{n_add_2plus}</b></div>
  <div>$ added in top-50 (gross): <b>{fmt_money(total_mv_in)}</b></div>
  <div>$ in fresh-name top-50: <b>{fmt_money(total_new_mv)}</b></div>
</div>

<div class="caveat">
  <b>Data quality note:</b> Q1 2026 has ~8.3% rows with unresolved tickers (vs 4.8% in Q4),
  mostly foreign-domiciled biotechs (CUSIPs starting with G/N/Y) and recent IPOs/de-SPACs.
  Aggregation here is by CUSIP, so position deltas are correct, but some tickers display as
  &mdash; until the OpenFIGI resolver is refreshed. Industry comes from
  <code>data/prices.db.ticker_snapshot</code>; rows showing <i>(unresolved)</i> need a Module-4a
  snapshot refresh to populate industry/market-cap.
</div>

<table>
<thead>
<tr>
  <th>#</th>
  <th>Ticker</th>
  <th>Name</th>
  <th>Industry</th>
  <th class="num">Mkt Cap</th>
  <th>Funds Q4&rarr;Q1</th>
  <th class="num">Q4 $MV</th>
  <th class="num">Q1 $MV</th>
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
  <span class="swatch"></span> Highlighted rows = fresh names (not held by any tracked fund in Q4 2025).
  &nbsp;&middot;&nbsp; "Mkt Cap" is from cached ticker snapshots; blank = ticker not yet snapshotted.
  &nbsp;&middot;&nbsp; Ranking signal = 200&times;(&Delta;funds) + 100&times;(Q1 funds, fresh-name bonus) + log10($M added).
</div>

</body>
</html>
"""

    out = 'Outputs/q1_2026_consensus_builds.html'
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html_doc)
    print(f"Wrote: {out}")
    print(f"  rows: {len(top50)}, fresh-name entries: {n_fresh}, $ added gross: {fmt_money(total_mv_in)}")


if __name__ == '__main__':
    main()
