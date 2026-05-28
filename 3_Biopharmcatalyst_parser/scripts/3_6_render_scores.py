"""Render a self-contained HTML report of Module 6's catalyst_scores.

Reads ``catalyst_scores`` JOIN ``catalyst_snapshots`` + ``catalyst_timing``
for one snapshot (default: most recent) and writes
``Outputs/catalyst_scores.html``. Fully self-contained — embedded CSS,
vanilla JS, JSON data; no external dependencies.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_render_scores.py
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402


OUTPUT_PATH = PROJECT_ROOT / "Outputs" / "catalyst_scores.html"
INSIDER_WINDOW_DAYS = 365  # matches Module 6's lookback_days default


def _fetch_score_rows(conn: sqlite3.Connection, snap: date) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            cs.ticker, cs.drug, cs.nct_number, cs.next_catalyst_type,
            cs.hard_pass, cs.fail_reasons, cs.timing_bucket,
            cs.insider_gross_weighted_usd, cs.insider_score,
            cs.return_30d_pct, cs.momentum_score,
            cs.fund_quarter_latest, cs.fund_quarter_previous,
            cs.funds_holding_latest, cs.funds_holding_previous,
            cs.fund_accumulation_usd, cs.fund_accumulation_score,
            cs.composite_score, cs.rules_version,
            t.date_min, t.date_max, t.precision_tier, t.source_lane,
            t.matched_phrase,
            s.name, s.indication, s.stage, s.market_cap_usd,
            s.price, s.catalyst_date, s.catalyst_text, s.conference
        FROM catalyst_scores cs
        LEFT JOIN catalyst_timing t USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        LEFT JOIN catalyst_snapshots s USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        WHERE cs.snapshot_date = ?
        ORDER BY
            cs.hard_pass DESC,
            cs.composite_score DESC NULLS LAST,
            cs.insider_score DESC NULLS LAST
        """,
        (snap.isoformat(),),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_insider_trades(
    conn: sqlite3.Connection, snap: date, tickers: list[str],
) -> dict[str, list[dict]]:
    """Up to 10 most-recent qualifying insider buys per ticker (CEO/CFO
    only, since those are the only roles M6 actually scores)."""
    if not tickers:
        return {}
    floor_iso = (snap - timedelta(days=INSIDER_WINDOW_DAYS)).isoformat()
    qmarks = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT ticker, source, insider_name, insider_position,
               executive_role, filing_date, transaction_date,
               shares, trade_price, gross_usd
        FROM v_executive_open_market_trades
        WHERE ticker IN ({qmarks})
          AND buy_sell = 'Buy'
          AND executive_role IN ('CEO','CFO')
          AND filing_date IS NOT NULL
          AND filing_date >= ?
          AND filing_date <= ?
        ORDER BY ticker, filing_date DESC
        """,
        list(tickers) + [floor_iso, snap.isoformat()],
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d["ticker"], []).append(d)
    for t in out:
        out[t] = out[t][:10]
    return out


def _fetch_funds_breakdown(
    conn: sqlite3.Connection, tickers: list[str], q_latest: str | None, q_prev: str | None,
) -> dict[str, list[dict]]:
    """Per-ticker per-fund position deltas, for the expand panel."""
    if not tickers or not q_latest or not q_prev:
        return {}
    # ATTACH funds DB (read-only path coupling) — same pattern as ingest.py
    from module_6.config import default_config_path, load_scoring_config
    from module_6.funds_reader import resolve_funds_db_path
    cfg = load_scoring_config(default_config_path())
    fpath = resolve_funds_db_path(PROJECT_ROOT, cfg.funds.db_path_relative_to_repo_root)
    if not fpath.exists():
        return {}
    try:
        conn.execute("ATTACH DATABASE ? AS funds", (str(fpath),))
    except sqlite3.Error:
        return {}
    qmarks = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        WITH ticker_funds AS (
          SELECT DISTINCT ticker, fund_id FROM funds.holdings
          WHERE period_of_report IN (?, ?) AND ticker IS NOT NULL
            AND ticker IN ({qmarks})
        )
        SELECT tf.ticker,
               tf.fund_id,
               f.name AS fund_name,
               COALESCE(l.shares, 0)        AS shares_latest,
               COALESCE(p.shares, 0)        AS shares_previous,
               COALESCE(l.market_value, 0)  AS mv_latest,
               COALESCE(p.market_value, 0)  AS mv_previous
        FROM ticker_funds tf
        LEFT JOIN funds.holdings l
          ON l.ticker = tf.ticker AND l.fund_id = tf.fund_id AND l.period_of_report = ?
        LEFT JOIN funds.holdings p
          ON p.ticker = tf.ticker AND p.fund_id = tf.fund_id AND p.period_of_report = ?
        LEFT JOIN funds.funds f ON f.id = tf.fund_id
        ORDER BY tf.ticker, (COALESCE(l.shares,0) - COALESCE(p.shares,0)) DESC
        """,
        [q_latest, q_prev] + list(tickers) + [q_latest, q_prev],
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d["ticker"], []).append(d)
    try:
        conn.execute("DETACH DATABASE funds")
    except sqlite3.Error:
        pass
    return out


def _aggregate_kpis(rows: list[dict]) -> dict:
    total = len(rows)
    hard_pass = sum(1 for r in rows if r["hard_pass"])
    defined = sum(
        1 for r in rows
        if r["hard_pass"] and r["timing_bucket"] == "catalyst_date_defined"
    )
    undefined = sum(
        1 for r in rows
        if r["hard_pass"] and r["timing_bucket"] == "catalyst_date_undefined"
    )
    fail_counts: dict[str, int] = {}
    for r in rows:
        if r["fail_reasons"]:
            for code in r["fail_reasons"].split(","):
                fail_counts[code] = fail_counts.get(code, 0) + 1

    composites = [r["composite_score"] for r in rows if r["composite_score"] is not None]
    score_dist = {"80-100": 0, "60-80": 0, "40-60": 0, "20-40": 0, "0-20": 0}
    for c in composites:
        if c >= 80: score_dist["80-100"] += 1
        elif c >= 60: score_dist["60-80"] += 1
        elif c >= 40: score_dist["40-60"] += 1
        elif c >= 20: score_dist["20-40"] += 1
        else: score_dist["0-20"] += 1

    n_with_insider = sum(
        1 for r in rows
        if r["hard_pass"] and (r.get("insider_gross_weighted_usd") or 0) > 0
    )
    n_with_funds = sum(
        1 for r in rows
        if r["hard_pass"] and (r.get("fund_accumulation_usd") or 0) > 0
    )

    funds_q_latest = None
    funds_q_prev = None
    for r in rows:
        if r["fund_quarter_latest"]:
            funds_q_latest = r["fund_quarter_latest"]
            funds_q_prev = r["fund_quarter_previous"]
            break

    rules_version = next((r["rules_version"] for r in rows if r.get("rules_version")), "")

    return {
        "total": total,
        "hard_pass": hard_pass,
        "excluded": total - hard_pass,
        "defined": defined,
        "undefined": undefined,
        "fail_counts": fail_counts,
        "score_dist": score_dist,
        "n_with_insider": n_with_insider,
        "n_with_funds": n_with_funds,
        "funds_q_latest": funds_q_latest,
        "funds_q_prev": funds_q_prev,
        "rules_version": rules_version,
    }


CSS = """
:root {
  --bg: #0b1020;
  --bg-elev: #131a30;
  --bg-row: #182040;
  --border: #2a3458;
  --text: #e6e8f2;
  --text-dim: #98a0c0;
  --accent: #7c9cff;
  --green: #34d399;
  --amber: #fbbf24;
  --red: #f87171;
  --slate: #94a3b8;
  --indigo: #818cf8;
  --purple: #c084fc;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 18px 20px 60px;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 13.5px;
  line-height: 1.4;
}
h1 { font-size: 18px; margin: 0 0 4px; }
.meta { color: var(--text-dim); font-size: 12px; margin-bottom: 16px; }
.meta code {
  background: var(--bg-elev); padding: 2px 6px; border-radius: 4px;
  color: var(--accent); font-size: 11.5px;
}

/* KPI strip */
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.kpi {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}
.kpi .label { color: var(--text-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
.kpi .value { font-size: 22px; font-weight: 600; margin-top: 4px; }
.kpi.green .value { color: var(--green); }
.kpi.amber .value { color: var(--amber); }
.kpi.slate .value { color: var(--slate); }
.kpi.indigo .value { color: var(--indigo); }
.kpi.purple .value { color: var(--purple); }
.kpi .sub { color: var(--text-dim); font-size: 11px; margin-top: 2px; }

/* Score distribution bar */
.score-dist {
  display: flex;
  gap: 2px;
  margin-bottom: 16px;
  height: 32px;
  border-radius: 6px;
  overflow: hidden;
  background: var(--bg-elev);
  border: 1px solid var(--border);
}
.score-dist .seg {
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; color: #fff; font-weight: 500;
}
.score-dist .seg.s80 { background: #15803d; }
.score-dist .seg.s60 { background: #65a30d; }
.score-dist .seg.s40 { background: #ca8a04; }
.score-dist .seg.s20 { background: #c2410c; }
.score-dist .seg.s00 { background: #7f1d1d; }

/* Tabs */
.tabs {
  display: flex; gap: 4px; margin-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.tab {
  background: transparent; color: var(--text-dim);
  border: 1px solid transparent; border-bottom: none;
  padding: 8px 14px; border-radius: 6px 6px 0 0;
  cursor: pointer; font-size: 13px;
}
.tab:hover { color: var(--text); }
.tab.active {
  background: var(--bg-elev); color: var(--text);
  border-color: var(--border);
  position: relative; top: 1px;
}
.tab .count {
  background: var(--bg-row); color: var(--text-dim);
  margin-left: 6px; padding: 1px 7px; border-radius: 999px;
  font-size: 11px;
}

/* Filter bar */
.filters {
  display: flex; gap: 12px; flex-wrap: wrap;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
  align-items: center;
}
.filters label { color: var(--text-dim); font-size: 12px; }
.filters input[type=number],
.filters select,
.filters input[type=text] {
  background: var(--bg); color: var(--text);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 4px 8px; font-size: 12.5px;
  min-width: 80px;
}
.filters input[type=text] { min-width: 140px; }
.filters .reset {
  background: var(--bg-row); color: var(--text-dim);
  border: 1px solid var(--border);
  border-radius: 5px; padding: 4px 10px;
  cursor: pointer; font-size: 12px;
}
.filters .reset:hover { color: var(--text); }
.filters .visible-count { color: var(--text-dim); font-size: 12px; margin-left: auto; }

/* Table */
table { width: 100%; border-collapse: collapse; }
thead th {
  text-align: left; font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.04em; color: var(--text-dim);
  border-bottom: 1px solid var(--border);
  padding: 8px 8px; position: sticky; top: 0;
  background: var(--bg); z-index: 1;
  cursor: pointer; user-select: none;
}
thead th:hover { color: var(--text); }
thead th.sorted-asc::after  { content: " ▲"; color: var(--accent); }
thead th.sorted-desc::after { content: " ▼"; color: var(--accent); }
tbody tr {
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
tbody tr:hover { background: var(--bg-row); }
tbody tr.expanded { background: var(--bg-row); }
tbody td {
  padding: 7px 8px; vertical-align: top;
}
tbody td .ticker {
  font-weight: 600; color: var(--accent);
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
}
tbody td .num { font-variant-numeric: tabular-nums; }
tbody td .score {
  display: inline-block; min-width: 42px; text-align: right;
  padding: 2px 7px; border-radius: 4px;
  font-variant-numeric: tabular-nums; font-size: 12px;
}
.score.s80 { background: rgba(21, 128, 61, 0.25); color: #4ade80; }
.score.s60 { background: rgba(101, 163, 13, 0.22); color: #a3e635; }
.score.s40 { background: rgba(202, 138, 4, 0.22); color: #facc15; }
.score.s20 { background: rgba(194, 65, 12, 0.22); color: #fb923c; }
.score.s00 { background: rgba(127, 29, 29, 0.22); color: var(--red); }
.score.zero { color: var(--text-dim); background: transparent; }

.tag {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  font-size: 11px; line-height: 1.5;
}
.tag.bucket-defined { background: rgba(52, 211, 153, 0.16); color: var(--green); }
.tag.bucket-undefined { background: rgba(251, 191, 36, 0.16); color: var(--amber); }
.tag.fail { background: rgba(248, 113, 113, 0.16); color: var(--red); font-family: ui-monospace, monospace; }
.tag.stage { background: rgba(124, 156, 255, 0.16); color: var(--accent); }
.tag.tier { background: rgba(148, 163, 184, 0.16); color: var(--slate); font-size: 10.5px; }

.expand-panel {
  background: var(--bg-elev);
  padding: 12px 14px;
  border-top: 1px solid var(--border);
}
.expand-panel h4 {
  margin: 10px 0 6px; font-size: 12px; color: var(--text-dim);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.expand-panel h4:first-child { margin-top: 0; }
.expand-panel .kv { display: grid; grid-template-columns: 180px 1fr; gap: 4px 12px; font-size: 12.5px; }
.expand-panel .kv .k { color: var(--text-dim); }
.expand-panel table.detail {
  width: 100%; margin-top: 4px; font-size: 12px;
}
.expand-panel table.detail th {
  text-align: left; padding: 4px 8px; color: var(--text-dim);
  font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.04em;
  border-bottom: 1px solid var(--border);
}
.expand-panel table.detail td {
  padding: 4px 8px; border-bottom: 1px solid rgba(42,52,88,0.5);
}
.expand-panel table.detail tr:last-child td { border-bottom: none; }
.delta-pos { color: var(--green); }
.delta-neg { color: var(--red); }
.delta-zero { color: var(--text-dim); }
.expand-panel .catalyst-text {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 8px 10px;
  font-size: 12.5px;
  color: var(--text-dim);
  white-space: pre-wrap;
  word-break: break-word;
}
.expand-panel .catalyst-text mark {
  background: rgba(124, 156, 255, 0.3);
  color: var(--text);
  padding: 0 2px;
  border-radius: 2px;
}

.no-rows { color: var(--text-dim); padding: 24px; text-align: center; font-style: italic; }
"""


JS = r"""
(function() {
  const STORAGE_KEY = 'catalyst_scores_filters_v1';

  function loadFilters() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      return JSON.parse(raw);
    } catch { return {}; }
  }
  function saveFilters(f) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(f)); } catch {}
  }

  const state = Object.assign({
    tab: 'catalyst_date_defined',
    minComposite: 0,
    requireInsider: 'any',
    requireFunds: 'any',
    stage: 'any',
    tickerFilter: '',
    sortCol: 'composite_score',
    sortDir: 'desc',
  }, loadFilters());

  // Cast numerics that may have been stringified by JSON->localStorage cycle
  state.minComposite = Number(state.minComposite) || 0;

  function scoreClass(s) {
    if (s == null) return 'zero';
    if (s >= 80) return 's80';
    if (s >= 60) return 's60';
    if (s >= 40) return 's40';
    if (s >= 20) return 's20';
    return 's00';
  }
  function fmtScore(s) {
    if (s == null) return '<span class="score zero">—</span>';
    const cls = scoreClass(s);
    return `<span class="score ${cls}">${s.toFixed(1)}</span>`;
  }
  function fmtMcap(v) {
    if (v == null) return '—';
    if (v >= 1e9) return (v/1e9).toFixed(2) + 'B';
    if (v >= 1e6) return (v/1e6).toFixed(0) + 'M';
    return v.toLocaleString();
  }
  function fmtUsd(v) {
    if (v == null || v === 0) return '—';
    if (Math.abs(v) >= 1e6) return '$' + (v/1e6).toFixed(2) + 'M';
    if (Math.abs(v) >= 1e3) return '$' + (v/1e3).toFixed(1) + 'k';
    return '$' + Math.round(v).toLocaleString();
  }
  function fmtPct(v) {
    if (v == null) return '—';
    const sign = v > 0 ? '+' : '';
    const cls = v > 0 ? 'delta-pos' : (v < 0 ? 'delta-neg' : 'delta-zero');
    return `<span class="${cls}">${sign}${v.toFixed(1)}%</span>`;
  }
  function fmtShares(v) {
    if (v == null || v === 0) return '0';
    return Math.round(v).toLocaleString();
  }
  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function highlightPhrase(text, phrase) {
    if (!text) return '';
    if (!phrase) return escapeHtml(text);
    const escaped = escapeHtml(text);
    const safePhrase = escapeHtml(phrase);
    // simple case-insensitive highlight
    const re = new RegExp(safePhrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    return escaped.replace(re, (m) => `<mark>${m}</mark>`);
  }

  // ============ rendering ============
  function rowMatchesFilters(r) {
    if (state.tab === 'excluded') {
      if (r.hard_pass) return false;
    } else {
      if (!r.hard_pass) return false;
      if (r.timing_bucket !== state.tab) return false;
    }
    if (r.composite_score != null && r.composite_score < state.minComposite) return false;
    if (state.requireInsider === 'yes') {
      if (!r.insider_gross_weighted_usd || r.insider_gross_weighted_usd <= 0) return false;
    } else if (state.requireInsider === 'no') {
      if (r.insider_gross_weighted_usd && r.insider_gross_weighted_usd > 0) return false;
    }
    if (state.requireFunds === 'yes') {
      if (!r.fund_accumulation_usd || r.fund_accumulation_usd <= 0) return false;
    } else if (state.requireFunds === 'no') {
      if (r.fund_accumulation_usd && r.fund_accumulation_usd > 0) return false;
    }
    if (state.stage !== 'any') {
      if (r.stage !== state.stage) return false;
    }
    if (state.tickerFilter && state.tickerFilter.length) {
      const t = state.tickerFilter.toLowerCase();
      if (!String(r.ticker || '').toLowerCase().includes(t) &&
          !String(r.name || '').toLowerCase().includes(t) &&
          !String(r.drug || '').toLowerCase().includes(t)) return false;
    }
    return true;
  }

  function sortRows(rows) {
    const col = state.sortCol;
    const dir = state.sortDir === 'asc' ? 1 : -1;
    return rows.slice().sort((a, b) => {
      let av = a[col], bv = b[col];
      // string vs number tolerant comparison
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string' && typeof bv === 'string') {
        return av.localeCompare(bv) * dir;
      }
      return (av - bv) * dir;
    });
  }

  function renderTable() {
    const tab = state.tab;
    const filtered = window.__DATA.rows.filter(rowMatchesFilters);
    const sorted = sortRows(filtered);
    document.getElementById('visible-count').textContent =
      `${filtered.length} of ${window.__DATA.rows.length} rows`;

    const tbody = document.getElementById('rows-body');
    if (!sorted.length) {
      tbody.innerHTML = '<tr><td colspan="13" class="no-rows">No rows match the current filters.</td></tr>';
      return;
    }

    const isExcluded = tab === 'excluded';

    tbody.innerHTML = sorted.map((r, i) => {
      const tagBucket = isExcluded
        ? (r.fail_reasons || '').split(',').filter(Boolean).map(c => `<span class="tag fail">${c}</span>`).join(' ')
        : `<span class="tag bucket-${r.timing_bucket === 'catalyst_date_defined' ? 'defined' : 'undefined'}">${(r.precision_tier || '').slice(0,8)}</span>`;
      return `
      <tr data-idx="${r.__idx}">
        <td class="num">${i+1}</td>
        <td><span class="ticker">${escapeHtml(r.ticker)}</span></td>
        <td>${escapeHtml(r.name || '')}</td>
        <td>${escapeHtml(r.drug || '')}</td>
        <td><span class="tag stage">${escapeHtml(r.stage || '')}</span></td>
        <td>${escapeHtml(r.next_catalyst_type || '')}</td>
        <td class="num">${escapeHtml(r.date_min || '—')}</td>
        <td>${tagBucket}</td>
        <td class="num">${fmtMcap(r.market_cap_usd)}</td>
        <td>${fmtScore(r.composite_score)}</td>
        <td>${fmtScore(r.insider_score)}</td>
        <td>${fmtScore(r.momentum_score)}</td>
        <td>${fmtScore(r.fund_accumulation_score)}</td>
      </tr>`;
    }).join('');
  }

  function renderTabs() {
    document.querySelectorAll('.tab').forEach(el => {
      el.classList.toggle('active', el.dataset.tab === state.tab);
    });
  }

  function renderSortIndicators() {
    document.querySelectorAll('thead th').forEach(th => {
      th.classList.remove('sorted-asc', 'sorted-desc');
      if (th.dataset.col === state.sortCol) {
        th.classList.add(state.sortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
      }
    });
  }

  // ============ expand panel ============
  function buildExpandPanel(r) {
    const trades = (window.__DATA.insider_trades[r.ticker] || []);
    const fundBreakdown = (window.__DATA.funds_breakdown[r.ticker] || []);

    let html = '<div class="expand-panel">';

    // Catalyst text panel
    if (r.catalyst_text) {
      html += '<h4>Catalyst text (BPC)</h4>';
      html += `<div class="catalyst-text">${highlightPhrase(r.catalyst_text, r.matched_phrase)}</div>`;
    }

    // Signal summary KV
    html += '<h4>Signal breakdown</h4>';
    html += '<div class="kv">';
    html += `<div class="k">Composite score</div><div>${r.composite_score != null ? r.composite_score.toFixed(2) : '—'}</div>`;
    html += `<div class="k">Insider score (35%)</div><div>${r.insider_score != null ? r.insider_score.toFixed(2) : '—'} — gross ${fmtUsd(r.insider_gross_weighted_usd)} (CEO×2 + CFO×1, 365d)</div>`;
    html += `<div class="k">Momentum score (35%)</div><div>${r.momentum_score != null ? r.momentum_score.toFixed(2) : '—'} — 30d return ${fmtPct(r.return_30d_pct)}</div>`;
    html += `<div class="k">Fund accumulation score (30%)</div><div>${r.fund_accumulation_score != null ? r.fund_accumulation_score.toFixed(2) : '—'} — net positive ${fmtUsd(r.fund_accumulation_usd)} across ${r.funds_holding_latest ?? 0} funds (was ${r.funds_holding_previous ?? 0} prev qtr)</div>`;
    if (r.fund_quarter_latest) {
      html += `<div class="k">Funds quarters</div><div>${escapeHtml(r.fund_quarter_latest)} vs ${escapeHtml(r.fund_quarter_previous || '?')}</div>`;
    }
    if (r.fail_reasons) {
      html += `<div class="k">Fail reasons</div><div>${r.fail_reasons}</div>`;
    }
    html += `<div class="k">Stage / next type</div><div>${escapeHtml(r.stage || '?')} → ${escapeHtml(r.next_catalyst_type || '?')}</div>`;
    html += `<div class="k">Indication</div><div>${escapeHtml(r.indication || '—')}</div>`;
    html += `<div class="k">Date window</div><div>${escapeHtml(r.date_min || '?')} → ${escapeHtml(r.date_max || '?')} (${escapeHtml(r.precision_tier || '?')}, lane=${escapeHtml(r.source_lane || '?')})</div>`;
    html += `<div class="k">Market cap / price</div><div>${fmtMcap(r.market_cap_usd)} / $${r.price != null ? r.price.toFixed(2) : '—'}</div>`;
    html += `<div class="k">NCT</div><div>${escapeHtml(r.nct_number || '—')}</div>`;
    html += '</div>';

    // Insider trades table
    if (trades.length) {
      html += '<h4>Qualifying insider buys (CEO/CFO, last 365d)</h4>';
      html += '<table class="detail"><thead><tr>'
           + '<th>Date</th><th>Role</th><th>Insider</th><th>Shares</th>'
           + '<th>Price</th><th>Gross</th><th>Source</th></tr></thead><tbody>';
      trades.forEach(t => {
        html += `<tr>
          <td class="num">${escapeHtml(t.filing_date || '')}</td>
          <td>${escapeHtml(t.executive_role)}</td>
          <td>${escapeHtml(t.insider_name || '')}</td>
          <td class="num">${fmtShares(t.shares)}</td>
          <td class="num">$${t.trade_price != null ? t.trade_price.toFixed(2) : '—'}</td>
          <td class="num">${fmtUsd(t.gross_usd)}</td>
          <td>${escapeHtml(t.source)}</td>
        </tr>`;
      });
      html += '</tbody></table>';
    } else if (r.hard_pass) {
      html += '<h4>Qualifying insider buys (CEO/CFO, last 365d)</h4><div style="color:var(--text-dim);font-size:12px">None.</div>';
    }

    // Funds breakdown table
    if (fundBreakdown.length) {
      html += '<h4>Per-fund position change</h4>';
      html += '<table class="detail"><thead><tr>'
           + '<th>Fund</th><th>Prev shares</th><th>Now shares</th>'
           + '<th>Δ shares</th><th>Now MV</th></tr></thead><tbody>';
      fundBreakdown.forEach(f => {
        const delta = (f.shares_latest || 0) - (f.shares_previous || 0);
        const cls = delta > 0 ? 'delta-pos' : (delta < 0 ? 'delta-neg' : 'delta-zero');
        const sign = delta > 0 ? '+' : '';
        html += `<tr>
          <td>${escapeHtml(f.fund_name || ('Fund ' + f.fund_id))}</td>
          <td class="num">${fmtShares(f.shares_previous)}</td>
          <td class="num">${fmtShares(f.shares_latest)}</td>
          <td class="num ${cls}">${sign}${fmtShares(delta)}</td>
          <td class="num">${fmtUsd(f.mv_latest)}</td>
        </tr>`;
      });
      html += '</tbody></table>';
    }

    html += '</div>';
    return html;
  }

  function toggleRow(tr) {
    const idx = Number(tr.dataset.idx);
    const r = window.__DATA.rows[idx];
    const next = tr.nextElementSibling;
    if (next && next.classList.contains('expand-row')) {
      next.remove();
      tr.classList.remove('expanded');
      return;
    }
    // collapse any other open panel
    document.querySelectorAll('tr.expand-row').forEach(el => el.remove());
    document.querySelectorAll('tr.expanded').forEach(el => el.classList.remove('expanded'));

    const tr2 = document.createElement('tr');
    tr2.className = 'expand-row';
    tr2.innerHTML = `<td colspan="13">${buildExpandPanel(r)}</td>`;
    tr.classList.add('expanded');
    tr.parentNode.insertBefore(tr2, tr.nextSibling);
  }

  // ============ event wiring ============
  function bind() {
    document.querySelectorAll('.tab').forEach(el => {
      el.addEventListener('click', () => {
        state.tab = el.dataset.tab;
        saveFilters(state);
        renderTabs();
        renderTable();
      });
    });

    document.getElementById('min-composite').addEventListener('input', e => {
      state.minComposite = Number(e.target.value) || 0;
      saveFilters(state); renderTable();
    });
    document.getElementById('require-insider').addEventListener('change', e => {
      state.requireInsider = e.target.value;
      saveFilters(state); renderTable();
    });
    document.getElementById('require-funds').addEventListener('change', e => {
      state.requireFunds = e.target.value;
      saveFilters(state); renderTable();
    });
    document.getElementById('stage-filter').addEventListener('change', e => {
      state.stage = e.target.value;
      saveFilters(state); renderTable();
    });
    document.getElementById('ticker-filter').addEventListener('input', e => {
      state.tickerFilter = e.target.value;
      saveFilters(state); renderTable();
    });
    document.getElementById('reset-filters').addEventListener('click', () => {
      state.minComposite = 0;
      state.requireInsider = 'any';
      state.requireFunds = 'any';
      state.stage = 'any';
      state.tickerFilter = '';
      saveFilters(state);
      restoreFormFromState();
      renderTable();
    });

    document.querySelectorAll('thead th').forEach(th => {
      if (!th.dataset.col) return;
      th.addEventListener('click', () => {
        if (state.sortCol === th.dataset.col) {
          state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          state.sortCol = th.dataset.col;
          state.sortDir = 'desc';
        }
        saveFilters(state); renderSortIndicators(); renderTable();
      });
    });

    document.getElementById('rows-body').addEventListener('click', e => {
      const tr = e.target.closest('tr[data-idx]');
      if (tr) toggleRow(tr);
    });
  }

  function restoreFormFromState() {
    document.getElementById('min-composite').value = state.minComposite || 0;
    document.getElementById('require-insider').value = state.requireInsider;
    document.getElementById('require-funds').value = state.requireFunds;
    document.getElementById('stage-filter').value = state.stage;
    document.getElementById('ticker-filter').value = state.tickerFilter || '';
  }

  function init() {
    // Annotate each row with its absolute index for expand lookup
    window.__DATA.rows.forEach((r, i) => { r.__idx = i; });

    bind();
    restoreFormFromState();
    renderTabs();
    renderSortIndicators();
    renderTable();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


def _html_template(*, snap: date, rows: list[dict], kpis: dict,
                   insider_trades: dict, funds_breakdown: dict) -> str:
    stages = sorted({r["stage"] for r in rows if r["stage"]})

    data_payload = {
        "snapshot_date": snap.isoformat(),
        "rules_version": kpis["rules_version"],
        "rows": rows,
        "insider_trades": insider_trades,
        "funds_breakdown": funds_breakdown,
    }
    payload_json = json.dumps(data_payload, default=str)

    def kpi(label, value, sub="", klass=""):
        return (
            f'<div class="kpi {klass}">'
            f'<div class="label">{html.escape(label)}</div>'
            f'<div class="value">{html.escape(str(value))}</div>'
            f'<div class="sub">{html.escape(sub)}</div>'
            f'</div>'
        )

    # Score distribution bar segments
    dist = kpis["score_dist"]
    dist_total = sum(dist.values()) or 1
    segs = []
    for key, klass in [("80-100", "s80"), ("60-80", "s60"), ("40-60", "s40"),
                       ("20-40", "s20"), ("0-20", "s00")]:
        n = dist[key]
        if n == 0: continue
        pct = 100 * n / dist_total
        segs.append(
            f'<div class="seg {klass}" style="flex:{pct} 0 0" '
            f'title="composite {key}: {n}">{n}</div>'
        )
    dist_bar = "".join(segs) if segs else '<div class="seg" style="flex:1">no hard_pass rows</div>'

    fail_str = ", ".join(
        f"{code}={n}" for code, n in sorted(kpis["fail_counts"].items())
    ) or "—"

    funds_meta = (
        f"funds quarters: {kpis['funds_q_latest']} vs {kpis['funds_q_prev']}"
        if kpis["funds_q_latest"] else "funds DB not attached"
    )

    stage_opts = "".join(
        f'<option value="{html.escape(s)}">{html.escape(s)}</option>'
        for s in stages
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Catalyst scores — {html.escape(snap.isoformat())}</title>
<style>{CSS}</style>
</head>
<body>
<h1>Catalyst scores — Module 6 output</h1>
<div class="meta">
  Snapshot <code>{html.escape(snap.isoformat())}</code> ·
  rules <code>{html.escape(kpis['rules_version'])}</code> ·
  {html.escape(funds_meta)} ·
  generated {datetime.utcnow().isoformat(timespec='seconds')}Z
</div>

<div class="kpi-strip">
  {kpi("Catalysts", kpis["total"], "snapshot total")}
  {kpi("Hard pass", kpis["hard_pass"], f"{kpis['excluded']} excluded", "green")}
  {kpi("Defined timing", kpis["defined"], "specific/conference/month/quarter", "indigo")}
  {kpi("Undefined timing", kpis["undefined"], "half/year", "amber")}
  {kpi("With CEO/CFO buy", kpis["n_with_insider"], "of hard-pass rows", "purple")}
  {kpi("With fund accum", kpis["n_with_funds"], "of hard-pass rows", "slate")}
</div>

<div class="score-dist" title="composite_score distribution across hard_pass=1 rows">
  {dist_bar}
</div>

<div class="meta" style="margin-top:-8px;margin-bottom:14px">
  Failures by rule (overlapping across rows): {html.escape(fail_str)}
</div>

<div class="tabs">
  <button class="tab" data-tab="catalyst_date_defined">
    Defined timing <span class="count">{kpis["defined"]}</span>
  </button>
  <button class="tab" data-tab="catalyst_date_undefined">
    Undefined timing <span class="count">{kpis["undefined"]}</span>
  </button>
  <button class="tab" data-tab="excluded">
    Excluded <span class="count">{kpis["excluded"]}</span>
  </button>
</div>

<div class="filters">
  <label>min composite <input type="number" id="min-composite" min="0" max="100" step="5" value="0"></label>
  <label>insider buy
    <select id="require-insider">
      <option value="any">any</option>
      <option value="yes">required</option>
      <option value="no">none</option>
    </select>
  </label>
  <label>fund accum
    <select id="require-funds">
      <option value="any">any</option>
      <option value="yes">required</option>
      <option value="no">none</option>
    </select>
  </label>
  <label>stage
    <select id="stage-filter">
      <option value="any">any</option>
      {stage_opts}
    </select>
  </label>
  <label>search <input type="text" id="ticker-filter" placeholder="ticker, name, or drug"></label>
  <button class="reset" id="reset-filters">reset</button>
  <span class="visible-count" id="visible-count">…</span>
</div>

<table>
  <thead>
    <tr>
      <th>#</th>
      <th data-col="ticker">Ticker</th>
      <th data-col="name">Name</th>
      <th data-col="drug">Drug</th>
      <th data-col="stage">Stage</th>
      <th data-col="next_catalyst_type">Catalyst type</th>
      <th data-col="date_min">Date</th>
      <th data-col="precision_tier">Precision / fail</th>
      <th data-col="market_cap_usd">Market cap</th>
      <th data-col="composite_score">Composite</th>
      <th data-col="insider_score">Insider</th>
      <th data-col="momentum_score">Momentum</th>
      <th data-col="fund_accumulation_score">Funds</th>
    </tr>
  </thead>
  <tbody id="rows-body"></tbody>
</table>

<script>window.__DATA = {payload_json};</script>
<script>{JS}</script>
</body>
</html>
"""


def render(snapshot_date: date | None = None, out_path: Path = OUTPUT_PATH) -> Path:
    conn = get_connection()
    try:
        if snapshot_date is None:
            row = conn.execute(
                "SELECT MAX(snapshot_date) FROM catalyst_scores"
            ).fetchone()
            if row is None or row[0] is None:
                raise RuntimeError("catalyst_scores is empty — run Module 6 first")
            snapshot_date = date.fromisoformat(row[0])

        rows = _fetch_score_rows(conn, snapshot_date)
        if not rows:
            raise RuntimeError(
                f"no catalyst_scores rows for snapshot {snapshot_date.isoformat()}"
            )

        # Insider buys (CEO/CFO) for hard_pass tickers (saves payload size)
        hard_pass_tickers = sorted({r["ticker"] for r in rows if r["hard_pass"]})
        insider_trades = _fetch_insider_trades(conn, snapshot_date, hard_pass_tickers)

        # Funds breakdown — only for hard_pass tickers with positive accumulation
        funds_tickers = sorted({
            r["ticker"] for r in rows
            if r["hard_pass"] and (r.get("fund_accumulation_usd") or 0) > 0
        })
        q_latest = next((r["fund_quarter_latest"] for r in rows
                         if r.get("fund_quarter_latest")), None)
        q_prev = next((r["fund_quarter_previous"] for r in rows
                       if r.get("fund_quarter_previous")), None)
        funds_breakdown = _fetch_funds_breakdown(conn, funds_tickers, q_latest, q_prev)

        kpis = _aggregate_kpis(rows)
        page = _html_template(
            snap=snapshot_date, rows=rows, kpis=kpis,
            insider_trades=insider_trades, funds_breakdown=funds_breakdown,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(page, encoding="utf-8")
        return out_path
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-date", type=date.fromisoformat, default=None,
        help="ISO snapshot date to render (default: most recent in catalyst_scores)",
    )
    parser.add_argument(
        "--out", type=Path, default=OUTPUT_PATH,
        help=f"output HTML path (default: {OUTPUT_PATH.relative_to(PROJECT_ROOT)})",
    )
    args = parser.parse_args()

    out = render(snapshot_date=args.snapshot_date, out_path=args.out)
    size_kb = out.stat().st_size / 1024
    print(f"[3_6_render_scores] wrote {out} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
