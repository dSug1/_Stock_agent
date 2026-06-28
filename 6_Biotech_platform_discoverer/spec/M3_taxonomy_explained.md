# M3 explained — Stage 1: mechanism tagging (deterministic, reversible)

*What milestone 3 does. Companion to `SPEC_acrivon_pattern_screener.md` §5.3 and §4.*

---

## In one sentence

M3 tags each company in the universe against the **37-mechanism controlled vocabulary** (oncology /
autoimmune / GPCR) using its name + business description; a company that matches none is **flagged and
parked for review — never deleted**, and the exclusion is fully reversible once more evidence arrives.

## Where the text comes from

Stage 1 tags over `name + business_description + sector + industry`. The **business description** is
the key signal, and it's captured for free during Stage 0b's yfinance enrichment (`longBusinessSummary`)
— so schema v2 added `business_description` / `sector` / `industry` columns, filled in the same pass
that fetches market cap. (Once Stage 2 harvests OpenAlex/MeSH concepts in M4, those get appended to the
tagged text, sharpening recall — the tagger takes any text.)

## The tagger (`taxonomy.py`)

`TaxonomyTagger` compiles one word-boundary, case-insensitive regex per mechanism over its
`synonyms` + `example_targets` from `config/taxonomy.yaml`, longest-phrase-first. `tag(text)` returns
the matched mechanism ids with their branch, maturity, and which terms hit (for the audit log).

Matching is **recall-leaning by design**: over-tagging is safe (a wrongly-tagged company is just
scored and rejected later), while *under*-tagging routes a company to review. Terms shorter than 3
chars (the `C3`/`C5` gene symbols) are dropped to avoid spurious hits.

## Stage 1 (`stage1.py`) — tag, or park reversibly

For each company:
- **matches ≥1 mechanism** → write `ta_tags`, ensure `stage1_excluded=false`, audit `ta_tagged`.
- **no match** → add to `review_queue` with reason `no_ta_tag`, and (if `require_ta_tag`) set
  `stage1_excluded=true` with a logged reason.

`stage1_excluded` does **not** remove the company — the row stays; it's only left out of the *default*
Stage-2 harvest set. `stage1_filters.include_excluded: true` re-admits the whole excluded set without
re-harvesting anything (the spec's reversibility guarantee). And tagging is **idempotent + self-
correcting**: re-running after evidence lands (M4) re-tags, and a company that now matches is
automatically un-excluded (counted as `readmitted`).

**The cardinal rule holds end-to-end:** Stage 1 never calls `delete_company`. The only deletions in
the whole pipeline remain Stage 0b's `mktcap_out_of_band` / `not_live`. A test asserts zero deletions
occur during Stage 1.

Dev-stage tagging (platform / preclinical / phase 1–3) needs ClinicalTrials.gov + filing text, which
arrive with Stage-2 evidence — **deferred to M4**; M3 does the mechanism TA filter.

## How it runs

```bat
:: after the universe is enumerated + enriched (Stage 0):
scripts\6_screen.py --stage 1
```
The report's funnel then shows `TA-tagged` vs `no TA tag` counts, and each retained company lists its
mechanism tags (and a `stage1_excluded` flag where set).

## What's proven (tests)

`tests/test_stage1.py` — 11 tests: the tagger matches known mechanisms (TYK2→`tyk2_jak`,
PARP→`adp_ribosylation_parp_tankyrase`, menin-KMT2A, TEAD/Hippo) and stays silent on generic text;
Stage 1 tags matchers, parks non-matchers in `review_queue` + `stage1_excluded` **without deleting**
(asserted: zero `deleted` audit rows, company still present), respects `require_ta_tag`, and
**re-admits** a company on a later pass once its description reveals a mechanism.

## First live run (2026-06-28)

Over the real cap-filtered universe (1216 enumerated → **589 kept** after the Stage-0b band), Stage 1
tagged on yfinance business descriptions alone:

| Pass | TA-tagged | `no_ta_tag` |
|---|---|---|
| 1 — description only, original vocab | 85 | 504 |
| 2 — **+ harvested evidence + enriched vocab** | **122** (+44%) | 467 |

Pass 2 ran after Stage 2 harvested evidence for 441/589 companies (mostly trials) and the taxonomy
synonyms were enriched with spelled-out forms (so "Histone deacetylase" matches `hdac`, etc.).
**39 companies were `readmitted`** — previously parked, now tagged once evidence/vocab revealed an
in-scope mechanism (the reversibility guarantee paying off). Top mechanisms: `b_cell_depletion`
(33), `immune_exclusion_caf_collagen` (26), `tnf_il_axis` (24), `gpcr_peptide_ligand` (9),
`ubiquitination_degradation` (8).

## Caveat at this milestone

That ~14% tag rate (85/589) is **expected and recall-safe**, not a loss: yfinance descriptions are
generic ("a clinical-stage biopharmaceutical company developing therapies for…") and rarely name a
specific mechanism, so most companies land in `no_ta_tag` — **kept, parked for review, reversible**.
The rate climbs sharply once M4 evidence (OpenAlex concepts, trial conditions, patent titles) feeds
the tagger: re-running Stage 1 after a harvest re-tags and auto-readmits (`readmitted`) every company
whose evidence now reveals an in-scope mechanism. Tagging quality is bounded by input text, and right
now the only text is the description — by design this sharpens as the funnel fills.
