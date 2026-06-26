"""Tests for the crude panel builder — mechanical t0 + PIT feature reconstruction (D14).

All pure/offline: synthetic monthly series + price dicts, no network.
"""

import math

import pytest

from hype_parser import panel_builder as pb


def _series(values, start="2020-01"):
    """values: list of n_spec by consecutive month from `start`. Returns (periods, ns_by, nm_by)."""
    from hype_parser.diffusion import month_range
    months = month_range(start, _add(start, len(values) - 1))
    ns = {m: v for m, v in zip(months, values)}
    nm = {m: 0 for m in months}            # zero mainstream (the common crude case)
    return months, ns, nm


def _add(month, k):
    y, m = int(month[:4]), int(month[5:7])
    m += k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


# ---------- contiguous_series ----------

def test_contiguous_fills_gaps():
    periods = ["2020-01", "2020-03"]          # missing 2020-02
    full, nsf, nmf = pb.contiguous_series(periods, {"2020-01": 5, "2020-03": 9}, {})
    assert full == ["2020-01", "2020-02", "2020-03"]
    assert nsf["2020-02"] == 0                # gap filled with 0
    assert nsf["2020-03"] == 9


def test_contiguous_empty():
    assert pb.contiguous_series([], {}, {}) == ([], {}, {})


# ---------- nascency_as_of (point-in-time, leak-free) ----------

def test_nascency_is_leak_free():
    # flat for 6 months, then a big jump. The slope as-of an early month must NOT see the future jump.
    months, ns, nm = _series([5, 5, 5, 5, 5, 5, 100, 100], start="2020-01")
    full, nsf, nmf = pb.contiguous_series(months, ns, nm)
    early = pb.nascency_as_of(full, nsf, nmf, "2020-03", L=12, beta_min=0.0, p_max=0.5,
                              use_p_main_gate=False)
    late = pb.nascency_as_of(full, nsf, nmf, "2020-08", L=12, beta_min=0.0, p_max=0.5,
                             use_p_main_gate=False)
    assert early["beta_spec"] == pytest.approx(0.0, abs=1e-9)   # flat history -> ~0 slope
    assert late["beta_spec"] > 0.1                              # jump now visible
    assert late["gate"] is True


def test_nascency_needs_two_points():
    months, ns, nm = _series([5, 6, 7])
    full, nsf, nmf = pb.contiguous_series(months, ns, nm)
    assert pb.nascency_as_of(full, nsf, nmf, "2020-01", L=12, beta_min=0.0, p_max=0.5,
                             use_p_main_gate=False) is None
    assert pb.nascency_as_of(full, nsf, nmf, "1999-01", L=12, beta_min=0.0, p_max=0.5,
                             use_p_main_gate=False) is None     # absent month


def test_min_history_estimability_guard():
    # 8 months of series; with min_history=6 the first 5 months are ineligible (unestimable slope).
    months, ns, nm = _series([0, 0, 1, 2, 4, 8, 16, 32], start="2020-01")
    full, nsf, nmf = pb.contiguous_series(months, ns, nm)
    assert pb.nascency_as_of(full, nsf, nmf, "2020-04", L=12, beta_min=0.0, p_max=0.5,
                             use_p_main_gate=False, min_history=6) is None   # only 4 months -> blocked
    assert pb.nascency_as_of(full, nsf, nmf, "2020-06", L=12, beta_min=0.0, p_max=0.5,
                             use_p_main_gate=False, min_history=6) is not None  # 6 months -> eligible


def test_nascency_requires_growing_theme_and_corpus():
    # flat series -> beta ~0 -> strict beta>beta_min fails (D15 blocker-2 fix)
    flat_m, flat_ns, flat_nm = _series([7, 7, 7, 7, 7, 7], start="2020-01")
    full, nsf, nmf = pb.contiguous_series(flat_m, flat_ns, flat_nm)
    nas = pb.nascency_as_of(full, nsf, nmf, "2020-06", L=12, beta_min=0.0, p_max=0.5,
                            use_p_main_gate=False, min_n_spec=5)
    assert nas["beta_spec"] == pytest.approx(0.0, abs=1e-9) and nas["gate"] is False
    # growing but tiny corpus -> corpus floor fails even though beta>0
    g_m, g_ns, g_nm = _series([0, 1, 1, 2, 2, 3], start="2020-01")   # n_spec at 2020-06 = 3 < 5
    full, nsf, nmf = pb.contiguous_series(g_m, g_ns, g_nm)
    nas = pb.nascency_as_of(full, nsf, nmf, "2020-06", L=12, beta_min=0.0, p_max=0.5,
                            use_p_main_gate=False, min_n_spec=5)
    assert nas["beta_spec"] > 0 and nas["n_spec"] == 3 and nas["gate"] is False
    # growing AND real corpus -> passes
    big_m, big_ns, big_nm = _series([1, 2, 4, 8, 16, 32], start="2020-01")
    full, nsf, nmf = pb.contiguous_series(big_m, big_ns, big_nm)
    nas = pb.nascency_as_of(full, nsf, nmf, "2020-06", L=12, beta_min=0.0, p_max=0.5,
                            use_p_main_gate=False, min_n_spec=5)
    assert nas["gate"] is True


def test_p_main_gate_can_reject_when_enabled():
    # heavy mainstream coverage -> p_main high -> gate fails IF p_main gate is on
    months = ["2020-01", "2020-02"]
    full, nsf, nmf = pb.contiguous_series(months, {"2020-01": 1, "2020-02": 2},
                                          {"2020-01": 100, "2020-02": 200})
    on = pb.nascency_as_of(full, nsf, nmf, "2020-02", L=12, beta_min=0.0, p_max=0.5,
                           use_p_main_gate=True)
    off = pb.nascency_as_of(full, nsf, nmf, "2020-02", L=12, beta_min=0.0, p_max=0.5,
                            use_p_main_gate=False)
    assert on["p_main"] > 0.5 and on["gate"] is False          # rejected on mainstream saturation
    assert off["gate"] is True                                 # crude default ignores p_main


# ---------- drawdown_as_of ----------

def test_drawdown_uses_trailing_high():
    mc = {"2020-01": 100, "2020-02": 100, "2020-03": 100, "2020-04": 60}
    assert pb.drawdown_as_of(mc, "2020-04", lookback_months=12) == pytest.approx(-0.40)
    assert pb.drawdown_as_of(mc, "2020-01", lookback_months=12) == pytest.approx(0.0)
    assert pb.drawdown_as_of(mc, "2019-12", lookback_months=12) is None   # no price through t


def test_relative_drawdown_subtracts_benchmark():
    name = {"2020-01": 100, "2020-02": 100, "2020-03": 60}    # -40%
    bench = {"2020-01": 100, "2020-02": 100, "2020-03": 75}   # -25% (sector also down)
    # idiosyncratic component = -40% - (-25%) = -15%
    assert pb.relative_drawdown_as_of(name, bench, "2020-03", lookback_months=12) == pytest.approx(-0.15)
    assert pb.relative_drawdown_as_of(name, {}, "2020-03", lookback_months=12) is None


# ---------- mechanical_t0 (first week BOTH gates fire) ----------

def test_mechanical_t0_first_fire():
    # rising n_spec -> nascency true from month 2 on; price stays high then drops 40% at month 5.
    months, ns, nm = _series([1, 2, 4, 8, 16, 32], start="2020-01")
    full, nsf, nmf = pb.contiguous_series(months, ns, nm)
    mc = {"2020-01": 100, "2020-02": 100, "2020-03": 100, "2020-04": 100, "2020-05": 60,
          "2020-06": 55}
    t0, sig = pb.mechanical_t0(full, nsf, nmf, mc, L=12, beta_min=0.0, p_max=0.5,
                               use_p_main_gate=False, cheap_drawdown=0.25, dd_lookback=12)
    assert t0 == "2020-05"                                     # first month the drawdown gate fires
    assert sig["drawdown"] == pytest.approx(-0.40)
    assert sig["gate"] is True


def test_mechanical_t0_relative_gate_ignores_market_wide_selloff():
    # name and benchmark fall TOGETHER (market-wide) -> relative gate must NOT fire;
    # then the name keeps falling while the benchmark recovers -> idiosyncratic -> fires.
    months, ns, nm = _series([5, 10, 20, 40, 80, 160], start="2020-01")   # growing, real corpus
    full, nsf, nmf = pb.contiguous_series(months, ns, nm)
    name = {"2020-01": 100, "2020-02": 100, "2020-03": 60, "2020-04": 55, "2020-05": 50, "2020-06": 48}
    bench = {"2020-01": 100, "2020-02": 100, "2020-03": 62, "2020-04": 95, "2020-05": 98, "2020-06": 99}
    t0, sig = pb.mechanical_t0(full, nsf, nmf, name, L=12, beta_min=0.0, p_max=0.5,
                               use_p_main_gate=False, cheap_drawdown=0.25, dd_lookback=12,
                               min_n_spec=5, bench_close=bench, use_relative_dd=True,
                               relative_cheap=0.10)
    # 2020-03: name -40% but bench -38% -> rel -2% -> not idiosyncratic -> skip.
    # 2020-04: name -45% vs bench -5% -> rel -40% -> fires.
    assert t0 == "2020-04"
    assert sig["relative_drawdown"] < -0.10


def test_mechanical_t0_never_cheap():
    months, ns, nm = _series([1, 2, 4, 8], start="2020-01")
    full, nsf, nmf = pb.contiguous_series(months, ns, nm)
    mc = {m: 100 for m in months}                             # never drops -> mispricing never fires
    t0, sig = pb.mechanical_t0(full, nsf, nmf, mc, L=12, beta_min=0.0, p_max=0.5,
                               use_p_main_gate=False, cheap_drawdown=0.25, dd_lookback=12)
    assert t0 is None and sig is None


def test_mechanical_t0_requires_price_that_month():
    # drawdown deep at 2020-05 but no price row that month -> must skip it
    months, ns, nm = _series([1, 2, 4, 8, 16, 32], start="2020-01")
    full, nsf, nmf = pb.contiguous_series(months, ns, nm)
    mc = {"2020-01": 100, "2020-02": 100, "2020-03": 100, "2020-06": 50}   # 2020-05 missing
    t0, sig = pb.mechanical_t0(full, nsf, nmf, mc, L=12, beta_min=0.0, p_max=0.5,
                               use_p_main_gate=False, cheap_drawdown=0.25, dd_lookback=12)
    assert t0 == "2020-06"


# ---------- PIT features + labels ----------

def test_pit_features_multiplicative_narrative():
    sig = {"n_spec": 99, "beta_spec": 0.30, "p_main": 0.1, "drawdown": -0.4}
    feats = pb.pit_features("crispr_gene_editing", sig)
    assert feats["narrative"] == pytest.approx(math.log1p(99) * 0.30)
    assert feats["legibility"] == pytest.approx(math.log1p(99))
    assert feats["thematic_heat"] == 0.30
    assert feats["sector"] == 1.0                              # biotech
    assert feats["drawdown"] == -0.4


def test_theme_maps():
    assert pb.theme_sector_code("rag") == 0 and pb.theme_sector_code("mkras_vaccine") == 1
    assert pb.theme_regime("rag") == "B" and pb.theme_regime("crispr_gene_editing") == "A"
    assert pb.era_feature("2021-07") == 6.0


def test_crude_label():
    assert pb.crude_label(1.5, hit_return=1.0) == "positive"
    assert pb.crude_label(0.2, hit_return=1.0) == "hard_negative"
    assert pb.crude_label(None, hit_return=1.0) is None
