"""Render Module 5's catalyst-timing report as a templated HTML.

Splits output into two files in ``Outputs/``:

  * ``catalyst_timings.html``       — static template (CSS + JS + DOM
    skeleton). Rewritten only when the renderer's CSS/JS/markup changes
    (detected via a content-hash meta tag).
  * ``catalyst_timings_data.js``    — sidecar payload (``window.__DATA = {...}``).
    Rewritten on every pipeline run.

Default view is **rolling**: latest timing per unique
(ticker, drug, nct, type) across all snapshots, with materialization
filter (date_max >= effective today). Pass ``--snapshot-date YYYY-MM-DD``
for legacy single-snapshot view.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_render_timings.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_render_timings.py --rebuild-template
"""
from __future__ import annotations

import argparse
import hashlib
import html
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

from database.db import DEFAULT_DB_PATH, get_connection  # noqa: E402


OUTPUT_DIR = PROJECT_ROOT / "Outputs"
OUTPUT_PATH = OUTPUT_DIR / "catalyst_timings.html"
DATA_PATH = OUTPUT_DIR / "catalyst_timings_data.js"


# Match colors across KPI strip + bars + chips + table-cell tags.
LANE_COLOR = {
    "conference":             "#3b82f6",  # blue
    "catalyst_date_specific": "#10b981",  # green
    "text_parse":             "#f59e0b",  # amber
    "catalyst_date_bucket":   "#6b7280",  # slate
    "unknown":                "#ef4444",  # red
}
LANE_LABEL = {
    "conference":             "conference",
    "catalyst_date_specific": "specific date",
    "text_parse":             "text-parsed",
    "catalyst_date_bucket":   "bucket fallback",
    "unknown":                "unknown",
}
LANE_ORDER = ["conference", "catalyst_date_specific", "text_parse",
              "catalyst_date_bucket", "unknown"]

TIER_ORDER = ["specific", "conference", "month", "quarter", "half", "year", "unknown"]

# Roles that count as "executive" for the insider-activity summary.
# 10% owners are institutional, not executives — kept in the broader
# "any insider buy" tally but excluded from the C-suite badge.
EXECUTIVE_ROLES = ("CEO", "CFO", "COO", "CMO", "CSO", "President", "Chair")
# How far back the per-ticker insider summary looks. Anchored on the
# snapshot date, not actual today, so re-renders of an old snapshot
# reproduce the same insider context. 365d aligns with M2/M3's default
# `--lookback-days` so any insider activity captured in the EDGAR
# ingest is also visible in this report.
INSIDER_WINDOW_DAYS = 365


def _max_snapshot_date(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT MAX(snapshot_date) FROM catalyst_timing").fetchone()
    return r[0] if r and r[0] else None


def _fetch_rows(
    conn: sqlite3.Connection,
    *,
    snap: date | None = None,
) -> tuple[list[dict], dict]:
    """Fetch catalyst_timing+catalyst_snapshots rows. Two modes:

    * ``snap`` is None (default) → ROLLING view: latest timing row per
      (ticker, drug, nct_number, next_catalyst_type) across all
      snapshots, filtered to ``date_max >= effective_today``.
    * ``snap`` is a specific date → single-snapshot legacy view.

    Returns ``(rows, meta)``.
    """
    if snap is not None:
        rows = conn.execute(
            """
            SELECT
                t.snapshot_date,
                t.ticker, t.drug, t.nct_number, t.next_catalyst_type,
                t.date_min, t.date_max, t.precision_tier, t.source_lane,
                t.matched_phrase, t.rules_version, t.computed_at,
                s.name, s.indication, s.stage, s.status,
                s.catalyst_date, s.catalyst_text, s.conference,
                s.market_cap_usd, s.price, s.sentiment
            FROM catalyst_timing t
            JOIN catalyst_snapshots s USING
                (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
            WHERE t.snapshot_date = ?
            ORDER BY
                CASE WHEN t.date_min IS NULL THEN 1 ELSE 0 END,
                t.date_min ASC,
                t.ticker ASC
            """,
            (snap.isoformat(),),
        ).fetchall()
        return [dict(r) for r in rows], {
            "mode": "single",
            "snapshots_covered": [snap.isoformat()],
            "effective_today": snap.isoformat(),
            "materialized_dropped": 0,
        }

    max_snap_iso = _max_snapshot_date(conn)
    if max_snap_iso is None:
        return [], {
            "mode": "rolling",
            "snapshots_covered": [],
            "effective_today": None,
            "materialized_dropped": 0,
        }

    base = """
        WITH latest_per_catalyst AS (
            SELECT ticker, drug, nct_number, next_catalyst_type,
                   MAX(snapshot_date) AS max_snap
            FROM catalyst_timing
            GROUP BY ticker, drug, nct_number, next_catalyst_type
        )
        SELECT
            t.snapshot_date,
            t.ticker, t.drug, t.nct_number, t.next_catalyst_type,
            t.date_min, t.date_max, t.precision_tier, t.source_lane,
            t.matched_phrase, t.rules_version, t.computed_at,
            s.name, s.indication, s.stage, s.status,
            s.catalyst_date, s.catalyst_text, s.conference,
            s.market_cap_usd, s.price, s.sentiment
        FROM catalyst_timing t
        JOIN latest_per_catalyst l USING (ticker, drug, nct_number, next_catalyst_type)
        JOIN catalyst_snapshots s USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        WHERE t.snapshot_date = l.max_snap
    """
    materialized_dropped = conn.execute(
        """
        WITH latest_per_catalyst AS (
            SELECT ticker, drug, nct_number, next_catalyst_type,
                   MAX(snapshot_date) AS max_snap
            FROM catalyst_timing
            GROUP BY ticker, drug, nct_number, next_catalyst_type
        )
        SELECT COUNT(*) FROM catalyst_timing t
        JOIN latest_per_catalyst l USING (ticker, drug, nct_number, next_catalyst_type)
        WHERE t.snapshot_date = l.max_snap
          AND t.date_max IS NOT NULL AND t.date_max < ?
        """,
        (max_snap_iso,),
    ).fetchone()[0]
    rows = conn.execute(
        base + """
          AND (t.date_max IS NULL OR t.date_max >= ?)
        ORDER BY
            CASE WHEN t.date_min IS NULL THEN 1 ELSE 0 END,
            t.date_min ASC,
            t.ticker ASC
        """,
        (max_snap_iso,),
    ).fetchall()

    snapshots = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT snapshot_date FROM catalyst_timing ORDER BY snapshot_date"
        ).fetchall()
    ]
    return [dict(r) for r in rows], {
        "mode": "rolling",
        "snapshots_covered": snapshots,
        "effective_today": max_snap_iso,
        "materialized_dropped": materialized_dropped,
    }


def _fetch_insider_activity(
    conn: sqlite3.Connection, snap: date, tickers: list[str],
) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """For each ticker, pull a ``last 90 days from snap`` summary of
    insider open-market buys from v_executive_open_market_trades.

    Returns (summary_by_ticker, recent_trades_by_ticker).

    * summary_by_ticker[t] -> {
        'all_buys': int,         # any role
        'exec_buys': int,        # CEO / CFO / COO / CMO / CSO / President / Chair
        'director_buys': int,    # plain directors (no exec title)
        'role_counts': {role: n},
        'gross_usd_total': float,
        'latest_buy_date': str | None,
      }
    * recent_trades_by_ticker[t] -> [up to 10 most recent trades, each:
        {source, insider_name, insider_position, executive_role,
         date, shares, trade_price, gross_usd}]
    """
    if not tickers:
        return {}, {}
    floor_iso = (snap - timedelta(days=INSIDER_WINDOW_DAYS)).isoformat()
    qmarks = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT
            ticker,
            source,
            insider_name,
            insider_position,
            executive_role,
            COALESCE(transaction_date, filing_date) AS event_date,
            shares,
            trade_price,
            gross_usd
        FROM v_executive_open_market_trades
        WHERE buy_sell = 'Buy'
          AND COALESCE(transaction_date, filing_date) >= ?
          AND ticker IN ({qmarks})
        ORDER BY event_date DESC, ticker
        """,
        [floor_iso, *tickers],
    ).fetchall()

    summary: dict[str, dict] = {}
    recent: dict[str, list[dict]] = {}
    for r in rows:
        t = r["ticker"]
        s = summary.setdefault(t, {
            "all_buys": 0, "exec_buys": 0, "director_buys": 0,
            "role_counts": {}, "gross_usd_total": 0.0, "latest_buy_date": None,
        })
        s["all_buys"] += 1
        role = r["executive_role"] or "Other"
        s["role_counts"][role] = s["role_counts"].get(role, 0) + 1
        if role in EXECUTIVE_ROLES:
            s["exec_buys"] += 1
        elif role == "Director":
            s["director_buys"] += 1
        if r["gross_usd"] is not None:
            s["gross_usd_total"] += float(r["gross_usd"])
        d = r["event_date"]
        if d and (s["latest_buy_date"] is None or d > s["latest_buy_date"]):
            s["latest_buy_date"] = d

        if len(recent.setdefault(t, [])) < 10:
            recent[t].append({
                "source": r["source"],
                "insider_name": r["insider_name"],
                "insider_position": r["insider_position"] or "",
                "executive_role": role,
                "date": d,
                "shares": r["shares"],
                "trade_price": r["trade_price"],
                "gross_usd": r["gross_usd"],
            })
    return summary, recent


def _build_summary(rows: list[dict], snap: date) -> dict:
    """Compute KPI numbers — lane / tier counts + window memberships."""
    n = len(rows)
    lane_counts = {lane: 0 for lane in LANE_ORDER}
    tier_counts = {tier: 0 for tier in TIER_ORDER}
    for r in rows:
        lane_counts[r["source_lane"]] = lane_counts.get(r["source_lane"], 0) + 1
        tier_counts[r["precision_tier"]] = tier_counts.get(r["precision_tier"], 0) + 1

    # Window math anchored on snapshot_date (matches M5 resolver semantics).
    t14 = snap + timedelta(days=14)
    t60 = snap + timedelta(days=60)
    t180 = snap + timedelta(days=180)
    disc = exec_ = past_or_unknown = 0
    for r in rows:
        if r["precision_tier"] == "unknown":
            past_or_unknown += 1
            continue
        dmin = date.fromisoformat(r["date_min"]) if r["date_min"] else None
        dmax = date.fromisoformat(r["date_max"]) if r["date_max"] else None
        if dmin is None or dmax is None:
            past_or_unknown += 1
            continue
        # Discovery: dmin <= T+180 AND dmax >= T+14
        if dmin <= t180 and dmax >= t14:
            disc += 1
        # Execution: dmin <= T+60 AND dmax >= T+14
        if dmin <= t60 and dmax >= t14:
            exec_ += 1
        # Past (entirely past T+14)
        if dmax < t14:
            past_or_unknown += 1

    return {
        "total": n,
        "lane_counts": lane_counts,
        "tier_counts": tier_counts,
        "windows": {
            "discovery": disc,
            "execution": exec_,
            "past_or_unknown": past_or_unknown,
        },
    }


def _build_monthly_histogram(rows: list[dict], snap: date) -> list[dict]:
    """Bucket rows by date_min month for the next 12 months."""
    months: list[dict] = []
    for i in range(12):
        # Walk forward i months from snap
        y = snap.year + (snap.month - 1 + i) // 12
        m = (snap.month - 1 + i) % 12 + 1
        key = f"{y}-{m:02d}"
        label = date(y, m, 1).strftime("%b %Y")
        months.append({"key": key, "label": label, "count": 0, "by_tier": {}})

    idx = {m["key"]: m for m in months}
    for r in rows:
        if not r["date_min"]:
            continue
        dmin = date.fromisoformat(r["date_min"])
        key = f"{dmin.year}-{dmin.month:02d}"
        bucket = idx.get(key)
        if not bucket:
            continue
        bucket["count"] += 1
        tier = r["precision_tier"]
        bucket["by_tier"][tier] = bucket["by_tier"].get(tier, 0) + 1
    return months


def _row_for_json(r: dict, snap: date,
                  insider_summary: dict, recent_trades: dict) -> dict:
    days_to_min = None
    if r["date_min"]:
        days_to_min = (date.fromisoformat(r["date_min"]) - snap).days
    t = r["ticker"]
    s = insider_summary.get(t)
    return {
        "snapshot_date": r["snapshot_date"] if "snapshot_date" in r.keys() else None,
        "ticker": t,
        "drug": r["drug"],
        "name": r["name"],
        "indication": r["indication"] or "",
        "stage": r["stage"] or "",
        "status": r["status"] or "",
        "next_catalyst_type": r["next_catalyst_type"] or "",
        "nct_number": r["nct_number"] or "",
        "source_lane": r["source_lane"],
        "precision_tier": r["precision_tier"],
        "date_min": r["date_min"],
        "date_max": r["date_max"],
        "days_to_min": days_to_min,
        "matched_phrase": r["matched_phrase"] or "",
        "catalyst_date_raw": r["catalyst_date"] or "",
        "catalyst_text": r["catalyst_text"] or "",
        "conference": r["conference"] or "",
        "market_cap_usd": r["market_cap_usd"],
        "price": r["price"],
        "sentiment": r["sentiment"] or "",
        # Insider activity (last INSIDER_WINDOW_DAYS days from snapshot).
        # Snapshots populated per-ticker; nulls when ticker has zero rows.
        "insider_all_buys": (s or {}).get("all_buys", 0),
        "insider_exec_buys": (s or {}).get("exec_buys", 0),
        "insider_director_buys": (s or {}).get("director_buys", 0),
        "insider_gross_usd": (s or {}).get("gross_usd_total", 0.0),
        "insider_latest_buy_date": (s or {}).get("latest_buy_date"),
        "insider_recent_trades": recent_trades.get(t, []),
    }


def _build_payload(
    *,
    anchor_date: date,
    query_meta: dict,
    summary: dict,
    months: list[dict],
    rows_json: list[dict],
    rules_version: str,
    computed_at: str,
    db_path: Path,
) -> dict:
    return {
        "view_mode": query_meta["mode"],
        "snapshots_covered": query_meta["snapshots_covered"],
        "effective_today": query_meta["effective_today"],
        "materialized_dropped": query_meta["materialized_dropped"],
        "snapshot_date": anchor_date.isoformat(),  # also used by JS for window arithmetic
        "computed_at": computed_at,
        "rules_version": rules_version,
        "summary": summary,
        "months": months,
        "rows": rows_json,
        "lane_color": LANE_COLOR,
        "lane_label": LANE_LABEL,
        "lane_order": LANE_ORDER,
        "tier_order": TIER_ORDER,
        "executive_roles": list(EXECUTIVE_ROLES),
        "insider_window_days": INSIDER_WINDOW_DAYS,
        "db_path": str(db_path),
    }


def _template_version() -> str:
    """SHA-7 of the HTML template string — bumps on any CSS/JS/markup
    change, so the renderer can detect a stale on-disk template."""
    return hashlib.sha256(_HTML_TEMPLATE.encode("utf-8")).hexdigest()[:7]


def _build_template_html() -> str:
    return _HTML_TEMPLATE.replace("__TEMPLATE_VERSION__", _template_version())


def _build_data_js(payload: dict) -> str:
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Defend against an accidental "</script>" inside any string field.
    blob_safe = blob.replace("</", "<\\/")
    return f"window.__DATA = {blob_safe};\n"


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


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="template-version" content="__TEMPLATE_VERSION__">
<title>Catalyst Timing Report</title>
<style>
  :root {
    --bg: #0f172a;
    --panel: #1e293b;
    --panel-2: #334155;
    --border: #475569;
    --text: #e2e8f0;
    --text-dim: #94a3b8;
    --text-faint: #64748b;
    --accent: #38bdf8;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text); }
  body {
    font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
          "Helvetica Neue", Arial, sans-serif;
    padding: 16px 24px 64px;
  }
  h1 { font-size: 18px; margin: 0 0 4px; font-weight: 600; }
  h2 { font-size: 13px; margin: 24px 0 8px; font-weight: 600; color: var(--text-dim);
       text-transform: uppercase; letter-spacing: .08em; }
  .meta { color: var(--text-dim); font-size: 12px; margin-bottom: 16px; }
  .meta code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
               background: var(--panel); padding: 1px 6px; border-radius: 3px; }

  /* KPI strip */
  .kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 12px; }
  .kpi {
    background: var(--panel); border-radius: 6px; padding: 12px 14px;
    border-left: 4px solid var(--border);
  }
  .kpi .label { color: var(--text-dim); font-size: 11px; text-transform: uppercase;
                letter-spacing: .06em; }
  .kpi .value { font-size: 24px; font-weight: 600; margin: 4px 0 0; }
  .kpi .pct   { color: var(--text-faint); font-size: 12px; }

  .windows {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 24px;
  }
  .win { background: var(--panel); border-radius: 6px; padding: 10px 14px; }
  .win .label { color: var(--text-dim); font-size: 11px; text-transform: uppercase;
                letter-spacing: .06em; }
  .win .value { font-size: 18px; font-weight: 600; }
  .win .sub   { color: var(--text-faint); font-size: 11px; margin-top: 2px; }

  /* Distribution bars */
  .dist-row { display: grid; grid-template-columns: 160px 1fr 60px; gap: 8px;
              align-items: center; margin: 4px 0; }
  .dist-row .label { color: var(--text-dim); font-size: 12px; text-align: right; }
  .dist-bar { height: 18px; background: var(--panel); border-radius: 3px;
              position: relative; overflow: hidden; }
  .dist-bar .fill { height: 100%; }
  .dist-row .count { font-size: 12px; color: var(--text); }

  /* Histogram */
  .hist { display: grid; grid-template-columns: repeat(12, 1fr); gap: 4px;
          margin-top: 8px; align-items: end; }
  .hist .bar { display: flex; flex-direction: column-reverse; min-height: 4px;
               background: var(--panel); border-radius: 3px 3px 0 0;
               position: relative; }
  .hist .bar .seg { width: 100%; }
  .hist .hlbl { font-size: 10px; color: var(--text-faint); text-align: center;
                margin-top: 4px; }
  .hist .hcnt { font-size: 11px; color: var(--text); text-align: center;
                margin-bottom: 2px; }

  /* Legend */
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin: 6px 0 0;
            color: var(--text-dim); font-size: 11px; }
  .legend span { display: inline-flex; align-items: center; gap: 4px; }
  .legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

  /* Filters */
  .filters { display: flex; gap: 14px; flex-wrap: wrap; align-items: center;
             background: var(--panel); padding: 10px 14px; border-radius: 6px;
             margin: 8px 0; }
  .filter-group { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .filter-group label { color: var(--text-dim); font-size: 11px;
                        text-transform: uppercase; letter-spacing: .06em; }
  .pill {
    display: inline-flex; align-items: center; padding: 3px 8px; font-size: 11px;
    border-radius: 999px; background: var(--panel-2); color: var(--text-dim);
    border: 1px solid var(--border); cursor: pointer; user-select: none;
  }
  .pill.on { color: var(--text); border-color: transparent; }
  .filters select, .filters input[type=text] {
    background: var(--panel-2); color: var(--text); border: 1px solid var(--border);
    padding: 4px 8px; border-radius: 4px; font-size: 12px;
  }
  .filters input[type=text] { min-width: 200px; }
  .filters .reset {
    background: transparent; border: 1px solid var(--border); color: var(--text-dim);
    padding: 3px 10px; border-radius: 4px; font-size: 11px; cursor: pointer;
  }
  .footer-count { color: var(--text-dim); font-size: 12px; padding: 8px 4px; }

  /* Table */
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  thead th { position: sticky; top: 0; background: var(--panel); padding: 8px 10px;
             text-align: left; font-weight: 600; color: var(--text-dim);
             border-bottom: 1px solid var(--border); cursor: pointer; user-select: none;
             white-space: nowrap; }
  thead th .arrow { color: var(--text-faint); font-size: 10px; margin-left: 4px; }
  tbody td { padding: 8px 10px; border-bottom: 1px solid #1e293b; vertical-align: top; }
  tbody tr.row { cursor: pointer; }
  tbody tr.row:hover { background: #172033; }
  tbody tr.row.exp { background: #172033; }
  tbody tr.detail td { padding: 10px 16px 16px; background: #0b1224; color: var(--text-dim); }
  tbody tr.detail .dgrid { display: grid; grid-template-columns: 130px 1fr; gap: 6px 14px; }
  tbody tr.detail .dgrid .k { color: var(--text-faint); font-size: 11px;
                              text-transform: uppercase; letter-spacing: .06em; }
  tbody tr.detail .dgrid .v { color: var(--text); white-space: pre-wrap; word-wrap: break-word; }
  tbody tr.detail mark { background: #f59e0b; color: #1e293b; padding: 0 2px; border-radius: 2px; }
  .tag {
    display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 10px;
    font-weight: 600; color: #fff; white-space: nowrap;
  }
  .ttype { display: inline-block; padding: 0 6px; border-radius: 3px; font-size: 10px;
           font-weight: 600; background: var(--panel-2); color: var(--text-dim);
           text-transform: uppercase; letter-spacing: .04em; }
  .num { font-variant-numeric: tabular-nums; }
  .imminent { color: #fbbf24; }
  .past { color: var(--text-faint); }

  /* Insider activity */
  .insider-badge {
    display: inline-flex; align-items: center; gap: 4px; padding: 1px 7px;
    border-radius: 999px; font-size: 10px; font-weight: 600; white-space: nowrap;
  }
  .insider-badge.exec     { background: #10b981; color: #052e1f; }
  .insider-badge.director { background: #6366f1; color: #1e1b4b; }
  .insider-badge.any      { background: #475569; color: #e2e8f0; }
  .insider-badge.none     { color: var(--text-faint); }
  .role-tag {
    display: inline-block; padding: 0 6px; border-radius: 3px; font-size: 10px;
    font-weight: 600; background: var(--panel-2); color: var(--text-dim);
  }
  .role-tag.exec { background: #10b981; color: #052e1f; }
  .role-tag.dir  { background: #6366f1; color: #1e1b4b; }
  .trade-source { font-size: 9px; padding: 0 5px; border-radius: 3px;
                  background: var(--panel-2); color: var(--text-faint);
                  text-transform: uppercase; letter-spacing: .04em; }
  .recent-trades { font-size: 11px; margin-top: 4px; }
  .recent-trades table { width: 100%; border-collapse: collapse; }
  .recent-trades th, .recent-trades td {
    text-align: left; padding: 3px 6px; border-bottom: 1px solid #1e293b;
    color: var(--text-dim); font-weight: normal;
  }
  .recent-trades th { color: var(--text-faint); font-size: 10px;
                      text-transform: uppercase; letter-spacing: .04em; }
  .recent-trades td.num { color: var(--text); }
</style>
</head>
<body>

<h1>Catalyst Timing Report — <span id="snapshot-label">…</span></h1>
<div class="meta" id="meta"></div>

<div class="kpis" id="kpis"></div>
<div class="windows" id="windows"></div>

<h2 id="insider-h2">Insider activity (last <span id="insider-window-label">90</span>d, from snapshot date)</h2>
<div class="windows" id="insider-kpis"></div>

<h2>Lane distribution</h2>
<div id="lane-bars"></div>

<h2>Precision tier distribution</h2>
<div id="tier-bars"></div>

<h2>Catalysts by month (date_min, next 12 months, stacked by tier)</h2>
<div class="hist" id="hist"></div>
<div class="legend" id="hist-legend"></div>

<h2>Detail</h2>
<div class="filters" id="filters">
  <div class="filter-group" id="lane-pills">
    <label>Lane</label>
  </div>
  <div class="filter-group" id="tier-pills">
    <label>Tier</label>
  </div>
  <div class="filter-group">
    <label>Stage</label>
    <select id="f-stage"><option value="">all</option></select>
  </div>
  <div class="filter-group">
    <label>Window</label>
    <select id="f-window">
      <option value="all">all</option>
      <option value="discovery">discovery (T+14..T+180)</option>
      <option value="execution">execution (T+14..T+60)</option>
      <option value="past">past or unknown</option>
    </select>
  </div>
  <div class="filter-group">
    <label>Insider activity</label>
    <select id="f-insider">
      <option value="all">all</option>
      <option value="exec">has C-suite/Chair buy</option>
      <option value="any">has any insider buy</option>
    </select>
  </div>
  <div class="filter-group">
    <label>Search</label>
    <input type="text" id="f-search" placeholder="ticker or drug…">
  </div>
  <button class="reset" id="reset-filters">reset</button>
</div>
<div class="footer-count" id="footer-count"></div>
<table id="tbl">
  <thead>
    <tr id="thead-row"></tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<script src="catalyst_timings_data.js"></script>
<script>
const DATA = window.__DATA || {rows: [], summary: {windows: {}, lane_counts: {}, tier_counts: {}, insider: {}}, months: []};
const LS_KEY = "biotech_m5_filters_v1";

// ---- helpers -----------------------------------------------------------
const $ = (s, root=document) => root.querySelector(s);
const fmtPct = (n, total) => total ? (100 * n / total).toFixed(1) + "%" : "—";
const fmtDate = (s) => s || "";
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, ch => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[ch]));
const fmtMcap = (v) => {
  if (v == null) return "—";
  if (v >= 1e12) return (v/1e12).toFixed(2) + "T";
  if (v >= 1e9)  return (v/1e9).toFixed(2)  + "B";
  if (v >= 1e6)  return (v/1e6).toFixed(1)  + "M";
  return v.toFixed(0);
};
const escapeRegex = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// ---- title + snapshot label -------------------------------------------
(function setHeader() {
  const lbl = document.getElementById("snapshot-label");
  if (DATA.view_mode === "rolling") {
    const snaps = DATA.snapshots_covered || [];
    const range = snaps.length > 1
      ? `${snaps[0]} … ${snaps[snaps.length-1]} (${snaps.length} snapshots)`
      : (snaps[0] || "?");
    lbl.textContent = `Rolling: ${range} (as-of ${DATA.effective_today || "?"})`;
    document.title = `Catalyst Timing Report — Rolling as-of ${DATA.effective_today || ""}`;
  } else {
    lbl.textContent = DATA.snapshot_date || "?";
    document.title = `Catalyst Timing Report — ${DATA.snapshot_date || ""}`;
  }
})();

// ---- meta line ---------------------------------------------------------
const _dropMsg = DATA.materialized_dropped
  ? ` · <span style="color:var(--text-faint)">${DATA.materialized_dropped} materialized hidden</span>`
  : "";
$("#meta").innerHTML =
  `Computed <code>${escapeHtml(DATA.computed_at)}</code> · ` +
  `rules <code>${escapeHtml(DATA.rules_version)}</code> · ` +
  `<code>${DATA.summary.total}</code> rows from <code>${escapeHtml(DATA.db_path)}</code>` +
  _dropMsg;
$("#insider-window-label").textContent = DATA.insider_window_days;

// ---- KPI strip ---------------------------------------------------------
const kpiHtml = DATA.lane_order.map(lane => {
  const n = DATA.summary.lane_counts[lane] || 0;
  const color = DATA.lane_color[lane];
  return `
    <div class="kpi" style="border-left-color:${color}">
      <div class="label">${escapeHtml(DATA.lane_label[lane])}</div>
      <div class="value num">${n}</div>
      <div class="pct">${fmtPct(n, DATA.summary.total)}</div>
    </div>`;
}).join("");
$("#kpis").innerHTML = kpiHtml;

// ---- window summaries --------------------------------------------------
const W = DATA.summary.windows;
const ref = DATA.snapshot_date;
$("#windows").innerHTML = `
  <div class="win">
    <div class="label">Discovery window</div>
    <div class="value num">${W.discovery}</div>
    <div class="sub">T+14..T+180 from ${ref}</div>
  </div>
  <div class="win">
    <div class="label">Execution window</div>
    <div class="value num">${W.execution}</div>
    <div class="sub">T+14..T+60 from ${ref}</div>
  </div>
  <div class="win">
    <div class="label">Past or unknown</div>
    <div class="value num">${W.past_or_unknown}</div>
    <div class="sub">excluded from both windows</div>
  </div>
`;

// ---- insider activity KPI strip ---------------------------------------
const I = DATA.summary.insider || {};
$("#insider-kpis").innerHTML = `
  <div class="win" style="border-left:4px solid #10b981; padding-left:10px">
    <div class="label">Catalysts with C-suite/Chair buys</div>
    <div class="value num">${I.catalysts_with_exec_buys || 0}</div>
    <div class="sub">CEO / CFO / COO / CMO / CSO / President / Chair</div>
  </div>
  <div class="win" style="border-left:4px solid #475569; padding-left:10px">
    <div class="label">Catalysts with any insider buy</div>
    <div class="value num">${I.catalysts_with_any_insider_buys || 0}</div>
    <div class="sub">includes directors, 10% owners, other officers</div>
  </div>
  <div class="win" style="border-left:4px solid #6366f1; padding-left:10px">
    <div class="label">Tickers with C-suite/Chair buys</div>
    <div class="value num">${I.tickers_with_exec_buys || 0}</div>
    <div class="sub">distinct issuers in our universe</div>
  </div>
`;

// ---- distribution bars -------------------------------------------------
function renderDistRows(targetEl, counts, order, colorMap, labelMap) {
  const max = Math.max(...order.map(k => counts[k] || 0));
  targetEl.innerHTML = order.map(k => {
    const n = counts[k] || 0;
    const pct = max ? (100 * n / max) : 0;
    const col = colorMap[k] || "#64748b";
    return `
      <div class="dist-row">
        <div class="label">${escapeHtml(labelMap[k] || k)}</div>
        <div class="dist-bar"><div class="fill" style="width:${pct}%;background:${col}"></div></div>
        <div class="count num">${n}</div>
      </div>`;
  }).join("");
}
renderDistRows($("#lane-bars"), DATA.summary.lane_counts, DATA.lane_order,
               DATA.lane_color, DATA.lane_label);

const TIER_COLOR = {
  specific:   "#22d3ee",
  conference: "#3b82f6",
  month:      "#a78bfa",
  quarter:    "#f59e0b",
  half:       "#f97316",
  year:       "#dc2626",
  unknown:    "#475569",
};
const TIER_LABEL = Object.fromEntries(DATA.tier_order.map(t => [t, t]));
renderDistRows($("#tier-bars"), DATA.summary.tier_counts, DATA.tier_order,
               TIER_COLOR, TIER_LABEL);

// ---- monthly histogram (stacked by tier) -------------------------------
const histMax = Math.max(1, ...DATA.months.map(m => m.count));
const histHtml = DATA.months.map(m => {
  const heightPx = Math.round(160 * m.count / histMax);
  const segs = DATA.tier_order.map(t => {
    const v = m.by_tier[t] || 0;
    if (!v) return "";
    const h = Math.round(160 * v / histMax);
    return `<div class="seg" style="height:${h}px;background:${TIER_COLOR[t]}" title="${t}: ${v}"></div>`;
  }).join("");
  return `
    <div>
      <div class="hcnt">${m.count || ""}</div>
      <div class="bar" style="height:${heightPx}px">${segs}</div>
      <div class="hlbl">${escapeHtml(m.label)}</div>
    </div>`;
}).join("");
$("#hist").innerHTML = histHtml;
$("#hist-legend").innerHTML = DATA.tier_order.map(t => `
  <span><i style="background:${TIER_COLOR[t]}"></i>${t}</span>
`).join("");

// ---- filter pills ------------------------------------------------------
const state = {
  lanes: new Set(DATA.lane_order),
  tiers: new Set(DATA.tier_order),
  stage: "",
  window: "all",
  insider: "all",      // 'all' | 'exec' | 'any'
  search: "",
  sortKey: "days_to_min",
  sortDir: 1,
};

// restore from localStorage
try {
  const saved = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  if (Array.isArray(saved.lanes)) state.lanes = new Set(saved.lanes);
  if (Array.isArray(saved.tiers)) state.tiers = new Set(saved.tiers);
  if (typeof saved.stage   === "string") state.stage   = saved.stage;
  if (typeof saved.window  === "string") state.window  = saved.window;
  if (typeof saved.insider === "string") state.insider = saved.insider;
  if (typeof saved.search  === "string") state.search  = saved.search;
  if (typeof saved.sortKey === "string") state.sortKey = saved.sortKey;
  if (typeof saved.sortDir === "number") state.sortDir = saved.sortDir;
} catch (e) {}

function saveState() {
  localStorage.setItem(LS_KEY, JSON.stringify({
    lanes: [...state.lanes], tiers: [...state.tiers],
    stage: state.stage, window: state.window, insider: state.insider,
    search: state.search, sortKey: state.sortKey, sortDir: state.sortDir,
  }));
}

function renderPills(containerSel, labelText, order, colorMap, labelMap, set) {
  const cont = $(containerSel);
  const labelEl = cont.querySelector("label");
  cont.innerHTML = "";
  cont.appendChild(labelEl);
  order.forEach(k => {
    const pill = document.createElement("span");
    pill.className = "pill" + (set.has(k) ? " on" : "");
    pill.textContent = labelMap[k] || k;
    if (set.has(k)) pill.style.background = colorMap[k];
    pill.onclick = () => {
      if (set.has(k)) set.delete(k); else set.add(k);
      renderPills(containerSel, labelText, order, colorMap, labelMap, set);
      saveState(); rerender();
    };
    cont.appendChild(pill);
  });
}
renderPills("#lane-pills", "Lane", DATA.lane_order, DATA.lane_color,
            DATA.lane_label, state.lanes);
renderPills("#tier-pills", "Tier", DATA.tier_order, TIER_COLOR, TIER_LABEL, state.tiers);

// stage dropdown — populate from data
const stages = Array.from(new Set(DATA.rows.map(r => r.stage).filter(Boolean))).sort();
const stageSel = $("#f-stage");
stages.forEach(s => {
  const o = document.createElement("option");
  o.value = s; o.textContent = s;
  stageSel.appendChild(o);
});
stageSel.value = state.stage;
stageSel.onchange = () => { state.stage = stageSel.value; saveState(); rerender(); };

const winSel = $("#f-window");
winSel.value = state.window;
winSel.onchange = () => { state.window = winSel.value; saveState(); rerender(); };

const insiderSel = $("#f-insider");
insiderSel.value = state.insider;
insiderSel.onchange = () => { state.insider = insiderSel.value; saveState(); rerender(); };

const searchEl = $("#f-search");
searchEl.value = state.search;
searchEl.oninput = () => { state.search = searchEl.value; saveState(); rerender(); };

$("#reset-filters").onclick = () => {
  state.lanes = new Set(DATA.lane_order);
  state.tiers = new Set(DATA.tier_order);
  state.stage = ""; state.window = "all"; state.insider = "all"; state.search = "";
  stageSel.value = ""; winSel.value = "all"; insiderSel.value = "all"; searchEl.value = "";
  renderPills("#lane-pills", "Lane", DATA.lane_order, DATA.lane_color,
              DATA.lane_label, state.lanes);
  renderPills("#tier-pills", "Tier", DATA.tier_order, TIER_COLOR, TIER_LABEL, state.tiers);
  saveState(); rerender();
};

// ---- table -------------------------------------------------------------
const COLS = [
  { key: "ticker",            label: "Ticker", num: false },
  { key: "drug",              label: "Drug",   num: false },
  { key: "stage",             label: "Stage",  num: false },
  { key: "source_lane",       label: "Lane",   num: false },
  { key: "precision_tier",    label: "Tier",   num: false },
  { key: "date_min",          label: "date_min", num: true },
  { key: "date_max",          label: "date_max", num: true },
  { key: "days_to_min",       label: "Δ days",   num: true },
  { key: "insider_exec_buys", label: "Exec buys", num: true },
  { key: "insider_all_buys",  label: "All buys",  num: true },
  { key: "matched_phrase",    label: "matched phrase", num: false },
];

const thRow = $("#thead-row");
COLS.forEach(c => {
  const th = document.createElement("th");
  th.dataset.key = c.key;
  th.innerHTML = `${escapeHtml(c.label)}<span class="arrow"></span>`;
  th.onclick = () => {
    if (state.sortKey === c.key) state.sortDir *= -1;
    else { state.sortKey = c.key; state.sortDir = 1; }
    saveState(); rerender();
  };
  thRow.appendChild(th);
});

function inWindow(r, kind) {
  const ref = new Date(DATA.snapshot_date);
  const t14 = new Date(ref); t14.setDate(ref.getDate() + 14);
  const t60 = new Date(ref); t60.setDate(ref.getDate() + 60);
  const t180 = new Date(ref); t180.setDate(ref.getDate() + 180);
  if (kind === "all") return true;
  if (kind === "past") {
    if (r.precision_tier === "unknown") return true;
    if (!r.date_max) return true;
    return new Date(r.date_max) < t14;
  }
  if (!r.date_min || !r.date_max || r.precision_tier === "unknown") return false;
  const dmin = new Date(r.date_min), dmax = new Date(r.date_max);
  if (kind === "discovery") return dmin <= t180 && dmax >= t14;
  if (kind === "execution") return dmin <= t60  && dmax >= t14;
  return true;
}

function rerender() {
  // arrows
  document.querySelectorAll("thead th").forEach(th => {
    const arrow = th.querySelector(".arrow");
    arrow.textContent = th.dataset.key === state.sortKey
      ? (state.sortDir > 0 ? "▲" : "▼") : "";
  });

  // filter
  const q = state.search.trim().toLowerCase();
  const filtered = DATA.rows.filter(r => {
    if (!state.lanes.has(r.source_lane)) return false;
    if (!state.tiers.has(r.precision_tier)) return false;
    if (state.stage && r.stage !== state.stage) return false;
    if (!inWindow(r, state.window)) return false;
    if (state.insider === "exec" && (r.insider_exec_buys || 0) === 0) return false;
    if (state.insider === "any"  && (r.insider_all_buys || 0) === 0) return false;
    if (q) {
      const hay = (r.ticker + " " + r.drug + " " + r.name + " " + r.indication).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  // sort — when ascending date_min/days_to_min, push past entries to the end
  // so the most-imminent-future row is on top by default.
  const key = state.sortKey, dir = state.sortDir;
  const pushPastLast = (key === "date_min" || key === "days_to_min") && dir > 0;
  filtered.sort((a, b) => {
    if (pushPastLast) {
      const aPast = a.days_to_min != null && a.days_to_min < 0;
      const bPast = b.days_to_min != null && b.days_to_min < 0;
      if (aPast && !bPast) return 1;
      if (!aPast && bPast) return -1;
    }
    const va = a[key], vb = b[key];
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (va < vb) return -1 * dir;
    if (va > vb) return  1 * dir;
    return 0;
  });

  $("#footer-count").textContent =
    `Showing ${filtered.length} of ${DATA.rows.length} rows`;

  const tbody = $("#tbody");
  tbody.innerHTML = filtered.map((r, i) => {
    const laneColor = DATA.lane_color[r.source_lane] || "#64748b";
    const dDays = r.days_to_min;
    const dDaysCls = dDays == null ? "" :
                     dDays < 0 ? "past" :
                     dDays < 14 ? "imminent" : "";
    const dDaysText = dDays == null ? "—"
                    : (dDays >= 0 ? "+" : "") + dDays;
    // Insider activity cell — most informative tag wins
    const exec = r.insider_exec_buys || 0;
    const all  = r.insider_all_buys  || 0;
    const dirOnly = r.insider_director_buys || 0;
    let execCell = '<span class="insider-badge none">—</span>';
    if (exec > 0) {
      execCell = `<span class="insider-badge exec">${exec}</span>`;
    } else if (dirOnly > 0) {
      execCell = `<span class="insider-badge director">${dirOnly}d</span>`;
    }
    const allCell = all > 0
      ? `<span class="insider-badge any">${all}</span>`
      : '<span class="insider-badge none">—</span>';
    return `
      <tr class="row" data-idx="${i}">
        <td>${escapeHtml(r.ticker)}</td>
        <td>${escapeHtml(r.drug)}</td>
        <td>${escapeHtml(r.stage)}</td>
        <td><span class="tag" style="background:${laneColor}">${escapeHtml(DATA.lane_label[r.source_lane])}</span></td>
        <td><span class="ttype">${escapeHtml(r.precision_tier)}</span></td>
        <td class="num">${fmtDate(r.date_min)}</td>
        <td class="num">${fmtDate(r.date_max)}</td>
        <td class="num ${dDaysCls}">${dDaysText}</td>
        <td class="num">${execCell}</td>
        <td class="num">${allCell}</td>
        <td>${escapeHtml(r.matched_phrase)}</td>
      </tr>
    `;
  }).join("");

  // wire row expand
  tbody.querySelectorAll("tr.row").forEach(tr => {
    tr.onclick = () => {
      const next = tr.nextElementSibling;
      if (next && next.classList.contains("detail")) {
        next.remove(); tr.classList.remove("exp"); return;
      }
      const r = filtered[+tr.dataset.idx];
      const detail = buildDetail(r);
      tr.classList.add("exp");
      tr.insertAdjacentHTML("afterend", detail);
    };
  });
}

function buildDetail(r) {
  const text = escapeHtml(r.catalyst_text || "(empty)");
  const matched = r.matched_phrase;
  let highlighted = text;
  if (matched) {
    const re = new RegExp(escapeRegex(escapeHtml(matched)), "i");
    highlighted = text.replace(re, m => `<mark>${m}</mark>`);
  }
  const conf = r.conference ? `<div class="k">Conference</div><div class="v">${escapeHtml(r.conference)}</div>` : "";
  const ncts = r.nct_number ? `<div class="k">NCT</div><div class="v">${escapeHtml(r.nct_number)}</div>` : "";
  const ind  = r.indication ? `<div class="k">Indication</div><div class="v">${escapeHtml(r.indication)}</div>` : "";
  const sent = r.sentiment ? `<div class="k">Sentiment</div><div class="v">${escapeHtml(r.sentiment)}</div>` : "";
  const mcap = r.market_cap_usd != null ? `<div class="k">Market cap</div><div class="v">$${fmtMcap(r.market_cap_usd)}</div>` : "";
  const price = r.price != null ? `<div class="k">Price</div><div class="v">$${r.price}</div>` : "";

  // Recent insider trades (last INSIDER_WINDOW_DAYS days from snapshot).
  let insiderPanel = "";
  const trades = r.insider_recent_trades || [];
  if (trades.length) {
    const gross = r.insider_gross_usd || 0;
    const grossLabel = gross > 0 ? ` (gross ≈ $${fmtMcap(gross)})` : "";
    const rows = trades.map(t => {
      const role = t.executive_role || "Other";
      const roleCls = DATA.executive_roles.includes(role) ? "exec" :
                      role === "Director" ? "dir" : "";
      const pos = t.insider_position || "—";
      const px  = t.trade_price != null ? "$" + Number(t.trade_price).toFixed(2) : "—";
      const sh  = t.shares != null ? Math.round(Number(t.shares)).toLocaleString() : "—";
      const grs = t.gross_usd != null ? "$" + fmtMcap(t.gross_usd) : "—";
      return `<tr>
        <td><span class="trade-source">${t.source}</span></td>
        <td>${t.date || "—"}</td>
        <td><span class="role-tag ${roleCls}">${escapeHtml(role)}</span></td>
        <td>${escapeHtml(t.insider_name)}</td>
        <td title="${escapeHtml(pos)}">${escapeHtml(pos.length > 30 ? pos.slice(0,30)+"…" : pos)}</td>
        <td class="num">${sh}</td>
        <td class="num">${px}</td>
        <td class="num">${grs}</td>
      </tr>`;
    }).join("");
    insiderPanel = `
      <div class="k">Recent insider buys (last ${DATA.insider_window_days}d)</div>
      <div class="v">
        ${trades.length} buy(s) total, ${r.insider_exec_buys || 0} from C-suite/Chair, ${r.insider_director_buys || 0} from directors${grossLabel}.
        <div class="recent-trades"><table>
          <thead><tr>
            <th>src</th><th>date</th><th>role</th><th>insider</th><th>position</th>
            <th>shares</th><th>price</th><th>gross</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
      </div>`;
  } else if ((r.insider_all_buys || 0) === 0) {
    insiderPanel = `
      <div class="k">Recent insider buys (last ${DATA.insider_window_days}d)</div>
      <div class="v" style="color:var(--text-faint)">none</div>`;
  }
  return `
    <tr class="detail">
      <td colspan="11">
        <div class="dgrid">
          <div class="k">Catalyst text</div>
          <div class="v">${highlighted}</div>
          <div class="k">BPC catalyst_date</div>
          <div class="v">${escapeHtml(r.catalyst_date_raw)}</div>
          <div class="k">Next catalyst type</div>
          <div class="v">${escapeHtml(r.next_catalyst_type)}</div>
          ${conf}${ncts}${ind}${sent}${mcap}${price}
          ${insiderPanel}
        </div>
      </td>
    </tr>`;
}

rerender();
</script>
</body>
</html>
"""


def render(
    snapshot_date: date | None = None,
    out_dir: Path = OUTPUT_DIR,
    force_template: bool = False,
) -> tuple[Path, Path, str, dict]:
    """Write the sidecar data file (always) and the HTML template
    (only when version-mismatched or missing).

    Default mode (``snapshot_date=None``) is rolling: latest timing per
    catalyst across all snapshots, materialization-filtered. Pass an
    explicit date for legacy single-snapshot render.

    Returns ``(html_path, data_path, template_action, summary)``.
    """
    conn = get_connection()
    try:
        rows, query_meta = _fetch_rows(conn, snap=snapshot_date)
        if not rows:
            raise RuntimeError(
                "no catalyst_timing rows — run Module 5 first"
                if snapshot_date is None
                else f"no catalyst_timing rows at snapshot {snapshot_date.isoformat()}"
            )

        # Anchor everything that needs a "today" on effective_today (or the
        # explicit snapshot for single-mode). This is what JS uses for
        # window arithmetic too.
        anchor_iso = query_meta["effective_today"] or (
            snapshot_date.isoformat() if snapshot_date else None
        )
        anchor_date = date.fromisoformat(anchor_iso)

        distinct_tickers = sorted({r["ticker"] for r in rows})
        insider_summary, recent_trades = _fetch_insider_activity(
            conn, anchor_date, distinct_tickers,
        )

        summary = _build_summary(rows, anchor_date)
        months = _build_monthly_histogram(rows, anchor_date)
        rows_json = [
            _row_for_json(r, anchor_date, insider_summary, recent_trades)
            for r in rows
        ]

        catalysts_with_insider_buys = sum(
            1 for r in rows_json if r["insider_all_buys"] > 0
        )
        catalysts_with_exec_buys = sum(
            1 for r in rows_json if r["insider_exec_buys"] > 0
        )
        tickers_with_exec_buys = sum(
            1 for t in distinct_tickers
            if insider_summary.get(t, {}).get("exec_buys", 0) > 0
        )
        summary["insider"] = {
            "catalysts_with_any_insider_buys": catalysts_with_insider_buys,
            "catalysts_with_exec_buys": catalysts_with_exec_buys,
            "tickers_with_exec_buys": tickers_with_exec_buys,
            "window_days": INSIDER_WINDOW_DAYS,
        }

        rules_version = rows[0]["rules_version"] or ""
        computed_at = rows[0]["computed_at"] or ""

        payload = _build_payload(
            anchor_date=anchor_date,
            query_meta=query_meta,
            summary=summary,
            months=months,
            rows_json=rows_json,
            rules_version=rules_version,
            computed_at=computed_at,
            db_path=DEFAULT_DB_PATH,
        )
    finally:
        conn.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    data_path = out_dir / DATA_PATH.name
    html_path = out_dir / OUTPUT_PATH.name

    data_path.write_text(_build_data_js(payload), encoding="utf-8")

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

    return html_path, data_path, action, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-date", type=date.fromisoformat, default=None,
        help="ISO snapshot date for legacy single-snapshot render "
             "(default: rolling view across all snapshots)",
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

    html_path, data_path, action, summary = render(
        snapshot_date=args.snapshot_date,
        out_dir=args.out_dir,
        force_template=args.rebuild_template,
    )
    data_kb = data_path.stat().st_size / 1024
    html_kb = html_path.stat().st_size / 1024
    print(f"[3_5_render_timings] data:     {data_path}  ({data_kb:.1f} KB) — refreshed")
    print(f"[3_5_render_timings] template: {html_path}  ({html_kb:.1f} KB) — {action}")
    print(f"  total rows:               {summary['total']}")
    print(f"  discovery (T+14..T+180):  {summary['windows']['discovery']}")
    print(f"  execution (T+14..T+60):   {summary['windows']['execution']}")
    print(f"  past or unknown:          {summary['windows']['past_or_unknown']}")
    print(f"  catalysts with any insider buys (last {INSIDER_WINDOW_DAYS}d): "
          f"{summary['insider']['catalysts_with_any_insider_buys']}")
    print(f"  catalysts with C-suite/Chair buys (last {INSIDER_WINDOW_DAYS}d): "
          f"{summary['insider']['catalysts_with_exec_buys']}")
    print(f"  tickers with C-suite/Chair buys (last {INSIDER_WINDOW_DAYS}d): "
          f"{summary['insider']['tickers_with_exec_buys']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
