# Layer −1 — Institutional Universe Construction

**Schedule:** Quarterly on 13F release dates + continuous
Form 4/13G/13D monitoring.

**Point-in-time rule:** All processing uses `filing_date`
field exclusively. Never use `period_of_report` date.
A Q1 13F (period ending March 31) filed May 14 is not
available until May 14 in any simulation or live run.

---

## −1.1 Tracked Institution Registry

34 institutions. Annually recalibrated per section −1.5.

### Tier 1A — Sector specialist, deep diligence (multiplier: 4.0×)

| Institution | CIK | Primary coverage |
|---|---|---|
| Baker Bros. Advisors LP | 0001263508 | Biotech — clinical stage, long hold through binary events |
| RA Capital Management LP | 0001346824 | Biotech/medtech — public/private crossover, early clinical |
| Perceptive Advisors LLC | 0001411579 | Clinical-stage biotech, oncology and rare disease |
| OrbiMed Advisors LLC | 0001060349 | Global healthcare — public and private, all stages |
| BVF Inc. | 0001099590 | Biotech — deep value, activist, long/short |
| RTW Investments LP | 0001701605 | Biotech/medtech — royalties, public and private crossover |
| Redmile Group LLC | 0001478454 | Biotech/healthcare — growth stage, long-term hold |
| Cormorant Asset Management LP | 0001622879 | Clinical-stage biotech — concentrated, high conviction |
| Boxer Capital LLC | 0001505512 | Small/microcap biotech |
| Sio Capital Management LLC | 0001595303 | Biotech — small/midcap, event-driven |
| ARCH Venture Management LLC | 0001274403 | Deep early-stage biotech — pre-IPO and early public |
| Samsara BioCapital LLC | 0001744967 | Clinical-stage biotech — science-first, concentrated |
| Sofinnova Partners SAS | 0001631134 | European biotech — early stage, life sciences |
| Deerfield Management LP | 0001273931 | Healthcare — equity and royalty structures |
| Goehring & Rozencwajg Associates | 0001665005 | Natural resources, commodity royalties |

### Tier 1B — High-conviction generalist superinvestor (multiplier: 3.5×)

| Institution | CIK | Primary coverage |
|---|---|---|
| Baupost Group LLC | 0001061219 | Deep value, cross-sector distress |
| Pershing Square Capital Mgmt | 0001336528 | Concentrated activist, cross-sector |
| Appaloosa Management LP | 0001004244 | Macro-aware, cross-sector |
| Third Point LLC | 0001040273 | Activist, cross-sector, corporate events |
| Berkshire Hathaway Inc | 0001067983 | Consumer staples, financials, energy, insurance |

### Tier 2A — Generalist deep-value, concentrated (multiplier: 2.5×)

| Institution | CIK |
|---|---|
| Greenlight Capital Inc | 0001079114 |
| Gotham Asset Management LLC | 0001530721 |
| Ariel Investments LLC | 0001048268 |
| Oakmark Funds | 0000763749 |

### Tier 2B — Sector specialist, broader mandate (multiplier: 2.0×)

| Institution | CIK |
|---|---|
| Coatue Management LLC | 0001336920 |
| Whale Rock Capital Management | 0001516523 |
| Horizon Kinetics LLC | 0001010470 |
| Orbis Investment Management | 0001056087 |

### Tier 3 — Quality institutional, active management (multiplier: 1.0×)

| Institution | CIK |
|---|---|
| Fidelity Management & Research | 0000315066 |
| T. Rowe Price Associates | 0001113169 |
| Wellington Management Group | 0001080351 |
| Royce & Associates LP | 0000085700 |

### Tier 4 — Large passive / index (multiplier: 0.0×)

| Institution | CIK |
|---|---|
| Vanguard Group Inc | 0000102909 |
| BlackRock Inc | 0001364742 |
| State Street Corporation | 0000093751 |

Zero signal value. Purchases are mechanically driven by
index composition — no investment conviction.
Tier 4 holdings are still parsed and stored.
Tier 4 institutions are still counted in institution_count
for crowding detection even though their TWOS contribution
is 0.0.

**Note on BlackRock Inc (CIK 0001364742):** BlackRock was
included in the original institution list but was not
present in the verified CIK list provided during
implementation. Verify this CIK on EDGAR before using.

---

## −1.2 Quarterly 13F Processing

### Step 1 — Position change classification

| Change type | Definition | Signal priority |
|---|---|---|
| New position | Not held last quarter | Highest |
| Significant increase | >15% QoQ (>10% for Tier 1A/1B) | High |
| Moderate increase | 5-15% QoQ | Medium |
| Flat / minor | <5% QoQ change | Low — no trigger |
| Decrease | >5% QoQ reduction | Negative signal |
| Exit | Held last quarter, gone this quarter | Negative — exit alert |

### Step 2 — Tier-Weighted Ownership Score (TWOS) per ticker
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

**Shares outstanding source priority:**
1. Sum of all institution_holdings shares for this ticker
   (approximation from 13F data)
2. yfinance `Ticker(ticker).info["sharesOutstanding"]`
   as fallback — cache result in parameters table
3. If neither available: log WARNING, use market_value
   as proxy denominator, do not crash

### Step 3 — Processing tier assignment

**Calibrated thresholds (updated April 2026 after empirical
testing on 34-institution universe):**

*Active monitoring* — full Layer 0-4 processing, press wire
tracked daily. Target 150-250 tickers.

Criteria (any one sufficient):
- TWOS >= 3.0
- OR any Tier 1A/1B institution initiated new position
  this quarter
- OR any Tier 1A/1B institution increased >10% QoQ
- OR any institution increased >15% QoQ AND TWOS >= 0.3

*Passive monitoring* — SEC EDGAR only, no press wire.
Target 300-500 tickers.

Criteria: TWOS >= 0.5 AND < 3.0 AND no active_monitoring
condition met.

*Watchlist only* — quarterly 13F check, no ongoing
processing.

Criteria: TWOS > 0 AND < 0.5, OR single Tier 3 institution
with flat or decreasing position.

**Empirical validation (April 2026, 34 institutions,
3 quarters of data):**

| TWOS threshold | Active tickers |
|---|---|
| 0.5 | 2,654 |
| 1.0 | 1,858 |
| 2.0 | 678 |
| 3.0 | 243 ← selected |
| 4.0 | 92 |
| 5.0 | 48 |

Threshold of 3.0 selected as it produces 243 active tickers,
within the 150-250 target range. To be recalibrated annually
as the institution list and market conditions evolve.

**Crowding penalty:**
If >4 tracked institutions hold a name AND appreciation >50%
since earliest tracked institution initiated position →
apply 0.6× to TWOS. Set `crowding_flag: true` in Layer 3
registry.

Appreciation check: compare current price to price on
earliest `filing_date` where this ticker appears in
`institution_holdings`. Use yfinance for price data.
If price data unavailable: skip crowding check, log WARNING.

### EDGAR filing retrieval

Use EDGAR submissions API per institution:
https://data.sec.gov/submissions/CIK{cik_10_digits}.json

Rate limit: 0.11 second sleep between every EDGAR request.
User-Agent header required on every request:
StockPicker contact@stockpicker.local

For each 13F-HR filing: download information table XML,
parse `<infoTable>` elements. Only process holdings where
`<sshPrnamtType>` == "SH" (shares). Skip PRN (bond
principal) holdings.

### CUSIP to ticker resolution

Primary: OpenFIGI API `https://api.openfigi.com/v3/mapping`
Batch up to 10 CUSIPs per request.
Free tier: 25 requests/minute, 250/day.
With API key (recommended): 250 requests/minute.

Filter for US equity exchanges:
exchCode in: US, UN, UA, UW, UR

Cache all resolutions in `cusip_ticker_map` table.
Unresolved CUSIPs stored as NULL — do not crash.

**Empirical CUSIP resolution rates (April 2026):**

Overall rate: ~69% across 34 institutions.
Low resolution funds (45-58%) hold primarily:
- Private placement warrants (PIPE deals)
- Convertible notes
- Foreign-listed biotechs (Israeli, European, Canadian)
- Pre-IPO instruments

These unresolved instruments are not actionable retail
portfolio positions. The resolved 69% represents the
relevant publicly-traded US equity universe.
No further improvement required at this stage.

### Filing-level deduplication

Tracked in `filings_log` table by `accession_number`.
Before processing any filing: check if
`institution_id + accession_number` already in `filings_log`.
If yes: skip entirely — do not re-download.

Parse status values:
- `success`: holdings_count > 0, stored successfully
- `empty`: XML parsed but zero SH holdings found
- `parse_error`: XML parsing raised exception
- `download_error`: HTTP request failed

Only `download_error` filings are retried on re-run.
All other statuses are skipped permanently.

---

## −1.3 Continuous Form 4 / 13G/13D Processing

**Form 4 (transaction code "P" only):**
Any "P" transaction → immediate elevation to active
monitoring + `INSIDER_PURCHASE_ALERT` in Layer 4 +
`INSIDER_PURCHASE_MONITOR` action in Module 12.

All other transaction codes (S, A, M, F, G, etc.) are
rejected immediately. Do not download full Form 4 XML
before confirming ticker is in active monitoring universe.

**13D/13G:**
- SC 13D (activist >5%): immediate active monitoring +
  highest-priority SMS alert
- SC 13G (passive >5%): active monitoring elevation only
  if filer CIK matches tracked institution with tier
  1A, 1B, or 2A
- SC 13G/A amendment: process only if ownership change
  >= 1.0 percentage point

**Insider signal source tiering:**

| Source | Tier | Bull probability adjustment |
|---|---|---|
| C-suite open market purchase >$500K | 1 | +8% |
| SC 13D activist stake initiation | 1 | +8% |
| Director open market purchase >$100K | 2 | +5% |
| Tier 1A/1B 13F new position | 2 | +5% |
| Tier 2A/2B 13F new position | 3 | +3% |
| Standard institutional 13F increase >15% QoQ | 4 | +1% |
| Index fund rebalance (Tier 4) | 5 | 0% |

**C-suite definition for Tier 1 classification:**
officerTitle contains: CEO, CFO, COO, CTO, President, CMO

**Alert priorities:**
- Source tier 1 or 2: SMS_immediate
- Source tier 3 or 4: daily_digest

**EDGAR RSS feeds polled every 15 minutes in live mode:**
Form 4:
https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent
&type=4&dateb=&owner=include&count=40&output=atom
SC 13D:
https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent
&type=SC+13D&dateb=&owner=include&count=40&output=atom
SC 13G:
https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent
&type=SC+13G&dateb=&owner=include&count=40&output=atom

**CIK to ticker resolution:**
Form 4 and 13D/13G filings identify companies by CIK.
Resolution via EDGAR submissions API:
https://data.sec.gov/submissions/CIK{cik_10_digits}.json
Cache in `cik_ticker_map` table. NULL stored on failure.

**Deduplication:**
Check `filing_url` against `form4_signals` and
`thirteendg_signals` tables before processing.
Same filing_url processed twice → second call skipped.

---

## −1.4 Supplementary Discovery Pipeline

**Source 1 — ClinicalTrials.gov:**
All biotech/pharma with IND filing or trial registration
in last 24 months, regardless of institutional ownership.
Passive monitoring intensity.
Auto-promoted to active monitoring when Tier 1A/1B 13F
position first appears.

**Source 2 — EDGAR new issuer screening:**
All companies filing first 10-K or S-1 in last 12 months
within biotech, medtech, specialty pharma SIC codes.
Watchlist intensity only.

**Source 3 — Press wire orphan detection:**
Any company not in current universe generating Tier 1 or
Tier 2 keyword hit receives one-time Layer 2 extraction.
If extraction produces `transformative` or `significant`
magnitude_class → enters passive monitoring pending
next 13F cycle.

---

## −1.5 Annual Institution Recalibration
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
TWOS_threshold_recalibration:
cadence: "annual"
method:
"run diagnose_twos2.py diagnostic
select threshold producing 150-250 active tickers
update assign_processing_tier() in twos_calculator.py
update this spec file"
current_threshold: 3.0
calibrated: "April 2026"
calendar_action_created:
"ANNUAL_INSTITUTION_RECALIBRATION action in Module 12
due every January 15
reminder 30 days and 14 days prior"

---

## −1.6 Feedback Loop Parameters — Layer −1

| Parameter | Feedback value | Compute cost | Cadence |
|---|---|---|---|
| Institution count (currently 34) | Moderate | Low | Annual |
| Tier multipliers (4.0/3.5/2.5/2.0/1.0/0.0) | High | Zero | Quarterly |
| Change momentum factors (2.0/1.5/1.2/1.0/0.7/0.0) | Moderate-high | Zero | Quarterly |
| Significant increase threshold (15%/10%) | Low-moderate | Low | Annual |
| Crowding penalty threshold (>4 institutions, >50%) | Moderate | Zero | Annual |
| TWOS active monitoring threshold (currently 3.0) | High | Zero | Annual |

MULTIPLIER_CALIBRATION_METHOD {
fit_regression:
dependent:            "position_return_at_horizon"
independent:          "tier_multiplier × change_momentum_factor"
regularisation:       "L2 (Ridge)"
max_parameters:       10
minimum_observations: 30 per tier
output:               "updated multiplier values"
}

---

## −1.7 Database Tables

Tables created and managed by Layer −1 implementation:

| Table | Purpose |
|---|---|
| `institutions` | Static institution registry with CIKs and tier multipliers |
| `institution_holdings` | All 13F holdings per institution per filing date |
| `twos_scores` | TWOS scores per ticker per run date with processing tier |
| `cusip_ticker_map` | CUSIP to ticker resolution cache |
| `cik_ticker_map` | Company CIK to ticker resolution cache |
| `filings_log` | Filing-level deduplication and parse status tracking |
| `form4_signals` | Detected Form 4 open market purchase signals |
| `thirteendg_signals` | Detected SC 13D and SC 13G signals |

**Schema versioning:** All table creation managed via
migration system in `src/database/db.py`.
Current migrations covering Layer −1:
- Version 1: initial schema
- Version 2: 13F ingestion tables
  (institution_holdings, twos_scores, cusip_ticker_map,
  cik_ticker_map)
- Version 3: filings_log
- Version 4: form4_signals, thirteendg_signals
  (added in Step 1.3)

---

## −1.8 Implementation Files
src/layer_minus1/
institution_registry.py   Step 1.1 — static institution data
edgar_13f_parser.py       Step 1.2 — EDGAR filing retrieval
and XML parsing
cusip_resolver.py         Step 1.2 — CUSIP to ticker resolution
holdings_store.py         Step 1.2 — point-in-time holdings queries
twos_calculator.py        Step 1.2 — TWOS formula and tier assignment
form4_monitor.py          Step 1.3 — Form 4 / 13G/13D monitoring
clinicaltrials_client.py  Step 1.4 — ClinicalTrials.gov discovery
edgar_new_issuer_screener.py Step 1.4 — new issuer screening
discovery_pipeline.py     Step 1.4 — supplementary discovery

---

## −1.9 Operational Notes

**Running quarterly updates:**

```bash
# Step 1: ingest new 13F filings from EDGAR
python -c "
from src.database.db import get_connection
from src.layer_minus1.edgar_13f_parser import ingest_all_institutions
from datetime import date
conn = get_connection()
summary = ingest_all_institutions(
    conn=conn,
    from_date='2025-01-01',
    to_date=date.today().isoformat()
)
conn.close()
"

# Step 2: recompute TWOS scores
python -c "
from src.database.db import get_connection
from src.layer_minus1.twos_calculator import run_quarterly_update
from datetime import date
conn = get_connection()
run_quarterly_update(run_date=date.today().isoformat(), conn=conn)
conn.close()
"
```

**Diagnosing active universe size:**
```bash
python diagnose_twos2.py
```
Target: 150-250 active tickers.
If outside range: adjust TWOS threshold in
`assign_processing_tier()` and update −1.6 table above.

**Diagnosing CUSIP resolution:**
```bash
python diagnose_cusip.py
```
Expected overall rate: ~69% given current institution mix.
Rates below 50% for Tier 1A biotech funds are normal due
to private placement and foreign-listed positions.

**Known CIK corrections applied:**
- ARCH Venture Partners: original CIK 0000882603 was wrong
  (404 on EDGAR). Correct CIK is 0001274403 for
  ARCH Venture Management LLC.
- ARCH last filed 13F in 2022 — expect zero holdings in
  2025 quarterly runs. Not an error.

  