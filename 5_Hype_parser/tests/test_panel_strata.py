"""Tests for panel stratification diagnostics (Protocol 2.3; Stage C / D19). Pure, offline."""

from hype_parser import panel_strata as ps


def test_max_window_share_detects_clustering():
    # all four in one month -> share 1.0
    clustered = ["2021-01-05", "2021-02-10", "2021-03-01", "2021-06-30"]
    assert ps.max_window_share(clustered) == 1.0
    # spread across ~4 years -> at most ~1/ (years) in any 12mo window
    spread = ["2018-01-01", "2019-06-01", "2021-01-01", "2023-06-01"]
    assert ps.max_window_share(spread) <= 0.5
    assert ps.max_window_share([]) == 0.0


def _rows(n_pos, n_hard, *, t0_pos="2021-01-01", sectors=("bio", "tech"), regimes=("A", "B")):
    rows = []
    for i in range(n_pos):
        rows.append({"label": "positive", "t0": t0_pos, "theme": "t",
                     "sector": sectors[i % len(sectors)], "regime": regimes[i % len(regimes)]})
    for i in range(n_hard):
        rows.append({"label": "hard_negative", "t0": "2020-03-01", "theme": "t",
                     "sector": sectors[i % len(sectors)], "regime": regimes[i % len(regimes)]})
    return rows


def test_underpowered_and_too_few_positives_flagged():
    rep = ps.evaluate_strata(_rows(3, 10), min_n=100, min_positive=50)
    assert any("underpowered" in f for f in rep["flags"])
    assert any("too few positives" in f for f in rep["flags"])
    assert rep["stratified_ok"] is False


def test_temporal_clustering_flagged():
    # 60 positives ALL at the same t0 -> 100% in a 12mo window
    rep = ps.evaluate_strata(_rows(60, 60), min_n=100, min_positive=50, max_temporal_share=0.50)
    assert any("temporal clustering" in f for f in rep["flags"])
    assert rep["temporal_positive_share"] == 1.0


def test_regime_and_sector_flags():
    rows = _rows(60, 60, sectors=("bio",), regimes=("A",))   # one sector, one regime
    rep = ps.evaluate_strata(rows, min_n=100, min_positive=50, min_sectors=2)
    assert any("sector spread" in f for f in rep["flags"])
    assert any("regime balance" in f for f in rep["flags"])


def test_healthy_panel_passes():
    # 60 positives spread over years + balanced sectors/regimes, 60 hard-negs
    rows = []
    for i in range(60):
        yr = 2017 + (i % 6)
        rows.append({"label": "positive", "t0": f"{yr}-0{(i % 9) + 1}-01", "theme": f"t{i % 5}",
                     "sector": ("bio", "tech")[i % 2], "regime": ("A", "B")[i % 2]})
    for i in range(60):
        yr = 2017 + (i % 6)
        rows.append({"label": "hard_negative", "t0": f"{yr}-0{(i % 9) + 1}-01", "theme": f"t{i % 5}",
                     "sector": ("bio", "tech")[i % 2], "regime": ("A", "B")[i % 2]})
    rep = ps.evaluate_strata(rows, min_n=100, min_positive=50, max_temporal_share=0.50, min_sectors=2)
    assert rep["n"] == 120 and rep["n_positive"] == 60
    assert rep["stratified_ok"] is True, rep["flags"]
