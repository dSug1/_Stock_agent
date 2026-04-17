# Layer 7 — Historical Simulation Harness

7.1 Data Assembly
HISTORICAL_DATA_SOURCES {

  simulation_period: "January 2020 to December 2024",
  // 5 years — includes COVID volatility, rate cycle,
  // biotech boom/bust — sufficient regime diversity

  sources: {

    SEC_EDGAR: {
      types:    ["8-K", "10-Q", "10-K", "Form4",
                 "13F", "13D", "13G"],
      method:   "EDGAR bulk download + full-text index",
      key_rule: "use filing_date field exclusively —
                 never period_of_report",
      cost:     "free"
    },

    price_history: {
      source:            "Polygon.io or Nasdaq Data Link",
      fields:            ["open", "high", "low", "close",
                          "volume", "vwap"],
      includes_delisted: true,
      // survivorship-bias-free — mandatory
      cost:              "$29-79/month depending on tier"
    },

    macro_data: {
      source: "FRED API",
      fields: ["DFF",           // Fed Funds rate
               "CPIAUCSL",      // CPI
               "PCEPI",         // PCE
               "T10Y2Y",        // yield curve
               "BAMLC0A0CM",    // IG spreads
               "BAMLH0A0HYM2"], // HY spreads
      VIX_source: "CBOE via yfinance",
      cost: "free"
    },

    clinical_trials: {
      source: "ClinicalTrials.gov API historical records",
      fields: ["registration_date", "status_history",
               "results_posting_date",
               "primary_completion_date"],
      cost: "free"
    },

    options_history: {
      source: "Polygon.io options data",
      fields: ["implied_volatility", "put_call_ratio",
               "open_interest"],
      cost:   "$50-200/month — optional for initial simulation"
    }
  }
}

7.2 Point-in-Time Enforcement
POINT_IN_TIME_RULES {

  13F_filings:
    "use filing_date not period_of_report
     Q1 13F (period: March 31) filed May 14:
     available in simulation only from May 14"

  10Q_10K_financial_data:
    "use filing_date for all financial snapshots
     Q1 financials filed May 1:
     cash_runway updates in registry only from May 1"

  earnings_data:
    "use announcement_date
     never use period_end_date for
     earnings momentum calculations"

  price_data:
    "use only prices with date <= simulation_date
     no forward-looking price data permitted"

  enforcement_mechanism:
    "all data access filtered by:
     data_date <= current_simulation_date
     implemented as database query constraint
     not as application-level check"
}

7.3 LLM Knowledge Contamination Management
LLM_CONTAMINATION_PROTOCOL {

  problem:
    "LLM trained on data including outcomes of
     historical events cannot simulate genuine
     uncertainty about those outcomes"

  mitigation_1_extraction_only_mode:
    "HISTORICAL mode: Layer 2 extracts structure only
     no LLM probability adjustment applied
     base rates applied directly from hardcoded table
     llm_probability_adjustment_applied = false"

  mitigation_2_separate_validation:
    "extraction accuracy (did LLM correctly identify
     catalyst type, magnitude, time_horizon?)
     validated separately from prediction accuracy
     using ground-truth labelled historical events"

  mitigation_3_explicit_flagging:
    "all calibration parameters derived from
     historical simulation flagged as:
     potentially_optimistic_due_to_llm_contamination
     first 6 months of LIVE operation serve as
     true out-of-sample validation"

  contamination_detection:
    "if live_brier_score > historical_brier_score × 1.25:
     LLM contamination effect confirmed
     action: tighten all adjustment bounds by 50%
             in Layer 2 PROBABILITY_PRIORS"

  calendar_action:
    "CONTAMINATION_CHECK action in Module 12
     due 3 months after LIVE mode launch
     priority: high"
}

7.4 Simulation Execution Loop
python# Pseudocode — implementation target for Claude Code

def run_historical_simulation(
    start_date: date,
    end_date: date,
    operating_mode: str = "HISTORICAL"
):
    # Phase 1 — initialise
    universe = {}
    registry = {}
    shadow_portfolio = ShadowPortfolio()
    outcome_tracker = OutcomeTracker()

    for trading_day in business_days(start_date, end_date):

        # Layer -1 — reconstruct universe as of trading_day
        # strict point-in-time: filing_date <= trading_day
        if is_13F_release_date(trading_day):
            available_13Fs = fetch_13Fs(
                filing_date_lte=trading_day
            )
            universe = reconstruct_TWOS(available_13Fs)
            active_monitoring = assign_processing_tiers(universe)

        # Layer 0 — macro regime
        macro_data = fetch_macro_data(date=trading_day)
        regime = classify_regime(macro_data)

        # Layer 1 — document ingestion
        new_documents = fetch_filings(
            date=trading_day,
            tickers=active_monitoring
        )
        filtered = apply_keyword_filter(new_documents)

        # Layer 2 — extraction only (HISTORICAL mode)
        for doc in filtered:
            extraction = llm_extract(
                document=doc,
                mode="HISTORICAL",
                base_rates=PROBABILITY_PRIORS
                # no LLM probability adjustment
            )
            # Layer 3 — with deduplication
            registry = update_registry_with_deduplication(
                registry, extraction, trading_day
            )

        # Layer 3 — CCS computation
        prices = fetch_prices(
            date=trading_day,
            tickers=list(registry.keys())
        )
        for ticker in registry:
            registry[ticker].CCS = compute_CCS(
                registry[ticker], regime
            )
            check_sell_triggers(
                registry[ticker], prices[ticker]
            )

        # Layer 4 — signal generation
        signals = generate_signals(registry, trading_day)

        # Shadow portfolio execution
        for signal in signals:
            shadow_portfolio.execute(
                signal=signal,
                price=prices[signal.ticker],
                date=trading_day
            )

        # Layer 5 — record outcomes for resolved positions
        for position in shadow_portfolio.check_resolved(
            trading_day, prices
        ):
            outcome_tracker.record(position)

    # Layer 6 — calibrate on historical outcomes
    calibration_report = calibrate_parameters(
        outcome_tracker.all_outcomes()
    )

    return calibration_report

7.5 Compute Cost Estimates
SIMULATION_COST {

  data_assembly: {
    EDGAR_download:  "free — one-time, hours",
    price_history:   "$29-79/month during development",
    FRED_macro:      "free",
    clinical_trials: "free"
  },

  layer_2_extraction: {
    volume:
      "500 tickers × 3 docs/month × 60 months
       = 90,000 extractions",
    tokens_per_extraction: 600,
    total_tokens:          54000000,
    cost_at_3_per_million: "$162 total one-time cost"
  },

  runtime: {
    data_assembly: "hours — one-time download",
    layer_minus1:  "minutes — pure data processing",
    layer_0_to_1:  "minutes",
    layer_2:       "24-48 hours wall clock — rate limited,
                    run overnight in batches",
    layer_3_to_4:  "minutes after Layer 2 complete",
    total:         "approximately 24-48 hours unattended"
  },

  ongoing_live_cost: {
    layer_2_monthly: "$2.70/month at 500 tickers",
    layer_1_audit:   "$0.10/month",
    prompt_testing:  "$0.90 per variant — max 2/quarter",
    total_monthly:   "approximately $5-10/month"
  }
}

7.6 Warm-Up Period Governance
WARMUP_GOVERNANCE {

  if historical_simulation_completed_before_launch: {
    warm_up_period:         "none required",
    initial_parameters:     "from historical calibration report",
    first_live_calibration: "after 3 months live operation",
    validation_check:
      "compare live Brier score vs historical Brier score
       at 3-month mark
       if degradation > 25%: tighten LLM adjustment bounds"
  },

  if historical_simulation_not_completed: {
    warm_up_period:     "6 months shadow/live operation",
    initial_parameters: "hardcoded defaults",
    first_calibration:  "after 6 months",
    note: "do not update parameters before minimum
           observation counts are met"
  }
}


