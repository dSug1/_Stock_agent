"""Top-down signal taxonomy loader + validator — Decision L / spec §4b (v0.4).

Priors ONLY. The live weights `w_s` and per-ticker loadings `beta_{t,s}` are LEARNED by the feedback
loop and live in the store (`signal_weights` / `ticker_loadings`), never in the YAML (invariant §12).
Loaded with ``yaml.safe_load`` (repo security invariant); path is config-overridable so tests never
depend on the shipped file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import ROOT

CLASSES = {"monetary", "geopolitical", "cross_asset", "rotation", "ticker_catalyst"}
SCOPES = {"market", "sector", "factor", "ticker"}
SCHEDULES = {"dated", "continuous", "probabilistic"}


@dataclass(frozen=True)
class SignalSpec:
    """One taxonomy row (a market-perturbation signal). ``w_prior`` is a starting weight, not the live one."""

    id: str
    cls: str
    scope: str
    schedule: str
    w_prior: float
    learnable: bool = True
    anticipation: str = ""


def taxonomy_path(cfg: dict | None = None) -> Path:
    override = (cfg or {}).get("topdown", {}).get("taxonomy_file")
    return Path(override) if override else ROOT / "config" / "signals_taxonomy.yaml"


def load_taxonomy(cfg: dict | None = None) -> dict:
    """Parse + validate the taxonomy. Returns ``{version, signals: [SignalSpec], learning: {...}}``."""
    with open(taxonomy_path(cfg), "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    signals = [_parse(row) for row in (data.get("signals") or [])]
    _validate(signals)
    return {"version": data.get("version", 1), "signals": signals,
            "learning": data.get("learning", {}) or {}}


def _parse(row: dict) -> SignalSpec:
    try:
        return SignalSpec(
            id=str(row["id"]), cls=str(row["class"]), scope=str(row["scope"]),
            schedule=str(row["schedule"]), w_prior=float(row["w_prior"]),
            learnable=bool(row.get("learnable", True)), anticipation=str(row.get("anticipation", "")),
        )
    except KeyError as e:
        raise ValueError(f"taxonomy signal missing required field {e}") from e


def _validate(signals: list[SignalSpec]) -> None:
    if not signals:
        raise ValueError("taxonomy has no signals")
    seen: set[str] = set()
    for s in signals:
        if s.id in seen:
            raise ValueError(f"duplicate signal id: {s.id}")
        seen.add(s.id)
        if s.cls not in CLASSES:
            raise ValueError(f"{s.id}: bad class {s.cls!r} (allowed {sorted(CLASSES)})")
        if s.scope not in SCOPES:
            raise ValueError(f"{s.id}: bad scope {s.scope!r} (allowed {sorted(SCOPES)})")
        if s.schedule not in SCHEDULES:
            raise ValueError(f"{s.id}: bad schedule {s.schedule!r} (allowed {sorted(SCHEDULES)})")
        if not (0.0 <= s.w_prior <= 1.0):
            raise ValueError(f"{s.id}: w_prior {s.w_prior} out of [0,1]")


def regime_buckets(taxonomy: dict) -> list[str]:
    """Weight buckets to seed: 'all' (the always-present fallback) + per-regime when regime_conditional."""
    learning = taxonomy.get("learning", {})
    regimes = list(learning.get("regimes", [])) if learning.get("regime_conditional") else []
    return ["all"] + regimes
