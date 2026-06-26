# 5_Hype_parser — Decisions log

Append-only record of *why* the build deviates from or calibrates the spec. The spec
(`Overall_specification.md`) describes the target; this file records what was decided/built and
why. Mirrors the repo discipline (`2_Funds_parser`, `3_Biopharmcatalyst_parser`,
`4_List_renderer` all keep a `decisions.md`). Update at every step.

Conventions: `D<n>` = a locked decision; `OD-<n>` = an open decision still to be made.

---

## D1 (2026-06-26) — Framework v0.1 locked

The conceptual framework in `Overall_specification.md` is accepted as the foundation. Locked
elements (do not re-litigate without a superseding decision):

- **Orthogonality** — cheapness and hype-potential are independent modules + a combiner (§2.1).
- **Multiplicative hype** — `HYPE = Legibility × ThematicHeat × AttentionAccel × Convexity ×
  NarrativeRealization`; any factor ≈ 0 ⇒ product ≈ 0 (§2.2). Validated by the TScan disqualify.
- **Potential − kinetic** — buy latent narrative minus realized attention (§2.3).
- **Theme-first dynamic universe** — detect nascent sub-themes, expand to constituents, screen the
  union; never screen the whole market (§2.4). Sub-theme granularity is load-bearing (§3).
- **Dual-regime via `max()`** — catalyst is a factor, not a gate; `NarrativeRealization =
  max(discrete, continuous)` and `Convexity = max(microstructure, re-rating)` (§2.5, §5).
- **Un-bid-ness replaces cheapness** — Module A passes Value-mode OR Re-rating-headroom-mode;
  the latter requires the earliness triple-lock or is dropped (§4).
- **Source-registry taxonomy + FDA layer** — selection by inverse packaging/cost; juried
  awards / FDA designations = donated specialist curation; registry schema per §6.2.
- **diffusion_ratio as the core instrument** — one mechanism defines the universe AND times the
  entry; synonymy solved by embeddings, not generation (§7).
- **Build economics** — LLM at the narrow end only; the diffusion engine is keyword + local
  embeddings (sentence-transformers), zero Claude; Claude reserved for theme labeling,
  constituent extraction, Legibility scoring, under Batch + prompt caching, ~$5–15 envelope (§8).
- **Calibration verdicts** — the 7 cases in §10 set *structure* (multiplicative form + the two
  generalizations), never parameters.

**Explicitly NOT locked (require the §12 open artifacts):** feature formulas, normalization,
thresholds/θ, labeling protocol, backtest mechanics. No parameter is trustworthy until a labeled
point-in-time panel exists (§11).

**Why:** the user supplied a deliberately framework-only spec; locking structure now lets the
build proceed on the engine (no Claude) without prematurely fixing weights that need data.

---

## D2 (2026-06-26) — Features v0.2 + Protocol v0.2 accepted

Two complements added to `spec/`: `features_spec_v0.2.md` (operational definitions + formulas for
every feature, incl. the full `diffusion_ratio` instrument) and `protocol_spec_v0.2.md` (panel
construction, PIT discipline, fitting, validation, weekly run). Together they **resolve open
artifacts #2–#5** from v0.1 §12 at the specification level (parameters remain unfit by design).

Key elements now locked at the spec level (instantiation still pending the panel):

- **`diffusion_ratio` operationalized** (Features §1): membership by cosine ≥ `τ_member` to a
  sub-theme centroid; specialist momentum `β_spec` = OLS slope of `ln(1+N_spec)`; mainstream
  penetration `p_main = N_main/(N_spec+N_main)` as the bounded S-curve position; nascency gate
  `β_spec ≥ β_min ∧ p_main ≤ p_max`; survival filter = persistence + bridge-leakage. Synonymy is
  embedding geometry, **no synonym dictionary**. New-theme discovery = HDBSCAN + one Claude label.
- **Every feature has a formula** (Features §2–3): Module A auto-selects the multiple; B1–B5 defined;
  `HYPE = B1·B2·B3·B4·B5` with **no additive fallback** (TScan is the regression test).
- **All free constants are deferred** — marked ⚙, collected in Features §5; "no constant in this
  document is set." Nothing is fit until the panel exists.
- **Cardinal PIT rule** (Protocol §1): every panel feature at `t0` uses only data timestamped ≤ `t0`
  against the registry version frozen ≤ `t0`; **delisted-inclusive** universe (no survivorship);
  first-print fundamentals; registry add-dates gate source availability. One violation invalidates
  the parameter it touches.
- **Leak-free `t0`** (Protocol §2.2): `t0` = first week a name mechanically satisfies both gates —
  timing is set by PIT features, never by outcome knowledge.
- **Kill-switch test** (Protocol §4): regress `forward_return ~ Legibility×ThematicHeat + controls`
  on the panel; if the narrative composite has no incremental power after controls, **stop** — the
  program collapses to value+momentum. Pre-register the pass condition.
- **Acceptance criteria** (Protocol §8): QUALIFY must beat all four benchmarks out-of-sample —
  especially **cheap+catalyst** — plus decile monotonicity, anchor reproduction (incl. TCRX ≈ 0),
  free-params ≪ n, manipulation filter catches known pumps.
- **Critical path** (Protocol §9): Registry instance (#1) ∥ diffusion params (#2) → labeled PIT
  panel → kill-switch → fit → acceptance → weekly production. **The panel is the gating artifact.**

**Why:** these convert the framework into a buildable, falsifiable plan with the premise-test up
front, before any feature-fitting spend. `decisions.md` here also serves the role Protocol §6 calls
`implementation_decisions.md` (single canonical log, matching the other four components).

---

## D3 (2026-06-26) — Standalone architecture (resolves OD-1)

5_Hype_parser is built **standalone**, not on top of `4_List_renderer`'s adapter/SWR/embedding
substrate. It owns its full stack: its own SQLite DB(s) under `data/`, its own ingestion clients,
its own local embedding store (sentence-transformers), its own cost-gated Claude runner.

Reuse is limited to **repo-shared, framework-neutral pieces** (this is hygiene, not coupling):
- the existing **EDGAR client + rate limiter** (`2_Funds_parser/src/module_4c/edgar_client.py`,
  `3_Biopharmcatalyst_parser` M2) — wrap/import rather than re-implement SEC fetching + the
  10 req/s limiter;
- existing **signal outputs** the spec already calls for — 13F new-position clustering (§6.6),
  Form 4 / fundamentals — consumed as data, not as a code dependency.

**Why:** keeps the diffusion engine + panel machinery self-contained and independently testable;
avoids entangling this project's PIT/backtest discipline with Curator's live-serving concerns;
the embedding needs differ (corpus-wide document clustering here vs. per-item ranking in Curator),
so a shared embedding layer would be a forced fit. Re-evaluate only if duplication becomes painful.

---

## D4 (2026-06-26) — M1 source registry built (open artifact #1, schema + seed)

First code. The frozen source-registry artifact (§12 #1) at the schema + seed level.

- **`data/hype.db` schema v1** (`src/hype_parser/db.py`, additive migrations via `user_version`):
  `sources` (the §6.2 registry fields + `history_availability` + `tier` + `enabled`/timestamps) and
  `registry_versions` (immutable, content-hashed PIT snapshots).
- **`src/hype_parser/registry.py`** — CRUD + `seed_from_config` (idempotent on `source_id`,
  all-or-nothing validation of the enum fields) + `freeze`/`get_version`/`list_versions` (snapshot +
  sha256 content hash for PIT pinning per Protocol §1).
- **`config/sources.yaml`** — **70 sources** seeded from spec §6.3–6.6 (Tier-0 backbone, FDA layer,
  Awards A/B/C, threshold events, capital-commitment, VC). `scrapeability_verified=0` for all
  (claimed-not-verified, §6.7).
- **`scripts/5_registry.py`** — `--seed/--list/--edge/--all/--verify/--freeze/--versions`.
- **14 tests pass** (`tests/test_registry.py`), incl. a smoke test seeding the real config.
- Seeded + frozen as `v1` (content hash `8efa7f5e32b4`).

**OD-2 quantified:** of 70 sources, **31 `queryable` / 39 `forward_only`**. The panel era is
reconstructable now from the 31; the 39 (award taxonomies, FDA BTD/Fast-Track real-time,
WG-charter/conference diffs, job postings, VC RSS) cannot be rebuilt — they enter the panel only if
a forward archive starts now. This makes OD-2 a live, costed decision rather than a vague worry.

**Why:** the registry is the moat *and* the bias (§6.7); freezing it (versioned, hashed) before any
backtest window is the precondition for PIT honesty. Seeding the full list now (vs. trickling
sources) is deliberate — the registry must be frozen *before* the panel to avoid hindsight source
selection (§11).

---

## D5 (2026-06-26) — OD-2 forward archive built (resolves OD-2)

A standalone weekly snapshotter for the 39 `forward_only` sources whose history is otherwise
unrecoverable. Stands up the OD-2 insurance the PIT rule (Protocol §1) demands.

- **`data/hype.db` schema v2** — `source_snapshots (snapshot_id, source_id, fetched_at, url,
  http_status, content_hash, bytes, content, changed, error)`. **Content stored only when its
  sha256 changes** (taxonomies move ~annually) → bounded growth; a metadata cadence row is still
  written every run.
- **`src/hype_parser/archive.py`** — `snapshot_source` (fail-open, change-detecting),
  `archive_forward_only` (scoped to enabled `forward_only`), `snapshot_stats`. Injectable
  `http_get` for tests.
- **`scripts/5_archive.py`** (`--run/--dry-run/--source/--limit/--stats`) + **`run_5_archive.bat`**
  (schedule weekly via Task Scheduler).
- **9 tests pass** (`tests/test_archive.py`); 23 total in the suite.
- **Dry-run:** 34 / 39 forward_only sources are fetchable today; **5 have no URL yet**
  (`fda_btd`, `fda_fast_track`, `conference_tracks`, `design_awards`, `job_postings_ats`) — they
  need per-source fetch specs (FDA = 8-K/PR, etc.) later. **Not yet run live** (a `--run` makes
  outbound requests to 34 external sites; first live capture is a user action).

**v0.1 scope:** snapshots the source's *registered URL* as-is; refining the exact taxonomy/list
page + pagination per source is later work, tracked with `scrapeability_verified`.

**Why:** every week without this loses panel-era history for >half the registry. The snapshotter is
small, independent, and fail-open, so starting it now is cheap insurance against an unrecoverable
loss (OD-2).

---

## D6 (2026-06-26) — Ingestion order for the diffusion engine (M2 plan)

The order in which sources are wired into the zero-Claude ingest + embedding + `diffusion_ratio`
engine (open artifact #2). Ordered by: PIT-queryable history depth, leading-indicator value,
cross-sector breadth, embedding-richness (text to embed), API robustness (API > scrape), and
ticker-linkage for later constituent expansion. **Denominator sources are Wave 1 because
`diffusion_ratio` is undefined without a mainstream bottom.**

| Wave | Sources | Why this wave |
|---|---|---|
| **1 — MVP slice** | arXiv (numerator) + **GDELT + Wikipedia pageviews** (denominator) | Smallest set that makes `diffusion_ratio` computable end to end; cleanest APIs; arXiv abstracts are ideal embedding fodder; covers tech/AI/quant-bio. |
| **2 — biotech anchors** | bioRxiv/medRxiv + ClinicalTrials.gov v2 | The anchor cases (ELTX/TCRX/CRISPR) are biotech; adds the second specialist domain + registration signal (feeds B5 discrete later). |
| **3 — builder + filings** | Hacker News (Algolia) + EDGAR full-text search | Builder mindshare (software/infra themes — ROKU/NET-style) + theme-keyword emergence in filings with direct ticker linkage (reuses shared EDGAR client, D3). |
| **4 — IP + public capital** | USPTO PatentsView + NIH RePORTER / NSF / SBIR | Corroborating leading signals; deep queryable history; rich text; funding-into-theme for B2. |
| **5 — bridge layer** | ETF holdings + N-1A, 13F new-positions, earnings transcripts, Product Hunt, regulations.gov, GitHub stars, Stack Overflow, Europe PMC | The `N_bridge` inputs the Stage-2 survival filter needs (leakage test); lower-priority/overlapping last. |

`forward_only` sources (D5 archive) fold in as their forward archives accrue depth. Each wave is
independently runnable and testable; Wave 1 is the immediate next build.

**Why:** front-loads the cleanest, highest-history, highest-embedding-value sources so the engine
is provable on a vertical slice before breadth is added; orders biotech early to exercise the
anchor cases; defers scrape-fragile/overlapping sources.

---

## D7 (2026-06-26) — M2 diffusion engine Wave 1 built (open artifact #2, zero Claude)

The diffusion radar end to end, no LLM (Features §8 economics). Proves the instrument on a
vertical slice before any Claude spend.

- **`data/hype.db` schema v3** — `themes`, `documents` (corpus + embedding BLOB), `theme_documents`
  (per-theme membership + cosine), `theme_series` (monthly N_spec/N_main/wiki_views).
- **Ingest** (`src/hype_parser/ingest/`): `arxiv` (specialist numerator; **date-windowed per-year
  fetch** so N_spec has monthly history — the newest-N approach collapsed a hot theme into one
  month), `gdelt` (mainstream N_main, TimelineVolRaw, retry/backoff on 429), `wikipedia`
  (pageviews level). All take an injectable `http_get` (tests run offline).
- **`embed.py`** — pluggable local embedder (Features §1.1): `SentenceTransformerEmbedder` if
  `sentence_transformers` is importable, else `HashingEmbedder` (word/bigram hashing, lexical).
- **`diffusion.py`** — `β_spec` (OLS slope of ln(1+N_spec) over L), `p_main`, `diffusion_ratio`,
  informational `nascency_gate`. Pure functions.
- **`themes.py`** storage; **`render_radar.py`** → self-contained inline HTML at
  `_intermediate_outputs/radar_report.html` (Protocol §6); **`scripts/5_radar.py`** (SWR cache,
  `--theme/--refresh/--no-fetch/--render-only/--open-browser`); **`run_5_Hype_radar.bat`**.
- **Config:** `themes_seed.yaml` (3 seed sub-themes: rag, ssm_mamba, mkras_vaccine — tech-leaning
  to suit arXiv; biotech one deliberately shows the source-domain gap → motivates Wave 2);
  `diffusion.yaml` (all ⚙ params: `tau_member=0.20`, `L=12`, `beta_min=0`, `p_max=0.5`).
- **40 tests pass** (`tests/test_diffusion.py` added: embedder, diffusion math, ingest parsers,
  storage/membership).

### D7a — Embedder is pluggable; hashing fallback is a placeholder
No `sentence-transformers`/`torch` in the repo venv (multi-GB install). To keep Wave 1 runnable
*today*, the default embedder is a deterministic lexical hashing vectorizer — real cosine geometry,
**not semantic**. It is labelled as a placeholder in logs + the HTML banner. Installing
`sentence-transformers` makes the production semantic encoder a one-line swap (`embed.model` in
`diffusion.yaml`). The **embed model is a frozen per-registry-version parameter** (Features §1.1);
switching it invalidates cached embeddings (re-embed on model change is built in).

**Why:** unblocks the §8 "engine first, no Claude" path without waiting on a heavy dependency;
the lexical fallback proves the full pipeline (ingest → embed → membership → series → HTML) on real
arXiv data; the semantic model swaps in cleanly later. All thresholds stay ⚙ until the panel.

**Observed (live, placeholder embedder):** arXiv + Wikipedia fetch cleanly; GDELT rate-limits (429)
and fails open. The lexical embedder + `tau=0.2` gives usable-but-noisy membership — expected; the
report's job here is to show the instrument runs, not to trade.

**Seed fix (2026-06-26):** the `mkras_vaccine` Wikipedia title `KRAS` 404s on the pageviews API;
changed to `Cancer vaccine` (resolves + on-theme). mKRAS now renders a mainstream-attention curve
but `N_spec=0` — arXiv has ~no KRAS-vaccine papers, so its specialist numerator stays empty until
Wave 2 (bioRxiv/ClinicalTrials, D6). That empty-numerator state is the intended source-domain gap,
now shown as a real card (with the Wikipedia series) rather than a blank one. NB: running the radar
with `--theme X` renders a SINGLE-theme report (overwrites the full report) — omit `--theme` (or use
the render-only bat) for the all-themes view.

**GDELT robustness (2026-06-26):** GDELT's free DOC API rate-limits hard (persistent HTTP 429), so
`N_main` (the diffusion denominator) often comes back empty. Two fixes: (a) **non-destructive** — a
failed GDELT/wiki fetch no longer overwrites prior good values with zeros (preserves the cache); (b)
**pacing** — `gdelt_pause_s=4` before each call + `gdelt_retries=3`/`gdelt_backoff_s=8` (config).
GDELT 429 is transient; a later `--refresh` run when it cools populates `N_main`, and the cache then
keeps it. The report is still meaningful without it: `N_spec`/`beta_spec` (the alpha-bearing
specialist numerator) are populated; only `p_main` (mainstream penetration) waits on GDELT.

---

## D8 (2026-06-26) — Two bats: one runs the pipeline + renders, one renders only

User found the prior two bats (separate archive + radar) confusing. Consolidated to exactly two,
matching how the user thinks about it:

- **`run_5_Hype_parser.bat`** — the FULL pipeline + render: step 1 forward archive
  (`5_archive.py --run --if-stale-days 7`, self-skips if snapshots are <7 days old, so it is cheap
  to call every run instead of crawling 34 sites each time); step 2 the radar
  (`5_radar.py --open-browser`: ingest → embed → diffusion → render → open). Extra flags pass
  through (e.g. `--refresh`).
- **`run_5_Hype_render.bat`** — RENDER ONLY: `5_radar.py --render-only --open-browser` — rebuilds +
  opens the HTML from cached `hype.db` data, no network, no recompute of ingestion.

Added `--if-stale-days N` to `5_archive.py` (skips a run when the most recent snapshot is younger
than N days). Deleted `run_5_archive.bat` and `run_5_Hype_radar.bat`.

**Why:** the user asked for exactly one "pipeline + render" bat and one "render-only" bat. Folding
the (gated) archive into the pipeline keeps "run the pipeline" complete without a third bat, and the
staleness gate prevents redundant outbound crawls on frequent ad-hoc runs.

---

## D9 (2026-06-26) — M2 diffusion engine Wave 2: biomedical specialist sources

Adds the biomedical specialist numerator so biotech themes (the ELTX/TCRX/CRISPR anchors) get a
real `N_spec`, closing the Wave-1 source-domain gap. Still zero Claude.

- **Europe PMC** (`ingest/europepmc.py`) is used as the searchable route to **bioRxiv/medRxiv +
  PubMed**. D6 named "bioRxiv/medRxiv"; the raw bioRxiv API is **date-dump only (no keyword
  search)**, so a keyword-driven numerator needs Europe PMC, which indexes those preprints plus
  PubMed and returns abstracts + dates. cursorMark paging, per-year `FIRST_PDATE` windows (history).
- **ClinicalTrials.gov v2** (`ingest/clinicaltrials.py`) — each study is a document timestamped by
  its first-posted date (registration = specialist activity; later feeds B5 discrete). pageToken
  paging.
- **No new tables.** Both sources write into the existing `documents` corpus with
  `source_id ∈ {europepmc, clinicaltrials}`; embeddings + centroid membership are unchanged, so
  their docs extend `N_spec` exactly like arXiv. A theme may set any subset of `arxiv_query /
  europepmc_query / ctgov_query`; the union feeds one membership pass.
- **Seeds:** `mkras_vaccine` gained `europepmc_query` + `ctgov_query`; added a new
  `crispr_gene_editing` theme (CRSP/NTLA/BEAM cohort).
- **Report:** each card now shows "member docs by source" (e.g. `arxiv 12, europepmc 30, ctgov 8`).
- **Config:** `europepmc_max_per_year=150`, `ctgov_max_results=300` (`diffusion.yaml`).
- **48 tests pass** (`tests/test_ingest_wave2.py` added — Europe PMC + CTgov parsers/paging).

**Why Europe PMC instead of raw bioRxiv:** same preprints, but keyword-searchable with abstracts and
dates — required for a theme-driven numerator. Recorded as a deliberate deviation from the D6 label.

**Why no schema change:** the Wave-1 corpus model is source-agnostic; adding sources is purely an
ingest concern. Keeps Modules/membership/series identical across waves.

---

## D10 (2026-06-26) — M2 diffusion engine Wave 3: builder mindshare + filing emergence + tickers

Adds the two Wave-3 sources (D6), handled differently because their data shapes differ. Still zero
Claude. Schema bumped to **v4** (`theme_series.edgar_filings` column + `theme_tickers` table).

- **Hacker News** (`ingest/hackernews.py`, HN Algolia) is a **document source** with rich text, so
  it joins the corpus (`source_id='hackernews'`) and feeds `N_spec` through the normal embedding
  membership path — exactly like arXiv/Europe PMC. Per-year `created_at_i` windows for history.
- **EDGAR full-text search** (`ingest/edgar_fts.py`) is **not** a document source — its API
  keyword-matches server-side and returns no embeddable abstract. It is used as:
  1. a **separate yearly filing-count series** (`hits.total.value` per year) stored in the
     `theme_edgar` table (schema **v5**), kept **distinct from N_spec** (a corporate/capital
     signal, not specialist literature) and shown as its own sparkline; and
  2. **ticker linkage** — tickers parsed from each hit's `display_names` → `theme_tickers`
     (the first concrete bridge toward constituent expansion / stock-picking).
  Queried **per year** (~9 requests/theme) with retry+backoff: the FTS endpoint returns frequent
  intermittent HTTP 500s under load, and per-month (~100 requests/theme) was both slow and fragile.
  Yearly granularity is fine for this secondary trend; the high-value output is the tickers. (The
  unused `theme_series.edgar_filings` column from v4 is left in place.)
- **Seeds:** `hn_query` added to the tech themes (rag, ssm_mamba); `edgar_query` added to all four.
- **Report:** EDGAR sparkline + a "Tickers mentioning in SEC filings" line per card.
- **56 tests pass** (`tests/test_ingest_wave3.py` added).

**Why HN and EDGAR are handled differently:** membership-by-embedding only makes sense for sources
that return real text. HN does; EDGAR FTS does not (it returns matched-filing metadata + company
names). Forcing EDGAR through the embedding path would silently drop it (low cosine on a bare
company name). So EDGAR is modelled honestly as a keyword-count series + a ticker extractor.

**EDGAR client (D3 note):** Wave 3 uses a minimal local FTS client (one endpoint, SEC User-Agent,
<=10 req/s) rather than importing the cross-project shared EDGAR client, to keep 5_Hype standalone.
Heavier EDGAR data (companyfacts / Form 4) in a later module would reuse the shared client.

---

## D11 (2026-06-26) — M2 diffusion engine Wave 4: public capital + IP

Adds the Wave-4 sources (D6) — all **document sources** that feed `N_spec` through the existing
membership path (no schema change), like Europe PMC / Hacker News. Still zero Claude.

- **NIH RePORTER** (`ingest/nih_reporter.py`, keyless POST) — funded biomedical projects;
  strong for crispr/mKRAS. Verified live (returns real CRISPR grants).
- **NSF Awards** (`ingest/nsf.py`, keyless GET) — research funding; science/tech. Verified live.
- **SBIR.gov** (`ingest/sbir.py`, keyless GET) — small-business R&D awards, cross-sector. Works but
  rate-limits (HTTP 429) like GDELT; fail-open.
- **USPTO PatentsView** (`ingest/patentsview.py`) — the current API requires a free key
  (`X-Api-Key`); **skipped gracefully** when `PATENTSVIEW_API_KEY` is absent (read from env or the
  repo-root `.env` by the orchestrator). With a key, patents feed N_spec too.
- **Theme fields:** `nih_query / nsf_query / sbir_query / patents_query` (any subset). Seeds: NIH on
  biomed themes, NSF on tech themes, SBIR + patents on all.
- Award amounts are captured in the doc text but the engine only uses **doc counts** for N_spec; a
  $-weighted "funding into theme" signal for B2 (ThematicHeat) is a later module.
- **68 tests pass** (`tests/test_ingest_wave4.py` added).

**Why these are document sources (not a separate series like EDGAR):** grants/patents return real
title+abstract text, so embedding membership works and they belong in `N_spec` (specialist
activity). EDGAR FTS (no text) had to be a separate count; these don't.

**All five source waves of the diffusion engine are now built** (D7 arXiv; D9 Europe PMC +
ClinicalTrials; D10 Hacker News + EDGAR; D11 NIH/NSF/SBIR/PatentsView). Remaining D6 Wave 5 (bridge
layer: ETF/13F/transcripts/Product Hunt/regulations.gov) is optional polish; the higher-leverage
next step is the **labeled PIT panel + kill-switch** (Protocol §2–4) before any parameter is trusted.

---

## D12 (2026-06-26) — Labeled PIT panel + kill-switch INFRASTRUCTURE (Protocol 2-4)

Built the gating-artifact infrastructure (not yet a valid panel — that needs n>=100). Schema **v6**:
`panel` (name/ticker/t0/regime/theme/mispricing_mode/label/label_source), `panel_returns`
(per-horizon forward return + max draw-up/down), `panel_features` (PIT snapshot for the kill-switch).

- **`prices.py`** — yfinance forward-return engine (injectable; local-personal only until a licensed
  provider is swapped). Per-horizon fwd return + draw-up/down; skips horizons that haven't elapsed.
- **`panel.py`** — storage + `seed_anchors`.
- **`killswitch.py`** — numpy OLS (no statsmodels) of `fwd_return ~ narrative + controls` with the
  **pre-registered** pass condition (positive coef, |t|>=2, n>=min_n). Refuses a verdict when
  underpowered.
- **`config/panel_anchors.yaml`** (7 anchors, pre-registered verdicts) + **`config/panel.yaml`**
  (horizons, pre-registered kill-switch condition). **`scripts/5_panel.py`** CLI.
- **81 tests pass** (`tests/test_panel.py` added).

### D12a — Anchor forward returns (live) partly validate, and expose the t0 problem
Computed real forward returns from each anchor's hand-set t0:

| Anchor | label | 13w / 26w / 52w | reads as |
|---|---|---|---|
| ROKU | positive | +111 / +188 / **+312%** | strong run ✓ |
| NET | positive | +35 / +114 / **+346%** | strong run ✓ |
| SNAP | positive | +16 / +127 / **+218%** | strong run ✓ |
| CRSP | positive | −33 / +34 / **+158%** | run after a dip ✓ |
| TCRX | hard_negative | +3 / −1 / (n/a) | flat ✓ (the TScan trap) |
| **SOFI** | positive | −37 / −24 / **−67%** | **did NOT validate** |
| **ELTX** | positive | −52 / −34 / −2% | **did NOT validate** at this t0 |

4/6 positives validate strongly and TCRX confirms as flat. **SOFI and ELTX do not** — because their
hand-picked `t0` is wrong (SOFI 2021-06 was near the post-SPAC top, not the base; ELTX's run sits
outside the 2024-06 + 52w window). This is exactly **Protocol 2.2's lesson**: `t0` must be set
**mechanically** (first week both gates fire), never by hand — hand-placement smuggles in look-ahead
*and* mis-timing. We deliberately do NOT massage the t0s/labels to look right (that would be the
hindsight overfitting Protocol 11 warns against); the mismatch is recorded as the finding.

### D12b — The kill-switch cannot run yet (correctly)
It reports underpowered (n=7 < 100) and has **no PIT features** (narrative composite + controls are
not reconstructed for the anchors). The harness is correct and ready; the verdict awaits real data.

**Genuine remaining work before a valid verdict (large, partly needs user input):**
1. Build the panel to **n>=100** delisted-inclusive names with **mechanical `t0`** (Protocol 2.2) —
   needs a historical universe + PIT mispricing/nascency gates.
2. Reconstruct **PIT features** per panel row: crude Legibility x ThematicHeat + controls
   (free_float, time_to_catalyst, drawdown, sector, era), all as-of `t0`.
3. **`m_share`** labelling (multiple-expansion share of return) for derived labels — needs historical
   fundamentals (data-provider decision).
4. Then run the kill-switch; gate all Module A/B + parameter fitting behind it (Protocol 8).

---

## D13 (2026-06-26) — Semantic embedder enabled (replaces the lexical placeholder)

Installed `sentence-transformers` (+ torch) into the shared venv and switched the diffusion engine
from the lexical hashing fallback to the real semantic encoder (`all-MiniLM-L6-v2`, dim 384). This
is the production embedder the framework always intended (Features §1.1); the hashing version (D7a)
was only a runnable placeholder.

- `config/diffusion.yaml`: `embed.model: all-MiniLM-L6-v2`, `tau_member: 0.20 → 0.45` (semantic
  cosines run higher than lexical). Re-embedding is automatic when the model name changes; ran
  `scripts/5_radar.py --no-fetch` to re-embed all ~7.4k cached docs + recompute membership (no
  network). The embed model is frozen per registry version (Features §1.1).
- **Synonymy now works:** cos("retrieval augmented generation", "grounding LLM answers in retrieved
  documents") = 0.45 with no shared keywords; unrelated text ≈ 0.
- **Membership got more accurate** (N_spec by source, semantic vs the prior lexical run):

  | theme | lexical N_spec | semantic N_spec | what changed |
  |---|---|---|---|
  | ssm_mamba | 186 | **61** | removed lexical false-positives ("state"/"space"/"model" are common words) |
  | crispr | 1328 | **1807** | recognized domain-relevant grants/papers hashing missed (NIH 293→510) |
  | mkras_vaccine | 105 | **318** | semantic "cancer vaccine"/KRAS relevance |
  | rag | 346 | **384** | slight gain |

  Top CRISPR members sit at cosine 0.81–0.82 and are all genuinely about genome editing — the
  membership is on-topic.
- `tau_member=0.45` is still ⚙ (validate/fit on the panel); `81 tests pass` (the get_embedder test
  is now availability-agnostic and checks cosine≈1 for identical text, not bitwise equality).

**Why now:** the placeholder was the weakest link in the whole engine; upgrading it tightens N_spec
across all four waves before the panel/kill-switch work consumes those signals. Note: `torch` is a
heavy dependency now in the shared venv (used only by 5_Hype).

**Follow-up (2026-06-26):** `SentenceTransformerEmbedder._load` now loads from the local HF cache
first (`local_files_only=True`), downloading from HF only on a cache miss. Routine/scheduled runs are
now genuinely offline (no per-run HF revalidation HEADs, no "unauthenticated requests" warning, and
resilient to HF rate-limits/outages) — making the "render-only = no network" promise actually true.

---

## D14 (2026-06-26) — Crude indicative panel: mechanical t0 + PIT features built (Phase 1 of "start crude, escalate")

User chose **"start crude, escalate"** over a one-shot full rigorous panel. Phase 1's explicit goal
is to **exercise the mechanical-t0 + PIT-feature pipeline end-to-end and surface problems early** —
not to deliver a powered verdict. Built `src/hype_parser/panel_builder.py` (pure functions) +
`scripts/5_panel.py --build-crude` + the `crude:` block in `config/panel.yaml` + 13 offline tests
(**94 pass**, was 81).

**What it does (all crude proxies, flagged unfit):**
- **Candidate universe:** the EDGAR ticker linkage (`theme_tickers`) across the 4 seeded themes
  (~89 names → 56 with usable yfinance history). Name→theme membership is *current*, not PIT — a
  crude shortcut.
- **Mechanical t0 (Protocol 2.2):** first month BOTH gates fire — `ThemeNascency_gate` (β_spec ≥
  β_min, computed PIT from `theme_series` as-of each month) AND a `Mispricing_gate` proxy
  (drawdown ≥ 25% below trailing-12-mo high, PIT from prices). t0 is set entirely by PIT features;
  the label is measured afterward. **Estimability guard** `min_history_months=6` blocks left-edge
  t0s where the slope is unestimable.
- **PIT features:** `narrative = Legibility × ThematicHeat` = `log1p(n_spec_t0) × β_spec_t0` (the
  multiplicative composite the kill-switch regresses on); controls `drawdown` (PIT), `sector`
  (theme proxy), `era` (PIT), `free_float` (current-float proxy, NON-PIT, median-imputed when the
  yfinance lookup fails). `time_to_catalyst` is **dropped** from the crude control set (no PIT
  catalyst calendar yet).
- **p_main gate DISABLED in crude:** GDELT `N_main` coverage is uneven (nonzero only for crispr,
  zero for the other three themes), so a `p_main ≤ p_max` gate would reject the clean crispr cohort
  and wave through the rest. `p_main` is still recorded as a feature.
- **Labels:** price-only (positive if 52w fwd ≥ +100%, else hard_negative — every row passed the
  gates, so there are no easy-negatives by construction). No `m_share` (needs PIT fundamentals — the
  escalation step). The kill-switch regresses *continuous* return, so `hit_return` only labels.

**Result: 56 rows (26 crispr / 2 mkras / 28 rag), kill-switch correctly UNDERPOWERED (n=56 < 100,
no verdict).** The narrative coefficient came out *negative* — but that is **not** a finding about
the premise; it is dominated by the four problems the crude run was built to surface:
1. **The drawdown mispricing proxy fires on market-wide selloffs, not theme weakness.** t0 clusters
   at 2020-10 (COVID recovery churn) and 2017 across unrelated names — "cheap" ≠ "mispriced vs a
   latent theme." Module A (the real value/re-rating triple-lock) is needed.
2. **β_min=0 makes the nascency gate vacuous.** A flat/near-zero specialist series passes (slope
   0 ≥ 0), so t0 fires at theme *birth* where `n_spec ≈ 0` → `narrative = 0.000` for ~20/56 rows
   (incl. the cleanest early entries). A meaningful gate needs **β_min > 0 and/or a minimum n_spec
   level** (the theme must actually exist at t0). Left as a calibration finding — *deliberately not
   tuned to chase a positive sign* (Protocol 3.2/11 forbids hindsight fitting).
3. **Era confound.** The 2021-vintage high-β entries (NKTX/CRBU/IPSC/SANA…) all crashed in the
   2021–22 biotech drawdown, so higher-narrative names did *worse*. This is why `era`/regime
   stratification (Protocol 2.3) and out-of-sample walk-forward (3.1) are mandatory, not optional.
4. **Theme-level narrative vs name-level outcome** → within-theme collinearity (all names in a
   theme share the theme series), which a 4-theme panel cannot break.

**Escalation (Phase 2, the full rigorous panel — only if warranted):** n ≥ 100 delisted-inclusive +
temporally/sector/regime stratified (2.3); a real Module-A mispricing gate; β_min > 0 / n_spec-level
nascency; `m_share` labels via a **historical-fundamentals provider decision** (still open, OD-4
adjacent); restore `time_to_catalyst` + the `p_main` gate once `N_main` coverage is consistent. The
crude harness (`panel_builder.py` + `--build-crude`) is the reusable skeleton for it.

**Why now:** validated the gating pipeline cheaply and converted "we don't know if the mechanics
work" into four concrete, named blockers — exactly Phase 1's job. No ⚙ parameter was fit; the
kill-switch still refuses a verdict below n=100.

---

## D15 (2026-06-26) — Crude gate fixes: real-theme nascency + benchmark-relative mispricing (D14 blockers 1–2)

Continued the build by fixing the two **engine-correctness** blockers D14 surfaced — both principled,
neither outcome-tuned. (Blockers 3 era-confound + 4 theme-collinearity are inherently escalation
scope: they need n≥100 + more themes, not a gate change.)

**Blocker 2 — `β_min=0` made nascency vacuous (`narrative=0` at theme birth).** `nascency_as_of` now
requires the theme to genuinely **exist and be accelerating** at t0:
- `β_spec > β_min` (now **strict** `>`, was `>=`) — the specialist slope must be measurably positive
  (the theme is growing). The threshold is **0 — the premise's own sign claim** (slope up), not a
  value swept against returns;
- `n_spec ≥ min_n_spec_level` (default **5**) — a real specialist corpus must exist ("a theme," not
  one stray doc).
This **eliminated all ~20/56 `narrative≈0` rows** (now **0**); the cleanest names carry real values
(CRSP narrative 0.89 at t0=2018-06, was 0.000 at a 2017 birth-cluster t0).

**Blocker 1 — drawdown fired on market-wide selloffs, not theme mispricing.** Added a
**benchmark-relative** mispricing gate (`relative_drawdown_as_of` = name drawdown − sector-ETF
drawdown; config `use_relative_drawdown: true`, `relative_cheap: 0.10`, `benchmarks: {crispr,mkras→
XBI; rag,ssm→QQQ}`). A name now qualifies only when it is ≥10pp **below its sector benchmark** —
idiosyncratic weakness, not "the whole sector fell." Falls back to absolute drawdown if a benchmark
series is missing. `relative_drawdown` is also recorded as a feature.

**Effect on the read (still UNDERPOWERED, still NOT a verdict — n=57<100):** the spurious strong-
negative `narrative_t` from D14 (**−3.57**, an artifact of the `narrative=0` rows) collapsed to
**−0.67** (indistinguishable from zero) — i.e. with the corruption removed, the crude panel shows
*no* signal either way, which is the honest state below n=100. The t0 cluster shifted off the
COVID-2020 selloff; a residual 2023-05 rag cluster (~20 names) reflects the noisy EDGAR rag ticker
universe, not a gate bug. **97 tests pass** (was 94; +3 gate tests). Defaults `min_n_spec_level=5` /
`relative_cheap=0.10` remain ⚙ (unfit) — chosen by principle, to be validated on the rigorous panel,
**not** swept against the label.

**Why now:** these were the only two D14 blockers fixable without the open data-provider decision,
and they directly de-corrupt the gating artifact. The narrative variable is now meaningful per row;
the remaining "no signal" read is a power problem (n), which is the escalation's job.

---

## D16 (2026-06-26) — m_share fundamentals = SEC EDGAR (reuse module_4c); pre-revenue ⇒ m_share=1

Resolves the open data-provider fork that gated the rigorous panel (Protocol §2.1 `m_share` label).
User decisions:

1. **Fundamentals source = SEC EDGAR companyfacts (XBRL), reusing the `2_Funds_parser/module_4c`
   client.** Chosen over Sharadar SF1 (paid, turnkey), a cheap API (FMP/EOD — restated, breaks PIT
   discipline), and defer-m_share (price-only). EDGAR is the only option that is **free +
   survivorship-free** (filings persist post-delisting) **+ PIT/first-print** (filing dates known) **+
   licensing-clean** (public domain — also satisfies the repo `project_data_provider_switch` memory),
   and the repo already has a battle-tested companyfacts client + `fundamentals.db` pattern
   (`module_4c`, with conditional-GET caching, TTM correction, XBRL-tag aliasing — D32/D54/D56). Cost:
   the build of a **PIT historical-series extractor** (companyfacts JSON carries every reported
   period + `filed` date) + adding the `Revenues` concept + the `m_share` decomposition. US-only is
   acceptable (the panel/themes are US-listed).

2. **Pre-revenue names ⇒ `m_share = 1` (pure-narrative).** Much of the panel is pre-revenue biotech
   (CRISPR/mKRAS/Elicio); with no revenue/earnings, P/S and P/E are undefined and the "fundamental"
   is ~0, so any run **is** a re-rating by definition. Rule: pre-revenue ⇒ `m_share := 1`; the
   fundamental-vs-multiple decomposition (e.g. P/S for revenue-bearing names) applies only where a
   real fundamental exists. Chosen over a clinical-stage proxy (needs M6/web_search clinical data —
   more build, deferrable) and excluding pre-revenue names (would gut the biotech cohort + anchors
   and make the panel a disguised software screener — contradicts the cross-sector premise D1).

**Resulting staged build (Phase 2 escalation):**
- **Stage A** — PIT fundamentals module for 5_Hype: reuse `module_4c` `edgar_client` to fetch
  companyfacts, extract a **first-print** (revenue, shares) series with `filed`-date discipline, store
  in 5_Hype's own `data/hype.db` (or a sidecar), expose `as_of(ticker, date)`.
- **Stage B** — `m_share` decomposition: split `[t0, t0+H]` return into Δmultiple vs Δfundamental
  (P/S where revenue exists; `m_share=1` pre-revenue per decision 2); becomes the real label.
- **Stage C** — expand the panel to **n≥100** delisted-inclusive + temporal/sector/regime
  stratification (Protocol §2.3); more seed themes to break the 4-theme collinearity (blocker 4).
- **Stage D** — run the kill-switch on the real panel; gate Modules A/B behind it (Protocol §8).

**Why now:** unblocks the only thing standing between the de-corrupted crude harness and a powered
verdict, and it does so with $0 / clean licensing by reusing existing repo infrastructure.

---

## Repo conventions inherited (not numbered — carried from the monorepo)

These are standing rules from the other components' decision logs + the repo memory index; they
apply here without re-deciding:

- **SQLite-only durable state** (`data/*.db`); no JSON/Parquet on disk except a browser sidecar.
- **Cost-gated, cache-first Claude** — single runner, mandatory `[y/N]` gate, prompt caching,
  Batch where possible, per-call ledger (`llm_tasks`-style). Only an explicit `--yes` bypasses.
- **Embeddings run locally** (sentence-transformers), not via a paid API — matches §8 and the
  `4_List_renderer` D33 L3 plan.
- **Output folders:** `Outputs/` = files the user opens; `data/` = durable cache/state;
  `_intermediate_outputs/` = code-only plumbing.
- **Naming carve-out:** top-level dirs/scripts may carry the `5_` prefix; `src/` packages may not
  (so `src/hype_parser/`). Shared venv `..\.venv\`; `PYTHONPATH=src`.
- **Reuse, don't duplicate:** the EDGAR client + rate limiter already exist
  (`2_Funds_parser/src/module_4c/edgar_client.py`, `3_Biopharmcatalyst_parser` M2/M6.5); the RSS/
  web/http_api adapters + SWR cache + Claude resolver exist in `4_List_renderer`. Form 4 / 13F /
  fundamentals pipelines already produce signals named in §6.4/§6.6. Prefer wiring these in.
- **Provider/ToS hygiene:** any yfinance-sourced price/volume is local-personal only until a
  licensed provider is swapped (repo memory `project_data_provider_switch`).
- **Don't multiply user requests:** resolve ambiguity in code with flags + audit rows; batch any
  human review.

---

## Open decisions (to resolve before/at the relevant step)

- **OD-1 — RESOLVED by D3** — standalone; reuse limited to the shared EDGAR client + existing
  signal outputs.
- **OD-2 — RESOLVED by D5** — forward archive built and running weekly (34/39 fetchable; 5 need
  per-source fetch specs). The queryable sources still back-fill the panel from history.
- **OD-3 — RESOLVED by D2.** The premise check is the **kill-switch test** (Protocol §4), run on
  the full labeled panel — not a cheap retrospective on the calibration cases. Protocol §2.4
  explicitly makes the 7 anchors "sanity rails, NOT the training set," and §2.3 requires n ≥ 100
  (≥ 50 positive); an n=7 proxy check is statistically invalid and is rejected. There is no valid
  shortcut around building the panel first.
- **OD-4 — Source-data licensing for any commercial use** (Google Trends, GDELT, StockTwits/X).
  Inventory access terms at registry-freeze time per §6.7. (Raised 2026-06-26.) Note: the
  **m_share fundamentals provider** sub-question is **RESOLVED by D16** (SEC EDGAR = free + clean
  licensing); yfinance prices remain local-only pending the separate `project_data_provider_switch`.
