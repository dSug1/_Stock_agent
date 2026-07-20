"""Module 8 — export the live universe (US/Canada/Europe/Nordic) to Excel (zero-spend, no API calls).

One row per live entity in the requested markets: ticker, company name, jurisdiction/region, market cap,
SIC-derived sector, and a best-effort "what they work on" column assembled ONLY from data already sitting
in the local store (no Claude call, no new web/API fetch):
  1. Claude's `mechanism_summary` from `score.json`, if this entity was already scored (reused, not re-run).
  2. Otherwise, up to 3 ClinicalTrials.gov trial titles (from the free CT.gov signal already ingested).
  3. Otherwise, the SIC-derived sector class (therapeutics/devices/diagnostics/tools_platform) alone.
  4. If none of the above exist locally, the cell is left blank — filling it would require a new API call.

Run:  PYTHONPATH=src ..\\.venv\\Scripts\\python.exe scripts\\8_export_universe_excel.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

import pandas as pd

from early_detection.config import COMPONENT_ROOT, load_config
from early_detection.store import Store

_OUT = COMPONENT_ROOT / "Outputs" / "biotech_universe.xlsx"

_NORDIC = {"DK", "SE", "FI", "NO"}
_EUROPE = {"DE", "FR", "UK", "CH", "NL", "BE", "IT", "ES", "IE"}
_REGION_OF = {"US": "United States", "CA": "Canada"}
for _c in _NORDIC:
    _REGION_OF[_c] = "Nordic"
for _c in _EUROPE:
    _REGION_OF[_c] = "Europe"

_MARKETS = {"US", "CA"} | _NORDIC | _EUROPE


def _primary_listing_ticker(cur, entity_id: str) -> tuple[str | None, str | None]:
    row = cur.execute(
        "SELECT ticker, exchange FROM listing WHERE entity_id = ? AND is_primary = 1 "
        "AND ticker IS NOT NULL AND ticker != '' LIMIT 1",
        (entity_id,),
    ).fetchone()
    if row is None:
        row = cur.execute(
            "SELECT ticker, exchange FROM listing WHERE entity_id = ? "
            "AND ticker IS NOT NULL AND ticker != '' LIMIT 1",
            (entity_id,),
        ).fetchone()
    if row is None:
        return None, None
    return row["ticker"], row["exchange"]


def _mechanism_summary(cur, entity_id: str) -> str | None:
    row = cur.execute(
        "SELECT json FROM score WHERE entity_id = ? ORDER BY scored_at DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["json"])
    except (json.JSONDecodeError, TypeError):
        return None
    summary = data.get("mechanism_summary")
    return summary.strip() if isinstance(summary, str) and summary.strip() else None


def _clinical_titles(cur, entity_id: str, limit: int = 3) -> str | None:
    rows = cur.execute(
        "SELECT raw_payload_json FROM signal WHERE entity_id = ? AND signal_type = 'clinical_trial' "
        "ORDER BY detected_at DESC LIMIT ?",
        (entity_id, limit),
    ).fetchall()
    if not rows:
        return None
    titles: list[str] = []
    for r in rows:
        try:
            payload = json.loads(r["raw_payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        title = payload.get("title")
        if title and title not in titles:
            titles.append(title.strip())
    return " | ".join(titles) if titles else None


def main() -> None:
    cfg = load_config()
    store = Store(cfg.db_path)
    cur = store.conn.cursor()

    entities = cur.execute(
        "SELECT entity_id, legal_name, common_name, ticker_primary, exchange_primary, "
        "jurisdiction, market_cap_usd, mktcap_ccy, mktcap_unknown, below_floor, above_ceiling, "
        "sector_code_raw, sector_code_normalized, in_existing_universe "
        "FROM entity WHERE is_live = 1"
    ).fetchall()

    rows = []
    for e in entities:
        juris = e["jurisdiction"]
        if juris not in _MARKETS:
            continue

        ticker, exchange = e["ticker_primary"], e["exchange_primary"]
        if not ticker:
            ticker, exchange = _primary_listing_ticker(cur, e["entity_id"])

        focus = _mechanism_summary(cur, e["entity_id"])
        focus_source = "Claude score (already computed)" if focus else None
        if not focus:
            focus = _clinical_titles(cur, e["entity_id"])
            focus_source = "ClinicalTrials.gov trial title(s)" if focus else None
        if not focus:
            focus = e["sector_code_normalized"]
            focus_source = "SIC sector class only" if focus else None

        rows.append(
            {
                "Ticker": ticker,
                "Exchange": exchange,
                "Company": e["common_name"] or e["legal_name"],
                "Legal Name": e["legal_name"],
                "Region": _REGION_OF.get(juris, juris),
                "Jurisdiction": juris,
                "Market Cap (USD)": e["market_cap_usd"],
                "Market Cap Unknown": bool(e["mktcap_unknown"]),
                "Below $10M Floor": bool(e["below_floor"]),
                "Above $3B Ceiling": bool(e["above_ceiling"]),
                "SIC Sector": e["sector_code_normalized"],
                "SIC Code": e["sector_code_raw"],
                "What They Work On": focus,
                "Focus Source": focus_source,
                "Entity ID": e["entity_id"],
            }
        )

    df = pd.DataFrame(rows).sort_values(["Region", "Company"], na_position="last")

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(_OUT, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Universe")
        for region in ["United States", "Canada", "Europe", "Nordic"]:
            sub = df[df["Region"] == region]
            if not sub.empty:
                sub.to_excel(writer, index=False, sheet_name=region[:31])

        for sheet_name, sheet_df in [("Universe", df)] + [
            (r[:31], df[df["Region"] == r]) for r in ["United States", "Canada", "Europe", "Nordic"]
        ]:
            if sheet_df.empty:
                continue
            ws = writer.sheets[sheet_name]
            for i, col in enumerate(sheet_df.columns, start=1):
                width = min(60, max(10, sheet_df[col].astype(str).str.len().quantile(0.9) + 2))
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width

    n_focus = df["Focus Source"].notna().sum()
    print(f"Wrote {len(df)} entities to {_OUT}")
    print(f"  by region: {df['Region'].value_counts().to_dict()}")
    print(f"  'What They Work On' populated for {n_focus}/{len(df)} "
          f"({df['Focus Source'].value_counts().to_dict()})")


if __name__ == "__main__":
    main()
