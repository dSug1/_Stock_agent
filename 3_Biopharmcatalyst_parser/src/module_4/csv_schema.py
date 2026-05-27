"""Pydantic schema for one row of the BPC insider supplement CSV.

Field-level rules in spec §6.4. Same `mode='before'` coercion pattern
as Module 1: pydantic strict mode rejects silent type coercion, so we
do every string→native conversion explicitly per field.

The real `_csv_source/insider_data3.csv` is cleaner than the catalyst
CSV — `Buy/Sell` is only ever `Buy` or `Sell`, `Stock/Option` only
`Stock` or `Option`, only `Insider Position` (~28%) has blanks. No
real-data calibration deviations vs the spec.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator


# Spec §6.3: exactly these 13 columns, case-sensitive, any order.
EXPECTED_COLUMNS: frozenset[str] = frozenset({
    "Ticker", "Name", "Insider Name", "Insider Position", "Filing Date",
    "Buy/Sell", "Stock/Option", "Shares", "Shares Change", "Trade Price",
    "Cost", "Final Share", "No Of Shares",
})

CSV_TO_FIELD: dict[str, str] = {
    "Ticker":           "ticker",
    "Name":             "name",
    "Insider Name":     "insider_name",
    "Insider Position": "insider_position",
    "Filing Date":      "filing_date",
    "Buy/Sell":         "buy_sell",
    "Stock/Option":     "stock_or_option",
    "Shares":           "shares",
    "Shares Change":    "shares_change_pct",
    "Trade Price":      "trade_price",
    "Cost":             "cost",
    "Final Share":      "final_shares",
    "No Of Shares":     "no_of_shares",
}


def _blank_to_none(v: Any) -> Any | None:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


class InsiderRow(BaseModel):
    """One validated row from a BPC insider supplement CSV."""

    model_config = ConfigDict(strict=True, extra="forbid")

    ticker: str
    name: str | None
    insider_name: str
    insider_position: str | None
    filing_date: date
    buy_sell: Literal["Buy", "Sell"]
    stock_or_option: Literal["Stock", "Option"]
    shares: float
    shares_change_pct: float
    trade_price: float
    cost: float
    final_shares: int
    no_of_shares: int

    # ---- required string fields --------------------------------------
    @field_validator("ticker", mode="before")
    @classmethod
    def _v_ticker(cls, v: Any) -> str:
        if v is None or str(v).strip() == "":
            raise ValueError("Ticker is required")
        return str(v).strip().upper()

    @field_validator("insider_name", mode="before")
    @classmethod
    def _v_insider_name(cls, v: Any) -> str:
        if v is None or str(v).strip() == "":
            raise ValueError("Insider Name is required and must be non-empty")
        return str(v).strip()

    # ---- optional string fields (blank → None) -----------------------
    @field_validator("name", "insider_position", mode="before")
    @classmethod
    def _v_opt_str(cls, v: Any) -> str | None:
        v = _blank_to_none(v)
        return None if v is None else str(v)

    # ---- enum-like string fields -------------------------------------
    @field_validator("buy_sell", mode="before")
    @classmethod
    def _v_buy_sell(cls, v: Any) -> str:
        if v is None:
            raise ValueError("Buy/Sell is required")
        s = str(v).strip()
        if s not in ("Buy", "Sell"):
            raise ValueError(f"Buy/Sell must be 'Buy' or 'Sell', got {s!r}")
        return s

    @field_validator("stock_or_option", mode="before")
    @classmethod
    def _v_stock_or_option(cls, v: Any) -> str:
        if v is None:
            raise ValueError("Stock/Option is required")
        s = str(v).strip()
        if s not in ("Stock", "Option"):
            raise ValueError(f"Stock/Option must be 'Stock' or 'Option', got {s!r}")
        return s

    # ---- date --------------------------------------------------------
    @field_validator("filing_date", mode="before")
    @classmethod
    def _v_filing_date(cls, v: Any) -> date:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            raise ValueError("Filing Date is required")
        if isinstance(v, date):
            return v
        s = str(v).strip()
        try:
            return date.fromisoformat(s)
        except ValueError as e:
            raise ValueError(
                f"Filing Date {s!r} does not parse as YYYY-MM-DD"
            ) from e

    # ---- numerics ----------------------------------------------------
    @field_validator(
        "shares", "shares_change_pct", "trade_price", "cost", mode="before",
    )
    @classmethod
    def _v_float(cls, v: Any) -> float:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            raise ValueError("required float field is empty")
        try:
            return float(str(v).replace(",", "").strip())
        except (TypeError, ValueError) as e:
            raise ValueError(f"could not parse as float: {v!r}") from e

    @field_validator("final_shares", "no_of_shares", mode="before")
    @classmethod
    def _v_int(cls, v: Any) -> int:
        if v is None or (isinstance(v, str) and v.strip() == ""):
            raise ValueError("required int field is empty")
        try:
            return int(float(str(v).replace(",", "").strip()))
        except (TypeError, ValueError) as e:
            raise ValueError(f"could not parse as int: {v!r}") from e
