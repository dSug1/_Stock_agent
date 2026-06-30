# M7 — Stage 0a discovery + 0b gate (the universe front) — explained

*Milestone M7 of the v0.3 build (2026-06-30). The last pipeline stage — it *produces* the universe the rest
of the pipeline consumes (Decision C). Proven end-to-end on the live API. See `SPEC_momentum_parser.md` §3,
§5 and `decisions.md` D-2 (C).*

## What it does
- **Stage 0a — discovery (`stage0_discovery.py`, weekly, paid):** one cost-gated Claude call (web_search)
  proposes the candidate basket — retail-heavy, hype-prone, plausibly-liquid US names where attention is
  *building* — persisted to `discovery`. Reuses the M3 dispatch (`AnthropicScorer.complete`, allow-list,
  timeout, pause_turn).
- **Stage 0b — gate (`stage0_gate.py`, daily, free):** prunes candidates by **liquidity** (ADV $ volume ≥
  $100k×safety_mult), **volatility** (weekly σ ≥ floor — the thesis needs movement), and **price** (≥ penny
  floor). **Flag, never silently drop** (cardinal rule): every name gets a status
  (`pass`/`penny`/`illiquid`/`placid`/`no_data`); only `pass` → `universe_gate` feeds scoring.
- `universe.load_universe(cfg, store)` now **prefers the gate-pass list** (`store.investable_tickers()`),
  falling back to the seed CSV — so discovery→gate becomes the live universe for `--harvest`/`--score`/`--blend`.

## Schema v6
`discovery(run_id, ticker, name, reason, tags, discovered_at)` + `universe_gate(ticker, asof, status, adv_usd,
weekly_sigma, last_price)`. DAO: `write_discovery`/`latest_discovery`, `write_gate`/`investable_tickers`
(tickers whose *most-recent* gate row passed).

## Live proof (2026-06-30)
- `--discover --dispatch` → **42 real candidates** from 5 web searches: meme/retail (GME, AMC, **HOOD** —
  "Reddit mentions surged 421% in 24h to June 29", SOFI, RDDT, PLTR, MSTR), hype-prone biotech (MRNA, VKTX,
  NTLA, BEAM, AXSM, IOVA — the motivating case), meme-adjacent (WEN, DNUT, CVNA). Reasons cite genuine
  *leading* signals.
- `--gate` on 10 fetched names → **all pass** with real metrics (HOOD $3.3B ADV/σ0.13, MRNA $481M/σ0.09/$69.70,
  AMC σ0.17/$2.03, NVDA $33.8B…); un-fetched candidates correctly fall to `no_data`.

## Lesson learned (recorded to memory)
The first real discovery call returned `{"candidates": []}` with **0 web searches** — under a forced JSON
schema *and* an open-ended "find names" task, the model emitted the empty structured output without
searching (Opus/Sonnet 4.6+ reach for tools conservatively). **Fix: a prescriptive "you MUST call
web_search several times before answering" directive** (system + an explicit search-first user turn) — then
it ran 5 searches and returned 42 names. Second issue: 8 searches **exceeded the 120s timeout** (the
slow-multi-search class from `feedback_claude_web_search_variant_and_output_cap`) → gave discovery its own
`discovery_timeout_s` (300) + trimmed `discovery_searches` to 6. *Per-ticker* scoring (specific subject)
searched fine without the directive; *open-ended* discovery needs the push.

## How to run / verify
```sh
../.venv/Scripts/python.exe -m pytest tests/test_stage0.py -q
PYTHONPATH=src python scripts/7_momentum.py --discover --dispatch     # 0a (paid; dry without --dispatch)
PYTHONPATH=src python scripts/7_momentum.py --stage 1 --tickers <discovered>   # fetch bars
PYTHONPATH=src python scripts/7_momentum.py --gate                    # 0b (free) -> investable universe
```
Tests: discovery request/schema/`clean` (dedupe/cap), discovery orchestration via a fake scorer (dry +
persist), gate statuses (pass/penny/illiquid/placid/no_data), and `investable_tickers`.

## What M7 completes
**All pipeline stages now exist:** 0a discover → 0b gate → 1 prices → 2 harvest → 3 tiered Claude → 4 model
→ 5 blend → §9 validate/calibrate. The screen now builds its own universe. Remaining is trust + production
(out-of-sample calibration, forward ledger, orchestrator-renumber/daily-runner, provider wiring, v2 loop) —
no new architecture.
