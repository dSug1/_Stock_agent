# Module 12 — Action tracking calendar

> Deviations from this spec are logged in [spec/decisions.md](decisions.md).

This module is the portfolio owner's persistent memory. It automatically creates, schedules, reminds, and tracks every action the system requires. Nothing falls through the cracks because the portfolio owner does not need to remember dates manually.

12.1 Action Record Schema
ACTION_RECORD {
  action_id:  uuid,
  action_type: enum,  // see 12.2
  category:   "position_level|system_recurring|alert_triggered",
  priority:   "urgent|high|medium|low",
  status:     "pending|completed|overdue|cancelled",

  scheduling: {
    due_date:       ISO8601,
    reminder_dates: [ISO8601],
    // auto-computed from due_date and reminder_lead_times
    // see 12.4 for lead times by priority
    completed_date: ISO8601 or null,
    overdue_since:  ISO8601 or null
    // auto-set when due_date passes without completion
  },

  context: {
    ticker:                   string or null,
    position_id:              uuid or null,
    catalyst_id:              uuid or null,
    source_recommendation_id: uuid or null,
    source_layer: "Layer_4|Layer_0|Layer_6|Module_12|Manual"
  },

  content: {
    title:           string,
    description:     string,
    // what needs to be reviewed or decided
    required_action: string,
    // specific instruction: what to do, what to check,
    // what decision to make
    relevant_data:   [string],
    // list of data fields or registry sections
    // the user should look at when performing this action
    outcome_options: [string]
    // possible outcomes the user should record on completion
  },

  recurrence: {
    is_recurring:    boolean,
    frequency:       "daily|weekly|monthly|quarterly|
                      semiannual|annual|on_trigger",
    next_occurrence: ISO8601 or null,
    auto_create:     boolean
    // if true: on completion, next occurrence is auto-created
  },

  completion: {
    completed_by:         "user",
    outcome_chosen:       string,
    notes:                string,
    // free text for user to record what they observed and decided
    // feeds postmortem analysis
    follow_up_action_ids: [uuid]
    // new actions created as result of this one
  }
}

12.2 Action Types
ACTION_TYPES {

  // Position-level — created per position on BUY recommendation

  THESIS_REVIEW: {
    description:
      "Formal reassessment of the investment thesis.
       Review current CCS, probability history, any new
       documents since entry, and institutional accumulation.
       Decide: confirm, reduce, or exit.",
    trigger:   "Layer 4 BUY recommendation",
    due_date:  "thesis_review_date from RECOMMENDATION_OUTPUT",
    reminder_lead: "14 days, 7 days, 2 days",
    priority:  "high",
    recurring: false,
    outcome_options: [
      "Thesis confirmed — hold current position",
      "Thesis strengthened — consider increasing if sleeve allows",
      "Thesis weakened — reduce position by one tier",
      "Thesis invalidated — exit within 2 sessions"
    ]
  },

  CATALYST_DATE_MONITOR: {
    description:
      "Binary catalyst resolution expected on or around this date.
       Monitor for press release, SEC filing, or conference
       announcement. Be prepared to execute sell within 2 sessions
       of resolution regardless of direction.",
    trigger:   "Layer 4 BUY with datable catalyst",
    due_date:  "catalyst_resolution_date from RECOMMENDATION_OUTPUT",
    reminder_lead: "7 days, 3 days, 1 day, day-of",
    priority:  "urgent",
    recurring: false,
    weekend_note:
      "if catalyst_date_weekend_flag true:
       create additional reminder for Friday before
       noting that gap risk exists over weekend"
  },

  LTCG_THRESHOLD_MONITOR: {
    description:
      "This position approaches the 12-month holding period
       for long-term capital gains tax treatment.
       Review whether tax efficiency justifies holding
       vs exiting before this threshold if the thesis
       is weakening.",
    trigger:   "Layer 4 BUY recommendation",
    due_date:  "T_entry + 305 days (60-day warning before 12-month mark)",
    reminder_lead: "30 days, 14 days, 7 days",
    priority:  "medium",
    recurring: false,
    outcome_options: [
      "Hold — thesis valid, tax efficiency warrants continuing",
      "Exit — thesis weakened, tax saving not sufficient",
      "Reduce — partial exit, hold remainder to LTCG threshold"
    ]
  },

  WEEKEND_CATALYST_REVIEW: {
    description:
      "Catalyst resolution date falls on a weekend or holiday.
       Review position size and ensure you are comfortable
       with gap risk at Monday open. Consider reducing before
       Friday close.",
    trigger:   "catalyst_date_weekend_flag = true",
    due_date:  "Friday before catalyst_resolution_date",
    reminder_lead: "3 days, 1 day",
    priority:  "high",
    recurring: false,
    outcome_options: [
      "Hold full position — accept gap risk",
      "Reduce to half position — limit gap exposure",
      "Exit — unwilling to hold through binary weekend event"
    ]
  },

  SELL_TRIGGER_REVIEW: {
    description:
      "A sell trigger has fired for this position. Review the
       specific trigger condition, current CCS, and thesis
       status. Execute sell within 2 sessions for near-term
       catalyst positions.",
    trigger:   "any sell trigger fires in Layer 4",
    due_date:  "same day as trigger",
    reminder_lead: "immediate — SMS",
    priority:  "urgent",
    recurring: false
  },

  MONTHLY_POSITION_RESCORE: {
    description:
      "Monthly CCS rescore for this medium-term or structural
       position. Review Layer 3 registry entry, check for
       new contradictions, dependency status, and institutional
       accumulation changes. Confirm no sell triggers approaching.",
    trigger:   "BUY recommendation for S2 or S3 sleeve",
    due_date:  "T_entry + 30 days, then every 30 days",
    reminder_lead: "3 days",
    priority:  "medium",
    recurring: true,
    frequency: "monthly",
    auto_create: true
  },

  // System-level recurring — created at system initialisation

  WEEKLY_UNIVERSE_REVIEW: {
    description:
      "Review CCS rescore outputs from Layer 3 for all
       active monitoring positions. Check alert digest.
       Review any new watchlist entries above CCS 55.
       Review sleeve allocation drift.",
    trigger:   "system initialisation",
    due_date:  "every Sunday evening",
    reminder_lead: "none — auto-generated weekly",
    priority:  "medium",
    recurring: true,
    frequency: "weekly",
    auto_create: true
  },

  MONTHLY_FULL_RESCORE: {
    description:
      "Full quantitative rescore across all four dimensions
       (A: valuation, B: fundamentals, C: catalyst, D: macro)
       for all held positions. Review any positions where
       quantitative score has shifted materially. Check
       cash reserve level.",
    trigger:   "system initialisation",
    due_date:  "first Monday of each month",
    reminder_lead: "3 days",
    priority:  "medium",
    recurring: true,
    frequency: "monthly",
    auto_create: true
  },

  MONTHLY_KEYWORD_AUDIT: {
    description:
      "Review Layer 1 keyword audit output. Check false negative
       rate by keyword tier. Add any newly identified terminology
       to the appropriate keyword tier. Confirm pass condition
       thresholds remain appropriate.",
    trigger:   "system initialisation",
    due_date:  "first Monday of each month",
    reminder_lead: "none — same day as MONTHLY_FULL_RESCORE",
    priority:  "low",
    recurring: true,
    frequency: "monthly",
    auto_create: true
  },

  QUARTERLY_13F_REFRESH: {
    description:
      "13F filing window has opened. Process new 13F filings
       for all tracked institutions. Update TWOS scores.
       Reassign processing tiers. Check for new position
       initiations by Tier 1A/1B institutions. Identify
       any institution exits from held positions.",
    trigger:   "system initialisation",
    due_dates: "February 15, May 15, August 15, November 15",
    reminder_lead: "7 days, 3 days",
    priority:  "high",
    recurring: true,
    frequency: "quarterly",
    auto_create: true
  },

  QUARTERLY_CALIBRATION_RUN: {
    description:
      "Run Layer 6 parameter calibration engine on all
       resolved positions since last calibration.
       Review Brier scores by catalyst type. Review
       Sharpe ratio by CCS band and sleeve. Check
       exit timing delta. Apply any parameter updates
       that meet minimum observation thresholds.
       Flag any changes > 30% for manual review.",
    trigger:      "system initialisation",
    due_date:     "15 days after each QUARTERLY_13F_REFRESH",
    reminder_lead: "3 days",
    priority:     "high",
    recurring:    true,
    frequency:    "quarterly",
    auto_create:  true,
    prerequisite: "QUARTERLY_13F_REFRESH completed"
  },

  SEMIANNUAL_LAYER0_RECALIBRATION: {
    description:
      "Review Layer 0 macro regime boundary parameters.
       Check whether VIX thresholds, credit spread thresholds,
       and yield curve cutoffs remain appropriate. Review
       regime modulation magnitudes against empirical return
       data by regime. Requires minimum 2 complete regime
       transitions.",
    trigger:      "system initialisation",
    due_dates:    "April 1, October 1",
    reminder_lead: "14 days, 7 days",
    priority:     "medium",
    recurring:    true,
    frequency:    "semiannual",
    auto_create:  true
  },

  ANNUAL_INSTITUTION_RECALIBRATION: {
    description:
      "Annual review of tracked institution tier assignments.
       Compute hit rate and average alpha for each institution
       over prior 12 months. Apply tier promotion or demotion
       rules. Identify any new funds for potential addition.
       Remove institutions showing consistent Tier 4 behaviour.
       Requires minimum 30 observations.",
    trigger:      "system initialisation",
    due_date:     "January 15 each year",
    reminder_lead: "30 days, 14 days",
    priority:     "high",
    recurring:    true,
    frequency:    "annual",
    auto_create:  true
  },

  ANNUAL_BRIER_REVIEW: {
    description:
      "Full annual review of system calibration performance.
       Compare Brier scores across all catalyst types for
       the full year. Compare live performance against
       historical simulation baseline. Check for LLM
       contamination signal (live Brier > historical × 1.25).
       Review options divergence threshold calibration.
       Review exit timing delta trend.",
    trigger:      "system initialisation",
    due_date:     "January 15 each year",
    reminder_lead: "14 days",
    priority:     "high",
    recurring:    true,
    frequency:    "annual",
    auto_create:  true
  },

  // Alert-triggered — created dynamically

  REGIME_CHANGE_REVIEW: {
    description:
      "Macro regime has changed. Review all open positions
       for position size adjustment under new regime multipliers.
       Check whether any positions in pre-revenue names now
       breach the tightened survival threshold. Review
       sleeve allocation under new regime context.",
    trigger:      "Layer 0 regime change event",
    due_date:     "same day",
    reminder_lead: "SMS_immediate",
    priority:     "urgent",
    recurring:    false
  },

  CONTRADICTION_RESOLUTION: {
    description:
      "A new signal contradicts an existing probability estimate
       for this position. Review both the original signal and
       the contradicting signal. Determine which is more
       credible. Update the registry manually if required.
       Do not change position size until contradiction resolved.",
    trigger:      "CONTRADICTION_FLAGGED alert",
    due_date:     "within 2 business days",
    reminder_lead: "1 day",
    priority:     "high",
    recurring:    false
  },

  OPTIONS_DIVERGENCE_REVIEW: {
    description:
      "Options market pricing diverges from registry expected
       value by more than 15%. Review whether the registry
       probability estimate needs revision or whether the
       options market is mispricing. Document rationale
       before any position size change.",
    trigger:      "OPTIONS_DIVERGENCE alert",
    due_date:     "within 2 business days",
    reminder_lead: "1 day",
    priority:     "high",
    recurring:    false
  },

  SURVIVAL_WARNING_REVIEW: {
    description:
      "Cash runway for this pre-revenue position has fallen
       below 3 quarters. Assess whether survival probability
       to catalyst date has dropped below 0.50. If so,
       sell trigger may be imminent. Check for any new
       equity raise announcements or ATM activity.",
    trigger:      "SURVIVAL_WARNING alert",
    due_date:     "same day",
    reminder_lead: "SMS_immediate",
    priority:     "urgent",
    recurring:    false
  },

  CONTAMINATION_CHECK: {
    description:
      "Three months since LIVE mode launch. Compare live
       Brier scores against historical simulation baseline.
       If live Brier > historical × 1.25, LLM contamination
       is confirmed — tighten all adjustment bounds by 50%
       in Layer 2 PROBABILITY_PRIORS.",
    trigger:      "3 months after LIVE mode launch",
    due_date:     "T_live_launch + 90 days",
    reminder_lead: "14 days, 7 days",
    priority:     "high",
    recurring:    false
  },

  PROMPT_REVIEW: {
    description:
      "Layer 2 extraction quality has degraded below threshold.
       Review calibration data for the specific failure mode
       (magnitude misassignment or time_horizon error).
       Draft revised prompt. Test on 500-document historical
       sample before deploying.",
    trigger:      "PROMPT_CHANGE_CONTROL trigger condition detected",
    due_date:     "within 14 days of trigger",
    reminder_lead: "7 days, 3 days",
    priority:     "medium",
    recurring:    false
  },

  INSIDER_PURCHASE_MONITOR: {
    description:
      "A Form 4 open market purchase (transaction code P) has been
       detected for a ticker in the monitoring universe. Review the
       filing to confirm: who purchased (role, name), dollar amount,
       whether this is a C-suite or director purchase, and whether
       the ticker is already in the watchlist or held portfolio.
       If Tier 1 or Tier 2 signal (C-suite >$500K or director >$100K):
       review current CCS for this ticker and assess whether the
       insider signal warrants escalating the ticker to active
       monitoring or adjusting position size on a held position.",
    trigger:   "Form 4 transaction code P detected in Layer -1.3",
    due_date:  "within 1 business day of filing date",
    reminder_lead: "SMS_immediate on detection",
    priority:  "high",
    recurring: false,
    outcome_options: [
      "Reviewed — no action required, signal noted in registry",
      "Escalated to active monitoring — ticker promoted",
      "Position size increased — insider signal supports held position",
      "Flagged for thesis_review — contradicts current bear view"
    ]
  },

  ANNUAL_OPTIONS_DIVERGENCE_REVIEW: {
    description:
      "Annual calibration of the options divergence threshold
       (currently 15%). Review Layer 5 outcome records for all
       positions where divergence_flag was triggered during the year.
       For each: was the registry EV or the options-implied move
       more accurate (closer to actual price_impact_pct at
       T_resolution)? Compute registry_accuracy_rate vs
       options_accuracy_rate across all flagged positions.
       Decision rules:
       - If options more accurate > 60% of the time:
         lower threshold from 15% to 10%
       - If registry more accurate > 60% of the time:
         raise threshold from 15% to 20%
       - If fewer than 20 divergence_flag events in the year:
         retain current 15% threshold and document reason.
       Update the divergence_flag threshold in the parameters
       table after review. This review is distinct from
       ANNUAL_BRIER_REVIEW which covers broader calibration.",
    trigger:   "system initialisation",
    due_date:  "January 1 each year",
    reminder_lead: "14 days, 7 days",
    priority:  "medium",
    recurring: true,
    frequency: "annual",
    auto_create: true,
    outcome_options: [
      "Threshold lowered to 10% — options more accurate > 60%",
      "Threshold raised to 20% — registry more accurate > 60%",
      "Threshold retained at 15% — insufficient events (<20)",
      "Threshold retained at 15% — accuracy split, no clear winner"
    ]
  }
}

12.3 Auto-Creation Rules
Actions are created automatically by the following events. No manual entry required.
AUTO_CREATION_RULES {

  on_BUY_recommendation: {
    always_create: [
      "THESIS_REVIEW — due: thesis_review_date",
      "LTCG_THRESHOLD_MONITOR — due: T_entry + 305 days"
    ],
    if_datable_catalyst: [
      "CATALYST_DATE_MONITOR — due: catalyst_resolution_date"
    ],
    if_weekend_catalyst_flag: [
      "WEEKEND_CATALYST_REVIEW — due: Friday before catalyst_date"
    ],
    if_sleeve_S2_or_S3: [
      "MONTHLY_POSITION_RESCORE — due: T_entry + 30d, recurring"
    ]
  },

  on_SELL_recommendation: {
    create: ["SELL_TRIGGER_REVIEW — due: same day, urgent"],
    cancel: [
      "all pending position-level actions for this ticker:
       THESIS_REVIEW, CATALYST_DATE_MONITOR,
       LTCG_THRESHOLD_MONITOR, MONTHLY_POSITION_RESCORE,
       WEEKEND_CATALYST_REVIEW"
    ]
  },

  on_regime_change: {
    create: ["REGIME_CHANGE_REVIEW — due: same day, urgent"]
  },

  on_CONTRADICTION_FLAGGED: {
    create: ["CONTRADICTION_RESOLUTION — due: +2 business days"]
  },

  on_OPTIONS_DIVERGENCE: {
    create: ["OPTIONS_DIVERGENCE_REVIEW — due: +2 business days"]
  },

  on_SURVIVAL_WARNING: {
    create: ["SURVIVAL_WARNING_REVIEW — due: same day, urgent"]
  },

  on_INSIDER_PURCHASE_ALERT: {
    create: [
      "INSIDER_PURCHASE_MONITOR — due: within 1 business day,
       priority: high, SMS_immediate on detection"
    ]
  },
  
  on_system_initialisation: {
    create: [
      "WEEKLY_UNIVERSE_REVIEW — recurring every Sunday",
      "MONTHLY_FULL_RESCORE — recurring first Monday each month",
      "MONTHLY_KEYWORD_AUDIT — recurring first Monday each month",
      "QUARTERLY_13F_REFRESH — due Feb15/May15/Aug15/Nov15",
      "QUARTERLY_CALIBRATION_RUN — due 15d after each 13F refresh",
      "SEMIANNUAL_LAYER0_RECALIBRATION — due Apr1/Oct1",
      "ANNUAL_INSTITUTION_RECALIBRATION — due Jan15",
      "ANNUAL_BRIER_REVIEW — due Jan15",
      "ANNUAL_OPTIONS_DIVERGENCE_REVIEW — due Jan1"      
    ]
  },

  on_live_mode_launch: {
    create: ["CONTAMINATION_CHECK — due: T_launch + 90 days"]
  },

  on_prompt_change_trigger: {
    create: ["PROMPT_REVIEW — due: within 14 days"]
  }
}

12.4 Reminder Lead Times by Priority
REMINDER_LEAD_TIMES {
  urgent: ["same day — SMS_immediate"],
  high:   ["14 days — email", "7 days — email", "2 days — SMS"],
  medium: ["7 days — email", "3 days — email"],
  low:    ["3 days — email"]
}

12.5 Dashboard Views
DASHBOARD_VIEWS {

  view_1_TODAY: {
    title: "Actions Due Today",
    content: [
      "all actions where due_date == today, ordered by priority",
      "all overdue actions (due_date < today, status = pending),
       ordered by overdue_since ascending",
      "all SMS_immediate alerts triggered since last login"
    ],
    display:
      "action_type, ticker (if applicable), priority badge,
       required_action text, outcome_options buttons"
  },

  view_2_THIS_WEEK: {
    title: "Next 7 Days",
    content:
      "all pending actions where due_date <= today + 7 days
       grouped by due_date, then by priority within each date"
  },

  view_3_UPCOMING_CATALYSTS: {
    title: "Catalyst Calendar",
    content:
      "all CATALYST_DATE_MONITOR actions, ordered by due_date
       shows: ticker, catalyst_resolution_date,
              350-token catalyst summary from Layer 2,
              current CCS, bull/base/bear probabilities,
              recommended_position_$, weekend_flag indicator"
  },

  view_4_POSITION_TIMELINE: {
    title: "Position Action Timeline",
    content:
      "per held position: all associated pending actions
       on a timeline showing thesis_review_date,
       catalyst_date (if applicable), LTCG_threshold_date,
       monthly rescore dates
       colour-coded: urgent=red, high=orange, medium=yellow"
  },

  view_5_SYSTEM_CALENDAR: {
    title: "System Maintenance Calendar",
    content:
      "all recurring system-level actions for next 12 months
       grouped by month:
       QUARTERLY_13F_REFRESH dates,
       QUARTERLY_CALIBRATION_RUN dates,
       SEMIANNUAL_LAYER0_RECALIBRATION dates,
       ANNUAL_INSTITUTION_RECALIBRATION date,
       ANNUAL_BRIER_REVIEW date"
  },

  view_6_COMPLETED_LOG: {
    title: "Completed Actions",
    content:
      "all completed actions in reverse chronological order
       shows: action_type, ticker, completed_date,
              outcome_chosen, notes
       filterable by: action_type, ticker, date range
       used for: postmortem analysis, calibration review"
  }
}

12.6 Action Status Management
STATUS_RULES {

  pending_to_completed:
    "user selects outcome_option and optionally enters notes
     system records completed_date
     system creates follow_up_actions if applicable
     if recurring: system creates next occurrence automatically"

  pending_to_overdue:
    "automatic when due_date passes without completion
     overdue_since = due_date
     priority escalated one level (medium→high, high→urgent)
     additional reminder generated"

  pending_to_cancelled:
    "automatic when SELL recommendation fires for the ticker
     (position-level actions only)
     system actions cannot be cancelled — only completed"

  overdue_escalation:
    "if action overdue > 3 business days: escalate to urgent
     if action overdue > 7 business days: flag in daily digest
     as CRITICAL_OVERDUE"
}

12.7 Export Formats
EXPORT_FORMATS {

  ical:
    "iCalendar format (.ics) for import into Google Calendar,
     Apple Calendar, Outlook
     includes: title, description, due_date as event date,
               reminder_dates as calendar alerts
     exported per view or full calendar"

  csv:
    "all pending actions as CSV:
     action_id, action_type, ticker, due_date, priority,
     title, required_action
     suitable for Excel or other tracking tools"

  json:
    "full ACTION_RECORD schema for each pending action
     suitable for external integrations"
}

