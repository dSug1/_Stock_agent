# Layer 3 — Living Catalyst Registry

> Deviations from this spec are logged in [spec/decisions.md](decisions.md).

3.1 Registry Entry Schema
COMPANY_ENTRY {
  ticker:            string,
  name:              string,
  sector:            string,
  sub_industry:      string,
  processing_tier:   "active|passive|watchlist",
  TWOS:              float,
  TWOS_history:      [{ quarter, TWOS, QoQ_change }],
  QoQ_change_signal: "new|significant_increase|moderate_increase
                      |flat|decrease|exit",
  crowding_flag:     boolean,
  regime_at_entry:   string,

  financials_snapshot: {
    cash_and_equivalents:              float,
    quarterly_burn_rate:               float,
    cash_runway_quarters:              float,
    last_equity_raise_date:            ISO8601,
    last_equity_raise_dilution_pct:    float,
    survival_probability_to_catalyst:  float,
    data_as_of_filing_date:            ISO8601
  },

  open_catalysts: [
    {
      catalyst_id:                uuid,
      catalyst_data:              Layer2_extraction_JSON,
      current_bull_probability:   float,
      current_base_probability:   float,
      current_bear_probability:   float,
      current_expected_value_pct: float,

      probability_history: [
        {
          timestamp:          ISO8601,
          bull:               float,
          base:               float,
          bear:               float,
          ev:                 float,
          trigger_document:   url,
          change_rationale:   string,
          update_magnitude:   float,
          corroborating_flag: boolean
        }
      ],

      active_contradictions: [
        {
          contradiction_type: string,
          source_document:    url,
          date:               ISO8601,
          resolution_status:  "open|resolved"
        }
      ],

      contributing_signals: [url],
      dependency_status: [
        { dependency: string,
          status: "intact|violated|uncertain" }
      ],
      status: "open|resolved_positive|resolved_negative|expired",
      deduplication_fingerprint: string
      // hash of catalyst_type + subtype + ticker + date_window_quarter
    }
  ],

  institutional_accumulation: {
    TWOS:                     float,
    TWOS_QoQ_change:          float,
    QoQ_change_signal:        string,
    up_down_volume_ratio_20d: float,
    up_down_volume_ratio_60d: float,
    accumulation_signal:      "strong|moderate|neutral|distribution",
    superinvestor_positions: [
      {
        institution:    string,
        tier:           int,
        ownership_pct:  float,
        QoQ_change_pct: float,
        change_type:    string
      }
    ],
    insider_purchases_90d: [
      {
        filer:         string,
        role:          string,
        dollar_amount: float,
        date:          ISO8601,
        source_tier:   int
      }
    ]
  },

  options_surface: {
    implied_volatility_30d:      float,
    put_call_ratio:              float,
    unusual_oi_flag:             boolean,
    market_implied_move_pct:     float,
    divergence_from_registry_ev: float,
    divergence_flag:             boolean  // true if divergence > 15%
  },

  composite_catalyst_score:  float,
  watchlist_status: "not_eligible|watchlist|
                     position_recommended|in_position"
}

3.2 Composite Catalyst Score Formula
CCS = Σ over all open catalysts of:
  [EV_pct × magnitude_weight × time_decay × survival_probability]
  × insider_accumulation_multiplier
  × crowding_adjustment
  × regime_modulation

magnitude_weight:
  transformative: 1.00
  significant:    0.70
  moderate:       0.40
  minor:          0.20

time_decay:
  immediate: 1.00
  0-30d:     0.95
  30-90d:    0.80
  90-180d:   0.60
  180d+:     0.35

survival_probability:
  min(cash_runway_quarters / quarters_to_catalyst, 1.0)

insider_accumulation_multiplier:
  base:                                       1.00
  + per Tier 1 signal (C-suite, SC 13D):    +0.08
  + per Tier 2 signal (1A/1B 13F, dir.):    +0.05
  + per Tier 3 signal (2A/2B 13F):          +0.03
  + strong volume accumulation (>1.5, 60d):  +0.03
  + significant QoQ increase:               +0.02
  cap:                                        1.35

crowding_adjustment:
  if crowding_flag: 0.60
  else:             1.00

regime_modulation:
  GROWTH_FAVOURABLE:   1.00
  DEFENSIVE_EASING:    0.85
  CYCLICAL_TRANSITION: 0.85
  FULL_RISK_OFF:       0.70

3.3 Bayesian Update Sequence
On each new Layer 2 document arrival:
BAYESIAN_UPDATE {

  step_1_deduplication:
    "compute deduplication_fingerprint for incoming extraction:
     hash(catalyst_type + subtype + ticker + date_window_quarter)
     check against all existing open_catalysts for this ticker
     if fingerprint matches existing catalyst:
       CORROBORATING MODE: apply reduced Bayesian weight
         weight = 0.3 × standard_update_weight
         log as corroborating_signal in contributing_signals
         do NOT create new open_catalyst entry
       rationale: same underlying event picked up by multiple
                  sources (e.g. press release + 8-K) should not
                  double-count as independent signals
     if no match:
       NEW EVENT MODE: proceed with standard Bayesian update
                       and create new open_catalyst entry"

  step_2: "identify open catalyst(s) relevant to document"

  step_3: "apply Layer 2 probability adjustment to current estimate
            not original prior
            if CORROBORATING MODE: apply 0.3× weight reduction"

  step_4: "check all active dependencies
            mark violated dependencies"

  step_5: "log to probability_history with full provenance:
            timestamp, old values, new values,
            trigger_document, change_rationale,
            update_magnitude (abs change in bull probability),
            corroborating_flag (true if deduplication triggered)"

  step_6: "check for contradictions with existing signals
            log to active_contradictions if present"

  step_7: "update institutional_accumulation block
            if document is Form 4 or 13F-related"

  step_8: "recompute CCS"

  step_9: "trigger Layer 4 if CCS crosses threshold boundary"
}

3.4 Feedback Loop — Layer 3
CCS formula weights — quarterly recalibration:
CCS_FORMULA_CALIBRATION {
  cadence:         "quarterly"
  parameters_tuned: [
    "magnitude_weight values",
    "time_decay curve values",
    "insider_accumulation_multiplier increments and cap"
  ]
  method:
    "from Layer 5 outcome tracker:
     regress position_return_at_resolution on CCS_components
     use Ridge regression (L2) — maximum 10 parameters
     verify that higher magnitude_weight predicts
     proportionally higher returns
     verify time_decay curve matches empirical
     return decay with holding period
     update values where empirical ratios diverge
     from formula ratios by more than 20%"
  minimum_observations: 40 resolved positions
  output: "updated formula weight values"
}
Bayesian update weighting calibration — quarterly:
BAYESIAN_UPDATE_CALIBRATION {
  cadence:        "quarterly"
  feedback_value: "Moderate"
  compute_cost:   "Zero"
  method:
    "from Layer 5 outcome tracker:
     for all update events where update_magnitude > 10pp:
       check whether actual price move at T_resolution
       was in the same direction as the probability update
     compute directional_accuracy_rate:
       = pct of large updates (>10pp) that correctly
         predicted subsequent price direction
     if directional_accuracy_rate < 55%:
       large updates are noise — tighten Bayesian update weight:
         standard_update_weight × 0.75
     if directional_accuracy_rate > 70%:
       updates are predictive — retain or widen bounds
     also validate corroborating_weight (currently 0.3×):
       compare outcomes of positions with vs without
       corroborating signals — if corroborating signals
       add predictive value, increase weight toward 0.5×"
  minimum_observations: 20 update events with known resolution
}
Options divergence threshold calibration — annual:
OPTIONS_DIVERGENCE_CALIBRATION {
  cadence:        "annual"
  feedback_value: "Low-moderate"
  compute_cost:   "Zero"
  method:
    "from Layer 5 outcome tracker:
     for all positions where divergence_flag was triggered:
       track whether the registry EV or the options-implied move
       was more accurate (closer to actual price_impact_pct)
     compute registry_accuracy_rate vs options_accuracy_rate
     if options market is more accurate > 60% of the time:
       lower divergence threshold from 15% to 10%
       (require human review at smaller divergence)
     if registry is more accurate > 60% of the time:
       raise threshold to 20% (reduce false positives)
     if insufficient divergence_flag events (<20):
       retain current 15% threshold"
  minimum_observations: 20 divergence_flag events
  calendar_action:
    "ANNUAL_OPTIONS_DIVERGENCE_REVIEW in Module 12
     due every January 1"
}

