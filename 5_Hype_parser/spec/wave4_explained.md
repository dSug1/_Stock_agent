# Wave 4 — what it actually does (as built)

**Status:** built 2026-06-26 (decision D11). Zero Claude. Builds on Waves 1–3. Parameters remain
informational defaults (⚙).

---

## 1. In one sentence

Wave 4 adds four more **specialist document sources** — research/biomed funding (**NIH RePORTER**,
**NSF**), small-business R&D (**SBIR**), and patents (**USPTO PatentsView**) — all feeding `N_spec`
the same way arXiv/Europe PMC/Hacker News do. It widens "who is working on this theme" to include
**public capital and IP**.

No schema change, no new engine logic: these are documents (grant/patent title + abstract) that go
through the existing embedding-membership path.

---

## 2. The four sources

| Source | Key? | Signal | source_id |
|---|---|---|---|
| **NIH RePORTER** (`ingest/nih_reporter.py`) | no (POST API) | biomedical grant funding | `nih` |
| **NSF Awards** (`ingest/nsf.py`) | no (GET API) | science/tech research funding | `nsf` |
| **SBIR.gov** (`ingest/sbir.py`) | no (GET API) | small-business R&D awards (cross-sector) | `sbir` |
| **USPTO PatentsView** (`ingest/patentsview.py`) | **yes** (free `X-Api-Key`) | IP / patents | `patentsview` |

All take an injectable HTTP callable (tests run offline) and fail open. Each fetches in **per-year
windows** (`grants_max_per_year=100`) so the specialist series has monthly history.

**PatentsView needs a key.** The current PatentsView API requires a free `X-Api-Key`. Without one
the source is **skipped gracefully**. To enable it, put `PATENTSVIEW_API_KEY=...` in the repo-root
`.env` (the orchestrator reads env first, then `.env`).

---

## 3. How they integrate

Identical to every prior document source: fetched docs are upserted into the `documents` corpus with
their `source_id`, embedded locally, and kept as members if their cosine to the theme centroid ≥
`tau_member`. So `N_spec` for a theme is now the union of arXiv + Europe PMC + ClinicalTrials +
Hacker News + **NIH + NSF + SBIR + (PatentsView)** members. The report's "member docs by source"
line shows the mix per theme.

Theme fields (`config/themes_seed.yaml`): `nih_query`, `nsf_query`, `sbir_query`, `patents_query` —
any subset. Seeds: NIH on biomedical themes (crispr, mKRAS), NSF on tech themes (rag, ssm), SBIR +
patents on all.

**Funding amounts** are captured in the document text, but the engine only uses doc **counts** for
N_spec. A dollar-weighted "funding into theme" signal (for B2 ThematicHeat) is a later module.

---

## 4. Run it

```
run_5_Hype_parser.bat --refresh      # re-ingest all themes (now incl. NIH/NSF/SBIR/PatentsView)
run_5_Hype_render.bat                 # render-only from cache (no network)
```

---

## 5. Observed (live, 2026-06-26; placeholder hashing embedder)

N_spec by source after the Wave-4 refresh:

| Theme | N_spec by source |
|---|---|
| crispr_gene_editing | europepmc 1017, **nih 293**, arxiv 10, clinicaltrials 8 |
| mkras_vaccine | europepmc 86, clinicaltrials 14, **nih 5** |
| ssm_mamba | arxiv 170, hackernews 17, **nsf 2** |
| rag | arxiv 203, hackernews 143 |

- **NIH RePORTER is the standout Wave-4 contributor** — +293 members for CRISPR (NIH richly funds
  gene editing), +5 for mKRAS. This is real public-funding specialist signal.
- **NSF** added a couple of ssm members; for rag, NSF/SBIR returned candidates that were **correctly
  filtered out** by membership (NSF keyword search is loose — unrelated awards — so they failed the
  cosine threshold). The membership filter trimming loose matches is the engine working as intended.
- **SBIR** rate-limited (429, like GDELT) and contributed no members this run; fail-open.
  **PatentsView** skipped (no `PATENTSVIEW_API_KEY`).
- All 68 tests pass; report regenerated with the updated per-source breakdown.

---

## 6. What Wave 4 does NOT do (and what's next)

Same boundary — still no Claude, no theme discovery, no survival filter, no Module A/B, no fitted
parameters. **All five diffusion-engine source waves are now built** (arXiv; Europe PMC +
ClinicalTrials; Hacker News + EDGAR; NIH/NSF/SBIR/PatentsView). D6 Wave 5 (the bridge layer:
ETF/13F/transcripts/Product Hunt/regulations.gov) is optional polish.

The higher-leverage next step is the **labeled point-in-time panel + the kill-switch test**
(Protocol §2–4): only that tells us whether the whole premise (legible-hot-theme cheap names
outperform illegible-cheap names) holds — and nothing downstream (Modules A/B, parameter fitting) is
justified until it does. `theme_tickers` (Wave 3) is the seed for the constituent-expansion side.
