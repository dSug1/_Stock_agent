# Module 7 — Few-shot examples (cacheable, appended to system prompt)
#
# Two worked examples showing the m7-v1 output shape. Both are
# synthetic (drug names + numbers are illustrative, NOT advice). They
# teach the model:
#   • How to anchor pos_adjusted on pos_base_rate then adjust
#   • Asymmetric move math anchored on rNPV/share + cash floor
#   • Per-indication rNPV split for multi-asset companies
#   • How to phrase competition_landscape + bar_set_by_others
#   • Catalyst-date sanity check pattern
#   • HARD RULE compliance (probability bounds, sign discipline,
#     no prose outside the JSON fence)
#
# The user message immediately after this file is the REAL ticker.
# =============================================================================

# EXAMPLE 1 — Mid-conviction Phase 2 topline (PAH follower)

User message would carry a pack like:

```json
{
  "snapshot_date": "2026-05-28",
  "identity": {"ticker": "EX01", "company_name": "Example PAH Therapeutics",
               "drug": "EX1-145", "drug_raw": "EX1-145 FTD",
               "indication": "PAH", "stage": "phase2",
               "nct_number": "NCT99000001", "status": "ongoing"},
  "catalyst": {"next_catalyst_type": "Topline Data",
               "catalyst_text": "Topline Phase 2 readout in Q3 2026",
               "date_min": "2026-07-01", "date_max": "2026-09-30",
               "weeks_to_catalyst_min": 5, "weeks_to_catalyst_max": 18,
               "precision_tier": "quarter", "fda_designations": ["FTD"],
               "historical_loa": 0.32, "historical_pop": 0.42},
  "market_snapshot": {"last_price_usd": 24.13, "basic_shares_count": 38500000,
                       "prefunded_warrants_count": 4200000,
                       "fully_diluted_shares_count": 42700000,
                       "market_cap_fdsc_usd": 1030351000,
                       "pfw_share_dilution_warning": false},
  "fundamentals": {"cash_total_usd": 178000000, "quarterly_burn_usd": 22000000,
                    "runway_months": 24.3, "operating_cf_ttm_usd": -88000000}
}
```

Your response would be:

```json
{
  "ticker": "EX01",
  "reasoning_trace": "EX1-145 is a follower in the activin receptor class behind sotatercept. STELLAR established the receptor mechanism in PAH with strong 6MWD efficacy; PULSAR is ongoing. EX1-145 differentiates on receptor-A selectivity (vs sotatercept's dual A/B binding) and monthly dosing, plausibly reducing the bone-density / hematology signal that limited sotatercept's label. Phase 1 was clean on safety with PD biomarker hits (PAP, PVR). Phase 2 is 80 patients, 12-week 6MWD primary. Industry POS for Phase 2 → Phase 3 in PAH is ~0.35; sotatercept derisk + clean Phase 1 PD signal nudges this to 0.42. Hit reprices toward 0.5 of rNPV/share since one of two indications derisks; miss to cash floor + 15% as $178M cash supports ~24-month runway. No IR-reaffirmation issues — Q3 2026 confirmed in Q1 earnings.",
  "drug_profile": {
    "moa": "Selective activin receptor type IIA ligand trap",
    "moa_class_precedent": "Acceleron sotatercept — acquired MSD $11.5B 2021; STELLAR/PAH primary endpoint hit 2023",
    "differentiation": "Receptor-A selectivity vs sotatercept's A/B dual binding; monthly SC vs Q3W",
    "competition_landscape": "follower",
    "competition_bar_set_by_others": "STELLAR 6MWD +40m p<0.001; non-inferiority alone insufficient for premium share",
    "patent_moat": {
      "composition_patent_expiry": "2038-10",
      "method_patent_expiry": "2040-06",
      "summary": "Composition coverage to 2038; method-of-use for PAH-specific dosing to 2040; LCM filings active."
    },
    "fda_designations": ["FTD"],
    "regulatory_pathway": "standard",
    "tam_usd": 4200000000,
    "tam_rationale": "Global PAH ~70k patients addressable × $60k WAC × ~50% peak share assuming differentiated tolerability"
  },
  "clinical_evidence": {
    "preclinical_summary": "Murine PH model showed PVR reduction 35-45% vs vehicle; non-human primate tox clean to 10× clinical dose.",
    "phase1_results": "32 healthy volunteers; clean safety; dose-dependent reduction in serum activin A confirming target engagement; half-life 18 days.",
    "phase2_interim": "Pre-specified 24-week interim 2025-11-15: PVR -180 dyn-s/cm5 (vs +20 placebo); 6MWD trend +28m at 12w.",
    "phase2_final": null,
    "prior_class_successes": ["sotatercept-PAH-2023 (STELLAR primary endpoint hit; +40m 6MWD)"],
    "prior_class_failures": ["BMS-986278-IPF-2023 (failed ASPEN-IPF — different indication, same MoA class)"]
  },
  "rnpv_by_indication": [
    {"indication": "PAH",
     "pos_base_rate": 0.35, "pos_adjusted": 0.42,
     "rnpv_contribution_usd": 1200000000, "peak_sales_year": 2032,
     "rationale": "STELLAR derisks receptor mechanism; selectivity advantage credible but unproven head-to-head."},
    {"indication": "HFpEF (platform optionality)",
     "pos_base_rate": 0.15, "pos_adjusted": 0.18,
     "rnpv_contribution_usd": 800000000, "peak_sales_year": 2034,
     "rationale": "Early-signal-only; HFpEF MoA crowded by SGLT2 + finerenone; differentiation argument speculative."}
  ],
  "rnpv_total_usd": 2000000000,
  "rnpv_per_share_usd": 46.84,
  "lead_indication": "PAH",
  "catalyst_outcome": {
    "p_clinical": 0.42,
    "p_clinical_low": 0.30,
    "p_clinical_high": 0.55,
    "expected_move_on_hit_pct": 80.0,
    "expected_move_on_miss_pct": -65.0,
    "move_anchor_rationale": "Hit derisks PAH (60% of total rNPV), reprices toward 0.4 × $46.84/share = ~$18-21 lift, +80% vs $24 current. Miss to cash-per-share floor $178M/42.7M = $4.17 + ~15% retention ≈ $5; -65% vs $24."
  },
  "financial_overhang": {
    "cash_runway_quarters": 8,
    "dilution_risk": "low",
    "near_term_raise_likely": false,
    "rationale": "$178M cash + ~$22M quarterly burn = 8Q runway; covers catalyst + 4-5 quarters of post-readout planning. No PFW overhang flag."
  },
  "management_track_record": {
    "score": 0.65,
    "summary": "CEO led prior cardiopulmonary biotech to Phase 3 + BMS partnership 2018; CMO ex-Novartis cardio (entresto)."
  },
  "acquisition_target": {
    "score": 0.55,
    "rationale": "PAH franchise consolidation post-MRK/sotatercept makes a differentiated activin trap a logical bolt-on for MSD, JNJ, or BMY at $2-3B."
  },
  "thesis_summary": "Mid-conviction Phase 2 PAH catalyst with differentiated activin ligand trap. Sotatercept derisks the receptor mechanism; selectivity + dosing advantage credible but unproven head-to-head. Cash runway covers catalyst + post-readout planning. Asymmetric expectancy weighted toward hit given the derisk, partially offset by mid-tier base rate and STELLAR bar.",
  "key_risks": [
    "STELLAR set a high efficacy bar; non-inferiority alone is not enough for premium label.",
    "Phase 2 powered for 6MWD primary; clinical-events endpoint absent so durability proof punted to Phase 3.",
    "HFpEF arm is platform-only optionality — does not drive this readout.",
    "Dose-finding window narrow; MTD/AE signal could reset timeline 6+ months.",
    "MoA-class miss possible if BMS-986278 IPF failure extrapolates (unlikely but cited)."
  ],
  "catalyst_date_sanity_check": {
    "ir_page_consistent": true,
    "catalyst_passed_already": false,
    "notes": "Q3 2026 timing reaffirmed in 2026-05-09 Q1 earnings call and 2026-05-15 ASCO investor session."
  }
}
```

# EXAMPLE 2 — Low-conviction Phase 1 first-in-class miss-skew

User message would carry a pack like:

```json
{
  "snapshot_date": "2026-05-28",
  "identity": {"ticker": "EX02", "company_name": "Example NeuroBio",
               "drug": "EX2-220", "drug_raw": "EX2-220",
               "indication": "Friedreich's ataxia", "stage": "phase1",
               "nct_number": "NCT99000002", "status": "ongoing"},
  "catalyst": {"next_catalyst_type": "Interim Data",
               "catalyst_text": "Phase 1b interim biomarker readout, H2 2026",
               "date_min": "2026-09-01", "date_max": "2026-12-15",
               "weeks_to_catalyst_min": 14, "weeks_to_catalyst_max": 28,
               "precision_tier": "half", "fda_designations": ["ODD", "FTD"],
               "historical_loa": 0.18, "historical_pop": 0.10},
  "market_snapshot": {"last_price_usd": 3.84, "basic_shares_count": 22100000,
                       "prefunded_warrants_count": 6800000,
                       "fully_diluted_shares_count": 28900000,
                       "market_cap_fdsc_usd": 110976000,
                       "pfw_share_dilution_warning": true},
  "fundamentals": {"cash_total_usd": 38000000, "quarterly_burn_usd": 9000000,
                    "runway_months": 12.7, "operating_cf_ttm_usd": -36000000}
}
```

Your response would be:

```json
{
  "ticker": "EX02",
  "reasoning_trace": "EX2-220 is a first-in-class frataxin protein replacement therapy for Friedreich's ataxia. No prior class to derisk; Reata's omaveloxolone is approved for FA but works on a different MoA (Nrf2 activator). Phase 1a was 12 healthy volunteers showing target tissue delivery on PET imaging. The Phase 1b interim readout is biomarker-only (frataxin levels in lymphocytes) — not efficacy. Industry POS for Phase 1 first-in-class neurodegenerative is ~0.10-0.15; we're at 0.20 because the biomarker has reasonable mechanistic support but no patient-level efficacy. PFW dilution warning is on (6.8M PFWs ≈ 31% of basic) which caps reasonable hit move. Hit move modest because biomarker-only interim doesn't fully derisk efficacy; miss move severe because $3.84 share is already at ~3-4× cash floor and a clean miss would trigger PFW exercise + financing. Catalyst H2 2026 was reaffirmed in Q1 2026 earnings and confirmed in a 2026-04-30 8-K.",
  "drug_profile": {
    "moa": "Frataxin protein replacement therapy (gene therapy AAV9 vector)",
    "moa_class_precedent": "None for protein replacement in FA; omaveloxolone (different MoA) approved 2023",
    "differentiation": "First-in-class direct frataxin replacement; addresses root cause vs symptomatic SOD/anti-oxidant approaches",
    "competition_landscape": "first_to_market",
    "competition_bar_set_by_others": null,
    "patent_moat": {
      "composition_patent_expiry": "2041-02",
      "method_patent_expiry": "2042-09",
      "summary": "Composition + AAV9 capsid engineering coverage to 2041; ODD provides additional 7-year market exclusivity post-approval."
    },
    "fda_designations": ["ODD", "FTD"],
    "regulatory_pathway": "accelerated_approval",
    "tam_usd": 850000000,
    "tam_rationale": "FA prevalence ~5k US / 15k EU × $400k annual gene therapy WAC × peak share 30-40% (single-administration competitive dynamic)"
  },
  "clinical_evidence": {
    "preclinical_summary": "AAV9 vector achieved frataxin restoration to 60-80% of WT in YG8R mouse model; sensory-motor improvement at 12 weeks; primate tox clean to 12-month time point.",
    "phase1_results": "12 healthy volunteers (Phase 1a); PET imaging confirmed CNS delivery; no DLTs; circulating frataxin restored to ~50% of normal at MTD.",
    "phase2_interim": null,
    "phase2_final": null,
    "prior_class_successes": [],
    "prior_class_failures": ["Larimar tabriba — different MoA (synthetic peptide) — Phase 2 mixed in 2024; not directly applicable but cools the FA space."]
  },
  "rnpv_by_indication": [
    {"indication": "Friedreich's ataxia",
     "pos_base_rate": 0.10, "pos_adjusted": 0.20,
     "rnpv_contribution_usd": 240000000, "peak_sales_year": 2035,
     "rationale": "First-in-class binary; biomarker support credible but Phase 1 too early to estimate clinical effect. Adjustment bounded by uncertainty."}
  ],
  "rnpv_total_usd": 240000000,
  "rnpv_per_share_usd": 8.30,
  "lead_indication": "Friedreich's ataxia",
  "catalyst_outcome": {
    "p_clinical": 0.25,
    "p_clinical_low": 0.15,
    "p_clinical_high": 0.40,
    "expected_move_on_hit_pct": 90.0,
    "expected_move_on_miss_pct": -55.0,
    "move_anchor_rationale": "Single-asset company; no platform contribution modelled (per HARD RULE #12 — single FA gene therapy, no disclosed pipeline beyond AAV9 platform background). Hit (biomarker shows dose-proportional frataxin restoration in patients) derisks the platform partially → reprice to ~0.25 × rNPV/share $8.30 = $2.08, but Phase 1 biomarker stage discount band is 15-30% — using 25% midpoint anchors hit to ~$7 (still below rNPV/share). +90% vs $3.84 current. Miss to cash floor $38M/28.9M = $1.31 × 1.3 retention = $1.70, ~-55% vs $3.84. PFW overhang flagged."
  },
  "financial_overhang": {
    "cash_runway_quarters": 4,
    "dilution_risk": "high",
    "near_term_raise_likely": true,
    "rationale": "12.7-month runway covers catalyst + ~1 quarter of follow-up only. PFW overhang already at 31% of basic; historical pattern is post-readout dilution either way."
  },
  "management_track_record": {
    "score": 0.40,
    "summary": "First-time CEO ex-academia (PI on AAV9 IND); CMO ex-Sarepta DMD program (mixed outcomes)."
  },
  "acquisition_target": {
    "score": 0.35,
    "rationale": "Possible bolt-on for Sarepta, Pfizer, or Roche post-readout if biomarker is clean; pre-readout M&A unlikely given first-in-class binary risk."
  },
  "thesis_summary": "Low-conviction biomarker readout for first-in-class FA gene therapy. Mechanism is mechanistically appealing but unprecedented; biomarker-only interim limits derisk magnitude even on hit. PFW overhang + 12-month runway constrain upside. Expectancy negative-skewed: hit derisks platform partially but doesn't address efficacy; miss is catastrophic with dilution forced.",
  "key_risks": [
    "First-in-class binary — no prior class to anchor.",
    "Biomarker-only interim cannot derisk patient-level efficacy.",
    "PFW overhang 31% of basic — any positive signal triggers exercise dilution.",
    "12-month runway forces a raise even on a clean hit.",
    "Larimar/CTI cooling effect on FA space — sentiment-driven discount risk on neutral data."
  ],
  "catalyst_date_sanity_check": {
    "ir_page_consistent": true,
    "catalyst_passed_already": false,
    "notes": "H2 2026 reaffirmed in 2026-04-30 8-K and 2026-05-12 Q1 earnings call."
  }
}
```

The next user message contains the real ticker. Apply the same
reasoning framework. Return ONE ```json``` block. No prose outside.
