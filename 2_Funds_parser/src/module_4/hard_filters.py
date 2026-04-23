"""Module 4a — snapshot enrichment + hard filters.

Pipeline:
    1. Load Module 3's universe parquet.
    2. Phase 1 — cheap fund-side filters (no network) on the full universe.
    3. Snapshot fetch (yfinance + cache) for Phase-1 survivors only.
    4. Phase 2 — expensive snapshot-side filters on enriched survivors.
    5. Write survivors.parquet, hard_filter_rejections.parquet, and
       Outputs/filter_summary_{quarter}.html.

Design notes (see spec/module_4_spec.md):
- "Cheap before expensive" filter ordering minimises yfinance calls.
- Snapshot rows live in `data/prices.db` and respect snapshot.ttl_days.
  Filter-threshold tweaks (e.g. raising fund_count_min) NEVER trigger refetch.
- First-failure-wins rejection logging (decision D18).
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from module_1 import ConfigError, PipelineConfig, ensure_dir

from . import prices as price_cache

log = logging.getLogger(__name__)


SNAPSHOT_COLS: list[str] = [
    "short_name", "long_name", "sector", "industry", "exchange", "currency",
    "market_cap", "shares_out", "last_close", "adv_30d",
    "snapshot_fetched_at", "snapshot_status",
]


# ─── Config loading ──────────────────────────────────────────────────────────

def load_filters_config(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"filters config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        loc = f"{path}:{mark.line + 1}:{mark.column + 1} " if mark else f"{path} "
        raise ConfigError(f"{loc}YAML syntax error: {e}") from e
    if not isinstance(raw, dict) or "hard_filters" not in raw or "snapshot" not in raw:
        raise ConfigError(
            f"{path}: top-level keys 'hard_filters' and 'snapshot' are required"
        )
    return raw


# ─── Phase 1: cheap fund-side filters ────────────────────────────────────────

def _check_cheap(row: pd.Series, hf: dict) -> tuple[bool, Optional[str], Optional[float]]:
    """First failure wins. Returns (passed, reason, value)."""
    # 1. require_ticker_resolved
    if hf.get("require_ticker_resolved", True):
        if pd.isna(row.get("ticker")) or row.get("ticker") in (None, ""):
            return False, "require_ticker_resolved", None

    # 2. exclude_has_unknown_class
    if hf.get("exclude_has_unknown_class", False):
        if bool(row.get("has_unknown_class", False)):
            return False, "exclude_has_unknown_class", 1.0

    # 3. require_ticker_verified
    if hf.get("require_ticker_verified", False):
        if not bool(row.get("ticker_is_verified", False)):
            return False, "require_ticker_verified", 0.0

    # 4. fund_count_min
    fc_min = hf.get("fund_count_min", 1)
    fc = row.get("fund_count")
    if fc is None or pd.isna(fc) or int(fc) < int(fc_min):
        return False, "fund_count_min", float(fc) if pd.notna(fc) else None

    # 5. exclude_lonely_seller — fund_count == 1 AND decreased_positions >= 1
    if hf.get("exclude_lonely_seller", True):
        decreased = row.get("decreased_positions")
        if (int(fc) == 1 and decreased is not None
                and not pd.isna(decreased) and int(decreased) >= 1):
            return False, "exclude_lonely_seller", float(decreased)

    return True, None, None


def apply_cheap_filters(
    universe_df: pd.DataFrame,
    filters_config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Phase 1 filters. No network. Returns (passing, rejections)."""
    hf = filters_config["hard_filters"]
    if universe_df.empty:
        return universe_df.copy(), _empty_rejections()

    passing_idx: list[int] = []
    rej_rows: list[dict] = []
    for idx, row in universe_df.iterrows():
        ok, reason, value = _check_cheap(row, hf)
        if ok:
            passing_idx.append(idx)
        else:
            rej_rows.append({
                "ticker": row.get("ticker"),
                "cusip": row.get("cusip"),
                "name_of_issuer": row.get("name_of_issuer"),
                "quarter": row.get("quarter"),
                "rejection_reason": reason,
                "rejection_value": value,
            })
    passing = universe_df.loc[passing_idx].copy().reset_index(drop=True)
    rejections = pd.DataFrame(rej_rows, columns=_REJECTION_COLS)
    return passing, rejections


# ─── Snapshot enrichment ─────────────────────────────────────────────────────

def fetch_or_reuse_snapshots(
    tickers: list[str],
    db_path: Path,
    snapshot_config: dict,
) -> pd.DataFrame:
    """Returns a DataFrame keyed by ticker with snapshot columns + status.

    Two-tier freshness:
      - Static fields (shares_out, sector, etc.): TTL = static_ttl_days
        (default 30). Refetched only when stale, missing, or previously failed.
      - Price-derived fields (last_close, adv_30d, market_cap): always
        recomputed from bars on every run. Cheap; uses the chart endpoint.

    On Yahoo throttle: rows already fetched are persisted; remaining tickers
    return as 'partial' (no fetch_error) so the next run's cache-staleness
    check picks them up for retry.
    """
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return pd.DataFrame(columns=["ticker", *SNAPSHOT_COLS])

    static_ttl_s = float(
        snapshot_config.get("static_ttl_days",
                            snapshot_config.get("ttl_days", 30))
    ) * 86_400
    fetch_descriptive = bool(snapshot_config.get("fetch_descriptive_info", False))

    cached = price_cache.get_snapshots(db_path, tickers)
    fresh_static: set[str] = set()
    needs_static_fetch: list[str] = []

    for t in tickers:
        c = cached.get(t)
        if c is None:
            needs_static_fetch.append(t)
            continue
        age = price_cache.age_seconds(c.get("fetched_at"))
        if age > static_ttl_s:
            needs_static_fetch.append(t)
            continue
        status = c.get("fetch_status")
        if status in ("failed", None):
            needs_static_fetch.append(t)
            continue
        if c.get("shares_out") is None:
            # Static fetch never succeeded for shares_out — retry.
            needs_static_fetch.append(t)
            continue
        if fetch_descriptive and not c.get("sector"):
            # Caller wants descriptive info, but cache lacks it — retry .info path.
            needs_static_fetch.append(t)
            continue
        fresh_static.add(t)

    log.info(
        "snapshot: %d tickers (static fresh=%d, static stale/missing=%d)",
        len(tickers), len(fresh_static), len(needs_static_fetch),
    )

    price_cache.configure_rate_limits(
        info_rate_per_s=float(snapshot_config.get("info_rate_per_s", 2.0)),
        bars_rate_per_s=float(snapshot_config.get("bars_rate_per_s", 2.0)),
    )

    # All tickers go through fetch_snapshots_parallel — those in fresh_static
    # skip yfinance (just bars + cache reuse), the rest do the full path.
    snaps = price_cache.fetch_snapshots_parallel(
        tickers,
        db_path,
        cached_static=cached,
        fresh_static=fresh_static,
        fetch_descriptive_info=fetch_descriptive,
        bars_batch_size=int(snapshot_config.get("bars_batch_size", 150)),
        info_max_workers=int(snapshot_config.get("info_max_workers", 4)),
        retries=int(snapshot_config.get("retries", 2)),
        backoff_s=tuple(snapshot_config.get("retry_backoff_s", [1.0, 2.0, 4.0])),
    )

    out_rows: list[dict] = []
    for t in tickers:
        row = snaps.get(t, {})
        out_rows.append({
            "ticker": t,
            "short_name": row.get("short_name"),
            "long_name": row.get("long_name"),
            "sector": row.get("sector"),
            "industry": row.get("industry"),
            "exchange": row.get("exchange"),
            "currency": row.get("currency"),
            "market_cap": row.get("market_cap"),
            "shares_out": row.get("shares_out"),
            "last_close": row.get("last_close"),
            "adv_30d": row.get("adv_30d"),
            "snapshot_fetched_at": row.get("fetched_at"),
            "snapshot_status": row.get("fetch_status") or "missing",
        })
    return pd.DataFrame(out_rows, columns=["ticker", *SNAPSHOT_COLS])


# ─── Phase 2: expensive snapshot filters ─────────────────────────────────────

def _check_snapshot(row: pd.Series, hf: dict) -> tuple[bool, Optional[str], Optional[float]]:
    if row.get("snapshot_status") not in ("ok", "partial"):
        return False, "snapshot_unavailable", None

    last_close = row.get("last_close")
    if last_close is None or pd.isna(last_close):
        return False, "snapshot_unavailable", None

    # 1. require_min_price_usd
    min_price = hf.get("require_min_price_usd")
    if min_price is not None and float(last_close) < float(min_price):
        return False, "require_min_price_usd", float(last_close)

    # 2. market_cap range — uses fetch_ceiling as the upper bound (the user
    # cap is applied separately via apply_user_market_cap, after the user
    # picks one from the prompt or CLI flag).
    mc = row.get("market_cap")
    if mc is None or pd.isna(mc):
        return False, "market_cap_missing", None
    mc_min = hf.get("market_cap_min_usd")
    mc_ceiling = hf.get("market_cap_fetch_ceiling_usd")
    if mc_min is not None and float(mc) < float(mc_min):
        return False, "market_cap_min_usd", float(mc)
    if mc_ceiling is not None and float(mc) > float(mc_ceiling):
        return False, "market_cap_fetch_ceiling_usd", float(mc)

    # 3. adv_30d
    adv_min = hf.get("adv_30d_min_usd")
    adv = row.get("adv_30d")
    if adv_min is not None:
        if adv is None or pd.isna(adv) or float(adv) < float(adv_min):
            return False, "adv_30d_min_usd", float(adv) if pd.notna(adv) else None

    # 4. sector allow/block
    allowlist = hf.get("sector_allowlist")
    blocklist = hf.get("sector_blocklist")
    sector = row.get("sector") or ""
    if allowlist:
        if sector not in allowlist:
            return False, "sector_allowlist", None
    if blocklist:
        if sector in blocklist:
            return False, "sector_blocklist", None

    return True, None, None


# ─── User-driven market-cap cap: distribution + interactive prompt ──────────

def _market_cap_distribution(caps: pd.Series) -> list[tuple[str, int, int, int]]:
    """Returns rows of (label, lo_usd, hi_usd, count) for display."""
    breakpoints_usd = [
        ("<= $100M",     0,        100e6),
        ("$100M-$500M",  100e6,    500e6),
        ("$500M-$1B",    500e6,    1e9),
        ("$1B-$2B",      1e9,      2e9),
        ("$2B-$3.7B",    2e9,      3.7e9),
        ("$3.7B-$5B",    3.7e9,    5e9),
        ("$5B-$7.5B",    5e9,      7.5e9),
        ("$7.5B-$10B",   7.5e9,    10e9),
        ("> $10B",       10e9,     float("inf")),
    ]
    out: list[tuple[str, int, int, int]] = []
    for label, lo, hi in breakpoints_usd:
        n = int(((caps >= lo) & (caps < hi)).sum())
        out.append((label, int(lo), int(hi) if hi != float("inf") else 10**18, n))
    return out


def prompt_user_for_cap(
    candidates_df: pd.DataFrame,
    *,
    default_usd: int,
    ceiling_usd: int,
    interactive: bool = True,
) -> int:
    """Show a histogram of market caps + prompt for a max.

    Returns the chosen cap in USD. When stdin is not a TTY (or interactive=False),
    falls back to default_usd silently. ASCII-only output (Windows cp1252 safe).
    """
    if candidates_df.empty:
        return default_usd
    caps = pd.to_numeric(candidates_df["market_cap"], errors="coerce").dropna()
    if caps.empty:
        return default_usd

    n = len(caps)
    cumul = 0
    print(f"\n=== Market-cap distribution among {n} candidates ===")
    print(f"  {'bucket':<14s} {'count':>6s} {'cum':>6s}  bar")
    bars_scale = max(50, n) / 50
    for label, _, _, count in _market_cap_distribution(caps):
        cumul += count
        bar = "#" * int(round(count / bars_scale))
        print(f"  {label:<14s} {count:>6d} {cumul:>6d}  {bar}")
    print(f"  (default: ${default_usd / 1e9:.2f}B; ceiling: ${ceiling_usd / 1e9:.1f}B)")

    use_default_msg = f"using default ${default_usd / 1e9:.2f}B"
    if not interactive or not sys.stdin.isatty():
        print(f"  [non-interactive -- {use_default_msg}]\n")
        return default_usd

    while True:
        try:
            raw = input(
                "Enter max market cap in $M "
                f"[Enter for default {int(default_usd / 1e6):,}M, 'all' for full ceiling]: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print(f"  [aborted -- {use_default_msg}]\n")
            return default_usd
        if not raw:
            print(f"  [{use_default_msg}]\n")
            return default_usd
        if raw.lower() in ("all", "ceiling", "max"):
            print(f"  [keeping all candidates up to ${ceiling_usd / 1e9:.1f}B]\n")
            return ceiling_usd
        try:
            mil = float(raw.replace(",", ""))
            if mil <= 0:
                print("  Must be > 0; try again.")
                continue
            cap = int(mil * 1e6)
            print(f"  [user cap: ${cap / 1e9:.3f}B]\n")
            return cap
        except ValueError:
            print(f"  '{raw}' isn't a number; enter $M as a number, or 'all'.")


def apply_user_market_cap(
    candidates_df: pd.DataFrame,
    user_cap_usd: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split candidates by user-chosen cap. Returns (survivors, extra_rejections).

    extra_rejections are formatted to merge into the main rejections frame
    (rejection_reason='user_market_cap_max', rejection_value=actual market_cap).
    """
    if candidates_df.empty:
        return candidates_df.copy(), _empty_rejections()

    caps = pd.to_numeric(candidates_df["market_cap"], errors="coerce")
    above_mask = caps > float(user_cap_usd)
    below_or_eq = candidates_df.loc[~above_mask].copy().reset_index(drop=True)
    above = candidates_df.loc[above_mask].copy()

    if above.empty:
        return below_or_eq, _empty_rejections()

    rej = pd.DataFrame({
        "ticker": above["ticker"].values,
        "cusip": above["cusip"].values if "cusip" in above else None,
        "name_of_issuer": above["name_of_issuer"].values if "name_of_issuer" in above else None,
        "quarter": above["quarter"].values if "quarter" in above else None,
        "rejection_reason": "user_market_cap_max",
        "rejection_value": pd.to_numeric(above["market_cap"], errors="coerce").values,
    }, columns=_REJECTION_COLS)
    return below_or_eq, rej


def apply_snapshot_filters(
    enriched_df: pd.DataFrame,
    filters_config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Phase 2 filters. Returns (survivors, rejections)."""
    hf = filters_config["hard_filters"]
    if enriched_df.empty:
        return enriched_df.copy(), _empty_rejections()

    passing_idx: list[int] = []
    rej_rows: list[dict] = []
    for idx, row in enriched_df.iterrows():
        ok, reason, value = _check_snapshot(row, hf)
        if ok:
            passing_idx.append(idx)
        else:
            rej_rows.append({
                "ticker": row.get("ticker"),
                "cusip": row.get("cusip"),
                "name_of_issuer": row.get("name_of_issuer"),
                "quarter": row.get("quarter"),
                "rejection_reason": reason,
                "rejection_value": value,
            })
    survivors = enriched_df.loc[passing_idx].copy().reset_index(drop=True)
    rejections = pd.DataFrame(rej_rows, columns=_REJECTION_COLS)
    return survivors, rejections


# ─── Output writing ──────────────────────────────────────────────────────────

_REJECTION_COLS = ["ticker", "cusip", "name_of_issuer", "quarter",
                   "rejection_reason", "rejection_value"]


def _empty_rejections() -> pd.DataFrame:
    return pd.DataFrame(columns=_REJECTION_COLS)


def _sort_survivors(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.sort_values(
        by=["fund_count", "total_market_value", "ticker", "cusip"],
        ascending=[False, False, True, True],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)


def write_survivors(survivors: pd.DataFrame, rejections: pd.DataFrame,
                    quarter: str, out_dir: Path) -> dict[str, Path]:
    ensure_dir(out_dir)
    paths = {
        "survivors": out_dir / f"survivors_{quarter}.parquet",
        "rejections": out_dir / f"hard_filter_rejections_{quarter}.parquet",
    }
    survivors.to_parquet(paths["survivors"], index=False, engine="pyarrow")
    rejections.to_parquet(paths["rejections"], index=False, engine="pyarrow")
    return paths


# ─── HTML summary ────────────────────────────────────────────────────────────

def generate_filter_summary_html(
    rejections_df: pd.DataFrame,
    survivors_df: pd.DataFrame,
    universe_count: int,
    quarter: str,
    output_path: Path,
    *,
    candidate_pool_count: Optional[int] = None,
    user_cap_usd: Optional[int] = None,
    fetch_ceiling_usd: Optional[int] = None,
) -> None:
    """Funnel + per-reason breakdown table + sample dropped tickers per reason."""
    survivors_count = len(survivors_df)
    rejected_count = len(rejections_df)
    breakdown = Counter(rejections_df["rejection_reason"].tolist())
    rows_html = []
    for reason, count in sorted(breakdown.items(), key=lambda kv: (-kv[1], kv[0])):
        sample = (
            rejections_df[rejections_df["rejection_reason"] == reason]
            .head(8)["ticker"]
            .dropna()
            .astype(str)
            .tolist()
        )
        sample_str = ", ".join(sample) if sample else "—"
        rows_html.append(
            f"<tr><td><code>{html.escape(reason)}</code></td>"
            f"<td class='num'>{count:,}</td>"
            f"<td>{html.escape(sample_str)}</td></tr>"
        )
    rows = "\n".join(rows_html) or "<tr><td colspan='3'>No rejections.</td></tr>"

    pct_kept = (survivors_count / universe_count * 100) if universe_count else 0.0
    now_iso = dt.datetime.utcnow().isoformat(timespec="seconds")

    cap_meta = ""
    if user_cap_usd is not None and fetch_ceiling_usd is not None:
        cap_meta = (
            f"<div class='meta'>Fetch ceiling: ${fetch_ceiling_usd / 1e9:.1f}B · "
            f"User-selected max cap: <b>${user_cap_usd / 1e9:.3f}B</b></div>"
        )

    pool_stage = ""
    if candidate_pool_count is not None:
        pool_stage = (
            f"<div class='arrow'>→</div>"
            f"<div class='stage'>Candidate pool<br><b>{candidate_pool_count:,}</b></div>"
        )

    body = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'>
<title>Module 4a Filter Summary — {html.escape(quarter)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 980px; color: #222; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 1rem; }}
  .funnel {{ display: flex; gap: 0.5rem; align-items: center; margin: 1.5rem 0; flex-wrap: wrap; }}
  .funnel .stage {{ padding: 0.6rem 1rem; background: #eef; border-radius: 6px; }}
  .funnel .arrow {{ color: #999; font-size: 1.2rem; }}
  .funnel .pct {{ font-weight: 600; color: #045; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ padding: 0.4rem 0.7rem; border-bottom: 1px solid #eee; text-align: left; }}
  th {{ background: #f6f6f6; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
</style></head><body>
<h1>Filter Summary — {html.escape(quarter)}</h1>
<div class='meta'>Generated {html.escape(now_iso)} UTC</div>
{cap_meta}

<div class='funnel'>
  <div class='stage'>Universe<br><b>{universe_count:,}</b></div>
  <div class='arrow'>→</div>
  <div class='stage'>Rejected<br><b>{rejected_count:,}</b></div>
  {pool_stage}
  <div class='arrow'>→</div>
  <div class='stage'>Survivors<br><b>{survivors_count:,}</b> <span class='pct'>({pct_kept:.1f}%)</span></div>
</div>

<h2>Rejection breakdown</h2>
<table>
  <thead><tr><th>Reason</th><th class='num'>Count</th><th>Sample tickers</th></tr></thead>
  <tbody>
{rows}
  </tbody>
</table>
</body></html>
"""
    output_path.write_text(body, encoding="utf-8")


# ─── End-to-end orchestrator ─────────────────────────────────────────────────

def run_hard_filters(
    config: PipelineConfig,
    quarter: Optional[str] = None,
    *,
    user_market_cap_max_usd: Optional[int] = None,
    interactive: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read Module 3's universe and produce 4a outputs.

    Two cap layers:
      - `market_cap_fetch_ceiling_usd` (filters.yaml; default $10B): hard
        outer bound. Tickers above this never enter the candidate pool —
        no snapshot data persisted, no chance of selection.
      - User cap (CLI flag, prompt, or `market_cap_max_usd_default`): the
        actual cut applied to the candidate pool to produce `survivors`.
        Tickers between user_cap and ceiling are recorded with
        rejection_reason='user_market_cap_max' so the user can see what's
        just over their line and easily re-run with a different cap.

    `user_market_cap_max_usd`: if provided, skip the prompt entirely.
    `interactive=False` also skips the prompt (uses the YAML default).

    Returns (survivors_df, rejections_df). Writes both Parquets +
    Outputs/filter_summary_{quarter}.html as a side effect.
    """
    from module_1 import resolve_quarter

    quarter = quarter or resolve_quarter(config)
    universe_path = (
        config.paths.intermediate_outputs_dir / f"universe_{quarter}.parquet"
    )
    if not universe_path.exists():
        raise FileNotFoundError(
            f"Module 3 universe not found at {universe_path}. "
            "Run scripts/3_build_universe.py first."
        )
    universe = pd.read_parquet(universe_path)
    log.info("Loaded universe: %d rows from %s", len(universe), universe_path)

    from module_1 import PROJECT_ROOT
    filters_yaml = PROJECT_ROOT / "config" / "filters.yaml"
    cfg = load_filters_config(filters_yaml)
    hf = cfg["hard_filters"]

    # Phase 1 — cheap fund-side filters
    phase1_pass, phase1_rej = apply_cheap_filters(universe, cfg)
    log.info("Phase 1 (cheap): %d pass, %d reject", len(phase1_pass), len(phase1_rej))

    # Snapshot fetch (only for Phase-1 survivors)
    snapshots = fetch_or_reuse_snapshots(
        phase1_pass["ticker"].dropna().astype(str).tolist(),
        config.paths.prices_db,
        cfg.get("snapshot", {}),
    )
    enriched = phase1_pass.merge(snapshots, on="ticker", how="left")
    enriched["snapshot_status"] = enriched["snapshot_status"].fillna("missing")

    # Phase 2 — snapshot filters; uses fetch_ceiling as the upper bound
    candidate_pool, phase2_rej = apply_snapshot_filters(enriched, cfg)
    log.info("Phase 2 (snapshot, ceiling ${:.1f}B): {} pass, {} reject".format(
        float(hf.get("market_cap_fetch_ceiling_usd", 10e9)) / 1e9,
        len(candidate_pool), len(phase2_rej),
    ))

    # User cap — CLI flag wins; otherwise prompt; otherwise YAML default
    fetch_ceiling = int(hf.get("market_cap_fetch_ceiling_usd", 10_000_000_000))
    default_cap = int(hf.get("market_cap_max_usd_default", 3_700_000_000))
    if user_market_cap_max_usd is not None:
        chosen_cap = int(user_market_cap_max_usd)
        log.info("User market-cap max from CLI: ${:.3f}B".format(chosen_cap / 1e9))
    else:
        chosen_cap = prompt_user_for_cap(
            candidate_pool,
            default_usd=default_cap,
            ceiling_usd=fetch_ceiling,
            interactive=interactive,
        )
        log.info("User market-cap max chosen: ${:.3f}B".format(chosen_cap / 1e9))

    survivors, user_rej = apply_user_market_cap(candidate_pool, chosen_cap)
    log.info("User-cap filter: %d survivors / %d above cap", len(survivors), len(user_rej))

    survivors = _sort_survivors(survivors)
    rejections = pd.concat([phase1_rej, phase2_rej, user_rej], ignore_index=True)
    rejections = rejections.sort_values(
        by=["rejection_reason", "ticker"], na_position="last", kind="stable",
    ).reset_index(drop=True)

    paths = write_survivors(
        survivors, rejections, quarter, config.paths.intermediate_outputs_dir
    )
    log.info("Wrote %s (%d) and %s (%d)",
             paths["survivors"], len(survivors), paths["rejections"], len(rejections))

    summary_html = config.paths.reports_dir / f"filter_summary_{quarter}.html"
    generate_filter_summary_html(
        rejections, survivors, len(universe), quarter, summary_html,
        candidate_pool_count=len(candidate_pool),
        user_cap_usd=chosen_cap,
        fetch_ceiling_usd=fetch_ceiling,
    )
    log.info("Wrote summary HTML -> %s", summary_html)

    return survivors, rejections
