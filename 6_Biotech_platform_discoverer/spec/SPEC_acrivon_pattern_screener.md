# SPECIFICATION — "Acrivon-Pattern" Listed-Biotech Screener

**Version:** 1.0 (build target for Claude Code)
**Author of intent:** André (institutional biotech equity research)
**Purpose of this document:** a complete, buildable specification. Claude Code should be able to construct the system module-by-module from this file. Where a decision is left to the builder, it is marked **[BUILDER DECISION]**. Where a value must be confirmed against live docs, it is marked **[VERIFY]**.

---

## 0. Purpose & scope

Build an automated, re-runnable pipeline that scans the universe of **listed** biotech/life-science companies and surfaces a ranked shortlist of companies matching the **"Acrivon pattern"**:

1. A **proprietary data-generation engine** (a wet-lab or sensor modality producing large, multidimensional, *proprietary* data — e.g. phosphoproteomics, spatial/single-cell omics, functional-genomic or perturbation screens, high-content imaging).
2. A **computational inference layer** that turns that data into actionable inference — ideally generative / zero-shot, optionally LLM/foundation-model-based.
3. **External or wet-lab validation** of that inference (the analog of Acrivon's InViKA loop and CPTAC/FLT4 origination test) — not just held-out metrics on the model's own training database.
4. **Mechanism anchoring** to one of the in-scope biological mechanisms (oncology-24 + autoimmune + GPCR-driven), with disruption potential.
5. A **translational bridge** (companion diagnostic, biomarker-defined clinical assets, or a clear discovery→drug path).

The output is a scored, audit-logged shortlist in the analyst's house format, refreshed incrementally.

### 0.1 Locked decisions (do not re-litigate in build)

| Decision | Value |
|---|---|
| Market-cap band | **USD 50M – 3B**, fully-diluted (incl. pre-funded warrants where available), FX-normalized, ADR/dual-listing deduplicated. |
| AI / foundation-model axis | **High weight, NEVER a gate.** A company with strong proprietary-data + validation + mechanism can rank highly using non-LLM ML. |
| Mechanism taxonomy | **All three branches**: oncology-24, autoimmune, GPCR-driven (single controlled vocabulary). |
| Embeddings | **Detect-and-reuse**: probe the Python environment for an already-available, suitable embedding capability and use it if fit-for-purpose; only install/configure a provider if nothing suitable exists. Log the selection. |
| v1 regions | **US + Sweden + Europe.** JPX (Japan) and KRX (Korea) are **architected but config-gated OFF** for v1 (flip a flag to enable in v2). They are deferred, not designed-out — no candidate is permanently lost, only not-yet-activated. |

### 0.2 The cardinal rule (drives the whole Stage-0 design)

**Stage 0 is the only stage that can lose a candidate invisibly.** Every later stage scores companies it has already seen; a Stage-0 false negative never appears to be debugged. Therefore:

> **A company is only ever DELETED for (a) market cap out-of-band or (b) liveness (delisted/halted/acquired shell). Every other narrowing is a FLAG + an audit-log entry, never a deletion.** Companies with missing data are KEPT and FLAGGED, never dropped.

---

## 1. Design principles

1. **Recall-safe upstream, precision-safe downstream.** Stage 0 maximizes recall (union of over-inclusive nets). Narrowing happens later, cheaply, deterministically, and reversibly.
2. **Evidence over marketing.** Score peer-reviewed publications, patents, conference posters, and clinical-trial registrations — not investor-relations adjectives. The AI axis cannot be lifted by press-release language alone.
3. **Audit everything.** Every cut, flag, and score writes a row to an audit log with a reason and a timestamp. Any filter must be answerable to "show me everything it removed and why."
4. **Reversibility.** Re-running a downstream stage with looser thresholds must not require re-harvesting. Raw evidence is persisted once; filters/scores are recomputed against it.
5. **Incremental by default.** Re-runs only re-harvest/re-score companies with *new* evidence since last seen.
6. **Cheap-to-expensive funnel.** Deterministic filters → embeddings (pennies) → tiered Claude (bounded $). Opus only touches contested finalists.
7. **Falsifiable.** A labeled seed set (known positives + negatives) is scored every run; precision/recall are reported. If the screen can't recover Acrivon/Tango/IDEAYA/Boundless and reject generic "AI-pharma" shells, it is not trusted.

---

## 2. System architecture

```
                         ┌─────────────────────────────────────────────┐
                         │            SQLite evidence store             │
                         │   companies · evidence · scores · audit_log  │
                         │   · review_queue · seed_labels · run_meta    │
                         └─────────────────────────────────────────────┘
                                       ▲            ▲
        ┌──────────┬──────────┬────────┴───┬────────┴───┬───────────┬──────────┐
        │ Stage 0a │ Stage 0b │  Stage 1   │  Stage 2   │  Stage 3  │ Stage 4  │ Stage 5
        │ universe │ hard cut │  TA filter │  harvest   │ embed-cut │  score   │ rank
        │ (union)  │ mktcap+  │ (logged,   │  evidence  │ (semantic │ (Haiku→  │ dedup
        │ of nets  │ liveness │ reversible)│  (APIs)    │  pre-rank)│ Sonnet→  │ persist
        │          │          │            │            │           │  Opus)   │ export
        └──────────┴──────────┴────────────┴────────────┴───────────┴──────────┘
```

- **Orchestrator** runs stages in order, but each stage is independently invocable against the store (`run_stage(n)`), enabling partial re-runs.
- **Idempotent stages**: re-invoking a stage with unchanged inputs produces no new writes (keyed on content hash + last-seen).

---

## 3. Configuration (`config/config.yaml`)

All tunables live here so re-runs with different thresholds need no code change.

```yaml
run:
  regions: ["US", "SE", "EU"]        # JPX/KRX deferred: add "JP","KR" in v2
  incremental: true
  max_companies_to_score: 200        # safety cap on Stage 4 inputs

market_cap:
  min_usd: 50_000_000
  max_usd: 3_000_000_000
  use_fully_diluted: true            # incl. pre-funded warrants where available
  keep_if_unknown: true              # NEVER drop on missing cap; flag instead

stage0a_nets:                        # union; a company passes if ANY net hits
  sector_codes:                      # BROAD set — not just "Biotechnology"
    sic: ["2836","2834","8731","3826","3829"]
    gics_industries: ["Biotechnology","Pharmaceuticals",
                      "Life Sciences Tools & Services","Health Care Equipment"]
    icb_equiv: ["Biotechnology","Pharmaceuticals","Medical Equipment",
                "Health Care"]
  index_membership: ["NBI", "Nordic Health", "Euronext Health"]   # per region
  name_keywords: ["therapeutic","therapeutics","bio","pharma","oncolog",
                  "genomic","proteomic","sciences","immun","onco","tx"]
  seed_lists: ["config/tracked_universe.csv"]   # analyst's existing names

stage1_filters:
  require_ta_tag: true               # but borderline -> review_queue, not delete
  allowed_stages: ["preclinical","phase1","phase2","phase3","platform"]
  deletion_allowed_reasons: ["mktcap_out_of_band","not_live"]   # ENFORCED

stage3_embedding:
  prefer_local: true                 # reuse env capability if suitable
  similarity_cut_keep_top_frac: 0.40 # keep top 40% by archetype similarity
  min_keep: 120                      # never cut below this (recall floor)

stage4_scoring:
  triage_model:  "claude-haiku-4-5-20251001"   # [VERIFY] current id
  score_model:   "claude-sonnet-4-6"           # [VERIFY] current id
  finalize_model:"claude-opus-4-8"             # [VERIFY] current id
  use_batch_api: true                # async batch for the Sonnet pass
  finalize_if_composite_between: [0.55, 0.75]  # contested band -> Opus

composite_weights:                   # sum need not be 1; normalized internally
  A_proprietary_data: 0.28
  B_compute_engine:   0.16           # high weight, but optional (never a gate)
  C_validation:       0.24
  D_mechanism:        0.16
  E_translation:      0.16

cost_controls:
  max_usd_per_run: 50                # hard stop; log and halt if exceeded
  anthropic_max_concurrency: 4
```

---

## 4. Controlled vocabulary (`config/taxonomy.yaml`)

A single mechanism vocabulary referenced by BOTH the embedding archetypes (§8) and the Claude `D_mechanism` field (§9). Each entry: `id`, `branch`, `label`, `synonyms[]`, `example_targets[]`. This file is the canonical mechanism list — keep it editable.

**Branch `oncology` (the 24):**
`ubiquitination_degradation`, `acetylation_hdac_bet_p300`, `methylation_histone_dna`, `methylation_synthetic_lethal_prmt5_mtap`, `sumoylation`, `glycosylation_oglcnac_siglec`, `adp_ribosylation_parp_tankyrase`, `oncometabolite_metabolic_reprogramming`, `rna_m6a_epitranscriptome`, `llps_condensates`, `chromothripsis`, `ecdna`, `tad_topology_ctcf_cohesin`, `integrins`, `yap_taz_hippo_tead`, `piezo_mechano`, `mhc_i_loss_antigen_presentation`, `immune_exclusion_caf_collagen`, `metabolic_immune_suppression_adenosine`, `splicing_sf3b1_rbm39`, `circrna`, `ribosome_biogenesis_pol_i`, `menin_kmt2a`, `lsd1_demethylase`.

**Branch `autoimmune`** (seed; extend freely): `tyk2_jak`, `tnf_il_axis`, `b_cell_depletion_degrader`, `treg_modulation`, `complement`, `s1p_modulation`, `oral_macrocyclic_peptide_il_blockade`, `tolerogenic_antigen_specific`.

**Branch `gpcr`** (seed; extend freely): `gpcr_orphan_deorphanization`, `gpcr_biased_agonism`, `gpcr_allosteric_modulation`, `gpcr_structure_cryo_em_platform`, `gpcr_peptide_ligand_discovery`.

Each entry should also carry a `maturity` hint (`validated` / `registrational` / `emerging` / `lab_stage`) used as a contextual prior in scoring (not a filter).

---

## 5. Pipeline stages

### 5.1 Stage 0a — Universe assembly (union of nets)

**Goal:** maximize recall. A company enters the pool if **ANY** net catches it (UNION, not intersection).

Inputs: exchange listing directories for in-scope regions; sector/industry code maps; index membership lists; `name_keywords`; `seed_lists`.

Nets (all configured in `stage0a_nets`):
- **Broad sector codes** — biotech + pharma + life-science tools + diagnostics + healthcare equipment + healthcare-tagged software. (The Acrivon-pattern company is frequently *mis-coded* as Tools/Equipment/Software — strict "Biotechnology" would drop it.)
- **Exchange health-index membership.**
- **Name/description keyword hit** (incl. non-EN equivalents where applicable).
- **Curated seed lists** (analyst's tracked universe, prestige-lab spinouts, prior shortlists).

Output: `companies` rows with `source_nets[]` recording which nets matched. No filtering beyond union membership. Expect an over-inclusive pool (thousands); this is intended.

**Recall posture:** maximum. Precision is irrelevant here.

### 5.2 Stage 0b — Hard cuts (the ONLY deletions in the pipeline)

Apply **only** unambiguous, recall-safe cuts:
- **Market cap** within `[min_usd, max_usd]`, computed on **fully-diluted** shares including pre-funded warrants where the data exists; FX-normalized to USD; ADR/dual-listing deduplicated (keep primary listing, link secondaries).
  - **Missing cap → KEEP + flag `mktcap_unknown`.** Never delete on missing data.
- **Liveness** — drop delisted / halted / acquired shells (deterministic, safe).

Deletions here MUST write `audit_log(reason ∈ {mktcap_out_of_band, not_live})`. The DAO **rejects any delete with a reason outside this set** (enforces the cardinal rule in code).

Output: enriched, capped, live pool (order ~hundreds to low-thousands).

### 5.3 Stage 1 — TA / biotech / stage filters (deterministic, logged, REVERSIBLE)

Now narrow on judgment-adjacent but still deterministic signals — **without deleting**:
- Tag each company against the taxonomy (§4) using keyword/synonym match over name + business description + (if harvested) MeSH/OpenAlex concepts. A company with no mechanism tag is **flagged `no_ta_tag` and routed to `review_queue`**, not deleted.
- Tag development stage (platform / preclinical / phase 1–3) from ClinicalTrials.gov sponsor lookup + filing text where available.
- Companies failing `require_ta_tag` are **retained in the store, marked `stage1_excluded=true` with a logged reason**, and excluded from the *default* Stage-2 harvest set — but a config flag (`include_excluded=true`) re-admits them without re-harvesting anything. This is the reversibility guarantee.

**Recall posture:** protected. Borderline → `review_queue`. Nothing deleted.

### 5.4 Stage 2 — Evidence harvesting

For each Stage-1-retained company, harvest a structured **evidence bundle** (§6 for clients, §7 for schema). Persist raw payloads (cached) + normalized fields. Sources: OpenAlex, ClinicalTrials.gov v2, patents (PatentsView US + optional Lens), EDGAR full-text (US), and IR/publications-page scrape (Stage-2 survivors only — this is where high-signal posters like KaiSR live, and they are not API-accessible).

Incremental: skip companies whose source cursors (latest publication date, latest trial update, latest filing) are unchanged since last run.

### 5.5 Stage 3 — Embedding semantic pre-rank & cut

Detect-and-reuse an embedding capability (§8). Build archetype vectors (§8.2). Embed each company's evidence bundle; rank by max cosine similarity to **positive** archetypes minus penalty for similarity to **negative** archetypes. Keep top `keep_top_frac` but **never below `min_keep`** (recall floor). Cut companies write `audit_log(reason="embed_low_similarity", score=...)` — reversible (re-rankable against stored embeddings).

### 5.6 Stage 4 — Tiered Claude scoring

The judgment layer — the part embeddings cannot do (distinguishing real proprietary-data + validation from press-release vapor). See §9.

- **Triage (Haiku):** cheap pass; assign coarse keep/kill + a 0–1 prior. Kills write reversible audit rows.
- **Score (Sonnet, batch):** full rubric JSON (§9.3) over triage survivors.
- **Finalize (Opus):** only companies whose composite lands in the contested band (`finalize_if_composite_between`) — re-score with the strongest model and an explicit adversarial pass.

### 5.7 Stage 5 — Rank, dedup, persist, export

Compute composite (§10), final ADR/name dedup, write `scores`, refresh `review_queue`, export shortlist to `outputs/` in the analyst's house format (dense prose + targeted tables; `[V]`/`[INF]` labels preserved from Claude output). Emit run summary + validation metrics (§13).

#### 5.6.1 Prestige-lab seed (`config/prestige_labs.yaml`)
Founder/SAB pedigree is one of the **highest-precision** signals and the hardest to automate (Acrivon's tell was the Olsen/Mann lineage). Maintain a curated list of prestige labs / major awardees / foundation-model-in-biology authors (e.g. NAS members; Gairdner/Heineken-tier laureates; Nobel-class such as Bertozzi for glyco). In Stage 4, match company founders/SAB against this list via OpenAlex author IDs + h-index percentile; surface matches as a scoring signal and in the memo. High manual upkeep, high discriminating power.

---

## 6. Data sources & clients

| Client | Source | Auth | Coverage | Notes |
|---|---|---|---|---|
| `listings.py` | Exchange directories + market-data vendor | vendor key | Listings, mktcap, shares, FX | US, Nasdaq Nordic, Euronext/DB/SIX/LSE. **[BUILDER DECISION]** pick a market-data provider (e.g. FMP / EODHD / yfinance for prototype). FD share counts may need filing cross-check. |
| `openalex.py` | OpenAlex REST | none (use polite pool, `mailto`) | Global, English | Works, authors, institutions, concepts, citation velocity. Primary science signal. ~10 req/s polite. |
| `clinicaltrials.py` | ClinicalTrials.gov **API v2** | none | Global | Sponsor→indications/phases/biomarker & companion-Dx language. JSON. |
| `edgar.py` | SEC EDGAR full-text search + submissions API | none (set `User-Agent`) | US only | 10-K "Business", S-1; richest filing text. |
| `patents.py` | **PatentsView** (USPTO, free) + optional **Lens.org** (token) | PatentsView none; Lens token | US free; global via Lens | Assignee=company; weight **method/platform** patents over composition-of-matter. |
| `ir_scraper.py` | Company IR "publications/posters" pages | none | Global | **Stage-2 survivors only.** High-signal posters/abstracts (AACR/ASCO/ASH) that no API exposes. Respect robots.txt; polite rate. |
| `anthropic_client.py` | Anthropic Messages API (+ Message Batches) | API key (env) | — | Tiered models; batch for Sonnet pass. **[VERIFY]** model ids, batch endpoint, limits at https://docs.claude.com/en/docs_site_map.md |

**Coverage insight to encode:** foreign-language *filings* are the weak link, but the strongest Acrivon-pattern signals — **peer-reviewed publications (OpenAlex) and ClinicalTrials.gov** — are English and global. A JPX/KRX company with the pattern remains detectable via its science even when its DART/EDINET filing is opaque. Reserve Claude's multilingual ability for a handful of high-signal foreign documents, not whole-filing translation. (Relevant when JP/KR are enabled in v2.)

---

## 7. Data model (SQLite — `store.py`)

```sql
companies(
  company_id TEXT PRIMARY KEY,        -- stable hash(name|primary_listing)
  name TEXT, primary_ticker TEXT, exchange TEXT, country TEXT,
  isin TEXT, lei TEXT,
  mktcap_usd_fd REAL, mktcap_unknown INTEGER DEFAULT 0,
  source_nets TEXT,                   -- JSON array (which Stage-0a nets hit)
  ta_tags TEXT,                       -- JSON array of taxonomy ids
  dev_stage TEXT,
  stage1_excluded INTEGER DEFAULT 0,
  is_live INTEGER DEFAULT 1,
  first_seen TEXT, last_seen TEXT
)

evidence(
  company_id TEXT, source TEXT,       -- openalex|ctgov|edgar|patents|ir
  cursor TEXT,                        -- latest-seen marker for incremental
  payload_hash TEXT, payload_json TEXT,
  fetched_at TEXT,
  PRIMARY KEY(company_id, source)
)

scores(
  company_id TEXT, run_id TEXT,
  model TEXT,                         -- which Claude tier produced this
  json TEXT,                          -- full rubric JSON (§9.3)
  A REAL,B REAL,C REAL,D REAL,E REAL,
  composite REAL, confidence REAL,
  PRIMARY KEY(company_id, run_id)
)

audit_log(
  ts TEXT, run_id TEXT, company_id TEXT,
  stage TEXT, action TEXT,            -- flagged|excluded|deleted|cut|scored
  reason TEXT, detail_json TEXT
)

review_queue(company_id TEXT PRIMARY KEY, reason TEXT, added_at TEXT)

seed_labels(company_id TEXT PRIMARY KEY, label TEXT)  -- positive|negative

run_meta(run_id TEXT PRIMARY KEY, started TEXT, finished TEXT,
         config_hash TEXT, cost_usd REAL, metrics_json TEXT)
```

**DAO guardrail:** `delete_company(reason)` raises unless `reason ∈ deletion_allowed_reasons`. This is the code-level enforcement of the cardinal rule.

---

## 8. Embedding detect-and-reuse (`embed/detect.py`)

### 8.1 Probe order (use the first suitable capability found; log the choice)
1. **Already-installed local model** — probe for `sentence_transformers` and a loadable model (e.g. a BGE/E5/GTE family model present in cache). If importable and a model loads, use it.
2. **Already-configured hosted provider** — if `voyageai` is importable and `VOYAGE_API_KEY` is set, use it; else if `openai` importable and `OPENAI_API_KEY` set, use its embeddings.
3. **Fallback (only if nothing suitable):** install a small local sentence-transformers model (CPU-friendly) and use it; log that a new dependency was added.

"Suitable" = produces fixed-length dense vectors, dim ≥ 384, handles ≥512-token inputs (chunk + mean-pool if needed), runs within the run's latency/cost budget. Note: **Anthropic does not provide a native embeddings endpoint** — do not attempt one; the Claude API is for §9 only. Record the chosen provider/model/dim in `run_meta`.

### 8.2 Archetypes
Build vectors from short, curated descriptions:
- **Positive anchors:** Acrivon (canonical), plus Tango, IDEAYA, Boundless Bio (mechanism-pure positives). See **Appendix B** for the distilled Acrivon feature breakdown that seeds this archetype.
- **Negative anchors:** generic "AI drug discovery" shell (buzzwords, no proprietary data), pure-CRO, pure-tools/instruments vendor.
- Company similarity score = `max_sim(positive) − λ·max_sim(negative)` (λ tunable). Rank; keep top frac with `min_keep` floor.

Embeddings also serve as a **retrieval layer**: at Stage 4, fetch the top-k most archetype-relevant evidence chunks per company so Claude reasons over signal, not raw dumps.

---

## 9. Claude orchestration & rubric (`scoring/rubric.py`, `clients/anthropic_client.py`)

### 9.1 Tiering
- **Haiku** triage (kill obvious non-matches; cheap 0–1 prior).
- **Sonnet** full rubric, run via **Message Batches** for throughput/cost. **[VERIFY]** batch API shape & limits.
- **Opus** only on contested-band composites; adds an explicit adversarial pass.

Model ids from current product knowledge — confirm before first run: triage `claude-haiku-4-5-20251001`, score `claude-sonnet-4-6`, finalize `claude-opus-4-8`. **[VERIFY]** at docs.claude.com.

### 9.2 Input per company
A compact JSON evidence bundle: identity + mktcap; publication summary (counts, top concepts, citation velocity, prestige-lab author matches); patent summary (method/platform vs composition); clinical summary (assets, phases, biomarker/companion-Dx language); IR-poster extracts; taxonomy tag candidates. Plus the retrieval-selected evidence chunks (§8.2).

### 9.3 Output JSON schema (validated; reject & retry on malformed)
```json
{
  "company": "", "ticker": "", "exchange": "", "mktcap_usd_fd": 0,
  "A_proprietary_data": {"score":0,"modality":"","scale_evidence":"","citation":""},
  "B_compute_engine":   {"score":0,"is_foundation_model":false,
                         "generative_evidence":"","citation":""},
  "C_validation":       {"score":0,
                         "type":["held-out|external-benchmark|wetlab-loop|prospective-clinical"],
                         "citation":""},
  "D_mechanism":        {"score":0,"mechanism_ids":[],"branch":"",
                         "disruption_rationale":""},
  "E_translation":      {"score":0,"companion_dx":false,
                         "clinical_assets":"","lead_phase":""},
  "moat_location":      {"data_vs_architecture":"data|architecture|mixed",
                         "rationale":""},
  "substance_check":    {"verdict":"substantive|marketing|mixed",
                         "disconfirming_evidence":""},
  "composite":0.0, "confidence":0.0, "memo":""
}
```

### 9.4 Two mandatory adversarial axes (encode the KaiSR lessons)
- **`moat_location`** — force a call on whether defensibility is **proprietary data** (Acrivon's 120k-phosphosite set) or a **commoditizable architecture** (the disclosed ESM-2 ensemble). Dock companies whose only asset is a replicable model.
- **`substance_check`** — adversarial by mandate (consistent with the analyst's sensitivity-analysis discipline): require the model to name the **disconfirming** evidence and flag when an AI claim is press-release vapor unbacked by publications/patents/wet-lab. **B and C cannot score high without external evidence.**

### 9.5 Rubric prompt (system prompt for the scoring call — drop-in)
> You are a skeptical biotech equity analyst screening for the "Acrivon pattern": a proprietary high-dimensional **data-generation engine** + a **computational inference layer** (optionally generative/LLM) + **external or wet-lab validation** + anchoring to an in-scope biological **mechanism** + a **translational bridge** (companion Dx or biomarker-defined clinical assets).
> Score ONLY from the supplied evidence (publications, patents, trial registrations, conference posters). Treat investor-relations adjectives as near-worthless absent peer-reviewed/patent/wet-lab backing.
> Apply these rules: (1) The AI/foundation-model axis (B) is high-value but NOT required — strong proprietary data + validation + mechanism can score well with non-LLM ML. (2) Distinguish a proprietary-DATA moat from a commoditizable-ARCHITECTURE one; an impressive model trained only on public databases is weak moat. (3) Reward validation that uses fresh/external ground truth (wet-lab loops, prospective clinical, independent benchmarks) over held-out metrics on the model's own training database. (4) For every company, state the strongest DISCONFIRMING evidence; if the "platform" is unbacked vocabulary, say so. (5) Carry inferred values as inferences, not facts; do not invent citations — if evidence is absent, score low and say why.
> Use the worked calibration example in **Appendix B** as the reference for correct scoring behaviour — especially the data-vs-architecture moat call and the requirement for fresh/external (not held-out) validation.
> Return ONLY the JSON object specified, no preamble.

---

## 10. Composite & confidence (`scoring/composite.py`)
- `composite = Σ wᵢ·scoreᵢ / Σ wᵢ` over A–E using `composite_weights`, each score normalized to 0–1.
- Apply a **moat penalty**: if `moat_location.data_vs_architecture == "architecture"`, multiply composite by a configurable factor (<1). If `substance_check.verdict == "marketing"`, apply a stronger penalty / route to `review_queue` rather than shortlist.
- `confidence` = function of evidence completeness (missing sources lower it) and tier (Opus-finalized = higher). Surface low-confidence shortlist entries distinctly.

---

## 11. Audit log & review queue
- Every stage writes `audit_log` rows for flags, exclusions, cuts, scores — each with `reason` + `detail_json`.
- Query helpers: `why_excluded(company_id)`, `removed_at_stage(n, reason)`, `review_queue_dump()`.
- `review_queue` collects borderline cases (`no_ta_tag`, `substance_check=mixed`, low-confidence high-composite). Human-reviewable; promotable back into scoring without re-harvest.

---

## 12. Incremental re-runs
- Each evidence source stores a `cursor` (latest publication date / trial update / filing accession). Stage 2 skips companies whose cursors are unchanged.
- Stages 3–5 recompute only for companies with new/changed evidence since the prior `run_id`.
- `run_meta` records config hash; a config change (e.g. looser thresholds) forces re-evaluation of affected stages against stored evidence **without** re-harvesting.

---

## 13. Validation harness (`validation/seed_eval.py`)
- `config/seed_labels.csv` holds **known positives** — Acrivon, Tango (TNGX), IDEAYA (IDYA), Boundless (BOLD); borderline AI-discovery: Recursion (RXRX), Schrödinger (SDGR), Relay (RLAY) — and **known negatives** — generic "AI-pharma" shells, pure CROs, pure instruments/tools vendors.
- Every run scores the seed set through the full funnel and reports **precision/recall + per-stage survival** of seeds. If a known positive is lost, the log shows the stage and reason (and, if lost at Stage 0, that is a spec-level failure to fix before trusting the run).
- Tune funnel thresholds until positives are recovered and negatives rejected. Persist metrics to `run_meta.metrics_json`. **The screen is not trusted until it passes this.**

---

## 14. Cost & rate-limit controls
- Per-source **token-bucket** rate limiters (`util/ratelimit.py`); respect OpenAlex polite pool, EDGAR `User-Agent`, IR robots.txt.
- Anthropic: bounded concurrency (`anthropic_max_concurrency`); Sonnet pass via Batch API for cost; Opus gated to contested band.
- **Hard run budget** `max_usd_per_run`: track estimated spend; on breach, halt gracefully, persist progress, log. Re-run resumes incrementally.
- Order-of-magnitude expectation [INF]: Stages 0–2 ≈ free (public APIs); embeddings ≈ cents; Sonnet scoring of ~150 companies ≈ low single-to-double-digit USD; Opus finalize adds modestly. Designed as a weekly monitor, not a one-shot.

---

## 15. Observability
- Structured JSON logs per stage (counts in/out, deletions vs flags, cost, wall-time).
- Run summary to stdout + `outputs/run_<id>_summary.md`: universe size at each stage, seeds recovered, shortlist size, top movers vs last run, cost.

---

## 16. Build milestones (suggested order)
1. `store.py` (schema + DAO guardrail) + `config` loading + `models.py`.
2. `stage0a` + `stage0b` + `listings.py` + `fx`/`dedup`; verify cardinal-rule enforcement with a unit test (attempt illegal delete → must raise).
3. `stage1` (taxonomy tagging + reversibility flag + review_queue).
4. `clients/` (OpenAlex, ClinicalTrials, EDGAR, PatentsView) + `stage2` + incremental cursors.
5. `embed/detect.py` + `stage3` + archetypes; confirm `min_keep` floor.
6. `anthropic_client.py` (tiering + batch) + `rubric.py` + `stage4` + JSON validation/retry.
7. `composite.py` + `stage5` + export format.
8. `validation/seed_eval.py`; iterate thresholds until seed precision/recall acceptable.
9. Incremental re-run + cost guardrails + observability polish.

Each milestone is independently testable against the store.

---

## 17. Open items / v2 roadmap
- **JPX + KRX enablement:** flip `regions` to include `JP`,`KR`; add DART/EDINET/TDnet adapters; lean on OpenAlex/ClinicalTrials for science signal; use Claude multilingual on a few high-signal foreign docs only.
- **Market-data provider** selection and FD/PFW share-count cross-check against filings **[BUILDER DECISION]**.
- **Lens.org** global patent enablement (token) beyond US PatentsView.
- **Prestige-lab seed** expansion + automated awardee ingestion (NAS/Gairdner/Heineken/Nobel feeds).
- **Conference-abstract ingestion** beyond IR scraping if a structured source becomes available.
- Optional: promote `review_queue` triage to a lightweight Haiku pass.

---

### Appendix A — Enforcement checklist (must hold at all times)
- [ ] No code path deletes a company for any reason other than `mktcap_out_of_band` or `not_live`.
- [ ] Missing market cap → kept + `mktcap_unknown`, never dropped.
- [ ] Stage 0a is a UNION of nets (any-hit passes), using the BROAD sector set.
- [ ] Every non-deletion narrowing writes an audit row and is reversible against stored evidence.
- [ ] AI axis (B) is weighted but never a gate.
- [ ] `min_keep` floor enforced at the embedding cut.
- [ ] Seed-set precision/recall reported every run; lost positives traced to a stage+reason.
- [ ] Run halts on `max_usd_per_run` breach with state persisted.


---

### Appendix B — Acrivon calibration example (reference for correct scoring)

The gold-standard worked example of *desired scoring behaviour*, distilled from the KaiSR poster (C053) and the analyst parse. It is a **few-shot calibration anchor for the rubric prompt (§9.5)** and the **seed for the canonical positive archetype (§8.2)** — not harvested evidence. Do **not** inject the full poster/markdown into per-company scoring inputs (token bloat + over-fitting to phosphoproteomics specifically). The point is to teach the *pattern*, transferable to spatial omics, perturbation screens, GPCR cryo-EM platforms, etc. Scores below are illustrative targets `[INF]`.

**Acrivon Therapeutics — target rubric output:**

| Axis | Target | Why |
|---|---|---|
| `A_proprietary_data` | **5** | AP3 phosphoproteomics engine; the durable moat is the **proprietary ~120,000-phosphosite in-house drug-response dataset** (acute drug-exposure profiling), not the algorithm. |
| `B_compute_engine` | **3 (capped, NOT 5)** | KaiSR = two ESM-2-fine-tuned transformers, ensemble, zero-shot — genuinely generative (originated FLT4 from CPTAC). But the **architecture is disclosed and replicable** from public DBs (PSP, iKiP-DB). High capability, weak architectural moat → high score is *capped*, never maxed. |
| `C_validation` | **5** | **InViKA** wet-lab loop (recombinant WEE1/PLK1 → MS ground truth; hypergeometric p ≈ 9e-65 / 6e-13) + external benchmark vs Phosformer/KolossuS on **independent** perturbation data + CPTAC/FLT4 survival origination. Fresh/external ground truth, *not* held-out AUPRC alone. |
| `D_mechanism` | **high** | Anchors to CHK1 / replication-stress (ACR-368) and WEE1/PKMYT1 (ACR-2316); maps to multiple in-scope oncology mechanisms. |
| `E_translation` | **5** | **OncoSignature** companion Dx (FDA Breakthrough Device) + biomarker-defined ACR-368 registrational path. |
| `moat_location` | **data** | Defensibility = the *withheld* 120k-phosphosite dataset + model weights, **not** the disclosed ESM-2 ensemble. |
| `substance_check` | **substantive** | Backed by posters / wet-lab / clinical. `disconfirming_evidence`: company-authored validation; held-out AUPRC partly recapitulates the training DB; ACR-368 = repurposed prexasertib → biomarker-attribution risk. |

**Calibration lessons the scorer must internalise:**
1. A model that merely **recapitulates public databases** (high held-out AUPRC) does **not** by itself earn a high `B` — require fresh/external validation (the InViKA test).
2. **Reward the data moat; penalise architecture-only moats.** An impressive *disclosed* architecture caps `B`, it does not max it.
3. `C` is earned by **wet-lab loops / prospective clinical / independent benchmarks**, not by held-out metrics on the model's own training source.
4. **Transfer the pattern, not the modality.** Apply the same logic to any proprietary high-dimensional data engine + inference layer + external validation, regardless of assay type.

*Secondary illustration (optional):* the companion A097 poster (ACR-2316 / PLK1 causal-rescue) is the mirror-image validation signal — wet-lab experiments **causally confirm** what the model predicts computationally. Where such "model predicts → bench confirms" loops exist, weight `C` up. (Grounded in the analyst parse; only C053 was supplied as a primary file.)
