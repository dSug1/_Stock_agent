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
   similar (cosine ≥ `tau_converge`), else seeds a new group. A group is a *candidate theme*.
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

### 3. Inspect  (`--list`, `--watch`)
`--list` shows the discovered themes with their horizon, jury breakdown, and Track-A tickers; `--watch`
lists the Track-B private firms under EDGAR listing-watch.

## What the first live run showed (D27)

Ingesting **three juries** — YC (4 583 leading), Nobel (102 denominator), MIT-TR10 (103 leading) =
4 891 embedded signals — convergence produced **5 themes** where YC startups line up with MIT-TR
breakthrough topics (each backed by 2 independent leading juries; horizon ≈3.5 yr), and org resolution
split **1 listed / 489 private** — exactly right, because YC firms are private. With only the lone
MIT-TR snapshot loaded, convergence **promotes nothing** (it needs ≥2 independent leading juries) — the
honest underpowered state, the same discipline as the panel kill-switch.

## What is deliberately unfinished (and why it's safe)

- **`tau_converge` is unfit.** At 0.55 the AI startups collapse into one ~380-signal mega-cluster. The
  threshold (and the jury weights, min-juries, top-k) are calibrated only at the §9-step-5 back-test
  against the hand-seeded baseline — every value in `config/discovery.yaml` is a ⚙ knob, not a fit.
- **Snapshot extractors are crude.** Until a per-source parser tags real **companies** (and the edition
  **year**), the scrape juries contribute to *convergence* (via their text) but not to the *org roster*
  — so a breakthrough **headline** is never mislabelled as a watchlist firm.
- **The historical backfill (spec §3a) and the specialist-fund 13F provider (§4b) are not wired yet.**
  The `funds.cross_reference` scorer is pure and ready; it just needs a new-position feed (2_Funds reuse
  for biotech, EDGAR/ETF deltas elsewhere).
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
  funds.py          specialist-fund cross-reference (config + pure scorer)
```
Schema: `db.py::_migration_8` (`jury_signals`, `theme_convergence`, `theme_orgs`, `themes` +cols).
Tests: `tests/test_discovery.py` (18).
