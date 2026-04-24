# Module 11 — Portfolio state tracker

> Deviations from this spec are logged in [spec/decisions.md](decisions.md).

## Purpose
Maintains current portfolio state. Enforces all hard constraints
before any BUY recommendation executes. Tracks LTCG holding periods.

## State tracked
- Current positions per ticker: entry_date, entry_price,
  actual_position_$, sleeve_assignment, holding_days
- Sleeve allocations: S1 current %, S2 current %, S3 current %
- Cash reserve: current balance
- Macro hedge: current balance (static $3,750)
- Sector exposure per GICS sector
- Catalyst type concentration: % of total CCS exposure per type

## Hard constraints enforced on every BUY
[copy the hard_constraints block from Portfolio Specifications]
[copy the position_sizing_override_rule]

## Sleeve ceilings
- S1: max 43% of portfolio
- S2: max 31% of portfolio
- S3: max 16% of portfolio
BUY blocked if adding position would breach ceiling.

## Gates
- Cash reserve gate: BUY blocked if cash_reserve < $3,000
- Max single sector gate: BUY blocked if adding would take
  any GICS sector above 25%
- Max catalyst type gate: BUY blocked if adding would take
  any catalyst type above 40% of total CCS exposure

## LTCG tracking
- entry_date recorded on every position open
- days_held computed on every read: today - entry_date
- days_to_12month_ltcg = 365 - days_held (if days_held < 365)
- Flag raised when days_to_12month_ltcg <= 60

## Implementation inputs and outputs
- inputs: manual execution confirmations from portfolio owner,
          Layer 4 recommendations (for constraint checking)
- outputs: current portfolio state, constraint check results,
           LTCG tracking data for Layer 4 execution_flags
- key constraint: sleeve ceiling and cash reserve gate
                  enforced before recommendation reaches owner