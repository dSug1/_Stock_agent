"""Labeled point-in-time panel storage (Protocol section 2).

The panel is the gating artifact for the whole program: a set of names, each at a leak-free `t0`,
with a label (positive / hard_negative / easy_negative), forward-return outcomes, and a PIT feature
snapshot for the kill-switch. v1 seeds the 7 pre-registered calibration anchors (section 2.4); a
real panel needs n>=100 PIT-reconstructed names (section 2.3) before the kill-switch is valid.
"""

import logging

from .db import now_iso

log = logging.getLogger(__name__)

_LABELS = {"positive", "hard_negative", "easy_negative"}


def upsert_panel_row(conn, row: dict) -> int:
    """Insert/update a panel name (keyed by ticker + t0_date). Returns panel_id."""
    if row.get("label") not in _LABELS:
        raise ValueError(f"{row.get('ticker')}: label must be one of {sorted(_LABELS)}")
    now = now_iso()
    conn.execute(
        """
        INSERT INTO panel (name, ticker, t0_date, regime, theme, mispricing_mode, label,
                           label_source, notes, created_at)
        VALUES (:name, :ticker, :t0_date, :regime, :theme, :mispricing_mode, :label,
                :label_source, :notes, :now)
        ON CONFLICT(ticker, t0_date) DO UPDATE SET
            name=excluded.name, regime=excluded.regime, theme=excluded.theme,
            mispricing_mode=excluded.mispricing_mode, label=excluded.label,
            label_source=excluded.label_source, notes=excluded.notes
        """,
        {"name": row.get("name"), "ticker": row["ticker"], "t0_date": row["t0_date"],
         "regime": row.get("regime"), "theme": row.get("theme"),
         "mispricing_mode": row.get("mispricing_mode"), "label": row["label"],
         "label_source": row.get("label_source", "derived"), "notes": row.get("notes"),
         "now": now},
    )
    conn.commit()
    return conn.execute(
        "SELECT panel_id FROM panel WHERE ticker=? AND t0_date=?",
        (row["ticker"], row["t0_date"]),
    ).fetchone()[0]


def seed_anchors(conn, anchors: list[dict]) -> int:
    for a in anchors:
        a = dict(a)
        a.setdefault("label_source", "pre_registered")
        upsert_panel_row(conn, a)
    return len(anchors)


def list_panel(conn):
    return conn.execute("SELECT * FROM panel ORDER BY t0_date, ticker").fetchall()


def write_returns(conn, panel_id: int, horizon_weeks: int, stats: dict):
    conn.execute(
        """
        INSERT INTO panel_returns (panel_id, horizon_weeks, start_price, end_price, fwd_return,
                                   max_drawup, max_drawdown, computed_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(panel_id, horizon_weeks) DO UPDATE SET
            start_price=excluded.start_price, end_price=excluded.end_price,
            fwd_return=excluded.fwd_return, max_drawup=excluded.max_drawup,
            max_drawdown=excluded.max_drawdown, computed_at=excluded.computed_at
        """,
        (panel_id, horizon_weeks, stats["start_price"], stats["end_price"], stats["fwd_return"],
         stats["max_drawup"], stats["max_drawdown"], now_iso()),
    )
    conn.commit()


def read_returns(conn, panel_id):
    return conn.execute(
        "SELECT horizon_weeks, fwd_return, max_drawup, max_drawdown FROM panel_returns "
        "WHERE panel_id=? ORDER BY horizon_weeks", (panel_id,)
    ).fetchall()


def set_feature(conn, panel_id, feature, value):
    conn.execute(
        "INSERT INTO panel_features (panel_id, feature, value) VALUES (?,?,?) "
        "ON CONFLICT(panel_id, feature) DO UPDATE SET value=excluded.value",
        (panel_id, feature, float(value)))
    conn.commit()


def read_features(conn, panel_id):
    return {r["feature"]: r["value"] for r in conn.execute(
        "SELECT feature, value FROM panel_features WHERE panel_id=?", (panel_id,))}
