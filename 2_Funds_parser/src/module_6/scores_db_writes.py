"""Write helpers for llm_scores.db.

Kept separate from scores_db.py so the schema-init / read helpers stay tight
and stable. All writers here run inside the caller's transaction (no implicit
commit) — the caller wraps a single Module 6 dispatch in one transaction so
either everything lands or nothing does.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .parsing import ParsedFullScore
from .scoring import TickerScore


def open_run(
    conn: sqlite3.Connection,
    *,
    quarter: str,
    prompt_version: str,
    model: str,
    mode: str,
    feed_size: int,
    tier_a_count: int,
    tier_b_count: int,
    tier_c_count: int,
    gate_config: dict,
    batch_id: Optional[str] = None,
) -> int:
    """Insert an llm_runs row at start of dispatch and return its run_id."""
    cur = conn.execute(
        """
        INSERT INTO llm_runs(
            quarter, prompt_version, model, mode, batch_id,
            gate_config_json, feed_size,
            tier_a_count, tier_b_count, tier_c_count,
            started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quarter, prompt_version, model, mode, batch_id,
            json.dumps(gate_config, default=str),
            feed_size, tier_a_count, tier_b_count, tier_c_count,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    return int(cur.lastrowid)


def close_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    wall_time_s: float,
    input_tokens_total: int,
    output_tokens_total: int,
    cache_read_tokens_total: int,
    cache_creation_tokens_total: int,
    web_search_calls_total: int,
    usd_cost_total: float,
    usd_cost_list_price: float,
) -> None:
    """Stamp the finished_at + token + cost totals on an open llm_runs row."""
    conn.execute(
        """
        UPDATE llm_runs SET
            wall_time_s = ?,
            input_tokens_total = ?,
            output_tokens_total = ?,
            cache_read_tokens_total = ?,
            cache_creation_tokens_total = ?,
            web_search_calls_total = ?,
            usd_cost_total = ?,
            usd_cost_list_price = ?,
            finished_at = ?
        WHERE run_id = ?
        """,
        (
            wall_time_s, input_tokens_total, output_tokens_total,
            cache_read_tokens_total, cache_creation_tokens_total,
            web_search_calls_total, usd_cost_total, usd_cost_list_price,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            run_id,
        ),
    )


def write_full_score_rows(
    conn: sqlite3.Connection,
    *,
    parsed: ParsedFullScore,
    score: TickerScore,
    quarter: str,
    prompt_version: str,
    model: str,
    run_id: int,
    raw_text: str,
    response_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    web_search_calls: int,
    usd_cost: float,
    pack_source_rank_hash: str,
    source_tier: str = "C",
    refreshed_from_row_id: Optional[int] = None,
) -> None:
    """Write two rows (one per horizon) for a Tier C full-scoring response.

    Per-ticker fields (entry ranges, financials, rNPV, scores) are duplicated
    across both horizon rows for query simplicity (per spec).
    """
    rb = parsed.research_brief
    fin = rb.get("financials", {}) or {}
    epr = parsed.entry_price_ranges
    research_brief_json = json.dumps(rb, ensure_ascii=False)

    # Lifted scalars for indexed SQL filtering
    moat_score = (rb.get("moat") or {}).get("score")
    tech_score = (rb.get("technology") or {}).get("uniqueness_score")
    acq_score = (rb.get("acquisition_target") or {}).get("score")
    mgmt_score = (rb.get("mgmt_track_record_score") or {}).get("score")
    lead_indication = (rb.get("fda") or {}).get("lead_indication")
    rnpv_total = rb.get("rnpv_total_usd")
    rnpv_per_share = rb.get("rnpv_per_share_usd")

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for horizon, hblock, hscore in (
        ("3mo", parsed.near_term_3mo, score.near_term_3mo),
        ("12mo", parsed.long_term_12mo, score.long_term_12mo),
    ):
        conn.execute(
            """
            INSERT INTO llm_scores(
                ticker, quarter, horizon, prompt_version, model, run_id,
                target_price_usd, time_to_catalyst_weeks, probability,
                catalyst_type, catalyst_detail, thesis_summary, key_risks_json,
                current_price_at_scoring_usd,
                appreciation_from_current_pct,
                appreciation_from_fair_pct, appreciation_from_full_reward_pct,
                score_at_current_pct_per_month,
                score_at_fair_pct_per_month, score_at_full_reward_pct_per_month,
                fair_entry_low_usd, fair_entry_high_usd, fair_entry_rationale,
                full_reward_low_usd, full_reward_high_usd, full_reward_rationale,
                fully_diluted_shares_count, prefunded_warrants_count,
                cash_and_equivalents_usd, runway_months,
                rnpv_total_usd, rnpv_per_share_usd,
                moat_score, technology_uniqueness_score,
                acquisition_target_score, mgmt_track_record_score,
                lead_indication, research_brief_json,
                raw_text, response_id,
                input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens,
                web_search_calls, usd_cost,
                source_tier, pack_source_rank_hash, refreshed_from_row_id,
                scored_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?,
                ?,
                ?, ?,
                ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?
            )
            ON CONFLICT(ticker, quarter, horizon, prompt_version, model)
            DO UPDATE SET
                run_id = excluded.run_id,
                target_price_usd = excluded.target_price_usd,
                time_to_catalyst_weeks = excluded.time_to_catalyst_weeks,
                probability = excluded.probability,
                catalyst_type = excluded.catalyst_type,
                catalyst_detail = excluded.catalyst_detail,
                thesis_summary = excluded.thesis_summary,
                key_risks_json = excluded.key_risks_json,
                current_price_at_scoring_usd = excluded.current_price_at_scoring_usd,
                appreciation_from_current_pct = excluded.appreciation_from_current_pct,
                appreciation_from_fair_pct = excluded.appreciation_from_fair_pct,
                appreciation_from_full_reward_pct = excluded.appreciation_from_full_reward_pct,
                score_at_current_pct_per_month = excluded.score_at_current_pct_per_month,
                score_at_fair_pct_per_month = excluded.score_at_fair_pct_per_month,
                score_at_full_reward_pct_per_month = excluded.score_at_full_reward_pct_per_month,
                fair_entry_low_usd = excluded.fair_entry_low_usd,
                fair_entry_high_usd = excluded.fair_entry_high_usd,
                fair_entry_rationale = excluded.fair_entry_rationale,
                full_reward_low_usd = excluded.full_reward_low_usd,
                full_reward_high_usd = excluded.full_reward_high_usd,
                full_reward_rationale = excluded.full_reward_rationale,
                fully_diluted_shares_count = excluded.fully_diluted_shares_count,
                prefunded_warrants_count = excluded.prefunded_warrants_count,
                cash_and_equivalents_usd = excluded.cash_and_equivalents_usd,
                runway_months = excluded.runway_months,
                rnpv_total_usd = excluded.rnpv_total_usd,
                rnpv_per_share_usd = excluded.rnpv_per_share_usd,
                moat_score = excluded.moat_score,
                technology_uniqueness_score = excluded.technology_uniqueness_score,
                acquisition_target_score = excluded.acquisition_target_score,
                mgmt_track_record_score = excluded.mgmt_track_record_score,
                lead_indication = excluded.lead_indication,
                research_brief_json = excluded.research_brief_json,
                raw_text = excluded.raw_text,
                response_id = excluded.response_id,
                input_tokens = excluded.input_tokens,
                output_tokens = excluded.output_tokens,
                cache_read_tokens = excluded.cache_read_tokens,
                cache_creation_tokens = excluded.cache_creation_tokens,
                web_search_calls = excluded.web_search_calls,
                usd_cost = excluded.usd_cost,
                source_tier = excluded.source_tier,
                pack_source_rank_hash = excluded.pack_source_rank_hash,
                refreshed_from_row_id = excluded.refreshed_from_row_id,
                scored_at = excluded.scored_at
            """,
            (
                parsed.ticker, quarter, horizon, prompt_version, model, run_id,
                float(hblock["target_price_usd"]),
                int(hblock["time_to_catalyst_weeks"]),
                float(hblock["probability"]),
                hblock.get("catalyst_type"),
                hblock.get("catalyst_detail"),
                hblock.get("thesis_summary"),
                json.dumps(hblock.get("key_risks", []), ensure_ascii=False),
                score.current_price_usd,
                hscore.appreciation_from_current_pct,
                hscore.appreciation_from_fair_pct,
                hscore.appreciation_from_full_reward_pct,
                hscore.score_at_current_pct_per_month,
                hscore.score_at_fair_pct_per_month,
                hscore.score_at_full_reward_pct_per_month,
                float(epr["fair_entry_low_usd"]),
                float(epr["fair_entry_high_usd"]),
                epr.get("fair_entry_rationale"),
                float(epr["full_reward_low_usd"]),
                float(epr["full_reward_high_usd"]),
                epr.get("full_reward_rationale"),
                float(fin.get("fully_diluted_shares_count") or 0),
                float(fin.get("prefunded_warrants_count") or 0),
                float(fin.get("cash_and_equivalents_usd") or 0),
                float(fin.get("runway_months") or 0),
                float(rnpv_total) if rnpv_total is not None else None,
                float(rnpv_per_share) if rnpv_per_share is not None else None,
                float(moat_score) if moat_score is not None else None,
                float(tech_score) if tech_score is not None else None,
                float(acq_score) if acq_score is not None else None,
                float(mgmt_score) if mgmt_score is not None else None,
                lead_indication,
                research_brief_json,
                raw_text, response_id,
                input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens,
                web_search_calls, usd_cost,
                source_tier, pack_source_rank_hash, refreshed_from_row_id,
                now_iso,
            ),
        )


def write_error_row(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    ticker: str,
    quarter: str,
    horizon: Optional[str],
    error_kind: str,
    error_detail: str,
    raw_text: str,
) -> None:
    conn.execute(
        """
        INSERT INTO llm_errors(run_id, ticker, quarter, horizon,
                               error_kind, error_detail, raw_text, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, ticker, quarter, horizon, error_kind, error_detail, raw_text,
         datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def upsert_web_search_cache_row(
    conn: sqlite3.Connection,
    *,
    url: str,
    ticker: str,
    quarter: str,
    run_id: int,
    search_query: str,
    title: str,
    content: str,
    domain: str,
    published_date: Optional[str] = None,
) -> None:
    """Insert-or-update a web_search_cache row (D36)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO web_search_cache(
            url, ticker, quarter, run_id, search_query, title, content,
            content_length, domain, published_date,
            first_seen_date, last_seen_date, seen_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(url) DO UPDATE SET
            last_seen_date = excluded.last_seen_date,
            seen_count = web_search_cache.seen_count + 1
        """,
        (
            url, ticker, quarter, run_id, search_query, title, content,
            len(content or ""), domain, published_date,
            now, now,
        ),
    )


def write_final_rankings(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    quarter: str,
    rows: list[dict],
) -> None:
    """Replace prior final_rankings rows for this (quarter, run_id) and insert new.

    Each entry in `rows` is a dict with the projection produced by reports.py.
    """
    conn.execute(
        "DELETE FROM final_rankings WHERE quarter = ? AND run_id = ?",
        (quarter, run_id),
    )
    cols = (
        "ticker", "quarter", "run_id", "final_rank", "final_horizon", "final_score",
        # D46 — current-price columns first; ranking key is final_score (= score_at_current at final_horizon)
        "current_price_at_scoring_usd",
        "score_at_current_3mo", "score_at_current_12mo",
        "score_at_fair_3mo", "score_at_fair_12mo",
        "score_at_full_reward_3mo", "score_at_full_reward_12mo",
        "target_price_3mo_usd", "target_price_12mo_usd",
        "appreciation_from_current_3mo_pct", "appreciation_from_current_12mo_pct",
        "appreciation_from_fair_3mo_pct", "appreciation_from_fair_12mo_pct",
        "current_vs_fair_mid_pct",
        "time_to_catalyst_3mo_weeks", "time_to_catalyst_12mo_weeks",
        "probability_3mo", "probability_12mo",
        "fair_entry_low_usd", "fair_entry_high_usd",
        "full_reward_low_usd", "full_reward_high_usd",
        "fully_diluted_shares_count", "rnpv_per_share_usd",
        "moat_score", "fda_pos_adjusted_lead",
        "archetype", "composite_best", "fund_count", "market_cap_usd", "industry",
    )
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT INTO final_rankings({','.join(cols)}) VALUES ({placeholders})"
    for r in rows:
        conn.execute(sql, tuple(r.get(c) for c in cols))
