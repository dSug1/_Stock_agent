# M2 — Stage 2 multidimensional harvest — explained

*Milestone M2 of the v0.3 build (2026-06-30). Populates the `evidence` + `catalysts` tables M1 created.
Pure feature math is offline; the network is isolated behind fail-open clients. See `SPEC_momentum_parser.md`
§4–§5 and `decisions.md` D-2 (B, G).*

## What Stage 2 produces
For each ticker on its `asof` date, three **evidence** rows — one per leading-attention dimension — plus the
forward **catalysts** calendar:

| Dimension | What it captures | `score ∈ [-1,1]` driven by |
|---|---|---|
| `media` | mainstream-media **volume surge** + **tone** + attention slope | 0.6·(surge−1) + 0.4·tone |
| `search` | **search-interest surge vs baseline** (the 5_Hype_parser idea; replaces social) | surge−1 |
| `catalyst` | **days to the next scheduled catalyst** (forward only) | 1.0 at day 0 → 0 at the horizon |

These are *numbers only* — the rich/contextual reading is left to Claude's `web_search` at Stage 3
(spec §4). Keeping Stage 2 numeric also means **no untrusted scraped text enters a prompt from here**, so
this stage adds no prompt-injection surface.

## Design — three layers, each with a job
1. **Pure feature math** (`features.py`, `catalysts.py`) — `surge_ratio`, `slope`, `media_features`,
   `search_features`, `days_to_next`, `proximity_score`. No network, no pandas → fast and fully unit-tested.
2. **Provider seams** (`clients/news.py`, `clients/search_interest.py`, `clients/catalysts_client.py`) —
   one isolated module per source, each **fail-open** (returns `[]` until a provider is wired). The actual
   providers are a *deferred decision* (GDELT for news, a Trends-style feed for search, FDA/ct.gov/earnings
   calendars for catalysts — all PIT-archivable, which the §9 backtest needs). Swapping in a real provider
   is a one-file change; nothing downstream imports a provider directly.
3. **Orchestration** (`stage2_harvest.py`) — wires clients → features → `store.upsert_evidence` /
   `upsert_catalysts`. Clients are **injectable** (`clients=` dict) so the whole write path is tested
   offline with fakes.

## Key behaviours
- **Surge vs baseline:** `surge_ratio(series, w)` = latest ÷ mean of the preceding `w` values. A ratio > 1
  is attention building above its recent norm — the leading signal. Returns `None` (not 0, not a crash) on
  thin history or a non-positive baseline; the feature builder maps that to a neutral 0 contribution.
- **Forward-only catalysts (Decision G):** `days_to_next` ignores past dates entirely; `proximity_score`
  ramps 1.0 → 0 across `catalyst_horizon_days` (default 21) and scores 0 beyond it. A *scheduled* readout
  next week is anticipatory; the *result* is a-posteriori and never enters here. `asof` is always passed in
  — the catalyst math never reads the wall clock.
- **Fail-open / data-coverage fairness:** with no provider wired, every dimension still writes a row (score
  0, empty features) so the pipeline runs and a missing signal is *absence of evidence*, not negative
  evidence (6_Biotech "rule 10"). A ticker with no `asof` (no cached bars) is skipped + counted, never
  guessed.

## How to run / verify
```sh
# offline tests (12 of the 43 are M2)
../.venv/Scripts/python.exe -m pytest tests/test_features.py tests/test_catalysts.py tests/test_stage2_harvest.py -q
# CLI (writes evidence+catalysts for the universe / --tickers)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --harvest --tickers AAPL
```
`test_stage2_harvest.py` injects fake clients and asserts all three dimensions land with the right
scores, the catalyst proximity (`days_to_catalyst`) is correct, the `catalysts` table is populated, and
the fail-open + no-asof paths behave. Verified end-to-end via `--harvest`.

## Notes / follow-ups
- **Orchestrator stage numbering** still uses the v0.1 scaffold's `--stage 1..4`; M2 is reached via the
  additive `--harvest` flag. The orchestrator will be reconciled to the v0.3 spec numbering (0a/0b/1/2/3/4/5)
  when the Claude stages land — tracked in the handoff.
- **Providers are stubs.** Wiring GDELT / a search-interest feed / catalyst calendars is its own task; the
  fail-open seams keep the pipeline runnable until then, and Claude's web_search still reads media live at
  Stage 3.

## What M2 unblocks
Stage 3's evidence bundle now has real inputs to read (`store.get_evidence` + `store.next_catalyst`), and
the §9 backtest has the dimensions it will reconstruct from PIT archives.
