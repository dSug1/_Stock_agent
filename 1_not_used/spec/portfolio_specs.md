# Portfolio Specifications - Purpose & Scope

> Deviations from this spec are logged in [spec/decisions.md](decisions.md).

This document specifies a complete autonomous stock picking system for a $75,000 real-money portfolio. It covers universe construction, signal generation, catalyst identification, portfolio management, feedback loop, historical simulation, and action tracking. No short selling. No options. All trades executed manually by the portfolio owner.
This document is written to serve as the primary prompt input to Claude Code for implementation. Each layer and module is specified with sufficient precision that a code generation prompt can be derived directly from the relevant section without requiring cross-reference to prior documents.

System Overview
SYSTEM_ARCHITECTURE {

  production_pipeline: {
    Layer_minus1: "Institutional universe construction",
    Layer_0:      "Macro regime classification",
    Layer_1:      "Document ingestion and pre-filtering",
    Layer_2:      "LLM catalyst extraction",
    Layer_3:      "Living catalyst registry",
    Layer_4:      "Action signal generation"
  },

  evaluation_pipeline: {
    Layer_5: "Outcome tracker",
    Layer_6: "Parameter calibration engine",
    Layer_7: "Historical simulation harness"
  },

  support_modules: {
    Module_12: "Action tracking calendar"
  },

  operating_modes: {
    LIVE:       "Production pipeline only, real money",
    SHADOW:     "Production pipeline in simulation, no real money",
    HISTORICAL: "Full replay of 2020-2024, pre-calibration"
  }
}

Portfolio Specifications
PORTFOLIO_SPECIFICATIONS {

  capital:         75000,
  currency:        "USD",
  execution_mode:  "manual — human executes all trades",
  short_selling:   false,
  options:         false,

  horizons: {
    near_term:   { min: 1,  max: 4,  units: "months",
                   trigger: "datable_catalyst" },
    medium_term: { min: 4,  max: 9,  units: "months",
                   trigger: "valuation_plus_catalyst" },
    structural:  { min: 9,  max: 18, units: "months",
                   trigger: "secular_trend" }
  },

  horizon_entry_definition: {
    near_term:   "specific datable event — PDUFA, readout date, earnings",
    medium_term: "valuation discount + undated catalyst progression",
    structural:  "secular trend + quantitative score, no specific catalyst"
  },

  position_count: { min: 18, max: 22, target: 20 },

  sleeves: {
    S1_catalyst:  { max_pct: 43, positions: 8,
                    size_range_$: [3500, 4500] },
    S2_medium:    { max_pct: 31, positions: 6,
                    size_range_$: [3300, 4400] },
    S3_discovery: { max_pct: 16, positions: "4-6",
                    size_range_$: [1875, 2800] },
    macro_hedge:  { target_pct: 5, target_$: 3750,
                    instrument: "GLD or T-bill ETF",
                    sizing: "static" },
    cash_reserve: { target_pct: 5, target_$: 3750,
                    min_$: 3000,
                    purpose: "opportunistic entry + settlement buffer" }
  },

  hard_constraints: {
    max_single_position_$:     5500,
    max_single_position_pct:   7.3,
    max_single_sector_pct:     25,
    max_catalyst_type_pct:     40,
    max_loss_per_position_$:   3000,
    max_loss_per_position_pct: 4,
    min_cash_reserve_$:        3000
  },

  position_sizing_override_rule:
    "final_position_$ = min(
       CCS_band_$,
       sleeve_ceiling_$,
       3000 / abs(bear_scenario_pct),
       5500
     )",

  execution_flags: {
    liquidity_caution_ADV_$:  1000000,
    liquidity_minimum_ADV_$:  500000,
    illiquid_entry_method:    "limit order at mid or better",
    tax_ltcg_tracking:        true,
    weekend_catalyst_flag:    true
  }
}

Data Flow
Layer -1  →  Institutional universe construction
               (quarterly 13F + continuous Form 4/13G/13D)
               ↓
Layer  0  →  Macro regime classification
               (adaptive daily/weekly)
               ↓
Layer  1  →  Document ingestion and pre-filtering
               (continuous)
               ↓
Layer  2  →  LLM catalyst extraction
               (per document — LIVE/SHADOW mode)
               (extraction-only mode — HISTORICAL mode)
               ↓
Layer  3  →  Living catalyst registry
               (continuous Bayesian updates + deduplication)
               ↓
Layer  4  →  Action signal generation
               (threshold-triggered)
               ↓
          →  Human review gate
               (contradiction/divergence flags)
               ↓
          →  Recommendation output → manual execution
               ↓
          →  Module 12 ← action calendar auto-populated
               ↓
Layer  5  →  Outcome tracker
               (records predicted vs actual)
               ↓
Layer  6  →  Parameter calibration engine
               (quarterly)
               ↓
Layer  7  →  Historical simulation harness
               (one-time pre-launch)

