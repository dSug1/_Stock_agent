"""Module 7 — pre-flight cost estimator unit tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.cost_estimate import (  # noqa: E402
    EstimateInputs,
    estimate_cost,
    render_cost_html,
)

# Mirror config/module_7.yaml::pricing — tests pin against these values.
PRICING = {
    "model": "claude-opus-4-7",
    "input_per_mtok": 15.00,
    "output_per_mtok": 75.00,
    "cache_read_multiplier": 0.10,
    "cache_creation_multiplier": 0.10,
    "batch_discount": 0.50,
    "web_search_per_1k": 10.00,
    "search_result_avg_tokens": 1500,
    "cost_calibration_factor": 0.10,
}


def _inputs(**over):
    base = dict(
        snapshot_date="2026-08-04",
        prompt_version="m7-v1:abc1234",
        model="claude-opus-4-7",
        cached_prefix_text="x" * 30000,                  # ~8.3k tokens prefix
        pack_jsons_sample=[json.dumps({"id": i, "pad": "y" * 1000})
                           for i in range(3)],
        n_tickers=20,
        max_output_tokens=12500,
        max_uses_web_search=10,
        pricing=PRICING,
    )
    base.update(over)
    return EstimateInputs(**base)


# ─────────────────────── basic shape ────────────────────────


def test_returns_three_scenarios_in_order():
    out = estimate_cost(_inputs())
    names = [s.name for s in out.scenarios]
    assert names == ["no-optim", "cache-only", "cache+batch"]


def test_n_api_calls_matches_n_tickers():
    out = estimate_cost(_inputs(n_tickers=15))
    assert out.n_api_calls == 15


def test_pack_sample_required():
    with pytest.raises(ValueError):
        estimate_cost(_inputs(pack_jsons_sample=[]))


# ─────────────────────── cache savings ──────────────────────


def test_cache_only_beats_no_optim():
    """Caching the prefix is always cheaper than not caching."""
    out = estimate_cost(_inputs())
    assert out.scenarios[1].total_usd < out.scenarios[0].total_usd


def test_cache_plus_batch_beats_cache_only():
    """Batch's 50% discount applies on top of caching → cheapest scenario."""
    out = estimate_cost(_inputs())
    assert out.scenarios[2].total_usd < out.scenarios[1].total_usd


# ─────────────────────── pricing math ───────────────────────


def test_calibration_factor_applied():
    """Setting calibration to 1.0 must produce 10× the cost vs the 0.10 default."""
    out_default = estimate_cost(_inputs())
    pricing_no_calib = {**PRICING, "cost_calibration_factor": 1.0}
    out_no_calib = estimate_cost(_inputs(pricing=pricing_no_calib))
    ratio = out_no_calib.scenarios[2].total_usd / out_default.scenarios[2].total_usd
    assert abs(ratio - 10.0) < 0.001


def test_batch_discount_only_on_tokens_not_search():
    """Increase search fee and the batch-discount-only ratio shifts toward
    the search fee dominating; setting batch_discount=1.0 must collapse to
    cache_only."""
    out = estimate_cost(_inputs())
    s_only = out.scenarios[1]
    s_batch = out.scenarios[2]
    # Search fee identical (no batch discount applies to it)
    assert s_only.search_fee_usd_upper_bound == s_batch.search_fee_usd_upper_bound


def test_batch_discount_collapses_to_cache_only_when_disabled():
    p = {**PRICING, "batch_discount": 1.0}
    out = estimate_cost(_inputs(pricing=p))
    assert abs(out.scenarios[1].total_usd - out.scenarios[2].total_usd) < 1e-6


# ─────────────────────── scaling sanity ─────────────────────


def test_doubling_tickers_roughly_doubles_cost():
    out_small = estimate_cost(_inputs(n_tickers=10))
    out_big = estimate_cost(_inputs(n_tickers=20))
    ratio = out_big.scenarios[2].total_usd / out_small.scenarios[2].total_usd
    assert 1.8 < ratio < 2.2                              # ≈ 2x within rounding


def test_zero_tickers_zero_cost():
    out = estimate_cost(_inputs(n_tickers=0))
    for s in out.scenarios:
        assert s.total_usd == 0.0
        assert s.input_tokens_total == 0
        assert s.output_tokens_total == 0


def test_more_searches_cost_more():
    out_low = estimate_cost(_inputs(max_uses_web_search=2))
    out_high = estimate_cost(_inputs(max_uses_web_search=10))
    assert out_high.scenarios[2].total_usd > out_low.scenarios[2].total_usd


# ─────────────────────── notes + production_total_usd ──────


def test_notes_mention_calibration_factor():
    out = estimate_cost(_inputs())
    joined = " ".join(out.notes)
    assert "cost_calibration_factor" in joined


def test_production_total_property():
    out = estimate_cost(_inputs())
    assert out.production_total_usd == out.scenarios[2].total_usd


# ─────────────────────── HTML renderer ─────────────────────


# ─────────────────────── D21 sync_concurrency model ────────


def test_sync_concurrency_higher_means_more_cache_creation():
    """D21: at sync_concurrency=C, the first C parallel calls each write
    their own cache; only subsequent waves can read. Higher C → more
    cache_create tokens and less cache_read tokens in the cache-only
    (sync-realistic) scenario."""
    low = estimate_cost(_inputs(n_tickers=20, sync_concurrency=1))
    high = estimate_cost(_inputs(n_tickers=20, sync_concurrency=8))
    assert high.scenarios[1].cache_creation_tokens_total \
        > low.scenarios[1].cache_creation_tokens_total
    assert high.scenarios[1].cache_read_tokens_total \
        < low.scenarios[1].cache_read_tokens_total


def test_sync_concurrency_does_not_affect_cache_batch_scenario():
    """cache+batch models the batch-mode optimistic path (1 write,
    N-1 reads) — independent of sync_concurrency."""
    low = estimate_cost(_inputs(n_tickers=20, sync_concurrency=1))
    high = estimate_cost(_inputs(n_tickers=20, sync_concurrency=8))
    assert low.scenarios[2].total_usd == high.scenarios[2].total_usd


def test_render_html_produces_file(tmp_path: Path):
    out = estimate_cost(_inputs())
    target = tmp_path / "cost_estimate.html"
    render_cost_html(out, gates={"hard_pass_only": True, "n_tickers": 20}, output_path=target)
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert "<!doctype html>" in body
    assert "Module 7" in body
    assert "cache+batch" in body
