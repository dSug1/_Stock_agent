"""Release-calendar logic — decide which jury sources are DUE this week (spec §5a; D26).

Most juries publish **annually**, so re-scraping all of them weekly is wasteful and ban-risky. The
weekly run fetches a source only when **DUE** = its publication window is open AND this period's edition
isn't captured yet (per-source watermark). D26 added the windows (`config/discovery_calendar.yaml`);
this module is the logic that *consults* them + the `jury_signals` watermark and returns the due set, so
a typical week is a near no-op (annual juries skipped ~50 wks/yr; only continuous feeds + any award in
its window + the post-13F-deadline fund cross-ref actually fetch).

Pure where it matters: `is_jury_due` / `is_funds_due` take primitives so they're trivially testable;
`due_jury_sources` is the DB-backed wrapper that reads the registry + the watermark.
"""

import logging
from datetime import date

log = logging.getLogger(__name__)

# Cadences that always poll incrementally (watermark + conditional-GET do the de-dup, not the calendar).
_CONTINUOUS = {"continuous", "daily", "weekly"}


def load_calendar(path: str) -> dict:
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def captured_year(conn, source_id: str):
    """The latest edition year already ingested for a source (the watermark), or None."""
    row = conn.execute(
        "SELECT MAX(year) AS y FROM jury_signals WHERE source_id=? AND year IS NOT NULL",
        (source_id,)).fetchone()
    return row["y"] if row and row["y"] is not None else None


def is_jury_due(cadence, publish_months, *, today: date, captured_year, current_year: int) -> bool:
    """Is an award/jury source due to fetch on ``today``?

    - quarterly  -> never here (the fund cross-ref uses ``is_funds_due``).
    - annual     -> due only if this year's edition isn't captured yet AND we're in/after the publish
                    window (or the window is unknown -> check until captured).
    - continuous/daily/weekly/episodic/unknown -> always poll (cheap, watermark-deduped).
    """
    if cadence == "quarterly":
        return False
    if cadence == "annual":
        if captured_year is not None and captured_year >= current_year:
            return False                          # this year's edition already in the DB
        return (not publish_months) or today.month >= min(publish_months)
    return True


def is_funds_due(today: date, due_from_list, *, window_days: int = 21) -> bool:
    """Are specialist-fund 13F positions due? True only in the ~3 weeks after a 13F deadline
    (Feb/May/Aug/Nov), when new positions actually land."""
    for e in due_from_list or []:
        raw = e.get("due_from") or ""
        try:
            mm, dd = raw.split("-")
            due = date(today.year, int(mm), int(dd))
        except (ValueError, AttributeError):
            continue
        if 0 <= (today - due).days <= window_days:
            return True
    return False


def due_jury_sources(conn, calendar: dict, *, today: date, restrict_to=None) -> list[str]:
    """Enabled registry sources due to fetch on ``today``, per the calendar + watermark. ``restrict_to``
    (a set of source_ids) limits the result to sources discovery can actually parse."""
    current_year = today.year
    annual_pub = calendar.get("annual_publish", {}) or {}
    rows = conn.execute("SELECT source_id, cadence FROM sources WHERE enabled=1").fetchall()
    due = []
    for r in rows:
        sid = r["source_id"]
        if restrict_to is not None and sid not in restrict_to:
            continue
        if is_jury_due(r["cadence"], annual_pub.get(sid), today=today,
                       captured_year=captured_year(conn, sid), current_year=current_year):
            due.append(sid)
    return sorted(due)


def funds_due(calendar: dict, *, today: date) -> bool:
    return is_funds_due(today, calendar.get("quarterly_13f_due_from"))
