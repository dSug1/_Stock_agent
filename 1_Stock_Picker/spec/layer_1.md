# Layer 1 — Document Ingestion & Pre-Filtering
Subscribes to: active_monitoring_universe from Layer −1.
Passive monitoring tickers: SEC EDGAR sub-pipeline only — no press wire.

1.1 SEC EDGAR Pipeline
EDGAR_PIPELINE {
  source:       "EDGAR full-text RSS feed + bulk download index",
  tickers:      "active_monitoring + passive_monitoring universes",
  filing_types: ["8-K", "10-Q", "10-K", "Form4"],

  retention_rules: {
    "8-K":   "items 1.01, 2.02, 4.02, 7.01, 8.01, 9.01",
    "10-Q":  "all — for cash runway and financial snapshot updates",
    "10-K":  "all — for annual financial snapshot",
    "Form4": "transaction_code == 'P' only"
  },

  discard_rules: [
    "routine Section 16 reports with no P transactions",
    "8-K items not in retention list",
    "amended filings where original already processed"
  ]
}

1.2 Press Wire Pipeline
Active monitoring tickers only.
RSS from Business Wire, PR Newswire, GlobeNewswire, AccessWire.
Tiered keyword pre-filter:
KEYWORD_FILTER {

  tier_1_regulatory: [
    "FDA", "EMA", "NDA", "BLA", "IND", "PDUFA",
    "CRL", "complete response letter",
    "breakthrough designation", "fast track",
    "accelerated approval", "priority review",
    "orphan drug", "advisory committee"
  ],

  tier_2_clinical: [
    "Phase 1", "Phase 2", "Phase 3",
    "interim analysis", "primary endpoint",
    "overall survival", "progression-free survival",
    "objective response rate", "hazard ratio",
    "p-value", "dose-limiting toxicity",
    "maximum tolerated dose", "DSMB",
    "data safety monitoring board",
    "trial discontinuation", "futility analysis"
  ],

  tier_3_corporate: [
    "merger", "acquisition", "licensing",
    "partnership", "collaboration",
    "equity offering", "shelf registration",
    "reverse split", "going concern",
    "covenant breach", "CEO", "CFO"
  ],

  tier_4_financial: [
    "cash runway", "operating cash burn",
    "quarterly cash position", "ATM offering", "PIPE"
  ],

  pass_conditions: {
    always_pass:      "≥1 Tier 1 OR Tier 2 keyword hit",
    conditional_pass: "≥2 Tier 3/4 keyword hits",
    active_only_pass: "≥1 Tier 3/4 hit AND ticker in active_monitoring",
    always_reject:    "single Tier 3/4 hit AND ticker in passive/watchlist"
  }
}
Estimated filter reduction: ~70% of raw document volume before LLM processing.

1.3 Feedback Loop — Layer 1
Keyword vocabulary — monthly random audit:
KEYWORD_AUDIT {
  cadence: "monthly"
  method:
    "sample 5% of filtered-out documents randomly
     run sampled documents through Layer 2 extraction
     flag any extraction producing magnitude_class
     of significant or transformative
     add missed keywords to appropriate tier"
  cost:
    "~35-50 documents × 600 tokens ≈ $0.10/month"
  output:
    "updated keyword vocabulary list"
    "false_negative_rate by keyword tier"
  calendar_action:
    "MONTHLY_KEYWORD_AUDIT in Module 12
     due first Monday of each month
     reminder: none — auto-generated"
}
Pass condition thresholds — quarterly calibration:
PASS_THRESHOLD_CALIBRATION {
  cadence: "quarterly"
  method:
    "track confidence_in_extraction for all
     documents that passed the filter
     if median confidence < 0.5 for a keyword tier:
       tighten that tier's pass condition
     if false_negative_rate > 5% from monthly audit:
       relax that tier's pass condition"
  note:
    "tune separately for active vs passive monitoring.
     do not apply relaxed thresholds to watchlist tickers."
}


