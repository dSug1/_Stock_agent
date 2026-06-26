# Wave 2 — what it actually does (as built)

**Status:** built 2026-06-26 (decision D9). Zero Claude. Builds directly on Wave 1; read
`wave1_explained.md` first. All model parameters remain informational defaults (⚙), unfit until the
labeled panel.

---

## 1. In one sentence

Wave 2 adds a **biomedical specialist numerator** to the diffusion engine — **Europe PMC**
(bioRxiv/medRxiv + PubMed literature) and **ClinicalTrials.gov** (trial registrations) — so biotech
sub-themes get a real `N_spec` curve instead of the empty one arXiv produced in Wave 1.

That's the entire change. Everything downstream of ingestion — embeddings, centroid membership,
the monthly series, `beta_spec` / `p_main` / `diffusion_ratio`, the HTML — is **unchanged from
Wave 1**; it just now has biomedical documents to work on.

---

## 2. What changed vs Wave 1

| Area | Wave 1 | Wave 2 adds |
|---|---|---|
| Specialist sources (numerator) | arXiv only | **+ Europe PMC, + ClinicalTrials.gov** |
| Theme fields | `arxiv_query` | **+ `europepmc_query`, + `ctgov_query`** (any subset) |
| Corpus / schema | `documents` table | **unchanged** — new docs just carry `source_id ∈ {europepmc, clinicaltrials}` |
| Membership / series / math | as built | **unchanged** |
| Report | per-theme metrics | **+ "member docs by source"** line (e.g. `arxiv 12, europepmc 30, ctgov 8`) |
| Seed themes | rag, ssm_mamba, mkras_vaccine | **+ crispr_gene_editing**; mkras_vaccine gains biomed queries |

No database migration was needed — the Wave-1 corpus model is source-agnostic, so adding sources is
purely an ingestion concern.

---

## 3. The two new sources

### Europe PMC (`src/hype_parser/ingest/europepmc.py`)
- The **searchable** route to bioRxiv/medRxiv preprints **plus** PubMed. (D6 named
  "bioRxiv/medRxiv", but the raw bioRxiv API is date-dump-only with no keyword search; Europe PMC
  indexes those preprints and supports keyword queries with abstracts + dates — see D9.)
- `resultType=core` returns abstracts; paged via `cursorMark`; fetched in **per-year `FIRST_PDATE`
  windows** (2018→now, `europepmc_max_per_year=150`) so the specialist series has monthly history
  (same pattern as arXiv).
- Each result → a document `{doc_id: "epmc:<source>:<id>", source_id: "europepmc", title, abstract,
  url, published_at}`.

### ClinicalTrials.gov v2 (`src/hype_parser/ingest/clinicaltrials.py`)
- Free-text study search (`query.term`), paged via `nextPageToken`, up to `ctgov_max_results=300`.
- Each study → `{doc_id: "ctgov:<NCT>", source_id: "clinicaltrials", title: briefTitle,
  abstract: briefSummary, url, published_at: studyFirstPostDate}`.
- A registration is **specialist activity**; its first-posted date is the timestamp, so trials
  contribute to `N_spec` by registration month. (This is also the raw material for B5's discrete
  NarrativeRealization later.)

Both clients take an injectable `http_get` (tests run offline) and fail open (a failed fetch
returns nothing and never crashes the run).

---

## 4. How a theme uses the sources

In `config/themes_seed.yaml` a theme may set any subset of `arxiv_query`, `europepmc_query`,
`ctgov_query`. During a fetch run (`scripts/5_radar.py`), every configured source is queried and all
returned documents are upserted into the one `documents` corpus and added to the theme's candidate
set. Then the **single, unchanged** membership pass runs: embed each candidate, keep those whose
cosine to the theme centroid ≥ `tau_member`, and count members per month → `N_spec`. So a theme's
specialist signal is the **union across its sources**, deduplicated by `doc_id`.

Example (post-Wave-2 seeds):
- `rag`, `ssm_mamba` → arXiv only (tech themes).
- `mkras_vaccine` → arXiv (≈0) + Europe PMC + ClinicalTrials → now a real biomedical `N_spec`.
- `crispr_gene_editing` → arXiv (narrow) + Europe PMC + ClinicalTrials (the CRSP/NTLA/BEAM cohort).

---

## 5. Reading the report

Identical to Wave 1, plus each card's header now lists **member docs by source**, so you can see
which sources carry a theme (e.g. a biotech theme dominated by `europepmc` + `clinicaltrials`, a
tech theme by `arxiv`). The metrics (`beta_spec`, `p_main`, `diffusion_ratio`) and their meaning are
unchanged.

---

## 6. Run it

```
run_5_Hype_parser.bat --refresh      # re-ingest all themes (now incl. the biomedical sources)
run_5_Hype_render.bat                 # render-only from cache (no network)
```
Biotech themes need a `--refresh` the first time after Wave 2 (their Wave-1 series is still within
the 7-day SWR window, so a plain run won't re-fetch).

---

## 7. Observed (live, first Wave-2 run 2026-06-26; placeholder hashing embedder)

| Theme | N_spec total | beta_spec (L12) | member docs by source |
|---|---|---|---|
| `mkras_vaccine` | 100 | +0.039 | europepmc 86, clinicaltrials 14 (**arXiv was 0 in Wave 1**) |
| `crispr_gene_editing` | 1035 | +0.050 | europepmc 1017, arxiv 10, clinicaltrials 8 |
| `rag` | 73 | +0.106 | arxiv 73 |
| `ssm_mamba` | 58 | +0.081 | arxiv 58 |

The biotech anchor (mKRAS) went from an empty card to a real specialist curve sourced from Europe
PMC + ClinicalTrials. All four themes show positive `beta_spec` (specialist attention accelerating).
GDELT (`N_main`) remains rate-limited (429) under heavy use; the non-destructive cache + a later
`--refresh` populate it. As in Wave 1, `N_spec`/`beta_spec` (the numerator) do not depend on GDELT.

---

## 8. What Wave 2 still does NOT do (later)

Same boundary as Wave 1 — Wave 2 only widens the specialist source set. Still no Claude, no theme
**discovery** (themes are hand-seeded; HDBSCAN + one-call labelling is later), no survival filter,
no constituent/stock mapping, no Module A/B, no fitted parameters. Next per the D6 order: Wave 3
(Hacker News + EDGAR full-text — builder mindshare + filing keyword emergence with ticker linkage).
