# Early-Detection Pipeline for Disruptive Biotech Mechanisms — Build Spec v1

Markets: US, Canada, Europe (EU+UK+CH), Nordic, Japan, Korea
Purpose: surface small/micro-cap biotech-pharma-life-sciences names carrying independently-corroborated but still institutionally under-recognized mechanisms, before a catalyst rerates the stock. Feeds into, does not replace, the existing stack-convergence-biotech-screen-spec.md scoring framework.

Note: the "company-disclosed target predates peer-reviewed validation" signal is dropped per your instruction — treated as a Satellos-specific artifact, not a generalizable feature.

---

## 1. System overview

```
[UNIVERSE MODULE] ──► [SIGNAL INGESTION MODULES] ──► [DATA STORE] ──► [CLAUDE SCORING LAYER] ──► [DIGEST/ALERTS]
                                                            ▲
                                     [EXISTING USER UNIVERSE] ──merge/dedupe──┘
```

Four independently buildable phases (see §8 roadmap). Each module is a scheduled job writing to a shared Postgres/SQLite store, keyed on a canonical `entity_id` (see §3).

---

## 2. Universe construction module

### 2.1 Purpose
Build and maintain a deduplicated master list of biotech/pharma/life-sciences-tools companies across all six markets, each tagged with every identifier needed to join against literature, patent, trial, and filing data.

### 2.2 Per-market source

| Market | Primary source | Format/access | Sector filter | Refresh |
|---|---|---|---|---|
| US | SEC EDGAR `company_tickers.json` + EDGAR full-text search by SIC | Free JSON/REST | SIC 2834 (pharma prep), 2836 (biological products), 8731 (commercial physical/biological research), 3826/3841 (lab instruments/devices — for tools cos) | Weekly |
| US (supplementary) | Nasdaq Trader symbol directory (`nasdaqtrader.com/dynamic/SymDir`) | Free flat file | Cross-reference for exchange/listing status | Weekly |
| Canada | SEDAR+ issuer list; TSX/TSXV company directory | Scrape (no clean public API) — budget for this as a build cost | GICS Pharmaceuticals/Biotechnology/Life Sciences Tools | Weekly |
| Canada (supplementary) | EDGAR foreign-private-issuer filter (40-F/20-F/6-K filers with Canadian address) | Free API | Catches dual-listed Canadian names automatically | Weekly |
| Europe (EU) | ESMA FIRDS (Financial Instruments Reference Data System) | Free, bulk XML, CFI-code classification | CFI codes for equity + ICB/GICS sector tag where available | Weekly (FIRDS publishes daily deltas) |
| Europe (UK) | LSE issuer list / Companies House sector codes | Free (LSE list), Companies House API (free, SIC-based) | ICB 2010/3020 Pharmaceuticals & Biotechnology | Weekly |
| Europe (CH) | SIX Swiss Exchange listed companies | Public list, scrape | ICB Pharma/Biotech | Monthly |
| Nordic | Nasdaq Nordic + Nasdaq First North instrument lists (Stockholm, Copenhagen, Helsinki, Iceland) | Free downloadable instrument files | ICB Pharma/Biotech — First North is disproportionately important here (Swedish biotech is heavily small-cap growth-market listed) | Weekly |
| Japan | JPX (Tokyo Stock Exchange) listed company list | Free downloadable Excel/CSV | JPX 33-sector code "Pharmaceutical"; supplement with TSE Growth Market for micro-caps | Weekly |
| Korea | KRX/KIND (Korea Investor's Network for Disclosure) | Public list, scrape or KRX data files | KOSDAQ bio/pharma sector — KOSDAQ carries the disproportionate share of Korean biotech, prioritize over KOSPI | Weekly |

### 2.3 Canonical entity schema

```
Company {
  entity_id            -- internal UUID, primary key
  legal_name
  common_name
  ticker_primary
  ticker_secondary[]    -- dual/cross listings
  exchange_primary
  exchange_secondary[]
  isin
  lei                  -- Legal Entity Identifier, if available (GLEIF API, free, global coverage — use as cross-market join key)
  cik                  -- SEC, if applicable
  jurisdiction
  filer_type           -- domestic / FPI / foreign-issuer-equivalent per market
  sector_code_raw       -- source-native code (SIC / ICB / JPX-33 / KRX)
  sector_code_normalized -- mapped to a single internal taxonomy: {therapeutics, diagnostics, tools/platform, devices, agbio, other}
  founder_scientists[]  -- populated by Claude extraction, see §5.1
  academic_affiliations[]
  market_cap_usd_equiv
  last_updated
}
```

**LEI-first join strategy**: GLEIF's free LEI API is the cleanest cross-market entity key (covers US/CA/EU/UK/CH/Nordic well; Japan/Korea coverage weaker but improving). Where LEI is missing, fall back to ISIN, then ticker+exchange composite key. Build a reconciliation job that flags unmatched entities for manual review rather than silently deduping incorrectly — false merges are worse than duplicate rows here.

### 2.4 Integration with your existing universe pipeline
- Export your pre-existing universe as a flat file (ticker, exchange, name minimum) and run it through the same LEI/ISIN reconciliation step on first load.
- Your existing universe becomes a **priority tier** — every signal module tags matches against it with `in_existing_universe: true/false`, and the digest ranks `true` matches (mechanism-expansion within a name you already cover) above `false` matches (cold discovery) by default, since the former is cheaper to act on and lower false-positive risk, per §9.
- Two-way sync: any new entity discovered by this pipeline that clears a scoring threshold gets exported back out in a format your existing pipeline can ingest, not just consumed one-way.

### 2.5 Known gap
Canada (SEDAR+/SEDI) and Korea (KRX/KIND) have no clean free API — both require scraping with respectful rate limits, or a paid data vendor (e.g., Refinitiv, FactSet, or a regional aggregator) if scrape reliability becomes a maintenance burden. Budget this explicitly rather than discovering it mid-build.

---

## 3. Signal ingestion modules

Each module below is a scheduled job. All write into a shared `signals` table:
```
Signal {
  signal_id
  entity_id            -- FK to Company, nullable if not yet matched
  signal_type           -- enum, see below
  source
  raw_payload           -- JSON
  detected_at
  event_date            -- date of the underlying event, not detection date
  language               -- ISO code, for JP/KR/EU-non-English content
}
```

### 3.1 Literature & citation-network signal
- **Sources**: PubMed E-utilities (free, 3 req/s unauthenticated / 10 req/s with free API key), Europe PMC REST API (better full-text + citation graph than PubMed alone, also indexes more non-US journals — useful for EU/Nordic/Japan academic output), OpenAlex API (free, best citation-network + institution-affiliation data at scale; also has good non-English-journal coverage relevant to Japan/Korea).
- **Query construction**: per-company founder-scientist name + institution, refreshed quarterly as `founder_scientists[]` is populated/updated by the Claude extraction job (§5.1).
- **What's captured**: new publications by founder-scientists; new publications *citing* a founder-scientist's foundational paper, tagged by citing-author institution for independence classification (§5.2).
- **Non-English handling**: Japanese/Korean-language academic output that doesn't reach PubMed/Europe PMC (rare for peer-reviewed science, which is overwhelmingly English-first even from JP/KR labs, but J-STAGE (Japan) and KISTI/RISS (Korea) exist as supplementary national repositories if coverage gaps appear in practice).

### 3.2 Clinical trial registry signal
- **Sources**: ClinicalTrials.gov API v2 (free, confirmed current, well-structured) for US/global-registered trials; **EU Clinical Trials Register (CTIS)** has a public data API for EU-region trials not otherwise dual-registered; **JRCT** (Japan Registry of Clinical Trials) — no clean API, scrape; **CRIS** (Clinical Research Information Service, Korea) — no clean API, scrape.
- **Mechanism**: daily pull, diff against prior day's snapshot per NCT-ID/registry-ID. Flag: new registrations from watchlist sponsors, enrollment-count changes, primary-completion-date changes, new arms/cohorts added.
- **Sponsor-to-entity matching**: sponsor name string matching against Company table is noisy (subsidiaries, CRO-of-record vs. actual sponsor) — this is a good Claude extraction task (§5.1) rather than pure string matching.

### 3.3 Patent signal
- **Sources**: USPTO PatentsView API (free, US filings/grants), EPO Open Patent Services (OPS, free tier available, covers European filings including EPO-validated national filings), J-PlatPat (Japan Platform for Patent Information — public search, no clean bulk API, scrape or use Google Patents Public Datasets on BigQuery as an aggregated free alternative covering US/EP/JP/KR/WO patent families in one schema).
- **Google Patents BigQuery public dataset** is likely the single best free multi-jurisdiction source here — one schema covering US, EP, JP, KR, WO filings, updateable via scheduled BigQuery queries rather than five separate scrapers. Recommend this as the primary patent source, with USPTO/EPO direct APIs as supplementary for filing-day-fresh data (BigQuery dataset has ingestion lag, typically weeks).
- **What's captured**: new academic-institution-assigned patent families later exclusively licensed to a small-cap (license assignment recorded in USPTO assignment database, free, searchable); new composition-of-matter filings from watchlist institutions.

### 3.4 Regulatory designation signal
- **Sources**: FDA Orphan Drug/Fast Track/Breakthrough/RMAT — no real-time API; FDA publishes a periodically-updated Orphan Drug Designation database (downloadable). Real-time designation news arrives via company PR, not a registry feed — treat as a news-feed problem (§3.6), not a structured-data problem. EMA (Europe) PRIME designation list — published, downloadable, periodic. PMDA (Japan) Sakigake designation — published list, periodic. MFDS (Korea) — periodic published list, less consistently available in English.
- **Mechanism**: periodic full-refresh diff (weekly) against each designation database, joined to Company table by name matching (Claude-assisted, §5.1).

### 3.5 Capital markets / ownership signal
- **Sources**: SEC EDGAR full-text search API (`efts.sec.gov`, free) for 13G/13D/13F/Form 4/8-K/6-K full-text keyword and entity search — this is your highest-value, most reliable structured source and covers US-listed and US-cross-listed FPI names cleanly. UK: Companies House + LSE RNS feed (free, regulatory news service, real-time). EU: national competent authority filing systems vary by country — no single EU-wide equivalent to EDGAR full-text search exists; treat as lower-priority/manual for EU-only-listed (non-UK, non-dual-US) names initially. Nordic: Nasdaq Nordic company news/RNS-equivalent feeds, free. Japan: TDnet (Tokyo Disclosure Network) — public disclosure feed, scrape. Korea: DART (Data Analysis, Retrieval and Transfer System, Korea's EDGAR-equivalent) — has a public API, free with registration, good coverage.
- **What's captured**: new 5%+ ownership crossings by specialist healthcare funds (maintain a named-fund watchlist — Baker Brothers, Lynx1, BVF, OrbiMed, RA Capital, Cormorant, Perceptive, EcoR1, Qiming, Sofinnova, Forbion, Andera, HealthCap (Nordic), Athos, SR One, plus JP/KR-specific specialist funds to be identified during build), insider Form-4/DART-equivalent clusters, shelf/ATM filings, designation-related 8-K/6-K text.

### 3.6 News/PR & conference signal
- **Sources**: GlobeNewswire, BioSpace, Fierce Biotech, PR Newswire — all have free RSS feeds, no scraping needed for headline/summary level; full-article fetch via `web_fetch`-equivalent when a headline matches a watchlist entity or keyword. Conference abstracts (AACR, ASCO, ASH, AAN, ICNMD, ESMO, and JP/KR-region equivalents) — no unified API, seasonal scrape jobs targeted at each portal's abstract-release window (typically 60-90 days pre-conference).
- **Priority**: this module is the lowest structured-data-maturity, highest manual-build-cost component. Sequence it last (see roadmap).

---

## 4. Data store

- Postgres (not SQLite, given multi-market/multi-language volume and the need for concurrent scheduled jobs writing to shared tables).
- Core tables: `Company`, `Signal`, `Publication`, `Trial`, `Patent`, `Filing`, `ConferenceAbstract`, `Score` (Claude scoring output, §5), `WatchlistFund`, `WatchlistInstitution`.
- Full-text search index (Postgres `tsvector` or an attached OpenSearch instance if volume warrants) across `raw_payload` fields for ad hoc querying outside the automated pipeline.

---

## 5. Claude API scoring layer

All calls use forced structured (JSON schema) output, and a cached system prompt carrying your standing sensitivity-discipline and tagging rules (`[V]`/`[INF]`, TCRX rule, base-rate discipline) so it doesn't need to be re-sent per call — meaningful cost control at daily-batch volume.

### 5.1 Extraction jobs (run per new raw signal)
- **Founder/lineage extraction**: input = paper author list + affiliations, or filing officer/director list; output = structured `{name, role, institution, is_company_officer: bool, company_entity_id}`. Run against every new Company record once, refreshed quarterly.
- **Sponsor-to-entity resolution**: input = trial registry sponsor string; output = resolved `entity_id` or `null` + confidence score.
- **Designation/PR entity+topic extraction**: input = news/PR text; output = `{entity_id, designation_type, target/mechanism, date}`.

### 5.2 Independence classification (batch job, weekly)
- Input: citation list for each tracked foundational paper, pulled from OpenAlex.
- Output per citing paper: `{relationship: self-citation | same-institution | independent-lab | industry-authored, citing_institution, citing_lab_pi}`.
- Aggregate to an `independence_score` per mechanism = count of genuinely independent-lab citations, decayed by recency.

### 5.3 Novelty/precedent check (run per new target/mechanism identified)
- Input: target name + indication.
- Output: `{approved_precedent_exists: bool, closest_precedent, precedent_stage_reached}` — flags genuine first-in-class candidates. Use Claude with web search tool enabled for this call specifically (not a batch/cached call) since it needs current information, not just the ingested corpus.

### 5.4 Sensitivity-discipline scoring (run per candidate that clears a minimum evidence threshold — this is the expensive, high-value call, not run on every raw signal)
- Input: full assembled evidence packet for a candidate entity (literature independence score, trial status/n-size where disclosed, patent status, ownership/designation signals, existing-universe flag).
- Output, forced schema, mirroring the manual analysis pattern from this session:
```json
{
  "entity_id": "...",
  "mechanism_summary": "...",
  "independent_validation_status": "none | single-institution-follow-on | multi-lab-independent",
  "disclosed_data_n_size": "...",
  "model_to_trial_population_match": "match | mismatch | unknown",
  "base_rate_context": "...",
  "stack_convergence_dimension_fit": "...",
  "conviction_flag": "surveil | deep-dive-candidate | deprioritize",
  "confidence_caveats": ["..."]
}
```
- This schema is where you plug in the stack-convergence-spec dimensions directly, so output routes into your existing framework rather than creating a second scoring system.

### 5.5 Cost control
- Prompt-cache the system/discipline prompt (static, reused across all §5.2-5.4 calls).
- Run cheap extraction jobs (§5.1) on a smaller/faster model tier; reserve the full model for §5.4 scoring, which only runs on candidates that already cleared a rules-based pre-filter (e.g., "has ≥1 independent citation AND ≥1 capital-markets signal in the trailing 12 months AND not already fully covered in your existing deep-dive archive").
- Rough volume estimate to size a budget: universe of ~2,000-4,000 tracked entities across six markets → daily raw signal volume in the low hundreds after dedup → §5.4 full scoring calls realistically in the 10-30/week range once the pre-filter is tuned. This is a cost-manageable volume; the literature/citation ingestion volume (§3.1, §5.2) is the larger token driver and should stay on cheaper batch processing.

---

## 6. Orchestration
- Claude Code (or GitHub Actions/cron) running scheduled jobs per module cadence in §2-3.
- Each job idempotent, writes to Postgres, logs failures per-source (a J-PlatPat scrape failure shouldn't block the EDGAR pull).
- Weekly full-pipeline run producing the digest; daily lightweight run for capital-markets/news signals only (these are the most time-sensitive).

## 7. Output
- Weekly Claude-generated digest: ranked candidate list, `in_existing_universe` tier first, each with the §5.4 structured evidence packet attached — not free prose, so you can scan/sort before deciding what gets a full manual deep-dive.
- Ad hoc query interface: since everything lands in Postgres with full-text search, you (or I, via bash/SQL in a follow-up session) can query the store directly outside the scheduled digest — e.g., "show me every independent-lab citation event in the last 90 days for Nordic-listed names."

---

## 8. Build roadmap (suggested phase order, by ROI-to-build-cost ratio)

| Phase | Scope | Rationale |
|---|---|---|
| 1 | Universe module: US + Canada (EDGAR-based, cleanest APIs) + your existing-universe merge | Highest-quality free data, immediate integration value, validates the entity-schema/LEI-reconciliation approach before extending to messier markets |
| 2 | Signal modules: literature (§3.1) + capital markets (§3.5, EDGAR full-text) + Claude extraction/independence scoring (§5.1-5.2) | These are the modules that most directly replicate what you had me do manually this session, and are the best-instrumented (free, clean APIs) |
| 3 | Add trial registry (§3.2) + patent (§3.3, Google Patents BigQuery as primary) | Structured, free, moderate build cost |
| 4 | Extend universe + signals to Europe/UK/Nordic (FIRDS, LSE, Nasdaq Nordic — reasonably clean data) | Nordic in particular is high-value for your thesis (First North is genuinely biotech-dense and under-covered by US-centric screens) |
| 5 | Japan + Korea universe/signals (JPX/KRX, TDnet/DART) + regulatory designation module (§3.4) | Higher build cost (language, scraping, less-standardized APIs), sequence last; DART (Korea) is workable via free API, JP requires more scraping |
| 6 | Conference/abstract module (§3.6) | Highest manual-build-cost, lowest automation ceiling — treat as an ongoing manual/semi-automated add rather than a clean one-time build |

---

## 9. Limitations to carry forward (per standing discipline)
- **Coverage is structurally uneven across markets**: US/Canada/UK will be well-instrumented on free APIs; Korea is workable (DART); Japan and continental-EU-only names will have the weakest structured coverage and highest false-negative risk — don't treat a "no signal" result from those markets as meaningfully informative until the scrape-based modules are validated.
- **Survivorship/seed bias remains**: this pipeline is structurally better at finding "another Satellos" (founder-lab lineage, independent-citation pattern) than a genuinely novel first-time-founder breakthrough with no academic pedigree trail — flagged in the prior discussion, still true here, no architectural fix proposed.
- **The independence-classification and novelty-check Claude calls are themselves unverified against a labeled dataset** — before trusting §5.2/§5.3 output at face value, worth back-testing against 10-15 known cases (Satellos included) where you already know the right answer, to calibrate before relying on it for new/unknown candidates.
