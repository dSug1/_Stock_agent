# Layer 4 — Action Signal Generation

4.1 Watchlist Entry
WATCHLIST_ENTRY_TRIGGER {
  conditions: [
    "CCS crosses 45 from below",
    "survival_probability_to_catalyst > 0.70"
  ],
  on_entry: {
    tag_regime:        "current macro regime",
    tag_QoQ_signal:    "current QoQ_change_signal",
    set_thesis_review: "T_entry + horizon_midpoint",
    generate_alert:    "NEW_CATALYST_DETECTED → daily_digest",
    calendar_actions:  "see Module 12 section 12.3"
  }
}

4.2 Position Size Recommendation
Step 1 — CCS band:
CCSBand position %Band position $85-1008-10%$6,000-$7,50070-845-7%$3,750-$5,25055-693-4%$2,250-$3,00045-54Watchlist only—
Step 2 — Hard override:
final_position_$ = min(
  CCS_band_$,
  sleeve_ceiling_$,
  3000 / abs(bear_scenario_pct),
  5500
)
Step 3 — Multipliers (applied sequentially):
ConditionMultiplierOptions divergence flag active×0.70Active unresolved contradictions ≥2×0.80Macro regime FULL_RISK_OFF×0.60Crowding flag active×0.70Tier 1A/1B new position this quarter×1.15Tier 1 insider signal (C-suite, 13D)×1.15Both institution + insider present×1.25 (capped, not additive)
Pre-revenue names: cap one tier below CCS-implied.
All multipliers respect $5,500 absolute hard cap.

4.3 Sell / Exit Triggers
Any one sufficient:
SELL_TRIGGERS {
  score_collapse:
    "CCS falls below 35 on any rescore"
  dependency_violation:
    "hard dependency violated — DSMB halt,
     competitor approval closing market,
     going concern issued"
  valuation_realisation:
    "options market-implied move fully realised
     AND next catalyst > 180 days away"
  survival_risk:
    "survival_probability_to_catalyst < 0.50"
  institutional_exit:
    "Tier 1A/1B exits position AND CCS declining
     → immediate sell evaluation"
  TWOS_deterioration:
    "TWOS declines > 30% QoQ due to distribution"
}

4.4 Recommendation Output Schema
RECOMMENDATION_OUTPUT {
  ticker:             string,
  action:             "buy|sell|reduce|hold",
  sleeve_assignment:  "S1|S2|S3",
  entry_trigger_type: "datable_catalyst|valuation_catalyst|secular_trend",

  horizon: {
    category:                 "near_term|medium_term|structural",
    months_min:               int,
    months_max:               int,
    catalyst_resolution_date: "ISO8601 or null if undated",
    thesis_review_date:       "ISO8601"
  },

  position_sizing: {
    CCS_at_recommendation:        float,
    CCS_band_recommendation_pct:  float,
    bear_scenario_impact_pct:     float,
    max_position_loss_$:          3000,
    recommended_position_$:       float,
    recommended_position_pct:     float,
    sleeve_ceiling_applied:       boolean,
    bear_loss_limit_applied:      boolean
  },

  thesis_statement:
    "350-token investment case covering:
     (1) CATALYST — what is the specific catalyst driving
         this recommendation, what is the probability
         assessment and on what basis (base rate +
         any quantitative evidence from the document);
     (2) INSTITUTIONAL SIGNAL — which tracked institutions
         hold this name, at what tier, and what has their
         recent activity been (new position, increase,
         volume accumulation pattern);
     (3) VALUATION OR STRUCTURAL CONTEXT — is the stock
         trading at a discount to sector peers, and if so
         by how much; or what is the secular structural
         trend supporting the position;
     (4) SCENARIO SUMMARY — what happens in bull, base,
         and bear scenarios and at what probability;
     (5) MACRO CONTEXT — current regime and whether it
         supports or constrains this position.
     Write in plain English. Be specific. Cite numbers
     from the underlying data where available.",

  thesis_invalidation:
    "specific condition proving thesis wrong —
     not a price level but an event or data point",

  exit_discipline: {
    near_term:
      "exit within 2 sessions of catalyst resolution
       regardless of direction",
    medium_term:
      "rescore monthly — exit if CCS < 35 or valuation
       gap closes without catalyst",
    structural:
      "annual review — exit if secular thesis broken
       or valuation fully realised"
  },

  execution_flags: {
    avg_daily_volume_$:         float,
    liquidity_flag:             "normal|caution|illiquid",
    catalyst_date_weekend_flag: boolean,
    days_to_12month_ltcg:       int,
    suggested_entry_method:     "market|limit_0.5pct|limit_1pct"
  },

  alert_priority:           "SMS_immediate|daily_digest|weekly_summary",
  regime_at_recommendation: string,
  operating_mode:           "LIVE|SHADOW|HISTORICAL",

  calendar_actions_created: [action_id]
  // list of Module 12 action IDs auto-created by this recommendation
}

4.5 Alert Framework
SMS / push — within minutes:

Catalyst resolution on any held position
DEPENDENCY_VIOLATED on any held position
Tier 1A Form 4 "P" >$1M in watchlist name
SURVIVAL_WARNING on any held position
Layer 0 regime change

Daily digest — morning before market open:

New watchlist entries CCS >55
PROBABILITY_UPGRADE or PROBABILITY_DOWNGRADE >10pp
INSTITUTIONAL_EXIT Tier 1A/1B on held positions
Weekly CCS rescore results
Positions within 2 weeks of thesis_review_date

Weekly summary — Sunday evening:

Universe update from Layer −1
Sleeve allocation drift report
Cash reserve level (below $3,000 → no new entries flag)
Positions within 60 days of 12-month LTCG threshold
Module 12 upcoming actions next 14 days

Full alert type registry:
AlertTriggerPriorityNEW_UNIVERSE_ENTRYTicker promoted to activeWeeklyQOQ_ACCUMULATION_SIGNALTier 1A/1B new or >10% increaseDailyINSIDER_PURCHASE_ALERTForm 4 "P" any amountSMSNEW_CATALYST_DETECTEDCompany enters watchlistDailyPROBABILITY_UPGRADEBull +>10ppDailyPROBABILITY_DOWNGRADEBear +>10ppDailyCONTRADICTION_FLAGGEDNew signal contradicts existingDailyOPTIONS_DIVERGENCEMarket vs registry EV >15%DailySURVIVAL_WARNINGCash runway <3 quartersSMSDEPENDENCY_VIOLATEDAny dependency violatedSMSINSTITUTIONAL_EXITTier 1A/1B exitsDailyCROWDING_FLAG_SET>4 institutions + >50% appreciationDailyREGIME_CHANGELayer 0 regime tag changesSMSLTCG_APPROACHING60 days to 12-month thresholdWeeklyCASH_RESERVE_LOWCash reserve <$3,000Daily

4.6 Feedback Loop — Layer 4
LAYER_4_CALIBRATION {
  cadence: "quarterly"

  CCS_band_thresholds: {
    method:
      "compute Sharpe ratio contribution by CCS band
       at recommendation time
       if 70-84 band outperforms 85-100 band on Sharpe:
         merge bands or lower 85 threshold
       test thresholds 40/45/50/55 in shadow mode
       select threshold maximising Sharpe ratio"
    minimum_observations: 20 per band
  }

  position_size_multipliers: {
    method:
      "compare risk-adjusted returns for positions
       where multipliers were applied vs. not applied
       adjust multiplier values proportionally
       to observed return differentials"
    minimum_observations: 15 per multiplier condition
  }

  watchlist_entry_threshold: {
    current:     45
    test_values: [40, 45, 50, 55]
    method:      "shadow mode comparison across all test values"
    selection:   "threshold maximising Sharpe ratio"
  }

  sell_trigger_threshold: {
    current: 35
    method:
      "compare fixed_horizon_return vs model_exit_return
       if model_exit_return systematically lower:
         sell triggers firing too early — raise threshold
       if model holds losers too long:
         lower threshold"
    minimum_observations: 20 completed exits
  }
}

