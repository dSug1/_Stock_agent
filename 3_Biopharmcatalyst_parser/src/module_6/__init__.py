"""Module 6 — Scoring & Ranking.

Reads catalyst_snapshots + catalyst_timing + v_executive_open_market_trades
and (optionally) the 2_Funds_parser holdings DB; applies hard filters
H1-H5; computes a composite score from three soft signals (insider
CEO+CFO buys / 30d momentum / fund accumulation); writes one row per
catalyst into catalyst_scores. See spec/biotech_pipeline_spec.md §12
and decisions.md D8 + D9.
"""
