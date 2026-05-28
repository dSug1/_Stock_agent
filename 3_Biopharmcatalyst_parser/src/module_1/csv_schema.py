"""Pydantic schema for a single BPC FDA-calendar CSV row.

Implements the field-level validation rules in spec §3.4. Header
validation lives in ingest.py.

Coercion happens in `mode='before'` validators so the model fields can
be strictly typed (`int | None`, `date | None`, …) under `strict=True`.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, field_validator

log = logging.getLogger(__name__)


# Spec §3.3: exactly these 19 columns, case-sensitive, any order.
EXPECTED_COLUMNS: frozenset[str] = frozenset({
    "Ticker", "Name", "Price", "30 Day Price Change", "Drug", "NCT Number",
    "Indication", "Stage", "Status", "Next Catalyst", "Catalyst Date",
    "Catalyst", "Conference", "Historical LOA", "Historical POP",
    "Bullish or Bearish", "Market Cap", "Last Updated", "No Of Shares",
})

# CSV column → model field name.
CSV_TO_FIELD: dict[str, str] = {
    "Ticker": "ticker",
    "Name": "name",
    "Price": "price",
    "30 Day Price Change": "price_history_30d",
    "Drug": "drug",
    "NCT Number": "nct_number",
    "Indication": "indication",
    "Stage": "stage",
    "Status": "status",
    "Next Catalyst": "next_catalyst_type",
    "Catalyst Date": "catalyst_date",
    "Catalyst": "catalyst_text",
    "Conference": "conference",
    "Historical LOA": "historical_loa",
    "Historical POP": "historical_pop",
    "Bullish or Bearish": "sentiment",
    "Market Cap": "market_cap_usd",
    "Last Updated": "bpc_last_updated",
    "No Of Shares": "no_of_shares",
}

_STAGE_RE = re.compile(r"^phase[0-5]$")

# Sentinels BPC uses to mean "not applicable / no data". Treated as blank
# everywhere `_blank_to_none` is called. The em dash (U+2014) is BPC's
# convention for "not relevant" in big-pharma rows (no historical LOA/POP).
_BLANK_SENTINELS: frozenset[str] = frozenset({"", "—", "-", "n/a", "N/A", "NA"})


def _blank_to_none(v: Any) -> Any | None:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() in _BLANK_SENTINELS:
        return None
    return v


class CatalystRow(BaseModel):
    """One validated row from a BPC FDA-calendar CSV download."""

    model_config = ConfigDict(strict=True, extra="forbid")

    ticker: str
    name: str | None
    price: float | None
    price_history_30d: str | None
    drug: str
    nct_number: str  # never NULL — '' when blank in source
    indication: str | None
    stage: str  # already normalized to lowercase, matches ^phase[0-5]$
    status: str | None
    next_catalyst_type: str  # required, non-empty
    catalyst_date: date | None
    catalyst_text: str | None
    conference: str | None
    historical_loa: float | None
    historical_pop: float | None
    sentiment: str | None
    market_cap_usd: float | None
    bpc_last_updated: datetime | None
    no_of_shares: int | None

    # ---- required string fields --------------------------------------
    @field_validator("ticker", mode="before")
    @classmethod
    def _v_ticker(cls, v: Any) -> str:
        if v is None or str(v).strip() == "":
            raise ValueError("Ticker is required")
        return str(v).strip().upper()

    @field_validator("drug", mode="before")
    @classmethod
    def _v_drug(cls, v: Any) -> str:
        # Spec §3.4: required; empty → ''
        return "" if v is None else str(v).strip()

    @field_validator("nct_number", mode="before")
    @classmethod
    def _v_nct(cls, v: Any) -> str:
        # Spec §3.4: empty → '' (never NULL — PK column).
        return "" if v is None else str(v).strip()

    @field_validator("next_catalyst_type", mode="before")
    @classmethod
    def _v_next_catalyst(cls, v: Any) -> str:
        # Spec §3.4 originally said "required, non-empty", but the real
        # BPC v3 CSV has ~45 rows where Next Catalyst is blank (big-pharma
        # pipeline trackers without a specific next-event tag). Treat
        # blank as '' so those rows survive; PK uniqueness still holds
        # because (snapshot_date, ticker, drug, nct_number) is enough
        # to discriminate. See decisions.md D2.
        return "" if v is None else str(v).strip()

    @field_validator("stage", mode="before")
    @classmethod
    def _v_stage(cls, v: Any) -> str:
        if v is None:
            raise ValueError("Stage is required")
        s = str(v).strip().lower()
        if not _STAGE_RE.match(s):
            raise ValueError(f"Stage {s!r} does not match ^phase[0-5]$")
        return s

    # ---- optional string fields (blank → None) -----------------------
    @field_validator(
        "name", "price_history_30d", "indication", "status",
        "catalyst_text", "conference", "sentiment", mode="before",
    )
    @classmethod
    def _v_opt_str(cls, v: Any) -> str | None:
        v = _blank_to_none(v)
        return None if v is None else str(v)

    # ---- numeric fields -----------------------------------------------
    @field_validator("price", "market_cap_usd", mode="before")
    @classmethod
    def _v_opt_float(cls, v: Any) -> float | None:
        v = _blank_to_none(v)
        if v is None:
            return None
        try:
            return float(str(v).strip())
        except (TypeError, ValueError) as e:
            raise ValueError(f"could not parse as float: {v!r}") from e

    @field_validator("historical_loa", "historical_pop", mode="before")
    @classmethod
    def _v_pct_0_100(cls, v: Any) -> float | None:
        v = _blank_to_none(v)
        if v is None:
            return None
        try:
            f = float(str(v).strip())
        except (TypeError, ValueError) as e:
            raise ValueError(f"could not parse as float: {v!r}") from e
        if not (0.0 <= f <= 100.0):
            raise ValueError(f"value {f} outside [0, 100]")
        return f

    @field_validator("no_of_shares", mode="before")
    @classmethod
    def _v_opt_int(cls, v: Any) -> int | None:
        v = _blank_to_none(v)
        if v is None:
            return None
        try:
            # Tolerate "1,000,000" and "5.0"
            return int(float(str(v).replace(",", "").strip()))
        except (TypeError, ValueError) as e:
            raise ValueError(f"could not parse as int: {v!r}") from e

    # ---- date / timestamp fields -------------------------------------
    # BPC has shipped at least two CSV format generations: v3 used
    # DD/MM/YYYY and DD/MM/YYYY HH:MM; v4 (2026-05-28+) uses ISO
    # YYYY-MM-DD and YYYY-MM-DD HH:MM:SS. Try each in order; first
    # match wins. See decisions.md D11.
    _DATE_FORMATS: ClassVar[tuple[str, ...]] = ("%d/%m/%Y", "%Y-%m-%d")
    _TIMESTAMP_FORMATS: ClassVar[tuple[str, ...]] = (
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
    )

    @field_validator("catalyst_date", mode="before")
    @classmethod
    def _v_catalyst_date(cls, v: Any) -> date | None:
        v = _blank_to_none(v)
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.date()
        if isinstance(v, date):
            return v
        s = str(v).strip()
        for fmt in cls._DATE_FORMATS:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        # Spec §3.4: unparseable → NULL + warn (NOT row-rejecting).
        log.warning("Catalyst Date %r does not parse as any known format; storing NULL", s)
        return None

    @field_validator("bpc_last_updated", mode="before")
    @classmethod
    def _v_last_updated(cls, v: Any) -> datetime | None:
        v = _blank_to_none(v)
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        s = str(v).strip()
        for fmt in cls._TIMESTAMP_FORMATS:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        log.warning("Last Updated %r does not parse as any known format; storing NULL", s)
        return None
