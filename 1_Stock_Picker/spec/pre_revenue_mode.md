# Pre-Revenue Mode

## Purpose
Suspends the standard qualitative gate for pre-revenue companies.
Entry governed entirely by CCS and catalyst quality rather than
financial metrics.

## Entry rules
[copy PRE_REVENUE_ENTRY_RULES block from TimeStamp9]

## Cross-layer effects
- Layer 2: catalyst_requirement check applied during extraction
- Layer 3: survival_threshold of 0.70 used instead of 0.50
           cash_runway_quarters checked against 3-quarter minimum
           (4-quarter minimum in FULL_RISK_OFF regime)
- Layer 4: position size capped one tier below CCS-implied
           bear-scenario dollar loss limit still applies
- Layer 7: pre-revenue tickers from ClinicalTrials.gov pipeline
           included in historical simulation