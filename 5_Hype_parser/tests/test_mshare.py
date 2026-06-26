"""Tests for the m_share decomposition + label (Stage B; D18). Pure, offline.

`min_revenue=0` in the mechanics tests so the materiality floor doesn't interfere; the floor itself
is tested separately."""

import math

import pytest

from hype_parser import mshare as ms


def _d(price0, priceH, rev0, sh0, revH, shH, min_revenue=0):
    return ms.decompose_mshare(price0, priceH, rev0, sh0, revH, shH, min_revenue=min_revenue)


# ---------- decomposition mechanics ----------

def test_pure_rerating_is_one():
    # fundamental per share unchanged, price doubles -> all re-rating
    r = _d(10, 20, rev0=100, sh0=10, revH=100, shH=10)
    assert r["mode"] == "decomposed" and r["m_share"] == pytest.approx(1.0)


def test_pure_fundamental_growth_is_zero():
    # multiple unchanged: price doubles because revenue/share doubles
    r = _d(10, 20, rev0=100, sh0=10, revH=200, shH=10)
    assert r["m_share"] == pytest.approx(0.0)


def test_mixed_half():
    # price x4, fundamental x2 -> multiple x2 -> m_share = log2/log4 = 0.5
    r = _d(10, 40, rev0=100, sh0=10, revH=200, shH=10)
    assert r["m_share"] == pytest.approx(0.5)


def test_dilution_pushes_mshare_above_one():
    # revenue flat but shares double (dilution) -> rev/share halves while price doubles:
    # the re-rating did MORE than 100% of the lift. m_share = log4/log2 = 2.
    r = _d(10, 20, rev0=100, sh0=10, revH=100, shH=20)
    assert r["m_share"] == pytest.approx(2.0)


def test_flat_is_none():
    assert _d(10, 10, 100, 10, 120, 10)["mode"] == "flat"
    assert _d(10, 10, 100, 10, 120, 10)["m_share"] is None


def test_incomplete_end_is_none():
    assert _d(10, 20, 100, 10, None, 10)["mode"] == "incomplete_end"


# ---------- pre-revenue / materiality floor ----------

def test_pre_revenue_when_no_revenue():
    r = ms.decompose_mshare(10, 50, None, 10, None, 10, min_revenue=25_000_000)
    assert r["m_share"] == 1.0 and r["mode"] == "pre_revenue"


def test_immaterial_revenue_floor_is_pre_revenue():
    # $3M revenue at t0 with a $25M floor -> treated as pre-revenue (milestone revenue, e.g. CRSP)
    r = ms.decompose_mshare(10, 50, 3_000_000, 1_000_000, 5_000_000, 1_000_000,
                            min_revenue=25_000_000)
    assert r["m_share"] == 1.0 and r["mode"] == "pre_revenue"


def test_material_revenue_decomposes():
    r = ms.decompose_mshare(10, 20, 100_000_000, 10, 100_000_000, 10, min_revenue=25_000_000)
    assert r["mode"] == "decomposed" and r["m_share"] == pytest.approx(1.0)


# ---------- label ----------

def test_label_positive_needs_return_and_hype():
    # cleared +100% AND re-rating-dominated -> positive
    assert ms.mshare_label(1.5, 0.8, hit_return=1.0, m_share_min=0.5) == "positive"
    # cleared the return but it was fundamental growth (low m_share) -> hard_negative
    assert ms.mshare_label(1.5, 0.2, hit_return=1.0, m_share_min=0.5) == "hard_negative"
    # didn't clear the return bar -> hard_negative regardless
    assert ms.mshare_label(0.3, 0.9, hit_return=1.0, m_share_min=0.5) == "hard_negative"
    # cleared the bar, no decomposition available -> fall back to price-only positive
    assert ms.mshare_label(1.5, None, hit_return=1.0, m_share_min=0.5) == "positive"
    # no forward data
    assert ms.mshare_label(None, 0.9, hit_return=1.0, m_share_min=0.5) is None
