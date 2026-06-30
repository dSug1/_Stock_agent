# M10 — GDELT news provider (real media dimension, 429-safe) — explained

*Milestone M10 of the v0.3 build (2026-06-30). The first real data provider — the Stage-2 **media**
dimension stops being a neutral stub. Ported from 5_Hype_parser and hardened against HTTP 429 as the
operator requested. See `SPEC_momentum_parser.md` §4 and the memory
`feedback_claude_web_search_variant_and_output_cap` (sibling lesson) + 5_Hype's `ingest/gdelt.py`.*

## What it does
`clients/gdelt.py` fetches real daily **article volume** (GDELT DOC 2.0 `TimelineVolRaw`) and **tone**
(`TimelineTone`) per ticker over a lookback, feeding `features.media_features` (surge ratio + tone) — so
the `media` evidence dimension is now real instead of 0. Wired via `sources.media_provider: gdelt` (the
fail-open stub stays the default-off alternative); the daily run uses it automatically.

## 429-avoidance (the 5_Hype lesson, extended)
GDELT throws 429 aggressively when queried fast across many tickers. Four layers, in order of impact:
1. **Same-day disk cache** (`data/gdelt_cache`, TTL 24h) — the biggest lever: a re-run never re-hits GDELT
   for a query already fetched today. The 2 modes × N tickers collapse to one fetch each per day.
2. **Proactive inter-call spacing** — a global `min_interval_s` (5s) between *any* two GDELT calls, so we
   never burst.
3. **Retry with backoff honoring `Retry-After`** on 429, then **fail open** (return empty — a signal we
   can't fetch ≠ negative evidence; `media` just stays neutral for that name).
4. **Capped read** (64MiB) + hardcoded HTTPS endpoint (no SSRF; ticker only enters query params).

## Precision: query by company NAME, not ticker
Validated live that `"GME stock"` returns ~0 articles but the name `"GameStop"` returns a real series
(**30/31 days with news; 2–16 articles/day**), and `"Tesla"` returns 277–581/day. So the client prefers a
**company-name phrase query** (`name_query_template '"{name}"'`), pulling names from the discovery table via
`OPTS['names']`; it falls back to `"{ticker} stock"` when the name is unknown. This is what makes the media
dimension actually informative.

## Live validation (2026-06-30)
- `"GameStop"` → 31-day series, 30 days with news (2–16/day). `"Tesla"` → 277–581/day (attention building).
- Parser verified on GDELT's real date format `20260603T000000Z` → `2026-06-03`.
- **Honest caveat:** GDELT rate-limits hard even at 5–6s spacing on *first* contact, so some individual
  calls fail-open under load (one MRNA `counts` call did; its `tone` got through at 0.18). That's acceptable
  by design — fail-open + data-coverage fairness — and the **same-day cache** means a daily run pays the
  fetch cost once and re-runs are free. For a full universe, expect a few minutes of (cached-thereafter)
  fetching and a few fail-opens.

## How to run / verify
```sh
../.venv/Scripts/python.exe -m pytest tests/test_gdelt.py -q     # 7 offline tests
# live (config sources.media_provider: gdelt): the daily harvest uses GDELT automatically
PYTHONPATH=src python scripts/7_momentum.py --harvest --tickers GME,AMC
```
Tests: parse on the real date format, window-fill, **cache prevents the second hit** (the core
429-avoidance), **429 → retries → fail-open**, tone normalization, and name-vs-ticker query selection.

## Status / follow-ups
- **Media dimension is now real** (where GDELT cooperates). Search + catalyst dimensions remain stubs
  (next providers: a Trends-style search feed; FDA/ct.gov/earnings calendars).
- **PIT media archive for the backtest** is still deferred — GDELT has the history, but reconstructing
  point-in-time daily series for every historical decision is its own task; for now only the technical leg
  is backtestable (M9), and the media dimension is validated forward via the ledger.
- Query tuning (disambiguating common names, sector qualifiers) is a refinement on `name_query_template`.

## What M10 completes
The first real signal dimension is live, behind a 429-safe, cached, fail-open provider seam. The model's
media inputs are no longer neutral — which is where the M9 backtest said the edge has headroom.
