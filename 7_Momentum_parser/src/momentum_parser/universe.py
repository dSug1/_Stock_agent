"""Stage 0 — load the ticker universe to screen.

Scaffold source is a hand-maintained CSV (``config/universe.csv``: ``ticker,name``). The other modules
each grew from a seed CSV the same way; later this can pull from 6_Biotech_platform_discoverer's
shortlist, 2_Funds_parser holdings, or 5_Hype_parser discovered themes (see ROADMAP).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import NamedTuple

from .config import ROOT


class UniverseRow(NamedTuple):
    ticker: str
    name: str


def load_universe(cfg: dict, store=None) -> list[UniverseRow]:
    """The live scoring universe: prefer the Stage-0b gate-pass list (Decision C) when present, else the
    seed CSV (warm-start / fallback before discovery+gate have run)."""
    if store is not None:
        inv = store.investable_tickers()
        if inv:
            return [UniverseRow(t, "") for t in inv]
    rel = cfg.get("universe", {}).get("seed_csv") or cfg.get("universe", {}).get("csv", "config/universe.csv")
    path = ROOT / rel
    rows: list[UniverseRow] = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8", newline="") as fh:
        for rec in csv.DictReader(fh):
            t = (rec.get("ticker") or "").strip().upper()
            if t and not t.startswith("#"):
                rows.append(UniverseRow(t, (rec.get("name") or "").strip()))
    return rows
