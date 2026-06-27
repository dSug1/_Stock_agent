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

## D17 (2026-06-26) — Stage A built: PIT first-print fundamentals from SEC EDGAR companyfacts

Built the point-in-time fundamentals layer the `m_share` label needs. `src/hype_parser/
fundamentals.py` + schema **v7** (`company_facts` + `company_facts_log`) + `scripts/5_fundamentals.py`
+ `tests/test_fundamentals.py` (8 offline tests; **105 total pass**).

**Reuse reconciliation (refines D16's "reuse the module_4c client"):** module_4c's `fetch_companyfacts`
returns only the **8 most-recent** periods and **drops the per-fact `filed` date**, and it hard-imports
`layer_1.*` — none of which fits a PIT panel back to 2017 or 5_Hype's standalone architecture (D3). So
we reused module_4c's **GAAP-concept knowledge** (concept aliases + the TTM cumulative-YTD logic) and
**added the `Revenues` tags**, but implemented a **5_Hype-local, stdlib-only, injectable, fail-open**
extractor (house style) that stores **every reported period with its `filed` date**.

- **`fundamentals_as_of(ticker, date)`** picks the latest period **FILED ≤ date** → first-print,
  leak-free (Protocol §1); survivorship-free because filings persist post-delisting. Revenue = latest
  annual (10-K / ~365d) else a quarterly-derived TTM; shares = latest-ended filed value. `pre_revenue`
  is True when no positive revenue is derivable yet → Stage B sets `m_share=1` (D16).
- **Validated live:** NTLA as-of 2018-01 = pre_revenue ✓, as-of 2023-06 = $52M revenue ✓ (the as-of
  evolves PIT); NTAP 2020-10 = $5.41B TTM / $24.38 rev-per-share (matches NetApp's actual FY2020);
  the restatement unit test confirms a later 10-K/A is invisible before its filing date.
- **Stage-B flag (not fixed here):** CRSP shows $3.1M as-of 2020-01 — *collaboration/milestone*
  revenue, not product sales — so it reads "revenue-bearing" though its P/S is astronomical and
  `m_share≈1` regardless. Stage B should apply a small **revenue (or revenue-per-share) floor** so
  milestone-only names are treated as effectively pre-revenue. Recorded for Stage B.

**Next — Stage B:** the `m_share` decomposition (combine these PIT fundamentals with PIT price/shares
to split `[t0,t0+H]` return into Δmultiple vs Δfundamental; `m_share=1` for pre-revenue + sub-floor
names) → becomes the real panel label.

---

## D18 (2026-06-26) — Stage B built: m_share decomposition is now the real panel label

`src/hype_parser/mshare.py` (pure) + `crude.m_share*` config + wired into `build_crude` + 10 tests
(**115 total pass**). The price-only label is replaced by the hype-vs-value-realization label
Protocol §2.1 requires.

- **Decomposition (per-share identity):** `price = M × F` with `F = revenue_ttm/shares` (per-share
  fundamental) and `M = price/F` (P/S multiple), so `log(price_H/price_0) = log(M_H/M_0) +
  log(F_H/F_0)` exactly, and **`m_share = log(M_H/M_0)/log(price_H/price_0)`** — the re-rating's share
  of the run. Uses the same adjusted prices as the forward return, so it's an exact identity in the
  inputs; dilution shows up in `F` via the share count. Endpoints come from `fundamentals_as_of`
  (Stage A) at `t0` and `t0+H`.
- **Pre-revenue + materiality floor ⇒ `m_share=1`** (D16 + the D17 flag): `min_revenue_usd=25M` so
  milestone/collaboration revenue (CRSP's $3M) is treated as no real fundamental.
- **Label:** positive ⇔ forward return ≥ `hit_return` **AND** (where decomposable) `m_share ≥
  m_share_min=0.50`; falls back to price-only when fundamentals aren't loaded (graceful).
- **Live 57-row rebuild:** modes **22 decomposed / 32 pre_revenue / 3 no_fundamentals**. All 4
  doublers had `m_share ≥ 0.5` (re-rating-driven), so the clause is **non-binding on this small
  panel** — an honest finding: every nascent-theme name that doubled here did so via re-rating, not
  revenue growth. It is tested-discriminating (4 decomposed names sit `<0.5` and would be demoted if
  they doubled), and will bite at n≥100 when revenue-bearing doublers appear. The **kill-switch read
  is unchanged (`narrative_t=−0.67`)** — expected, because it regresses *continuous* forward return;
  m_share only sharpens the positive/hard_negative split (for the quota + eventual Module fitting).
- **Known artifact (Stage-C flag):** `m_share` is unstable when `|total return|≈0` (decomposed tail
  −3.23…+12.86 among small-return names); harmless now since it only gates *large*-return positives
  (stable denominator), but **winsorize** if ever used as a continuous feature.

**Next — Stage C:** expand to n≥100 delisted-inclusive + temporal/sector/regime stratification + more
seed themes (breaks blocker 4). The label + fundamentals plumbing is now real; Stage C is about
*coverage* (and is where the m_share clause starts doing work).

---

## D19 (2026-06-26) — Stage C scaffolding: panel stratification diagnostics (Protocol 2.3)

Built the representativeness meter that guides (and gates) the n≥100 expansion, before committing to
the heavy coverage work. `src/hype_parser/panel_strata.py` (pure) + `config/panel.yaml::strata` +
`scripts/5_panel.py --strata` + `tests/test_panel_strata.py` (5 tests; **120 total pass**).

`evaluate_strata` reports n, positive/hard-negative counts + ratio, the **max share of positives in
any rolling 12-month window** (the temporal-clustering metric, blocker 3), and the sector/regime/
theme/era distributions — emitting a `flags` list of Protocol §2.3 violations (empty ⇒ stratified_ok).

**Live read on the current panel (the Stage-C to-do list, quantified):**
- **VIOLATIONS:** `n=57 < 100`; **`n_positive=4 < 50`** — the two binding constraints.
- Already OK: sectors balanced (bio 28 / tech 29), regimes balanced (A 28 / B 29).
- Weak: only **3 effective themes** (rag 29 / crispr 26 / mkras 2; ssm 0 — no EDGAR tickers) ⇒ the
  4-theme collinearity (blocker 4); era spread is wide but rag-heavy in 2023.
So the expansion must **~2× the names and ~12× the positives** → a much broader, delisted-inclusive
universe + more seed themes. (With only 4 positives the temporal metric (0.50) is too noisy to trust
yet — it becomes meaningful once positives grow.)

**Why now:** turns "expand the panel" from a vibe into a measured target, and the same `--strata`
check is the §2.3 gate the finished panel must pass before the kill-switch verdict counts. The two
remaining Stage-C judgment calls (delisted **price source**; **universe/theme breadth**) are now teed
up for the user — they were deliberately not picked unilaterally.

---

## D20 (2026-06-26) — Stage C coverage decisions + constituent-broadening built

**User decisions (the two teed-up Stage-C forks):**
1. **Price source = accept yfinance survivorship bias for now (free).** Proceed to a bigger panel on
   yfinance; the kill-switch read stays explicitly **INDICATIVE** (delisted names drop out → failures/
   hard-negatives under-counted → positive rate biased up). A survivorship-free price provider
   (Sharadar/Norgate/EODHD) is the later swap for a *powered* verdict — same swap point as the
   `project_data_provider_switch` memory. **The verdict tier is capped at INDICATIVE until then —
   carry this caveat forward.**
2. **Universe = more themes + broaden constituents** (the most rigorous option): seed ~10 more
   cross-sector sub-themes AND harvest more constituents per theme, not just the top EDGAR-FTS hits.

**Built now (the decision-free infra for #2):** EDGAR FTS **pagination** — `edgar_fts.fetch_yearly`
gained `ticker_pages` (config `diffusion.yaml::ingest.edgar_ticker_pages=5`), paging hits via `from`
to harvest ~50 filers/year/theme instead of ~10 (best-effort; stops past `total`). +2 tests (**122
pass**). This broadens constituents for both new and existing themes on the next ingest.

**Proposed ~10 new sub-themes (cross-sector, to break the 2-AI/2-bio collinearity — for sign-off
before ingest):** bio/health — `glp1_obesity`, `radiopharma`, `adc_oncology`; energy/climate —
`solid_state_battery`, `green_hydrogen`, `smr_nuclear`; deep-tech/semis — `quantum_computing`,
`silicon_photonics`; AI/software — `ai_agents`; mobility — `evtol_uam`. (Spread: bio×3 / energy×3 /
semis-quantum×2 / AI×1 / mobility×1.)

**Next (the heavy coverage run, multi-step):** seed those themes into `themes_seed.yaml` (full query
fields) → re-ingest all themes (diffusion engine; network-heavy, GDELT/SBIR 429-flaky but fail-open)
→ `5_fundamentals.py --fetch` the new tickers → `--build-crude --rebuild` → `--strata` (must clear
§2.3) → `--killswitch`. Expected to lift n past 100 and positives toward 50, at which point the
m_share clause starts binding and the kill-switch finally yields an (indicative) read.

---

## D21 (2026-06-26) — Course-correction: hand-seeding themes is rejected; build theme DISCOVERY instead

**Supersedes the "seed ~10 hand-picked themes" half of D20.** The user flagged that hand-populating
the theme list **defeats the project's whole purpose** — early identification of *emerging* themes.
Picking known themes is look-ahead: `glp1_obesity / adc_oncology / ai_agents` are "ship has sailed"
(already mainstream), and `quantum_computing / green_hydrogen` have unproven/likely-unprofitable
business models. Seeding any of these biases the panel toward already-hyped narratives — the opposite
of "specialist slope rising while mainstream is still low."

- **Confirmed state:** there is **no discovery module**. Themes enter only via the hand-written
  `config/themes_seed.yaml` (loaded by `scripts/5_radar.py`). The Features §1.1 discovery engine
  (cluster emergent docs → label) was **specced but never built**; "hand-seeded" has been a silent
  carried gap.
- **Correct path (to build):** **theme discovery** — a **broad, untargeted** document ingest (by
  arXiv/bioRxiv *category*, not by theme keyword) → local embeddings → **clustering (HDBSCAN-style,
  zero-Claude)** → the existing **diffusion nascency gate** (`β_spec` rising, `p_main` low) ranks the
  emergent clusters → optional one-call Claude labelling of survivors. This makes the theme list an
  *output*, not an input.
- **Structural prerequisite (why it's non-trivial):** the current ~7,200-doc corpus was ingested with
  **per-theme queries**, so it contains only the 4 seeded themes — there are no unknown themes in it
  to discover. Discovery needs a **new broad category-based ingest mode**.
- **`silicon_photonics`:** force-include as a tracked seed (user instruction) even once discovery
  runs. Caveat: constituent linkage today is **EDGAR (listed filers only)**, so a pre-IPO/unlisted
  candidate won't surface that way — surfacing unlisted names needs a doc-mined / S-1-pipeline
  constituent source (folded into the discovery design).
- **Still valid from D20:** the EDGAR-FTS **constituent pagination** (`edgar_ticker_pages`) — broader
  constituents are useful regardless of how themes are chosen. The yfinance-bias / INDICATIVE-tier
  decision also stands.

**OPEN (user deferred — "read and decide later"):** the **discovery corpus scope** —
(a) arXiv + bioRxiv/medRxiv by category; (b) + Hacker News / patents / grants; (c) cluster the
existing corpus only (sub-themes, not real discovery). Build of the discovery module waits on this.

---

## D22 (2026-06-26) — Discovery redesigned: expert-jury convergence (couple-MB), not volume-clustering

The v0.1 broad-ingest-and-cluster discovery spec was **rejected** by the user and replaced by
`discovery_spec_v0.2.md`. This also **closes D21's open "corpus scope" question** — the answer is
*none of broad-arXiv / +HN-patents / existing-corpus*; discovery uses **curated expert juries**, not a
document corpus.

User constraints + how v0.2 meets them:
1. **"Several GB is too big — target a couple of MB."** Juries are sparse + annual → a few thousand
   short signal rows ≈ **1–3 MB** (embed the *signal texts* only; no corpus). No bulk paper/patent
   landing.
2. **"Patents: too broad, too noisy."** **Dropped** from discovery (at most a later targeted
   confirmation).
3. **"Best use of insiders' knowledge — awards, recognitions, breakthrough therapies (e.g. the 2001
   internet award to Louis Pouzin)."** These are **already in the registry** (`config/sources.yaml`,
   `edge_type: awards`, with `jury_credibility` + `diffusion_position`): **leading** juries FIND
   nascency (MIT TR-10, R&D 100, Fierce 15, RSA Sandbox, DARPA/IARPA BAAs); **bridge** = FDA BTD;
   **denominator** = Nobel/Turing/Lasker — the registry already notes these *"can TRIGGER diffusion but
   do not FIND nascency."* The Pouzin example = a **denominator** signal (recognition arrived; sets the
   clock, doesn't start it). A theme is discovered from the **convergence of independent leading
   juries** on the same area (credibility-weighted), not from volume. These juries are **already being
   snapshotted by the D5 forward archive** — discovery just **parses** the snapshots (no new ingest).
4. **"Use positions of specialized investment funds (2_Funds_parser)."** Reuse (D3) its
   `module_3/bridge.py` **`new_positions`** per security; specialist funds' *new* positions are a
   capital jury alongside the awards.
5. **"A theme may take years to be broadly recognized — associate a time horizon."** Every discovered
   theme carries an estimated **runway (years)** from which jury tiers fired + the diffusion curve
   position (leading-only & low `p_main` → 2–5 yr; bridge → 1–3 yr; denominator/high `p_main` → ~0,
   too late), calibrated on historical first-leading-jury → mainstream-inflection lags. **Consequence:**
   the panel's forward-return horizon `H` must become **theme-horizon-aware** (multi-year), not a fixed
   13/26/52-week / 3–18-month window — a spec change flagged for the panel.

Reuses D4 (registry = the jury catalogue), D5 (forward archive already captures the juries), the
diffusion engine (measures each promoted theme's curve), and `2_Funds_parser` (D3). Smallest proving
slice: parse the ~5 leading juries + one specialist-fund feed → embed → show convergence groups for the
last few years (couple of MB, no Claude). **OPEN:** jury weights / specialist-fund definition / horizon
calibration (⚙, set at build). Discovery build awaits the user's go on v0.2.

---

## D23 (2026-06-26) — Expanded the LEADING-jury catalogue (web research, broad-industry)

The user asked to "work harder on `diffusion_position: leading`" and parse the web for jury sources
across more industries (fintech, internet, cybersecurity, semiconductors, social, batteries, sensors,
opto-electronics, e-commerce, consumer, software, …). Ran **4 parallel research agents** (deep-tech
hardware / software-cyber-AI / consumer-fintech / cross-industry+gov+bio), each web-verifying sources.
Added **33 new leading juries to `config/sources.yaml`** (registry now **108 sources, 79 leading**, no
dupes, YAML validated). Industry coverage now spans every named sector:

- **semis:** EE Times Silicon 100 (best — ~100 private startups, ~40%/yr turnover), SEMI S3, DARPA ERI,
  Elektra Start-up.
- **sensors / photonics:** Best of Sensors, SPIE Startup Challenge (pre-revenue), SPIE Prism.
- **batteries/energy:** BloombergNEF Pioneers, ARPA-E (open CSV).
- **robotics / quantum / materials:** RBR50, The Quantum Insider, JEC Composites.
- **fintech / cyber / software-cloud:** Forbes Fintech 50, CB Insights (AI100/Fintech100/RetailTech100/
  DigitalHealth50), SC Awards Emerging, Black Hat Arsenal, Gartner Cool Vendors, CNCF Sandbox, GitHub
  Accelerator.
- **consumer / marketplaces / cross-industry:** a16z Marketplace 100, Product Hunt, WEF Tech Pioneers,
  Forbes AI 50 / Next-Billion-Dollar, CNBC Disruptor 50, In-Q-Tel, LinkedIn Top Startups, FC Next Big
  Things.
- **biotech/medtech + gov:** Endpoints 11, MedTech Innovator, Fierce Medtech, ARPA-H, NSF Convergence
  Accelerator, EIC Accelerator/Pathfinder.

Key build guidance baked into the rows: **`access_method: api` = machine-readable, build first** (YC
yc-oss JSON, CNCF `landscape.yml`, ARPA-E data.gov CSV, Product Hunt API, EIC/CORDIS); the rest scrape
the OD-2 forward-archive snapshots. **Private/unlisted-firm sources flagged** (In-Q-Tel, SEMI S3, SPIE
Startup Challenge, EIC, Fierce Medtech, MedTech Innovator, Forbes/CB-Insights private-only) — these are
the `theme_orgs` route to pre-IPO candidates (the silicon_photonics goal). **Denominator/people-prize/
lagging sources explicitly excluded** (IEEE Internet Award, Internet Hall of Fame, Webby, Deloitte Fast
500, Inc 5000, Prix Galien core, BIO awards, Rock Health tracker). Access gotchas recorded in the row
notes (Forbes/WEF/FC 403 bot-blocks → VC mirrors; CB Insights / Gartner / Cleantech 100 full lists
gated → reconstruct from PRs; Best-of-Sensors overwrites → Wayback).

Registry is still frozen at v1 (D4) — these are catalogue additions; re-seed + re-freeze a new version
when the discovery parsers are built. Refines `discovery_spec_v0.2.md` §2 (jury set).

---

## D24 (2026-06-26) — Discovery output split: LISTED (investable) vs PRIVATE (watchlist)

User requirement: discovery must **categorize public-listed vs private companies** so listed names can
be invested and private ones tracked. Added to `discovery_spec_v0.2.md` (§4a + §7 schema):

- Every surfaced `org_name` is resolved against the SEC ticker map (`company_tickers.json`, already in
  `fundamentals.py`) → `listing_status ∈ {listed, private, unknown}`.
- **Track A — INVESTABLE (listed):** carries ticker/cik → feeds the diffusion/panel/screener.
- **Track B — WATCHLIST (private):** no ticker → `listing_watch=1`, monitored on EDGAR for
  **S-1/F-1/424B/S-4**; first filing flips it private→listed (`became_listed_at`) and into Track A —
  the IPO itself is often the re-rating catalyst, so the flip is a signal. (The silicon_photonics case.)
- `unknown` = low-confidence name match → queued for manual/Claude resolution.
- `theme_orgs` schema updated accordingly. Also rewrote the **handoff brief** to a clean,
  comprehensive D1–D23 state + the two workstreams (panel escalation; discovery) so a fresh session can
  continue from it.

---

## D25 (2026-06-26) — First-run 10-yr awardee backfill + specialist-fund cross-reference

Two user requirements for the discovery module; both specced in `discovery_spec_v0.2.md`.

**(1) Historical awardee backfill (first run, last ~10 years)** — seeds the sub-theme DB with a
`β_spec`/`p_main` *history* to gate nascency. Per-source route (spec §3a), cheap + tiny: (a) the jury's
own **multi-year "past winners" archive** (URL-pattern-per-year scrape — SPIE Prism 2008→, BNEF
Pioneers 2010→, Fierce 15 / Endpoints 11 per-year, Forbes/CB-Insights slugs, RBR50…); (b) **Wikidata
SPARQL** (one query → all years, for prizes with a Wikidata item); (c) **Wayback Machine CDX** for
sources that overwrite each year (Best of Sensors) — enumerate past annual snapshots + parse each;
(d) **machine-readable APIs with full history** (YC yc-oss 2005→, ARPA-E data.gov, CNCF landscape git,
Nobel, CORDIS, Product Hunt). Output `jury_signals` tagged by year (~low-thousands of rows total); the
forward archive then takes over. `history_availability` flags which route.

**(2) Specialist-fund list per theme + cross-reference module** — the analogue of the 21 biotech
specialist funds curated in `2_Funds_parser` (its `funds` table: Baker Bros, RA Capital, Perceptive,
OrbiMed, Avoro, BVF, Cormorant, Deerfield, EcoR1, RTW, Redmile, Boxer, …). Built
**`config/specialist_funds.yaml`** (via 2 web-research agents) covering software/AI, semis, cyber,
fintech, energy/cleantech, consumer/internet, space/defense/deep-tech, materials/mobility. **Research
finding:** dedicated-specialist 13F filers are thick only in **biotech + software/growth** (Whale Rock,
Altimeter, Coatue, Tiger, Light Street, Sylebra, Dragoneer, Durable); in semis/cyber/fintech/energy/
space/materials most specialists are **private VCs that file no 13F** → those sectors fall back to
**thematic-ETF holdings** (semis SMH/XSD, cyber CIBR/BUG, fintech Ribbit+FINX, batteries LIT/BATT, space
UFO/ARKX/XAR, materials REMX/XME/URNM). Cross-reference module (spec §4b): reuse `2_Funds_parser/
module_3 new_positions` (D3) and intersect {specialist new buys ∪ ETF additions} with a theme's Track-A
(listed) tickers → **capital-jury confirmation** while the theme is early. 13F caveat: US-listed,
quarterly, ~45-day-lagged, longs-only, crossover private books invisible → confirmation on *listed*
names, not private discovery (private comes from the award juries → Track B). CIKs in the config are
web-research best-effort → **verify on EDGAR at build**.

---

## D26 (2026-06-26) — Self-contained fund list + weekly release calendar (cache-max)

- **Inlined the 21 biotech specialist funds** (names + CIKs from `2_Funds_parser`'s `funds` table) into
  `config/specialist_funds.yaml` so it is one self-contained list (44 funds across 9 sectors). The
  cross-ref still reuses 2_Funds' 13F holdings/`new_positions` pipeline (D3); CIKs flagged to
  EDGAR-verify at build.
- **Weekly-run efficiency / "don't parse every award every week" (`config/discovery_calendar.yaml` +
  spec §5a):** the weekly run fetches a source only when **DUE** = publication window open AND this
  period's edition not yet captured (per-source watermark). Most juries are **annual**, so they are a
  no-op ~50 weeks/year; a typical week polls only the **continuous** feeds (YC/Product Hunt/CNCF/ARPA-E,
  incrementally), any **award currently in its publish window**, and the **fund cross-ref only after a
  13F deadline** (Feb/May/Aug/Nov). `annual_publish` lists best-effort publication months per jury that
  self-correct as editions are captured.
- **Caching is maximized (already-built mechanisms, reused):** diffusion SWR (`cache_ttl_days=7`,
  `--no-fetch` = zero-network recompute), non-destructive-on-failure, content-hash forward archive
  (writes only on change), conditional-GET (ETag/304) in the SEC client, foundational embedding/CIK
  caches never cleared, and a **one-time** 10-yr backfill. `data/hype.db` is the source of truth; the
  weekly run is mostly DB reads + a few due-this-week fetches.

---

## D27 (2026-06-26) — Theme discovery BUILT: jury-convergence smallest proving slice (schema v8)

Workstream 2 leaves "specced, awaiting go" and becomes real. Built the end-to-end jury-convergence
pipeline of `discovery_spec_v0.2.md` §9's "smallest first slice" — zero Claude, tiny footprint,
fully tested. **140 tests pass** (was 122; +18). DB schema **v8** (additive).

**What was built:**
- **Schema v8 (`db.py::_migration_8`):** `jury_signals` (one embedded expert recognition),
  `theme_convergence` (which signals back a discovered theme), `theme_orgs` (constituent roster,
  `listing_status ∈ {listed,private,unknown}` + `listing_watch` + `became_listed_at`), and three
  `themes` columns (`horizon_years`, `horizon_confidence`, `discovered_from`). All per spec §7.
- **`src/hype_parser/discovery/`** package:
  - `parsers.py` — **build-first machine-readable feeds**: `parse_yc`/`fetch_yc` (yc-oss JSON; a
    *leading* startup jury naming private firms; batch→year) and `parse_nobel`/`fetch_nobel`
    (a *denominator* jury). Parse-split-from-fetch + injectable-HTTP + fail-open. Plus
    `parse_snapshot_source` — the framework that parses the OD-2 forward-archive snapshots of the
    **scrape** juries (MIT-TR10 etc.) with a **labelled-crude** generic HTML extractor (per-source
    extractors are the spec §8 follow-up). `API_FETCHERS` registry keys the clean feeds.
  - `signals.py` — `jury_signals` upsert (dedup on `source_id+year+item_hash`), `annotate_from_registry`
    (backfill position/credibility from `sources`), local embedding (`embed_pending`, reuses the
    MiniLM encoder), `load_embedded_signals`.
  - `convergence.py` — **the novel core.** Greedy cosine clustering (`tau_converge`), score by
    `Σ_source credibility_weight × position_weight` over **distinct** sources (independence), promote
    eligible groups (≥`min_signals` AND ≥`min_leading_juries` distinct *leading* sources) to `themes`
    + `theme_convergence`. Horizon from which tiers fired (§5 heuristic). Denominator juries add **0**
    to discovery score — they only move the horizon (the Pouzin nuance, encoded).
  - `resolve.py` — listed/private classification via the SEC `company_tickers.json` name index
    (reuses `fundamentals.py`); exact-normalized match (suffix-stripped) ⇒ listed, else token-Jaccard
    ≥ `match_min_confidence` ⇒ listed, else private (Track B, `listing_watch=1`). `mark_listed` flips
    private→listed on a registration filing. **Only `entity_type='company'` is rostered** — the crude
    snapshot extractor's `unknown` entities (e.g. MIT-TR *headlines*) are NOT mislabelled as watchlist
    firms (a real fix found in the live run).
  - `funds.py` — specialist-fund cross-reference (reads `specialist_funds.yaml`; pure `cross_reference`
    scorer weighting specialists above crossover/ETF). The 13F new-position *data* sourcing
    (2_Funds reuse / EDGAR / ETF deltas) is the wiring follow-up; the scorer is provider-agnostic.
- **`scripts/5_discovery.py`** — CLI: `--ingest` (+`--no-fetch`, `--since-year`), `--converge`,
  `--list`, `--watch`. **`config/discovery.yaml`** holds every ⚙ knob (all unfit until the §9-step-5
  back-test).
- **Live proof (the slice working):** ingested 4 891 signals across **3 juries** (YC 4 583 leading +
  Nobel 102 denominator + MIT-TR 103 leading, semantic-embedded), and convergence produced **5
  discovered themes** where YC startups converge with MIT-TR breakthrough topics (each backed by 2
  independent *leading* juries), horizon ≈3.5 yr; org resolution split **1 listed (Track A) / 489
  private (Track B)** — correct, since YC firms are private. With only the single MIT-TR snapshot
  source, convergence correctly **promotes nothing** (needs ≥2 independent leading juries) — the
  honest underpowered state, mirroring the panel kill-switch discipline.

**Two build bugs found + fixed in the live run (kept as a caution):** (1) `promote` suffixed theme_ids
against *pre-existing* DB themes, so each re-run spawned `…-2` duplicates — fixed to dedup **within the
run only** (stable slug ⇒ same theme_id ⇒ ON CONFLICT refresh; verified idempotent at 5). (2) the
crude `unknown`-typed snapshot entities were polluting the listing-watch — fixed by rostering only
`company`-typed entities.

**OPEN / next (unchanged priorities):** `tau_converge` is unfit (at 0.55 the AI startups collapse into
one 379-signal mega-cluster — the §8 calibration ⚙); per-source snapshot parsers (tag real companies +
years) so the scrape juries contribute orgs; wire the historical backfill (§3a) and the specialist-fund
13F provider (§4b); then feed discovered themes into the diffusion engine + panel and back-test against
the hand-seeded baseline (§9 step 5). Horizon calibration (§5) still heuristic. Walkthrough:
`spec/discovery_explained.md`.

---

## D28 (2026-06-26) — Specialist-fund 13F cross-reference wired to real 2_Funds data (spec §4b)

Completes the §4b smart-money confirmation end-to-end. The D27 `funds.cross_reference` scorer was pure
(no data source); now `funds.new_buys_from_2funds(db_path, cfg)` reads **real NEW 13F positions** from
`2_Funds_parser/2_fundparser.db` — the D3/D25 reuse path.

- **Definition** matches `module_3.bridge`'s `is_new`: a `(fund, ticker)` is new in a quarter if the
  fund holds it then but did **not** the prior quarter; each fund's **latest filing per period** wins
  (amendments). Implemented as **stdlib SQL** against the 2_Funds `holdings`/`funds` tables — we reuse
  2_Funds' *signal output*, not its pandas code (the standalone rule, D3). Fund **type** is tagged by
  CIK from `specialist_funds.yaml` (`cik_type_map`); the cross-ref weights pure specialists above
  crossover/VC. Fail-open to `{}` if the DB is missing/unreadable.
- **CLI:** `5_discovery.py --funds [--quarter YYYY-MM-DD] [--funds-db PATH]` crosses the newest quarter's
  new buys against each discovered theme's Track-A tickers. **Live:** read **292 new specialist
  positions / 208 tickers** (2026-03-31 vs 2025-12-31) from the 21 biotech funds; **no overlap** with
  the current AI/drones/nuclear discovered themes (YC+MIT-TR) — correct, biotech 13F holders won't hold
  them. It will fire when biotech/listed-heavy themes are discovered (Fierce 15 etc., once those juries
  are parsed). 143 tests (was 140; +3, synthetic 2_Funds DB).
- **Caveat (carried):** 13F is US-listed, quarterly, ~45-day-lagged, longs-only — confirmation on
  *listed* names, not discovery of private ones (per spec §4b). The non-biotech sectors still fall back
  to **thematic-ETF holdings deltas** (`specialist_funds.yaml::etf_fallback`); that ETF-delta provider
  is the remaining §4b wiring (the 2_Funds reuse covers biotech today). Not persisted yet — surfaced as
  a computed signal; a `theme_smart_money` table follows when the panel consumes it.

---

## D29 (2026-06-26) — Discovery nascency gate: rank themes by the jury-year timeline (spec §3a)

Convergence (D27) scores a theme only by *how many* independent leading juries agree — so all 5 live
themes tied at 1.6 and were indistinguishable. D29 adds the **nascency gate** (`discovery/nascency.py`)
that ranks them by **how nascent + accelerating** the jury recognition is, read from the
`jury_signals.year` timeline already ingested — zero network, zero Claude.

- **Instrument reuse:** `beta_jury` = the diffusion engine's `beta_spec` (OLS of ln(1+N) over a recent
  window) applied to **annual jury counts** instead of monthly corpus counts. Gaps are filled to real
  contiguous years (a missing year is a real 0). `rank_score = convergence_score × (recency_floor +
  recency) × (1 + max(0, beta_jury))` — fuses convergence (independent juries) × acceleration (β) ×
  recency (share in the last few years). **Pure read** (recomputes from stored signals, the diffusion
  compute-on-read pattern); nothing persisted unless `--persist-horizon`.
- **Refined horizon (spec §5):** the runway estimate was tier-only in D27; now it is nudged within its
  tier band by the timeline (all-recent + accelerating ⇒ long/early end; old/decelerating ⇒ short end),
  optionally written back to `themes.horizon_years` with `horizon_confidence='timeline'`.
- **CLI:** `5_discovery.py --rank [--persist-horizon] [--current-year N]`. **Live:** the 5 tied themes
  spread out — "drones" tops (β +0.31, recency 0.69, ~4.1y runway = early), the `tau`-over-merged "AI in
  2026" mega-cluster sinks (β −0.14, recency 0.36, ~2.5y = later-stage). Horizon now varies 2.5–4.1y
  from data, not a flat 3.5y tier default. 147 tests (was 143; +4). Walkthrough:
  `spec/discovery_nascency_explained.md`.
- **Limits (carried):** β is coarse on sparse annual data (a tilt, not a rate); `--rank` *orders*, it
  does not *gate*/drop (the nascency threshold belongs with the §9 back-test alongside `tau`); and this
  is the jury-timeline proxy — the corpus `β_spec`/`p_main` via the diffusion engine (§4 step 4) is the
  next, heavier integration.

---

## D30 (2026-06-26) — Discovered themes wired into the diffusion engine (spec §4 step 4 / §9 step 5)

Closes the loop: a discovered theme is now **measured by the same diffusion engine** as a hand-seeded
one, so the §9-step-5 back-test (discovered vs hand-seeded baseline) is unblocked. Promote (D27) leaves
a theme with a descriptor + entity keywords but **no diffusion queries**, so the radar couldn't ingest
a corpus for it. `discovery/diffusion_bridge.py` derives them, zero-Claude.

- **`derive_query(label, member_texts)`** — **label-first**: the convergent label IS the curated topic,
  so the corpus query is built from it; member jury texts (YC blurbs) enrich **only** a content-less
  label (they *drift* — sector tags like "defense/saas" pull an off-topic corpus). Bigrams rank above
  unigrams. Also emits a **topic-facing descriptor** (`"{label}. {terms}"`) — the promote() descriptor
  is the jury blurbs (startup-speak), too far from research abstracts.
- **`assign_diffusion_queries(conn)`** writes `arxiv_query`/`gdelt_query`/`wiki_article`/`keywords`/
  `descriptor` onto discovered themes (the columns already exist — no schema change). CLI
  `5_discovery.py --diffusion-queries [--overwrite-queries]`. **`load_discovered_radar_themes`** shapes
  them as radar theme-dicts; `5_radar.py --include-discovered` then processes them exactly like seeds.
- **Live proof + a real finding:** first run gave **0 member-months** — the broad auto-query
  (`drones OR defense OR industrials`) + a jury-blurb descriptor produced a corpus whose top cosine was
  0.354, below `tau_member=0.45`. Fixing to a **label-first** query (`all:"drones"`) + topic descriptor:
  max cosine **0.623**, **17 member-months / 59 N_spec**, top docs all genuine drone papers (UAV swarms,
  vision-based drones, delivery) → **β_spec = +0.081, nascency_gate = True** (p_main = 0 only because
  GDELT 429-rate-limited this run). The loop is proven end-to-end on a coherent theme.
- **Carried limits (⚙, deferred to back-test):** the auto-query is a *labelled-crude* heuristic — good
  for coherent clusters (drones/nuclear → clean queries), noisy for the `tau`-over-merged "AI in 2026"
  blob and for short tokens ("ai" is filtered as <3 chars); spec §6 allows one cheap Claude *naming*
  pass as the quality upgrade. `tau_member` is itself unfit (tuned for hand-crafted descriptors). 152
  tests (was 147; +5). Walkthrough: `spec/discovery_explained.md` (§ "Measuring discovered themes").

---

## D31 (2026-06-26) — Theme assessment: fuse the two nascency signals + compare vs hand-seeded (spec §9.5)

The discovery module now reads "how early" a theme is **two independent ways** — the jury timeline
(D29: `beta_jury`, recency) and the corpus diffusion (D30: `beta_spec`, `p_main`, `nascency_gate`).
`discovery/assess.py` fuses them per discovered theme and lays the discovered themes next to the
hand-seeded baseline on the **same** diffusion metric — the §9-step-5 comparison (the diffusion-signature
part; the full panel/forward-return back-test is separate, Protocol §4).

- **Fusion (monotone, never penalises):** `combined_score = jury rank_score × (1 + max(0, beta_spec))`
  when the radar has measured the theme, else jury-only. Measuring a theme can only *raise* its rank;
  an unmeasured theme is un-lifted, not penalised. Pure read over the stored series (no network).
- **CLI:** `5_discovery.py --assess`. **Live result (encouraging):** the discovered **drones** theme
  scores `beta_spec = 0.08` — **on par with / above the best hand-seeded themes** (Mamba 0.07, RAG
  0.04; CRISPR is late at p_main 0.81; mKRAS negative). Median discovered β_spec **0.081** > seed median
  **0.032**. So discovery surfaced a theme with an early-diffusion signature comparable to the curated
  baseline — the first real signal that the jury-convergence approach finds genuinely-nascent themes.
- **Caveat:** only 1 of 5 discovered themes is measured (the radar ran on drones only); the harness
  honestly shows `n/a` + "1 measured" for the rest. Running `5_radar.py --include-discovered` across all
  discovered themes (a network pass) fills the comparison. 155 tests (was 152; +3).

This closes the discovery vertical for now: **ingest juries → converge → resolve orgs (Track A/B) →
nascency-rank → smart-money confirm → diffusion-measure → assess vs baseline**, all zero-Claude. The
remaining open items are calibration (`tau_converge`, `tau_member`, the fusion/horizon weights), the
per-source snapshot parsers + §3a backfill + §4b ETF-delta fallback, and the full panel/forward-return
back-test (Protocol §4) — all gated on the kill-switch and the panel reaching n≥100.

---

## D32 (2026-06-26) — Anti-mega-cluster: self-correcting split of oversized convergence groups

The standing convergence defect (D27–D31): greedy single-pass clustering lets one centroid **drift** and
absorb hundreds of loosely-related signals — the **379-signal "AI in 2026" blob**, a single theme that
swallowed most AI startups. Raising `tau_converge` globally is blunt (it also fragments the *good*
tight themes). Fix: keep `tau` where it is, but **split only the oversized groups** by re-clustering
their members at a progressively tighter tau (`convergence._split_oversized`, config
`max_cluster_size`/`split_tau_step`/`tau_ceiling`).

- **Self-correcting:** a genuinely cohesive theme stays one group when tightened (the re-cluster returns
  a single group → stop), while a drifted blob fragments into sub-themes. Each fragment is then re-scored
  by jury convergence — sub-fragments that lose the ≥2-distinct-leading-jury bar are simply **not
  promoted** (correct: they were only a "theme" by accident of the blob).
- **Live effect:** "AI in 2026" **379 → 7 signals**; total Track-B private orgs **489 → 121** (the blob's
  hundreds of loosely-related YC startups no longer lump into one theme). Still 5 themes promoted, now
  tight (chatbots 49 / drones 34 / AI-and-math 40 / AI-in-2026 7 / nuclear 5). Re-measured drones still
  β_spec 0.081, so the §9.5 comparison stands. Backward-compatible (no `max_cluster_size` ⇒ old behavior).
  157 tests (was 155; +2).
- **Still ⚙:** `max_cluster_size=60`, `split_tau_step=0.08`, `tau_ceiling=0.85` are unfit defaults like
  every discovery knob — the back-test calibrates them. This is an algorithmic guard against the
  *failure mode*, not a fitted value.

---

## D33 (2026-06-26) — Discovery HTML report (makes the module's output consumable)

The discovery module was fully functional (D27–D32) but its rich output was CLI-text only. `--report`
renders a single self-contained HTML diagnostic — the discovery analogue of `render_radar` — to
`_intermediate_outputs/discovery_report.html`.

- **`render_discovery.render(report, out_path)`** (pure, inline-styled, no network) + **`assess.build_report`**
  (assembles the render dict). Per discovered theme, ranked by combined score: the **juries** that
  converged, **nascency** (β_jury / recency / runway), **corpus diffusion** (β_spec / p_main / gate when
  measured, else "run radar"), **Track A** investable tickers, **smart-money** new buys, and the **Track
  B** private watchlist (collapsed). A **hand-seeded baseline** table closes it for the §9.5 eyeball.
- Read-only diagnostic ⇒ single self-contained HTML in `_intermediate_outputs/` (matching `render_radar`),
  not the edit-persisting template+sidecar split (that convention is for reports that save user edits).
- CLI `5_discovery.py --report [--open-browser]`. Live: 5 themes, ~9 KB, all sections present (Track-A
  absent only because the post-D32-split themes have no resolved listed ticker — conditional rendering).
  159 tests (was 157; +2).

This is the consumable capstone of the discovery vertical (D27–D33). All remaining work is calibration
(the ⚙ knobs on the back-test), data-gated wiring (per-source snapshot parsers, §4b ETF-delta, §3a
backfill), or gated on the panel reaching n≥100 (the full forward-return back-test).

---

## D34 (2026-06-26) — Weekly release-calendar logic: fetch a jury only when DUE (spec §5a)

D26 added the calendar *config* (`discovery_calendar.yaml`) but nothing *consulted* it — every step was a
manual flag and an ingest fetched all parseable sources every time. `discovery/calendar.py` is the logic
that decides which jury sources are **DUE** this week, so a typical week is a near no-op (annual juries
skipped ~50 wks/yr).

- **`is_jury_due`** (pure): `quarterly` → never here (funds); `annual` → due only if this year's edition
  isn't captured yet (watermark = `MAX(jury_signals.year)` per source) AND we're in/after its publish
  window; `continuous`/`daily`/`weekly`/unknown → always poll (cheap, watermark-deduped). **`is_funds_due`**
  → true only in the ~3 weeks after a 13F deadline (Feb/May/Aug/Nov). **`due_jury_sources`** wraps these
  over the registry + watermark.
- **CLI:** `--due [--as-of DATE]` shows what the weekly run would fetch; `--weekly` runs the due-gated
  pipeline (fetch only due juries → converge → rank → report, + funds cross-ref iff the 13F window is
  open). `cmd_ingest` gained a `due` filter.
- **Live proof:** as of 2026-06-27, **2 of 3** ingestable juries due (YC continuous + the MIT-TR
  snapshot; **Nobel correctly skipped** — annual, its window is October); simulated 2026-10-05 → Nobel
  becomes due (2026 edition not yet captured). 164 tests (was 159; +5).
- **Note:** only sources discovery can actually parse (the build-first APIs + archived snapshots) are
  considered; the ~79 leading juries still need per-source snapshot parsers before the calendar fully
  exercises them — but the *when-to-fetch* logic is now correct and tested.

---

## D35 (2026-06-26) — Third build-first jury: CNCF landscape (+ registry re-seed to 108)

Discovery's convergence was thin — only YC contributed real entities, so themes formed mainly from
YC↔MIT-TR overlap. Added **CNCF** (`cncf_sandbox`), the third build-first machine-readable jury
(`parsers.parse_cncf`/`fetch_cncf`, added to `API_FETCHERS`): the cloud-native `landscape.yml`, a
*leading, high-credibility* software jury. One signal per **CNCF-accepted project** (those with a
`project` maturity — sandbox/incubating/graduated — not every landscape member); `entity_type='technology'`
(project != company, per the registry note), acceptance year = the leading signal; fail-open on
network/YAML.

- **Registry re-seed:** the DB held only the original 70 sources (frozen v1); the D23 expansion to 108
  was config-only. Ran `5_registry.py --seed` → **+38 sources** (now 108, incl. `cncf_sandbox`), so
  `annotate_from_registry` + the D34 calendar see the new juries.
- **Live effect — the convergence thesis working:** ingesting CNCF (**238 signals**) and re-converging
  took discovery **5 → 12 themes**. The 7 new ones are cloud-native infra (Cadence Workflow, bpfman/eBPF,
  CDK8s, metal3 bare-metal, Apicurio schema registry, Serverless Workflow…) — formed where CNCF projects
  converge with YC software startups (each backed by 2 independent leading juries). One extra independent
  jury surfaced a whole new sector, exactly as the model predicts. 167 tests (was 164; +3).
- **Note:** CNCF entities are technologies, so they strengthen *theme formation*, not the Track-A/B org
  roster directly (the company behind a project is a later enrichment, per the registry note). Product
  Hunt (OAuth) and ARPA-E (less-clean data portal) remain the other build-first candidates.

---

## D36 (2026-06-26) — Security hardening pass for external-content fetches + the untrusted-data policy

The discovery module pulls third-party web data (YC, Nobel, CNCF, the SEC ticker map). This pass hardens
that surface and writes down the standing policy.

**The load-bearing property — no prompt-injection surface:** the discovery pipeline is **zero-LLM**
(local MiniLM embeddings + cosine math; no model reads the fetched text). A poisoned jury entry
("ignore previous instructions…") is just a string that becomes a vector — there is nothing to instruct.
**This changes only when the optional Claude theme-*naming* step (spec §6) is built:** at that point
scraped text enters a prompt and MUST be treated as untrusted data (delimited, the model told it is
content not instructions, never allowed to trigger tools/actions). That code does not exist yet.

**What was already safe (verified, not assumed):** external content is **parsed, never executed** —
`json.loads` + `yaml.safe_load` (never `yaml.load`, which can construct arbitrary Python objects); no
`eval`/`exec`/`pickle`/`subprocess` in the path. SQL is fully **parameterized** (bound `?`/named, never
string-built). Endpoints are **hardcoded constants** (no SSRF from fetched data). HTTPS, GET-only, **no
credentials sent** (nothing exfiltrates). Fetchers are **fail-open + injectable** (a hostile/garbage
response → `[]`; tests never touch the network). Single-user **local** tool (bounded blast radius).

**Fixed this pass:**
1. **Response size cap** — `parsers._default_http_get` now reads one byte past `MAX_RESPONSE_BYTES`
   (64 MB) and rejects oversized bodies, so a hostile/broken host can't exhaust memory (no
   `Accept-Encoding` is sent ⇒ no gzip-bomb vector either). `resolve` (the SEC ticker-map fetch) now
   routes through this capped getter.
2. **`href` scheme allow-list** — `render_radar._safe_url` admits only `http(s)` into an `href` (blocks
   `javascript:`/`data:` XSS from externally-sourced doc links) and adds `rel="noopener noreferrer"`.
   (The discovery report, `render_discovery`, already `html.escape`s every external string and puts no
   external URL in an `href` — so it was already XSS-safe.)
3. **Tests:** size-cap enforcement, fail-open on the cap error, and the `_safe_url` allow-list. 170 tests
   (was 167; +3).

**Standing policy (applies repo-wide):** treat all fetched/scraped content as untrusted — safe loaders
only, parameterized SQL, `html.escape` + http(s)-only hrefs in any report, size-capped reads. Any future
provider that needs a key (Product Hunt, ARPA-E — deferred; user won't register) must read it from
env/`.env`, never log or commit it. **Follow-up (DONE in D37):** propagate the size cap to the OD-2
archive getter and the Wave-1..4 ingest clients (`arxiv`/`gdelt`/… were still uncapped) — same one-line
pattern.

---

## D37 (2026-06-27) — Size-cap propagated repo-wide; shared helper; the missed `fundamentals` getter closed

Completes the D36 follow-up: every external HTTP getter in 5_Hype now bounds its response body, so no
single hostile/broken host can exhaust memory. The work was the **whole-repo security audit** (S10;
`../SECURITY_AUDIT.md`) and is now verified complete and consistent.

**What was done:**
1. **Shared helper** — `src/hype_parser/nethttp.py::capped_read(resp, max_bytes=64 MiB)` reads one byte
   past the cap and raises `ValueError` on overflow (no `Accept-Encoding` sent ⇒ no gzip-bomb). This is
   the canonical implementation; the discovery getter (`discovery/parsers.py`) keeps its equivalent
   inline cap from D36.
2. **Wired into all 11 Wave-1..4 ingest getters** (`arxiv`, `gdelt`, `wikipedia`, `europepmc`,
   `clinicaltrials`, `hackernews`, `edgar_fts`, `nih_reporter`, `nsf`, `sbir`, `patentsview`) **and the
   OD-2 `archive.py` getter** — each now `capped_read(resp)` instead of a bare `resp.read()`. Callers stay
   fail-open (the raised `ValueError` degrades to `[]`/no-write, the house style).
3. **The getter the first pass MISSED** — `fundamentals._default_http_get` (the SEC companyfacts /
   `company_tickers.json` fetch) was still doing a bare `resp.read()`. It is the one most worth capping
   (companyfacts JSON is the largest single body 5_Hype pulls). Now routed through `capped_read`. This was
   the real remaining gap; with it closed, **`grep -rn '\.read()' src/hype_parser` returns nothing.**
4. **Tests** — `tests/test_nethttp.py` covers the cap *semantics* (oversized rejected / under-cap passes /
   exact-cap boundary); a new `test_fundamentals.py::test_default_http_get_routes_through_size_cap` locks
   the *wiring* of the previously-missed getter against silent regression. **174 tests** (was 173; +1 here,
   the +3 nethttp tests landed with the audit).

**Note on the stale notes:** the handoff §4(a) already claimed this done (correctly, for the ingest +
archive getters) while the §8 file-map still said "getters still uncapped" and `fundamentals` was in fact
uncapped — the inconsistency that prompted the recheck. Both are now corrected. Standing rule unchanged
(D36 policy): all fetched content is untrusted; size-capped reads on every getter.

---

## D38 (2026-06-27) — Toward listed-heavy discovery: openFDA biotech jury + SEC-UA fix; finding = one jury can't promote

**Context (the §4(c) pivot, the panel workstream):** before spending the radar network run, we set out to
**broaden discovery toward sectors with LISTED constituents** so the discovered universe can feed the
panel (the 12 themes to date are CNCF/YC-heavy → resolve to *private* orgs). The DB confirmed it: 12
discovered themes, **0 with a `theme_series`, only 1 with any Track-A ticker**. Biotech is the natural
lever (the spec's "breakthrough designations" jury type; 2_Funds already supplies 21 biotech specialist
funds). Also wired the candidate-set union for the panel — see the panel change below.

**Built (the openFDA biotech jury):** `parsers.parse_fda_approvals` + `fetch_fda_approvals` (registered in
`API_FETCHERS` as `fda_drug_approvals`; new registry entry, re-seeded → 109 sources). Build-first: openFDA
`drugsfda`, server-side-filtered to **PRIORITY ORIGINAL approvals** (`submission_type:ORIG AND
review_priority:PRIORITY AND submission_status:AP`) so the **regulatory jury** signal is the novel
therapy, not the generic/labeling-supplement noise. One signal per application: `entity` = **sponsor
company** (mostly LISTED pharma/biotech → Track-A), `item_text` = brand + active ingredients + dosage/route
+ submission class, `year` = earliest ORIG/AP date (not a later SUPPL). Live: **275 signals, 185 distinct
sponsors, 2016–2026** (IMMUNOCORE, NOVARTIS, BRISTOL, SERVIER…). +3 parser tests.

**Fixed (a real pre-existing bug):** the discovery parsers' `USER_AGENT` lacked a contact, so SEC's
fair-access policy **403'd `resolve.load_name_index`** (SEC `company_tickers.json`) — and because the
fetch fail-opens, it **silently zeroed Track-A resolution for *every* theme** (the "only 1/12 has Track-A"
symptom was partly this, not only private orgs). Aligned the UA with the working `fundamentals.USER_AGENT`.
Post-fix the resolver runs: 1 listed / 222 private / 0 unknown across the 12 themes — confirming the
current themes' company-entities are genuinely **private-heavy** (their listed constituents would come from
the radar's EDGAR-FTS linkage, not jury org-resolution).

**THE FINDING (empirical, decisive):** adding openFDA **alone does NOT promote any biotech theme.**
Convergence requires **`min_leading_juries ≥ 2`** (independence; D27/`discovery.yaml`), and the 275 FDA
signals cluster among *themselves* (one jury) — drug-approval text does not co-cluster with the tech-heavy
YC/CNCF juries at `tau=0.55`. So every pure-FDA cluster is ineligible; the promoted set stayed the same 12
tech themes, and **FDA's listed sponsors are never surfaced** (no biotech theme to attach them to). **A
single biotech jury is insufficient — biotech needs a PARTNER leading jury whose text co-clusters with
drug approvals** (candidate build-first partners: ClinicalTrials.gov sponsors — shares drug vocabulary, an
API already in-repo; or a biotech awards jury).

**RESOLVED (user decision):** take the regulatory-jury exception — a single high-credibility *regulatory*
jury (an FDA priority approval is itself a multi-reviewer consensus) may promote a cluster on its own.
Implemented NOT as a global `min_leading_juries=1` (that would let any one startup-award promote) but as an
explicit allow-list `convergence.solo_leading_sources: [fda_drug_approvals]` (⚙). `_eligible` now: enough
`min_signals` AND (`n_leading_juries ≥ min_leading_juries` OR the group contains a solo-promote leading
source). `score_group` exposes `leading_sources` for the check; empty allow-list = the strict ≥2 rule,
unchanged. **Live result:** discovered themes **12 → 28**, Track-A listed constituents **1 → 18** — 16
biotech themes now surface listed sponsors the panel can screen (AZN, ABBV, AMGN, BIIB, BMY, GILD, INCY,
LLY, PFE, NVS, ADCT…). +1 test (`test_solo_leading_source_promotes_alone_else_needs_two`).

**Known limitation (follow-up):** FDA-cluster theme *labels* are the sponsor company name (e.g. "ASTRAZENECA
AB") because `_group_label` picks the top leading signal's entity — fine for the panel (Track-A tickers
resolve correctly) but a poor theme name, and `diffusion_bridge.derive_query` would then query the *company*
not the modality. Fix later: label/​query a solo-regulatory cluster by its dominant active-ingredient/drug-
class text instead of the sponsor. Conservative SEC name-matching also leaves foreign/private sponsors
(Bayer, Ipsen, Servier) as Track-B — expected.

**Also (panel §4(c) step 2):** `panel_builder.candidate_tickers(edgar, track_a, max_n)` (pure, +3 tests) +
`build_crude` now screens **EDGAR ticker-linkage ∪ discovered-theme Track-A** per theme (no-op for
hand-seeded themes; theme-agnostic gates unchanged). Inert until the radar gives discovered themes a series.

**Tests: 181** (was 174; +3 FDA parser, +3 candidate_tickers, +1 solo-promote). Registry 109 sources (re-seed
needed before a backtest window — still frozen at `v1`; §6.7). Discovered themes 28, Track-A constituents 18.
Next: run the radar across the discovered themes (gives them a series + EDGAR linkage), then rebuild the
crude panel on EDGAR ∪ Track-A and re-check the Protocol §2.3 strata toward n≥100.

---

## D39 (2026-06-27) — Two discovered-theme→radar bridge bugs (found by the first radar run)

The first `5_radar.py --include-discovered` run measured the 12 query-ready discovered themes but came back
near-useless: most got a `theme_series` with **0 member-months** (e.g. drones: 1080 candidate docs, 0
members) and **0 EDGAR tickers** — so they'd contribute nothing to the panel. Two real bugs in
`diffusion_bridge.py`, both now fixed:

1. **Descriptor/keywords clobbered by re-converge.** `convergence.promote()`'s `ON CONFLICT` overwrites
   `themes.descriptor` + `themes.keywords` with the members' raw `item_text`/entities (YC startup-speak)
   on every run, but leaves `arxiv_query` — so running `--diffusion-queries` *then* re-converging (as D38
   did) leaves a clean arxiv query beside a startup-speak descriptor. The radar's membership centroid =
   `encode(descriptor + keywords)`, so it stopped matching the research corpus → empty membership (the
   D30 "0 members" failure, re-triggered). **Fix:** `load_discovered_radar_themes` now **re-derives** the
   topic query + descriptor from the (clobber-proof) `label` at read time via `derive_query`, instead of
   trusting the stored columns. Immune to converge/diffusion-queries ordering.
2. **No EDGAR ticker linkage for discovered themes.** `_process_theme` only runs Wave-3 EDGAR FTS when the
   theme dict has an `edgar_query`; hand-seeded themes get one from the seed YAML, but the discovered-theme
   dict never had one → `theme_tickers` empty for every discovered theme (the listed constituents the panel
   needs). **Fix:** `derive_query` now emits an `edgar_query` (quoted-phrase FTS), surfaced by
   `load_discovered_radar_themes`.

Extended 2 existing bridge tests (edgar_query present; clobber-resilience of the re-derive — count stays
181). Re-ran the radar after the fix. Note the
root cause #1 (promote clobbers descriptor) still exists in `promote()` itself — the bridge now defends
against it, but the canonical pipeline order remains converge → diffusion-queries → radar.

**Third gotcha — SWR hid the EDGAR fetch.** After the descriptor fix the second radar run recomputed
membership from cache (drones 0 → 187 member-months ✓) but still wrote **0 EDGAR tickers**: `_process_theme`
guards EDGAR behind `if do_fetch:`, and `do_fetch = args.refresh or _theme_is_stale(theme, ttl=7d)` — the
first run had just made every theme "fresh", so the second skipped all fetching. EDGAR for the discovered
themes had never been fetched (run 1 had no `edgar_query`), so there was nothing cached to recompute from.
Fixed operationally with a **targeted EDGAR backfill** (call `edgar_fts.fetch_yearly` per discovered theme
+ `write_theme_tickers`, no arxiv refetch). Result: **drones → 75 tickers** (UAVS, BLDP, GPRO, OPTT…),
**nuclear-power → 67** (BWXT, LTBR, UEC, ETR…) — the cheap small-caps the screener targets. The noisy
"ai-*" themes resolved many junk tickers (broad/drifty label queries — the D38 label-quality follow-up
again) but carry ~0 membership, so the panel's nascency gate (`β>0 ∧ n_spec≥5`) auto-filters them; the
cloud-native OSS themes resolve 0 (no listed filers). Lesson: a per-theme `--refresh`/EDGAR-only path would
make this a one-command operation — a small radar-CLI follow-up.

**First horizon-fixed rebuild result (the motivation for D40):** panel **57 → 87 rows** (+30, all from
**drones** — UAVS/RCAT/DPRO/ONDS/AVAV/KTOS…), 5 positives. Of 12 discovered themes only drones contributed
(the only one with BOTH dense membership *and* relevant tickers); nuclear + ai-* have tickers but too-thin
membership (nascency gate filters), cloud-native have 0 tickers. Strata still violates: **n=87<100 AND
positives 5≪50**. The ≥50-positives gap (~6% positive rate ⇒ ~n800) is the real wall → D40.

---

## D40 (2026-06-27) — Horizon-aware H (spec D22): the forward-return window = the theme's diffusion runway

**Why (the ≥50-positives wall):** the crude panel labels a positive as forward-return ≥ +100% over a
**fixed 52-week** window for every theme. But a nascent theme's re-rating plays out over its *diffusion
runway* — drones' convergence horizon is ~3.5yr — so a 52w window systematically **under-counts** the
re-rating, depressing the positive rate (5/87 ≈ 6%, which implies n≈800 to reach 50 positives). User picked
this as the highest-leverage next push.

**What:** each theme now gets its own horizon `H` = its diffusion runway. `panel_builder.theme_horizon_weeks`
(pure, +2 tests): a DISCOVERED theme uses its convergence `horizon_years` (D29) × 52, clamped to
`[min,max]_horizon_weeks` (26..208w); a hand-seeded theme (no `horizon_years`) falls back to
`default_horizon_weeks`. `build_crude` computes `prim_h` per theme, fetches forward returns at the standard
13/26/52 **∪ prim_h**, and labels + decomposes m_share (t0→t0+H) + feeds the kill-switch at `prim_h`. The
chosen window is stored as a `horizon_weeks` feature and added to the crude **controls** (partials out the
mixed-window effect; the OLS `pinv` tolerates it being constant in fixed mode). `--killswitch` reads each
row's stored `horizon_weeks`. New config (`crude.horizon_mode: theme_aware | fixed`, `default/min/max_
horizon_weeks`); **`horizon_mode: fixed` reproduces the exact pre-D40 single-horizon behaviour.**

**Trade-off (expected):** a longer H needs more elapsed time, so some recent-t0 rows drop (n may dip), but
each surviving row captures the full multi-year re-rating, so the positive *rate* should rise — the whole
point. Result of the rebuild reported inline in the handoff §3 live-numbers. **183 tests.** Still INDICATIVE
(yfinance bias, D20); H itself is a ⚙ (the runway×52 mapping + clamp band) to calibrate on the panel.

---

## D41 (2026-06-27) — Centroid enrichment for thin themes; and the arxiv-coverage structural finding

**Goal:** the theme inventory showed only **drones** (of 12 discovered) contributes panel rows — it's the
only one with BOTH dense membership AND tickers. **nuclear** has 67 great small-cap tickers (BWXT/OKLO/SMR/
UEC/LTBR) and is a slow multi-year theme like drones, but membership was only 11 → fails the `n_spec≥5`
nascency gate → 0 rows. Root cause: drones has **35 jury members** (many YC drone startups) → rich
membership centroid; nuclear has **7** (mostly duplicate MIT-TR headlines + 1 startup) → thin centroid →
few arxiv docs clear `tau_member`.

**What:** `diffusion_bridge.derive_query` now **enriches the membership centroid** (descriptor + keywords)
with the cluster's SHARED member vocabulary, while keeping the candidate-fetch query label-precise. Two
guards against the D30 drift: (1) **document-frequency** gate (a term must appear in ≥`enrich_min_df` of
member texts — the theme's common vocabulary, not one startup's tag); (2) **bigram-only** (a shared
*phrase* like 'nuclear reactors' is theme vocabulary; a shared *unigram* across YC blurbs — 'defense',
'industrials', 'saas' — is a sector tag). Live: nuclear centroid → `[nuclear power, nuclear reactors,
reactors waste]`; drones stays clean `[drones]` (the tag drift is dropped). `_shared_member_terms` helper +
2 tests (enrich vs drift). +`mean/means/like/just/only` stopwords. **185 tests.**

**Result + the structural finding:** re-measuring nuclear lifted membership **11 → 19** (+73%) — the
mechanism works — **but `max(n_spec)` is still 2**, below the `n_spec≥5` gate, so nuclear still contributes
0 rows. The deeper cause is **not** the query: **arxiv under-represents nuclear ENERGY** (322 docs over the
window, mostly nuclear *physics*; ~0.14 topical docs/month). The diffusion engine's specialist corpus is
arxiv-dominated, which measures CS/bio themes well (drones/crispr/rag — preprint-heavy fields) but
**structurally under-measures industrial/energy themes** (nuclear, batteries, materials). **The lever for
those is wave-reweighting** — lean N_spec on patents (PatentsView) + DOE/ARPA-E/SBIR grants + news for
industrial verticals, not arxiv. That's the next build for breadth beyond CS/bio; the enrichment here is
the right general fix and helps any arxiv-present thin theme. Did NOT rebuild the panel (nuclear adds 0
rows until the wave-reweight). min_n_spec_level stays 5 (a ⚙; lowering it to admit a 2-doc theme would
re-open the D15 "theme barely exists" failure — defer to panel calibration).

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
