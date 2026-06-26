# Wave 3 — what it actually does (as built)

**Status:** built 2026-06-26 (decision D10). Zero Claude. Builds on Waves 1–2; read those explainers
first. Parameters remain informational defaults (⚙).

---

## 1. In one sentence

Wave 3 adds two more sources — **Hacker News** (builder mindshare) and **EDGAR full-text search**
(SEC filing-keyword emergence) — and, from EDGAR, extracts the **tickers** that mention each theme,
the first concrete bridge from "themes" toward "stocks."

---

## 2. The two sources are handled differently (on purpose)

| Source | Has embeddable text? | Role | How it's stored |
|---|---|---|---|
| **Hacker News** (Algolia API) | yes (title + story text) | builder-mindshare **specialist document** | joins the corpus (`source_id='hackernews'`), goes through the **same embedding membership** path as arXiv/Europe PMC → feeds **N_spec** |
| **EDGAR full-text search** | no (returns matched-filing metadata + company names) | corporate **filing-keyword emergence** + ticker linkage | a **separate yearly count series** (`theme_edgar`, NOT N_spec) + **`theme_tickers`** |

Why the split: membership-by-embedding only works on real text. HN has it; EDGAR FTS does not (it
keyword-matches server-side and returns company names, not abstracts). Forcing EDGAR through the
embedding path would silently drop it. So EDGAR is modelled honestly as a keyword **count** plus a
**ticker extractor** — see D10.

---

## 3. Hacker News (`src/hype_parser/ingest/hackernews.py`)

- Queries the HN Algolia search API (`tags=story`) in **per-year `created_at_i` windows** (Algolia
  caps paging at 1000/window), `hn_max_per_year=200`.
- Each story → `{doc_id:"hn:<objectID>", source_id:"hackernews", title, abstract:story_text, url,
  published_at:created_at}`.
- These documents are embedded and kept if their cosine to the theme centroid ≥ `tau_member`, so HN
  contributes to **N_spec** alongside arXiv (great for software/infra themes — RAG, Mamba).

## 4. EDGAR full-text search (`src/hype_parser/ingest/edgar_fts.py`)

- One request **per year** to `efts.sec.gov/LATEST/search-index` (SEC User-Agent, ≤10 req/s, with
  retry+backoff): reads `hits.total.value` → that year's count of filings mentioning the theme →
  stored in the **`theme_edgar`** table (its own sparkline in the report).
- Per-year (not per-month) on purpose: the FTS endpoint returns frequent intermittent **HTTP 500s**
  under load; ~9 requests/theme with retry is robust where ~100/theme was slow and fragile (D10).
- From each request's sample hits, parses tickers out of `display_names`
  ("Company (TICK) (CIK …)") → aggregated into **`theme_tickers`** (ticker, n_mentions).
- EDGAR full-text coverage starts ~2001; we query from `arxiv_start_year` (2018).

---

## 5. What changed vs Wave 2

- **Schema:** v4 added `theme_tickers`; v5 added `theme_edgar` (yearly counts). No other engine
  changes (the v4 `theme_series.edgar_filings` column is unused after the per-year switch).
- **Theme fields:** `+ hn_query` (Wave-3 HN → N_spec), `+ edgar_query` (Wave-3 EDGAR series +
  tickers). A theme may set any subset of all source queries.
- **Report:** an EDGAR-filings sparkline and a **"Tickers mentioning in SEC filings (EDGAR)"** line
  per theme card.
- **N_spec definition is unchanged** — it is still specialist *documents* (now arXiv + Europe PMC +
  ClinicalTrials + Hacker News); EDGAR is a separate, parallel signal.

---

## 6. Run it

```
run_5_Hype_parser.bat --refresh      # re-ingest all themes (now incl. HN + EDGAR)
run_5_Hype_render.bat                 # render-only from cache (no network)
```

---

## 7. Observed (live, 2026-06-26; placeholder hashing embedder)

**Hacker News feeds N_spec** for the tech themes:

| Theme | N_spec by source |
|---|---|
| rag | arxiv 203, **hackernews 143** |
| ssm_mamba | arxiv 170, **hackernews 16** |

**EDGAR yearly filings + ticker linkage** (the bridge to stocks):

| Theme | EDGAR filings | Top tickers mentioning it in SEC filings |
|---|---|---|
| crispr_gene_editing | 50,386 / 9yr | **CRSP, NTLA, CRBU, VRTX, EDIT, NKTX** (the gene-editing cohort) |
| mkras_vaccine | 54 / 7yr | **ELTX** (Elicio — the anchor case), MRNA, GRTS |
| rag | 239 / 4yr | ESTC, PRGS, NTAP, INOD, RSSS |
| ssm_mamba | 2 / 1yr | (none — "state space model" barely appears in filings) |

The ticker linkage landed exactly on target: CRISPR surfaced its real gene-editing cohort, and
mKRAS surfaced **ELTX (Elicio)** — the program's own anchor case. GDELT (`N_main`) remains
rate-limited (429); the non-destructive cache preserves prior values.

---

## 8. What Wave 3 still does NOT do (later)

Same boundary — still no Claude, no theme **discovery**, no survival filter, no Module A/B, no fitted
parameters. **But** `theme_tickers` is the first step toward **constituent expansion** (Stage 3):
turning a hot theme into the bounded universe of stocks to screen. Wiring those tickers into a
mispricing/hype screen is a later phase. Next per D6: Waves 4–5 (IP + public capital; bridge layer),
then the panel + kill-switch (Protocol §2–4) before any parameters are trusted.
