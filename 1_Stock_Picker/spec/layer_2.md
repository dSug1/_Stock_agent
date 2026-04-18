# Layer 2 - LLM Catalyst Extraction

> Deviations from this spec are logged in [spec/decisions.md](decisions.md).

Two operating modes:

LIVE / SHADOW: Full extraction including LLM probability assessment
HISTORICAL: Extraction-only — LLM extracts structure but does not generate probability adjustments. Hardcoded base rates applied directly. Prevents LLM knowledge contamination of historical simulation.


2.1 Extraction Output Schema
json{
  "company": "string",
  "ticker": "string",
  "filing_type": "8K|10Q|10K|Form4|PressRelease",
  "operating_mode": "LIVE|SHADOW|HISTORICAL",
  "source_url": "string",
  "extraction_timestamp": "ISO8601",
  "document_publication_date": "ISO8601",

  "catalyst": {
    "type": "regulatory|clinical|corporate|financial|macro",
    "subtype": "FDA_approval|trial_readout|M&A|equity_offering|
                insider_purchase|partnership|going_concern|...",
    "direction": "positive|negative|ambiguous",
    "magnitude_class": "transformative|significant|moderate|minor",
    "time_horizon": "immediate|0-30d|30-90d|90-180d|180d+",

    "summary": "350-token plain language description covering:
                (1) what the catalyst is and why it matters —
                    mechanism of action, regulatory significance,
                    or corporate event impact;
                (2) key quantitative evidence present in the
                    document if any — p-values, hazard ratios,
                    endpoint definitions, deal terms, cash amounts;
                (3) primary dependency that must hold for the
                    catalyst to materialise;
                (4) primary risk factor that could invalidate
                    the catalyst;
                (5) estimated time to resolution if determinable.
                Write in plain English. Avoid financial jargon
                not present in the source document."
  },

  "scenario_decomposition": {
    "bull": {
      "probability": 0.0,
      "price_impact_pct": 0.0,
      "trigger_condition": "string"
    },
    "base": {
      "probability": 0.0,
      "price_impact_pct": 0.0,
      "trigger_condition": "string"
    },
    "bear": {
      "probability": 0.0,
      "price_impact_pct": 0.0,
      "trigger_condition": "string"
    },
    "probability_sum_check": "must equal 1.0 — validated before storage",
    "expected_value_pct": "computed: bull×impact + base×impact + bear×impact"
  },

  "insider_signal": {
    "present": false,
    "signal_type": "Form4_P|13F_new|13F_increase|
                   superinvestor_new|superinvestor_increase",
    "source_tier": null,
    "dollar_amount": 0.0,
    "source_name": "string",
    "tiering_rationale": "string"
  },

  "dependencies": [
    "string — condition that must hold for catalyst to materialise"
  ],

  "disqualifiers_check": {
    "going_concern_flag": false,
    "shelf_registration_dilution_risk": false,
    "covenant_breach": false,
    "management_departure_key": false
  },

  "probability_prior_basis": {
    "base_rate_source": "therapeutic_area|corporate_event|regulatory_precedent",
    "base_rate_value": 0.0,
    "adjustment_direction": "up|down|neutral",
    "adjustment_magnitude_pct": 0.0,
    "adjustment_rationale":
      "string — must cite specific quantitative evidence
       (p-value, hazard ratio, endpoint met) OR state:
       no quantitative basis — adjustment capped at ±10%"
  },

  "confidence_in_extraction": 0.0,
  "llm_probability_adjustment_applied": false
}
HISTORICAL mode behaviour:
if operating_mode == HISTORICAL:
  catalyst.summary = "extracted text only, no probability framing"
  scenario_decomposition.bull.probability = base_rate_value
  scenario_decomposition.bear.probability = 1 - base_rate_value
  scenario_decomposition.base.probability = 0.0
  probability_prior_basis.adjustment_magnitude_pct = 0.0
  llm_probability_adjustment_applied = false

2.2 Hardcoded Probability Prior Base Rates
PROBABILITY_PRIORS {

  // LLM may adjust within stated bounds in LIVE/SHADOW mode only
  // Bounds require quantitative evidence to reach maximum
  // Beyond bounds: human override flag required

  "Phase_3_oncology_solid_tumour": {
    base_rate:        0.40,
    max_upward_adj:   0.20,  // requires HR or p-value in document
    max_downward_adj: 0.20
  },
  "Phase_3_CNS": {
    base_rate:        0.48,
    max_upward_adj:   0.15,
    max_downward_adj: 0.25
  },
  "Phase_3_rare_disease": {
    base_rate:        0.68,
    max_upward_adj:   0.15,
    max_downward_adj: 0.20
  },
  "Phase_2_to_Phase_3_prediction": {
    base_rate:        0.28,
    max_upward_adj:   0.20,
    max_downward_adj: 0.15
  },
  "FDA_approval_given_NDA_BLA_filed": {
    base_rate:        0.85,
    max_upward_adj:   0.10,
    max_downward_adj: 0.30  // safety signal required for max downward
  },
  "MA_deal_close_given_announcement": {
    base_rate:        0.78,
    max_upward_adj:   0.10,
    max_downward_adj: 0.40  // regulatory risk required for max downward
  }
}
Adjustments beyond bounds require human override flag in registry.

2.3 Feedback Loop — Layer 2
Probability prior base rates — quarterly recalibration:
BASE_RATE_RECALIBRATION {
  cadence: "quarterly"
  method:
    "from Layer 5 outcome tracker:
     for each catalyst_type with ≥20 resolved outcomes:
       actual_success_rate = resolved_positive / total_resolved
       updated_base_rate = 0.7 × current_base_rate
                         + 0.3 × actual_success_rate
     // weighted update — prevents overcorrection on small samples"
  minimum_observations: 20 per catalyst type
  output: "updated base_rate values in PROBABILITY_PRIORS"
}
LLM adjustment bounds — quarterly recalibration:
ADJUSTMENT_BOUND_RECALIBRATION {
  cadence: "quarterly"
  method:
    "stratify resolved outcomes by adjustment_magnitude_pct bucket
     compute actual_success_rate per bucket
     if upward adjustments show no correlation with success:
       tighten max_upward_adj by 25%
     compute Brier score improvement from adjustments vs base rates alone
     if adjustments degrade Brier score: reduce all bounds by 50%"
  minimum_observations: 15 per adjustment direction
}
Magnitude_class boundaries — quarterly recalibration:
MAGNITUDE_CLASS_RECALIBRATION {
  cadence:        "quarterly"
  feedback_value: "High"
  compute_cost:   "Zero"
  method:
    "from Layer 5 outcome tracker:
     stratify actual price_impact_pct at T_resolution
     by magnitude_class assignment at extraction time
     verify that transformative > significant > moderate > minor
     in terms of actual median price impact
     if empirical return ratios between classes diverge from
     CCS formula weights (1.0/0.7/0.4/0.2) by more than 20%:
       propose updated magnitude_weight values for Layer 3 CCS formula
     also check magnitude_class assignment accuracy:
       if transformative assigned but actual impact < 15%:
         flag as systematic over-classification"
  minimum_observations: 15 per magnitude_class
  output:
    "updated magnitude_weight values + classification accuracy report"
}
Extraction prompt — change-controlled:
PROMPT_CHANGE_CONTROL {
  trigger:
    "systematic extraction error detected:
     magnitude_class misassignment rate > 20%
     time_horizon misassignment rate > 25%
     confidence_in_extraction median < 0.5"
  process:
    "1. identify failure mode from outcome data
     2. draft revised prompt
     3. test on 500-document historical sample
     4. compare extraction accuracy against current prompt
     5. deploy only if improvement confirmed"
  cost_per_test:      "500 documents × 600 tokens ≈ $0.90"
  maximum_frequency:  "2 prompt changes per quarter"
  calendar_action:
    "PROMPT_REVIEW action created in Module 12
     when trigger condition detected
     priority: medium, due within 14 days"
}
Training universe expansion:
TRAINING_UNIVERSE {
  scope:   "full passive monitoring universe — ~500 tickers"
  cadence: "continuous — same as active monitoring"
  estimated_monthly_cost:
    "500 tickers × 3 docs/month × 600 tokens
     = 900,000 tokens = $2.70/month"
  purpose:
    "generate outcome data for parameter calibration
     at 10× the rate of real positions alone"
  note:
    "passive monitoring tickers receive Layer 2 extraction
     but do not generate Layer 4 position recommendations
     unless promoted to active monitoring"
}

