Overall Specification
Purpose
A modular Python pipeline that scans the quarterly holdings of 21 hand-picked biotech/healthcare specialist funds, identifies stocks where "the train has not already left" (i.e., price trajectory suggests untapped upside), and uses the Anthropic Claude API to rank the most promising candidates by estimated rate of price appreciation.
The pipeline is deterministic end-to-end except for Module 6 (LLM scoring), where cost is controlled via batch processing and prompt caching.
Pipeline flow
2_Funds_parser -> project started in previous conversation. Read .claude/2_Funds_parser_handoff.md
run_2_Funds_parser.bat ─────────► existing .bat file. Shall be updated when new scripts are created.
                                │
                                ▼
Module 1  ─── Configuration & foundation
Module 2  ─── 13F extraction (EXISTING — already built)
Module 3  ─── Bridge: prepare data for Module 4
Module 4  ─── "Train hasn't left" filter & archetype ranking
Module 5  ─── Market data enrichment (pre-LLM)
Module 6  ─── LLM scoring via Claude API (batch + cached)
Module 6b ─── Post-scoring refinement + user-driven selective dispatch
Module 7  ─── Outcome tracking & feedback loop
Each module is independently runnable, testable, and cached where appropriate.
________________________________________
Module 1 — Configuration & Foundation
Role: Non-runtime. Establishes the shared foundation every other module depends on.
Owns:
•	Master pipeline config (config/pipeline.yaml) — paths, cache TTLs, API keys, debug/production mode, DB schema mapping
•	Directory structure
•	Shared logging setup
•	Validation of all YAML configs at load time (fail fast with line numbers on error)
•	Environment variables via .env (API keys). .env is in the root folder (not in 2_Funds_parser folder)
•	Debug vs. production mode conventions (row subsetting, cache clearing, log verbosity)
Outputs: state, not files. Validated config objects accessible to downstream modules.
Key principle: Misconfiguration here silently corrupts every downstream module, so explicit schemas and upfront validation are non-negotiable.
________________________________________
Module 2 — 13F Extraction (Existing)
Role: Already built. Not re-spec'd.
Layer 0 output (inside 2_Funds_parser/2_fundparser.db):
•	funds table — 21 rows, one per selected biotech fund. Populated from Input/list_of_funds.xlsx via scripts/2_seed_funds.py.
Layer 1 output (same 2_Funds_parser/2_fundparser.db, appended by scripts/2_ingest_13f.py):
•	filings_log — currently 502 rows. Every 13F-HR filing downloaded from SEC EDGAR for those 21 funds. All 21/21 funds have at least one filing. Date range: 2020-01-28 → 2026-02-17.
•	holdings — currently 77,763 rows. One position per (fund, filing_date, CUSIP).
•	cusip_ticker_map — currently 9,865 rows. Side cache of CUSIP → ticker resolutions (OpenFIGI + SEC name match); not fund-specific but populated as a side-effect of ingesting these 21 funds.
A single file (2_fundparser.db) is the canonical persisted output of the Layer 0 + Layer 1 pipeline, and it currently contains the SEC 13F-HR holdings data for all 21 registered funds.
The output of Layer 0 → Layer 1 is the single file 2_Funds_parser/2_fundparser.db (SQLite).
________________________________________
Module 3 — Data Bridge
Role: Read Module 2's output and transform it into the shape Module 4 expects.
Why this module exists: Module 2 produces raw per-filing data; Module 4 needs an aggregated, quarter-scoped, ticker-keyed universe with fund accumulation metrics attached. Module 3 bridges these representations.
What it does:
1.	Read from filings.db (schema mapping from Module 1 config). Filter to 21 tracked funds.
2.	Select target quarter (CLI override, config default, or auto-latest).
3.	Apply share-type aggregation rules per (fund, ticker) position: 
o	Sum common stock + pre-funded warrants into economic share count.
o	Drop option positions (puts/calls) — tracked in a separate audit file; has_options flag preserved on the surviving row.
o	Drop debt instruments and unrecognized share classes (logged, non-fatal).
4.	Aggregate across funds per ticker, producing per-ticker metrics: 
o	fund_count — how many of the 21 funds hold it this quarter
o	qoq_share_change — net share change vs. prior quarter
o	qoq_fund_count_change — net fund count change
o	new_positions — funds that opened a position this quarter
o	increased_positions — funds that added to existing positions
5.	Propagate ticker-resolution confidence (ticker_is_verified boolean from Module 2's OpenFIGI vs. SEC-name-match tiering) so Module 4 and Module 6 can use it.
6.	Emit a single Parquet file — the unified universe Module 4 consumes.
Output: _intermediate_outputs/universe_{quarter}.parquet — one row per ticker, with fund accumulation metrics and metadata. Plus audit files: dropped_rows_{quarter}.parquet, unresolved_positions_{quarter}.parquet.
Key principle: This module does not fetch market data (no yfinance, no price history). That comes later. Module 3's job is purely to reshape the fund-positions picture.
________________________________________
Module 4 — Filter & Archetype Ranking ("Train Hasn't Left")
Role: Apply hard filters, then rank surviving tickers by price-trajectory archetype matching.
Spec: Full spec already written separately (module_4_spec.md). At high level:
•	4a — Hard filters. Cut the universe using snapshot market data (market cap, ADV, fund count, sector, minimum history). Deterministic binary filters.
•	4b — Ratio matrix + archetypes. For survivors, fetch price history (stored in SQLite prices.db), compute the 4×4 price-ratio matrix across 4w/12w/26w/52w windows, match each ticker against 8 configured archetypes (fresh_awakening, quiet_compression, post_crash_rebase, etc.), rank by composite_score = archetype_score × match_confidence.
Output: ranked_candidates_{quarter}.parquet + HTML/Excel ranking reports.
Re-ranking: If the user edits archetypes.yaml or ranking.yaml, Module 4b can be re-run in seconds without re-fetching price data or invalidating downstream LLM caches.
________________________________________
Module 5 — Market Data Enrichment (pre-LLM context)
Role: For the top-N ranked candidates, gather the market/fundamental context the LLM will need.
What it does (high level):
•	User selects top-N from Module 4's ranking (typically 100–300).
•	For each ticker, assemble context: current price, 52w range, market cap, shares outstanding, pre-funded warrants, ADV, sector/industry, recent news headlines (if search budget allows), fund accumulation summary from Module 3.
•	Output a structured per-ticker context pack ready to inject into Module 6's LLM prompts.
Key principle: Module 5 gathers data; it does no scoring. Separating data assembly from LLM inference keeps each testable and means re-runs of Module 6 don't redo enrichment.
Output: llm_context_pack_{quarter}.parquet — one row per top-N ticker with all fields the Module 6 prompt will reference.
________________________________________
Module 6 — LLM Scoring via Claude API
Role: Use Claude to estimate upside potential and time-to-catalyst for each top-N candidate, then rank by appreciation rate.
What it does (high level):
•	Pre-flight cost estimator runs first; requires user approval before submitting batch.
•	Structured prompt with a cacheable prefix (scoring framework, JSON schema, few-shot examples) and a variable suffix (per-ticker context from Module 5).
•	Uses Anthropic's Batch API (50% discount, 24h SLA) combined with prompt caching (cached-read tokens at 10% of base input price). Discounts stack.
•	Prompt returns structured JSON: upside_pct, time_to_catalyst_weeks, catalyst_type, confidence, thesis_summary, key_risks.
•	Composite appreciation rate = upside_pct / time_to_catalyst_weeks, computed deterministically in Python (not by the LLM, to avoid compound-rate hallucination).
•	Responses cached by (ticker, quarter) key — re-ranking Module 4 or re-running Module 6 reuses existing responses.
Expected cost: ~$5–15 per quarterly run for 200 tickers, excluding web search tool fees. Explicit cost gates prevent surprises.
Output: llm_scores_{quarter}.parquet + final integrated ranking report (HTML + Excel) combining Module 4 archetype score with Module 6 LLM rate.
________________________________________
Module 6b — Post-scoring refinement + user-driven selective dispatch
Role: Two responsibilities, both downstream of Module 6's LLM dispatch. Pure-Python and HTML-only — no additional Anthropic calls.
Spec: spec/module_6b_spec.md. Decisions: spec/decisions.md § D47 (composite modifier) + § D48 (selective dispatch + mandatory gate).
Part (a) — Composite score modifier (post-LLM, deterministic):
•	Applies a deterministic multiplier (0.50–1.50) to the M6 primary score so that material signals already in the LLM's research_brief actually move the ranking. The D46 score formula uses only target/time/probability/current_price; everything else (competitive landscape, partnerships, insider activity, runway, dilution, past failures, mgmt track record, acquisition probability, moat, rNPV concentration) is captured but invisible to ranking.
•	9 components combined multiplicatively, clipped to [0.50, 1.50]: competitive crowding, financing risk, recent dilution, insider conviction, mgmt track record, acquisition optionality, moat durability, recent operational failure, rNPV concentration. Each maps a research_brief field to a factor; per-component evidence captured in JSON for audit.
•	Per-component factor maps tunable in config/scoring_modifier.yaml. Adding a component requires a spec rev; tuning a band is a config edit.
•	Ranking key changes: final_rankings.final_rank now sorts by final_score_adjusted (= score_at_current × score_modifier). Raw final_score preserved as a reference column.
•	Run sites: end of scripts/6_score.py after final_rankings write; end of scripts/6_recompute_scores.py after the D46 score recompute; standalone scripts/6b_apply_modifiers.py for retroactive application across historical runs.
Part (b) — User-driven selective dispatch (pre-LLM control):
•	The Module 6 final-ranking HTML report becomes a two-way control surface. Each ticker row carries a checkbox (default checked). Header has a master "select all" checkbox + "Save selection" + "Copy CLI command" buttons. JavaScript handles toggling, master-state derivation, and HTML serialisation of the checked attribute.
•	"Save selection" overwrites the HTML in place via File System Access API (Blob download as fallback). Saved HTML carries the user's selection in the checked attributes.
•	scripts/6_score.py gains --selection-from-html PATH flag — pipeline parses the HTML, restricts dispatch to checked tickers. Mutually exclusive with --ticker / --tickers.
•	Confirmation gate ([y/N] before any Anthropic call) is mandatory regardless of TTY state. Only an explicit single-shot --yes flag bypasses it. The earlier "skip gate when stdin is non-interactive" path is removed (footgun: parent processes piping stdin would otherwise dispatch silently).
•	Selective dispatch + tier classification compose: selected tickers still go through D39 tier classification. Tier A hits within the selection cost $0; the modifier still re-applies. Pre-flight cost summary shows the breakdown.
•	No new SQL columns for selective dispatch (selection lives in the HTML); llm_runs.gate_config_json gains "selection_source" + "selection_count" for audit.
Outputs (changed from M6):
•	Outputs/final_ranking_{quarter}.html — adds checkbox column, master checkbox, Save/Copy buttons, modifier column, adjusted-score column, "Score modifier breakdown" detail panel.
•	Outputs/final_ranking_{quarter}.xlsx — adds modifier + adjusted-score columns; conditional formatting moves to the adjusted score.
•	llm_scores.db gains 6 additive columns (3 per table × 2 tables): score_modifier, score_modifier_json, score_at_current_adjusted_pct_per_month on llm_scores; score_modifier, score_modifier_json, final_score_adjusted on final_rankings.
________________________________________
Module 7 — Outcome Tracking & Feedback Loop
Role: Record the forward price paths of scored tickers, compare realized returns to predicted returns, and surface empirical performance of archetypes and LLM scores.
What it does (high level):
•	After each Module 6 run, append (ticker, quarter, archetype, LLM score, price_at_scoring) rows to outcomes.db.
•	On a scheduled cadence (weekly), fill forward price columns (+4w, +12w, +26w, +52w) as time elapses.
•	Produce analysis reports: 
o	Archetype performance table (realized mean/median return by archetype × window)
o	LLM score calibration (predicted vs. realized scatter, decile-ordered return plot)
o	Suggested archetype score adjustments based on realized data
Key principle: Module 7 is advisory, not auto-updating. It reports what the data says; it does not silently retune archetypes.yaml or Module 6 prompts. User retains editorial control.
Rationale: Auto-adjusting archetype weights from LLM outputs would compound model opinions rather than anchor to reality. Forward-return tracking is the only honest feedback loop.
________________________________________
Consensus-builds delta report (Module 2 derived)
A standalone reporting script — `scripts/_q1_consensus_report.py` — produces a quarter-over-quarter biotech consensus-builds HTML showing which tickers attracted the most new funds entering vs. the previous quarter. Originally a one-off Q1-2026 vs Q4-2025 analysis; generalised 2026-05-28 (D59) to accept `--quarter` and `--prev-quarter` ISO-date arguments. Output: `Outputs/<YYYYQn>_consensus_builds.html`. Defaults to the two most-recent `period_of_report` values in `holdings`.
Auto-invoked by the sibling `3_Biopharmcatalyst_parser` pipeline whenever its orchestrator detects a 13F filing window or a stale funds DB; see `3_Biopharmcatalyst_parser/spec/decisions.md` D13 and `3_Biopharmcatalyst_parser/README texts/Funds_auto_refresh.md`.
________________________________________
Cross-cutting conventions
These apply to all modules and are documented once here rather than repeated per-module:
•	Config-driven. Every tunable parameter (filter thresholds, archetype ranges, fund list, API keys, cache TTLs) lives in YAML, not code. Users tune without code edits.
•	Cache hierarchy. Each module owns its own cache. Core lookup caches (ticker, CUSIP) never cleared even in debug mode. Per-module caches have 30-day hard TTL.
•	Debug vs. production mode. Flip a flag in pipeline.yaml to subset rows, clear stage caches, shorten HTML display timeouts, verbose logging.
•	Idempotency. Any module can be re-run. Outputs are deterministically sorted; byte-identical across runs on the same inputs.
•	Cost approval gates. Any module that calls a paid API (only Module 6) runs a cost estimator first and requires user approval. Per D48, the [y/N] gate is mandatory regardless of TTY state — only an explicit single-shot --yes flag bypasses it.
•	Failure handling. Modules log and continue on row-level anomalies (unrecognized share classes, failed ticker fetches). They fail fast on structural issues (missing config, schema mismatch, missing upstream DB).
•	Quarter convention. Every module uses "YYYYQn" format internally. Quarter is derived from 13F period_of_report (quarter-end date), never from filing_date (submission date, which lags by ~45 days).
________________________________________
Tech stack
•	Python 3.11+, pandas, pyarrow for Parquet
•	SQLite via sqlite3 stdlib
•	yfinance for market data (Modules 4b and 5), with adapter structure allowing future swap to a paid source
•	Anthropic Python SDK for Module 6
•	openpyxl for Excel output
•	YAML configs via PyYAML, env vars via python-dotenv
•	Logging via stdlib logging
________________________________________
Open decisions deferred to implementation
These are flagged explicitly so Claude Code stubs them or asks rather than inventing defaults:
•	Exact column names in Module 2's filings.db (user confirms via sqlite3 ... ".schema" during Module 1 setup)
•	Calibration reference tickers list for Module 4 archetype tuning (TCRX, BCYC, MRNA as starting set)
•	Top-N cutoff feeding Module 5 → Module 6 (user chooses per run)
•	Module 6 prompt content, search budget, and model choice (Opus vs. Sonnet tradeoff)
•	Module 7 update cadence (weekly as default; user may prefer daily or monthly)

