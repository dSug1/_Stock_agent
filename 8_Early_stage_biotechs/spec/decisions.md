# 8_Early_stage_biotechs — Decisions log

*Newest first. The overall design lives in `disruptive-biotech-early-detection-spec.md`; this file
records where the build deviates from it and why. Phase-1 build detail is in
`phase1_universe_build_spec.md`.*

---

## D8 — Founder-lineage extraction is the first Claude spend; cheap Haiku tier, gated, lessons-applied
**Spec ref:** §5.1, §5.5. **Decision:** the first Claude call in Module 8 is **founder-lineage
extraction** — per active-universe entity, Claude researches the scientific founders / key inventors /
SAB via web_search and returns a structured roster → the `founder` table (schema **v3**), the join
surface the literature/independent-citation signal (§3.1/§5.2) will key on. Runs on the **cheap Haiku
tier** (`claude-haiku-4-5`, §5.5) with the **basic `web_search_20250305`** variant — it honors
`max_uses`, is ~10× faster than the dynamic `web_search_20260209`, and is the only web_search valid on
Haiku ([[feedback_claude_web_search_variant_and_output_cap]]). All the ported dispatch lessons apply
([[feedback_reuse_claude_dispatch_patterns]], [[feedback_persist_during_long_api_batches]]): forced
structured output (json_schema) sized to full `max_output_tokens` so JSON isn't truncated→dropped;
**Batch API default** (50%) with the batch_id persisted to `data/extract_batch_id.txt` before polling
for `--resume`; realtime async fan-out for small runs; **per-entity persist**; **skip-cache on identity
+ prompt version** (`extraction_prompt_version` bump re-opens everyone); a mandatory `[y/N]` cost gate +
`max_usd_per_run` hard guard; untrusted company text delimited as data (model output only writes the DB).
Cost estimate scaled by `cost_calibration_factor=0.10` [[project_anthropic_cost_calibration]]. Model IDs
+ pricing are current API facts (claude-api skill). **Live-validated (2 entities, ~$0.01, 22s):** real,
verifiable founders (Stuart Rich@Northwestern for Tenax; Michael Hays for NRC Health); where no
institution was found it returned "Unknown" — the never-invent discipline held on a real call.
**Status:** built 2026-07-11 (`clients/anthropic_client.py`, `extraction.py`, `scripts/8_extract.py`).
**Deferred:** literature signal (§3.1 OpenAlex, keyed on these founders), independence classification
(§5.2), the D3 stack-convergence scoring rubric (§5.4). Full sweep not yet run (~$0.10–0.50 at scale).

## D7 — Ownership-crossing signal via EDGAR full-text (efts), fund-first; the M7 13D/G gap closed
**Spec ref:** §3.5 headline. **Decision:** the specialist-fund 5%+ crossings that M7 structurally
couldn't get (13D/G index under the *investor's* CIK, not the subject's) are captured via the EDGAR
**full-text** API (`efts.sec.gov`), whose hits carry *all* associated CIKs. **Fund-first**: search per
watchlist fund (~19 queries, not one per company) for SC/SCHEDULE 13D/G in the lookback window, then
match each filing's CIKs back to our universe (a fund isn't a biotech, so it never self-matches). Two
precision rails: the fund must appear in the filing's `display_names` (not a stray body mention), and
only universe CIKs are emitted. `signal_type="ownership_crossing"`, `source="edgar_fts"`; idempotent
(hash(entity, adsh, fund)); per-fund commit; in-run dedup for efts pagination overlap.

**KEY FINDING (empirical, 2026-07-10):** SEC **relabeled the forms** in its 2024–25 EDGAR
modernization — the old `SC 13D`/`SC 13G` labels return **zero** hits for 2026 (they match only
pre-~2025 filings); recent filings are `SCHEDULE 13D`/`SCHEDULE 13G`. We query **both** old+new labels
(`edgar_fts.OWNERSHIP_FORMS`) so the window spans the transition. Also: efts intermittently 500s under
load → `edgar_fts._get_json` retries 429/5xx (a dropped fund = a whole fund's crossings lost, e.g. Baker
Bros). **Live result:** 19 funds → 278 crossings on our universe in 180d (RA Capital 69, Perceptive 36,
Deep Track 33, OrbiMed 26, Baker Bros 18, …). **Status:** built 2026-07-10 (`clients/edgar_fts.py`,
`signals/ownership.py`, `8_signals.py --ownership`). **Deferred:** classify 13D (active) vs 13G
(passive) and new-position vs amendment (/A); fund watchlist is US-focused — add EU/JP/KR specialists.

## D6 — Phase 2 starts with the capital-markets signal (EDGAR), zero-LLM, keyed on CIK
**Spec ref:** §3.5, §8 Phase 2. **Decision:** the first signal ingester is **capital-markets** —
"your highest-value, most reliable structured source" (§3.5) and fully free/zero-LLM, so no spend and
no prompt-injection surface. It reads each active-universe entity's recent SEC filings (submissions
API, one call per CIK) and writes a `signal` row per **material form** within a lookback window (default
180d): SC 13D/G (5%+ ownership crossings), Form-4 (insiders), 8-K (material events), S-1/S-3/424B5/424B3
(registration/shelf/ATM raises). **Scope = active universe** (`is_live=1 AND below_floor=0 AND cik NOT
NULL`) — includes unknown-cap names (a fresh 13D on an unpriced micro-cap is exactly the signal), skips
known-below-floor. Idempotent (signal_id = hash(entity, accession, form)); per-entity commit
(crash-safe); bounded-concurrency fetch, main-thread persist.

**KEY FINDING (full run, 880 entities → 17,645 signals):** by-form = Form-4 12,544 · 8-K 4,413 · 424B5
311 · 424B3 185 · S-3 151 · S-1 41 — and **zero SC 13D/13G**. This is structural, not absence of events:
a 13D/G is filed under the *investor's* CIK (the fund), not the subject company's, so the submissions
API for a company never returns 13D/G *about* it. Form-4 (insider) IS indexed under the issuer CIK, so
those come through. **Consequence:** M7 captures insider activity, material 8-Ks, and capital raises —
but the §3.5 headline signal (specialist-fund 5%+ ownership crossings) requires the **EDGAR full-text
search API** (`efts.sec.gov`) keyed by subject + fund watchlist, which is now the top Phase-2 refinement
(not optional). SC 13D/G stay in `material_forms` (harmless; they'll match once efts is added).
Secondary: Form-4 volume is high (~14/entity/180d) → needs clustering before scoring.
**Status:** built 2026-07-10 (`signals/capital_markets.py`, `scripts/8_signals.py`); efts 13D/G = next.

## D5 — GLEIF LEI backfill is high-precision (exact-name), never overwrites, collisions → review queue
**Spec ref:** §2.3 (LEI-first join), phase1 §3.4/§8 Q1. **Decision:** backfill the Legal Entity
Identifier from GLEIF (free, no key) for entities lacking one — but **LEI is Module-8's strongest
identity key**, so a wrong LEI would cause a false merge on the next universe build. Therefore matching
is deliberately **high-precision / low-recall**: `gleif.pick_lei` accepts an LEI only when exactly one
candidate's *normalized* legal name equals the entity's (ISSUED-status tiebreak); zero/ambiguous → no
LEI. `set_lei` **never overwrites** an existing LEI. If the picked LEI is already held by a *different*
stored entity, it is **not set** — a `gleif_lei_collision` row is queued instead (it usually means the
two rows are the same company, a store duplicate to reconcile by hand), so we never create two rows
sharing one LEI. Live-validated: cleanly matches `Genmab→GENMAB A/S`, `Zealand→ZEALAND PHARMA A/S`;
correctly rejects fuzzy fulltext garbage (`Acumen`→PotNetwork/Rexam). Recall is conservative by design;
improve later (fuzzy-completions endpoint / country-less fallback) only if precision holds. **Status:**
built 2026-07-10; `scripts/8_enrich.py --lei`, opt-in, re-runnable (only touches LEI-less rows).

## D4 — Market-cap enrich via yfinance; the floor is a queryable `below_floor` flag, not a delete
**Spec ref:** phase1 §5, §8 Q2. **Decision:** `company_tickers.json` carries no market cap, so the
universe build leaves EDGAR-discovered names `mktcap_unknown`. A separate **enrich stage**
(`enrich.py` + `scripts/8_enrich.py`) fetches each unknown-cap entity's cap via **yfinance**
(LOCAL-ONLY ToS — swap for a licensed provider before public deploy, [[project_data_provider_switch]]),
converts to USD (`fx.py`, static illustrative rates incl. CAD for TSX names), and persists
`market_cap_usd` + a new **`below_floor`** column (schema **v2**, migration 2). The floor is now a
*queryable flag* (active universe = `is_live=1 AND below_floor=0 AND mktcap_unknown=0`), not merely an
audit row — so Phase-2 signal jobs can gate on it. Each ticker is **persisted immediately** (repo rule
for long API loops, [[feedback_persist_during_long_api_batches]]); a miss stays KEPT + `mktcap_unknown`
(missing ≠ small ≠ delete) with `enriched_at` stamped so it isn't retried every run. **Status:** built
2026-07-10; enrich is opt-in (`scripts/8_enrich.py`), re-runnable, resumable.

## D3 — Scoring framework: define the "stack-convergence" rubric fresh inside Module 8
**Spec ref:** §1, §5.4 ("feeds into … the existing `stack-convergence-biotech-screen-spec.md` scoring
framework"). **Decision:** that companion spec and the Satellos worked-example do **not** exist in this
repo (they came from a different session). Rather than block, Module 8 will **define its own**
stack-convergence rubric in `config/` when the scoring layer is built (Phase 2+), calibrated later
against known cases (Satellos/MSLE — which Module 6 already surfaced at rank 20 — plus others).
**Why:** unblocks universe + signals now; the scoring schema (§5.4) is the last thing built and the
easiest to slot in once real signal data exists. **Status:** deferred to Phase 2+, recorded here so the
dangling dependency is explicit and not silently assumed.

## D2 — Reuse Module 6's universe as a seed + priority tier; do not rebuild it, do not write into it
**Spec ref:** §2 (universe construction), §2.4 (integrate existing universe). **Decision:** Module 6
(`6_Biotech_platform_discoverer/data/store.db`, 614 live companies) is read **read-only** as the
"existing user universe" of §2.4. Its rows seed Module 8's entity table tagged
`in_existing_universe = true` (the §2.4 priority tier). Module 8 **never writes into M6's store** —
M6's cardinal-rule guarantee (delete only for `mktcap_out_of_band`/`not_live`) must stay intact.
**Why:** M6 already does multi-market listed-biotech enumeration + tiered Claude scoring and even
surfaced Satellos; rebuilding that wholesale is waste. **But M6 is not sufficient** — it is ~95% US
(584/614), floored at $50M–3B, and has **no CIK column and no founder-lineage fields**, which every
§3 signal join needs. So Module 8 keeps its **own** store and enriches beyond M6. **Status:** adopted;
M6-reader is a Phase-1 universe provider.

## D1 — Data store: SQLite (repo convention), not Postgres
**Spec ref:** §4 ("Postgres, not SQLite, given multi-market/multi-language volume"). **Decision:** use
**SQLite** (one `data/early_detection.db`, WAL mode, additive migrations tracked by `PRAGMA
user_version`), matching every other module in this repo (0/2/3/4/5/6/7). **Why:** the spec's own
volume estimate (§5.5: ~2–4k tracked entities, low-hundreds of signals/day after dedup) sits
comfortably inside SQLite's envelope; Postgres would add a server dependency and break the repo's
uniform "one gitignored `*.db` per module" convention for no measured benefit at this scale.
`tsvector`/OpenSearch full-text (§4) is likewise deferred — SQLite FTS5 covers ad-hoc query need if it
arises. **Revisit if:** concurrent scheduled writers genuinely contend, or the entity/signal volume
grows an order of magnitude beyond the §5.5 estimate. **Status:** adopted for Phase 1+.

---

### Standing constraints inherited from the repo (not Module-8-specific decisions)
- **Security** (repo-wide, per `SECURITY_AUDIT.md`): all fetched/scraped content is untrusted →
  SSRF allow-list + private-IP block on user/config URLs, 64 MiB capped reads, `defusedxml`,
  `html.escape` + http(s)-only hrefs in any report, secrets from `.env` never logged, parameterized
  SQL. Module 8 reuses M6's hardened `clients/_net.py` rather than reimplementing fetch.
- **yfinance is local-only** until a licensed provider is swapped in (repo-wide ToS constraint). Any
  Phase-1 market-cap read that uses it inherits this flag.
- **Claude cost discipline** (when Phase 2+ scoring lands): mandatory `[y/N]` cost gate before any
  dispatch, prompt caching of the static discipline prompt, Batch API for bulk, cheap tier for
  extraction / expensive tier only past a rules-based pre-filter (§5.5).
