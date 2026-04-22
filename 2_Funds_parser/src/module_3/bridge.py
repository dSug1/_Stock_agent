"""Module 3 orchestrator: build per-ticker universe from per-filing holdings.

Pipeline (see spec/module_3_spec.md for full details):

    resolve quarter
        -> load current-quarter holdings (amendment-deduplicated)
        -> classify share types
        -> split into kept / dropped / unresolved
        -> aggregate per (fund_id, position_key)
        -> repeat for prior quarter
        -> aggregate across funds per ticker with QoQ metrics
        -> write three Parquets

Deterministic: same DB snapshot + same YAML rules -> byte-identical output.
No network. No external APIs. Pure DB read + pandas + Parquet write.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import yaml

from module_1 import (
    PROJECT_ROOT,
    ConfigError,
    PipelineConfig,
    ensure_dir,
    prior_quarter,
    quarter_to_date_end,
    resolve_quarter,
)

log = logging.getLogger(__name__)

VALID_CLASSES: frozenset[str] = frozenset({
    "common",
    "prefunded_warrant",
    "regular_warrant",
    "put",
    "call",
    "preferred",
    "debt",
    "unknown",
})

# Retained in the universe share totals.
KEPT_CLASSES: frozenset[str] = frozenset({
    "common",
    "prefunded_warrant",
    "unknown",
})

# Dropped to dropped_rows_{quarter}.parquet (NOT in share totals).
DROPPED_CLASSES: frozenset[str] = frozenset({
    "put",
    "call",
    "regular_warrant",
    "preferred",
    "debt",
})

# ticker_source values that count as "verified" for ticker_is_verified flag.
# sec_name does NOT count (name-match can return the wrong security class).
_VERIFIED_SOURCES: frozenset[str] = frozenset({"openfigi", "manual"})


# ----- rule loading -----------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClassificationRule:
    match: str
    classify_as: str


def load_share_type_rules(path: Path) -> list[ClassificationRule]:
    """Parse config/share_types.yaml and validate each rule.

    Raises ConfigError if YAML is malformed, the top-level key is missing,
    or any rule has a bad shape / unknown classify_as value.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ConfigError(f"share_types.yaml not found at {path}") from e
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        loc = f"{path}:{mark.line + 1}:{mark.column + 1} " if mark else f"{path} "
        raise ConfigError(f"{loc}YAML syntax error: {e}") from e

    if not isinstance(raw, dict) or "classification_rules" not in raw:
        raise ConfigError(
            f"{path}: top-level key 'classification_rules' is missing"
        )
    rules_raw = raw["classification_rules"]
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ConfigError(
            f"{path}: classification_rules must be a non-empty list"
        )

    rules: list[ClassificationRule] = []
    for i, entry in enumerate(rules_raw):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"{path}: classification_rules[{i}] must be a mapping"
            )
        match = entry.get("match")
        classify_as = entry.get("classify_as")
        if not isinstance(match, str) or not match:
            raise ConfigError(
                f"{path}: classification_rules[{i}].match must be a non-empty string"
            )
        if classify_as not in VALID_CLASSES:
            raise ConfigError(
                f"{path}: classification_rules[{i}].classify_as='{classify_as}' is not one of "
                f"{sorted(VALID_CLASSES)}"
            )
        rules.append(ClassificationRule(match=match, classify_as=classify_as))
    return rules


# ----- classification ---------------------------------------------------------

def classify_share_type(
    title_of_class: Optional[str],
    put_call: Optional[str],
    rules: Iterable[ClassificationRule],
) -> str:
    """Return one of VALID_CLASSES.

    put_call is checked first so a PUT titled 'COM' classifies as put.
    Substring match is case-insensitive; first rule wins.
    """
    if put_call == "Put":
        return "put"
    if put_call == "Call":
        return "call"
    title_upper = (title_of_class or "").upper()
    for rule in rules:
        if rule.match.upper() in title_upper:
            return rule.classify_as
    return "unknown"


# ----- DB load ----------------------------------------------------------------

def load_holdings_for_period(
    conn: sqlite3.Connection,
    holdings_table: str,
    funds_table: str,
    period_of_report: str,
) -> pd.DataFrame:
    """Amendment-deduplicated per-fund holdings for one quarter-end.

    For each (fund_id, period_of_report) keeps only rows whose filing_date
    equals MAX(filing_date) for that group -- i.e. the latest amendment.
    """
    sql = f"""
        WITH latest AS (
            SELECT fund_id, period_of_report, MAX(filing_date) AS latest_filing
            FROM {holdings_table}
            WHERE period_of_report = ?
            GROUP BY fund_id, period_of_report
        )
        SELECT h.fund_id, f.name AS fund_name, h.filing_date, h.period_of_report,
               h.name_of_issuer, h.ticker, h.ticker_source, h.cusip,
               h.shares, h.market_value, h.title_of_class, h.put_call
        FROM {holdings_table} h
        JOIN latest l
          ON h.fund_id = l.fund_id
         AND h.period_of_report = l.period_of_report
         AND h.filing_date = l.latest_filing
        JOIN {funds_table} f ON f.id = h.fund_id
    """
    df = pd.read_sql_query(sql, conn, params=(period_of_report,))
    # Normalise empty-string ticker to None for consistent null handling.
    if not df.empty:
        df["ticker"] = df["ticker"].where(
            df["ticker"].astype("string").str.len().fillna(0) > 0, None
        )
    return df


# ----- aggregation helpers ----------------------------------------------------

def _position_key(ticker: Optional[str], cusip: str) -> str:
    """Bucket key for per-ticker aggregation.

    When ticker is resolved we key on the ticker (so common + pre-funded
    warrants across different CUSIPs of the same issuer sum together).
    When ticker is null we fall back to the CUSIP prefixed with 'CUSIP:'
    so it cannot collide with a real ticker like 'C' or 'CUSIP'.
    """
    if ticker:
        return ticker
    return f"CUSIP:{cusip}"


def _add_derived_cols(df: pd.DataFrame, rules: list[ClassificationRule]) -> pd.DataFrame:
    """Add share_class and position_key columns to a holdings DataFrame."""
    out = df.copy()
    out["share_class"] = out.apply(
        lambda r: classify_share_type(
            r.get("title_of_class"), r.get("put_call"), rules
        ),
        axis=1,
    )
    out["position_key"] = out.apply(
        lambda r: _position_key(r.get("ticker"), r["cusip"]),
        axis=1,
    )
    return out


def aggregate_fund_positions(
    holdings_df: pd.DataFrame,
    rules: list[ClassificationRule],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Per-(fund_id, position_key) aggregation.

    Returns (fund_positions, dropped_rows, unresolved_positions, flags_by_key).

    - fund_positions: rows where share_class in KEPT_CLASSES, summed per group.
      Columns include shares_combined, market_value_combined, has_* flags.
    - dropped_rows: rows where share_class in DROPPED_CLASSES, unaggregated
      (one row per original position); destined for audit Parquet.
    - unresolved_positions: rows where share_class == 'unknown', unaggregated;
      also destined for audit Parquet. These ARE still included in
      fund_positions (retained as share-equivalent).
    - flags_by_key: one row per position_key with cross-fund any() flags.
      Carries the signal when a fund holds ONLY a dropped class (e.g. puts
      with no common) — that row would otherwise be lost at the rollup.
    """
    if holdings_df.empty:
        empty_fp = pd.DataFrame(columns=[
            "fund_id", "fund_name", "position_key", "ticker", "cusip",
            "name_of_issuer", "ticker_source", "shares_combined",
            "market_value_combined", "has_prefunded_warrants",
            "has_regular_warrants", "has_options", "has_unknown_class",
        ])
        empty_drop = pd.DataFrame(columns=[
            "fund_id", "fund_name", "filing_date", "period_of_report",
            "cusip", "ticker", "name_of_issuer", "title_of_class", "put_call",
            "share_class", "drop_reason", "shares", "market_value",
        ])
        empty_flags = pd.DataFrame(columns=[
            "position_key", "has_prefunded_warrants", "has_regular_warrants",
            "has_options", "has_unknown_class",
        ])
        return empty_fp, empty_drop, empty_drop.copy(), empty_flags

    df = _add_derived_cols(holdings_df, rules)

    # Fund-level flags, computed from the FULL (pre-drop) DataFrame.
    flag_frame = (
        df.groupby(["fund_id", "position_key"], dropna=False)
          .agg(
              has_prefunded_warrants=("share_class", lambda s: (s == "prefunded_warrant").any()),
              has_regular_warrants=("share_class", lambda s: (s == "regular_warrant").any()),
              has_options=("share_class", lambda s: s.isin(["put", "call"]).any()),
              has_unknown_class=("share_class", lambda s: (s == "unknown").any()),
          )
          .reset_index()
    )

    # Cross-fund position-key flags, so a dropped-only (fund_id, key) pair
    # still contributes its signal to the universe row.
    flags_by_key = (
        flag_frame.groupby("position_key", dropna=False)
          .agg(
              has_prefunded_warrants=("has_prefunded_warrants", "any"),
              has_regular_warrants=("has_regular_warrants", "any"),
              has_options=("has_options", "any"),
              has_unknown_class=("has_unknown_class", "any"),
          )
          .reset_index()
    )

    kept_mask = df["share_class"].isin(KEPT_CLASSES)
    dropped_mask = df["share_class"].isin(DROPPED_CLASSES)
    unresolved_mask = df["share_class"] == "unknown"

    kept = df[kept_mask].copy()
    dropped = df[dropped_mask].copy()
    unresolved = df[unresolved_mask].copy()

    # Per-(fund, position_key) aggregation of KEPT rows.
    fund_positions = (
        kept.groupby(["fund_id", "position_key"], dropna=False)
            .agg(
                fund_name=("fund_name", "first"),
                ticker=("ticker", "first"),
                cusip=("cusip", "first"),
                name_of_issuer=("name_of_issuer", _longest_non_null),
                ticker_source=("ticker_source", _best_ticker_source),
                shares_combined=("shares", "sum"),
                market_value_combined=("market_value", "sum"),
            )
            .reset_index()
    )

    # Ensure numeric types don't become float because of NaN in some rows.
    fund_positions["shares_combined"] = fund_positions["shares_combined"].fillna(0).astype("int64")
    fund_positions["market_value_combined"] = (
        fund_positions["market_value_combined"].fillna(0).astype("int64")
    )

    fund_positions = fund_positions.merge(
        flag_frame, on=["fund_id", "position_key"], how="left"
    )
    for col in ("has_prefunded_warrants", "has_regular_warrants",
                "has_options", "has_unknown_class"):
        fund_positions[col] = fund_positions[col].fillna(False).astype(bool)

    # Shape the audit frames.
    dropped_out = _shape_audit_frame(dropped)
    unresolved_out = _shape_audit_frame(unresolved)

    return fund_positions, dropped_out, unresolved_out, flags_by_key


def _longest_non_null(s: pd.Series) -> Optional[str]:
    """Return the longest non-null, non-empty string in s; tie-break alphabetical asc.
    Returns None if every value is null/empty.
    """
    vals = [v for v in s if isinstance(v, str) and v]
    if not vals:
        return None
    return max(vals, key=lambda v: (len(v), v))


def _best_ticker_source(s: pd.Series) -> Optional[str]:
    """Across multiple rows for the same (fund, position_key), return the
    'best' ticker_source for determining verification downstream.

    Preference order: openfigi > manual > sec_name > None. Only one value
    per group usually; this is mostly a defensive aggregator.
    """
    order = {"openfigi": 3, "manual": 2, "sec_name": 1}
    best_v: Optional[str] = None
    best_rank = -1
    for v in s:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        r = order.get(v, 0)
        if r > best_rank:
            best_rank = r
            best_v = v
    return best_v


def _shape_audit_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return audit-ready slice of the holdings frame (for dropped/unresolved)."""
    if df.empty:
        return pd.DataFrame(columns=[
            "fund_id", "fund_name", "filing_date", "period_of_report",
            "cusip", "ticker", "name_of_issuer", "title_of_class", "put_call",
            "share_class", "drop_reason", "shares", "market_value",
        ])
    out = df[[
        "fund_id", "fund_name", "filing_date", "period_of_report",
        "cusip", "ticker", "name_of_issuer", "title_of_class", "put_call",
        "share_class", "shares", "market_value",
    ]].copy()
    out["drop_reason"] = out["share_class"]
    return out[[
        "fund_id", "fund_name", "filing_date", "period_of_report",
        "cusip", "ticker", "name_of_issuer", "title_of_class", "put_call",
        "share_class", "drop_reason", "shares", "market_value",
    ]]


def aggregate_per_ticker(
    current_fund_positions: pd.DataFrame,
    prior_fund_positions: Optional[pd.DataFrame],
    quarter: str,
    current_flags_by_key: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Across-fund aggregation with QoQ metrics. One row per position_key.

    current_flags_by_key carries cross-fund has_* flags from the pre-drop
    frame. Pass it so tickers whose only options position is held by a fund
    that doesn't also hold the common still get has_options=True.
    """
    if current_fund_positions.empty:
        return pd.DataFrame(columns=_UNIVERSE_COLUMNS)

    # Current-quarter totals per ticker.
    current = (
        current_fund_positions.groupby("position_key", dropna=False)
            .agg(
                ticker=("ticker", "first"),
                cusip=("cusip", "first"),
                name_of_issuer=("name_of_issuer", _longest_non_null),
                fund_count=("fund_id", "nunique"),
                total_shares=("shares_combined", "sum"),
                total_market_value=("market_value_combined", "sum"),
                ticker_is_verified=(
                    "ticker_source",
                    lambda s: bool(s.isin(list(_VERIFIED_SOURCES)).any()),
                ),
            )
            .reset_index()
    )
    current["total_shares"] = current["total_shares"].astype("int64")
    current["total_market_value"] = current["total_market_value"].astype("int64")

    # Flags merged from the pre-drop frame (cross-fund any). Fallback to
    # rolling up from fund_positions for back-compat / tests that don't pass
    # the flag frame.
    flag_cols = ("has_prefunded_warrants", "has_regular_warrants",
                 "has_options", "has_unknown_class")
    if current_flags_by_key is not None and not current_flags_by_key.empty:
        current = current.merge(current_flags_by_key, on="position_key", how="left")
        for col in flag_cols:
            current[col] = current[col].fillna(False).astype(bool)
    else:
        fallback = (
            current_fund_positions.groupby("position_key", dropna=False)
                .agg({col: "any" for col in flag_cols})
                .reset_index()
        )
        current = current.merge(fallback, on="position_key", how="left")
        for col in flag_cols:
            current[col] = current[col].fillna(False).astype(bool)

    # QoQ metrics. Nullable-Int64 so missing values remain NA in Parquet.
    if prior_fund_positions is None or prior_fund_positions.empty:
        for col in ("qoq_share_change", "qoq_fund_count_change",
                    "new_positions", "increased_positions",
                    "decreased_positions", "exited_positions"):
            current[col] = pd.array([pd.NA] * len(current), dtype="Int64")
    else:
        prior_ticker = (
            prior_fund_positions.groupby("position_key", dropna=False)
                .agg(
                    prior_fund_count=("fund_id", "nunique"),
                    prior_total_shares=("shares_combined", "sum"),
                )
                .reset_index()
        )
        current = current.merge(prior_ticker, on="position_key", how="left")
        current["prior_total_shares"] = current["prior_total_shares"].fillna(0).astype("int64")
        current["prior_fund_count"] = current["prior_fund_count"].fillna(0).astype("int64")
        current["qoq_share_change"] = (
            current["total_shares"] - current["prior_total_shares"]
        ).astype("Int64")
        current["qoq_fund_count_change"] = (
            current["fund_count"] - current["prior_fund_count"]
        ).astype("Int64")

        # Per-fund deltas: new / increased / decreased / exited.
        cur = current_fund_positions[[
            "fund_id", "position_key", "shares_combined",
        ]].rename(columns={"shares_combined": "cur_shares"})
        pri = prior_fund_positions[[
            "fund_id", "position_key", "shares_combined",
        ]].rename(columns={"shares_combined": "pri_shares"})
        joined = cur.merge(pri, on=["fund_id", "position_key"], how="outer")
        joined["cur_shares"] = joined["cur_shares"].fillna(0).astype("int64")
        joined["pri_shares"] = joined["pri_shares"].fillna(0).astype("int64")
        joined["is_new"] = (joined["pri_shares"] == 0) & (joined["cur_shares"] > 0)
        joined["is_exited"] = (joined["pri_shares"] > 0) & (joined["cur_shares"] == 0)
        joined["is_increased"] = (
            (joined["cur_shares"] > joined["pri_shares"]) & (joined["pri_shares"] > 0)
        )
        joined["is_decreased"] = (
            (joined["cur_shares"] < joined["pri_shares"]) & (joined["cur_shares"] > 0)
        )
        per_key = (
            joined.groupby("position_key", dropna=False)
                  .agg(
                      new_positions=("is_new", "sum"),
                      increased_positions=("is_increased", "sum"),
                      decreased_positions=("is_decreased", "sum"),
                      exited_positions=("is_exited", "sum"),
                  )
                  .reset_index()
        )
        current = current.merge(per_key, on="position_key", how="left")
        for col in ("new_positions", "increased_positions",
                    "decreased_positions", "exited_positions"):
            current[col] = current[col].fillna(0).astype("Int64")

        current = current.drop(columns=["prior_total_shares", "prior_fund_count"])

    current["quarter"] = quarter
    current = current.drop(columns=["position_key"])
    current = current[_UNIVERSE_COLUMNS]

    # Deterministic sort.
    current = current.sort_values(
        by=["fund_count", "total_market_value", "ticker", "cusip"],
        ascending=[False, False, True, True],
        na_position="last",
        kind="stable",
    ).reset_index(drop=True)
    return current


_UNIVERSE_COLUMNS: list[str] = [
    "ticker",
    "cusip",
    "name_of_issuer",
    "fund_count",
    "total_shares",
    "total_market_value",
    "qoq_share_change",
    "qoq_fund_count_change",
    "new_positions",
    "increased_positions",
    "decreased_positions",
    "exited_positions",
    "ticker_is_verified",
    "has_prefunded_warrants",
    "has_regular_warrants",
    "has_options",
    "has_unknown_class",
    "quarter",
]


# ----- output -----------------------------------------------------------------

def write_outputs(
    universe: pd.DataFrame,
    dropped: pd.DataFrame,
    unresolved: pd.DataFrame,
    quarter: str,
    out_dir: Path,
) -> dict[str, Path]:
    """Write all three Parquets. Returns a dict of {name: path}."""
    ensure_dir(out_dir)
    paths = {
        "universe": out_dir / f"universe_{quarter}.parquet",
        "dropped_rows": out_dir / f"dropped_rows_{quarter}.parquet",
        "unresolved_positions": out_dir / f"unresolved_positions_{quarter}.parquet",
    }

    # Attach quarter column to audit frames for downstream ergonomics.
    dropped = dropped.copy()
    unresolved = unresolved.copy()
    dropped["quarter"] = quarter
    unresolved["quarter"] = quarter

    universe.to_parquet(paths["universe"], index=False, engine="pyarrow")
    dropped.to_parquet(paths["dropped_rows"], index=False, engine="pyarrow")
    unresolved.to_parquet(paths["unresolved_positions"], index=False, engine="pyarrow")
    return paths


# ----- orchestrator -----------------------------------------------------------

def build_universe(
    config: PipelineConfig,
    quarter: Optional[str] = None,
) -> pd.DataFrame:
    """End-to-end Module 3 pipeline. Returns the universe DataFrame and
    writes all three Parquets as a side effect.
    """
    # 0. Resolve quarter + dates.
    if quarter is None:
        quarter = resolve_quarter(config)
    target_period = quarter_to_date_end(quarter)
    prior_q = prior_quarter(quarter)
    prior_period = quarter_to_date_end(prior_q)
    log.info(
        "Module 3: quarter=%s target_period=%s prior=%s",
        quarter, target_period, prior_period,
    )

    # 0a. Load classification rules.
    rules_path = PROJECT_ROOT / "config" / "share_types.yaml"
    rules = load_share_type_rules(rules_path)
    log.info("Loaded %d classification rules from %s", len(rules), rules_path)

    # 1-4. Load, classify, split, aggregate current quarter.
    conn = sqlite3.connect(config.paths.fundparser_db)
    try:
        _assert_schema(conn, config.db_schema.holdings_table)
        raw_current = load_holdings_for_period(
            conn,
            config.db_schema.holdings_table,
            config.db_schema.funds_table,
            target_period,
        )
        log.info("Loaded %d current-quarter rows for %s", len(raw_current), target_period)
        if raw_current.empty:
            log.warning(
                "No holdings for quarter=%s period_of_report=%s; writing empty universe",
                quarter, target_period,
            )

        current_fund_positions, dropped, unresolved, current_flags = aggregate_fund_positions(
            raw_current, rules
        )

        # Fatal guard: 100% unknown means schema drift / bad rules.
        if (len(raw_current) > 0
            and len(unresolved) == len(raw_current)):
            sample_titles = sorted(set(
                str(t) for t in raw_current["title_of_class"].dropna()
            ))[:3]
            raise ConfigError(
                f"All {len(raw_current)} rows classified as 'unknown' - "
                f"share_types.yaml may be out of sync with the data. "
                f"Sample title_of_class values: {sample_titles}"
            )

        # 5. Prior-quarter aggregation (optional).
        raw_prior = load_holdings_for_period(
            conn,
            config.db_schema.holdings_table,
            config.db_schema.funds_table,
            prior_period,
        )
        if raw_prior.empty:
            log.info(
                "No prior-quarter holdings (%s); QoQ columns will be null",
                prior_period,
            )
            prior_fund_positions = None
        else:
            prior_fund_positions, _, _, _ = aggregate_fund_positions(raw_prior, rules)
    finally:
        conn.close()

    # 6. Across-fund aggregation + QoQ.
    universe = aggregate_per_ticker(
        current_fund_positions, prior_fund_positions, quarter,
        current_flags_by_key=current_flags,
    )
    log.info(
        "Universe built: %d tickers, %d dropped rows, %d unresolved rows",
        len(universe), len(dropped), len(unresolved),
    )

    # 7. Write outputs.
    out_dir = config.paths.intermediate_outputs_dir
    paths = write_outputs(universe, dropped, unresolved, quarter, out_dir)
    for name, path in paths.items():
        log.info("Wrote %s -> %s", name, path)

    return universe


def _assert_schema(conn: sqlite3.Connection, holdings_table: str) -> None:
    cols = {row[1] for row in conn.execute(
        f"PRAGMA table_info({holdings_table})"
    ).fetchall()}
    missing = {"title_of_class", "put_call"} - cols
    if missing:
        raise ConfigError(
            f"{holdings_table} is missing columns {sorted(missing)} - "
            "run Module 2 schema extension (ALTER TABLE in db.py) first"
        )
