# 6_Biotech_platform_discoverer — What's left to build

*Companion to `SPEC_acrivon_pattern_screener.md` + `decisions.md`. Current as of 2026-06-29.*

The pipeline is **functionally complete end-to-end and run at scale** — universe → hard cuts →
mechanism tagging → evidence harvest → tier-gated Claude scoring (with web research) →
rank/dedup/export → interactive report → seed validation → run summary. Built milestones: **M1–M8** +
**D9** (OpenAlex→web-research), **D10** (observability), **D11** (config-change incremental), **D12**
(SEC ipo-date fallback), **D13–D16** (Claude-dispatch optimizations: async/batch-resume + crash-safe
persist + basic web_search variant + 12500 output cap), **D17** (EDGAR 10-K Item 1 source), **D18**
(structured per-stage JSON logs), **D19** (evidence-level incremental), **D20** (triage-killed rows in
report), **D21–D23** (EU/Nordic enumeration + web-checked caps/IPO dates → tiered), **D24** (name-based
dedup), **D25–D26** (Japan/Korea enumeration + web-checked caps/dates → tiered). **183 offline tests
pass.**

**Coverage today:** US (SEC) + EU/Nordic + Japan/Korea, **614 live companies**, **94 scored** across
Tiers 1 & 2 (US + international), 91-row shortlist. The remaining work is **calibration, data-quality
enrichment, and productionization** — not core plumbing. Ordered by priority.

---

## A. TRUST — validate at scale (HIGHEST)

The whole pipeline is only as trustworthy as its seed-eval (§13), and that is still thin on labels.

1. ~~**Run full Tier-1 / Tier-2 Claude scoring.**~~ **DONE (2026-06-29).** Tiers 1 & 2 — the young,
   priority buckets — scored end-to-end across the full US + EU/Nordic + JP/KR universe: **Tier-1**
   255 candidates → 40 scored ($7.97); **Tier-2** 249 → 50 scored ($9.61). **94 companies scored
   total**, 91-row shortlist (top ACRV 0.968). New high-scorers incl. Immatics 0.904, Monte Rosa
   0.888, Adaptive 0.872, **PeptiDream (Japan) 0.840** — the international wiring delivered a top-tier
   hit. Still open: **Tiers 3 & 4** (older companies, lower priority) to finish the universe.
2. ~~**Validate the D9 web-research switch.**~~ **DONE.** Web-research scoring exercised over ~150
   real rubric calls; memos cite live findings, ACRV/GRAIL hold at ceiling, seed-eval P/R/F1 = 1.0
   across every run. Basic web_search variant + 12500 output cap (D16) confirmed at scale.
3. **Extend `config/seed_labels.csv`.** Current eval is the gating weakness — meaningful but tiny
   (TP=2 / TN=3). Add more
   **in-band** known positives (so they reach the scorer, not graduate out) and more **in-band**
   negatives (small-cap tools/CRO/"AI-pharma shells" that survive Stage 0) so precision/recall become
   statistically meaningful. This is curation, not code.
4. **Threshold tuning loop.** Once 1–3 give real numbers: tune the composite weights, penalties,
   triage keep-rate, contested band, and `seed_eval.decision_threshold` until positives recover and
   negatives reject. D11 makes this cheap — a config change auto-re-opens scored tickers.

---

## B. DATA-QUALITY ENRICHERS (raise signal per company)

5. **EDGAR full-text** ~~+ IR-poster~~ Stage-2 sources (spec §6). **EDGAR DONE (D17/B5)** —
   `clients/edgar_fulltext.py` fetches the latest 10-K/20-F, extracts Item 1 "Business", feeds a 2.8k
   excerpt to the scorer as `sec_10k_business` (US filers, fail-open, capped). Still open: the
   **IR-poster scraper** (AACR/ASCO/ASH posters, robots-respecting) and using S-1 text for pre-10-K
   names; and a better Item-1 anchor (the current longest-match heuristic can include a cross-ref prefix).
6. **Fully-diluted market cap incl. pre-funded warrants** (spec §5.2 / §0.1, deferred TODO). Today
   `mktcap_usd_fd` carries the **basic** yfinance cap (no PFW). For micro-caps PFW materially changes
   the band decision and the tier. Build: FD/PFW share counts cross-checked against filings (10-Q
   cover + warrant tables) → recompute cap. Affects Stage-0b deletes + tiering.
7. ~~`ipo_date` SEC first-filing fallback~~ **DONE (D12)** — `clients/sec_submissions.py` fills a
   missing ipo_date from the SEC earliest-filing date (US filers, fail-open, `stage0b.sec_ipo_fallback`).
   Still open: the D2 **"market-cap appreciation since IPO" lever** (needs price history) for the
   lifecycle multiplier; and note the SEC date is an S-1-era *proxy* (slightly older than true IPO).
8. **PatentsView API key.** The patent estate is inert without `PATENTSVIEW_API_KEY` — set it to
   activate method/platform-vs-composition patent scoring (already wired, just key-gated).

---

## C. PRODUCTIONIZATION (before any non-local / hosted use)

9. **Swap yfinance for a licensed provider** (repo-wide ToS rule). yfinance is LOCAL-prototype-only;
   replace with FMP / EODHD (cap, liveness, IPO date, price history) before hosting. `clients/market`
   is the single seam. **NOW ALSO BLOCKS EU COVERAGE:** the Wikidata net enumerates ~66 EU/Nordic names
   (D21) but yfinance does not resolve foreign-exchange tickers, so they all sit in **Tier 0 /
   unknown-cap** (can't be band-filtered, tiered, or properly scored). A provider with EU market data
   (cap + IPO date for `.CO`/`.ST`/Euronext/Xetra tickers) is what makes EU names first-class.
10. **Scheduler / weekly cadence.** The screen is designed as a weekly monitor. The run `.bat` exists
    but there's no scheduled automation; add a Windows Task / cron (Stage 0 enrich → harvest → score
    selected tiers → rank → summary → render). Top-movers (D10) makes weekly diffs useful.
11. ~~**Structured JSON logs per stage**~~ **DONE (D18/C11)** — `observability.append_stage_log` writes
    one JSON line per stage run (counts in/out, cost, wall-time) to `Outputs/logs/stage_events.jsonl`,
    wired into `6_screen.py` for every stage.

---

## D. SCOPE EXPANSION (v2, only when warranted)

12. **JP/KR regions** — **WIRED (D25)** via the Wikidata net (`JP→Q17`, `KR→Q884` + `run.regions`):
    ~27 JP/KR pharma/biotech enumerated (Takeda, Shionogi, Eisai, PeptiDream, Ono; Korean: Dae Hwa,
    Chong Kun Dang). Still open: they're Tier 0 / unknown-cap (no yfinance for `.T`/`.KS` — needs the
    licensed provider or a web check, like EU); and a **native DART/EDINET feed** for exhaustive
    small-cap coverage (Wikidata is thinner). Science signals are already global.
13. **Embedding semantic pre-rank (Stage 3)** — intentionally SKIPPED (D1) at ~589 companies. Revisit
    only if the universe grows to thousands (a Haiku pass over everything starts costing real money)
    or if semantic dedup/clustering becomes valuable. The `embed/` architecture slot stays open.

---

## E. HOUSEKEEPING

14. ~~Per-milestone explainers for D9–D11.~~ **DONE** — `spec/post_M5_explained.md` covers everything
    built after M5 (M6–M8 summarized + D9/D10/D11 in full).
15. ~~**§12 evidence-level incremental.**~~ **DONE (D19/E15)** — `stage4._company_score_key` folds the
    scoring-config hash with an evidence fingerprint (sorted `source:payload_hash`) into
    `scores.config_hash`; `_candidates` re-opens a ticker when its config OR evidence changed, within the
    TTL. Gated by `stage4_scoring.evidence_incremental` (default true). No schema migration.

---

## NOT on the list (already decided / done)

- Stage-3 embedding — skipped by decision (D1), not a gap.
- OpenAlex — dismissed by decision (D9), not a gap.
- The $50M–3B band ceiling — kept by decision (D7); graduates (TNGX/IDYA) are out-of-scope by design.
- Cardinal-rule deletion fencing, tiering, dedup, lifecycle multiplier (off), rescore-TTL, cost gate —
  all built and tested.
