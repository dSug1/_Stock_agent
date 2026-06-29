# 6_Biotech_platform_discoverer — What's left to build

*Companion to `SPEC_acrivon_pattern_screener.md` + `decisions.md`. Current as of 2026-06-29.*

The pipeline is **functionally complete end-to-end** — universe → hard cuts → mechanism tagging →
evidence harvest → tier-gated Claude scoring (with web research) → rank/dedup/export → interactive
report → seed validation → run summary. Built milestones: **M1–M8** + **D9** (OpenAlex→web-research),
**D10** (observability), **D11** (config-change incremental). 161 offline tests pass.

What remains is **calibration, data-quality enrichment, and productionization** — not core plumbing.
Ordered by priority.

---

## A. TRUST — the screen isn't validated at scale yet (HIGHEST)

The whole pipeline is only as trustworthy as its seed-eval (§13), and that is still thin.

1. **Run full-universe (or full Tier-1) Claude scoring.** Only ~14 tickers have ever been scored. The
   real deliverable — a ranked shortlist over the live 589 — doesn't exist yet. Tier-1 alone is ~256
   companies. Action: `--stage 4 --tiers 1 --dispatch` (batch), watch `max_usd_per_run`. *Blocked only
   by the cost gate + a human [y/N]; this is an operational run, not a build.*
2. **Validate the D9 web-research switch.** Publications + pedigree moved from OpenAlex to the Claude
   web_search call; the current store's scores mostly pre-date it. Re-score the seeds under web
   research and confirm via `6_eval.py` that it lifts the no-OpenAlex names (esp. ACRV) without
   regressions. *(A seed dispatch is running now — first real exercise of the path.)*
3. **Extend `config/seed_labels.csv`.** Current eval is meaningful but tiny (TP=2 / TN=3). Add more
   **in-band** known positives (so they reach the scorer, not graduate out) and more **in-band**
   negatives (small-cap tools/CRO/"AI-pharma shells" that survive Stage 0) so precision/recall become
   statistically meaningful. This is curation, not code.
4. **Threshold tuning loop.** Once 1–3 give real numbers: tune the composite weights, penalties,
   triage keep-rate, contested band, and `seed_eval.decision_threshold` until positives recover and
   negatives reject. D11 makes this cheap — a config change auto-re-opens scored tickers.

---

## B. DATA-QUALITY ENRICHERS (raise signal per company)

5. **EDGAR full-text + IR-poster Stage-2 sources** (spec §6, deferred). The 10-K "Business" (Item 1)
   and S-1 text are far richer than yfinance's `longBusinessSummary`, and AACR/ASCO/ASH IR posters are
   high-signal and API-invisible. Adds a real description signal for the data-engine judgment. Build:
   a fail-open EDGAR client (submissions API → latest 10-K accession → fetch → extract Item 1; respect
   `USER_AGENT`) + an IR-page scraper (robots-respecting). Wire as new `stage2.sources`.
6. **Fully-diluted market cap incl. pre-funded warrants** (spec §5.2 / §0.1, deferred TODO). Today
   `mktcap_usd_fd` carries the **basic** yfinance cap (no PFW). For micro-caps PFW materially changes
   the band decision and the tier. Build: FD/PFW share counts cross-checked against filings (10-Q
   cover + warrant tables) → recompute cap. Affects Stage-0b deletes + tiering.
7. **`ipo_date` SEC first-filing fallback + appreciation lever** (D2 phase 2). ~4 companies are
   Tier 0 (untiered) for missing `ipo_date`; yfinance gaps will recur. Build a SEC-submissions
   first-filing-date fallback (ticker→CIK via `company_tickers.json`, oldest filing). Then the D2
   "market-cap appreciation since IPO" lever (needs price history) for the lifecycle multiplier.
8. **PatentsView API key.** The patent estate is inert without `PATENTSVIEW_API_KEY` — set it to
   activate method/platform-vs-composition patent scoring (already wired, just key-gated).

---

## C. PRODUCTIONIZATION (before any non-local / hosted use)

9. **Swap yfinance for a licensed provider** (repo-wide ToS rule). yfinance is LOCAL-prototype-only;
   replace with FMP / EODHD (cap, liveness, IPO date, price history) before hosting. `clients/market`
   is the single seam.
10. **Scheduler / weekly cadence.** The screen is designed as a weekly monitor. The run `.bat` exists
    but there's no scheduled automation; add a Windows Task / cron (Stage 0 enrich → harvest → score
    selected tiers → rank → summary → render). Top-movers (D10) makes weekly diffs useful.
11. **Structured JSON logs per stage** (spec §14/§15). Today each stage `log.info`s a summary dict +
    writes audit rows; a structured per-stage JSON log line (counts in/out, cost, wall-time) would
    round out observability. Minor.

---

## D. SCOPE EXPANSION (v2, only when warranted)

12. **JP/KR regions** (spec §3, deferred). Add `JP`/`KR` to `run.regions` + DART/EDINET-aware
    enumeration; the science signals (ClinicalTrials, web research) are already global, so detection
    works even where filings are opaque.
13. **Embedding semantic pre-rank (Stage 3)** — intentionally SKIPPED (D1) at ~589 companies. Revisit
    only if the universe grows to thousands (a Haiku pass over everything starts costing real money)
    or if semantic dedup/clustering becomes valuable. The `embed/` architecture slot stays open.

---

## E. HOUSEKEEPING

14. **Per-milestone explainers for D9–D11.** M1–M8 have `spec/M*_explained.md`; the recent D9
    (web research), D10 (observability), D11 (incremental) currently live only in `decisions.md`.
    Add explainers if the per-milestone-doc convention is to be kept complete.
15. **§12 evidence-level incremental.** D11 covers config-change re-scoring; the "recompute only
    companies whose *evidence* changed since the prior run" gate for Stages 4/5 is still just the
    Stage-2 freshness TTL. `run_meta.config_hash` is recorded and available to build a stage-wide gate.

---

## NOT on the list (already decided / done)

- Stage-3 embedding — skipped by decision (D1), not a gap.
- OpenAlex — dismissed by decision (D9), not a gap.
- The $50M–3B band ceiling — kept by decision (D7); graduates (TNGX/IDYA) are out-of-scope by design.
- Cardinal-rule deletion fencing, tiering, dedup, lifecycle multiplier (off), rescore-TTL, cost gate —
  all built and tested.
