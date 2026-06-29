# 6_Biotech_platform_discoverer — build decisions

Records deviations from `SPEC_acrivon_pattern_screener.md` and the rationale (repo convention:
specs describe the target, decisions record what was built). Newest first.

---

## D23 (2026-06-29) — EU/Nordic IPO dates + Hikma cap web-checked → in-band EU names now tiered (DONE)

Follow-up to D22. (1) **Hikma** cap web-verified at **$4.14B** (was a $5.5B mega-approx) — still above
the $3B ceiling, so correctly out-of-band/excluded (not forced into the in-band set; the analyst band
is a hard constraint). (2) **IPO dates web-checked** for the 7 in-band EU names that lacked them
(Perrigo 1991-12-20, Compass 2020-09-18, Evotec 1999-11-10, DBV 2012-03-29 Euronext, Alliance Pharma
2003-12, Molecular Partners 2014-11-05, Orexo 2005-11-09) and **copied from the sibling EU listing** for
5 dual-listed names (Zealand/Camurus/Almirall/Bavarian Nordic/Galapagos). Note: PRGO/CMPS/DBV each had a
duplicate US-country row (already dated by yfinance) plus an undated EU-country row — set by company_id.

**Result:** all 11 in-band EU/Nordic biotechs are now **tiered** (cap + age both known), so they're
selectable by the Stage-4 tier gate: Molecular Partners **T1** (small & young — priority); Camurus /
Almirall / DBV / Compass **T2**; Bavarian Nordic / Galapagos / Perrigo / Evotec / Alliance Pharma **T3**;
Orexo **T4**. Caps + IPO dates persisted to `data/eu_market_caps_web_2026-06-29.csv`. Outstanding
data-quality nit: the duplicate US/EU company rows for ADR names (PRGO/CMPS/DBV/…) should be deduped.

---

## D22 (2026-06-29) — EU/Nordic market caps populated by manual web check (DONE)

**Why.** D21 enumerated ~66 EU/Nordic names but yfinance can't resolve foreign tickers → all sat in
Tier 0 / unknown-cap. User asked for a direct web check of their market caps (no Claude API).

**Done.** Web-searched current market caps (June 2026) and wrote them to the store:
- **32 web-verified caps** (individual WebSearch): incl. Bavarian Nordic $2.41B, Camurus $2.90B,
  Almirall $2.79B, Galapagos $1.87B, Perrigo $1.52B, Compass $1.40B, Evotec $1.10B, DBV $0.95B,
  Molecular Partners $0.14B, Orexo $0.07B, Zealand $3.30B, Genmab $15B, etc.
- **16 mega-caps** assigned approximate values (Novo/Sanofi/Novartis/AZN/GSK/JNJ/Bayer/Merck KGaA/
  Sandoz/Novonesis/Qiagen/Alkermes/Jazz/BioNTech/…) — all ≫$3B so band-cut regardless; flagged
  `mega_cap_approx` (not individually web-verified).
- **18 marked not-live** (acquired/delisted; Morphosys + Orchard web-confirmed, rest documented M&A:
  Shire→Takeda, Allergan→AbbVie, Horizon→Amgen, GW→Jazz, Mylan→Viatris, Prosensa→BioMarin, …).

After Stage-0b band logic: mega-caps deleted `mktcap_out_of_band`, defunct deleted `not_live`, leaving
**~11 in-band EU/Nordic biotechs** visible (several now tiered: Camurus T2, Bavarian Nordic/Galapagos
T3). Full 66-row mapping persisted to `data/eu_market_caps_web_2026-06-29.csv` (re-runnable audit
record, since the working DB is gitignored). **Caveat:** this is a one-off manual enrichment; the
durable fix remains the licensed-provider swap (ROADMAP item 9), which also brings EU **IPO dates** so
the remaining Tier-0 EU names (cap known, age unknown) can be tiered + scored.

---

## D20 (2026-06-29) — Surface triage-killed companies in the HTML report (BUILT)

**Why.** Only ~13 companies showed Claude results in the report; the ~59 companies Haiku *triage-killed*
(evaluated, cut before the rubric) had no score row and were indistinguishable from never-scored names —
the work done was invisible. User asked to "highlight them in red and move to the bottom."

**Built (`render.py`).** `_triaged_out(store)` reads the `stage4/cut/triage_kill` audit rows →
`{company_id: why}`. `build_data` sets `triaged_out=True` + `triage_reason` on any unscored row whose
group was triage-killed (scored takes precedence). The JS `sortRows` sinks `triaged_out` rows to the
bottom of every tier regardless of the active sort column; CSS renders them red (row tint + red ticker);
the status cell shows "triaged out"; the expand panel shows the Haiku reason. New funnel card "triaged
out". 3 new tests (test_render.py); 180 pass. Live: 13 scored + 27 triaged-out (deduped) in the report.

---

## D19 (2026-06-29) — Evidence-level incremental re-scoring §12 (E15) (BUILT)

**Problem.** D11 re-opens a ticker for scoring on a *config* change; the Stage-2 TTL refreshes *harvest*.
But nothing re-scored a ticker when its **evidence** changed (a fresh 10-K, new trials, updated patents)
within the rescore-TTL — the weekly monitor would keep a stale score.

**Built (no schema migration).** `stage4._evidence_fingerprint(store, cid)` = sha1 of sorted
`source:payload_hash` pairs over `_EVIDENCE_SOURCES`. `_company_score_key()` FOLDS the scoring-config
hash with that fingerprint and stores the combined key in the existing `scores.config_hash` column.
`_candidates` skips a ticker only when its stored key still matches the recomputed one — so a config
change OR new evidence re-opens it. Gated by `stage4_scoring.evidence_incremental` (default true; false
= legacy config-hash + TTL only). Persist sites (rubric/finalize/resume) all store the combined key.
Note: pre-D19 scores carry a plain config_hash, so each previously-scored ticker re-scores once.

**Tests.** `test_evidence_change_forces_rescore_within_ttl` + updated the two TTL/config tests to seed
the combined key. 177 tests pass.

---

## D18 (2026-06-29) — Structured per-stage JSON logs §14 (C11) (BUILT)

`observability.append_stage_log(out_dir, run_id, stage, summary, elapsed_s)` appends one JSON line per
stage run to `Outputs/logs/stage_events.jsonl` (counts in/out, cost, wall-time). Because each stage is
its own process invocation (own run_id), a single cumulative event log — not a per-run file — is the
queryable shape for the weekly cadence. Wired into `scripts/6_screen.py` via a `_emit()` timing wrapper
around every stage (0a/0b/1/2/4/5). Fail-open. 1 new test.

---

## D17 (2026-06-29) — EDGAR full-text Stage-2 source: 10-K Item 1 "Business" (B5) (BUILT)

**Why.** yfinance's `longBusinessSummary` is a one-paragraph blurb; the 10-K **Item 1 ("Business")** is
the company's own multi-page platform/technology narrative — the richest free signal for the A/B
data-engine judgment.

**Built.** `clients/edgar_fulltext.py`: latest annual report (10-K/20-F/40-F) via the SEC submissions
API → fetch primary doc → `_strip_html` → `_extract_item1` (longest-match heuristic, ≥400 chars, capped
16k). Persisted as Stage-2 source `edgar`; `build_bundle` feeds a 2.8k-char excerpt as `sec_10k_business`.
Added `edgar` to `stage2.sources` + `_EVIDENCE_SOURCES` (so it reaches the bundle AND the D19
fingerprint). Fail-open, SEC User-Agent, 64MB capped reads, US filers only (non-US → no edgar row,
reported as ABSENT DATA, never a penalty). Extraction is heuristic (can include a cross-reference
prefix); the bulk is genuine 10-K text. 6 pure-parser tests + live ACRV fetch verified (2026 10-K, 16k
chars). NOTE: the 13 already-scored companies pre-date this source; re-harvest + re-score to benefit
(D19 will auto-re-open them once edgar evidence lands).

---

## D16 (2026-06-29) — Speed up Stage-4 Claude calls: basic web_search variant + larger output cap (BUILT)

**Problem.** Stage-4 scoring was far slower than `3_Biopharmcatalyst_parser`'s dispatch (user: "the
return time for 3_Biopharmcatalyst_parser was much faster"). Real-time runs appeared to hang and a
batch never produced results.

**Diagnosis (measured, not guessed).** Timed one real Acrivon rubric call (Sonnet 4.6, identical
prompt/schema) under each permutation, `max_retries=0`:

| web_search | output | time | searches |
|---|---|---|---|
| `web_search_20260209` (dynamic-filtering) + structured | — | **>600s TIMEOUT** | — |
| `web_search_20260209` + free-form JSON | 277s | **20** (ignored max_uses=3) |
| `web_search_20250305` (basic) + structured | 71s | 3 |
| `web_search_20250305` (basic) + free-form JSON | 44s | 3 |

Two root causes: (1) the dynamic-filtering `web_search_20260209` runs a code-execution sandbox per
search, **ignores `max_uses`** (ran ~20 searches), and is **10x+ slower** (timed out) — whereas
`3_Biopharmcatalyst_parser` uses the basic `web_search_20250305` which honors `max_uses`; (2) every call
hit `stop_reason=max_tokens` at `max_tokens=1500` — the rubric JSON was **truncated → invalid → dropped
(scored=0)**.

**Built.**
- `web_search_tool()` gains a `tool_type` param, **default now `web_search_20250305`** (basic). Config
  knob `stage4_scoring.web_research.tool_type`.
- `stage4_scoring.max_output_tokens: 12500` (matches `3_Biopharmcatalyst_parser/module_7.yaml`),
  replacing the hardcoded `1500` at all three Stage-4 call sites (triage stays 256).
- Updated `test_web_search_tool_shape`; 169 tests pass.

**Validation.** Re-dispatched 8 tickers real-time: `scored:4, web_searches:12` (exactly 3/company —
`max_uses` honored), no timeout, $0.73. ACRV 0.968/0.904 with memos citing real web findings
(~120k-phosphosite dataset); GRAL 0.904/0.760 citing CCGA/STRIVE; ACET 0.486. Full untruncated memos.

**Lesson captured in project memory** (`feedback_claude_web_search_variant_and_output_cap`): prefer the
basic web_search variant for bounded fast calls; size `max_output_tokens` to the full response or
structured output silently truncates; structured output is ~40% slower than a free-form JSON fence;
diagnose API speed empirically on the real call.

---

## D15 (2026-06-29) — max_retries=1 + per-item async persistence (BUILT)

Two tightenings on top of D13/D14: (1) `AnthropicClient(max_retries=1)` (SDK default 2) on both the
sync and async clients — with the 180s timeout (D14) this caps a wedged call's worst case at 2×180s
instead of 3×180s; config `stage4_scoring.max_retries`. (2) `score_realtime_many(on_result=…)` fires a
persist callback **inside each async task the instant its score returns**, so the parallel rubric +
finalize tiers persist per-item (durable mid-gather) rather than only after the whole `gather` —
matching the batch path's per-result persistence. asyncio is single-threaded so the synchronous
`record_score` inside the task is race-free. 169 tests pass.

## D14 (2026-06-29) — Bound the per-request SDK timeout (the real cause of the "hangs") (BUILT)

Root-caused the two ~1hr wedged Stage-4 runs: NOT web search. A direct probe showed a single
`web_search_20260209` Sonnet call completes in **~20s** (SDK 0.97.0, 2 searches + dynamic-filtering
code execution). The real cause: `AnthropicClient` built the SDK with **no timeout → the 10-minute
default + 2 retries**, so any transient network stall on one call balloons to ~30 min, and a couple of
those across a run look like a multi-hour hang (the batch run was stuck in `batches.create()` — no
batch ever appeared in the account's batch list). Fix: `AnthropicClient(request_timeout_s=180)` passed
to both `Anthropic(timeout=…)` and `AsyncAnthropic(timeout=…)`; config `stage4_scoring.request_timeout_s`.
Now a stalled call fails fast and (with D13 async + crash-safe persist) the run stays bounded and
durable. Lesson added to memory `feedback_persist_during_long_api_batches`.

## D13 (2026-06-29) — Claude-API optimizations reapplied from 3_Biopharmcatalyst_parser (BUILT)

After a real-time web-search run hung ~1hr (sequential per-company web_search) and a stop lost all
in-memory work, audited `3_Biopharmcatalyst_parser`'s dispatch (`module_7/dispatch.py` + `cache.py`)
and reapplied its proven optimizations to component 6:

1. **Crash-safe incremental persist** (already shipped `0c317c3`): each score written the instant it's
   produced, not bulk-at-end. See memory `feedback_persist_during_long_api_batches`.
2. **Batch submit/poll split + `on_submit(batch_id)` + `--resume`**: `anthropic_client.submit_batch`
   returns the id immediately; `score_batch(on_submit=…)` persists it to `run_meta.metrics_json`
   (`stage4_batch_id`) BEFORE the long poll; `collect_batch(batch_id)` is idempotent; new
   `stage4.resume_stage4(run_id)` + `6_screen.py --resume RUN_ID` re-attach a crashed poll without
   re-dispatching (Anthropic keeps batch results ~29 days). No schema change (uses run_meta).
3. **Async bounded-concurrency real-time** (`score_realtime_many` via `AsyncAnthropic` + semaphore,
   `stage4_scoring.concurrency=6`): triage (all candidates) + Opus finalize (contested band) +
   the `--no-batch` rubric path now run in PARALLEL — the fix for the sequential ~1hr stall. Same
   pause_turn + cost/search tracking as the sync path.
4. **`allowed_domains` on web_search** (`web_research.allowed_domains`, default `[]`=unrestricted): a
   curated whitelist to focus searches + cut cost, mirroring 3_'s domains YAML.
5. **Skip-unchanged cache** — 3_'s identity+prompt-version cache (`cache.py`) is already the equivalent
   of component 6's rescore-TTL (D3) + config-hash (D11); not re-implemented.

Note: the in-flight batch was launched pre-D13 so its batch_id wasn't persisted (no resume for that one
run); all future runs are covered. 169 tests pass.

## D12 (2026-06-29) — SEC earliest-filing-date fallback for ipo_date (BUILT)

The Tier-0 (untiered) bucket exists when a company has no `ipo_date`; yfinance leaves gaps. Built a
fail-open SEC fallback (`clients/sec_submissions.py`): resolve ticker→CIK from the SEC cik↔ticker file
(reusing `sec_sic.parse_cik_exchange`, inverted), fetch `data.sec.gov/submissions/CIK….json`, take the
**oldest `filings.recent.filingDate`** as the ipo_date. Wired into `listings.yfinance_enricher` — it
fills ipo_date **only when yfinance gave none AND the company has none** (never overrides), gated by
`stage0b.sec_ipo_fallback` (default true), with the cik↔ticker file fetched once per run. US filers
only (non-US tickers have no CIK → None).

**Caveat (documented):** the earliest filing is a *proxy* — for a recent IPO it's usually the S-1,
which predates the actual first-trade date by months (e.g. ACRV → 2021-02-12 vs the Nov-2022 IPO). So
the SEC fallback can read a hair OLDER than the true IPO; acceptable for a recall-safe gap-filler
(yfinance `firstTradeDate` is preferred whenever present). Pure parsers unit-tested; live fetch
verified (ACRV/GRAL/ATYR/TKNO). 165 tests pass.

## D11 (2026-06-29) — Incremental re-runs: a scoring-config change forces a re-score §12 (BUILT)

§12 wants a config change to "force re-evaluation of affected stages." Built the scoring half:
`config.config_hash(config)` = stable 16-hex hash of the SCORING-relevant sections only
(`stage4_scoring` + `composite_weights` + `penalties`); schema **v4** adds `scores.config_hash`;
`record_score` stamps it; `store.last_score_meta` returns (run_id, config_hash). `stage4._scored_within`
now treats a score as fresh (TTL-skippable) ONLY if it's within `rescore_ttl_days` **AND** scored under
the current hash — a scoring-config change (e.g. retuned weights/penalties or model) re-opens every
ticker for re-scoring even inside the TTL; unrelated config edits (Stage-0 nets, regions) don't. Pre-v4
scores have `config_hash=NULL` → treated as changed → re-scored once on the next run (correct: the D9
web-research switch changed scoring, so those stale scores SHOULD re-run). 161 tests pass. The
evidence-level half of §12 (recompute only companies with changed evidence) remains the existing
Stage-2 incremental-TTL; the run_meta config_hash field is available for a future stage-wide gate.

## D10 (2026-06-29) — Run summary / observability §15 (BUILT)

`observability.py` + `scripts/6_summary.py` emit a per-run Markdown digest →
`Outputs/run_<id>_summary.md` + a stable `Outputs/run_summary.md`. Read-only (reconstructs the run
from audit_log + run_meta + scores; no API): **funnel** at each stage (0a/0b/1/2/4/5 counts incl. the
D9 web-search count + cost), **tier breakdown** (live universe), the ranked **shortlist** (reuses
`stage5.rank`), **top movers vs the previous score run** (`movers()` diffs each company's two most
recent composites — biggest up/down), and **seed validation** (precision/recall/F1 + spec-failure +
graduated, via `seed_eval.evaluate`). Wired into the run `.bat` after seed-eval. The spec §15
"structured JSON logs per stage" part is already covered by the existing per-stage `log.info(summary)`
+ audit rows; this builds the run-summary half. 159 tests pass.

## D9 (2026-06-29) — OpenAlex DISMISSED; the Claude call web-researches publications + pedigree (BUILT)

Analyst directive: "Dismiss OpenAlex entirely — not suitable for this pipeline. Assign to the Claude
API call whatever was previously queried to OpenAlex." OpenAlex gave the pipeline two signals —
**publications** (footprint/impact) and **scientific/founder pedigree** (top authors + prestige-lab
matches) — but its institution registry covers <5% of small-cap biotech and it rate-limits on a
depleting $-budget (D8). So OpenAlex is removed and those signals are now produced **by the Stage-4
Claude call itself, via the web_search server tool**.

**Removed:** `clients/openalex.py` (deleted); `openalex` + `pedigree` from `stage2.sources` (now
`["ctgov", "patents"]`) and the pedigree harvester; `openalex`/`pedigree` from `stage4._EVIDENCE_SOURCES`
and `build_bundle` (no more `publications`/`pedigree` bundle fields); OpenAlex concepts from Stage-1
tagging (now ctgov conditions only).

**Added — web research (per claude-api skill):**
- `clients.anthropic_client.web_search_tool()` → `{"type": "web_search_20260209", "name": "web_search",
  "max_uses": N}` (the dynamic-filtering variant; Sonnet 4.6 + Opus 4.8 support it — do NOT also
  declare code_execution). `score_realtime`/`score_batch` take a `tools=` param; realtime handles the
  `pause_turn` server-tool loop (re-send up to N); `_extract` now reads the LAST text block (the
  constrained JSON after the search turns); per-search fees tracked on `client.web_searches`/`spent_usd`.
- **Tiering:** web_search is given to the **rubric (Sonnet)** + **finalize (Opus)** tiers only; **Haiku
  triage stays search-free** (cheap recall cut). Config `stage4_scoring.web_research` (enabled,
  max_searches_per_company=5, cost_per_1k_searches_usd=10, est_searches_per_company=3).
- **Prompt:** the rubric preface now mandates web search to find the company's peer-reviewed
  publications + founder/SAB pedigree (never invent a citation/name); rule 6 (pedigree) + rule 10
  (coverage fairness) rewritten for the research model; the curated `prestige_labs.yaml` (awardees +
  labs) is injected into the system prompt as a PRESTIGE LIST for high-precision founder matching
  (`build_rubric_system(taxonomy, prestige)`).
- **Cost estimate** adds a `web_search` tier (n_survivors+n_contested × est_searches × per-search).

**Why structured output + web search coexist:** `output_config.format` is compatible with server
tools (claude-api skill confirms); the model runs the search loop, then emits the schema-constrained
JSON as its final text block. `max_uses` is bounded so the server loop finishes (avoids pause_turn) and
so it works under the Batch API. 155 tests pass (was 155; net: openalex/pedigree parser tests removed,
web-search/prompt/estimate tests added). The cost gate is unchanged — web spend only happens on
`--stage 4 --dispatch` past the `[y/N]` + `max_usd_per_run` ceiling.

## D8 (2026-06-29) — Data-coverage fairness: a no-OpenAlex company must not be penalized (BUILT)

Analyst concern: are biotechs with no OpenAlex record penalized? They were, at the margin — two
vectors: (a) `compute_confidence` drops with fewer evidence sources (mechanical, by design); (b) the
rubric could read an OpenAlex *coverage gap* as genuine absence of science and dock the A axis (rules
5/6). Since OpenAlex indexes <5% of small/early biotech as institutions (live: 29/589 openalex,
12/589 pedigree, vs 439/589 ctgov), this unfairly hit exactly the names the screen targets. Three
fixes (BUILT, code-only — take effect on re-score / re-harvest):

1. **`build_bundle` `data_coverage_note`** — when `publications`/`pedigree`/`patent_estate` are
   absent, the bundle now says so explicitly: "ABSENT DATA, not negative evidence … do not lower any
   axis." (verified live on ACRV, whose only evidence is ctgov.)
2. **Rubric rule (10) DATA-COVERAGE FAIRNESS** (takes precedence over rule 5 for missing
   OpenAlex/patent signals) + rule 6 scoped so "thin author list" only bites when a pedigree field IS
   present, not when it's entirely absent.
3. **Fallback pedigree** (`openalex.fetch_top_authors` → `parse_authors_from_works`) — when a company
   has no OpenAlex *institution* record, tally authors from works matching its raw affiliation string
   (`raw_affiliation_strings.search`), so names still feed prestige matching (h-index unavailable).

**KEY INFRA FINDING — OpenAlex "429" is a depleting $-BUDGET, not just req/s.** The 429 body reads
"this request costs $0.001 but you only have $0.0007 remaining"; the singular `raw_affiliation_string`
(no s) returns 400 (invalid field) while the correct `raw_affiliation_strings.search` returns 429 —
proving the filter is valid and the limiter is a credit budget that refills over time. This reframes
the whole 429 story: the polite-pool `mailto` raises the budget, but bulk harvests + bursts exhaust it
(hence sparse universe coverage is partly budget, partly genuine institution-coverage gaps). Mitigation
is fail-open everywhere (429 → None → status quo); a future improvement is budget-aware pacing /
spreading the harvest. 155 tests. NEXT: re-score the seeds to confirm no-OpenAlex names aren't docked.

## D7 (2026-06-29) — Keep the $3B ceiling; positives above it are "graduated", not failures (DECIDED)

The D6 seed-eval flagged TNGX ($5.1B) / IDYA ($3.5B) — known positives — deleted at the band ceiling.
**Analyst decision: KEEP the `$50M–3B` band** (spec §0.1). The screen deliberately targets the "early
but real" small-cap window; a positive that has re-rated above $3B has *graduated* and is out of scope
by design, not a screen failure.

Encoded in the harness so the signal stays honest: `seed_eval._disposition` classifies a positive's
Stage-0 deletion — **`graduated`** (cap > ceiling, accepted) vs **`below_floor`** (cap < floor) /
**`not_live`** (genuine losses). `spec_failure` now fires ONLY on genuine losses; graduates are
reported under `graduated_positives` with an ℹ️ note. Live: TNGX/IDYA → graduated, `spec_failure=False`,
`positives_lost_pre_scoring=0`. The flag is preserved for what it's meant to catch — a positive lost
*below* the floor or for liveness — so a real Stage-0 bug would still scream. 150 tests pass.

## D6 (2026-06-29) — Seed-eval validation harness §13 (BUILT)

The trust gate. `seed_eval.py` + `scripts/6_eval.py` score the labeled seed set
(`config/seed_labels.csv`) through the funnel and report **precision / recall + per-stage survival** →
`Outputs/seed_eval.md`, metrics persisted to `run_meta.metrics_json`. Read-only and FREE (no API): it
evaluates whatever scores already exist; it does NOT dispatch scoring.

**Labels** (`config/seed_labels.csv`, analyst-editable): positives = ACRV/TNGX/IDYA/BOLD; borderline
AI-discovery = RXRX/SDGR/RLAY; negatives = CRL/MEDP/ICLR (CROs) + BRKR/A (tools). Three labels, not
two — **borderline** is reported separately and never counted in precision/recall (the store
`seed_labels` table still only accepts positive|negative; borderline is CSV-only).

**Decisions:**
- **Per-seed funnel position is the headline, not just P/R.** For each seed we resolve: deleted at
  Stage 0 (with reason, recovered from the audit log since the row is gone) / absent (never entered) /
  in-store + tier + tagged + has-evidence + scored + composite. The spec's load-bearing check is a
  **positive DELETED at the hard cut** → `spec_failure=True` + a loud banner; that is a Stage-0 bug to
  fix before trusting the run (a positive can only be lost invisibly at Stage 0).
- **Unscored ≠ lost.** A positive that is in-band and retained but simply not scored yet is reported
  `unscored`, NOT a failure (scoring costs money / runs on selected tiers). P/R are computed only over
  pos/neg seeds that HAVE a composite, at `seed_eval.decision_threshold` (0.6). Both a strict
  confusion matrix (TP/FP/FN/TN) and a survival table per group are emitted.
- **`--persist-labels`** optionally writes pos/neg into the store `seed_labels` table; default off
  (eval stays read-only).

Wired into the run `.bat` after Stage 5 (free). 148 tests pass (was 137).

**LIVE FIRST RUN (2026-06-29) — the harness immediately earned its keep.** It flagged a SPEC-LEVEL
FAILURE: positives **TNGX ($5.1B)** and **IDYA ($3.5B)** (+ borderline **RLAY $4.0B**) were deleted at
Stage 0b as `mktcap_out_of_band` — they've re-rated ABOVE the `$50M–3B` ceiling. Precision/recall came
out 1.0/1.0 but over only 1 scored positive (ACRV) + 0 scored negatives (all 5 negatives correctly
deleted out-of-band) — not yet statistically meaningful.

**CALIBRATION PASS 2 (2026-06-29) — now a genuine pass.** Two fixes made the validation meaningful:
(1) **triage-kill = predicted-negative** — `seed_eval` now reads the Stage-4 audit `cut`/`triage_kill`
rows, so a Haiku kill counts as the pipeline's negative verdict (killed positive = FN, killed negative
= TN), not an ignored "unscored". (2) **in-band negatives added** (`TKNO`/`MRVI`/`NEOG` — small/mid-cap
tools/reagents vendors that survive Stage 0 and reach the scorer; the original CRO/tools negatives are
all >$3B and cut before scoring). Plus the seeds were **re-harvested** (OpenAlex 429 cleared — see the
ipo gotcha) so BOLD got a real bundle. Result: **P/R/F1 = 1.00/1.00/1.00 over TP=2 (ACRV 0.904, BOLD
0.800) · TN=3 (TKNO/MRVI/NEOG all triage-killed) · FP=FN=0**. The screen recovers known positives and
rejects known negatives. Borderline: RXRX 0.744 (pred. +), SDGR 0.568 (just under 0.6). 151 tests. **Open analyst decision (handoff PENDING 0b):**
raise `market_cap.max_usd` so graduated positives stay in, vs. accept the band targets the early window
and these are out-of-scope graduates. The band is analyst-authored (§0.1) — left unchanged pending that
call. This is exactly the calibration loop §13 exists for.

**yfinance field fix (part of this batch):** `ipo_date` capture (D4) read the obsolete
`firstTradeDateEpochUtc`; current yfinance uses `firstTradeDateMilliseconds` + `ipoExpectedDate`.
`market._first_trade_date` now tries all three — populated 3/589 before the fix, 589/589 after, which
is what activated the tiers.

## D5 (2026-06-29) — Market-cap × age TIERING upstream of Claude + interactive tiered report (BUILT)

Analyst request: **(1)** move the IPO-date signal UPSTREAM of the Claude call (use it as a filter, not
only the Stage-5 ranking tilt); **(2)** bucket tickers into **4 tiers** by market cap (`$400M`) AND
IPO age (`20yr`): T1 small&young, T2 large&young, T3 large&old, T4 small&old; **(3)** ask which tiers
to score at run time; **(4)** render with the `3_Biopharmcatalyst_parser` report features (tier tabs,
green-highlight + acknowledge checkbox for seen-tracking, collapsible Claude results, new-items filter).

**Built.**
- **`tiering.py`** — `compute_tier(company, config)` → 1-4, or **0 = untiered** when cap OR ipo_date is
  missing. Tier 0 is a deliberate addition (the user named 4 tiers; missing data needs a home):
  recall-safe, never hidden, operator can still select it. Boundary: `cap < threshold` = small,
  `age < threshold` = young (so the threshold value itself counts as large/old). Computed on the fly
  (never persisted) so it always tracks current cap/IPO. Config `tiers.{mktcap_threshold_usd,
  ipo_age_threshold_years}`. `age_years` now lives here; `stage5` imports it (one definition).
- **Stage 4 tier gate** — `_candidates(..., tiers=set)` filters the DUE set by tier BEFORE any API
  call; `due_tier_breakdown()` feeds the prompt. `run(..., tiers=)` carries `selected_tiers` into the
  estimate + dispatch summary + audit. The gate is the IPO-date filter "upstream of Claude."
- **CLI** — `--tiers 1,2|all` or, when omitted, an **interactive prompt** showing due-candidate counts
  per tier and asking which to score; `--yes` with no `--tiers` defaults to all. Aborts cleanly if the
  selected tiers have 0 due candidates.
- **Interactive report** — `render.py` rewritten as a **template + data-sidecar** pair
  (`screener_report.html` hash-stable + `screener_report_data.js` rewritten per run; repo
  html-template-data-split convention). Features ported from 3_: **tier tabs** (T1 default → T4,
  Untiered, All, with counts), **green highlight + acknowledge checkbox** (localStorage
  `pd_screener_acknowledged_v1`, PK = company_id; every company starts NEW until ticked — no
  bootstrap-seed, unlike 3_, because the screener has no "prior batch" to pre-ack), **collapsible
  Claude panel** (memo / axes / moat / mechanisms / disconfirming), **new-items filter** (any / new /
  ack) + ticker search + min-composite + scored-only. Duplicate company-ids collapse via the Stage-5
  shared-signal union so each real company is one row.

**Operational note — tiers need `ipo_date`.** It populates on the next Stage-0b `--enrich-yf`
(yfinance `firstTradeDateEpochUtc`, wired in D4). Until then every company is **Tier 0 (untiered)** —
verified live (581 untiered, the 6 scored among them). The run `.bat` already does `--enrich-yf`, so
the next full run fills the tiers. SEC first-filing fallback for `ipo_date` remains deferred (D4/D2).

**Relationship to D2 lifecycle multiplier:** tiering is now the PRIMARY use of IPO age (a hard
upstream filter the operator controls); the Stage-5 `lifecycle_weight` multiplier stays as an optional,
off-by-default downstream re-ranking tilt. They don't conflict — one gates *what gets scored*, the
other tilts *ordering of what was scored*. 137 tests pass (was 114).

## D4 (2026-06-29) — Stage 5 rank/dedup/export BUILT (+ lifecycle prereq) (BUILT)

The terminal stage (spec §5.7): rank → dedup → refresh review queue → persist run_meta → export the
analyst-house-format shortlist. No API, no deletions. Built as `stage5.py` + `--stage 5` (writes
`Outputs/shortlist.md`). Three sub-decisions:

**(a) Dedup is presentation-layer, not a row deletion.** The cardinal rule (§0.2) forbids deleting a
company for being a duplicate. So Stage-5 dedup collapses duplicate company-ids *only in the ranked
shortlist* — it keeps the best-scored representative and records the others as `merged_ids`; every row
survives in the store (`get_company` still returns them). Verified by test. The primary ADR/dual
collapse still happens earlier in `dedup.collapse` (before rows exist); this is the safety net for the
seed-CSV-name vs SEC-name double-id (ACRV/RXRX).

**(b) Dedup matches on SHARED SIGNALS via union-find, not a single per-row key.** First attempt used a
"strongest identifier per row" key (ISIN → ticker+country → name) — but it FAILED on the live ACRV
case: the seed row carries an ISIN while the SEC row does not, so they landed in different key tiers
(`isin:…` vs `tkr:ACRV@US`) and never merged (RXRX merged only because neither row had an ISIN). Fix:
each row emits a SET of signals {isin, ticker+country | name-when-tickerless}; rows are unioned if they
share ANY signal (transitive). Legal-entity suffixes (Inc/Corp/Ltd/AB/…) are stripped for the name
fallback; industry words (therapeutics/pharmaceuticals) are NOT, so distinct companies sharing a stem
stay separate. Live result: 8 scored rows → 6 shortlist rows (ACRV + RXRX each collapsed).

**(c) Lifecycle age-weighting wired but OFF by default (D2 Lever 2).** `rank_score = composite ×
lifecycle_weight(age)`; weight is 1.0 for age ≤ `old_threshold_years` (the young are never penalized,
rule 3a), decays linearly to `floor` between `old_threshold` and `hard_old_years`, and sits at `floor`
beyond (ship sailed, rule 3b). Unknown age → 1.0 (recall-safe). It NEVER alters the auditable composite
and never deletes — only reorders. **Prereq built:** schema **v3** adds `companies.ipo_date`;
`market.fetch_ticker_info` now reads `firstTradeDateEpochUtc` → ISO date, persisted on the next 0b
enrich (NULL until then). SEC first-filing fallback + market-cap-appreciation discount remain deferred
(D2 phase 2). Config: `stage5.lifecycle.enabled` (false), `stage5.shortlist_top` (25),
`stage5.review.*` thresholds.

**Review-queue refresh (§11):** scored names with `substance_check ∈ {marketing, mixed}` or
high-composite-but-low-confidence are routed back to the review queue for a human look. (On the current
store all 6 trip it — evidence is sparse because OpenAlex 429'd during the test harvest, so confidence
is low; re-harvest will lift confidence and clear most. See handoff PENDING #3.)

114 tests pass (was 98). Render's "Composite scores" section was upgraded to a deduped, lifecycle-aware
**Ranked shortlist** (axes + rank_score) driven by `stage5.rank`.

## D3 (2026-06-28) — Rescore-TTL: don't re-analyse a ticker within a year (BUILT)

Analyst request: once a ticker has been through the Claude scorer, don't run it again for **1 year**
unless reset. Built: `stage4._scored_within` (using `store.last_scored_at`, the MAX score `run_id`,
an ISO timestamp) filters out any company scored within `stage4_scoring.rescore_ttl_days` (default
365) from the candidate set — for both the cost estimate and the dispatch. `--force-rescore` is the
reset (the estimate then counts everything). Verified live: after one 10-ticker run, re-running the
same set drops 13 → 5 due (only the never-scored survive) at an estimated ~$0.05. This is the scoring
analogue of the Stage-2 evidence TTL; it bounds repeat Claude spend.

## D2 (2026-06-28) — Lifecycle / company-age weighting (PROPOSAL; optional, Stage 5)

Analyst intent: **(3a)** don't penalize *young* companies for thin clinical evidence; **(3b)**
deprioritize companies where "the ship has sailed" or that have been in business **> 20 years**;
**(3c)** weigh via IPO date, market-cap appreciation since IPO, etc. Two separate levers:

**Lever 1 — scoring fairness (3a) — BUILT NOW (rubric rule 9).** The Claude rubric is instructed not
to dock an early/recently-public platform for absent late-stage trials: score the *platform*
(A/B/pedigree/validation), and let a low E lower the score only modestly for a clearly young company.
So youth no longer drags the composite down — it's handled where the judgment happens.

**Lever 2 — a lifecycle MULTIPLIER at ranking (3b/3c) — PROPOSED, optional, Stage 5.** A transparent
post-score multiplier: `rank_score = composite × lifecycle_weight(company)`, **off by default**
(`stage5.lifecycle.enabled: false`). It never alters the Claude composite (auditable) and never
deletes a company (recall-safe) — it only reorders. The shape (config-driven):

| Age since IPO | weight | rationale |
|---|---|---|
| `< young_grace_years` (3) | **1.0** | the young get no penalty (3a) |
| `young_grace … sweet_max` (3–12) | **1.0** | the target "early but real" window |
| `old_threshold … hard_old` (15–20) | linear decay 1.0 → floor | maturing |
| `≥ hard_old_years` (20) | **floor** (0.5) | the ship has sailed (3b) |

**Signals (cheap → richer):** (a) **years since IPO** — yfinance `firstTradeDateEpochUtc`, fallback
SEC first-filing date (`data.sec.gov` submissions, oldest); (b) **market cap** (already have — microcap
≈ early, large ≈ mature, a secondary tilt); (c) **trial maturity** (already have from ClinicalTrials —
many Phase 3/4 ≈ late, few/early ≈ young); (d) **market-cap appreciation since IPO** (phase 2 — needs
IPO price + price history; if a name is already heavily re-rated, the upside ship has sailed → extra
discount; if under-appreciated with a strong platform, a small boost).

**Foundation to build first:** capture the age signal — extend `market.fetch_ticker_info` to read
`firstTradeDateEpochUtc` and persist it (schema v3 `ipo_date` column), populated on the next Stage-0b
enrich. Appreciation (phase 2) needs a price-history fetch. Then `stage5.lifecycle_weight(company,
config)` implements the curve, and Stage 5 surfaces both `composite` and `rank_score`.

Config scaffold is in `config.yaml → stage5.lifecycle` (disabled). Full wiring lands with Stage 5.

---

## D1 (2026-06-28) — Skip Stage 3 embedding; score the universe directly with tiered Claude

**Spec §5.5 / §8** put an embedding semantic pre-rank between harvest (Stage 2) and Claude scoring
(Stage 4): build archetype vectors, rank by cosine similarity, keep the top fraction (with a
`min_keep` floor) so the expensive Claude models only touch a cut-down set. **Decision: skip it** and
feed the whole retained universe straight into the tiered Claude scorer.

**Why.** The embedding cut exists to control cost when the candidate set is large. At our scale it
isn't large: the live universe after Stage 0b is **~589 companies**, and a full tiered Claude run over
*all* of them costs **~$2.57** (measured estimate: Haiku triage 589 → $0.65, Sonnet rubric ~294 →
$1.15 via Batch, Opus finalize ~59 contested → $0.77) — versus the `max_usd_per_run: 50` ceiling. The
embedding stage would save cents while adding a build, an embedding dependency, and a recall risk
(a wrong cut is invisible). The cheap **Haiku triage** tier already does the recall-safe first cut,
and it reads the *full* evidence rather than a cosine proxy.

**What we forgo (all minor at this scale):** (1) embedding *retrieval* of top-k evidence chunks —
unnecessary because our bundles are already compact summaries; (2) the `min_keep` recall floor —
moot, since scoring *everything* pre-cuts nothing (recall is higher); (3) semantic dedup / emergent
clustering — a nice-to-have, off the critical path.

**When to revisit.** If the universe grows to thousands (broader regions / looser filters) where a
Haiku pass over everything starts costing real money, or if semantic dedup becomes valuable, build the
embedding pre-rank then. The `embed/` slot in the spec architecture remains open for that.

**Built instead (M5 = Stage 4, spec §9–§10):** Haiku triage (keep/kill, recall-safe, every live
company) → Sonnet full §9.3 rubric over survivors via the Batch API → Opus adversarial re-score on the
contested composite band [0.55, 0.75]. Composite + penalties computed in code (not trusted from the
model). Pre-dispatch cost estimate + `[y/N]` gate + `max_usd_per_run` hard stop. Candidate set = all
live companies (`score_live_excluded: true`) — Stage 1's description-only exclusion does **not** gate
scoring (recall-safe); `ta_tags` feed the bundle as mechanism candidates that D_mechanism refines.
