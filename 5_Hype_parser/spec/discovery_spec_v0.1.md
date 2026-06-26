# Theme Discovery — module specification v0.1 (DRAFT) — ❌ SUPERSEDED by v0.2

**SUPERSEDED (2026-06-26) by `discovery_spec_v0.2.md`.** The user rejected this broad-ingest-and-cluster
design: (1) it implies several GB of storage — the budget is a *couple of MB*; (2) patents are too
broad/noisy; (3) it ignores the **expert-jury / insider-recognition** signal (awards, breakthrough
designations, specialist smart-money) that is the actual edge; (4) it lacks a per-theme **time
horizon**. v0.2 replaces volume-clustering with jury-convergence. Kept only for the decision trail.

---

**Status:** draft; **gated on the open corpus-scope decision (D21).** This specifies the module that
makes the theme list an **output** (clusters of emergent specialist activity) instead of a hand-written
input. Depends on: the existing ingest clients (Waves 1–4), `embed.py`, `diffusion.py`.

> Read `decisions.md` D21 first (why we're building this), then Features §1.1 (the spec'd intent).

---

## 0. Design principles (the non-negotiables)

1. **Bounded, linear growth — never exponential.** Documents arrive at a roughly *constant* rate per
   source per month. Growth is linear in (sources × months) and is hard-capped at every level. There
   is no fan-out, no per-document expansion, nothing that compounds.
2. **Embeddings are the asset, raw text is optional.** A 384-float vector (1.5 KB) is what clustering
   needs; the abstract (1–3 KB) can be dropped after embedding. Default keeps a truncated abstract
   for inspection; a `drop_text_after_embed` switch roughly halves storage.
3. **Bulk only the cheap, high-signal sources; everything heavy is TARGETED post-clustering.** Papers
   + grants are bulk-ingested (untargeted). Patents and clinical trials and EDGAR are queried *only
   for already-discovered clusters* — so the multi-hundred-k-row sources never bulk-land.
4. **Zero paid subscriptions; zero Claude in the hot path.** All sources are free APIs; embeddings are
   local; clustering is local. Claude is one optional cheap call to *name* a surviving cluster.
5. **Incremental + idempotent.** Each run advances a per-source watermark and pulls only the new
   window; re-running is a no-op (dedup on `doc_id`).

---

## 1. Pipeline (five stages)

```
(1) BULK INGEST        untargeted, by-category, windowed, watermarked  -> documents (corpus='broad')
(2) EMBED              local MiniLM on new docs only                   -> documents.embedding
(3) CLUSTER            HDBSCAN on a RECENT window of embeddings        -> discovery_clusters (centroids)
(4) NASCENCY RANK      project full corpus onto each centroid;          -> rank by beta_spec / p_main
                       reuse diffusion.py (beta_spec up, p_main low)
(5) PROMOTE            top nascent clusters -> candidate themes;         -> themes (+ optional Claude label)
                       targeted patent/clinical/EDGAR enrich for each   -> constituents (incl. unlisted)
```

Stages 1–2 run on a schedule (monthly incremental). Stages 3–5 run on demand ("what's emerging now?").

---

## 2. How the ingest is managed

- **Untargeted, by-category** — we pull *all* documents in a whitelisted set of arXiv categories /
  bioRxiv-medRxiv / grant programs for a time window, **not** by theme keyword. That's the whole point:
  unknown themes have to be present to be discovered.
- **Windowed + watermarked** — ingest iterates `[from_month … to_month]`; a per-source watermark row
  records the last completed window. A run pulls only months after the watermark, so steady-state is a
  small monthly append, and a one-time **backfill** (e.g. from 2017) is just the same loop over more
  windows. Idempotent: every doc has a deterministic `doc_id` (e.g. `arxiv:<id>`), `INSERT … ON
  CONFLICT DO NOTHING`.
- **Per-window hard caps** — `max_docs_per_category_per_month`. The ingest *cannot* exceed
  caps × categories × months; there is no path to unbounded intake.
- **Same proven plumbing** — injectable HTTP callable (offline tests), fail-open (a 429/500 skips that
  window, watermark not advanced, retried next run), SEC/arXiv rate-limit politeness. This is the
  Wave-1–4 pattern, reused.
- **Reuses the existing clients** — `ingest/arxiv.py`, `europepmc.py`, `hackernews.py`,
  `nih_reporter.py`, `nsf.py`, `sbir.py`, `patentsview.py` already fetch + normalize each source; the
  new code is a thin *untargeted* driver (category/window loop) around them + the clustering stage.

---

## 3. Does the database grow exponentially? — No. The numbers.

Growth is **linear and capped**. Concrete order-of-magnitude (broad scope = papers + grants):

| source | ~new docs/month (untargeted, relevant categories) |
|---|---|
| arXiv (cs.*, eess, stat.ML, q-bio, cond-mat, physics.optics, …) | ~12,000 |
| bioRxiv + medRxiv (via Europe PMC) | ~4,000 |
| NIH RePORTER + NSF + SBIR (new awards) | ~3,000 |
| **total** | **~20,000 / month** |

Per-doc cost in SQLite: 384-float embedding 1.5 KB + truncated title/abstract ~2 KB + metadata ≈
**~4 KB** (≈ **1.8 KB** with `drop_text_after_embed`).

- **Monthly steady state:** 20k × 4 KB ≈ **80 MB/month** (~40 MB text-dropped).
- **One-time backfill 2017→now (~8 yr ≈ 1.9 M docs):** ≈ **6–8 GB** (≈ **3.4 GB** text-dropped).
- **Compute:** embedding 1.9 M docs on CPU (MiniLM, batched ~200/s) ≈ **2–3 hours, once**; incremental
  monthly embed is seconds.

So: a few GB for a full historical corpus, tens of MB/month thereafter — **linear, bounded, and a knob
(`backfill_start_year`, caps, `drop_text_after_embed`) caps it hard.** Not exponential. The thing that
*could* explode — patents — is deliberately kept out of the bulk path (§6).

**Limit of the ingest** = `category_whitelist × max_docs_per_category_per_month × months`, with an
optional `max_db_gb` guard that halts ingest and warns. There is no configuration in which it runs away.

---

## 4. Subscriptions? — None.

| source | access | cost |
|---|---|---|
| arXiv | public API | free |
| bioRxiv / medRxiv (Europe PMC) | public API | free |
| Hacker News (Algolia) | public API | free |
| NIH RePORTER / NSF / SBIR | public APIs | free |
| USPTO PatentsView | public API, **free `X-Api-Key`** (registration, not payment) | free |
| embeddings | local sentence-transformers | free (CPU) |

**Zero paid subscriptions for discovery.** (The only paid question in the project is *survivorship-free
prices* for the panel — a separate, later, panel-side decision, unrelated to discovery.)

---

## 5. Where the awards/grants history is used (and the unlisted-constituent payoff)

Grants are not a side-source here — they're a **leading nascency signal and the route to unlisted
names**, used in three places:

1. **Discovery corpus (bulk):** untargeted NIH/NSF/SBIR award abstracts cluster alongside papers. A
   cluster rising in **both grants and preprints** while mainstream (`p_main`) stays low is the
   strongest nascent signal — money commits *before* the literature peaks and well before the news.
2. **Nascency ranking:** each cluster gets a **funding slope** (award count/$ over time) as a second
   `β`-style instrument next to the paper slope — the diffusion gate becomes multi-instrument.
3. **Constituent discovery, including PRIVATE/UNLISTED firms (your silicon_photonics point):** SBIR
   and NSF awards name the **awardee organization** — and SBIR awardees are overwhelmingly *small
   private companies*. So a discovered cluster's awardee list surfaces emerging firms **before they
   IPO** — exactly the "bring forward a future ticker candidate although it is not listed" goal that
   EDGAR (listed filers only) structurally cannot serve. Awardee → (later) ticker is the watch-list.

---

## 6. How patents are ingested (the bounded way)

USPTO is ~350k patents/year — bulk-ingesting it untargeted is the one thing that *would* bloat the DB.
So patents are **targeted, on demand, after clustering** — never bulk:

- **No bulk dump.** (PatentsView publishes multi-GB TSV bulk files; rejected — wrong for SQLite.)
- **Per-cluster query.** Once a cluster is discovered, query the **PatentsView API** for patents whose
  title/abstract match the cluster's top terms (or its CPC neighborhood), in date windows, **capped**
  (`max_patents_per_cluster`). Returns title/abstract/date + **assignee** + CPC.
- **Two outputs, both bounded:** (a) an **IP-momentum** slope per cluster (patent count over time —
  another confirmation instrument); (b) **assignee → constituent** linkage (the companies patenting in
  the theme — again catches private firms, complementing SBIR/NSF awardees and EDGAR filers).
- Needs the free `PATENTSVIEW_API_KEY` (already wired in `ingest/patentsview.py`); skipped gracefully
  if absent.

Clinical trials and EDGAR FTS are handled the same way — **targeted per discovered cluster**, not bulk.

---

## 7. Clustering + nascency ranking (zero-Claude)

- **Cluster a recent window, not the whole corpus.** Discovery is about what's emergent *now*, so
  HDBSCAN runs on the last `cluster_window_months` (e.g. 18–24 mo) of embeddings — a few hundred k
  vectors, not millions (bounds clustering cost). Optional UMAP dim-reduction first.
- **Each cluster → a centroid + top-TF-IDF terms + exemplar docs** (its zero-Claude identity).
- **Historical series by projection:** for each centroid, compute monthly `N_spec` over the *full*
  corpus by cosine-membership (`tau_member`) — the exact mechanism `diffusion.py` already uses for
  hand-themes. Then `β_spec` (paper slope), the funding slope (§5), and `p_main` (vs GDELT/Wikipedia
  mainstream) → the **nascency gate** ranks clusters: specialist+funding slopes up, mainstream low.
- **Promote** the top-ranked nascent clusters to candidate `themes` rows (descriptor = top terms /
  centroid). **Optional one Claude call per survivor** to assign a human label — the only Claude touch,
  cost-gated, and skippable (clusters work unlabeled).

---

## 8. Schema (additive migration v8, proposed)

- `documents`: add `corpus TEXT` (`'broad'` vs `'theme'`) + index; reuse the existing
  title/abstract/embedding/published_month columns as-is.
- `ingest_watermarks (source_id, category, last_window, updated_at)` — incremental driver state.
- `discovery_clusters (cluster_id, run_id, centroid BLOB, size, top_terms, beta_spec, funding_slope,
   p_main, nascency_rank, created_at)` — promoted cluster snapshots per discovery run.
- Constituents (awardee/assignee/filer) reuse `theme_tickers` plus a new `theme_orgs` for *unlisted*
  names (org name, source, first_seen — no ticker yet).

No change to the panel/kill-switch tables; discovery simply feeds better `themes` into the same
downstream pipeline.

---

## 9. Open parameters (⚙ — set at build, fit later)

- **Corpus scope** (D21, *user-deferred*): (a) arXiv+bioRxiv by category; (b) + HN/patents/grants;
  (c) existing-corpus-only. Drives the §3 numbers.
- `category_whitelist`, `max_docs_per_category_per_month`, `backfill_start_year`, `drop_text_after_embed`,
  `max_db_gb`.
- `cluster_window_months`, HDBSCAN `min_cluster_size` / `min_samples`, `tau_member`, UMAP on/off.
- nascency gate `beta_min` / `p_max` / funding-slope weight, `top_k_themes` to promote.
- `max_patents_per_cluster`, `PATENTSVIEW_API_KEY`.

All unfit until validated — but discovery's output (the theme list) is itself testable against the
panel/kill-switch: better themes should improve the read.
