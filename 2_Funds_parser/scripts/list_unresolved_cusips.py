"""List CUSIPs that still have no ticker for the given quarter.

Reads the Module 3 universe parquet. Emits a deduped list of CUSIPs
(with issuer names, fund counts, shares, dollar exposure) to stdout AND
to _intermediate_outputs/unresolved_cusips_{quarter}.tsv so the user can
paste it into an external AI tool for ticker lookup.

Usage:
    python 2_Funds_parser/scripts/list_unresolved_cusips.py
    python 2_Funds_parser/scripts/list_unresolved_cusips.py --quarter 2025Q4
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pandas as pd  # noqa: E402

from module_1 import ConfigError, ensure_dir, load_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quarter",
        default=None,
        help="Quarter in YYYYQn format. Default: pipeline.yaml resolved quarter.",
    )
    args = parser.parse_args()

    config = load_config()
    quarter = args.quarter or config.quarter
    universe_path = (
        config.paths.intermediate_outputs_dir / f"universe_{quarter}.parquet"
    )
    if not universe_path.exists():
        raise ConfigError(
            f"universe parquet not found at {universe_path} — "
            "run 3_build_universe.py first"
        )

    universe = pd.read_parquet(universe_path)
    unresolved = universe[universe["ticker"].isna()].copy()
    if unresolved.empty:
        print(f"No unresolved CUSIPs in universe_{quarter}.parquet. Nothing to do.")
        return 0

    # The universe is already cross-fund aggregated, so one row per CUSIP
    # (since position_key='CUSIP:<cusip>' for unresolved rows).
    # Collect every name_of_issuer variant across funds from the DB so the
    # user has more to work with than a single longest-name pick.
    conn = sqlite3.connect(config.paths.fundparser_db)
    try:
        cusips = unresolved["cusip"].tolist()
        placeholders = ",".join(["?"] * len(cusips))
        name_rows = conn.execute(
            f"SELECT DISTINCT cusip, name_of_issuer "
            f"FROM {config.db_schema.holdings_table} "
            f"WHERE cusip IN ({placeholders}) AND ticker IS NULL",
            cusips,
        ).fetchall()
    finally:
        conn.close()

    names_by_cusip: dict[str, list[str]] = {}
    for cusip, name in name_rows:
        if not name:
            continue
        names_by_cusip.setdefault(cusip, [])
        if name not in names_by_cusip[cusip]:
            names_by_cusip[cusip].append(name)

    unresolved["name_variants"] = unresolved["cusip"].map(
        lambda c: " | ".join(names_by_cusip.get(c, []))
    )

    out_cols = [
        "cusip", "name_variants", "fund_count",
        "total_shares", "total_market_value",
    ]
    tsv = unresolved[out_cols].sort_values(
        by=["fund_count", "total_market_value"], ascending=[False, False]
    ).reset_index(drop=True)

    ensure_dir(config.paths.intermediate_outputs_dir)
    out_path = (
        config.paths.intermediate_outputs_dir / f"unresolved_cusips_{quarter}.tsv"
    )
    tsv.to_csv(out_path, sep="\t", index=False)

    print(tsv.to_string(index=False))
    print()
    print(f"Wrote {len(tsv)} rows to {out_path}")
    print(
        "Paste the TSV into an external AI tool, ask for tickers, save as a "
        "two-column TSV (cusip\\tticker), then run "
        "apply_manual_ticker_mappings.py --input <file>."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
