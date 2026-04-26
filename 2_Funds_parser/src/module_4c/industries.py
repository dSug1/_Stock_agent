"""Module 4c — biotech industry gate.

Reads `prices.db.ticker_snapshot.industry` to decide which tickers M4c
will enrich. Non-biotech tickers are logged as `skipped_non_biotech`
in `fetch_log`; M5 emits packs without the fundamentals block; M6
falls back to `web_search`.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

DEFAULT_BIOTECH_INDUSTRIES = (
    "Biotechnology",
    "Drug Manufacturers - Specialty & Generic",
    "Drug Manufacturers - General",
)


def load_biotech_industries(yaml_path: Optional[Path]) -> set[str]:
    """Load biotech_industries: from config/fundamentals.yaml.
    Falls back to DEFAULT_BIOTECH_INDUSTRIES if file missing or key absent.
    """
    if yaml_path is None or not yaml_path.exists():
        return set(DEFAULT_BIOTECH_INDUSTRIES)
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        log.warning("fundamentals.yaml unparseable — using defaults")
        return set(DEFAULT_BIOTECH_INDUSTRIES)
    items = raw.get("biotech_industries") or []
    if not items:
        return set(DEFAULT_BIOTECH_INDUSTRIES)
    return {str(s) for s in items}


def load_ticker_industries(
    prices_db: Path, tickers: list[str]
) -> dict[str, Optional[str]]:
    """Bulk-read ticker_snapshot.industry. Missing tickers map to None."""
    out: dict[str, Optional[str]] = {t: None for t in tickers}
    if not prices_db.exists() or not tickers:
        return out
    placeholders = ",".join("?" * len(tickers))
    with sqlite3.connect(prices_db) as cx:
        rows = cx.execute(
            f"SELECT ticker, industry FROM ticker_snapshot WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    for ticker, industry in rows:
        out[ticker] = industry
    return out


def is_biotech_ticker(industry: Optional[str], biotech_set: set[str]) -> bool:
    """True iff industry is in the configured biotech allow-list."""
    return bool(industry) and industry in biotech_set


def filter_biotech(
    tickers: list[str],
    industries: dict[str, Optional[str]],
    biotech_set: set[str],
) -> tuple[list[str], list[tuple[str, Optional[str]]]]:
    """Returns (biotech_tickers, [(non_biotech_ticker, industry), ...])."""
    biotech: list[str] = []
    non_biotech: list[tuple[str, Optional[str]]] = []
    for t in tickers:
        ind = industries.get(t)
        if is_biotech_ticker(ind, biotech_set):
            biotech.append(t)
        else:
            non_biotech.append((t, ind))
    return biotech, non_biotech
