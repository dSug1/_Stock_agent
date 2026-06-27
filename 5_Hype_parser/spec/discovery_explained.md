# Theme discovery — as-built walkthrough (plain language)

This explains the **discovery module as it actually runs today** (decision D27; schema v8). It is the
first build of Workstream 2 — the part that *finds* themes instead of hand-seeding them. Pair it with
the design doc `discovery_spec_v0.2.md` (the why) and `decisions.md` D21–D27 (the history).

## The one idea

A theme worth catching early is one that **independent experts are already recognizing while the
mainstream is not**. Those experts leave sparse, public, structured traces — startup-accelerator
batches, award shortlists, breakthrough designations, agency calls, and specialist-fund new buys. When
*several independent* juries point at the **same** nascent area, that **convergence** is the discovery
signal. It is tiny data, high signal, and forward-looking — the opposite of clustering millions of
papers.

## The pipeline, step by step

Run it with `scripts/5_discovery.py`. Three phases:

### 1. Ingest — juries → `jury_signals`  (`--ingest`)
Each jury's edition is parsed into rows of **one recognition each**: `(source_id, year, item_text,
entity, entity_type, url)` plus the jury's `diffusion_position` (leading / bridge / denominator) and
`jury_credibility` copied from the source registry.

- **Build-first = machine-readable feeds** (`parsers.API_FETCHERS`): **Y Combinator** (the public
  `yc-oss` JSON — thousands of startups with batch, one-liner, tags; a *leading* jury that names
  **private** firms) and the **Nobel Prize** API (a *denominator* jury — it dates a theme, it does not
  find it: the Pouzin nuance). Both fetch-then-parse with an injectable HTTP function and fail open, so
  tests feed canned payloads with no network.
- **Scrape juries** (MIT-TR10, R&D 100, Fierce 15, SPIE, BNEF…) are **not re-fetched here** — the OD-2
  forward archive already snapshots them. `parse_snapshot_source` reads the latest archived edition and
  extracts candidate items. Today's generic HTML extractor is **deliberately crude** (it pulls
  `<li>`/`<h2..h4>` text and drops boilerplate); robust per-source extractors are the next step.
- Every signal's short `item_text` is **embedded locally** with the same MiniLM model the diffusion
  engine uses. Dedup is on `(source_id, year, item_hash)`, so re-ingesting an unchanged edition writes
  nothing.

### 2. Converge — signals → discovered `themes`  (`--converge`)
This is the heart.

1. **Cluster** the signal vectors greedily: each signal joins the existing group whose centroid is most
   similar (cosine ≥ `tau_converge`), else seeds a new group. A group is a *candidate theme*. Then any
   **oversized** group (centroid-drift mega-cluster) is **split** by re-clustering it tighter (D32) —
   self-correcting: a real theme stays whole, a blob fragments (this is what took the "AI in 2026" blob
   from 379 signals down to a tight 7).
2. **Score** each group by **convergence, not volume**:
   `score = Σ over DISTINCT sources of (credibility_weight × position_weight)`. One jury naming fifty
   companies counts **once** — what matters is how many *independent, credible, leading* juries agree.
   **Denominator juries contribute 0** to this score (their `position_weight` is 0) — they only set the
   horizon.
3. **Promote** the eligible groups (≥ `min_signals` AND ≥ `min_leading_juries` *distinct leading*
   sources) to `themes` rows, writing the membership to `theme_convergence`. Each gets a
   `horizon_years` estimate from which tiers fired (leading-only ⇒ long runway; bridge ⇒ mid;
   denominator ⇒ ~0, likely too late) — heuristic until calibrated.
4. **Classify constituents** (`resolve.py`): every *company* entity in a promoted theme is matched
   against the SEC ticker map. A confident match ⇒ **Track A (listed)** — investable, carries a ticker.
   No match ⇒ **Track B (private)** — recorded with a `listing_watch` so EDGAR can be monitored for an
   S-1/F-1/424B/S-4, flipping it to listed on first filing (the IPO is often the re-rating catalyst).

### 3. Inspect + confirm  (`--list`, `--watch`, `--funds`, `--assess`, `--report`)
`--report` renders the whole picture as a single self-contained HTML diagnostic
(`_intermediate_outputs/discovery_report.html`, the discovery analogue of the radar report): each
discovered theme ranked by combined score with its juries, nascency, corpus β_spec, Track-A tickers,
smart-money, and the Track-B watchlist, plus the hand-seeded baseline for comparison.

`--list` shows the discovered themes with their horizon, jury breakdown, and Track-A tickers; `--watch`
lists the Track-B private firms under EDGAR listing-watch.

`--funds` adds the **specialist-fund smart-money confirmation** (spec §4b, D28): it reads the newest
quarter's **NEW 13F positions** from `2_Funds_parser`'s holdings DB (the D3 reuse — a `(fund, ticker)`
is new if held this quarter but not last, latest filing per period winning) and crosses them against
each discovered theme's Track-A tickers. A specialist opening a position in a theme constituent while
the theme is still early is a *capital jury* — an independent expert vote, in money. Today the 21 funds
are biotech, so this fires once biotech/listed-heavy themes are discovered; non-biotech sectors fall
back to thematic-ETF holdings deltas (the remaining wiring).

## What the first live run showed (D27)

Ingesting **three juries** — YC (4 583 leading), Nobel (102 denominator), MIT-TR10 (103 leading) =
4 891 embedded signals — convergence produced **5 themes** where YC startups line up with MIT-TR
breakthrough topics (each backed by 2 independent leading juries; horizon ≈3.5 yr), and org resolution
split **1 listed / 489 private** — exactly right, because YC firms are private. With only the lone
MIT-TR snapshot loaded, convergence **promotes nothing** (it needs ≥2 independent leading juries) — the
honest underpowered state, the same discipline as the panel kill-switch.

## The weekly run — fetch only what's due (D34)

`--weekly` is the operational entry point. It consults `config/discovery_calendar.yaml` (D26) via
`calendar.py` and fetches a jury **only when DUE** — its publish window is open AND this year's edition
isn't already in the DB (the watermark is `MAX(jury_signals.year)` for that source). So most weeks are a
near no-op: annual juries are skipped ~50 weeks/year, only the continuous feeds (YC) poll, plus any award
in its window, plus the specialist-fund cross-ref in the ~3 weeks after a 13F deadline. Then it runs
converge → rank → report on whatever changed. `--due [--as-of DATE]` shows what a given week would fetch
without running it (e.g. on 2026-06-27 only YC + the MIT-TR snapshot are due; Nobel waits for October).

## Measuring discovered themes — the diffusion bridge (D30)

Convergence promotes a theme, but to know *where on the diffusion curve* it sits — the whole point
("entered early on the diffusion curve") — the diffusion engine has to measure its **β_spec / p_main**
on real literature. That engine is query-driven, and a promoted theme has no queries. The bridge
(`diffusion_bridge.py`) fills the gap, zero-Claude:

1. `5_discovery.py --diffusion-queries` derives an `arxiv_query` / `gdelt_query` / `wiki_article` and a
   topic descriptor for each discovered theme, **label-first** — the convergent label *is* the curated
   topic, so the query is built from it; the member jury blurbs only enrich a content-less label
   (they drift — YC sector tags like "defense/saas" pull an off-topic corpus). The columns already
   exist on `themes`, so no schema change.
2. `5_radar.py --include-discovered` then processes the discovered themes exactly like hand-seeded
   ones — fetch corpus → embed → membership → `theme_series` → β_spec / p_main / nascency_gate.

**A real finding from the first run:** it returned **0 member-months**. The broad auto-query
(`drones OR defense OR industrials`) plus the jury-blurb descriptor gave a corpus whose best cosine was
0.354 — below `tau_member = 0.45`. Switching to a **label-first** query (`all:"drones"`) and a
topic-facing descriptor fixed it: max cosine **0.623**, **17 member-months / 59 N_spec**, top docs all
genuine drone papers → **β_spec = +0.081, nascency_gate = True**. So a *discovered* theme is now
measured by the same instrument as a hand-seeded one — which is exactly what the §9-step-5 back-test
compares. (p_main read 0 only because GDELT was rate-limited that run.)

## Assessing discovery — two signals fused, compared to the baseline (D31)

A theme's "how early is it?" now has **two independent reads**: the **jury timeline** (`beta_jury`,
recency — `nascency.py`) and the **corpus diffusion** (`beta_spec`, `p_main`, `nascency_gate` —
the radar, via the bridge above). `assess.py` (`5_discovery.py --assess`) does two things:

1. **Fuses** them per discovered theme: `combined_score = jury rank_score × (1 + max(0, beta_spec))`
   when the theme has been measured on the diffusion engine, else jury-only. Measuring can only *raise*
   a theme's rank — an unmeasured theme is never penalised.
2. **Compares** the discovered themes against the **hand-seeded baseline** on the *same* corpus
   `beta_spec` — the §9-step-5 question: does discovery surface themes that look as early/accelerating
   as the curated ones?

**The encouraging live result:** the discovered **drones** theme scored `beta_spec = 0.08` — on par
with or above the best hand-seeded themes (Mamba 0.07, RAG 0.04; CRISPR is already mainstream at
`p_main` 0.81; mKRAS negative). Median discovered β_spec (0.081) beat the seed median (0.032). That is
the first real signal that jury-convergence finds genuinely-nascent themes — not just *any* themes. (It
is the diffusion-signature comparison; the full forward-return panel back-test, Protocol §4, is
separate and gated on the panel reaching n ≥ 100.)

## What is deliberately unfinished (and why it's safe)

- **`tau_converge` is unfit.** At 0.55 the AI startups used to collapse into one ~380-signal mega-cluster;
  the D32 oversized-split now fragments that blob (→ 7), but the threshold (and the jury weights,
  min-juries, top-k, `max_cluster_size`) are calibrated only at the §9-step-5 back-test against the
  hand-seeded baseline — every value in `config/discovery.yaml` is a ⚙ knob, not a fit.
- **Snapshot extractors are crude.** Until a per-source parser tags real **companies** (and the edition
  **year**), the scrape juries contribute to *convergence* (via their text) but not to the *org roster*
  — so a breakthrough **headline** is never mislabelled as a watchlist firm.
- **The specialist-fund 13F provider (§4b) is wired for biotech** (D28 — `new_buys_from_2funds` reads
  the 2_Funds holdings DB). The **ETF holdings-delta** fallback for the non-biotech sectors is the
  remaining piece. **The historical backfill (spec §3a)** for the scrape juries is not wired (the YC +
  Nobel APIs already carry full history).
- **No Claude anywhere** — convergence is cosine geometry over local embeddings, per the build economics.

## Files

```
config/discovery.yaml                       # every ⚙ knob (tau, weights, horizon tiers, resolve conf)
scripts/5_discovery.py                       # CLI: --ingest / --converge / --list / --watch
src/hype_parser/discovery/
  signals.py        jury_signals store + local embedding + dedup
  parsers.py        YC + Nobel feeds (build-first) + crude snapshot-jury framework
  convergence.py    greedy cluster → convergence score → promote themes + horizon
  resolve.py        listed/private classification (SEC ticker map) + listing-watch
  funds.py          specialist-fund cross-reference (config + scorer + 2_Funds 13F new-buys reader)
  nascency.py       jury-timeline nascency gate / ranking (see discovery_nascency_explained.md)
  diffusion_bridge.py  zero-Claude diffusion queries so the radar can measure discovered themes (D30)
  calendar.py       release-calendar DUE logic — the weekly run fetches only due juries (D34)
  assess.py         fuse jury-timeline + corpus diffusion; compare discovered vs hand-seeded (D31) + build_report (D33)
(render_discovery.py at package root — the HTML diagnostic, D33)
```
Schema: `db.py::_migration_8` (`jury_signals`, `theme_convergence`, `theme_orgs`, `themes` +cols).
Tests: `tests/test_discovery.py` (18).
