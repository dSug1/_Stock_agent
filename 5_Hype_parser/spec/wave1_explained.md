# Wave 1 — what it actually does (as built)

**Status:** built 2026-06-26 (decisions D7/D7a/D8). Zero Claude. All model parameters are
informational defaults (⚙), unfit until the labeled panel exists.
**This document describes the *implemented* behaviour**, not the target design. The target is in
`Overall_specification.md` + `features_spec_v0.2.md`; the decisions are in `decisions.md`.

---

## 1. In one sentence

Wave 1 builds the **diffusion radar instrument** for a few hand-seeded sub-themes: it counts how
fast *specialist* attention (arXiv papers) for each theme is rising versus how much *mainstream*
attention (GDELT news + Wikipedia) it already has, and writes that to an HTML report — with **no
LLM and no stock-picking yet**.

It implements part of **Features v0.2 §1** (the `diffusion_ratio` instrument). It does **not** yet
do theme discovery, the survival filter, constituent/stock mapping, or Modules A/B (those are later
waves).

---

## 2. The pipeline, stage by stage

Entry point: `scripts/5_radar.py` (run by `run_5_Hype_parser.bat`). For each seed theme:

### Stage 0 — Load config + open the DB
- `config/themes_seed.yaml` → the seed sub-themes (currently `rag`, `ssm_mamba`, `mkras_vaccine`).
- `config/diffusion.yaml` → the ⚙ parameters (membership threshold, slope window, ingest caps).
- `data/hype.db` is opened/migrated (schema v3); each theme is upserted into the `themes` table.

### Stage 1 — Ingest (only if the theme is "stale"; SWR)
A theme is re-fetched only if it has no series yet or its last compute is older than
`cache_ttl_days` (7). `--refresh` forces it; `--no-fetch`/`--render-only` skip it.

- **arXiv (specialist numerator).** `ingest/arxiv.py::fetch_windows` queries the arXiv API **per
  year** from `arxiv_start_year` (2018) to now, up to `arxiv_max_per_year` (120) papers/year. Per-year
  windowing is deliberate: fetching "the newest N" collapses a hot theme into one month, so we slice
  by `submittedDate` to get real monthly history. Each paper → `{doc_id, title, abstract, url,
  published_at}`, stored in `documents` (deduped on `doc_id`, `published_month` derived).
- **GDELT (mainstream denominator).** `ingest/gdelt.py::fetch` calls the GDELT DOC 2.0 API in
  `TimelineVolRaw` mode → `{month: article_count}` = **N_main**. Paced (`gdelt_pause_s`) and
  retried on 429 (`gdelt_retries`/`gdelt_backoff_s`).
- **Wikipedia (mainstream level).** `ingest/wikipedia.py::fetch` calls the Wikimedia pageviews API
  (monthly) → `{month: views}`. Shown as a secondary mainstream signal; **not** used as N_main.
- **Non-destructive:** if GDELT (429) or Wikipedia (404) returns nothing, the prior good values in
  `theme_series` are kept rather than overwritten with zeros.

### Stage 2 — Embed (local, no API)
`embed.py::get_embedder` returns a `SentenceTransformerEmbedder` if `sentence-transformers` is
installed, otherwise a `HashingEmbedder` (deterministic word/bigram hashing — **lexical, not
semantic**; a placeholder). Every candidate document missing an embedding for the current model is
encoded from `title + ". " + abstract` and stored as a float32 BLOB in `documents.embedding`.

### Stage 3 — Membership → N_spec
- The theme's **centroid** = normalized mean of the embeddings of `[descriptor] + keywords`
  (`embed.centroid`).
- For each candidate document: `cosine = dot(doc_vector, centroid)`; it is a **member** if
  `cosine >= tau_member` (0.20). Stored in `theme_documents` (cosine + is_member).
- **N_spec(month)** = number of *member* documents published that month.

### Stage 4 — Assemble the monthly series
Over a contiguous month range (so slopes have no gaps), build per month:
`{n_spec (Stage 3), n_main (GDELT), wiki_views (Wikipedia)}` and write it to `theme_series`.

### Stage 5 — Compute the instrument (`diffusion.py`)
For each theme, from the stored series:
- **`beta_spec`** = OLS slope of `ln(1 + N_spec)` over the last `L` (12) months. **> 0 means
  specialist attention is accelerating** — the core nascency signal.
- **`p_main`** = `N_main / (N_spec + N_main)` ∈ [0,1] for the latest month. **Low = nascent; high =
  mainstream-saturated** (late, a sell signal in the full model).
- **`diffusion_ratio`** = `N_spec / N_main` (the literal v0.1 ratio; `inf` if N_main = 0).
- **`nascency_gate`** = `beta_spec >= beta_min (0)` AND `p_main <= p_max (0.5)`. **Informational
  only** — the thresholds are unfit, so this is not a signal to act on.

### Stage 6 — Render the report
`render_radar.py` writes a single self-contained HTML to
`_intermediate_outputs/radar_report.html` (and opens it with `--open-browser`). Per theme it shows
the metrics above, sparklines of N_spec / N_main / Wikipedia, the top member documents by cosine,
and a collapsible monthly table — plus banners stating the embedder in use and that parameters are
unfit. Themes with no specialist data render an explanatory note instead.

---

## 3. What the numbers mean (reading the report)

| Field | Meaning | Good (nascent) looks like |
|---|---|---|
| `beta_spec` | slope of specialist (arXiv) attention, last 12mo | **positive** |
| `p_main` | share of attention that is already mainstream | **low** (near 0) |
| `diffusion_ratio` | specialist / mainstream | high (specialist running ahead) |
| `N_spec total` | total member papers found | enough to be real, not noise |
| nascency badge | informational gate | ignore until parameters are fit |

The thesis (unproven until the panel): a theme with **rising specialist attention but still-low
mainstream penetration** is early on the diffusion curve — the moment to look for cheap, un-bid
stocks exposed to it.

---

## 4. Where it's stored (`data/hype.db`, schema v3)

- `themes` — the seed sub-themes (label, keywords, descriptor, per-source queries, embed model).
- `documents` — the specialist corpus (arXiv): metadata + local embedding BLOB.
- `theme_documents` — per-theme membership (doc, cosine, is_member).
- `theme_series` — the monthly `n_spec / n_main / wiki_views` per theme (what the report reads).

---

## 5. How to run

```
run_5_Hype_parser.bat        # full pipeline (gated archive + ingest + embed + diffusion) + render + open
run_5_Hype_render.bat        # render-only from cache (no network)
run_5_Hype_parser.bat --refresh    # force re-ingest (ignore the 7-day SWR cache)
```

---

## 6. Current real-data status & caveats

- **Embedder is now the semantic `all-MiniLM-L6-v2`** (D13 — `sentence-transformers` installed).
  Membership is semantic (synonymy works); the lexical hashing version remains as an automatic
  fallback if the library is ever absent. `tau_member=0.45` for the semantic model (was 0.20).
- **arXiv + Wikipedia work**; **GDELT is rate-limited (429)** under heavy use, so `N_main` is often
  empty — meaning `p_main`/`diffusion_ratio` are not yet meaningful. It populates on a later
  `--refresh` once GDELT cools, and the non-destructive cache keeps it. `N_spec`/`beta_spec` (the
  alpha-bearing numerator) are populated regardless.
- **`mkras_vaccine` has N_spec = 0** — arXiv barely covers biomedical themes. This is the intended
  source-domain gap that **Wave 2** (bioRxiv/medRxiv + ClinicalTrials.gov) closes; it currently
  shows only a Wikipedia mainstream curve.
- **Observed:** `rag` beta_spec ≈ +0.106, `ssm_mamba` ≈ +0.081 (both accelerating) — the instrument
  works end to end.

---

## 7. What Wave 1 deliberately does NOT do (later waves / phases)

- **No Claude / LLM** anywhere (per Features §8 — engine first).
- **No theme discovery** — themes are hand-seeded; the unsupervised HDBSCAN clustering + one-call
  Claude labelling of *new* themes (Features §1.1) is later.
- **No survival filter** (Features §1.6), **no `phase_on_curve`** banding.
- **No constituent expansion** — themes are not yet mapped to stocks/tickers.
- **No Module A (mispricing)** or **Module B (the 5-factor HYPE score)**.
- **No fitted parameters** — every threshold is an informational default; nothing here is a trading
  signal until the labeled point-in-time panel + kill-switch (Protocol §2–4).
