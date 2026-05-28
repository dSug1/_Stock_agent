"""Module 6 — hard filter unit tests (H1-H5 + bucket partition)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6.config import default_config_path, load_scoring_config  # noqa: E402
from module_6.filters import apply_hard_filters  # noqa: E402


SNAP = date(2026, 5, 27)


def _cfg():
    return load_scoring_config(default_config_path())


def _good_inputs(**overrides):
    base = dict(
        market_cap_usd=500_000_000,
        precision_tier="conference",
        date_min=date(2026, 7, 1),    # +35 days
        date_max=date(2026, 7, 5),
        stage="phase2",
        next_catalyst_type="Interim Data",
        snapshot_date=SNAP,
    )
    base.update(overrides)
    return base


def test_hard_pass_clean_row():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs())
    assert v.hard_pass is True
    assert v.fail_reasons == ()
    assert v.timing_bucket == "catalyst_date_defined"


# ---------- H1 ----------

def test_H1_fails_below_min():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(market_cap_usd=10_000_000))
    assert v.hard_pass is False
    assert "H1" in v.fail_reasons


def test_H1_fails_at_or_above_max():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(market_cap_usd=2_000_000_000))
    assert v.hard_pass is False
    assert "H1" in v.fail_reasons


def test_H1_fails_on_null_mcap():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(market_cap_usd=None))
    assert "H1" in v.fail_reasons


def test_H1_boundary_pass_at_min():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(market_cap_usd=30_000_000))
    assert v.hard_pass is True


# ---------- H2 ----------

def test_H2_fails_on_unknown_precision():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(precision_tier="unknown"))
    assert "H2" in v.fail_reasons


# ---------- H3 ----------

def test_H3_fails_when_window_starts_too_soon():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(date_min=date(2026, 6, 1)))  # +5 days
    assert "H3" in v.fail_reasons


def test_H3_boundary_pass_at_plus_14_days():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(date_min=date(2026, 6, 10),  # +14 days
                                                    date_max=date(2026, 6, 15)))
    assert "H3" not in v.fail_reasons


def test_H3_fails_when_date_min_null():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(date_min=None))
    assert "H3" in v.fail_reasons


# ---------- H4 ----------

def test_H4_fails_when_window_entirely_past():
    cfg = _cfg()
    v = apply_hard_filters(
        cfg=cfg,
        **_good_inputs(
            date_min=date(2026, 5, 1),  # also fails H3
            date_max=date(2026, 5, 20),
        ),
    )
    assert "H4" in v.fail_reasons


def test_H4_boundary_pass_when_date_max_equals_snapshot():
    cfg = _cfg()
    v = apply_hard_filters(
        cfg=cfg,
        **_good_inputs(date_max=SNAP),
    )
    assert "H4" not in v.fail_reasons


# ---------- H5 ----------

def test_H5_rejects_phase4():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(stage="phase4"))
    assert "H5" in v.fail_reasons


def test_H5_rejects_phase5():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(stage="phase5"))
    assert "H5" in v.fail_reasons


def test_H5_rejects_regulatory_decision():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(next_catalyst_type="Regulatory Decision"))
    assert "H5" in v.fail_reasons


def test_H5_rejects_submission():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(next_catalyst_type="Submission"))
    assert "H5" in v.fail_reasons


def test_H5_rejects_eop_meeting():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(next_catalyst_type="End of Phase Meeting"))
    assert "H5" in v.fail_reasons


def test_H5_rejects_null_stage_or_type():
    cfg = _cfg()
    v1 = apply_hard_filters(cfg=cfg, **_good_inputs(stage=None))
    v2 = apply_hard_filters(cfg=cfg, **_good_inputs(next_catalyst_type=None))
    assert "H5" in v1.fail_reasons
    assert "H5" in v2.fail_reasons


def test_H5_accepts_all_clinical_types_phase1_to_3():
    cfg = _cfg()
    for stage in ("phase1", "phase2", "phase3"):
        for ct in ("Interim Data", "Initial Data", "Topline Data",
                   "Full Results", "Conference Presentation"):
            v = apply_hard_filters(cfg=cfg, **_good_inputs(stage=stage, next_catalyst_type=ct))
            assert "H5" not in v.fail_reasons, (stage, ct)


# ---------- Multiple failures collected ----------

def test_collects_multiple_failures():
    cfg = _cfg()
    v = apply_hard_filters(
        cfg=cfg,
        **_good_inputs(
            market_cap_usd=10e9,    # H1
            stage="phase4",         # H5
        ),
    )
    assert v.hard_pass is False
    assert "H1" in v.fail_reasons
    assert "H5" in v.fail_reasons


# ---------- Timing bucket ----------

def test_bucket_defined_includes_quarter():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(precision_tier="quarter"))
    assert v.timing_bucket == "catalyst_date_defined"


def test_bucket_undefined_for_half_and_year():
    cfg = _cfg()
    for tier in ("half", "year"):
        v = apply_hard_filters(cfg=cfg, **_good_inputs(precision_tier=tier))
        assert v.timing_bucket == "catalyst_date_undefined", tier


def test_bucket_none_when_hard_fail():
    cfg = _cfg()
    v = apply_hard_filters(cfg=cfg, **_good_inputs(precision_tier="unknown"))
    assert v.hard_pass is False
    assert v.timing_bucket is None
