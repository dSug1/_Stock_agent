# M8 — Ownership-crossing signal (EDGAR full-text), explained

*House-style milestone note. Companion to `decisions.md` D7 and overall spec §3.5. Closes the gap M7
surfaced: the specialist-fund 5%+ crossings the submissions API structurally can't return.*

---

## What it does
Captures **specialist-healthcare-fund 5%+ ownership crossings** — the §3.5 headline capital-markets
signal — as `ownership_crossing` signals. CLI: `scripts/8_signals.py --ownership`. Zero-LLM, free.

## Why a different approach than M7
M7 reads a company's *own* filings (submissions API), but a Schedule 13D/G is filed under the
**investor's** CIK, so a company's feed never contains a 13D/G *about* it — M7 returned zero across 880
entities. EDGAR **full-text search** (`efts.sec.gov`) indexes each filing with **all** its associated
CIKs (`ciks` + `display_names`), so from one search we recover both the filer (fund) and the subject
(company). That enables a **fund-first** sweep: ~19 fund queries instead of 880 per-company ones.

## How it works
`clients/edgar_fts.py` searches efts per fund for 13D/G in the lookback window and paginates
(`from`-based, 100/page). `signals/ownership.py`:
1. builds a `{universe CIK → entity_id}` map (active universe only);
2. for each fund, for each filing hit — requires the fund to appear in `display_names` (precision:
   not a stray body mention), then matches every CIK in the hit against the universe map. A fund isn't
   a biotech, so it never self-matches; whichever CIK is in our universe is the subject;
3. writes an `ownership_crossing` signal (fund attributed, form, accession, subject CIK). Idempotent
   (signal_id = hash(entity, accession, fund)); per-fund commit; in-run dedup for efts pagination
   overlap.

## Two findings that made the difference (both empirical, 2026-07-10)
1. **SEC relabeled the forms.** The first live run returned **zero** — because the old `SC 13D`/`SC 13G`
   root-form labels match only pre-~2025 filings; SEC's 2024–25 EDGAR modernization renamed them
   `SCHEDULE 13D`/`SCHEDULE 13G`. `forms=SC 13D` → 0 hits in 2026; `forms=SCHEDULE 13D` → 174. We query
   **both** old+new (`OWNERSHIP_FORMS`) so the lookback spans the transition. *This is the kind of thing
   only a real run surfaces — a unit test with a fixture would have passed while production returned
   nothing.*
2. **efts 500s under load.** Intermittent HTTP 500s dropped whole funds (Baker Bros, Deep Track) on the
   first pass. `edgar_fts._get_json` now retries 429/5xx with backoff — a dropped request is a dropped
   fund's entire crossing set, so fail-open-silent was too lossy here.

## Live result
19 funds → **278 ownership crossings** on our universe in the trailing 180 days: RA Capital 69,
Perceptive 36, Deep Track 33, OrbiMed 26, Venrock 22, Baker Bros 18, Redmile 18, Cormorant 16,
EcoR1 14, Sofinnova 7, Bain LS 7, Avoro 5, Foresite 4, Forbion 2, Andera 1.

## How to run / verify
```
cd 8_Early_stage_biotechs
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_ownership.py -q   # 5 offline tests
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --ownership --verbose
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --stats
```

## Deferred (honest scope)
- Classify **13D (active/control intent) vs 13G (passive)** and **new position vs amendment (/A)** —
  a new 13D on a small-cap is a stronger signal than a routine 13G/A; the raw `form` is stored, the
  classification isn't done yet.
- Fund watchlist is **US-specialist-focused** — add EU/JP/KR specialists as those markets are natively
  enumerated (later phases). Watchlist is config-editable (`config.specialist_funds`).
- Cross-link ownership + insider (M7) clusters per entity into a single capital-markets score (scoring
  layer, §5.4).
