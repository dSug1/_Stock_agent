# M3 — Stage 3 tiered Claude scoring — explained

*Milestone M3 of the v0.3 build (2026-06-30). The first **paid** stage and the architectural centerpiece.
The dispatch layer is the only networked code; all the orchestration + bundle/schema/cost logic is
offline-testable behind a mockable `Scorer`. See `SPEC_momentum_parser.md` §6, §9.1 and `decisions.md` D-2
(A, D).*

## What Stage 3 does
Per ticker, build the evidence bundle (technical composite + σ_week + harvested media/search/catalyst
dimensions + the next forward catalyst), send it to Claude, and persist a structured probability assessment
(`p_up`/`p_down`/`p_flat` to the vol-normalized target, expected return, per-dimension read, memo) into the
`scores` table. This is the **`p_claude`** leg of the hybrid (Stage 5 blends it with `p_model`).

Three tiers (Decision D), cheapest-first:

| Tier | Model | Search | Dispatch | Job |
|---|---|---|---|---|
| triage | Haiku 4.5 | no | real-time | drop names clearly going nowhere (recall-safe floor `triage_floor`=0.35) |
| rubric | Sonnet 4.6 | yes | **Batch API** | the main analysis on survivors |
| finalize | Opus 4.8 | yes | real-time | adversarial pass on the **contested band** `[0.45, 0.65]` only |

## The lessons-learned, and where each lives
Every prior Claude-API lesson (project memory) is implemented:

| Lesson | Implementation |
|---|---|
| **Batch API default** (−50% tokens) | `AnthropicScorer.submit_batch` / `poll_batch`; rubric tier uses it |
| **Persist `batch_id` before polling** + `--resume` | `store.record_batch(...)` is called *immediately* after submit; schema-v3 `batch_jobs` table; `stage3_score.run(resume=True)` re-polls `store.open_batches` |
| **Crash-safe incremental DB writes** | results are handed to an `on_result` callback the instant each completes → `store.write_score` one at a time; never a bulk write |
| **Bounded SDK timeout** (the "1hr hang") | `client = Anthropic(timeout=request_timeout_s)` (default 120s) |
| **Basic `web_search_20250305`** + bounded `max_uses` + 12500 output cap | set in `rubric.build_request`; the dynamic variant ignored `max_uses` and timed out |
| **Prompt caching** | the stable system prefix carries `cache_control: ephemeral`; volatile per-ticker bundle goes after it |
| **Async bounded concurrency** (real-time tiers) | `score_many_realtime` → `asyncio` + `Semaphore(concurrency)` |
| **Cost gate + calibration** | `cost.estimate` → `[dry-run] / --dispatch` gate + `max_usd_per_run` ceiling; `cost_calibration_factor` 0.10 scales the hot script estimate toward the real invoice |
| **`pause_turn` handling** | `_ascore` re-sends up to `max_continuations` for the server-tool loop |
| **Re-score skip** (don't re-pay for unchanged state) | `config_hash` + `evidence_fingerprint` on every `scores` row; `store.has_fresh_score` skips |

## Structure — pure vs networked
- **Pure / tested** (`scoring/`): `rubric.py` (bundle, system prompt, strict `OUTPUT_SCHEMA`, `build_request`,
  `clamp_parsed`), `cost.py` (estimate), `identity.py` (config/evidence hashes). `stage3_score.py`
  (tiering, gate, skip, Batch lifecycle, incremental persist) is tested via a `FakeScorer`.
- **Networked / not unit-tested** (`clients/anthropic_client.py`): the SDK calls. It exposes exactly three
  methods — `score_many_realtime`, `submit_batch`, `poll_batch` — so the fake is a faithful stand-in.

## Safety / cost posture
- **Nothing bills without `--dispatch`.** `--score` alone prints the estimate and returns. `claude.enabled`
  defaults `false`. The estimate is hard-capped by `max_usd_per_run` ($5/day) — over-budget aborts.
- **Prompt-injection defence:** the system prompt declares all web-fetched text **untrusted data**; the
  bundle is wrapped in a delimited `<evidence_bundle>` block; `web_search` runs with an allow-list
  (populate `claude.allowed_domains` before enabling) and bounded `max_uses`; reads are timeout-bounded.
- **Probabilities clamped** to [0.01,0.99] and renormalized in code (`clamp_parsed`) — the schema can't
  enforce numeric ranges, and no move is ever sold as a certainty.

## How to run / verify
```sh
../.venv/Scripts/python.exe -m pytest tests/test_scoring_rubric.py tests/test_scoring_cost_identity.py tests/test_stage3_score.py -q
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --score --tickers AAPL            # DRY estimate
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --score --tickers AAPL --dispatch  # SPENDS (real Claude)
# debug: cheap end-to-end real call, bypasses the $5 gate, caps to claude.debug.max_names, real-time:
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --score --tickers AAPL --debug --dispatch
# resume a killed run:
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/7_momentum.py --score --run-id <id> --resume --dispatch
```

### Token cap vs run budget (D-3)
`max_output_tokens` (12500) is a per-CALL truncation ceiling — **billed on tokens actually generated, not
the cap** — and is independent of `max_usd_per_run` ($5, the per-RUN spend gate). 12500 is generous for
this small schema; keep it (and stay <16000, above which the SDK requires streaming). `--debug` is the way
to run real calls cheaply: it ignores the $ gate, caps names, and forces real-time — a 1–3-name debug is
cents (calibrated est ~$0.004/name).
`test_stage3_score.py` drives the full tiering with a `FakeScorer`: AAA strong→rubric, BBB borderline→
finalize, CCC dead→triaged-out; asserts incremental persistence, the batch `submitted→done` lifecycle,
the unchanged-state skip, the over-budget abort, and `--resume` draining an open batch. Verified: dry-run
estimate end-to-end (no network/spend).

## Notes / follow-ups
- **Providers still stubbed** (M2): until news/search/catalyst feeds are wired, the bundle's media/search/
  catalyst dimensions are neutral, but the technical composite is real and Claude's web_search reads media
  live — so the call is meaningful even today.
- **Live validation pending:** the real Claude path isn't exercised by tests (network/$). First real
  `--dispatch` run should be a tiny `--tickers` smoke test before any full run.
- Orchestrator stage-renumber to v0.3 still deferred; M3 is reached via `--score` (like `--harvest`).

## What M3 unblocks
Stage 4/5 read `scores` for the `p_claude` leg → blend with `p_model` → long-only ranking; §9 ledger
records `p_final`. The dispatch infra (Batch + resume + incremental persist + cost gate) is now reusable.
