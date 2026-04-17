# Layer 6 — Parameter Calibration Engine

6.1 Calibration Schedule
CALIBRATION_SCHEDULE {

  warm_up_period:
    "first 6 months of LIVE/SHADOW operation:
     accumulate data only — no parameter changes
     OR replaced by historical simulation pre-calibration"

  standard_cadence: "quarterly"

  early_trigger_conditions: [
    "Brier score degrades > 20% vs prior quarter",
    "exit_timing_delta negative > 3 consecutive quarters",
    "Layer 0 regime misclassification rate > 30%"
  ]

  minimum_observations_before_update: {
    Layer_minus1_multipliers:      30,
    Layer_0_boundaries:            "2 complete regime transitions",
    Layer_2_base_rates:            20,
    Layer_2_magnitude_class:       15,
    Layer_3_CCS_weights:           40,
    Layer_3_Bayesian_update:       20,
    Layer_3_options_divergence:    20,
    Layer_4_band_thresholds:       20
  }

  parameter_change_governance:
    "all parameter updates logged with:
     prior value, new value, sample size,
     calibration output metrics before and after
     no parameter changed without minimum observations met
     changes > 30% require human review before deployment"

  calendar_action:
    "QUARTERLY_CALIBRATION_RUN in Module 12
     due 15 days after each 13F release date
     reminder 3 days prior"
}

6.2 Calibration Report
CALIBRATION_REPORT {
  quarter:            string,
  observations:       int,
  resolved_positions: int,

  output_A_brier_scores: {
    by_catalyst_type:    { type: float },
    by_therapeutic_area: { area: float },
    overall:             float,
    vs_prior_quarter:    float,
    vs_base_rate_only:   float
    // is LLM adjustment adding or destroying calibration?
  },

  output_B_price_impact_MAE: {
    by_magnitude_class: { class: float },
    by_scenario:        { bull: float, base: float, bear: float },
    overall:            float
  },

  output_C_returns: {
    sharpe_by_CCS_band:   { band: float },
    sharpe_by_sleeve:     { S1: float, S2: float, S3: float },
    sharpe_by_regime:     { regime: float },
    exit_timing_delta:    float,
    fixed_horizon_sharpe: float,
    model_exit_sharpe:    float
  },

  updated_parameters: {
    // only populated if minimum observations met
    // and change passes governance check
    Layer_minus1: { parameter: { old: value, new: value } },
    Layer_0:      { parameter: { old: value, new: value } },
    Layer_2:      { parameter: { old: value, new: value } },
    Layer_3:      { parameter: { old: value, new: value } },
    Layer_4:      { parameter: { old: value, new: value } }
  },

  flags: {
    llm_contamination_suspected: boolean,
    // true if live Brier score >> historical simulation Brier score
    parameters_insufficient_data: [string],
    // list of parameters not updated due to low observations
    manual_review_required: [string]
    // parameters where calibration suggests change > 30%
    // require human review before deployment
  }
}

