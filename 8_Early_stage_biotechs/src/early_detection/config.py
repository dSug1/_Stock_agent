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
    mktcap_ceiling_usd: float = 3_000_000_000.0       # $3B ceiling — the thesis is small/micro-cap (matches M6's band); flag, not delete
    sic_allow: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SIC_ALLOW))
    markets: tuple[str, ...] = ("US", "CA")           # Phase 1 scope
    gleif_enrich: bool = False                        # best-effort LEI enrich (phase1 §8 Q1); off by default
    user_agent: str = ""                              # SEC requires a UA w/ email; read from env at client init
    # Phase-2 capital-markets signal (spec §3.5): material EDGAR forms + lookback window. Includes
    # foreign-private-issuer (FPI) forms so cross-listed non-US names (e.g. TSX-listed Satellos, which
    # files 6-K/Form-D/F-10/SCHEDULE-13G with the SEC) get a capital signal — they were invisible when
    # only US-domestic forms were listed. Routine FPI annuals (40-F/20-F) are DELIBERATELY excluded:
    # every FPI files one, so they'd satisfy the pre-filter's capital gate trivially without conviction.
    material_forms: tuple[str, ...] = (
        # 5%+ ownership crossings — BOTH the legacy and the 2024-25 modernized labels (SEC relabeled
        # SC 13x → SCHEDULE 13x; the old labels return nothing for recent filings — the M8 finding).
        "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
        "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
        "4",                                           # insider transactions
        "8-K", "6-K",                                  # material events (6-K = FPI equivalent of 8-K)
        "S-1", "S-3", "424B5", "424B3",                # US registration / shelf / ATM raises
        "F-1", "F-3", "F-10", "F-10/A",                # FPI registration / shelf / offering
        "D", "D/A",                                    # Reg D private placements / financings
    )
    signal_lookback_days: int = 180                   # only ingest filings this recent
    # Phase-2 literature/citation signal (spec §3.1, OpenAlex). mailto → polite pool (better rate limit).
    openalex_mailto: str = ""                          # a contact email; falls back to the .env USER_AGENT email
    literature_pub_years: int = 4                      # recent-publication window
    literature_citation_years: int = 6                 # window for citations of the foundational paper
    literature_max_citing: int = 200                   # cap citing-works fetched per foundational paper
    # Specialist healthcare/biotech fund watchlist (spec §3.5) — a 5%+ crossing (SC 13D/G) by one of
    # these is the headline capital-markets signal. Queried against EDGAR full-text (efts). Names are
    # matched case/normalization-insensitively against filing display_names.
    # Phase-2 Claude founder-lineage extraction (spec §5.1) — cheap tier + hard spend cap.
    extraction_model: str = "claude-haiku-4-5"        # cheap tier per §5.5 (basic web_search variant)
    extraction_prompt_version: str = "v1"             # bump to re-open every entity (skip-cache key)
    extraction_max_output_tokens: int = 6000          # full response — undersizing truncates JSON → dropped
    extraction_web_search_max_uses: int = 4           # bounded so the server tool loop finishes in one call
    max_usd_per_run: float = 5.0                       # hard guard on any single dispatch
    cost_calibration_factor: float = 0.10             # scales script-computed cost → actual invoice [[cost calib]]
    # §5.4 stack-convergence scoring (the expensive, high-value call — full model, pre-filtered candidates)
    scoring_model: str = "claude-sonnet-5"            # stronger tier than extraction (§5.5)
    scoring_prompt_version: str = "v1"
    scoring_max_output_tokens: int = 4000
    prefilter_min_independent: int = 1                # ≥ this many independent-lab citations to qualify
    # §3.2 clinical-trials signal (ClinicalTrials.gov v2, free, no key). Clinical stage is an INDEPENDENT
    # convergence dimension that doesn't depend on the OpenAlex author-resolution trail.
    clinical_max_studies: int = 100                   # cap studies fetched per company (paginated)
    # §3.4 regulatory-designation signal (FDA + cross-listed foreign): phrase-first EDGAR full-text.
    # A designation is durable (doesn't expire like a 13D), so the lookback is years, not days.
    designation_lookback_days: int = 1460             # ~4 years
    designation_forms: str = "8-K,6-K,424B5,424B4,S-1,F-1"   # material-event + prospectus disclosures
    # phrase → normalized designation type. Exact-phrase full-text match (efts phrase-quotes it).
    designation_phrases: dict = field(default_factory=lambda: {
        "Breakthrough Therapy Designation": "breakthrough",
        "Fast Track designation": "fast_track",
        "Orphan Drug Designation": "orphan",
        "Regenerative Medicine Advanced Therapy": "rmat",
        "Rare Pediatric Disease Designation": "rare_pediatric",
    })
    # Optional pre-filter widening: when > 0, a candidate ALSO clears the pre-filter if it has a
    # company-led trial at ≥ this phase (1..4) + a capital signal — even without an independent citation.
    # Default 0 = OFF (behavior unchanged: independent-citation gate only). Set e.g. 2 to admit
    # clinical-stage names the literature trail misses.
    prefilter_clinical_min_phase: int = 0

    specialist_funds: tuple[str, ...] = (
        "Baker Bros", "RA Capital", "OrbiMed", "Perceptive Advisors", "BVF Partners",
        "Cormorant Asset Management", "EcoR1 Capital", "Deep Track Capital", "Avoro Capital",
        "Vivo Capital", "Redmile Group", "Venrock", "Sofinnova", "Forbion", "Andera Partners",
        "HealthCap", "Bain Capital Life Sciences", "Foresite Capital", "Logos Capital",
    )

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
    specialist_funds = tuple(raw.get("specialist_funds") or defaults.specialist_funds)

    extraction_kwargs = dict(
        extraction_model=str(raw.get("extraction_model", defaults.extraction_model)),
        extraction_prompt_version=str(raw.get("extraction_prompt_version", defaults.extraction_prompt_version)),
        extraction_max_output_tokens=int(raw.get("extraction_max_output_tokens", defaults.extraction_max_output_tokens)),
        extraction_web_search_max_uses=int(raw.get("extraction_web_search_max_uses", defaults.extraction_web_search_max_uses)),
        max_usd_per_run=float(raw.get("max_usd_per_run", defaults.max_usd_per_run)),
        cost_calibration_factor=float(raw.get("cost_calibration_factor", defaults.cost_calibration_factor)),
        scoring_model=str(raw.get("scoring_model", defaults.scoring_model)),
        scoring_prompt_version=str(raw.get("scoring_prompt_version", defaults.scoring_prompt_version)),
        scoring_max_output_tokens=int(raw.get("scoring_max_output_tokens", defaults.scoring_max_output_tokens)),
        prefilter_min_independent=int(raw.get("prefilter_min_independent", defaults.prefilter_min_independent)),
        clinical_max_studies=int(raw.get("clinical_max_studies", defaults.clinical_max_studies)),
        prefilter_clinical_min_phase=int(raw.get("prefilter_clinical_min_phase", defaults.prefilter_clinical_min_phase)),
        designation_lookback_days=int(raw.get("designation_lookback_days", defaults.designation_lookback_days)),
        designation_forms=str(raw.get("designation_forms", defaults.designation_forms)),
        designation_phrases=dict(raw.get("designation_phrases") or defaults.designation_phrases),
    )

    return Config(
        db_path=db_path,
        m6_store_path=m6_path,
        mktcap_floor_usd=float(raw.get("mktcap_floor_usd", 10_000_000.0)),
        mktcap_ceiling_usd=float(raw.get("mktcap_ceiling_usd", 3_000_000_000.0)),
        sic_allow=sic_allow,
        markets=markets,
        gleif_enrich=bool(raw.get("gleif_enrich", False)),
        user_agent=str(raw.get("user_agent", "")),
        material_forms=material_forms,
        signal_lookback_days=int(raw.get("signal_lookback_days", defaults.signal_lookback_days)),
        specialist_funds=specialist_funds,
        openalex_mailto=str(raw.get("openalex_mailto", defaults.openalex_mailto)),
        literature_pub_years=int(raw.get("literature_pub_years", defaults.literature_pub_years)),
        literature_citation_years=int(raw.get("literature_citation_years", defaults.literature_citation_years)),
        literature_max_citing=int(raw.get("literature_max_citing", defaults.literature_max_citing)),
        **extraction_kwargs,
    )
