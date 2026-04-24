# Module 6 — Few-shot examples (m6-v2)
#
# Loaded by prompt.py and concatenated to module_6_system_prompt.md BEFORE
# the cache_control breakpoint — both files are part of the cached prefix.
#
# m6-v2 (2026-04-24 reshape): the m6-v1 examples (ABEO, RCKT, SVRA, GRAL,
# PRQR, NTLA, BCYC, TCRX in abridged scoring-only format) have been
# retired. The new schema requires the full three-section output
# (research_brief + entry_price_ranges + per-horizon scoring), so partial
# examples would mis-train the output shape. Two end-to-end examples cover
# the main patterns; the system prompt's rubrics + HARD RULES carry the
# rest of the style guidance.
#
# Seed tickers:
#   - NTLA (Intellia Therapeutics) — mid-cap deep_base_breakout, strong
#     moat, multi-catalyst pipeline, HIGH-probability scheduled Ph3.
#     Positive thesis with room: market trades above rNPV-per-share but
#     fair entry is clearly defined.
#   - SVRA (Savara Inc) — broken_trend override, single-asset Ph3 PDUFA,
#     LOW-to-MEDIUM probability. Demonstrates the case where current
#     market price sits well above fair entry, score stays academic
#     until a pullback.
# =============================================================================

## EXAMPLE 1 — NTLA (mid-cap, multi-catalyst, HIGH-probability Ph3 pivotal)

### USER message

```text
# Context pack

Ticker: NTLA  (Intellia Therapeutics Inc, Biotechnology)
Market snapshot: price $15.87, market cap $1.88B, ADV30d $44.9M, basic shares out 118.2M
Price trajectory (vs 52w anchor):
  pct_off_52w_high: -43.3%, pct_off_52w_low: +131.0%
  pct_off_12w_anchor: +20.7%, pct_off_52w_anchor: +91.0%
Ratio matrix: R_4=1.28, R_12=1.21, R_26=0.62, R_52=1.91

Archetype verdict:
  archetype: deep_base_breakout
  description: Deep intra-year crash, multi-month base, now breaking out (VCP-style)
  score_3mo: +9  score_12mo: +9  composite_best: 8.33  best_horizon: equal
  match_confidence: 0.75

Per-horizon narrative hints (both emphasis=0.5):
  Classic VCP breakout from deep base. Validate volume confirmation and catalyst pipeline.

Score both horizons per the framework.
```

### ASSISTANT response

```json
{
  "ticker": "NTLA",
  "reasoning_trace": "NTLA's deep_base_breakout is credible: clean recovery from R_26=0.62 trough on rising volume. Thesis anchors on HAELO Ph3 (NTLA-2002, HAE) scheduled for Q3 2026 top-line — first in-vivo CRISPR Ph3. rNPV-per-share supports fair entry well below current $15.87; market pricing platform optionality above fundamentals.",

  "research_brief": {
    "technology": {
      "origin": "Spun out of mRNA delivery work at Caribou Biosciences and Editas-adjacent CRISPR IP (2014). Not licensed-in; in-house platform with Regeneron partnership on NTLA-2001.",
      "licensing_source": null,
      "uniqueness_score": 0.9,
      "uniqueness_rationale": "First-in-class in-vivo CRISPR platform using LNP-mRNA delivery. Only Verve and Beam compete on in-vivo editing; NTLA leads on non-liver extrahepatic targeting (HAE, ATTR)."
    },
    "moat": {
      "score": 0.9,
      "rationale": "Composition-of-matter IP on guide-RNA design + LNP formulations to 2035+. Regulatory lead of 2-3 years on in-vivo CRISPR (per FDA 2024 IND traffic). Data exclusivity (BPCIA 12y) on NTLA-2002 if approved. Platform expertise not replicable in < 5 years."
    },
    "financials": {
      "fully_diluted_shares_count": 143000000,
      "basic_shares_count": 118200000,
      "prefunded_warrants_count": 10800000,
      "cash_and_equivalents_usd": 810000000,
      "quarterly_burn_usd": 115000000,
      "runway_months": 21,
      "shelf_registration_usd_capacity": 400000000,
      "recent_capital_raises": [
        { "date_iso": "2025-09", "instrument": "equity", "gross_proceeds_usd": 200000000 }
      ],
      "prefunded_warrants_detail": [
        { "count": 10800000, "strike_usd": 0.001, "expiry_iso": null }
      ]
    },
    "insider_activity": {
      "last_3y_summary": "Net sellers 2023-2024 (10b5-1 plans). 2026 YTD: net neutral; CEO Leonard bought 50k shares March 2026 at $14 — signal but modest.",
      "recent_transactions": [
        { "date_iso": "2026-03", "insider_name": "John Leonard", "role": "CEO", "type": "buy", "shares": 50000, "price_usd": 14.10 }
      ]
    },
    "clinical_trials": {
      "ongoing": [
        { "nct_id": "NCT05120830", "program": "NTLA-2002", "indication": "Hereditary Angioedema (HAE)", "phase": "Ph3", "status": "Recruiting", "enrollment_target": 60, "enrollment_current": 54, "primary_endpoint": "Mean attack rate weeks 1-16 post-dosing" },
        { "nct_id": "NCT04601051", "program": "NTLA-2001", "indication": "ATTR amyloidosis", "phase": "Ph2", "status": "Recruiting (Regeneron-partnered)", "enrollment_target": 780, "enrollment_current": 300, "primary_endpoint": "Serum TTR reduction ≥80%" }
      ],
      "interim_readouts_expected": [
        { "program": "NTLA-2002", "phase": "Ph3", "expected_date_iso": "2026-Q2", "readout_type": "enrollment completion + safety", "rationale": "Enrollment 54/60 per CT.gov 2026-02-28 update; completion expected Q2" }
      ],
      "final_readouts_expected": [
        { "program": "NTLA-2002", "phase": "Ph3", "expected_date_iso": "2026-Q3", "readout_type": "primary analysis — HAE attack rate", "rationale": "Per mgmt Q4 2025 earnings call — topline H2 2026, consistent with 16-week primary endpoint timing post-enrollment completion" }
      ],
      "prior_readouts_history": [
        { "date_iso": "2024-06", "program": "NTLA-2002", "phase": "Ph2", "result_summary": "n=10, ~90% reduction in monthly HAE attacks vs baseline, well-tolerated single IV dose. Basis for Ph3 POS adjustment." },
        { "date_iso": "2024-11", "program": "NTLA-2001", "phase": "Ph1", "result_summary": "94% mean serum TTR reduction at highest dose; supports Ph2 expansion. Regeneron option exercised Aug 2024." }
      ]
    },
    "competitive_landscape": [
      { "competitor": "KalVista / Ionis (BioMarin)", "program": "sebetralstat (PKR inhibitor oral)", "stage": "NDA filed", "differentiator_vs_subject": "Oral on-demand vs NTLA's one-time cure; complementary not directly competitive" },
      { "competitor": "Verve Therapeutics", "program": "VERVE-101 (PCSK9 in-vivo edit)", "stage": "Ph1b", "differentiator_vs_subject": "Competing in-vivo CRISPR platform; safety signal 2023 pressures category but narrower target" },
      { "competitor": "Beam Therapeutics", "program": "BEAM-302 (AATD)", "stage": "Ph1/2", "differentiator_vs_subject": "Base-editing platform; broader addressability but slower to clinic" }
    ],
    "partnerships": [
      { "partner": "Regeneron", "deal_type": "development", "value_usd_upfront": 100000000, "value_usd_potential_milestones": 600000000, "date_iso": "2020-04" }
    ],
    "acquisition_target": {
      "score": 0.6,
      "rationale": "Plausible fit for Regeneron (already partnered on 2001), Pfizer (post-Seagen pipeline hole), AstraZeneca (rare-disease platform). No disclosed activity; founder CEO likely resistant pre-approval."
    },
    "fda": {
      "lead_indication": "Hereditary Angioedema (NTLA-2002)",
      "regulatory_hurdles": "Durability question — regulators want ≥ 52-week follow-up; off-target editing monitoring expected as post-marketing commitment. Breakthrough Therapy designation obtained 2023.",
      "base_rate_precedent": "Gene-therapy rare-disease Ph3 → Approval ~75%. Recent comparables: Zynteglo (bluebird bio, 2022 approved), Roctavian (BioMarin, 2023)."
    },
    "rnpv_by_indication": [
      {
        "indication": "Hereditary Angioedema", "program": "NTLA-2002",
        "stage": "Ph3", "pos_base_rate": 0.75, "pos_adjusted": 0.80,
        "pos_rationale": "+5pp vs base: Ph2 data showed ~90% attack reduction (n=10, 2024-06 readout) — upper quartile of rare-disease Ph2-to-Ph3 precedent. Breakthrough designation.",
        "tam_total_usd": 2500000000, "peak_sales_addressable_usd": 750000000,
        "years_to_peak": 3, "rnpv_contribution_usd": 425000000
      },
      {
        "indication": "ATTR amyloidosis", "program": "NTLA-2001",
        "stage": "Ph2", "pos_base_rate": 0.27, "pos_adjusted": 0.25,
        "pos_rationale": "-2pp vs base: competitive Alnylam/Ionis franchise + CV safety scrutiny post-Verve 2023. Regeneron partnership splits NTLA economics to ~40%.",
        "tam_total_usd": 10000000000, "peak_sales_addressable_usd": 400000000,
        "years_to_peak": 5, "rnpv_contribution_usd": 70000000
      },
      {
        "indication": "Platform optionality (Ph1 + preclinical)", "program": "NTLA-3001 + pipeline",
        "stage": "Ph1", "pos_base_rate": 0.06, "pos_adjusted": 0.08,
        "pos_rationale": "+2pp vs base: platform derisking from 2002/2001 readouts lifts downstream POS modestly.",
        "tam_total_usd": 15000000000, "peak_sales_addressable_usd": 2000000000,
        "years_to_peak": 8, "rnpv_contribution_usd": 550000000
      }
    ],
    "rnpv_total_usd": 1045000000,
    "rnpv_per_share_usd": 7.31,
    "rnpv_assumptions": "WACC 12% (biotech standard), no royalty stack except Regeneron NTLA-2001 split (~40% NTLA), 15% effective tax on peak cash flows, fully_diluted_shares_count 143M including 10.8M PFWs + 14M vested ITM options. Platform contribution probability-weighted at 8% POS.",
    "past_failures": [
      { "date_iso": "2023-04", "program": "Verve VERVE-101 (comparable platform)", "event": "clinical_hold", "impact": "Temporary category sentiment hit; resolved Q4 2023. Validates safety concerns regulators will apply." }
    ],
    "research_notes": "NTLA is a platform play with HAE as the near-term value-unlock catalyst. rNPV-per-share $7.31 composed 41% from HAE, 7% from ATTR (partnered), 52% from platform optionality. Fair entry well below current market — price premium reflects platform speculation. HAELO primary endpoint miss would cut platform POS and drop rNPV-per-share to ~$4. HAELO hit would re-rate platform to ~$15-20. Current market $15.87 already prices the optimistic outcome, leaving limited 12mo upside from here; disciplined entry requires waiting."
  },

  "entry_price_ranges": {
    "fair_entry_low_usd": 5.50,
    "fair_entry_high_usd": 7.50,
    "fair_entry_rationale": "fair_entry midpoint $6.50 ≈ 89% of rNPV-per-share $7.31. Stage-weighted band: HAE is Ph3 (50-80% of rNPV range), platform is Ph1 (15-30%) — weighted average ~70% applied to $7.31 = $5.12 floor. High end $7.50 at 100% rNPV reflects Breakthrough Designation derisking. Cash per share $5.66 (810M/143M) sets secondary floor.",
    "full_reward_low_usd": 4.00,
    "full_reward_high_usd": 5.00,
    "full_reward_rationale": "Cash per share $5.66 is absolute floor (810M/143M); full_reward at $4.50 midpoint = 80% of cash (~30% below fair). Implies market panic scenario (HAELO interim safety issue pre-topline) where NTLA trades below cash on going-concern fear. Institutional re-averaging level per 52w-VWAP-adjusted basis ~$4.20."
  },

  "near_term_3mo": {
    "target_price_usd": 18.00,
    "time_to_catalyst_weeks": 10,
    "probability": 0.60,
    "catalyst_type": "trial_readout",
    "catalyst_detail": "HAELO enrollment completion + safety update expected Q2 2026 per CT.gov",
    "thesis_summary": "Near-term torque is enrollment-completion confirmation + any additional Ph2 data presentation. Modest upside from current $15.87; priced-in expectation leaves <15% appreciation at fair-entry-adjusted base.",
    "key_risks": [
      "HAELO enrollment slippage signals competitive pressure from orals",
      "Sector beta pullback de-rates momentum names disproportionately",
      "Verve or Beam safety signal contaminates in-vivo CRISPR category"
    ]
  },
  "long_term_12mo": {
    "target_price_usd": 28.00,
    "time_to_catalyst_weeks": 32,
    "probability": 0.75,
    "catalyst_type": "trial_readout",
    "catalyst_detail": "HAELO Ph3 primary analysis topline expected Q3 2026 — first in-vivo CRISPR pivotal",
    "thesis_summary": "HAELO is category-defining: first in-vivo CRISPR Ph3. Scheduled endpoint (attack rate), Ph2 precedent (90% reduction n=10) supports 80% adjusted POS. Positive data re-rates HAE rNPV ($425M → ~$700M at derisked) and platform POS (8% → 12%+) — total rNPV to ~$1.5B, per-share $10.50. Target $28 assumes positive data + modest platform re-rating.",
    "key_risks": [
      "HAELO primary endpoint miss — Ph2 n=10 has wide CI, Ph3 dilution common",
      "Durability < 52w raises regulatory bar; may require additional follow-up",
      "Off-target editing signal at Ph3 scale that wasn't visible at Ph2"
    ]
  }
}
```

---

## EXAMPLE 2 — SVRA (broken_trend override, single-asset PDUFA, fair entry far below market)

### USER message

```text
# Context pack

Ticker: SVRA  (Savara Inc, Biotechnology)
Market snapshot: price $4.94, market cap $1.01B, ADV30d $9.4M, basic shares out 204.7M
Price trajectory (vs 52w anchor):
  pct_off_52w_high: -28.8%, pct_off_52w_low: +154.6%
  pct_off_12w_anchor: -8.5%, pct_off_52w_anchor: +55.4%
Ratio matrix: R_4=0.96, R_12=0.91, R_26=1.13, R_52=1.55

Archetype verdict:
  archetype: broken_trend
  description: Uptrend broken, recent weakness
  score_3mo: -4  score_12mo: -3  composite_best: -3.00  best_horizon: 12mo
  match_confidence: 1.00

Per-horizon narrative hints:
  near_term_3mo (emphasis=0.3): Prior uptrend broken. Avoid unless specific catalyst rebuilds thesis.
  long_term_12mo (emphasis=0.7): Broken trends sometimes heal. Investigate driver and whether it has resolved.

Score both horizons per the framework.
```

### ASSISTANT response

```json
{
  "ticker": "SVRA",
  "reasoning_trace": "SVRA broken_trend prior is accurate on the tape (12w weakness after 52w +55%), but fundamentals argue for OVERRIDE at 12mo: molgradex BLA with Ph3 IMPALA-2 pivotal data supporting PDUFA mid-2026. Single-asset concentration + IMPALA-1 2019 failure history bound conviction. Fair entry well below current $4.94 — market pricing approval near-certainty that precedent doesn't support.",

  "research_brief": {
    "technology": {
      "origin": "Inhaled recombinant GM-CSF (molgradex) — in-licensed from Serendex Pharmaceuticals 2017.",
      "licensing_source": "Serendex Pharmaceuticals (2017 asset purchase)",
      "uniqueness_score": 0.6,
      "uniqueness_rationale": "Only inhaled GM-CSF in clinical development; addressing aPAP niche where whole-lung lavage is current SOC. Differentiated delivery vs whole-molecule GM-CSF IV (Leukine, discontinued in aPAP)."
    },
    "moat": {
      "score": 0.3,
      "rationale": "Method-of-use IP on inhaled GM-CSF expires 2030-2032. No composition novelty. aPAP orphan designation provides 7y exclusivity post-approval but single-asset nature means any mechanism replicator (e.g. other recombinant GM-CSF) erodes franchise fast."
    },
    "financials": {
      "fully_diluted_shares_count": 265000000,
      "basic_shares_count": 204700000,
      "prefunded_warrants_count": 48000000,
      "cash_and_equivalents_usd": 105000000,
      "quarterly_burn_usd": 18000000,
      "runway_months": 17,
      "shelf_registration_usd_capacity": 200000000,
      "recent_capital_raises": [
        { "date_iso": "2025-03", "instrument": "equity", "gross_proceeds_usd": 75000000 }
      ],
      "prefunded_warrants_detail": [
        { "count": 48000000, "strike_usd": 0.001, "expiry_iso": null }
      ]
    },
    "insider_activity": {
      "last_3y_summary": "Net sellers across CEO + CFO 2023-2025 via 10b5-1. No insider buying since 2021. Board member added small position Jan 2026 (10k shares).",
      "recent_transactions": [
        { "date_iso": "2026-01", "insider_name": "Board Member", "role": "Director", "type": "buy", "shares": 10000, "price_usd": 5.20 }
      ]
    },
    "clinical_trials": {
      "ongoing": [
        { "nct_id": "NCT04983342", "program": "molgradex", "indication": "autoimmune Pulmonary Alveolar Proteinosis (aPAP)", "phase": "Ph3", "status": "Data analysis", "enrollment_target": 164, "enrollment_current": 164, "primary_endpoint": "Change in A-a gradient at week 24" }
      ],
      "interim_readouts_expected": [],
      "final_readouts_expected": [
        { "program": "molgradex", "phase": "Ph3", "expected_date_iso": "2026-Q2", "readout_type": "IMPALA-2 topline + BLA refile", "rationale": "Per mgmt Q4 2025 earnings call — topline Q2 2026, BLA refile H2 2026" }
      ],
      "prior_readouts_history": [
        { "date_iso": "2019-12", "program": "molgradex", "phase": "Ph3", "result_summary": "IMPALA-1 MISSED primary endpoint (A-a gradient change). Secondary endpoints supportive; FDA required confirmatory trial. Basis for IMPALA-2 POS haircut." }
      ]
    },
    "competitive_landscape": [
      { "competitor": "Whole-lung lavage (SOC)", "program": "procedure", "stage": "standard", "differentiator_vs_subject": "Invasive, hospital-based, repeat administration needed; molgradex would offer outpatient alternative" }
    ],
    "partnerships": [],
    "acquisition_target": {
      "score": 0.3,
      "rationale": "Single-asset orphan drug with modest TAM; acquirers typically wait until post-approval. No disclosed activity. Management has not signalled strategic review."
    },
    "fda": {
      "lead_indication": "autoimmune Pulmonary Alveolar Proteinosis",
      "regulatory_hurdles": "FDA required confirmatory trial post-IMPALA-1 (2020 CRL-equivalent). IMPALA-2 endpoint negotiated — A-a gradient with functional co-primary. Inhaled biologic manufacturing pre-approval inspection risk.",
      "base_rate_precedent": "Ph3-failed programs that refiled after adjusted endpoint: approval rate ~50% (vs 75% baseline for rare disease)."
    },
    "rnpv_by_indication": [
      {
        "indication": "autoimmune Pulmonary Alveolar Proteinosis", "program": "molgradex",
        "stage": "Ph3", "pos_base_rate": 0.75, "pos_adjusted": 0.55,
        "pos_rationale": "-20pp vs rare-disease Ph3 base: IMPALA-1 primary endpoint miss 2019 (failure history, cited); refile-after-fail precedent ~50% (cited above). At upper end of refile range given Ph2 molgradex efficacy was positive.",
        "tam_total_usd": 600000000, "peak_sales_addressable_usd": 280000000,
        "years_to_peak": 4, "rnpv_contribution_usd": 85000000
      }
    ],
    "rnpv_total_usd": 85000000,
    "rnpv_per_share_usd": 0.32,
    "rnpv_assumptions": "WACC 12%, no royalty stack (100% SVRA economics), 18% effective tax, fully_diluted_shares_count 265M including 48M PFWs + 12M vested ITM options. Single-asset — no platform contribution.",
    "past_failures": [
      { "date_iso": "2019-12", "program": "molgradex IMPALA-1", "event": "endpoint_miss", "impact": "Cut rNPV-per-share ~60%; forced confirmatory trial; dilutive financing 2020. Residual overhang on IMPALA-2 interpretation." }
    ],
    "research_notes": "SVRA is a binary single-asset PDUFA story. rNPV-per-share $0.32 is sobering — market $4.94 prices in ~15x rNPV, equivalent to ~85% implied approval probability. Our adjusted POS is 55% — gap reflects market over-confidence. Fair entry at Ph3 stage 50-75% of rNPV-per-share = $0.16-$0.24 — unreachable unless a catastrophic pre-PDUFA event (CRL leak, inspection finding). Practical implication: this ticker is NOT a buy at current price despite the upcoming catalyst; score is academic until market provides an entry near rNPV-derived fair range."
  },

  "entry_price_ranges": {
    "fair_entry_low_usd": 0.20,
    "fair_entry_high_usd": 0.30,
    "fair_entry_rationale": "fair_entry midpoint $0.25 ≈ 78% of rNPV-per-share $0.32 (Ph3 with refile history). Cash per share $0.40 ($105M/265M) is a SECONDARY floor — note fair entry is BELOW cash per share because rNPV is the primary anchor and rNPV < cash indicates pre-approval risk still dominates. Historical IMPALA-1 failure overhang justifies aggressive discount.",
    "full_reward_low_usd": 0.15,
    "full_reward_high_usd": 0.20,
    "full_reward_rationale": "Full-reward midpoint $0.175 implies ~50% of cash per share ($0.40). Requires a CRL or inspection finding scenario where SVRA trades as a pure cash-shell bet on re-refile optionality. Forced re-financing at this level given burn rate likely."
  },

  "near_term_3mo": {
    "target_price_usd": 5.50,
    "time_to_catalyst_weeks": 10,
    "probability": 0.45,
    "catalyst_type": "trial_readout",
    "catalyst_detail": "IMPALA-2 topline — primary A-a gradient endpoint readout expected Q2 2026",
    "thesis_summary": "OVERRIDES broken_trend prior: near-term is dominated by IMPALA-2 topline. Positive data → tape snaps +30% from current. Miss → -60%. Expected-value blend at probability 0.45 (LOW-MEDIUM band, reflecting refile-after-fail precedent).",
    "key_risks": [
      "IMPALA-2 misses primary endpoint — cut mcap ~70% to cash-plus",
      "Endpoint split success (primary hit, secondary miss) creates regulatory ambiguity",
      "Pre-PDUFA manufacturing inspection finding leaks"
    ]
  },
  "long_term_12mo": {
    "target_price_usd": 9.00,
    "time_to_catalyst_weeks": 40,
    "probability": 0.50,
    "catalyst_type": "approval",
    "catalyst_detail": "Molgradex PDUFA autoimmune PAP expected H2 2026/Q1 2027 following IMPALA-2 topline + BLA refile",
    "thesis_summary": "OVERRIDES broken_trend at 12mo: molgradex approval re-rates franchise to orphan peer multiples ($800M-$1.2B market). POS 0.50 reflects refile-after-fail precedent — NOT the 75% rare-disease base rate. Target $9 is EV blend of approval ($12, 50%) and CRL ($2, 50%).",
    "key_risks": [
      "CRL on manufacturing or labeling — refile cycles typically 9-18 months, further dilution",
      "Single-asset risk: no pipeline diversification to absorb adverse PDUFA",
      "Financing overhang pre-PDUFA — secondary at discounted mcap plausible"
    ]
  }
}
```

# END OF FEW-SHOT EXAMPLES

Now score the ticker(s) in the following user message.
