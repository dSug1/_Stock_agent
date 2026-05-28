"""Render Module 6's catalyst_scores as a templated HTML report.

Splits output into two files in ``Outputs/``:

  * ``catalyst_scores.html``       — static template (CSS + JS + DOM
    skeleton). Rewritten only when the renderer's CSS/JS/markup changes
    (detected via a content-hash meta tag).
  * ``catalyst_scores_data.js``    — sidecar payload (``window.__DATA = {...}``).
    Rewritten on EVERY run. This is the only file the daily pipeline touches.

The HTML loads the sidecar via ``<script src="catalyst_scores_data.js"></script>``;
all DOM construction (KPI strip, score-distribution bar, tabs, table)
happens client-side from ``window.__DATA``. The user can double-click the
HTML from Explorer — no local HTTP server required (same-directory
``<script src>`` works under ``file://``).

Run from ``3_Biopharmcatalyst_parser/``:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_render_scores.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_render_scores.py --rebuild-template
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
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


OUTPUT_DIR = PROJECT_ROOT / "Outputs"
HTML_PATH = OUTPUT_DIR / "catalyst_scores.html"
DATA_PATH = OUTPUT_DIR / "catalyst_scores_data.js"
INSIDER_WINDOW_DAYS = 365  # matches Module 6's lookback_days default


# =====================================================================
# DB queries
# =====================================================================

def _max_snapshot_date(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT MAX(snapshot_date) FROM catalyst_scores").fetchone()
    return r[0] if r and r[0] else None


def _fetch_score_rows(
    conn: sqlite3.Connection,
    *,
    snap: date | None = None,
) -> tuple[list[dict], dict]:
    """Fetch catalyst_scores rows. Two modes:

    * ``snap`` is None (default) → ROLLING view: take MAX(snapshot_date)
      per unique (ticker, drug, nct_number, next_catalyst_type) and
      filter out catalysts whose `date_max` has now passed (catalyst
      materialized). This is the daily-pipeline view that accumulates
      across CSV uploads instead of showing only the latest snapshot.
    * ``snap`` is a specific date → SINGLE-snapshot view (legacy
      behaviour, useful for audit replay of a historical render).

    Returns ``(rows, meta)``. ``meta`` carries the snapshots covered,
    the effective "today" used for materialization, and a count of
    catalysts dropped because their window has now passed.
    """
    if snap is not None:
        rows = conn.execute(
            """
            SELECT
                cs.snapshot_date,
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
        meta = {
            "mode": "single",
            "snapshots_covered": [snap.isoformat()],
            "effective_today": snap.isoformat(),
            "materialized_dropped": 0,
        }
        return [dict(r) for r in rows], meta

    # Rolling view
    max_snap_iso = _max_snapshot_date(conn)
    if max_snap_iso is None:
        return [], {
            "mode": "rolling",
            "snapshots_covered": [],
            "effective_today": None,
            "materialized_dropped": 0,
        }

    # Two queries from the same JOIN — one with materialization, one without,
    # so we can report how many rows were dropped because date_max passed.
    base_sql = """
        WITH latest_per_catalyst AS (
            SELECT ticker, drug, nct_number, next_catalyst_type,
                   MAX(snapshot_date) AS max_snap
            FROM catalyst_scores
            GROUP BY ticker, drug, nct_number, next_catalyst_type
        )
        SELECT
            cs.snapshot_date,
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
        JOIN latest_per_catalyst l USING
            (ticker, drug, nct_number, next_catalyst_type)
        LEFT JOIN catalyst_timing t USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        LEFT JOIN catalyst_snapshots s USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        WHERE cs.snapshot_date = l.max_snap
    """

    # Count materialized-only rows (date_max < effective today) for the meta info.
    materialized_dropped = conn.execute(
        base_sql.replace(
            "SELECT\n            cs.snapshot_date,",
            "SELECT COUNT(*) AS n,",
        ).split("ORDER BY")[0]
        + " AND t.date_max IS NOT NULL AND t.date_max < ?",
        (max_snap_iso,),
    ).fetchone()[0]

    rows = conn.execute(
        base_sql + """
          AND (t.date_max IS NULL OR t.date_max >= ?)
        ORDER BY
            cs.hard_pass DESC,
            cs.composite_score DESC NULLS LAST,
            cs.insider_score DESC NULLS LAST
        """,
        (max_snap_iso,),
    ).fetchall()

    snapshots = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT snapshot_date FROM catalyst_scores ORDER BY snapshot_date"
        ).fetchall()
    ]
    meta = {
        "mode": "rolling",
        "snapshots_covered": snapshots,
        "effective_today": max_snap_iso,
        "materialized_dropped": materialized_dropped,
    }
    return [dict(r) for r in rows], meta


def _fetch_insider_trades(
    conn: sqlite3.Connection, snap: date, tickers: list[str],
) -> dict[str, list[dict]]:
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
    if not tickers or not q_latest or not q_prev:
        return {}
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


# =====================================================================
# Template — CSS + JS + HTML skeleton
# =====================================================================

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

.score-dist {
  display: flex; gap: 2px; margin-bottom: 16px;
  height: 32px; border-radius: 6px; overflow: hidden;
  background: var(--bg-elev); border: 1px solid var(--border);
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
thead th.sorted-asc::after  { content: " \\25B2"; color: var(--accent); }
thead th.sorted-desc::after { content: " \\25BC"; color: var(--accent); }
tbody tr {
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
tbody tr:hover { background: var(--bg-row); }
tbody tr.expanded { background: var(--bg-row); }
tbody td { padding: 7px 8px; vertical-align: top; }
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
.no-data {
  background: var(--bg-elev); border: 1px dashed var(--border);
  border-radius: 8px; padding: 24px;
  color: var(--text-dim); text-align: center; margin: 30px 0;
}
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
  state.minComposite = Number(state.minComposite) || 0;

  // ============ formatting helpers ============
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
    return `<span class="score ${scoreClass(s)}">${s.toFixed(1)}</span>`;
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
    const re = new RegExp(safePhrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    return escaped.replace(re, (m) => `<mark>${m}</mark>`);
  }

  // ============ aggregation ============
  function aggregate(rows) {
    const fail_counts = {};
    const score_dist = {'80-100': 0, '60-80': 0, '40-60': 0, '20-40': 0, '0-20': 0};
    let hard_pass = 0, defined = 0, undef = 0;
    let n_with_insider = 0, n_with_funds = 0;
    rows.forEach(r => {
      if (r.hard_pass) {
        hard_pass++;
        if (r.timing_bucket === 'catalyst_date_defined') defined++;
        else if (r.timing_bucket === 'catalyst_date_undefined') undef++;
        if (r.insider_gross_weighted_usd && r.insider_gross_weighted_usd > 0) n_with_insider++;
        if (r.fund_accumulation_usd && r.fund_accumulation_usd > 0) n_with_funds++;
      }
      if (r.fail_reasons) {
        r.fail_reasons.split(',').forEach(c => {
          if (c) fail_counts[c] = (fail_counts[c] || 0) + 1;
        });
      }
      if (r.composite_score != null) {
        const c = r.composite_score;
        if (c >= 80) score_dist['80-100']++;
        else if (c >= 60) score_dist['60-80']++;
        else if (c >= 40) score_dist['40-60']++;
        else if (c >= 20) score_dist['20-40']++;
        else score_dist['0-20']++;
      }
    });
    let funds_q_latest = null, funds_q_prev = null;
    for (const r of rows) {
      if (r.fund_quarter_latest) {
        funds_q_latest = r.fund_quarter_latest;
        funds_q_prev = r.fund_quarter_previous;
        break;
      }
    }
    return {
      total: rows.length,
      hard_pass,
      excluded: rows.length - hard_pass,
      defined,
      undefined: undef,
      fail_counts,
      score_dist,
      n_with_insider,
      n_with_funds,
      funds_q_latest,
      funds_q_prev,
    };
  }

  // ============ header / meta / KPIs ============
  function renderMeta(data, kpis) {
    document.getElementById('title').textContent = 'Catalyst scores — Module 6 output';
    const fundsMeta = kpis.funds_q_latest
      ? `funds quarters: ${escapeHtml(kpis.funds_q_latest)} vs ${escapeHtml(kpis.funds_q_prev || '?')}`
      : 'funds DB not attached';
    let viewMeta;
    if (data.view_mode === 'rolling') {
      const snaps = data.snapshots_covered || [];
      const range = snaps.length > 1
        ? `${snaps[0]} … ${snaps[snaps.length-1]} (${snaps.length} snapshots)`
        : (snaps[0] || '?');
      const dropped = data.materialized_dropped || 0;
      viewMeta = `Rolling view <code>${escapeHtml(range)}</code> · `
               + `as-of <code>${escapeHtml(data.effective_today || '?')}</code> · `
               + `${dropped} materialized catalysts hidden`;
    } else {
      viewMeta = `Snapshot <code>${escapeHtml(data.snapshot_date)}</code>`;
    }
    document.getElementById('meta').innerHTML =
      viewMeta + ' · ' +
      `rules <code>${escapeHtml(data.rules_version || '?')}</code> · ` +
      `${fundsMeta} · ` +
      `data generated ${escapeHtml(data.generated_at || '?')}`;
  }

  function kpiCard(label, value, sub, klass) {
    return `<div class="kpi ${klass || ''}">
      <div class="label">${escapeHtml(label)}</div>
      <div class="value">${escapeHtml(String(value))}</div>
      <div class="sub">${escapeHtml(sub || '')}</div>
    </div>`;
  }
  function renderKpiStrip(kpis) {
    document.getElementById('kpi-strip').innerHTML =
      kpiCard('Catalysts', kpis.total, 'snapshot total') +
      kpiCard('Hard pass', kpis.hard_pass, `${kpis.excluded} excluded`, 'green') +
      kpiCard('Defined timing', kpis.defined, 'specific/conference/month/quarter', 'indigo') +
      kpiCard('Undefined timing', kpis.undefined, 'half/year', 'amber') +
      kpiCard('With CEO/CFO buy', kpis.n_with_insider, 'of hard-pass rows', 'purple') +
      kpiCard('With fund accum', kpis.n_with_funds, 'of hard-pass rows', 'slate');
  }

  function renderScoreDist(dist) {
    const total = Object.values(dist).reduce((a, b) => a + b, 0) || 1;
    const segs = [];
    for (const [key, klass] of [['80-100','s80'],['60-80','s60'],['40-60','s40'],['20-40','s20'],['0-20','s00']]) {
      const n = dist[key];
      if (!n) continue;
      const pct = 100 * n / total;
      segs.push(`<div class="seg ${klass}" style="flex:${pct} 0 0" title="composite ${key}: ${n}">${n}</div>`);
    }
    document.getElementById('score-dist').innerHTML =
      segs.length ? segs.join('') : '<div class="seg" style="flex:1">no hard_pass rows</div>';
  }

  function renderFailMeta(fail_counts) {
    const order = ['H1','H2','H3','H4','H5'];
    const parts = order.filter(k => fail_counts[k]).map(k => `${k}=${fail_counts[k]}`);
    document.getElementById('fail-meta').textContent =
      'Failures by rule (overlapping across rows): ' + (parts.length ? parts.join(', ') : '—');
  }

  function renderTabs(kpis) {
    document.querySelectorAll('.tab').forEach(el => {
      const t = el.dataset.tab;
      el.classList.toggle('active', t === state.tab);
      const countEl = el.querySelector('.count');
      if (countEl) {
        const n = t === 'catalyst_date_defined' ? kpis.defined
                : t === 'catalyst_date_undefined' ? kpis.undefined
                : kpis.excluded;
        countEl.textContent = n;
      }
    });
  }

  function populateStageFilter(rows) {
    const stages = Array.from(new Set(rows.map(r => r.stage).filter(Boolean))).sort();
    const sel = document.getElementById('stage-filter');
    // Preserve current value if it's still valid
    const current = sel.value;
    sel.innerHTML = '<option value="any">any</option>' +
      stages.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
    if (current && Array.from(sel.options).some(o => o.value === current)) {
      sel.value = current;
    }
  }

  // ============ row filtering / sorting / table render ============
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
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string' && typeof bv === 'string') return av.localeCompare(bv) * dir;
      return (av - bv) * dir;
    });
  }

  function renderTable() {
    const rows = window.__DATA.rows;
    const filtered = rows.filter(rowMatchesFilters);
    const sorted = sortRows(filtered);
    document.getElementById('visible-count').textContent =
      `${filtered.length} of ${rows.length} rows`;
    const tbody = document.getElementById('rows-body');
    if (!sorted.length) {
      tbody.innerHTML = '<tr><td colspan="13" class="no-rows">No rows match the current filters.</td></tr>';
      return;
    }
    const isExcluded = state.tab === 'excluded';
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
    if (r.catalyst_text) {
      html += '<h4>Catalyst text (BPC)</h4>';
      html += `<div class="catalyst-text">${highlightPhrase(r.catalyst_text, r.matched_phrase)}</div>`;
    }
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
      next.remove(); tr.classList.remove('expanded'); return;
    }
    document.querySelectorAll('tr.expand-row').forEach(el => el.remove());
    document.querySelectorAll('tr.expanded').forEach(el => el.classList.remove('expanded'));
    const tr2 = document.createElement('tr');
    tr2.className = 'expand-row';
    tr2.innerHTML = `<td colspan="13">${buildExpandPanel(r)}</td>`;
    tr.classList.add('expanded');
    tr.parentNode.insertBefore(tr2, tr.nextSibling);
  }

  // ============ wiring ============
  function bind(kpis) {
    document.querySelectorAll('.tab').forEach(el => {
      el.addEventListener('click', () => {
        state.tab = el.dataset.tab;
        saveFilters(state); renderTabs(kpis); renderTable();
      });
    });
    document.getElementById('min-composite').addEventListener('input', e => {
      state.minComposite = Number(e.target.value) || 0; saveFilters(state); renderTable();
    });
    document.getElementById('require-insider').addEventListener('change', e => {
      state.requireInsider = e.target.value; saveFilters(state); renderTable();
    });
    document.getElementById('require-funds').addEventListener('change', e => {
      state.requireFunds = e.target.value; saveFilters(state); renderTable();
    });
    document.getElementById('stage-filter').addEventListener('change', e => {
      state.stage = e.target.value; saveFilters(state); renderTable();
    });
    document.getElementById('ticker-filter').addEventListener('input', e => {
      state.tickerFilter = e.target.value; saveFilters(state); renderTable();
    });
    document.getElementById('reset-filters').addEventListener('click', () => {
      state.minComposite = 0; state.requireInsider = 'any';
      state.requireFunds = 'any'; state.stage = 'any'; state.tickerFilter = '';
      saveFilters(state); restoreFormFromState(); renderTable();
    });
    document.querySelectorAll('thead th').forEach(th => {
      if (!th.dataset.col) return;
      th.addEventListener('click', () => {
        if (state.sortCol === th.dataset.col) {
          state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          state.sortCol = th.dataset.col; state.sortDir = 'desc';
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
    if (!window.__DATA || !window.__DATA.rows) {
      document.getElementById('no-data').style.display = '';
      return;
    }
    document.getElementById('no-data').style.display = 'none';
    window.__DATA.rows.forEach((r, i) => { r.__idx = i; });
    const kpis = aggregate(window.__DATA.rows);
    renderMeta(window.__DATA, kpis);
    renderKpiStrip(kpis);
    renderScoreDist(kpis.score_dist);
    renderFailMeta(kpis.fail_counts);
    populateStageFilter(window.__DATA.rows);
    renderTabs(kpis);
    bind(kpis);
    restoreFormFromState();
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


# HTML skeleton — purely structural. No data. The data-script src points at
# the sidecar file written separately on each pipeline run.
HTML_SKELETON = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="template-version" content="__TEMPLATE_VERSION__">
<title>Catalyst scores</title>
<style>__CSS__</style>
</head>
<body>
<h1 id="title">Catalyst scores</h1>
<div class="meta" id="meta">loading…</div>

<div id="no-data" class="no-data" style="display:none">
  <strong>No data loaded.</strong><br>
  This report expects a sibling file <code>catalyst_scores_data.js</code> next to this HTML.
  Run <code>scripts/3_6_render_scores.py</code> to generate it, then refresh.
</div>

<div class="kpi-strip" id="kpi-strip"></div>
<div class="score-dist" id="score-dist"></div>
<div class="meta" id="fail-meta" style="margin-top:-8px;margin-bottom:14px"></div>

<div class="tabs">
  <button class="tab" data-tab="catalyst_date_defined">Defined timing <span class="count">0</span></button>
  <button class="tab" data-tab="catalyst_date_undefined">Undefined timing <span class="count">0</span></button>
  <button class="tab" data-tab="excluded">Excluded <span class="count">0</span></button>
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

<script src="catalyst_scores_data.js"></script>
<script>__JS__</script>
</body>
</html>
"""


def _template_version() -> str:
    """SHA-7 of CSS + JS + HTML skeleton. Bumps whenever any of the
    three changes, so re-runs of the renderer can detect when the
    on-disk template is out of sync and rewrite it."""
    h = hashlib.sha256()
    h.update(CSS.encode("utf-8"))
    h.update(JS.encode("utf-8"))
    h.update(HTML_SKELETON.encode("utf-8"))
    return h.hexdigest()[:7]


def _build_template_html() -> str:
    return (HTML_SKELETON
            .replace("__CSS__", CSS)
            .replace("__JS__", JS)
            .replace("__TEMPLATE_VERSION__", _template_version()))


_TEMPLATE_META_RE = re.compile(
    r'<meta\s+name="template-version"\s+content="([^"]*)"', re.IGNORECASE,
)


def _existing_template_version(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8")[:4096]
    except OSError:
        return None
    m = _TEMPLATE_META_RE.search(head)
    return m.group(1) if m else None


# =====================================================================
# Render
# =====================================================================

def render(
    snapshot_date: date | None = None,
    out_dir: Path = OUTPUT_DIR,
    force_template: bool = False,
) -> tuple[Path, Path, str]:
    """Write the sidecar data file (always) and the HTML template
    (only when needed). Returns (html_path, data_path, template_action)
    where template_action is one of 'rebuilt' / 'up-to-date' / 'forced'.

    When ``snapshot_date`` is None, renders the ROLLING view: latest
    score per unique catalyst across all snapshots, with materialization
    filter (date_max >= effective today). Otherwise renders that single
    snapshot's rows (legacy behaviour).
    """
    conn = get_connection()
    try:
        rows, query_meta = _fetch_score_rows(conn, snap=snapshot_date)
        if not rows:
            raise RuntimeError(
                "no catalyst_scores rows available — run Module 6 first"
                if snapshot_date is None
                else f"no catalyst_scores rows for snapshot {snapshot_date.isoformat()}"
            )

        # For insider trades: anchor on the per-row snapshot_date when
        # rolling (different rows may be scored against different
        # snapshots). Simpler: fetch insider trades using each ticker's
        # row snapshot_date as the anchor — but the query becomes more
        # complex. Practical compromise: anchor on the effective-today
        # for rolling view, on the requested snapshot for single view.
        # This keeps insider data consistent with the headline "as of"
        # date shown in the report.
        anchor_iso = query_meta["effective_today"] or (
            snapshot_date.isoformat() if snapshot_date else None
        )
        anchor_date = date.fromisoformat(anchor_iso) if anchor_iso else None
        hard_pass_tickers = sorted({r["ticker"] for r in rows if r["hard_pass"]})
        insider_trades = (
            _fetch_insider_trades(conn, anchor_date, hard_pass_tickers)
            if anchor_date else {}
        )

        funds_tickers = sorted({
            r["ticker"] for r in rows
            if r["hard_pass"] and (r.get("fund_accumulation_usd") or 0) > 0
        })
        q_latest = next((r["fund_quarter_latest"] for r in rows
                         if r.get("fund_quarter_latest")), None)
        q_prev = next((r["fund_quarter_previous"] for r in rows
                       if r.get("fund_quarter_previous")), None)
        funds_breakdown = _fetch_funds_breakdown(conn, funds_tickers, q_latest, q_prev)

        rules_version = next((r["rules_version"] for r in rows if r.get("rules_version")), "")
        payload = {
            "view_mode": query_meta["mode"],                  # 'rolling' | 'single'
            "snapshots_covered": query_meta["snapshots_covered"],
            "effective_today": query_meta["effective_today"],
            "materialized_dropped": query_meta["materialized_dropped"],
            "snapshot_date": anchor_iso,    # backwards compatibility for JS that reads this
            "rules_version": rules_version,
            "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "rows": rows,
            "insider_trades": insider_trades,
            "funds_breakdown": funds_breakdown,
        }
    finally:
        conn.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / DATA_PATH.name
    html_path = out_dir / HTML_PATH.name

    # Always write the data sidecar.
    data_js = "window.__DATA = " + json.dumps(payload, default=str) + ";\n"
    data_path.write_text(data_js, encoding="utf-8")

    # Conditionally write the HTML template.
    current_ver = _template_version()
    existing_ver = _existing_template_version(html_path)
    if force_template:
        html_path.write_text(_build_template_html(), encoding="utf-8")
        action = "forced"
    elif existing_ver != current_ver:
        html_path.write_text(_build_template_html(), encoding="utf-8")
        action = "rebuilt" if existing_ver else "created"
    else:
        action = "up-to-date"

    return html_path, data_path, action


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-date", type=date.fromisoformat, default=None,
        help="ISO snapshot date to render (default: most recent in catalyst_scores)",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=OUTPUT_DIR,
        help=f"output directory (default: {OUTPUT_DIR.relative_to(PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--rebuild-template", action="store_true",
        help="force the HTML template to be rewritten even if the version hash matches",
    )
    args = parser.parse_args()

    html_path, data_path, action = render(
        snapshot_date=args.snapshot_date,
        out_dir=args.out_dir,
        force_template=args.rebuild_template,
    )
    data_kb = data_path.stat().st_size / 1024
    html_kb = html_path.stat().st_size / 1024
    print(f"[3_6_render_scores] data:     {data_path}  ({data_kb:.1f} KB) — refreshed")
    print(f"[3_6_render_scores] template: {html_path}  ({html_kb:.1f} KB) — {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
