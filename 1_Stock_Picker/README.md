# Stock Picker — Implementation Plan

## Project overview

Autonomous stock picking system for a $75,000 real-money portfolio.
Catalyst-driven, institutionally-anchored. No short selling, no options,
manual execution by portfolio owner.

Full specification: `/spec/` directory.
Architecture reference: `CLAUDE.md`.
This file: ordered implementation plan, step by step.

---

## Prerequisites — complete before writing any code

### 1. Python environment

```bash
python -m venv .venv
source .venv/bin/activate        # Mac/Linux
.venv\Scripts\activate           # Windows
pip install --upgrade pip
```

### 2. API keys — obtain all before starting

If any API key is missing, alert me to obtain the API key before implementing the layer step.

| Service | Purpose | Cost | Where to get |
|---|---|---|---|
| Anthropic API | Layer 2 LLM extraction | ~$162 one-time simulation + ~$5-10/month live | console.anthropic.com |
| Polygon.io | Price history with delisted tickers | $29-79/month | polygon.io |
| FRED API | Macro data | Free | fred.stlouisfed.org/docs/api/fred |
| Twilio | SMS alerts | ~$1-2/month | twilio.com |
| SMTP credentials | Email alerts | Free (Gmail app password) | your email provider |

### 3. Create `.env` at the repo root (one level above this folder)

**Location:** `_Stock_agent/.env` — NOT inside `1_Stock_Picker/`.
The file must be ignored by git (step 4 handles this).
Never paste real keys into this README, commit messages, chat, or logs.

Template — copy this into `.env` and replace every `<...>` placeholder
with your actual key. Each variable on its own line:

```dotenv
ANTHROPIC_API_KEY=<your-anthropic-key>
POLYGON_API_KEY=<your-polygon-key>
FRED_API_KEY=<your-fred-key>
TWILIO_ACCOUNT_SID=<your-twilio-sid>
TWILIO_AUTH_TOKEN=<your-twilio-auth-token>
TWILIO_FROM_NUMBER=<+1XXXXXXXXXX>
TWILIO_TO_NUMBER=<+1XXXXXXXXXX>
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=<your-email@example.com>
SMTP_PASSWORD=<your-smtp-app-password>
ALERT_EMAIL_TO=<your-email@example.com>
```

Before committing anything, verify `.env` is ignored:
```bash
git check-ignore -v .env    # must print a .gitignore match line
```

### 4. Verify `.gitignore` at the repo root
The root `.gitignore` already covers `.env`, `.venv/`, `__pycache__/`,
`*.pyc`, `*.db`, `*.db-shm`, `*.db-wal`, and the picker's
`1_Stock_Picker/data/_cache/` and `1_Stock_Picker/data/historical/`.
If any of those patterns are missing, append them — one per line:

```gitignore
.env
.venv/
__pycache__/
*.pyc
*.db
*.db-shm
*.db-wal
1_Stock_Picker/data/_cache/
1_Stock_Picker/data/historical/
.DS_Store
```


### 5. Split TimeStamp9 specification into spec files

Copy each section of TimeStamp9 into the corresponding file.
Create the `/spec/` directory and populate as follows:

| File | Content to copy from TimeStamp9 |
|---|---|
| `spec/portfolio_specs.md` | PORTFOLIO_SPECIFICATIONS block |
| `spec/layer_minus1.md` | Full Layer −1 section (−1.1 through −1.6) |
| `spec/layer_0.md` | Full Layer 0 section (0.1 through 0.5) |
| `spec/layer_1.md` | Full Layer 1 section (1.1 through 1.3) |
| `spec/layer_2.md` | Full Layer 2 section (2.1 through 2.3) |
| `spec/layer_3.md` | Full Layer 3 section (3.1 through 3.4) |
| `spec/layer_4.md` | Full Layer 4 section (4.1 through 4.6) + Integration with Main Quantitative Stock Picker section |
| `spec/layer_5.md` | Full Layer 5 section (5.1 through 5.3) |
| `spec/layer_6.md` | Full Layer 6 section (6.1 through 6.2) |
| `spec/layer_7.md` | Full Layer 7 section (7.1 through 7.6) |
| `spec/module_10.md` | Alert dispatcher — extracted from Layer 4 section 4.5 + module_10 entry from Implementation Modules |
| `spec/module_11.md` | Portfolio state tracker — extracted from Portfolio Specifications + module_11 entry from Implementation Modules |
| `spec/module_12.md` | Full Module 12 section (12.1 through 12.7) |
| `spec/pre_revenue_mode.md` | Pre-Revenue / Biotech Mode section |

Note: module_10.md and module_11.md do not have dedicated sections in
TimeStamp9 — they must be constructed by extracting from Layer 4
section 4.5 and the Portfolio Specifications block respectively.
See CLAUDE.md for the exact content of each file.

### 6. Install all dependencies

```bash
pip install anthropic polygon-api-client yfinance fredapi \
            feedparser requests python-dotenv apscheduler \
            twilio tiktoken pytest scikit-learn numpy pandas \
            icalendar
pip freeze > requirements.txt
```

### 7. Create folder structure

```bash
mkdir -p spec prompts src/database src/layer_minus1 src/layer_0 \
         src/layer_1 src/layer_2 src/layer_3 src/layer_4 \
         src/layer_5 src/layer_6 src/layer_7 src/modules \
         tests/integration data/_cache data/_outputs data/historical
```

### 8. Git initialisation

```bash
git init
git add .
git commit -m "Project scaffold — spec files, environment, folder structure"
```

---

## Technology stack

| Component | Technology |
|---|---|
| Language | Python 3.11+ |
| Database | SQLite (Python sqlite3 — no installation needed) |
| LLM | Anthropic API — claude-sonnet-4-20250514 |
| Scheduling | APScheduler |
| SMS | Twilio |
| Email | Python smtplib (SMTP) |
| Price data | Polygon.io Python client + yfinance |
| Macro data | fredapi Python library |
| SEC filings | requests + feedparser (EDGAR RSS) |
| Token counting | tiktoken |
| Testing | pytest |
| Environment | python-dotenv |
| ML (calibration) | scikit-learn (Ridge regression) |

---

## Implementation sequence

Modules are ordered by dependency. Never start a module before
all prerequisites are marked complete. For each step:

1. Read the spec file listed — do this before writing the prompt
2. Write the Claude Code prompt using the structure shown
3. Review the generated code
4. Run the tests
5. Fix any failures
6. Check off the definition of done
7. Commit to git before moving to the next step

### How to write each Claude Code prompt

Every prompt follows this structure:
CONTEXT: Read /spec/[filename].md in full before writing any code.

TASK: Implement [module name] as defined in the spec.

INPUTS:
[exactly what this module receives]

OUTPUTS:
[exactly what this module produces]

INTERFACES TO RESPECT:
[contracts with adjacent modules already built]

SPECIFIC REQUIREMENTS:
[constraints from spec that are easy to miss]

WHAT TO PRODUCE:

/src/[path]/[module].py
/tests/test_[module].py covering [specific cases]
Update requirements.txt if new dependencies added
DO NOT:
[common pitfalls specific to this module]


Save each prompt as `/prompts/prompt_NN_[name].md` before
submitting it to Claude Code.

---

## Phase 0 — Database foundation

*No dependencies. Must be completed before everything else.*

---

### Step 0.1 — Database schema

**Spec files to read:** `spec/layer_3.md`, `spec/layer_5.md`,
`spec/module_12.md`, `spec/portfolio_specs.md`

**Prompt key points:**
- Create SQLite schema for all tables
- Tables needed: companies, open_catalysts, probability_history,
  active_contradictions, institutional_accumulation, outcome_records,
  action_records, parameters, document_queue, alert_queue
- probability_sum_check enforced as a CHECK constraint
- deduplication_fingerprint must be UNIQUE per ticker per catalyst
- All ISO8601 dates stored as TEXT
- Include created_at and updated_at on every table

**Produces:**
src/database/schema.sql
src/database/db.py
tests/test_schema.py


**Definition of done:**
- [ ] All tables created without error on a fresh database
- [ ] `probability_sum_check` enforced — insert with sum ≠ 1.0 raises error
- [ ] `deduplication_fingerprint` unique constraint enforced
- [ ] `pytest tests/test_schema.py` passes
- [ ] `git commit -m "Step 0.1 complete — database schema"`

---

## Phase 1 — Universe construction

*Requires Phase 0.*

---

### Step 1.1 — Institution registry

**Spec file to read:** `spec/layer_minus1.md` sections −1.1 and −1.6

**Prompt key points:**
- Hardcode all institutions from spec with correct tier multipliers
- Tier 1A: 4.0×, Tier 1B: 3.5×, Tier 2A: 2.5×, Tier 2B: 2.0×,
  Tier 3: 1.0×, Tier 4: 0.0×
- Store in DB parameters table and in memory
- Include method to retrieve all institutions by tier

**Produces:**
src/layer_minus1/institution_registry.py
tests/test_institution_registry.py


**Definition of done:**
- [ ] All institutions present with correct tier assignments
- [ ] Tier multipliers match spec exactly
- [ ] Retrieval by tier works correctly
- [ ] `pytest tests/test_institution_registry.py` passes
- [ ] `git commit -m "Step 1.1 complete — institution registry"`

---

### Step 1.2 — EDGAR 13F parser and TWOS calculator

**Spec file to read:** `spec/layer_minus1.md` section −1.2

**Prompt key points:**
- Parse EDGAR 13F filings for tracked institutions
- Use filing_date field exclusively — never period_of_report
- Classify QoQ changes for all 6 change types with correct thresholds:
  new position, significant increase (>15% general, >10% for 1A/1B),
  moderate increase (5-15%), flat (<5%), decrease (>5%), exit
- Compute TWOS: Σ(ownership_pct × tier_multiplier × change_momentum_factor)
  change_momentum_factor: new=2.0, sig_inc=1.5, mod_inc=1.2,
  flat=1.0, decrease=0.7, exit=0.0
- Assign processing tiers: active, passive, watchlist
- Apply crowding penalty: >4 institutions AND >50% appreciation → TWOS × 0.6
- Write results to companies table in DB

**Produces:**
src/layer_minus1/edgar_13f_parser.py
src/layer_minus1/twos_calculator.py
tests/test_13f_parser.py
tests/test_twos_calculator.py


**Definition of done:**
- [ ] Point-in-time enforcement tested — assert period_of_report never used
- [ ] TWOS formula verified against 3 manual calculations
- [ ] All 6 QoQ change types classified correctly
- [ ] Significant increase threshold differs for Tier 1A/1B vs others
- [ ] Crowding penalty triggers at correct thresholds
- [ ] Active/passive/watchlist assignment correct
- [ ] `pytest tests/test_13f_parser.py tests/test_twos_calculator.py` passes
- [ ] `git commit -m "Step 1.2 complete — 13F parser and TWOS calculator"`

---

### Step 1.3 — Form 4 / 13G / 13D continuous monitor

**Spec file to read:** `spec/layer_minus1.md` section −1.3

**Prompt key points:**
- Poll EDGAR RSS for Form 4 filings — retain transaction code "P" only
- Reject all non-P transactions (exercises, grants, dispositions)
- SC 13D: trigger immediate active monitoring elevation
- SC 13G: active monitoring elevation if filer is Tier 1A/1B/2A only
- SC 13G/A amendment >1% increase: treat as significant increase signal
- Apply insider signal source tiering from spec table
- Emit INSIDER_PURCHASE_ALERT to alert queue on any P transaction
- Emit action to Module 12 queue for calendar creation

**Produces:**
src/layer_minus1/form4_monitor.py
tests/test_form4_monitor.py


**Definition of done:**
- [ ] Transaction code "P" correctly identified — all others rejected
- [ ] SC 13D immediately elevates ticker to active monitoring
- [ ] SC 13G elevation conditional on filer tier
- [ ] Correct bull probability adjustment applied per source tier:
      +8% Tier 1, +5% Tier 2, +3% Tier 3, +1% Tier 4, 0% Tier 5
- [ ] INSIDER_PURCHASE_ALERT emitted to alert queue
- [ ] `pytest tests/test_form4_monitor.py` passes
- [ ] `git commit -m "Step 1.3 complete — Form 4 / 13G/13D monitor"`

---

### Step 1.4 — Supplementary discovery pipeline

**Spec file to read:** `spec/layer_minus1.md` section −1.4

**Prompt key points:**
- Source 1 — ClinicalTrials.gov: all biotech/pharma IND filings or
  trial registrations in last 24 months regardless of institutional holding
  Auto-promote to active monitoring when Tier 1A/1B 13F appears
- Source 2 — EDGAR new issuers: first 10-K or S-1 in last 12 months
  within biotech, medtech, specialty pharma SIC codes
  Watchlist intensity only
- Source 3 — Press wire orphan detection: company not in universe
  that generates Tier 1 or Tier 2 keyword hit gets one-time Layer 2 extraction
  If magnitude_class is transformative or significant: enter passive monitoring

**Produces:**
src/layer_minus1/clinicaltrials_client.py
src/layer_minus1/edgar_new_issuer_screener.py
src/layer_minus1/discovery_pipeline.py
tests/test_discovery_pipeline.py


**Definition of done:**
- [ ] ClinicalTrials.gov API returns registrations within 24-month window
- [ ] EDGAR new issuers correctly filtered to biotech/medtech/pharma SIC codes
- [ ] Auto-promotion to active monitoring fires on Tier 1A/1B 13F appearance
- [ ] Press wire orphan extraction produces valid Layer 2 JSON
- [ ] `pytest tests/test_discovery_pipeline.py` passes
- [ ] `git commit -m "Step 1.4 complete — supplementary discovery pipeline"`

---

## Phase 2 — Macro regime

*Requires Phase 0 only. Can run in parallel with Phase 1.*

---

### Step 2.1 — FRED and VIX data clients

**Spec file to read:** `spec/layer_0.md` section 0.1

**Prompt key points:**
- Fetch all 8 inputs: DFF, CPIAUCSL, PCEPI, T10Y2Y,
  BAMLC0A0CM, BAMLH0A0HYM2 via fredapi library
- Fetch VIX via yfinance (ticker ^VIX)
- Accept date parameter for historical mode queries
- Cache data locally after first fetch — no repeated API calls
  for the same date
- Compute 3-month rolling rate direction from DFF
- Compute 5-day credit spread and yield curve changes
- Compute 20-day VIX trend

**Produces:**
src/layer_0/fred_client.py
src/layer_0/vix_client.py
tests/test_macro_data_clients.py


**Definition of done:**
- [ ] All 8 FRED series fetch correctly with historical date parameter
- [ ] VIX history fetches correctly for any specified date
- [ ] Local cache prevents repeated API calls for same date
- [ ] 3-month rolling rate direction computed correctly
- [ ] 5-day change computations correct for spreads and yield curve
- [ ] `pytest tests/test_macro_data_clients.py` passes
- [ ] `git commit -m "Step 2.1 complete — macro data clients"`

---

### Step 2.2 — Regime classifier

**Spec file to read:** `spec/layer_0.md` sections 0.2, 0.3, 0.4

**Prompt key points:**
- Output one of four regime tags: GROWTH_FAVOURABLE,
  DEFENSIVE_EASING, CYCLICAL_TRANSITION, FULL_RISK_OFF
- Implement boundary parameters exactly as specified:
  VIX thresholds 18/25, credit spread thresholds -20bp/+30bp,
  yield curve thresholds +50bp/0bp, rate direction -15bp/+15bp
- Implement adaptive cadence: daily when any instability trigger active
  (VIX > 20, credit spread change > +20bp in 5 days,
  yield curve change > 20bp in 5 days, FOMC surprise),
  weekly otherwise
- Regime change propagates immediately: emit REGIME_CHANGE event
  to alert queue — SMS priority
- Write regime tag and cadence_flag to parameters table with timestamp
- HISTORICAL mode: accept explicit date parameter

**Produces:**
src/layer_0/regime_classifier.py
tests/test_regime_classifier.py


**Definition of done:**
- [ ] All 4 regime outputs reachable from boundary conditions
- [ ] Instability triggers switch cadence to daily correctly
- [ ] Regime change emits REGIME_CHANGE event to alert queue
- [ ] FULL_RISK_OFF correctly applies 0.70× magnitude modulation
- [ ] HISTORICAL mode accepts date parameter, returns correct regime
- [ ] GROWTH_FAVOURABLE applies 1.00× (no modulation)
- [ ] `pytest tests/test_regime_classifier.py` passes
- [ ] `git commit -m "Step 2.2 complete — regime classifier"`

---

## Phase 3 — Document ingestion

*Requires Phase 1 (for universe list).*

---

### Step 3.1 — EDGAR RSS client

**Spec file to read:** `spec/layer_1.md` section 1.1

**Prompt key points:**
- Poll EDGAR full-text RSS feed for 8-K, 10-Q, 10-K, Form 4
- Retain 8-K items: 1.01, 2.02, 4.02, 7.01, 8.01, 9.01 only
- Retain Form 4: transaction code "P" only
- Discard: routine Section 16 reports with no P transactions
- Discard: amended filings where original already processed
- Active monitoring tickers: all filing types
- Passive monitoring tickers: all filing types (no press wire)
- Poll interval: every 15 minutes in live mode

**Produces:**
src/layer_1/edgar_rss_client.py
tests/test_edgar_rss_client.py


**Definition of done:**
- [ ] Fetches all 4 filing types from EDGAR RSS
- [ ] 8-K item code filter: only listed items pass
- [ ] Form 4 non-P transactions discarded
- [ ] Amended filings deduplicated against originals
- [ ] Both active and passive monitoring tickers subscribed
- [ ] `pytest tests/test_edgar_rss_client.py` passes
- [ ] `git commit -m "Step 3.1 complete — EDGAR RSS client"`

---

### Step 3.2 — Press wire RSS client

**Spec file to read:** `spec/layer_1.md` section 1.2

**Prompt key points:**
- Poll RSS feeds from Business Wire, PR Newswire,
  GlobeNewswire, AccessWire
- Active monitoring tickers only — passive and watchlist excluded
- Deduplicate across wire services: same release on multiple wires
  should produce one document, not four
- Attach ticker to document by matching company name
  against active monitoring universe

**Produces:**
src/layer_1/presswire_rss_client.py
tests/test_presswire_rss_client.py


**Definition of done:**
- [ ] All 4 press wire RSS feeds polled
- [ ] Active monitoring tickers only — passive correctly excluded
- [ ] Cross-wire deduplication working (same release ≠ 4 entries)
- [ ] Ticker attribution from company name matching tested
- [ ] `pytest tests/test_presswire_rss_client.py` passes
- [ ] `git commit -m "Step 3.2 complete — press wire RSS client"`

---

### Step 3.3 — Keyword filter and document queue

**Spec file to read:** `spec/layer_1.md` sections 1.2 and 1.3

**Prompt key points:**
- Implement all 4 keyword tiers exactly as specified
- Pass conditions (all four must be implemented):
  (1) always pass: ≥1 Tier 1 OR Tier 2 keyword hit
  (2) conditional pass: ≥2 Tier 3/4 keyword hits
  (3) active-only pass: ≥1 Tier 3/4 hit AND ticker in active_monitoring
  (4) always reject: single Tier 3/4 hit AND ticker in passive/watchlist
- Log all filtered-out documents separately for monthly audit
  (only log ticker, date, document URL — not full text)
- Persist passed documents to document_queue DB table with:
  source_url, publication_date, ticker, filing_type, raw_text
- Estimated filter reduction: ~70% of volume before LLM

**Produces:**
src/layer_1/keyword_filter.py
src/layer_1/document_queue.py
tests/test_keyword_filter.py


**Definition of done:**
- [ ] All 4 pass/reject conditions tested with dedicated test cases
- [ ] Active vs passive vs watchlist routing all tested
- [ ] Filtered-out documents logged to separate audit table
- [ ] Passed documents persisted to document_queue table
- [ ] Keyword lists match spec exactly — no additions or removals
- [ ] `pytest tests/test_keyword_filter.py` passes
- [ ] `git commit -m "Step 3.3 complete — keyword filter and document queue"`

---

## Phase 4 — LLM extraction

*Requires Phase 3. Most critical module — read spec twice.*

---

### Step 4.1 — Probability priors and schema validator

**Spec file to read:** `spec/layer_2.md` sections 2.1 and 2.2

**Prompt key points:**
- Hardcode all 6 base rates from PROBABILITY_PRIORS spec block
- Hardcode adjustment bounds — enforced in code, not delegated to LLM
- Implement schema validator for extraction JSON:
  validates all required fields present, correct types,
  probability_sum_check equals 1.0 (tolerance: ±0.001)
- Implement adjustment bound capper: silently cap if LLM exceeds bounds,
  set human_override_flag in DB

**Produces:**
src/layer_2/probability_priors.py
src/layer_2/schema_validator.py
tests/test_priors_and_validator.py


**Definition of done:**
- [ ] All 6 base rates hardcoded with correct values from spec
- [ ] Adjustment bounds enforced in code — tested by attempting to exceed them
- [ ] probability_sum_check validates to exactly 1.0 (±0.001 tolerance)
- [ ] Schema validator rejects JSON missing any required field
- [ ] human_override_flag set when bounds are breached
- [ ] `pytest tests/test_priors_and_validator.py` passes
- [ ] `git commit -m "Step 4.1 complete — probability priors and validator"`

---

### Step 4.2 — LLM extraction pipeline

**Spec file to read:** `spec/layer_2.md` in full — read the entire file twice

**Prompt key points:**
- Model: claude-sonnet-4-20250514, max_tokens: 1500
- System prompt instructs LLM to return only valid JSON matching schema
- LIVE/SHADOW mode: LLM generates probability adjustment within bounds
- HISTORICAL mode: no LLM probability adjustment
  base_rate applied directly, llm_probability_adjustment_applied = false
- Validate probability_sum = 1.0 — if invalid, re-prompt up to 3 times
- After 3 failed validations: log error, skip document
- Cap adjustment bounds in code after LLM response — do not trust LLM
- Enforce 350-token summary via tiktoken (cl100k_base encoding)
  Truncate at 350 tokens if LLM exceeds limit
- Log token usage per extraction to parameters table for cost tracking
- Exponential backoff on API errors: 1s, 2s, 4s

**Produces:**
src/layer_2/extractor.py
src/layer_2/prompt_templates.py
tests/test_extractor.py


**Definition of done:**
- [ ] LIVE/SHADOW: LLM probability adjustment applied within bounds
- [ ] HISTORICAL: assert llm_probability_adjustment_applied = false
- [ ] probability_sum = 1.0 validated — re-prompt on failure
- [ ] Adjustment bounds capped in code regardless of LLM output
- [ ] 350-token summary enforced — test with >350 token input
- [ ] Token usage logged to parameters table
- [ ] Exponential backoff tested by mocking API failure
- [ ] `pytest tests/test_extractor.py` passes
- [ ] `git commit -m "Step 4.2 complete — LLM extraction pipeline"`

---

## Phase 5 — Registry

*Requires Phase 4.*

---

### Step 5.1 — Deduplicator

**Spec file to read:** `spec/layer_3.md` section 3.3 step_1_deduplication

**Prompt key points:**
- Fingerprint = hash(catalyst_type + subtype + ticker + date_window_quarter)
  where date_window_quarter = the calendar quarter of the document date
- If fingerprint matches existing open_catalyst for this ticker:
  CORROBORATING MODE — weight = 0.3 × standard_update_weight
  log corroborating_flag = true in probability_history
  do NOT create new open_catalyst entry
  add source_url to contributing_signals
- If no match: NEW EVENT MODE — standard weight, new open_catalyst entry

**Produces:**
src/layer_3/deduplicator.py
tests/test_deduplicator.py


**Definition of done:**
- [ ] Same event from press release AND 8-K → corroborating mode, not new entry
- [ ] Corroborating weight = 0.3× confirmed in test
- [ ] corroborating_flag = true in probability_history for corroborating events
- [ ] Different events in same quarter → separate new entries
- [ ] `pytest tests/test_deduplicator.py` passes
- [ ] `git commit -m "Step 5.1 complete — deduplicator"`

---

### Step 5.2 — CCS calculator

**Spec file to read:** `spec/layer_3.md` section 3.2

**Prompt key points:**
- Implement CCS formula exactly:
  CCS = Σ[EV_pct × magnitude_weight × time_decay × survival_probability]
        × insider_accumulation_multiplier
        × crowding_adjustment × regime_modulation
- magnitude_weight: transformative=1.00, significant=0.70,
  moderate=0.40, minor=0.20
- time_decay: immediate=1.00, 0-30d=0.95, 30-90d=0.80,
  90-180d=0.60, 180d+=0.35
- survival_probability = min(cash_runway / quarters_to_catalyst, 1.0)
- insider_accumulation_multiplier: base=1.00, cap=1.35
  increments per tier as specified
- crowding_adjustment: 0.60 if crowding_flag else 1.00
- regime_modulation: GROWTH_FAVOURABLE=1.00, DEFENSIVE_EASING=0.85,
  CYCLICAL_TRANSITION=0.85, FULL_RISK_OFF=0.70

**Produces:**
src/layer_3/ccs_calculator.py
tests/test_ccs_calculator.py


**Definition of done:**
- [ ] CCS formula output verified against 3 manual calculations
- [ ] All magnitude_weight values match spec
- [ ] All time_decay values match spec
- [ ] survival_probability correctly capped at 1.0
- [ ] insider_accumulation_multiplier correctly capped at 1.35
- [ ] crowding_adjustment applies 0.60× when flag set
- [ ] All 4 regime modulation values correct
- [ ] `pytest tests/test_ccs_calculator.py` passes
- [ ] `git commit -m "Step 5.2 complete — CCS calculator"`

---

### Step 5.3 — Bayesian updater

**Spec file to read:** `spec/layer_3.md` section 3.3 — all 9 steps

**Prompt key points:**
- Implement all 9 steps in exact order — do not reorder
- Step 1 (deduplication) always runs before any other step
- Step 3: apply adjustment to current estimate, not original prior
  if CORROBORATING MODE: multiply by 0.3×
- Step 5: log to probability_history with all fields including
  update_magnitude = abs(new_bull - old_bull) and corroborating_flag
- Step 6: contradiction detection — if new signal changes bull probability
  by >10pp in opposite direction to prior signal in same session,
  log to active_contradictions
- Step 9: emit threshold trigger event when CCS crosses 45 (entry)
  or drops below 35 (exit)

**Produces:**
src/layer_3/bayesian_updater.py
tests/test_bayesian_updater.py


**Definition of done:**
- [ ] All 9 steps execute in correct sequence
- [ ] Deduplication always first — tested by inserting duplicate
- [ ] probability_history row written with all fields on every update
- [ ] update_magnitude computed correctly
- [ ] corroborating_flag set correctly
- [ ] active_contradictions logged on opposing signal detection
- [ ] CCS threshold triggers emitted at correct boundaries (45 up, 35 down)
- [ ] `pytest tests/test_bayesian_updater.py` passes
- [ ] `git commit -m "Step 5.3 complete — Bayesian updater"`

---

### Step 5.4 — Registry manager

**Spec file to read:** `spec/layer_3.md` sections 3.1 and 3.4

**Prompt key points:**
- Implement full COMPANY_ENTRY schema in DB
- financials_snapshot: updated from each 10-Q/10-K
  using filing_date enforcement — never period end date
- options_surface: updated weekly
  divergence_flag = true when divergence_from_registry_ev > 15%
- All 6 sell trigger conditions detected:
  score_collapse (CCS < 35), dependency_violation,
  valuation_realisation, survival_risk (< 0.50),
  institutional_exit + CCS declining, TWOS deterioration (>30% QoQ)
- watchlist_status field updated on every CCS change

**Produces:**
src/layer_3/registry.py
tests/test_registry.py


**Definition of done:**
- [ ] COMPANY_ENTRY schema fully matches spec
- [ ] financials_snapshot uses filing_date — not period end date
- [ ] options_surface divergence_flag triggers at >15% divergence
- [ ] All 6 sell triggers detected and emitted correctly
- [ ] watchlist_status transitions: not_eligible → watchlist →
      position_recommended → in_position tested
- [ ] `pytest tests/test_registry.py` passes
- [ ] `git commit -m "Step 5.4 complete — registry manager"`

---

## Phase 6 — Portfolio tracking

*Requires Phase 0 only. Can run in parallel with Phases 1-5.*

---

### Step 6.1 — Portfolio state tracker (Module 11)

**Spec file to read:** `spec/module_11.md`, `spec/portfolio_specs.md`

**Prompt key points:**
- Track per position: ticker, entry_date, entry_price,
  actual_position_$, sleeve_assignment, holding_days, days_to_ltcg
- Track sleeve totals: S1 current %, S2 current %, S3 current %
- Track cash reserve current balance
- Enforce all hard constraints before confirming any BUY:
  sleeve ceiling (S1 43%, S2 31%, S3 16%),
  cash reserve gate (< $3,000 blocks BUY),
  max single sector 25%,
  max catalyst type 40% of total CCS exposure,
  max single position $5,500,
  max loss per position $3,000
- LTCG: record entry_date, compute days_held on read,
  flag when days_to_12month_ltcg ≤ 60

**Produces:**
src/modules/portfolio_tracker.py
tests/test_portfolio_tracker.py


**Definition of done:**
- [ ] Sleeve ceiling enforcement tested: BUY blocked when ceiling breached
- [ ] Cash reserve gate tested: BUY blocked when cash < $3,000
- [ ] Max single sector 25% enforcement tested
- [ ] LTCG tracking: entry_date correct, days_held correct,
      60-day flag triggers at correct threshold
- [ ] Position opens and closes update sleeve totals correctly
- [ ] `pytest tests/test_portfolio_tracker.py` passes
- [ ] `git commit -m "Step 6.1 complete — portfolio state tracker"`

---

## Phase 7 — Signal generation

*Requires Phases 5 and 6.*

---

### Step 7.1 — Position sizer

**Spec file to read:** `spec/layer_4.md` section 4.2

**Prompt key points:**
- CCS band mapping:
  85-100 → 8-10% / $6,000-$7,500
  70-84 → 5-7% / $3,750-$5,250
  55-69 → 3-4% / $2,250-$3,000
  45-54 → watchlist only
- Hard override rule applied after band selection:
  final_$ = min(CCS_band_$, sleeve_ceiling_$,
                3000/abs(bear_scenario_pct), 5500)
- Set sleeve_ceiling_applied = true if sleeve was binding constraint
- Set bear_loss_limit_applied = true if $3,000 loss limit was binding
- Apply 7 multipliers sequentially in exact order from spec
- Combined institution + insider capped at 1.25× — not additive
- Pre-revenue: cap one tier below CCS-implied before multipliers
- Absolute hard cap $5,500 applied last, after all multipliers

**Produces:**
src/layer_4/position_sizer.py
tests/test_position_sizer.py


**Definition of done:**
- [ ] Hard override: all 4 min() components tested individually
- [ ] Bear loss limit: $3,000/0.65 = $4,615 cap tested with -65% bear scenario
- [ ] All 7 multipliers apply in correct sequence
- [ ] Combined institution+insider capped at 1.25× not sum of 1.15+1.15
- [ ] Pre-revenue caps one tier below CCS
- [ ] $5,500 hard cap applied last — cannot be exceeded by any multiplier
- [ ] sleeve_ceiling_applied and bear_loss_limit_applied flags correct
- [ ] `pytest tests/test_position_sizer.py` passes
- [ ] `git commit -m "Step 7.1 complete — position sizer"`

---

### Step 7.2 — Thesis writer

**Spec file to read:** `spec/layer_4.md` section 4.4 thesis_statement field

**Prompt key points:**
- LLM generates thesis_statement covering all 5 required sections:
  (1) CATALYST — specific catalyst, probability basis, evidence
  (2) INSTITUTIONAL SIGNAL — which institutions, tier, recent activity
  (3) VALUATION OR STRUCTURAL CONTEXT — discount vs peers or secular trend
  (4) SCENARIO SUMMARY — bull/base/bear with probabilities
  (5) MACRO CONTEXT — current regime, supports or constrains position
- 350-token limit enforced via tiktoken cl100k_base
  truncate to 350 tokens if LLM exceeds
- thesis_invalidation generated as separate LLM call:
  specific event proving thesis wrong — not a price level
- Write to plain English, cite numbers from underlying data

**Produces:**
src/layer_4/thesis_writer.py
tests/test_thesis_writer.py


**Definition of done:**
- [ ] All 5 thesis sections present in generated output
- [ ] 350-token limit enforced — tested with mock LLM returning >350 tokens
- [ ] thesis_invalidation is a specific event, not a price level
- [ ] Numbers from underlying data cited in output
- [ ] `pytest tests/test_thesis_writer.py` passes
- [ ] `git commit -m "Step 7.2 complete — thesis writer"`

---

### Step 7.3 — Signal generator

**Spec file to read:** `spec/layer_4.md` sections 4.1, 4.3, 4.4, 4.5

**Prompt key points:**
- Watchlist entry: CCS crosses 45 from below AND
  survival_probability_to_catalyst > 0.70
- On entry: tag regime, tag QoQ_change_signal, set thesis_review_date
- RECOMMENDATION_OUTPUT JSON must match spec schema exactly
- All 6 sell triggers detected and routed to correct alert priority
- Alert priority assignment:
  SMS: catalyst resolution, DEPENDENCY_VIOLATED, SURVIVAL_WARNING,
       REGIME_CHANGE, Tier 1A Form 4 P > $1M
  Daily: PROBABILITY_UPGRADE/DOWNGRADE, INSTITUTIONAL_EXIT,
         new watchlist entries CCS > 55
  Weekly: universe updates, sleeve drift, LTCG approaching
- calendar_actions_created: list of Module 12 action IDs created
  by this recommendation — populated by calling Module 12

**Produces:**
src/layer_4/signal_generator.py
tests/test_signal_generator.py


**Definition of done:**
- [ ] Watchlist entry fires at CCS = 45 from below — not above
- [ ] Watchlist entry blocked if survival_probability < 0.70
- [ ] RECOMMENDATION_OUTPUT JSON passes schema validation
- [ ] All 6 sell triggers detected
- [ ] Alert priority assigned correctly for all 15 alert types
- [ ] calendar_actions_created populated with Module 12 IDs
- [ ] `pytest tests/test_signal_generator.py` passes
- [ ] `git commit -m "Step 7.3 complete — signal generator"`

---

## Phase 8 — Alerts and calendar

*Requires Phase 7.*

---

### Step 8.1 — Alert dispatcher (Module 10)

**Spec file to read:** `spec/module_10.md`, `spec/layer_4.md` section 4.5

**Prompt key points:**
- SMS via Twilio for all urgent alerts — fire immediately
- Daily digest: assembled at 8am local time, sent via SMTP
  Must include: new watchlist entries CCS > 55,
  PROBABILITY_UPGRADE/DOWNGRADE > 10pp on held positions,
  INSTITUTIONAL_EXIT Tier 1A/1B on held positions,
  weekly CCS rescore results,
  positions within 2 weeks of thesis_review_date,
  Module 12 upcoming 14-day action preview
- Weekly summary: assembled Sunday evening, sent via SMTP
  Must include: universe update from Layer -1,
  sleeve allocation drift report,
  cash reserve level (flag if < $3,000),
  positions within 60 days of 12-month LTCG threshold
- Queue-based: alerts received from all layers into alert_queue table
  dispatcher polls queue and routes by priority

**Produces:**
src/modules/alert_dispatcher.py
src/modules/sms_client.py
src/modules/email_client.py
tests/test_alert_dispatcher.py


**Definition of done:**
- [ ] SMS sent immediately for all 5 SMS-priority alerts
- [ ] Daily digest contains all required sections
- [ ] Weekly summary contains all required sections
- [ ] Module 12 14-day preview included in daily digest
- [ ] Queue polling and routing tested with mock alerts
- [ ] `pytest tests/test_alert_dispatcher.py` passes
- [ ] Manual test: send test SMS — confirm receipt on your phone
- [ ] Manual test: send test email — confirm receipt in your inbox
- [ ] `git commit -m "Step 8.1 complete — alert dispatcher"`

---

### Step 8.2 — Action tracking calendar (Module 12)

**Spec file to read:** `spec/module_12.md` — read all 7 sections in full

**Prompt key points:**
- Implement all 18 action types from spec section 12.2
- Auto-creation rules from spec section 12.3 — implement all:
  BUY → always: THESIS_REVIEW + LTCG_THRESHOLD_MONITOR
  BUY + datable catalyst → also: CATALYST_DATE_MONITOR
  BUY + weekend_catalyst_flag → also: WEEKEND_CATALYST_REVIEW
  BUY + S2 or S3 sleeve → also: MONTHLY_POSITION_RESCORE (recurring)
  SELL → create: SELL_TRIGGER_REVIEW + cancel all position actions
  Regime change → create: REGIME_CHANGE_REVIEW (urgent, same day)
  System init → create all 8 recurring system actions
  Live mode launch → create: CONTAMINATION_CHECK (+90 days)
  Prompt trigger → create: PROMPT_REVIEW (within 14 days)
- Status management: pending → completed/overdue/cancelled
- Overdue escalation: >3 business days → urgent,
  >7 business days → CRITICAL_OVERDUE flag in daily digest
- Recurring: on completion, auto-create next occurrence
- Implement all 6 dashboard views as DB queries
- iCal export produces valid .ics file
- CSV and JSON exports

**Produces:**
src/modules/action_calendar.py
src/modules/ical_exporter.py
tests/test_action_calendar.py


**Definition of done:**
- [ ] All 18 action types implemented
- [ ] BUY with datable catalyst creates correct set of 4 actions
- [ ] BUY without datable catalyst creates correct set of 2 actions
- [ ] SELL cancels all pending position-level actions for that ticker
- [ ] Regime change creates REGIME_CHANGE_REVIEW with urgent priority
- [ ] System init creates all 8 recurring system actions with correct dates
- [ ] Overdue escalation: status escalates after 3 business days
- [ ] CRITICAL_OVERDUE flag after 7 business days
- [ ] Recurring auto-create tested: complete WEEKLY_UNIVERSE_REVIEW,
      assert next occurrence created for following Sunday
- [ ] All 6 dashboard views return correct data
- [ ] iCal export imports correctly into Google Calendar or Apple Calendar
- [ ] `pytest tests/test_action_calendar.py` passes
- [ ] `git commit -m "Step 8.2 complete — action tracking calendar"`

---

## Phase 9 — Outcome tracking and calibration

*Requires Phase 7.*

---

### Step 9.1 — Measurement clocks

**Spec file to read:** `spec/layer_5.md` section 5.2

**Prompt key points:**
- T_entry: closing price on recommendation day
  slippage: ADV > $1M → 0%, ADV $500k-$1M → 0.5%, ADV < $500k → 1.0%
- T_resolution: closing price on catalyst resolution day
  for undated catalysts: T_thesis_change = date when
  probability_history records update_magnitude > 10pp
- T_exit: closing price on first sell trigger day
  OR T_entry + fixed_horizon if no trigger fires:
  near_term = +75 days, medium_term = +195 days, structural = +405 days
- Always compute both round_trip_return and fixed_horizon_return
- exit_timing_delta = round_trip_return - fixed_horizon_return
  positive = exit timing added value
  negative = exit timing destroyed value

**Produces:**
src/layer_5/measurement_clocks.py
tests/test_measurement_clocks.py


**Definition of done:**
- [ ] T_entry slippage applied correctly for each ADV tier
- [ ] T_resolution uses correct catalyst date for dated catalysts
- [ ] T_thesis_change triggers at > 10pp probability update
- [ ] Fixed horizon fallback: all 3 horizon values correct (75/195/405)
- [ ] Both returns always computed — neither is optional
- [ ] exit_timing_delta arithmetic correct
- [ ] `pytest tests/test_measurement_clocks.py` passes
- [ ] `git commit -m "Step 9.1 complete — measurement clocks"`

---

### Step 9.2 — Outcome tracker (Layer 5)

**Spec file to read:** `spec/layer_5.md` sections 5.1 and 5.3

**Prompt key points:**
- Implement OUTCOME_RECORD schema in full including all three outputs
- thesis_statement_at_entry: copy verbatim from Layer 4 output
  at time of recommendation — never modify
- Brier score:
  BS = (bull_p - bull_occurred)² + (base_p - base_occurred)²
       + (bear_p - bear_occurred)²
- Price impact MAE: abs(actual_price_impact - predicted_scenario_impact)
- Sharpe contribution: (return - risk_free_rate) / position_volatility
  annualised
- Survivorship bias — mandatory:
  Use Polygon.io with delisted tickers
  Delisted: record last available trading price or $0.01
  Bankrupt: bear scenario, price_impact = -100%
  Never exclude any position from calculations

**Produces:**
src/layer_5/outcome_tracker.py
src/layer_5/brier_calculator.py
tests/test_outcome_tracker.py


**Definition of done:**
- [ ] OUTCOME_RECORD schema fully implemented in DB
- [ ] thesis_statement_at_entry copied verbatim — not regenerated
- [ ] Brier score formula correct — verified against manual calculation
- [ ] Delisted ticker: last available price used, not excluded
- [ ] Bankrupt ticker: -100% price impact, bear scenario, still recorded
- [ ] Polygon.io client configured to include delisted tickers
- [ ] `pytest tests/test_outcome_tracker.py` passes
- [ ] `git commit -m "Step 9.2 complete — outcome tracker"`

---

### Step 9.3 — Parameter calibration engine (Layer 6)

**Spec file to read:** `spec/layer_6.md` in full

**Prompt key points:**
- Minimum observations enforced before any parameter update —
  assert in code, never bypass
- All calibration targets implemented:
  Layer -1 tier multipliers via Ridge regression
  Layer 0 boundaries semi-annual
  Layer 2 base rates: updated_base = 0.7×current + 0.3×actual
  Layer 2 adjustment bounds: tighten by 25% if no correlation
  Layer 2 magnitude_class: verify empirical ratios vs formula
  Layer 3 CCS weights via Ridge regression — max 10 parameters
  Layer 4 band thresholds: Sharpe ratio comparison
  Layer 4 sell trigger: fixed_horizon vs model_exit comparison
- Governance: changes > 30% set manual_review_required flag,
  do not auto-apply, require human confirmation
- All updates logged to parameters table with old value, new value,
  sample size, metrics before and after
- Early trigger: run immediately if Brier degrades > 20% vs prior quarter
- Output calibration_report as JSON

**Produces:**
src/layer_6/calibration_engine.py
src/layer_6/calibration_report.py
tests/test_calibration_engine.py


**Definition of done:**
- [ ] Minimum observation enforcement: assert update blocked below threshold
      Test with 19 observations for a parameter requiring 20
- [ ] Weighted base rate update formula: 0.7×current + 0.3×actual verified
- [ ] Ridge regression produces plausible multipliers on synthetic data
- [ ] Governance flag: change of 35% sets manual_review_required,
      does not auto-apply
- [ ] All updates logged to parameters table with all required fields
- [ ] Early trigger: detects Brier degradation > 20% correctly
- [ ] calibration_report JSON exported to data/_outputs/
- [ ] `pytest tests/test_calibration_engine.py` passes
- [ ] `git commit -m "Step 9.3 complete — parameter calibration engine"`

---

## Phase 10 — Historical simulation

*Requires all Phases 0-9 complete.*
*Run last. This is the pre-calibration step before going live.*

---

### Step 10.1 — Historical data loader

**Spec file to read:** `spec/layer_7.md` sections 7.1 and 7.2

**Prompt key points:**
- All data queries filtered by data_date <= current_simulation_date
  Implemented as DB query constraint — NOT application-level check
- Download EDGAR filings selectively:
  only tracked institutions (35) and active universe tickers (500)
  not full EDGAR archive
- Download Polygon.io price history with delisted tickers
  Date range: January 1 2020 to December 31 2024
- Cache all downloaded data locally under data/historical/
  Do not re-download if already cached
- filing_date used everywhere — period_of_report never referenced
- FRED data cached locally after first download

**Produces:**
src/layer_7/data_loader.py
src/layer_7/point_in_time_enforcer.py
tests/test_data_loader.py


**Definition of done:**
- [ ] Point-in-time enforcer: query for 2021-06-01 data
      cannot return any record with data_date > 2021-06-01
      Tested with assertion on query results
- [ ] EDGAR downloads selective — tracked tickers only, not full archive
- [ ] Polygon.io download includes at least 3 known-delisted tickers
- [ ] Local cache prevents re-download on second run
- [ ] filing_date used in all queries — assert period_of_report not referenced
- [ ] `pytest tests/test_data_loader.py` passes
- [ ] `git commit -m "Step 10.1 complete — historical data loader"`

---

### Step 10.2 — Checkpoint manager

**Spec file to read:** `spec/layer_7.md` section 7.4

**Prompt key points:**
- Save checkpoint after each simulated trading day completes
- Checkpoint file: single JSON in data/_cache/simulation_checkpoint.json
  containing last_processed_date and run metadata
- Restart resumes from last_processed_date + 1 business day
  not from beginning of simulation
- Checkpoint must be atomic — write to temp file then rename
  so partial writes do not corrupt checkpoint

**Produces:**
src/layer_7/checkpoint_manager.py
tests/test_checkpoint_manager.py


**Definition of done:**
- [ ] Checkpoint saved after each day processed
- [ ] Restart resumes from correct date — tested by simulating crash
- [ ] Atomic write: temp file + rename pattern implemented
- [ ] Checkpoint file is valid JSON — never partially written
- [ ] `pytest tests/test_checkpoint_manager.py` passes
- [ ] `git commit -m "Step 10.2 complete — checkpoint manager"`

---

### Step 10.3 — Simulation harness

**Spec file to read:** `spec/layer_7.md` section 7.4 pseudocode

**Prompt key points:**
- Run in strict chronological order: January 2020 to December 2024
- Pass operating_mode="HISTORICAL" to every Layer 2 call — assert in loop
- Layer 2 extractions run in overnight batches with rate limiting
  Do not run interactively — configure for unattended execution
- Checkpoint saves after each simulated day
- At end: automatically run Layer 6 calibration engine on all outcomes
- HISTORICAL mode must prevent all probability adjustments in Layer 2

**Produces:**
src/layer_7/simulation_harness.py
tests/test_simulation_harness.py


**Definition of done:**
- [ ] operating_mode="HISTORICAL" asserted on every Layer 2 call in loop
- [ ] Simulation runs 30-day window test without errors
      (do not run full 5-year test in pytest — too expensive)
- [ ] Checkpoint saves after each day in 30-day test
- [ ] Known-delisted ticker: correctly processed, recorded in outcome_tracker
- [ ] Layer 6 runs at end of simulation — calibration_report produced
- [ ] `pytest tests/test_simulation_harness.py` passes (30-day window only)
- [ ] `git commit -m "Step 10.3 complete — simulation harness"`

---

### Step 10.4 — Run full historical simulation

**This is an execution step, not a code step.**

Configure power settings to prevent sleep. Then run:

```bash
python src/layer_7/simulation_harness.py \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --mode HISTORICAL
```

Monitor progress via checkpoint file:
```bash
cat data/_cache/simulation_checkpoint.json
```

Expected runtime: 24-48 hours unattended.
Expected API cost: approximately $162.

Consider running on a cloud VM (AWS t3.medium ~$2 for 48 hours)
to avoid laptop sleep interruptions. Download the output SQLite
database when complete.

**After completion — review:**
- [ ] Calibration report reviewed: Brier scores by catalyst type
      are plausible (Phase 3 oncology close to 40% base rate)
- [ ] Sharpe ratios by CCS band make intuitive sense:
      higher CCS bands should show higher Sharpe
- [ ] No parameter update flagged as manual_review_required
      for a change > 30% without clear reason
- [ ] LLM contamination flag: not yet applicable at this stage
      (no live data to compare against)
- [ ] Initial calibrated parameter set saved from calibration_report
- [ ] `git commit -m "Step 10.4 complete — historical simulation run"`

---

## Phase 11 — Integration and go-live

---

### Step 11.1 — End-to-end integration test

**Spec files to read:** All spec files — this test exercises full pipeline

**Prompt key points:**
- Use 5 controlled test tickers:
  RCKT (biotech, still listed), PRVB (biotech, delisted 2022),
  IMUX (biotech, active), MSFT (large-cap tech), VST (energy)
- Use 90-day date window January 1 to March 31 2022
- Run in SHADOW mode throughout
- Assert all constraint checks active (no relaxation for tests)

**Produces:**
tests/integration/test_full_pipeline.py


**Definition of done:**
- [ ] TWOS computed for all 5 tickers from 13F data
- [ ] Regime classified correctly for Q1 2022 (tightening environment)
- [ ] At least 1 document per ticker passes Layer 1 keyword filter
- [ ] Layer 2 produces valid JSON with probability_sum = 1.0 for each
- [ ] Layer 3 CCS computed and within 0-100 range
- [ ] Layer 4 produces at least 1 recommendation in 90-day window
- [ ] Module 12 creates THESIS_REVIEW action for each BUY
- [ ] PRVB (delisted): recorded correctly in outcome_tracker,
      not excluded from calculations
- [ ] No position exceeds $5,500 in any recommendation
- [ ] Cash reserve gate blocks BUY when portfolio_tracker shows < $3,000
- [ ] `pytest tests/integration/test_full_pipeline.py` passes
- [ ] `git commit -m "Step 11.1 complete — integration test"`

---

### Step 11.2 — Scheduler setup

**Spec files to read:** `spec/layer_0.md` section 0.3,
`spec/layer_minus1.md` sections −1.2 and −1.3

**Prompt key points:**
- Use APScheduler with SQLAlchemy job store for persistence across restarts
- Schedule all recurring tasks:
  Layer 1 document ingestion: every 15 minutes (continuous)
  Layer 0 regime classifier: daily 7am when instability active,
    Sunday 6pm when stable
  Layer -1 13F refresh: February 15, May 15, August 15, November 15
  Module 12 weekly review: every Sunday 6pm
  Module 12 monthly rescore: first Monday of each month 7am
  Module 12 keyword audit: first Monday of each month 7am
  Layer 6 calibration: 15 days after each 13F refresh date
  Layer 0 semi-annual recalibration: April 1, October 1
  Layer -1 annual recalibration: January 15
- Jobs persist across restarts via SQLAlchemy job store
- Scheduler logs all job executions with start time, end time, status

**Produces:**
src/scheduler.py
tests/test_scheduler.py


**Definition of done:**
- [ ] All recurring jobs scheduled with correct intervals
- [ ] Jobs persist across process restart — kill and restart, jobs still present
- [ ] Job execution logged to DB
- [ ] Layer 0 adaptive cadence: switches to daily when instability triggers
- [ ] `pytest tests/test_scheduler.py` passes
- [ ] `git commit -m "Step 11.2 complete — scheduler"`

---

### Step 11.3 — Shadow mode validation (30 days)

**This is an execution step, not a code step.**

Run in SHADOW mode for 30 days before committing real money.

```bash
python src/scheduler.py --mode SHADOW
```

Leave running continuously for 30 days.
Review the action calendar and alert output weekly.

**After 30 days — checklist:**
- [ ] At least 5 recommendations generated across 30 days
- [ ] All Module 12 actions created correctly for each recommendation
- [ ] SMS alerts received on phone for at least 1 triggered alert
- [ ] Daily digest emails arriving at 8am every day
- [ ] Weekly summary arrived each Sunday evening
- [ ] No crashes or unhandled exceptions in logs
- [ ] CCS scores updating as new documents arrive
- [ ] Action calendar iCal file imports correctly into your calendar app
- [ ] At least 1 position-level action completed and
      next occurrence auto-created (for recurring actions)
- [ ] Module 12 TODAY view shows correct overdue escalation
      for any action not completed within 3 business days
- [ ] `git commit -m "Step 11.3 complete — 30-day shadow validation"`

---

### Step 11.4 — Go live

Switch to LIVE mode. Execute your first real trade manually.

**Before switching — checklist:**
- [ ] Cash reserve of $3,750 available in brokerage account
- [ ] GLD or T-bill ETF position opened at $3,750 (static macro hedge)
- [ ] Current macro regime confirmed by reviewing Layer 0 output
- [ ] At least 1 recommendation from shadow mode reviewed and understood
- [ ] All alert delivery tested (SMS + email confirmed working)
- [ ] Module 12 action calendar imported into your calendar application
- [ ] Brokerage account configured with limit order capability
      for illiquid positions

```bash
python src/scheduler.py --mode LIVE
```

**Immediately after switching:**
- [ ] CONTAMINATION_CHECK action created in Module 12
      (due today + 90 days)
- [ ] First QUARTERLY_13F_REFRESH due date confirmed in Module 12
- [ ] First QUARTERLY_CALIBRATION_RUN scheduled 15 days after
      next 13F release
- [ ] `git commit -m "Step 11.4 — LIVE mode initiated $(date)"`

---

## File map — complete
stock_picker/
│
├── CLAUDE.md
├── README.md                        ← this file
│
├── spec/
│   ├── portfolio_specs.md
│   ├── layer_minus1.md
│   ├── layer_0.md
│   ├── layer_1.md
│   ├── layer_2.md
│   ├── layer_3.md
│   ├── layer_4.md
│   ├── layer_5.md
│   ├── layer_6.md
│   ├── layer_7.md
│   ├── module_10.md
│   ├── module_11.md
│   ├── module_12.md
│   └── pre_revenue_mode.md
│
├── prompts/
│   ├── prompt_01_database_schema.md
│   ├── prompt_02_institution_registry.md
│   ├── prompt_03_13f_parser.md
│   ├── prompt_04_form4_monitor.md
│   ├── prompt_05_discovery_pipeline.md
│   ├── prompt_06_macro_data_clients.md
│   ├── prompt_07_regime_classifier.md
│   ├── prompt_08_edgar_rss.md
│   ├── prompt_09_presswire_rss.md
│   ├── prompt_10_keyword_filter.md
│   ├── prompt_11_priors_and_validator.md
│   ├── prompt_12_llm_extractor.md
│   ├── prompt_13_deduplicator.md
│   ├── prompt_14_ccs_calculator.md
│   ├── prompt_15_bayesian_updater.md
│   ├── prompt_16_registry_manager.md
│   ├── prompt_17_portfolio_tracker.md
│   ├── prompt_18_position_sizer.md
│   ├── prompt_19_thesis_writer.md
│   ├── prompt_20_signal_generator.md
│   ├── prompt_21_alert_dispatcher.md
│   ├── prompt_22_action_calendar.md
│   ├── prompt_23_measurement_clocks.md
│   ├── prompt_24_outcome_tracker.md
│   ├── prompt_25_calibration_engine.md
│   ├── prompt_26_historical_data_loader.md
│   ├── prompt_27_checkpoint_manager.md
│   ├── prompt_28_simulation_harness.md
│   ├── prompt_29_integration_test.md
│   └── prompt_30_scheduler.md
│
├── src/
│   ├── database/
│   │   ├── schema.sql               Step 0.1
│   │   └── db.py                    Step 0.1
│   ├── layer_minus1/
│   │   ├── institution_registry.py  Step 1.1
│   │   ├── edgar_13f_parser.py      Step 1.2
│   │   ├── twos_calculator.py       Step 1.2
│   │   ├── form4_monitor.py         Step 1.3
│   │   ├── clinicaltrials_client.py Step 1.4
│   │   ├── edgar_new_issuer_screener.py Step 1.4
│   │   └── discovery_pipeline.py   Step 1.4
│   ├── layer_0/
│   │   ├── fred_client.py           Step 2.1
│   │   ├── vix_client.py            Step 2.1
│   │   └── regime_classifier.py    Step 2.2
│   ├── layer_1/
│   │   ├── edgar_rss_client.py      Step 3.1
│   │   ├── presswire_rss_client.py  Step 3.2
│   │   ├── keyword_filter.py        Step 3.3
│   │   └── document_queue.py        Step 3.3
│   ├── layer_2/
│   │   ├── probability_priors.py    Step 4.1
│   │   ├── schema_validator.py      Step 4.1
│   │   ├── extractor.py             Step 4.2
│   │   └── prompt_templates.py      Step 4.2
│   ├── layer_3/
│   │   ├── deduplicator.py          Step 5.1
│   │   ├── ccs_calculator.py        Step 5.2
│   │   ├── bayesian_updater.py      Step 5.3
│   │   └── registry.py              Step 5.4
│   ├── layer_4/
│   │   ├── position_sizer.py        Step 7.1
│   │   ├── thesis_writer.py         Step 7.2
│   │   └── signal_generator.py      Step 7.3
│   ├── layer_5/
│   │   ├── measurement_clocks.py    Step 9.1
│   │   ├── outcome_tracker.py       Step 9.2
│   │   └── brier_calculator.py      Step 9.2
│   ├── layer_6/
│   │   ├── calibration_engine.py    Step 9.3
│   │   └── calibration_report.py    Step 9.3
│   ├── layer_7/
│   │   ├── data_loader.py           Step 10.1
│   │   ├── point_in_time_enforcer.py Step 10.1
│   │   ├── checkpoint_manager.py    Step 10.2
│   │   └── simulation_harness.py    Step 10.3
│   ├── modules/
│   │   ├── portfolio_tracker.py     Step 6.1
│   │   ├── alert_dispatcher.py      Step 8.1
│   │   ├── sms_client.py            Step 8.1
│   │   ├── email_client.py          Step 8.1
│   │   ├── action_calendar.py       Step 8.2
│   │   └── ical_exporter.py         Step 8.2
│   └── scheduler.py                 Step 11.2
│
├── tests/
│   ├── test_schema.py               Step 0.1
│   ├── test_institution_registry.py Step 1.1
│   ├── test_13f_parser.py           Step 1.2
│   ├── test_twos_calculator.py      Step 1.2
│   ├── test_form4_monitor.py        Step 1.3
│   ├── test_discovery_pipeline.py   Step 1.4
│   ├── test_macro_data_clients.py   Step 2.1
│   ├── test_regime_classifier.py    Step 2.2
│   ├── test_edgar_rss_client.py     Step 3.1
│   ├── test_presswire_rss_client.py Step 3.2
│   ├── test_keyword_filter.py       Step 3.3
│   ├── test_priors_and_validator.py Step 4.1
│   ├── test_extractor.py            Step 4.2
│   ├── test_deduplicator.py         Step 5.1
│   ├── test_ccs_calculator.py       Step 5.2
│   ├── test_bayesian_updater.py     Step 5.3
│   ├── test_registry.py             Step 5.4
│   ├── test_portfolio_tracker.py    Step 6.1
│   ├── test_position_sizer.py       Step 7.1
│   ├── test_thesis_writer.py        Step 7.2
│   ├── test_signal_generator.py     Step 7.3
│   ├── test_alert_dispatcher.py     Step 8.1
│   ├── test_action_calendar.py      Step 8.2
│   ├── test_measurement_clocks.py   Step 9.1
│   ├── test_outcome_tracker.py      Step 9.2
│   ├── test_calibration_engine.py   Step 9.3
│   ├── test_data_loader.py          Step 10.1
│   ├── test_checkpoint_manager.py   Step 10.2
│   ├── test_simulation_harness.py   Step 10.3
│   ├── test_scheduler.py            Step 11.2
│   └── integration/
│       └── test_full_pipeline.py    Step 11.1
│
├── data/
│   ├── _cache/
│   │   └── simulation_checkpoint.json
│   ├── _outputs/
│   └── historical/
│
├── .env
├── .gitignore
└── requirements.txt


---

## Estimated timeline

| Phase | Steps | Working days |
|---|---|---|
| Prerequisites | Setup | 1 |
| Phase 0 — Database | 0.1 | 1 |
| Phase 1 — Universe | 1.1-1.4 | 4-5 |
| Phase 2 — Macro regime | 2.1-2.2 | 2 |
| Phase 3 — Ingestion | 3.1-3.3 | 3 |
| Phase 4 — LLM extraction | 4.1-4.2 | 3-4 |
| Phase 5 — Registry | 5.1-5.4 | 4-5 |
| Phase 6 — Portfolio tracking | 6.1 | 1-2 |
| Phase 7 — Signal generation | 7.1-7.3 | 3-4 |
| Phase 8 — Alerts and calendar | 8.1-8.2 | 3-4 |
| Phase 9 — Outcome and calibration | 9.1-9.3 | 3-4 |
| Phase 10 — Historical simulation | 10.1-10.4 | 3 coding + 48h unattended |
| Phase 11 — Integration and live | 11.1-11.4 | 3 coding + 30 calendar days shadow |
| **Total** | **30 steps** | **~35-40 working days** |

Working days = part-time, approximately 2-3 hours each.
The 48-hour simulation run and 30-day shadow period are
calendar time, not working time.

---

## Critical rules — never break these

1. `filing_date` only — never `period_of_report`
2. Survivorship-bias-free prices — Polygon.io with delisted tickers
3. HISTORICAL mode — Layer 2 extraction only, no probability adjustment
4. `probability_sum` must equal 1.0 (±0.001) before any DB write
5. Deduplication fingerprint check before every Bayesian update
6. Max position $5,500 — hard cap applied last in sizing chain
7. Min cash reserve $3,000 — gate blocks BUY if breached
8. Parameter updates blocked below minimum observation threshold
9. Changes > 30% set manual_review_required flag — never auto-apply
10. Commit to git after every step's definition of done is met
11. Read the spec file before writing the implementation prompt
12. Write tests alongside the code — never defer testing to later