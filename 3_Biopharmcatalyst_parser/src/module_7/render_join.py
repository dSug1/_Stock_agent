"""Module 7 — render-time join helper.

Loads the latest deep_dives row per catalyst PK from
`data/claude_deep_dives.db` and returns a `{pk_tuple: deep_dive_dict}`
keyed map for consumption by `scripts/3_6_render_scores.py`.

The renderer LEFT-JOINs this onto its existing rows so:
  • Rows with a matching deep_dive get three new columns + the expanded-
    row deep-dive block.
  • Rows without a matching deep_dive show `—` in those columns.

`raw_text` is intentionally stripped from the sidecar payload — it
sits at 30-80 KB per row and would bloat the .js file 10-30×. The
selection HTTP server (`3_7_serve_selection.py`) serves it on demand.

Spec: spec/module_7_spec.md §5.11.3.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DD_PATH = PROJECT_ROOT / "data" / "claude_deep_dives.db"


def _maybe_json(text: Optional[str]) -> Any:
    if text is None or text == "":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def fetch_latest_deep_dive_map(
    db_path: Path | str | None = None,
    *,
    only_snapshot_date: Optional[str] = None,
) -> dict[tuple, dict]:
    """Return ``{(ticker, drug, nct, type): deep_dive_payload}``.

    D38 (2026-06-04 bug fix): key is now the **catalyst PK without
    snapshot_date**. Previously we keyed on the full 5-tuple including
    snapshot_date, which meant that when M6 ingested a new BPC docx and
    the rolling-view rolled a catalyst forward to a newer snapshot, its
    prior deep_dive became invisible — same ticker/drug/nct/type, but
    a different snapshot_date didn't match. Per catalyst PK we keep the
    deep_dive whose `run_id` is highest (i.e. the most recent dispatch
    across all snapshots for that catalyst).

    The payload is shaped to match the spec's `_data.js::row.deep_dive`
    contract (no raw_text — fetched lazily by the local HTTP server).
    """
    path = Path(db_path) if db_path else DEFAULT_DD_PATH
    if not path.exists():
        return {}

    # ATTACH-free read since we own the file; open with row_factory.
    cx = sqlite3.connect(str(path))
    cx.row_factory = sqlite3.Row
    try:
        sql = """
        SELECT d.* FROM deep_dives d
        JOIN (
            SELECT ticker, drug, nct_number, next_catalyst_type,
                   MAX(run_id) AS max_run
            FROM deep_dives
        """
        params: list = []
        if only_snapshot_date:
            sql += " WHERE snapshot_date = ? "
            params.append(only_snapshot_date)
        sql += """
            GROUP BY ticker, drug, nct_number, next_catalyst_type
        ) latest ON
            latest.ticker = d.ticker AND
            latest.drug = d.drug AND
            latest.nct_number = d.nct_number AND
            latest.next_catalyst_type = d.next_catalyst_type AND
            latest.max_run = d.run_id
        """
        rows = cx.execute(sql, params).fetchall()
    finally:
        cx.close()

    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r["ticker"], r["drug"], r["nct_number"], r["next_catalyst_type"])
        out[key] = {
            "run_id":              r["run_id"],
            "run_completed_at":    r["created_at"],
            "prompt_version":      r["prompt_version"],
            "model":               r["model"],
            "response_id":         r["response_id"],
            "raw_text_id":         r["run_id"],         # surrogate for HTTP fetch endpoint
            # Claude scalars
            "p_clinical":          r["p_clinical"],
            "p_clinical_low":      r["p_clinical_low"],
            "p_clinical_high":     r["p_clinical_high"],
            "expected_move_on_hit_pct":  r["expected_move_on_hit_pct"],
            "expected_move_on_miss_pct": r["expected_move_on_miss_pct"],
            "rnpv_total_usd":      r["rnpv_total_usd"],
            "rnpv_per_share_usd":  r["rnpv_per_share_usd"],
            "lead_indication":     r["lead_indication"],
            "management_track_record_score": r["management_track_record_score"],
            "acquisition_target_score":      r["acquisition_target_score"],
            # Structured blocks (decoded so JS doesn't double-parse)
            "rnpv_by_indication":      _maybe_json(r["rnpv_by_indication_json"]),
            "drug_profile":            _maybe_json(r["drug_profile_json"]),
            "clinical_evidence":       _maybe_json(r["clinical_evidence_json"]),
            "financial_overhang":      _maybe_json(r["financial_overhang_json"]),
            "catalyst_date_sanity_check": _maybe_json(r["catalyst_date_sanity_json"]),
            "key_risks":               _maybe_json(r["key_risks_json"]) or [],
            "thesis_summary":          r["thesis_summary"],
            "reasoning_trace":         r["reasoning_trace"],
            # Python modifiers + expectancy (D33 — momentum fields dropped)
            "insider_score_input":           r["insider_score_input"],
            "fund_accumulation_score_input": r["fund_accumulation_score_input"],
            "m_insider":               r["m_insider"],
            "m_funds":                 r["m_funds"],
            "p_final":                 r["p_final"],
            "e_move_pct":              r["e_move_pct"],
            "weeks_to_catalyst_mid":   r["weeks_to_catalyst_mid"],
            "expectancy_per_week_pct": r["expectancy_per_week_pct"],
            "catalyst_signature":      r["catalyst_signature"],
            # D25 — price anchor + $ targets so JS can recompute share-
            # price-appreciation and expectancy/time from live current_price.
            "price_at_api_time_usd":    r["price_at_api_time_usd"],
            "target_price_on_hit_usd":  r["target_price_on_hit_usd"],
            "target_price_on_miss_usd": r["target_price_on_miss_usd"],
            # D35 — M8 rescue artifacts. claude_resolved_catalyst_date
            # drives the renderer's 📅 marker; catalyst_date_source
            # populates the hover tooltip + the expand-panel kv line.
            # rescue_class is also on catalyst_scores but having it on
            # the deep_dive payload keeps the Rescued tab JS simple.
            "claude_resolved_catalyst_date": r["claude_resolved_catalyst_date"]
                                              if "claude_resolved_catalyst_date" in r.keys() else None,
            "catalyst_date_source":           r["catalyst_date_source"]
                                              if "catalyst_date_source" in r.keys() else None,
            "rescue_class_dispatch":          r["rescue_class"]
                                              if "rescue_class" in r.keys() else None,
            # Audit
            "usd_cost":         r["usd_cost"],
            "tokens": {
                "input":          r["input_tokens"],
                "output":         r["output_tokens"],
                "cache_read":     r["cache_read_tokens"],
                "cache_creation": r["cache_creation_tokens"],
            },
            "web_search_calls": r["web_search_calls"],
        }
    return out


def attach_deep_dive_payload(
    rows: list[dict],
    dd_map: dict[tuple, dict],
) -> None:
    """Mutate `rows` in place: add `deep_dive` key per row (None when absent).

    D38 (2026-06-04 bug fix): match by (ticker, drug, nct, type) only —
    NOT including snapshot_date. See `fetch_latest_deep_dive_map` for
    the rationale.

    The renderer feeds the result through `json.dumps`.
    """
    for row in rows:
        key = (row.get("ticker"), row.get("drug"),
               row.get("nct_number"), row.get("next_catalyst_type"))
        row["deep_dive"] = dd_map.get(key)
