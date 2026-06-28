# M4 explained — Stage 2: evidence harvesting

*What milestone 4 does. Companion to `SPEC_acrivon_pattern_screener.md` §5.4, §6, §7.*

---

## In one sentence

M4 harvests, for every Stage-1-retained company, a compact **evidence bundle** from the free
science / clinical / IP sources — publications (OpenAlex), trials (ClinicalTrials.gov), patents
(PatentsView) — and persists it (one row per company+source, with a cursor) so later stages reason
over *evidence*, not company names.

## The sources (all free; the spec's "strongest signals" first)

| Source (`clients/…`) | Signal | Key? |
|---|---|---|
| **OpenAlex** `openalex.py` | the company as an institution-of-type-company: works count, citation impact (h-index, mean citedness), top research concepts — the primary science signal, global + English | none (polite pool via `mailto`) |
| **ClinicalTrials.gov v2** `clinicaltrials.py` | trials by lead sponsor: count, phases, top conditions, and **biomarker / companion-diagnostic language** (the `E_translation` tell) | none |
| **PatentsView** `patentsview.py` | patents by assignee, bucketed **method/platform vs composition** (a platform's tell is method/assay/screen patents, not a single molecule) | needs `PATENTSVIEW_API_KEY` — **inert without it** |

All route through `clients/_net.py` (64 MiB caps, repo `USER_AGENT`, rate-limited, fail-open). Each
client has a **pure parser** (unit-tested on sample payloads) + a fail-open `fetch` returning
`(summary, cursor)`. EDGAR full-text (10-K "Business"/S-1) and IR-poster scraping are spec sources
**deferred** to a follow-up — they need filing-text fetch + per-company IR-URL discovery.

## The orchestrator (`stage2.py`)

For each company that is **not `stage1_excluded`** (unless `stage1_filters.include_excluded` — the
reversibility switch), harvest each enabled source and upsert an `evidence` row
`(company_id, source, cursor, payload_hash, payload_json, fetched_at)`.

- **Incremental**: a company+source harvested within `incremental_ttl_days` (default 7) is skipped —
  polite and cheap (the spec's "skip companies whose cursors are unchanged"). The cursor is the
  source's latest-date marker (latest publication / trial update / patent grant).
- **Fail-open per source**: one source erroring for one company drops only that bundle; the rest
  proceed (asserted in tests).
- **Reversibility**: raw summaries are stored once; re-runs reuse them, and a `stage1_excluded`
  company is simply not harvested by default — re-admitting it (`include_excluded`) harvests it
  without disturbing anything already stored.

## How it runs

```bat
scripts\6_screen.py --stage 2                 :: harvest all retained companies (incremental)
scripts\6_screen.py --stage 2 --limit 50      :: bounded first run
```
`--limit` caps companies processed — useful because a full harvest is one call per company per
source (≈ retained-count × 2–3 network calls). Set `PATENTSVIEW_API_KEY` in `.env` to add patents.

## Validated live (2026-06-28)

- **ClinicalTrials.gov**: Acrivon → 3 trials (Phase 1–2, companion-Dx language, latest 2026-06-15);
  IDEAYA → 11 trials (Phase 1–3, biomarker language). Solid.
- **OpenAlex**: IDEAYA → 92 works, h-index 37. Confirmed the `topics`-vs-deprecated-`x_concepts`
  shape and fixed the concept fallback.
- **Full harvest against the live store** (`--stage 2 --include-excluded`, all 589): **447 evidence
  rows** — 426 ClinicalTrials, 21 OpenAlex; **441/589 companies now carry evidence**. (Patents inert
  without a key; OpenAlex sparse because most small caps lack an institution record — they still get
  trials.) Real bundles, not boilerplate — e.g. CLYM (6 trials, anti-CD19, ITP/lupus), GLUE (L-MYC/
  N-MYC-amplified tumors), XBiotech (OpenAlex, 36 works). Feeding this back into evidence-aware Stage
  1 lifted TA-tagged coverage **85 → 122 (+44%)**, readmitting 39 previously-parked companies.

## Honest limitations

- **OpenAlex is institution-level**: a company is only found if it has an OpenAlex *institution*
  record (enough indexed output). Smaller companies (e.g. Acrivon at this size) may return no
  OpenAlex signal — they still get ClinicalTrials + patents. A future refinement is an
  affiliation-string works query for sub-institution coverage.
- **Patents are key-gated** (PatentsView now requires a free key) — inert until one is set.
- **No EDGAR full-text / IR-poster scraping yet** — deferred (the IR posters, e.g. KaiSR, are the
  highest-signal but the least API-accessible; they come with the embedding/retrieval work).

## What's proven (tests)

`tests/test_stage2.py` — 12 tests (**75 total**): name cleaning; the three parsers on sample payloads
(incl. the OpenAlex name-guard rejecting a same-named university, the CTgov biomarker-language scan,
the PatentsView method-vs-composition bucketing, patents inert without a key); and Stage 2
orchestration — harvests retained companies, **skips `stage1_excluded`**, **skips fresh** within TTL
(no re-fetch), and **fails open** when a client raises.
