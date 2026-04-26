"""Module 4c — orchestrator (financials-only, biotech).

For each (ticker, source) pair:
  1. Industry gate (biotech only).
  2. TTL gate (fetch_log.last_fetched_at against per-source TTL).
  3. Fetch via edgar_client.
  4. Write rows + fetch_log; commit per-ticker.

Fail-open: a single (ticker, source) failure logs to fetch_log and continues.

Reads:
  config/fundamentals.yaml
  data/prices.db (industry classification)
  Outputs/sec_company_tickers.json (ticker→CIK)
  _intermediate_outputs/ranked_candidates_{quarter}.parquet (ticker feed)

Writes:
  data/fundamentals.db
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import yaml

from module_1 import ConfigError, PROJECT_ROOT, PipelineConfig, resolve_quarter

from . import edgar_client
from .fundamentals_db import (
    db_connect,
    init_fundamentals_db,
    upsert_capital_raise,
    upsert_fetch_log,
    upsert_financials_row,
    upsert_insider_transaction,
)
from .industries import (
    DEFAULT_BIOTECH_INDUSTRIES,
    filter_biotech,
    load_biotech_industries,
    load_ticker_industries,
)

log = logging.getLogger(__name__)


SOURCES_ALL = ("companyfacts", "submissions", "form4", "capital_raises")

_FORMS_FOR_RAISES = ("8-K", "S-3", "S-3/A", "424B5")
_FORMS_FOR_FORM4 = ("4",)


@dataclass
class EnrichResult:
    quarter: str
    n_tickers_total: int = 0
    n_biotech: int = 0
    n_non_biotech: int = 0
    n_no_cik: int = 0
    rows_written: dict[str, int] = field(default_factory=lambda: {s: 0 for s in SOURCES_ALL})
    failed: list[tuple[str, str, str]] = field(default_factory=list)
    skipped_ttl: list[tuple[str, str]] = field(default_factory=list)
    wall_seconds: float = 0.0


def _now_iso() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _days_old(iso_ts: Optional[str], now: dt.datetime) -> float:
    if not iso_ts:
        return float("inf")
    s = iso_ts.rstrip("Z").split(".")[0]
    try:
        ts = dt.datetime.fromisoformat(s)
    except ValueError:
        return float("inf")
    return (now - ts).total_seconds() / 86400.0


def _load_yaml_config(path: Path) -> dict:
    """Load fundamentals.yaml or return defaults if missing."""
    if not path.exists():
        log.warning("fundamentals.yaml not found at %s — using defaults", path)
        return {
            "biotech_industries": list(DEFAULT_BIOTECH_INDUSTRIES),
            "ttls": {
                "companyfacts_days":   30,
                "submissions_days":     7,
                "form4_days":          30,
                "capital_raises_days": 30,
            },
            "sources": {s: True for s in SOURCES_ALL},
            "edgar":  {"max_workers": 5},
            "form4":  {"lookback_days_first_run": 1095, "lookback_days_per_run": 180,
                       "ignore_txn_types": ["other"]},
            "db_path": "data/fundamentals.db",
        }
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: YAML syntax error: {e}") from e
    return raw


def _quarter_tickers(quarter: str, parquet_dir: Path) -> list[str]:
    """Read the M4b ranked feed for `quarter` and return its ticker list."""
    parquet_path = parquet_dir / f"ranked_candidates_{quarter}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(
            f"ranked candidates not found at {parquet_path}. "
            "Run scripts/4_rank.py first or use --ticker / --tickers."
        )
    df = pd.read_parquet(parquet_path, columns=["ticker"])
    seen: set[str] = set()
    out: list[str] = []
    for t in df["ticker"].dropna().astype(str).tolist():
        T = t.upper()
        if T not in seen:
            seen.add(T)
            out.append(T)
    return out


# ─── Per-source workers ──────────────────────────────────────────────────────

def _compute_shelf_capacity(conn: sqlite3.Connection, ticker: str) -> Optional[int]:
    """Compute MAX(S-3 gross_proceeds) - SUM(424B5 gross_proceeds) over the
    trailing 3y window."""
    cutoff = (dt.date.today() - dt.timedelta(days=1095)).isoformat()
    s3_max_row = conn.execute(
        """
        SELECT COALESCE(MAX(gross_proceeds_usd), 0) FROM capital_raises
        WHERE ticker = ? AND form LIKE 'S-3%' AND filing_date >= ?
        """,
        (ticker, cutoff),
    ).fetchone()
    s3_max = (s3_max_row[0] or 0) if s3_max_row else 0
    if s3_max <= 0:
        return None
    drawn_row = conn.execute(
        """
        SELECT COALESCE(SUM(gross_proceeds_usd), 0) FROM capital_raises
        WHERE ticker = ? AND form = '424B5' AND filing_date >= ?
        """,
        (ticker, cutoff),
    ).fetchone()
    drawn = (drawn_row[0] or 0) if drawn_row else 0
    cap = max(0, s3_max - drawn)
    return int(cap)


def _run_companyfacts(
    conn: sqlite3.Connection, ticker: str, cik: str, now_iso: str,
) -> tuple[str, Optional[str], int]:
    res = edgar_client.fetch_companyfacts(ticker, cik, now_iso=now_iso)
    rows_written = 0
    if res.rows:
        shelf_cap = _compute_shelf_capacity(conn, ticker)
        for i, r in enumerate(res.rows):
            r["ticker"] = ticker
            r["cik"] = cik
            r["fetched_at"] = now_iso
            r["fetch_status"] = res.status
            r["fetch_error"] = res.error
            r["shelf_registration_usd_capacity"] = shelf_cap if i == 0 else None
            r.setdefault("companyfacts_raw_json", None)
            upsert_financials_row(conn, r)
            rows_written += 1
    return res.status, res.error, rows_written


def _run_submissions(
    cik: str, since_iso: str,
) -> tuple[str, Optional[str], dict[str, list[dict]]]:
    """Submissions fetch returns a dict by-form for downstream Form 4 + raises
    workers to consume."""
    forms = list(_FORMS_FOR_RAISES) + list(_FORMS_FOR_FORM4)
    res = edgar_client.fetch_recent_filings(cik, since_date_iso=since_iso, forms=forms)
    if res.status != "ok":
        return res.status, res.error, {}
    by_form: dict[str, list[dict]] = {}
    for f in res.filings:
        by_form.setdefault((f["form"] or "").upper(), []).append(f)
    return "ok", None, by_form


def _run_form4(
    conn: sqlite3.Connection, ticker: str, cik: str,
    form4_filings: list[dict], now_iso: str, ignore_txn_types: set[str],
) -> tuple[str, Optional[str], int]:
    """Fetch + parse each Form 4 filing not yet in the DB."""
    if not form4_filings:
        return "ok", None, 0
    existing = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT accession_number FROM insider_transactions WHERE ticker = ?",
            (ticker,),
        ).fetchall()
    }
    rows_written = 0
    last_error: Optional[str] = None
    any_partial = False
    for f in form4_filings:
        accession = f["accession_number"]
        if accession in existing:
            continue
        res = edgar_client.fetch_form4(cik, accession, f.get("primary_doc"))
        if res.status == "failed":
            any_partial = True
            last_error = res.error
            log.debug("Form 4 %s/%s failed: %s", ticker, accession, res.error)
            continue
        if res.status == "partial":
            any_partial = True
            last_error = res.error
        for t in res.transactions:
            if (t.get("txn_type") or "other") in ignore_txn_types:
                continue
            row = {
                **t,
                "ticker": ticker,
                "cik": cik,
                "accession_number": accession,
                "filing_date": f["filing_date"],
                "raw_form4_url": edgar_client._build_form4_url(
                    cik, accession, f.get("primary_doc"),
                ),
                "fetched_at": now_iso,
                "fetch_status": res.status,
            }
            upsert_insider_transaction(conn, row)
            rows_written += 1
    status = "partial" if any_partial else "ok"
    return status, last_error, rows_written


def _run_capital_raises(
    conn: sqlite3.Connection, ticker: str, cik: str,
    raise_filings: list[dict], now_iso: str,
) -> tuple[str, Optional[str], int]:
    """Fetch + parse each candidate capital-raise filing."""
    if not raise_filings:
        return "ok", None, 0
    existing = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT accession_number FROM capital_raises WHERE ticker = ?",
            (ticker,),
        ).fetchall()
    }
    rows_written = 0
    any_partial = False
    last_error: Optional[str] = None
    for f in raise_filings:
        accession = f["accession_number"]
        if accession in existing:
            continue
        parsed = edgar_client.fetch_capital_raise(cik, f)
        if parsed is None:
            continue
        if parsed.get("fetch_status") == "partial":
            any_partial = True
        row = {
            **parsed,
            "ticker": ticker,
            "cik": cik,
            "event_date": parsed.get("filing_date"),
            "fetched_at": now_iso,
        }
        upsert_capital_raise(conn, row)
        rows_written += 1
    return ("partial" if any_partial else "ok"), last_error, rows_written


# ─── Top-level ───────────────────────────────────────────────────────────────

def run_enrichment_4c(
    config: PipelineConfig,
    *,
    quarter: Optional[str] = None,
    explicit_tickers: Optional[Iterable[str]] = None,
    sources: Optional[Iterable[str]] = None,
    force_refresh: bool = False,
    dry_run: bool = False,
) -> EnrichResult:
    """Top-level M4c run. See module docstring for inputs/outputs."""
    started = time.monotonic()
    quarter = quarter or resolve_quarter(config)

    cfg_path = PROJECT_ROOT / "config" / "fundamentals.yaml"
    fcfg = _load_yaml_config(cfg_path)
    db_path = PROJECT_ROOT / str(fcfg.get("db_path", "data/fundamentals.db"))
    init_fundamentals_db(db_path)

    biotech_set = load_biotech_industries(cfg_path)
    ttls: dict = fcfg.get("ttls") or {}
    src_toggles: dict = fcfg.get("sources") or {}
    form4_cfg: dict = fcfg.get("form4") or {}

    enabled_sources = {s for s in SOURCES_ALL if src_toggles.get(s, True)}
    if sources:
        requested = {s for s in sources}
        unknown = requested - set(SOURCES_ALL)
        if unknown:
            raise ConfigError(f"unknown sources: {sorted(unknown)}")
        enabled_sources = enabled_sources & requested

    ignore_txn_types = set(form4_cfg.get("ignore_txn_types") or ["other"])
    form4_lookback_first = int(form4_cfg.get("lookback_days_first_run") or 1095)
    form4_lookback_per_run = int(form4_cfg.get("lookback_days_per_run") or 180)

    # ---- Resolve ticker list
    if explicit_tickers:
        tickers = sorted({str(t).upper() for t in explicit_tickers if t})
    else:
        tickers = _quarter_tickers(quarter, config.paths.intermediate_outputs_dir)

    result = EnrichResult(quarter=quarter, n_tickers_total=len(tickers))
    if not tickers:
        log.warning("no tickers resolved for quarter %s", quarter)
        result.wall_seconds = time.monotonic() - started
        return result

    industries = load_ticker_industries(config.paths.prices_db, tickers)
    biotech, non_biotech = filter_biotech(tickers, industries, biotech_set)
    result.n_biotech = len(biotech)
    result.n_non_biotech = len(non_biotech)
    log.info(
        "M4c quarter=%s tickers=%d biotech=%d non_biotech=%d",
        quarter, len(tickers), len(biotech), len(non_biotech),
    )

    ticker_cik = edgar_client.load_ticker_cik_map()

    now = dt.datetime.utcnow()
    now_iso = _now_iso()

    if dry_run:
        log.info("DRY-RUN: would process %d biotech tickers across sources %s",
                 len(biotech), sorted(enabled_sources))
        for t in biotech:
            cik = ticker_cik.get(t)
            log.info("  %s  cik=%s", t, cik or "(none)")
        for t, ind in non_biotech:
            log.info("  SKIP %s (industry=%s)", t, ind)
        result.wall_seconds = time.monotonic() - started
        return result

    with db_connect(db_path) as conn:
        # Mark non-biotech in fetch_log so subsequent runs see the skip reason.
        for t, ind in non_biotech:
            for src in SOURCES_ALL:
                upsert_fetch_log(
                    conn,
                    ticker=t, source=src,
                    last_fetched_at=now_iso,
                    last_status="skipped_non_biotech",
                    last_error=f"industry={ind!r}",
                    rows_written=0,
                )

        # Tickers without a SEC CIK get logged-and-skipped.
        for t in biotech:
            cik = ticker_cik.get(t)
            if not cik:
                for src in SOURCES_ALL:
                    upsert_fetch_log(
                        conn,
                        ticker=t, source=src,
                        last_fetched_at=now_iso,
                        last_status="failed",
                        last_error="no CIK in SEC ticker map",
                        rows_written=0,
                    )
                result.n_no_cik += 1
                result.failed.append((t, "*", "no CIK in SEC ticker map"))

        for t in biotech:
            cik = ticker_cik.get(t)
            if not cik:
                continue
            t_started = time.monotonic()

            # Fetch existing fetch_log for this ticker (per-source TTL gate).
            log_rows = {
                r[0]: dict(zip(["last_fetched_at", "last_status", "rows_written"], r[1:]))
                for r in conn.execute(
                    "SELECT source, last_fetched_at, last_status, rows_written "
                    "FROM fetch_log WHERE ticker = ?",
                    (t,),
                ).fetchall()
            }

            def _ttl_ok(source: str, ttl_days: int) -> bool:
                if force_refresh:
                    return False
                row = log_rows.get(source)
                if not row:
                    return False
                if row.get("last_status") == "failed":
                    return False
                return _days_old(row.get("last_fetched_at"), now) < ttl_days

            # ── Submissions (always probed first; cheap discovery) ──
            forms_by: dict[str, list[dict]] = {}
            if "submissions" in enabled_sources:
                ttl_subs = int(ttls.get("submissions_days") or 7)
                if _ttl_ok("submissions", ttl_subs):
                    result.skipped_ttl.append((t, "submissions"))
                    log.info("[%s] submissions TTL fresh — skip", t)
                else:
                    has_form4 = bool(conn.execute(
                        "SELECT 1 FROM insider_transactions WHERE ticker = ? LIMIT 1",
                        (t,),
                    ).fetchone())
                    lookback = form4_lookback_per_run if has_form4 else form4_lookback_first
                    since_iso = (dt.date.today() - dt.timedelta(days=lookback)).isoformat()
                    s_status, s_error, by_form = _run_submissions(cik, since_iso)
                    forms_by = by_form
                    upsert_fetch_log(
                        conn,
                        ticker=t, source="submissions",
                        last_fetched_at=now_iso, last_status=s_status,
                        last_error=s_error,
                        rows_written=sum(len(v) for v in by_form.values()),
                    )
                    if s_status == "failed":
                        result.failed.append((t, "submissions", s_error or ""))

            # ── companyfacts ──
            if "companyfacts" in enabled_sources:
                ttl_cf = int(ttls.get("companyfacts_days") or 30)
                if _ttl_ok("companyfacts", ttl_cf):
                    result.skipped_ttl.append((t, "companyfacts"))
                else:
                    cf_status, cf_error, n = _run_companyfacts(conn, t, cik, now_iso)
                    upsert_fetch_log(
                        conn,
                        ticker=t, source="companyfacts",
                        last_fetched_at=now_iso, last_status=cf_status,
                        last_error=cf_error, rows_written=n,
                    )
                    result.rows_written["companyfacts"] += n
                    if cf_status == "failed":
                        result.failed.append((t, "companyfacts", cf_error or ""))

            # ── capital_raises ──
            if "capital_raises" in enabled_sources:
                ttl_cr = int(ttls.get("capital_raises_days") or 30)
                if _ttl_ok("capital_raises", ttl_cr) and not forms_by:
                    result.skipped_ttl.append((t, "capital_raises"))
                else:
                    raise_filings: list[dict] = []
                    for f in _FORMS_FOR_RAISES:
                        raise_filings += forms_by.get(f.upper(), [])
                    cr_status, cr_error, n = _run_capital_raises(
                        conn, t, cik, raise_filings, now_iso,
                    )
                    upsert_fetch_log(
                        conn,
                        ticker=t, source="capital_raises",
                        last_fetched_at=now_iso, last_status=cr_status,
                        last_error=cr_error, rows_written=n,
                    )
                    result.rows_written["capital_raises"] += n
                    if cr_status == "failed":
                        result.failed.append((t, "capital_raises", cr_error or ""))

            # ── form4 ──
            if "form4" in enabled_sources:
                ttl_f4 = int(ttls.get("form4_days") or 30)
                if _ttl_ok("form4", ttl_f4) and not forms_by:
                    result.skipped_ttl.append((t, "form4"))
                else:
                    form4_filings = forms_by.get("4", [])
                    f4_status, f4_error, n = _run_form4(
                        conn, t, cik, form4_filings, now_iso, ignore_txn_types,
                    )
                    upsert_fetch_log(
                        conn,
                        ticker=t, source="form4",
                        last_fetched_at=now_iso, last_status=f4_status,
                        last_error=f4_error, rows_written=n,
                    )
                    result.rows_written["form4"] += n
                    if f4_status == "failed":
                        result.failed.append((t, "form4", f4_error or ""))

            conn.commit()
            log.info("[%s] done in %.1fs", t, time.monotonic() - t_started)

    result.wall_seconds = time.monotonic() - started
    return result
