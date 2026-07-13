# D24 — OpenAlex credit-budget guard + credit-safe daily runner

*Prose companion to decision D24. Read this to understand why the OpenAlex jobs now watch a credit
budget, how the guard works, and how to run/verify it.*

## The problem

The literature signal (§3.1) and the §5.2 independence refinement are the two OpenAlex-backed jobs, and
author RESOLUTION (`/authors?search=`) is the pipeline's digest bottleneck — only names whose founders
resolve to an OpenAlex author get an independent-citation trail, and only those clear the pre-filter.

Historically OpenAlex was framed as a pure per-second rate limit ("stay at 5/s, never burst"). As of
mid-2026 that's no longer the binding constraint. OpenAlex now enforces a **credit / USD quota** on top
of the rate limit. Every response carries these headers:

| Header | Meaning | Observed 2026-07-13 |
|---|---|---|
| `X-RateLimit-Limit` | credits per window | ~**1000** |
| `X-RateLimit-Remaining` | credits left this window | decrements per call |
| `X-RateLimit-Reset` | seconds to the window reset | ~**77,000s ≈ 21.6h (≈ daily)** |
| `X-RateLimit-Limit-USD` / `-Remaining-USD` | the same budget in dollars | ~**$0.10** |
| `X-RateLimit-Prepaid-Remaining-USD` / `-Onetime-Remaining` | a **prepay** path for more | 0 (free tier) |

**Cost is per-endpoint, not per-call:**
- `/authors?search=` (author resolution) = **10 credits**
- a `/works` list / `cites:` page = **1 credit**

So the free tier is roughly **100 author searches per day**. A naive sweep of a large founder backlog
blows the budget partway through, then every remaining call 429s — wasting retry/backoff wall-clock and,
worse, risking a half-processed founder being stamped "done" with no citations (poisoning).

## The design

**Read the budget from the server and stop before it's gone — never hardcode the per-endpoint prices.**
The tracker reads `X-RateLimit-Remaining` after every call, so it self-corrects no matter what an
endpoint costs. Four pieces:

1. **`_net` header observation.** `get_json_retry` / `safe_json_retry` take an optional `on_headers`
   callback, invoked with the response headers on **both** a success **and** a retryable `HTTPError` (so a
   429's `remaining=0` is seen too). It is defensive — a missing `.headers` attribute or a throwing
   callback never breaks the fetch — and it does not change the JSON return contract.

2. **`openalex.CreditTracker` + a module-shared `CREDITS`.** `note(headers)` updates
   remaining/limit/reset/USD. `exhausted(reserve)` is **False while the budget is unknown** (so the very
   first call of a run is always allowed — it populates the tracker) and **True once observed remaining ≤
   reserve**. `_get` feeds every response into `CREDITS` and short-circuits to `None` once the budget is
   truly gone — and a `None` from `_get` is exactly the "throttled fetch" signal the callers already
   handle by leaving the founder **unstamped** for retry (the D14/M16 anti-poisoning contract).
   `probe_credits()` makes one cheap 1-credit `/works` call to populate the budget before a run.

3. **A per-founder budget gate.** `config.openalex_credit_reserve` (default **40**) is sized to
   comfortably finish one founder in flight (an author search ~10 credits + a few 1-credit publication /
   citation pages). The literature and independence loops check `CREDITS.exhausted(reserve)` at the **top
   of each founder iteration** — never mid-founder — and `break`, leaving the rest of the backlog
   unstamped. Because the check is between founders, a half-processed founder is never stamped or
   poisoned. The result objects carry `stopped_early` / `budget_left`.

4. **`scripts/8_openalex_daily.py` (+ `run_8_openalex_daily.bat`).** Probe the budget, then run the
   literature pass and the independence pass **in one process** so the shared `CREDITS` tracker carries
   the budget across both. It reports remaining credits, the reset time, and the remaining backlog after
   the run, and it bails early (no expensive sweep) if the budget is already at the reserve.

## Why this satisfies "don't lose results on a 429"

Three existing/added guarantees compose:
- **Per-founder persistence** — every resolved author + emitted citation is committed as it's produced.
- **None-on-throttle contract** — a throttled fetch returns `None` (not `[]`), so the founder is left
  unstamped rather than recorded as a genuine "no author / no citations".
- **Between-founders-only gate** — the budget stop happens before a new founder starts, so no founder is
  ever left half-written.

A re-run in the next window therefore resumes exactly where it left off, losslessly.

## How to run

From the component dir (`8_Early_stage_biotechs`):

```
run_8_openalex_daily.bat              REM probe budget → literature + independence, credit-safe
run_8_openalex_daily.bat --limit 50   REM cap founders attempted (also budget-bounded)
run_8_openalex_daily.bat --no-independence
```

or directly:

```
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_openalex_daily.py
```

The individual `8_signals.py --literature` / `--independence` commands also now print the credit budget
and whether they stopped early. When the backlog is drained, fold the new evidence into conviction with
the paid, operator-gated re-score:

```
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_score.py --force
```

## Verified

- `tests/test_openalex_credits.py` (8): header parsing, exhaustion semantics (unknown → allowed; at/below
  reserve → blocked), the `_get` short-circuit making no network call when exhausted, and the
  literature + independence loops stopping on the reserve while leaving the backlog unstamped. Full suite
  **158 passing, offline**.
- **Live drain (2026-07-13):** literature 36/36 founders (4 authors resolved, 759 independent citations),
  independence 46/46 (5,821 independent citations after co-authorship reclassification); 987 → 454 credits
  consumed, never touching the reserve; backlog 0 / 0.

## Follow-ups

- **Free author-resolution fallback** to relieve the 10-credit author-search chokepoint: **ORCID Public
  API** (free; needs free OAuth public-API credentials, not paid membership) and **Crossref REST** (free,
  no key, `mailto` polite pool, no credit quota). Highest-leverage next build for OpenAlex-independent
  author resolution.
- **Prepaid OpenAlex** (the `-Prepaid-Remaining-USD` / `-Onetime` headers) is the concrete paid path if
  the free ~1000-credit budget becomes the constraint; raise `openalex_credit_reserve` accordingly (the
  tracker reads the real remaining count regardless).
- **Schedule** `run_8_openalex_daily.bat` on the daily runner so the founder backlog drains a window at a
  time without manual re-runs.
