"""Module 6 — scoring.yaml loader + pydantic validation."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6.config import default_config_path, load_scoring_config  # noqa: E402


def test_real_scoring_yaml_loads():
    cfg = load_scoring_config(default_config_path())
    assert cfg.rules_version_label == "v1.0"
    assert cfg.rules_version.startswith("v1.0:")
    assert len(cfg.rules_version.split(":")[1]) == 7

    assert cfg.hard_filters.H1.mcap_min_usd == 30_000_000
    assert cfg.hard_filters.H1.mcap_max_usd == 2_000_000_000
    assert "phase1" in cfg.hard_filters.H5.allowed_stages
    assert "Conference Presentation" in cfg.hard_filters.H5.allowed_catalyst_types

    assert cfg.insider.lookback_days == 365
    assert cfg.insider.role_weights == {"CEO": 2.0, "CFO": 1.0}

    assert cfg.composite.weight_insider == 0.35
    assert cfg.composite.weight_momentum == 0.35
    assert cfg.composite.weight_funds == 0.30


def test_rules_version_changes_on_yaml_edit(tmp_path: Path):
    p = tmp_path / "scoring.yaml"
    real = default_config_path().read_text(encoding="utf-8")
    p.write_text(real, encoding="utf-8")
    cfg_a = load_scoring_config(p)
    # mutate one byte (a comment line)
    p.write_text(real + "\n# extra comment\n", encoding="utf-8")
    cfg_b = load_scoring_config(p)
    assert cfg_a.rules_version != cfg_b.rules_version


def _minimal_yaml() -> str:
    return textwrap.dedent("""
        rules_version_label: v1.0
        hard_filters:
          H1: { mcap_min_usd: 30000000, mcap_max_usd: 2000000000 }
          H3: { window_start_days: 14 }
          H5:
            allowed_stages: [phase1]
            allowed_catalyst_types: [Interim Data]
        timing_buckets:
          catalyst_date_defined: [specific]
          catalyst_date_undefined: [year]
        insider:
          lookback_days: 365
          role_weights: { CEO: 2.0, CFO: 1.0 }
          normalisation_cap_weighted_usd: 5000000
        momentum:
          curve:
            - { return_pct: -10, score: 0 }
            - { return_pct:  10, score: 100 }
          null_score: 50
        funds:
          db_path_relative_to_repo_root: 2_Funds_parser/2_fundparser.db
          normalisation_cap_usd: 50000000
          stale_warning_days: 180
        composite:
          weight_insider:  0.35
          weight_momentum: 0.35
          weight_funds:    0.30
          tiebreaker_chain: [insider_score, fund_accumulation_score]
    """).strip()


def test_minimal_yaml_validates(tmp_path: Path):
    p = tmp_path / "scoring.yaml"
    p.write_text(_minimal_yaml(), encoding="utf-8")
    cfg = load_scoring_config(p)
    assert cfg.composite.weight_insider == 0.35


def test_weights_must_sum_to_one(tmp_path: Path):
    bad = _minimal_yaml().replace("weight_funds:    0.30", "weight_funds:    0.50")
    p = tmp_path / "scoring.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scoring_config(p)


def test_timing_buckets_must_be_disjoint(tmp_path: Path):
    bad = _minimal_yaml().replace(
        "catalyst_date_undefined: [year]",
        "catalyst_date_undefined: [specific, year]",
    )
    p = tmp_path / "scoring.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scoring_config(p)


def test_momentum_curve_must_be_ascending(tmp_path: Path):
    bad = _minimal_yaml().replace(
        "- { return_pct: -10, score: 0 }",
        "- { return_pct:  50, score: 0 }",
    )
    p = tmp_path / "scoring.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scoring_config(p)


def test_h1_min_must_be_less_than_max(tmp_path: Path):
    bad = _minimal_yaml().replace(
        "mcap_max_usd: 2000000000", "mcap_max_usd: 10000000",
    )
    p = tmp_path / "scoring.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scoring_config(p)
