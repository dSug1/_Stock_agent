# Layer -1 — Institutional Universe Construction
Schedule: Quarterly on 13F release dates + continuous Form 4/13G/13D monitoring.
Point-in-time rule: All processing uses filing_date field exclusively. Never use period_of_report date.

−1.1 Tracked Institution Registry
25-35 institutions. Annually recalibrated per section −1.5.
Tier 1A — Sector specialist, deep diligence (multiplier: 4.0×)
| Institution | Primary coverage |
|---|---|
| Baker Bros. Advisors | Biotech — clinical stage, long hold through binary events |
| RA Capital Management | Biotech/medtech — public/private crossover, early clinical |
| Perceptive Advisors | Clinical-stage biotech, oncology and rare disease |
| Boxer Capital (Tavistock) | Small/microcap biotech |
| Deerfield Management | Healthcare — equity and royalty structures |
| Goehring & Rozencwajg | Natural resources, commodity royalties |
| OrbiMed Advisors | Global healthcare — public and private, all stages |
| BVF Inc. | Biotech — deep value, activist, long/short |
| RTW Investments | Biotech/medtech — royalties, public and private crossover |
| Redmile Group | Biotech/healthcare — growth stage, long-term hold |
| Cormorant Asset Management | Clinical-stage biotech — concentrated, high conviction |
| Sio Capital Management | Biotech — small/midcap, event-driven |
| ARCH Venture Partners | Deep early-stage biotech — pre-IPO and early public |
| Samsara BioCapital | Clinical-stage biotech — science-first, concentrated |
| Sofinnova Partners | European biotech — early stage, life sciences |
Tier 1B — High-conviction generalist superinvestor (multiplier: 3.5×)
InstitutionPrimary coverageBaupost Group (Klarman)Deep value, cross-sector distressPershing Square (Ackman)Concentrated activist, cross-sectorAppaloosa Management (Tepper)Macro-aware, cross-sectorThird Point (Loeb)Activist, cross-sector, corporate eventsBerkshire HathawayConsumer staples, financials, energy, insurance
Tier 2A — Generalist deep-value, concentrated (multiplier: 2.5×)
Greenlight Capital, Gotham Asset Management, Ariel Investments, Oakmark Funds
Tier 2B — Sector specialist, broader mandate (multiplier: 2.0×)
Coatue Management, Whale Rock Capital, Horizon Kinetics, Orbis Investment Management
Tier 3 — Quality institutional, active management (multiplier: 1.0×)
Fidelity active funds, T. Rowe Price active, Wellington Management active, Royce & Associates
Tier 4 — Large passive / index (multiplier: 0.0×)
Vanguard index, BlackRock iShares, SPDR index products. Zero signal value.

−1.2 Quarterly 13F Processing
Step 1 — Position change classification:
Change typeDefinitionSignal priorityNew positionNot held last quarterHighestSignificant increase>15% QoQ (>10% for Tier 1A/1B)HighModerate increase5-15% QoQMediumFlat / minor<5% QoQ changeLow — no triggerDecrease>5% QoQ reductionNegative signalExitHeld last quarter, gone this quarterNegative — exit alert
Step 2 — Tier-Weighted Ownership Score (TWOS) per ticker:
TWOS = Σ over all tracked institutions of:
  (institution_ownership_pct
   × tier_multiplier
   × change_momentum_factor)

change_momentum_factor:
  new_position:         2.0
  significant_increase: 1.5
  moderate_increase:    1.2
  flat:                 1.0
  decrease:             0.7
  exit:                 0.0
Step 3 — Processing tier assignment:
Active monitoring — full Layer 0-4 processing, press wire tracked daily. Estimated 150-250 tickers.
Criteria (any one sufficient):

TWOS above sector-calibrated threshold
Any Tier 1A/1B institution initiated new position this quarter
Any Tier 1A/1B institution increased >10% QoQ
Any institution increased >15% QoQ and TWOS above minimum threshold

Passive monitoring — SEC EDGAR only, no press wire. Estimated 300-500 tickers.
Criteria: Held by Tier 2-3 institutions, no significant recent change. TWOS above minimum floor but below active threshold.
Watchlist only — quarterly 13F check, no ongoing processing.
Criteria: Single Tier 3 institution, decreasing or flat. TWOS below minimum floor.
Crowding penalty:
If >4 tracked institutions hold a name AND appreciation >50% since earliest tracked institution initiated → apply 0.6× to TWOS. Set crowding_flag: true in Layer 3 registry.

−1.3 Continuous Form 4 / 13G/13D Processing
Form 4 (transaction code "P" only):
Any "P" transaction → immediate elevation to active monitoring + INSIDER_PURCHASE_ALERT in Layer 4 + INSIDER_PURCHASE_MONITOR action in Module 12.
13D/13G:

SC 13D (activist >5%): immediate active monitoring + highest-priority alert
SC 13G (passive >5%): active monitoring if filer is Tier 1A/1B/2A
SC 13G/A amendment >1% increase: treated as significant increase signal

Insider signal source tiering:
SourceTierBull probability adjustmentC-suite open market purchase >$500K1+8%SC 13D activist stake initiation1+8%Director open market purchase >$100K2+5%Tier 1A/1B 13F new position2+5%Tier 2A/2B 13F new position3+3%Standard institutional 13F increase >15% QoQ4+1%Index fund rebalance (Tier 4)50%

−1.4 Supplementary Discovery Pipeline
Source 1 — ClinicalTrials.gov:
All biotech/pharma with IND filing or trial registration in last 24 months, regardless of institutional ownership. Passive monitoring intensity. Auto-promoted to active when Tier 1A/1B 13F position first appears.
Source 2 — EDGAR new issuer screening:
All companies filing first 10-K or S-1 in last 12 months within biotech, medtech, specialty pharma SIC codes. Watchlist intensity only.
Source 3 — Press wire orphan detection:
Any company not in current universe generating Tier 1 or Tier 2 keyword hit receives one-time Layer 2 extraction. If extraction produces transformative or significant magnitude_class → enters passive monitoring pending next 13F cycle.

−1.5 Annual Institution Recalibration
RECALIBRATION_RULES {

  performance_metric:
    "hit_rate = pct of initiated positions outperforming
     GICS sub-industry median over following 12 months"

  tier_adjustments:
    promote_one_tier:
      condition: "hit_rate > 65% AND avg_alpha > 10%"
      floor: "Tier 1A"
    demote_one_tier:
      condition: "hit_rate < 45% OR avg_alpha < 0%
                  over two consecutive years"
    remove_from_list:
      condition: "consistent Tier 4 behaviour detected
                  (mechanical rebalancing pattern)"

  new_institution_addition:
    trigger: "untracked fund appears as new position initiator
              in >3 active monitoring tickers in one quarter"
    action:  "flag for tier assessment"

  multiplier_recalibration:
    method: "regression of position-level returns
             grouped by institution tier"
    output: "updated tier multipliers replacing defaults"
    minimum_observations: 30

  calendar_action_created:
    "ANNUAL_INSTITUTION_RECALIBRATION action in Module 12
     due every January 15 — reminder 30 days and 14 days prior"
}

−1.6 Feedback Loop Parameters — Layer −1
ParameterFeedback valueCompute costCadenceInstitution count (35-42)ModerateLowAnnualTier multipliers (4.0/3.5/2.5/2.0/1.0/0.0)HighZeroQuarterlyChange momentum factors (2.0/1.5/1.2/1.0/0.7/0.0)Moderate-highZeroQuarterlySignificant increase threshold (15%/10%)Low-moderateLowAnnualCrowding penalty threshold (>4 institutions, >50%)ModerateZeroAnnual
MULTIPLIER_CALIBRATION_METHOD {
  fit_regression:
    dependent:              "position_return_at_horizon"
    independent:            "tier_multiplier × change_momentum_factor"
    regularisation:         "L2 (Ridge)"
    max_parameters:         10
    minimum_observations:   30 per tier
    output:                 "updated multiplier values"
}

