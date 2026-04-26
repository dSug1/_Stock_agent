"""Module 5 — orchestrator.

End-to-end flow:
1. Load ranked_candidates_{quarter}.parquet + enrichment.yaml + narrative templates.
2. apply_selection() — v1 only drops denylisted tickers.
3. Bulk-load 52w price extremes from prices.db (one SQL query).
4. For each selected ticker: probe cache (D24); if miss, build pack; upsert.
5. Write exclusions parquet and HTML summary.

No network, no yfinance. Reads `data/prices.db` only. Deterministic given the
same inputs + pack_version.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from module_1 import ConfigError, PROJECT_ROOT, PipelineConfig, ensure_dir, resolve_quarter

from . import packs_db
from .fundamentals import (
    fundamentals_fetched_at_for_hash,
    load_fundamentals_for_tickers,
)
from .packs import (
    build_context_pack,
    compute_source_rank_hash,
    fetch_prices_extremes,
    load_narrative_templates,
)
from .reports import generate_enrichment_report_html
from .selection import apply_selection

log = logging.getLogger(__name__)


def load_enrichment_config(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"enrichment config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        loc = f"{path}:{mark.line + 1}:{mark.column + 1} " if mark else f"{path} "
        raise ConfigError(f"{loc}YAML syntax error: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top-level YAML must be a mapping")
    # Shallow defaults so missing sections don't crash the run.
    raw.setdefault("selection", {})
    raw.setdefault("rescue", {})
    raw.setdefault("pack", {})
    raw.setdefault("store", {})
    raw.setdefault("reports", {})
    return raw


def _expected_columns() -> list[str]:
    """Columns Module 5 reads from the ranked parquet. Fail loudly on mismatch."""
    return [
        "ticker", "name_of_issuer", "cusip", "quarter", "archetype",
        "score_3mo", "score_12mo", "match_confidence",
        "composite_3mo", "composite_12mo", "best_horizon", "composite_best",
        "R_4", "R_12", "R_26", "R_52",
        "R_4_over_R_12", "R_4_over_R_26", "R_4_over_R_52",
        "R_12_over_R_26", "R_12_over_R_52", "R_26_over_R_52",
        "price_today", "weeks_of_history_used", "young_ticker_flag",
        "fund_count", "new_positions", "increased_positions",
        "qoq_fund_count_change",
        "sector", "industry", "market_cap",
    ]


def _validate_schema(df: pd.DataFrame, parquet_path: Path) -> None:
    missing = [c for c in _expected_columns() if c not in df.columns]
    if missing:
        raise ConfigError(
            f"{parquet_path}: missing required columns {missing}. "
            f"Expected Module 4b dual-horizon schema — re-run `scripts/4_rank.py`."
        )


def run_enrichment(
    config: PipelineConfig,
    quarter: Optional[str] = None,
    *,
    force_refresh: bool = False,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Build context packs for every ranked ticker in the quarter.

    Returns a summary DataFrame: one row per processed ticker with
    (ticker, cache_status, composite_best, archetype, best_horizon).
    """
    started = time.monotonic()
    quarter = quarter or resolve_quarter(config)

    # ---- inputs
    ranked_path = (
        config.paths.intermediate_outputs_dir / f"ranked_candidates_{quarter}.parquet"
    )
    if not ranked_path.exists():
        raise FileNotFoundError(
            f"ranked candidates not found at {ranked_path}. "
            "Run scripts/4_rank.py first."
        )
    ranked = pd.read_parquet(ranked_path)
    log.info("Loaded %d ranked rows from %s", len(ranked), ranked_path)
    if ranked.empty:
        return _write_empty_outputs(quarter, ranked_path, config, started)

    _validate_schema(ranked, ranked_path)

    enrichment_cfg = load_enrichment_config(
        PROJECT_ROOT / "config" / "enrichment.yaml"
    )
    pack_cfg = enrichment_cfg["pack"]
    pack_version = str(pack_cfg.get("pack_version", "m5-v1"))

    narrative_path = PROJECT_ROOT / pack_cfg.get(
        "narrative_templates_path", "config/enrichment_narratives.yaml"
    )
    templates = load_narrative_templates(narrative_path)

    # ---- selection (v1: denylist + include_unclassified only)
    to_enrich, exclusions_df = apply_selection(ranked, enrichment_cfg["selection"])
    log.info(
        "Selection: %d -> %d (excluded: %d)",
        len(ranked), len(to_enrich), len(exclusions_df),
    )

    # ---- extremes (bulk query against prices.db)
    prices_db_path = config.paths.prices_db
    tickers = sorted(set(to_enrich["ticker"].dropna().astype(str).tolist()))
    prices_extremes: dict = {}
    if prices_db_path.exists() and tickers:
        with sqlite3.connect(prices_db_path) as pconn:
            reference_date = pd.Timestamp(dt.datetime.utcnow().date())
            prices_extremes = fetch_prices_extremes(pconn, tickers, reference_date)
    else:
        log.warning(
            "prices.db missing at %s — packs will be emitted with data_quality=partial",
            prices_db_path,
        )

    # ---- D54: fundamentals (bulk query against data/fundamentals.db)
    fundamentals_db_path = config.paths.data_dir / "fundamentals.db"
    fundamentals_by_ticker: dict[str, dict] = {}
    if fundamentals_db_path.exists() and tickers:
        fundamentals_by_ticker = load_fundamentals_for_tickers(
            fundamentals_db_path, tickers,
        )
        n_avail = sum(1 for v in fundamentals_by_ticker.values() if v.get("available"))
        log.info(
            "fundamentals: %d/%d tickers have M4c data (db=%s)",
            n_avail, len(tickers), fundamentals_db_path,
        )
    else:
        log.info(
            "fundamentals: %s missing — packs will omit the fundamentals block",
            fundamentals_db_path,
        )

    # ---- packs DB
    store_cfg = enrichment_cfg["store"]
    db_path = PROJECT_ROOT / str(store_cfg.get("db_path", "context_packs.db"))
    packs_db.init_packs_db(db_path)

    cache_enabled = bool(store_cfg.get("enable_cache", True)) and use_cache
    built_at = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    built_packs: list[dict] = []
    summary_rows: list[dict] = []
    status_counts = {"built": 0, "cache_hit": 0, "refreshed": 0}

    with sqlite3.connect(db_path) as conn:
        # Single transaction over the whole run (D24): atomicity + speed.
        for _, row in to_enrich.iterrows():
            ticker = str(row["ticker"])
            fundamentals = fundamentals_by_ticker.get(ticker)
            src_hash = compute_source_rank_hash(
                row,
                fundamentals_fingerprint=fundamentals_fetched_at_for_hash(fundamentals),
            )

            cached = None
            if cache_enabled and not force_refresh:
                cached = packs_db.probe_pack(conn, ticker, quarter, pack_version, src_hash)

            if cached is not None:
                built_packs.append(cached)
                # cheap UPDATE to tag the run's cache_status
                packs_db.mark_cache_hit(conn, ticker, quarter, pack_version)
                status_counts["cache_hit"] += 1
                status = "cache_hit"
            else:
                # Decide whether this is a fresh build or a refresh (existing row + hash mismatch).
                existing_row = conn.execute(
                    "SELECT 1 FROM context_packs "
                    "WHERE ticker = ? AND quarter = ? AND pack_version = ?",
                    (ticker, quarter, pack_version),
                ).fetchone()
                status = "refreshed" if existing_row else "built"

                try:
                    pack = build_context_pack(
                        row,
                        prices_extremes=prices_extremes,
                        templates=templates,
                        pack_config=pack_cfg,
                        source_rank_hash=src_hash,
                        built_at=built_at,
                        fundamentals=fundamentals,
                    )
                except Exception as e:
                    log.exception("Failed to build pack for %s: %s", ticker, e)
                    exclusions_df = pd.concat([
                        exclusions_df,
                        pd.DataFrame([{
                            "ticker": ticker,
                            "rank": row.get("rank"),
                            "archetype": row.get("archetype"),
                            "composite_best": row.get("composite_best"),
                            "reason": "build_error",
                            "detail": str(e)[:200],
                        }]),
                    ], ignore_index=True)
                    continue

                built_packs.append(pack)
                if cache_enabled:
                    packs_db.upsert_pack(
                        conn, pack, quarter, src_hash, status,
                        pack_version=pack_version, built_at=built_at,
                    )
                status_counts[status] += 1

            verdict = built_packs[-1]["archetype_verdict"]
            summary_rows.append({
                "ticker": ticker,
                "cache_status": status,
                "archetype": verdict.get("archetype"),
                "best_horizon": verdict.get("best_horizon"),
                "composite_best": verdict.get("composite_best"),
            })

        conn.commit()

    # ---- exclusions parquet
    exclusions_path = (
        config.paths.intermediate_outputs_dir / f"pack_exclusions_{quarter}.parquet"
    )
    ensure_dir(exclusions_path.parent)
    exclusions_df.to_parquet(exclusions_path, index=False, engine="pyarrow")

    # ---- HTML report
    reports_cfg = enrichment_cfg["reports"]
    report_path = (
        config.paths.reports_dir / f"enrichment_report_{quarter}.html"
    )
    ensure_dir(report_path.parent)
    db_size_mb = packs_db.db_size_bytes(db_path) / (1024 * 1024)
    run_stats = {
        "cache_status_counts": status_counts,
        "wall_seconds": time.monotonic() - started,
        "db_size_mb": db_size_mb,
        "pack_version": pack_version,
    }
    generate_enrichment_report_html(
        built_packs,
        exclusions_df,
        ranked_count=len(ranked),
        run_stats=run_stats,
        output_path=report_path,
        quarter=quarter,
        preview_count=int(reports_cfg.get("preview_count", 10)),
        rescue_thresholds=list(reports_cfg.get("rescue_threshold_samples", [5, 6, 7])),
        rescue_train_has_left=set(
            enrichment_cfg["rescue"].get("train_has_left_archetypes") or []
        ),
    )

    log.info(
        "Module 5 complete (quarter=%s): %d packs (built=%d cache_hit=%d refreshed=%d) "
        "wall=%.2fs DB=%.2fMB",
        quarter, len(built_packs), status_counts["built"],
        status_counts["cache_hit"], status_counts["refreshed"],
        run_stats["wall_seconds"], db_size_mb,
    )

    return pd.DataFrame(summary_rows)


def _write_empty_outputs(
    quarter: str, ranked_path: Path, config: PipelineConfig, started: float,
) -> pd.DataFrame:
    log.warning("ranked parquet at %s is empty; writing empty outputs", ranked_path)
    exclusions_path = (
        config.paths.intermediate_outputs_dir / f"pack_exclusions_{quarter}.parquet"
    )
    ensure_dir(exclusions_path.parent)
    pd.DataFrame(columns=[
        "ticker", "rank", "archetype", "composite_best", "reason", "detail",
    ]).to_parquet(exclusions_path, index=False, engine="pyarrow")

    report_path = config.paths.reports_dir / f"enrichment_report_{quarter}.html"
    ensure_dir(report_path.parent)
    generate_enrichment_report_html(
        [], pd.DataFrame(), ranked_count=0,
        run_stats={"cache_status_counts": {}, "wall_seconds": time.monotonic() - started,
                   "db_size_mb": 0.0, "pack_version": "m5-v1"},
        output_path=report_path, quarter=quarter,
    )
    return pd.DataFrame(columns=["ticker", "cache_status", "archetype",
                                  "best_horizon", "composite_best"])
