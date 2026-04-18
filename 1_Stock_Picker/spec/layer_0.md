# Layer 0 — Macro Regime Classifier

> Deviations from this spec are logged in [spec/decisions.md](decisions.md).

Critical design note: Two distinct cadences must not be conflated:

Regime classification — how often to run the classifier and update the active regime tag
Boundary parameter recalibration — how often to recalibrate the threshold values themselves


0.1 Inputs
InputSourceUpdate frequencyFed Funds rate directionFRED (DFF)DailyCPI vs. 2% targetFRED (CPIAUCSL)Monthly releasePCE vs. 2% targetFRED (PCEPI)Monthly releaseIG credit spreadsFRED (BAMLC0A0CM)DailyHY credit spreadsFRED (BAMLH0A0HYM2)DailyVIX levelCBOE via yfinanceDailyVIX 20-day trendComputedDaily10Y-2Y yield curve slopeFRED (T10Y2Y)Daily

0.2 Regime Classification
Output — 2×2 matrix:
Risk-OnRisk-OffEasing/Stable ratesGROWTH_FAVOURABLEDEFENSIVE_EASINGTightening ratesCYCLICAL_TRANSITIONFULL_RISK_OFF
Default boundary parameters (recalibrated semi-annually):
BOUNDARY_PARAMETERS {
  VIX:
    risk_on:      < 18
    transitional: 18-25
    risk_off:     > 25

  credit_spreads_vs_24M_median:
    tight:  < -20bp
    normal: -20bp to +30bp
    wide:   > +30bp

  yield_curve_slope:
    normal:   > +50bp
    flat:     0 to +50bp
    inverted: < 0bp

  rate_direction_3M_rolling:
    easing:     < -15bp
    stable:     -15bp to +15bp
    tightening: > +15bp
}
Regime determination logic:
if rate_direction in [easing, stable]:
  if VIX < 18 AND credit_spreads == tight:
    regime = GROWTH_FAVOURABLE
  elif VIX > 25 OR credit_spreads == wide:
    regime = DEFENSIVE_EASING
  else:
    regime = GROWTH_FAVOURABLE

if rate_direction == tightening:
  if VIX > 25 OR credit_spreads == wide:
    regime = FULL_RISK_OFF
  else:
    regime = CYCLICAL_TRANSITION

0.3 Adaptive Classification Cadence
CLASSIFICATION_CADENCE {

  instability_triggers:
    VIX_above:            20
    credit_spread_change: "> +20bp in 5 days"
    yield_curve_change:   "> 20bp in 5 days"
    rate_surprise:        "FOMC decision deviates from consensus"

  cadence_rules:
    if any instability_trigger active:
      run_classifier:  "daily, before market open"
      alert_on_change: "SMS_immediate"
    else:
      run_classifier:  "weekly, Sunday evening"
      alert_on_change: "daily_digest"

  regime_change_propagation:
    timing:  "same session as regime tag change"
    targets: ["Layer_3_CCS_formula",
              "Layer_4_position_multipliers",
              "Layer_4_watchlist_threshold"]
    method:  "automatic — no human action required"
    alert:   "SMS_immediate regardless of cadence"

  calendar_action_on_regime_change:
    "create REGIME_CHANGE_REVIEW action in Module 12
     priority: urgent
     due: same day as change
     description: review all open positions for size adjustment"
}

0.4 Downstream Modulation
REGIME_MODULATION {

  GROWTH_FAVOURABLE: {
    magnitude_weight_multiplier:   1.00,
    position_size_multiplier:      1.00,
    pre_revenue_runway_threshold:  3,
    long_duration_bonus:           true
  },

  DEFENSIVE_EASING: {
    magnitude_weight_multiplier:   0.85,
    position_size_multiplier:      0.85,
    pre_revenue_runway_threshold:  3,
    long_duration_bonus:           false
  },

  CYCLICAL_TRANSITION: {
    magnitude_weight_multiplier:   0.85,
    position_size_multiplier:      0.85,
    pre_revenue_runway_threshold:  4,
    long_duration_bonus:           false
  },

  FULL_RISK_OFF: {
    magnitude_weight_multiplier:   0.70,
    position_size_multiplier:      0.60,
    pre_revenue_runway_threshold:  4,
    long_duration_bonus:           false,
    near_term_priority:            true
  }
}

0.5 Feedback Loop Parameters — Layer 0
Regime classification cadence: Not a tunable parameter. Reviewed annually for appropriateness but not subject to automated calibration.
Boundary parameters — semi-annual recalibration:
BOUNDARY_RECALIBRATION {
  cadence: "semi-annual — aligned with FOMC cycle reviews"
  method:
    "for each regime classification in prior 6 months:
     did portfolio perform as predicted by regime tag?
     GROWTH_FAVOURABLE → positive returns expected
     FULL_RISK_OFF → reduced returns / capital preservation expected
     fit updated boundaries that minimise regime misclassification"
  minimum_observations:
    "at least 2 complete regime state transitions"
  output:
    "updated VIX thresholds, credit spread thresholds,
     yield curve cutoffs"
  calendar_action:
    "SEMIANNUAL_LAYER0_RECALIBRATION in Module 12
     due every April 1 and October 1
     reminder 14 days prior"
}
Regime modulation magnitudes — semi-annual recalibration:
MODULATION_RECALIBRATION {
  cadence: "semi-annual"
  method:
    "stratify resolved positions by regime_at_entry
     compute average return by regime tag
     fit updated modulation multipliers matching
     empirical return ratios across regime states"
  minimum_observations:
    "minimum 20 resolved positions across
     at least 2 distinct regime states"
  note:
    "if insufficient observations, retain default values.
     do not recalibrate on fewer than minimum observations."
}

