# What was built after M5, round 2 — speed, enrichment & incrementality (D16–D19)

*Companion to `post_M5_explained.md` (which covers M6–M8 + D9–D11). This note explains the four
changes made on 2026-06-29 after the first post-M5 round: a Stage-4 speed fix, a richer evidence
source, structured logging, and evidence-level incremental re-scoring. Plain-English first, then the
mechanics. Newest-decision-first ordering matches `decisions.md`.*

---

## TL;DR

| # | Change | One line | Files |
|---|---|---|---|
| **D16** | Stage-4 Claude **speed fix** | Use the *basic* web_search variant + a bigger output cap → calls go from **>600s timeout** to **~45-70s**, and stop silently dropping companies | `clients/anthropic_client.py`, `stage4.py`, `config.yaml` |
| **D17** | **EDGAR 10-K** Stage-2 source | Feed the scorer the company's own *Item 1 "Business"* narrative — the richest free signal for the data-engine judgment | `clients/edgar_fulltext.py`, `stage2.py`, `scoring/rubric.py` |
| **D18** | Structured **per-stage logs** | One JSON line per stage run (counts, cost, wall-time) → `Outputs/logs/stage_events.jsonl` | `observability.py`, `scripts/6_screen.py` |
| **D19** | **Evidence-level** incremental | Re-score a ticker when its *evidence* changed (new 10-K / trials), not just on a config change | `stage4.py`, `config.yaml` |

All four are tested (177 offline tests pass) and committed.

---

## D16 — Why Stage-4 scoring was slow, and the fix

**Symptom.** Stage-4 scoring was far slower than `3_Biopharmcatalyst_parser`'s dispatch; real-time runs
looked like they hung and a batch never produced results.

**We measured it** (instead of guessing). One real Acrivon rubric call, timed under every combination:

| web_search tool | output mode | time | searches |
|---|---|---|---|
| `web_search_20260209` (dynamic-filtering) + structured *(the old config)* | **>600s TIMEOUT** | — |
| `web_search_20260209` + free-form JSON | 277s | **20** (ignored the cap!) |
| `web_search_20250305` (basic) + structured | 71s | 3 |
| `web_search_20250305` (basic) + free-form JSON | **44s** | 3 |

**Two root causes:**

1. **The web_search variant.** The newer `web_search_20260209` runs a code-execution sandbox per search
   ("dynamic filtering"), **ignores `max_uses`** (it ran ~20 searches when capped at 3), and is **10×+
   slower**. The basic `web_search_20250305` honors the cap and is ~45-70s — and is exactly what
   component 3 uses, which is why it was "much faster." → **Default switched to the basic variant**, with
   a `stage4_scoring.web_research.tool_type` knob to switch back.
2. **The output cap.** `max_tokens=1500` was too small: every call hit `stop_reason=max_tokens`, the
   rubric JSON was **truncated → invalid → rejected → the company was dropped (scored=0)**. This looked
   like a "scoring failure" but was an output-cap bug. → **Raised `stage4_scoring.max_output_tokens` to
   12500** (matches `3_Biopharmcatalyst_parser`).

**Validated.** An 8-ticker re-dispatch scored cleanly: exactly 3 searches/company, no timeout, full
untruncated memos citing real web findings (ACRV 0.968/0.904, GRAL 0.904/0.760). Lessons saved to
project memory (`feedback_claude_web_search_variant_and_output_cap`).

> **Reusable takeaway:** for a bounded, fast web-search call use the *basic* variant; size
> `max_output_tokens` to the *full* response or structured output silently truncates; and diagnose API
> speed empirically on the *real* call (a trivial probe is not representative).

---

## D17 — EDGAR full-text: the 10-K "Business" section

**Why.** The data-engine judgment (axes A "proprietary data" and B "compute engine") leans heavily on
*what the company actually does*. yfinance's `longBusinessSummary` is a one-paragraph blurb. The 10-K
**Item 1 ("Business")** is the company's own multi-page description of its platform, technology, and
pipeline — by far the richest *free* text we can give the scorer.

**How it works** (`clients/edgar_fulltext.py`, all fail-open):

1. Map ticker → CIK (the SEC cik↔ticker file, fetched once per run).
2. Read the filer's submissions JSON, pick the **latest annual report** (`10-K`/`20-F`/`40-F`).
3. Fetch the primary document, strip HTML/scripts, and extract the **Item 1 Business** section
   (`_extract_item1` — longest match between "Item 1." and "Item 1A.", ≥400 chars, capped at 16k).
4. Persist as Stage-2 evidence `source="edgar"`; `build_bundle` feeds a **2.8k-char excerpt** to the
   scorer as `sec_10k_business`.

**Guardrails.** US filers only (a non-US ticker simply gets no `edgar` row, reported as *ABSENT DATA*,
never a penalty — the cardinal recall rule). SEC `User-Agent`, 64MB capped reads, polite rate limit.
Extraction is heuristic — it can include a cross-reference prefix before the real overview — but the
bulk is genuine 10-K text, and a web-search-enabled scorer uses what's relevant. Live ACRV fetch
verified (2026 10-K, 16k chars).

**Note.** Companies scored *before* D17 don't have this evidence yet; re-harvest + re-score to benefit
(D19 auto-re-opens them once `edgar` evidence lands).

---

## D18 — Structured per-stage JSON logs

Each stage already `log.info`'d a summary dict and wrote audit rows. D18 adds a machine-readable trail:
`observability.append_stage_log()` writes **one JSON line per stage run** — `{ts, run_id, stage,
elapsed_s, summary}` — to `Outputs/logs/stage_events.jsonl`. Because each stage runs as its own process
invocation (its own run_id), a single **cumulative** event log (not a per-run file) is the queryable
shape for the weekly-monitor cadence: you can grep wall-times and costs across every run. Wired into
`scripts/6_screen.py` with a tiny `_emit()` timing wrapper around all six stages; fail-open (logging
never breaks a stage).

---

## D19 — Evidence-level incremental re-scoring

**The gap.** D11 re-opens a ticker for scoring when the *scoring config* changes; the Stage-2 TTL
refreshes *harvested evidence*. But nothing re-scored a ticker when its **evidence** actually changed
(a fresh 10-K, new trials, updated patents) within the 1-year rescore-TTL — so the weekly monitor could
sit on a stale score.

**The fix** (no schema migration). For each company we compute an **evidence fingerprint** — a hash of
its sorted `source:payload_hash` pairs — and *fold it into* the value stored in `scores.config_hash`
(`_company_score_key = hash(config_hash : evidence_fingerprint)`). At candidate-selection time we
recompute that key; a ticker is skipped only if its stored key still matches. So **either** a config
change **or** new evidence re-opens it. Gated by `stage4_scoring.evidence_incremental` (default `true`;
`false` = legacy config-hash + TTL only).

One consequence: scores written before D19 carry a plain config_hash, so each previously-scored ticker
re-scores **once** the next time it's a candidate — expected and harmless.

---

## Where this leaves the pipeline

Functionally complete and now production-shaped: the remaining `ROADMAP_remaining.md` items are
**operational** (run full-Tier-1 scoring at scale, extend the seed set, tune thresholds) or
**pre-deploy** (swap yfinance for a licensed provider, IR-poster scraper, FD/PFW market cap), not core
plumbing. The buildable backlog — EDGAR source (B5), structured logs (C11), evidence incremental
(E15) — is done.
