# M7 — Capital-markets signal, explained (Phase 2 begins)

*House-style milestone note. Companion to `decisions.md` D6 and the overall spec §3.5. This is the
first signal ingester — it turns the `signal` table (created empty in schema v1) into live data.*

---

## What it does
For every **active-universe** entity with a CIK, reads recent SEC filings and writes one `signal` row
per **material filing** in a lookback window (default 180 days). Zero-LLM, free, no spend. CLI:
`scripts/8_signals.py --capital-markets`.

Active universe here = `is_live=1 AND below_floor=0 AND cik IS NOT NULL`. Unknown-cap names are
**included** (a fresh insider buy or shelf on an unpriced micro-cap is exactly the early signal we
want); known-below-floor names are skipped.

## Why capital-markets first
Spec §3.5 calls EDGAR "your highest-value, most reliable structured source," and it's fully free and
zero-LLM — so it carries no prompt-injection surface and no cost gate. It's also the cheapest way to
prove the signal-ingestion pattern (fetch → `signal` rows, idempotent, resumable) before the more
expensive literature + Claude-extraction work.

## How it works
`clients/edgar_signals.py` calls the submissions API once per CIK
(`data.sec.gov/submissions/CIK##########.json`), whose `filings.recent` block holds parallel arrays
(form / date / accession / doc). `parse_recent_filings` zips them; `recent_material_filings` filters to
the configured `material_forms` within the lookback. `signals/capital_markets.py` fans the fetches
across a bounded thread pool (network-bound) but **persists on the main thread** (SQLite is
single-threaded), committing per entity so a crash keeps prior work. Each signal's id is a stable hash
of (entity, accession, form), so re-runs upsert rather than duplicate.

Full run: **880 active entities → 17,645 signals in 1m51s** — Form-4 12,544, 8-K 4,413, 424B5 311,
424B3 185, S-3 151, S-1 41.

## The important caveat: no 13D/G from this endpoint (structural)
The full run returned **zero SC 13D/13G** — and that is *not* "no ownership crossings happened." A
Schedule 13D/G is filed under the **investor's** CIK (the fund crossing 5%), not the subject company's,
so the subject's submissions feed never contains a 13D/G *about* it. Form-4 (insider) **is** indexed
under the issuer CIK, which is why insider signals come through fine.

**Consequence:** M7 reliably captures **insider activity (Form-4), material events (8-K), and capital
raises (S-1/S-3/424B)** — genuinely useful — but the §3.5 *headline* signal, specialist-healthcare-fund
5%+ ownership crossings, is **not** captured here. That requires the EDGAR **full-text search** API
(`efts.sec.gov`) queried by subject company + a named-fund watchlist (Baker Bros, RA Capital, OrbiMed,
Perceptive, …). This is now the **top Phase-2 refinement**, not optional. SC 13D/G are left in
`material_forms` (harmless — they'll match once the efts path is added, and keep the config honest about
intent).

Secondary caveat: Form-4 volume is high (~14 per entity per 180 days), so the scoring layer will need to
**cluster** insider signals (a burst of buys is the signal, not each individual filing).

## How to run / verify
```
cd 8_Early_stage_biotechs
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_signals.py -q   # 6 offline tests
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --capital-markets --limit 30 --verbose
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --capital-markets            # full pass
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --stats
```

## Deferred (honest scope)
- **EDGAR full-text (efts) for 13D/G + fund-watchlist attribution** — the real §3.5 signal. Next.
- Form-4 clustering / aggregation before scoring.
- Literature (§3.1), patents (§3.3), trials (§3.2), regulatory designations (§3.4) — later signals.
- Claude extraction (§5.1 founder-lineage) + independence scoring (§5.2) + the D3 rubric (§5.4).
