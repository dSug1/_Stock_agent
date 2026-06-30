"""Stage-3 cost estimate (pure) — drives the `[y/N]`/`--dispatch` gate and the `max_usd_per_run` ceiling.

Pricing per MTok (claude-api skill, 2026-06): Opus 4.8 5/25 · Sonnet 4.6 3/15 · Haiku 4.5 1/5 ·
web_search ~$0.01/search · **Batch API −50% on tokens**. The script estimate runs hot vs the real invoice,
so it's scaled by `cost_calibration_factor` (project memory `anthropic_cost_calibration`, default 0.10).
"""

from __future__ import annotations

PRICING = {                       # (input $/MTok, output $/MTok)
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
WEB_SEARCH_USD = 0.01

# Per-tier token/search assumptions (override under claude.cost.est in config).
_DEFAULTS = {
    "triage_in": 2000, "triage_out": 300,
    "rubric_in": 8000, "rubric_out": 1500, "rubric_searches": 3,
    "finalize_in": 10000, "finalize_out": 2000, "finalize_searches": 3,
    "triage_pass_frac": 0.5, "contested_frac": 0.2,
}


def _price(model: str) -> tuple[float, float]:
    return PRICING.get(model, (5.0, 25.0))


def _tok_usd(model: str, in_tok: float, out_tok: float) -> float:
    pin, pout = _price(model)
    return in_tok / 1e6 * pin + out_tok / 1e6 * pout


def estimate(n_candidates: int, cfg: dict) -> dict:
    """Estimate Stage-3 spend for ``n_candidates`` names. Returns calibrated per-tier + total USD."""
    cl = cfg.get("claude", {})
    e = {**_DEFAULTS, **(cl.get("cost", {}).get("est", {}))}
    calib = float(cl.get("cost", {}).get("cost_calibration_factor", 0.10))
    batch = 0.5 if cl.get("use_batch", True) else 1.0
    triage_m, rubric_m, finalize_m = cl.get("triage_model"), cl.get("rubric_model"), cl.get("finalize_model")

    triage = n_candidates * _tok_usd(triage_m, e["triage_in"], e["triage_out"])     # realtime, no search
    survivors = n_candidates * e["triage_pass_frac"]
    rubric = survivors * (_tok_usd(rubric_m, e["rubric_in"], e["rubric_out"]) * batch
                          + e["rubric_searches"] * WEB_SEARCH_USD)
    contested = survivors * e["contested_frac"]
    finalize = contested * (_tok_usd(finalize_m, e["finalize_in"], e["finalize_out"])
                            + e["finalize_searches"] * WEB_SEARCH_USD)              # realtime

    out = {"triage": triage * calib, "rubric": rubric * calib, "finalize": finalize * calib}
    out["total"] = out["triage"] + out["rubric"] + out["finalize"]
    return {k: round(v, 4) for k, v in out.items()}
