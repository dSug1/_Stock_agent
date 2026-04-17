# Stock Picker — Claude Code Project

## What this is
Autonomous stock picker for a $75,000 real-money portfolio.
Catalyst-driven, institutionally-anchored.
No short selling. No options. Manual execution by portfolio owner.
Full specification: /spec/ directory.

## Read before implementing any module
Read CLAUDE.md (this file) first.
Then read the specific spec file for the module you are building.
Then read the prompt file in /prompts/ for that step.

## Spec file map
spec/portfolio_specs.md       Portfolio constraints and sleeve structure
spec/layer_minus1.md          Institutional universe construction
spec/layer_0.md               Macro regime classification
spec/layer_1.md               Document ingestion and filtering
spec/layer_2.md               LLM catalyst extraction
spec/layer_3.md               Living catalyst registry
spec/layer_4.md               Signal generation + quantitative scorer
spec/layer_5.md               Outcome tracker
spec/layer_6.md               Parameter calibration engine
spec/layer_7.md               Historical simulation harness
spec/module_10.md             Alert dispatcher
spec/module_11.md             Portfolio state tracker
spec/module_12.md             Action tracking calendar
spec/pre_revenue_mode.md      Pre-revenue / biotech mode rules

## Technology stack
Python 3.11, SQLite, Anthropic API (claude-sonnet-4-20250514),
Polygon.io, FRED API, Twilio, APScheduler, tiktoken, pytest

## Operating modes
LIVE     Production — real money
SHADOW   Simulation — no real money
HISTORICAL  2020-2024 replay for pre-calibration

## Critical constraints — never violate
1. filing_date only — never period_of_report
2. Survivorship-bias-free prices — Polygon.io with delisted tickers
3. HISTORICAL mode — Layer 2 extraction only, no probability adjustment
4. probability_sum must equal 1.0 before any DB write
5. Deduplication check before every Bayesian update
6. Max position $5,500 — hard cap enforced last in sizing chain
7. Min cash reserve $3,000 — gate blocks BUY if breached
8. Parameter updates require minimum observation threshold
9. Changes >30% require human review flag before deployment
10. Always commit to git after each implementation step completes

## Implementation sequence
See README.md for the ordered implementation plan.

## Sibling projects in this repo
- `../0_Renderer/` — independent 3D ticker-price visualizer (Flask + Three.js).
  Do NOT import from it; the picker is self-contained.
- Root `.env` holds shared secrets (ANTHROPIC_API_KEY). `.venv/` at repo root
  is the shared Python environment for both projects.
