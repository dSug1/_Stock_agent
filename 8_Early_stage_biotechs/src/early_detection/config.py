"""Configuration loading + resolved paths (Phase 1).

Loads ``config/config.yaml`` (``yaml.safe_load`` only — repo security discipline) layered over
built-in defaults, and resolves component-relative paths. Kept deliberately small in Phase 1; grows
as signal/scoring config lands in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Component root = .../8_Early_stage_biotechs (two parents up from this file's src/early_detection/).
COMPONENT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = COMPONENT_ROOT / "config"
DATA_DIR = COMPONENT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "early_detection.db"

# US SIC codes admitted into the biotech/pharma/life-sciences universe (spec §2.2 / phase1 §3.2).
DEFAULT_SIC_ALLOW = {
    "2834": "therapeutics",      # pharmaceutical preparations
    "2836": "therapeutics",      # biological products (except diagnostic)
    "8731": "tools_platform",    # commercial physical & biological research
    "3826": "tools_platform",    # laboratory analytical instruments
    "3841": "devices",           # surgical & medical instruments
}

# Module 6 store, read READ-ONLY as the existing-universe priority tier (decision D2).
DEFAULT_M6_STORE = COMPONENT_ROOT.parent / "6_Biotech_platform_discoverer" / "data" / "store.db"


@dataclass(frozen=True)
class Config:
    """Resolved Phase-1 configuration."""

    db_path: Path = DEFAULT_DB_PATH
    m6_store_path: Path = DEFAULT_M6_STORE
    mktcap_floor_usd: float = 10_000_000.0            # operator decision: $10M floor
    sic_allow: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SIC_ALLOW))
    markets: tuple[str, ...] = ("US", "CA")           # Phase 1 scope
    gleif_enrich: bool = False                        # best-effort LEI enrich (phase1 §8 Q1); off by default
    user_agent: str = ""                              # SEC requires a UA w/ email; read from env at client init
    # Phase-2 capital-markets signal (spec §3.5): material EDGAR forms + lookback window.
    material_forms: tuple[str, ...] = (
        "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",   # 5%+ ownership crossings
        "4",                                           # insider transactions
        "8-K",                                          # material events (designations, deals)
        "S-1", "S-3", "424B5", "424B3",                # registration / shelf / ATM raises
    )
    signal_lookback_days: int = 180                   # only ingest filings this recent

    @property
    def sic_codes(self) -> set[str]:
        return set(self.sic_allow)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config file {path} did not parse to a mapping")
    return data


def load_config(config_path: Path | None = None) -> Config:
    """Load ``config/config.yaml`` over defaults. Missing file → all defaults (Phase 1 needs none)."""
    raw = _read_yaml(config_path or (CONFIG_DIR / "config.yaml"))

    db_path = Path(raw["db_path"]).expanduser() if raw.get("db_path") else DEFAULT_DB_PATH
    m6_path = Path(raw["m6_store_path"]).expanduser() if raw.get("m6_store_path") else DEFAULT_M6_STORE
    sic_allow = dict(raw.get("sic_allow") or DEFAULT_SIC_ALLOW)
    markets = tuple(raw.get("markets") or ("US", "CA"))

    defaults = Config()
    material_forms = tuple(raw.get("material_forms") or defaults.material_forms)

    return Config(
        db_path=db_path,
        m6_store_path=m6_path,
        mktcap_floor_usd=float(raw.get("mktcap_floor_usd", 10_000_000.0)),
        sic_allow=sic_allow,
        markets=markets,
        gleif_enrich=bool(raw.get("gleif_enrich", False)),
        user_agent=str(raw.get("user_agent", "")),
        material_forms=material_forms,
        signal_lookback_days=int(raw.get("signal_lookback_days", defaults.signal_lookback_days)),
    )
