# What was built after M5 — Stage 5 onward

*Companion to `SPEC_acrivon_pattern_screener.md` and `decisions.md`. Current as of 2026-06-29.*

M1–M5 took the pipeline from an empty store to **tier-by-tier Claude scoring of an evidence bundle**
(universe → hard cuts → mechanism tagging → evidence harvest → Haiku→Sonnet→Opus scoring with a
code-computed composite). Everything since M5 turns that scorer into a **trustworthy, operable,
honest screener**: it ranks and de-duplicates the output, gates expensive scoring by an analyst-chosen
tier, surfaces results in an interactive report, validates itself against known answers, replaces the
weakest data source with live research, and reports + re-runs incrementally.

This note explains that arc. M6–M8 each have a full per-milestone explainer (linked); D9–D11 were
decisions without their own explainer, so they are explained in full here.

---

## M6 — Stage 5: rank, dedup, persist, export  *(full detail: `M6_stage5_explained.md`, D4)*

The terminal stage. Reads the persisted `scores`, then:
- **Ranks** by `rank_score = composite × lifecycle_weight` (the lifecycle age multiplier is **off by
  default** — D2; when on it favours the "early but real" window and floors old/graduated names).
- **De-dups at the presentation layer** (never deletes a row — cardinal rule §0.2): duplicate
  company-ids of one real company (e.g. the seed-CSV "Acrivon Therapeutics" vs the SEC "…, Inc.")
  collapse to one shortlist row via **shared-signal union-find** (ISIN ∪ ticker+country ∪
  suffix-stripped name), keeping the best-scored representative and logging the rest as `merged_ids`.
- **Refreshes the review queue** (§11: marketing/mixed substance, high-composite-low-confidence).
- **Exports** the analyst house-format Markdown shortlist (`Outputs/shortlist.md`) + persists
  `run_meta`. Prereq built here: schema **v3** `ipo_date` (yfinance first-trade date) for the age signal.

## M7 — Tiering (upstream of Claude) + the interactive report  *(full detail: `M7_tiering_and_report_explained.md`, D5)*

- **Tiering** buckets the universe by **market cap × company age** *before* the Claude call:
  T1 small&young (priority) · T2 large&young · T3 large&old · T4 small&old · **T0 untiered** (cap or
  IPO date missing — recall-safe, never hidden). The operator chooses which tiers to spend scoring
  budget on (`--tiers 1,2|all`, or an interactive prompt showing per-tier due counts). This is the
  IPO-date signal used as a **hard upstream filter**, complementing (not replacing) the off-by-default
  Stage-5 lifecycle tilt.
- **Interactive report** rewritten as a **template + data-sidecar** pair (`screener_report.html`
  hash-stable + `screener_report_data.js` per run): tier tabs, green-highlight + acknowledge checkbox
  (localStorage seen-tracking — everything starts NEW until ticked), collapsible Claude memo panel, and
  a new-items filter, ported from `3_Biopharmcatalyst_parser`.

## M8 — Seed-eval validation harness (§13)  *(full detail: `M8_seed_eval_explained.md`, D6/D7)*

The **trust gate**: scores the labeled seed set (`config/seed_labels.csv`: positives / negatives /
borderline) through the funnel and reports **precision/recall + per-stage survival**. Key design:
- A **positive deleted at the hard cut** trips a loud `spec_failure` — except when it merely
  **graduated** above the $3B ceiling (D7: the band is kept; graduates are out-of-scope, not failures).
- A Haiku **triage-kill counts as the pipeline's "predicted negative"**, so a killed positive is a
  recall miss and a killed negative is a correct rejection (not silently ignored).
- **Calibration pass:** after adding in-band negatives (TKNO/MRVI/NEOG) and re-harvesting the seeds,
  P/R/F1 = **1.00/1.00/1.00** over TP=2 (ACRV/BOLD) · TN=3 — a genuine pass, but small n (extend next).

---

## D9 — OpenAlex dismissed; the Claude call web-researches publications + pedigree

**Why.** OpenAlex supplied two signals — publication footprint and founder/scientific pedigree — but
its *institution* registry covers only ~29/589 of small-cap biotech and it rate-limits on a depleting
**$-budget** (the "429" body reads "this request costs $0.001 but you only have $X remaining"). So it
was structurally unable to do its job for exactly the names this screen targets.

**What changed.** OpenAlex is removed entirely (`clients/openalex.py` deleted; dropped from
`stage2.sources`, `stage4` evidence, `build_bundle`, Stage-1 tagging). Its role moved **into the Stage-4
Claude call**: the **rubric (Sonnet) and finalize (Opus) tiers carry the `web_search` server tool**
(`web_search_20260209`, the dynamic-filtering variant both models support — no separate
`code_execution`) and research publications + founder/SAB pedigree live. **Haiku triage stays
search-free** (cheap recall cut). The prompt mandates searching, forbids inventing citations/names, and
injects the curated `prestige_labs.yaml` as a **PRESTIGE LIST** for high-precision founder matching;
rules 6 (pedigree) and 10 (data-coverage fairness: "a signal you can't find ≠ negative evidence") were
rewritten for the research model.

**Plumbing.** `anthropic_client` gained `tools=` support on real-time and batch calls, a `pause_turn`
continuation loop, last-text-block extraction (the schema-constrained JSON after the search turns), and
per-search cost tracking. `max_uses` is bounded so the server tool loop finishes in one call (works
under Batch too). The Stage-4 cost estimate gained a `web_search` tier. Spend stays behind the existing
`--dispatch` → `[y/N]` → `max_usd_per_run` gate.

## D10 — Run summary / observability (§15)

`observability.py` + `scripts/6_summary.py` emit a per-run Markdown digest →
`Outputs/run_<id>_summary.md` (+ a stable `Outputs/run_summary.md`). Read-only (reconstructs the run
from the audit log + run_meta + scores; no API): the **funnel** at each stage (incl. the D9 web-search
count + cost), the **tier breakdown**, the ranked **shortlist**, **top movers vs the previous score
run** (`movers()` diffs each company's two most recent composites), and **seed validation**. Wired into
the run `.bat` after seed-eval. This is the run-summary half of §15; the structured-JSON-logs half is
covered by the existing per-stage `log.info(summary)` + audit rows.

## D11 — Incremental re-runs: a scoring-config change forces a re-score (§12)

`config.config_hash(config)` is a stable hash of the **scoring-relevant** sections only
(`stage4_scoring` + `composite_weights` + `penalties`); schema **v4** adds `scores.config_hash` and
`record_score` stamps it. Stage-4's rescore-TTL now skips a ticker **only if** it was scored within the
TTL **and** under the current hash — so retuning weights/penalties/model (or the D9 web-research switch
itself) automatically re-opens every ticker for re-scoring, while unrelated config edits (Stage-0 nets,
regions) don't. This makes the threshold-tuning loop cheap. (The evidence-level half of §12 — recompute
only companies whose *evidence* changed — remains the Stage-2 freshness TTL; `run_meta.config_hash` is
recorded for a future stage-wide gate.)

---

## The shape of the whole thing now

```
0a universe ─ 0b hard cuts ─ 1 tag ─ 2 harvest(ctgov/patents) ─┐
                                                               │  TIER GATE (M7): operator picks tiers
                                                               ▼
                          4 score: Haiku triage → Sonnet rubric → Opus finalize
                                   (Sonnet/Opus web_search publications + pedigree, D9)
                                   (config-hash stamped; TTL+config re-run gate, D11)
                                                               │
                                                               ▼
                          5 rank + union-find dedup + lifecycle(off) + export (M6)
                                                               │
                 ┌─────────────────────────┬──────────────────┴───────────────┐
                 ▼                          ▼                                    ▼
        interactive report (M7)   seed-eval §13 (M8)                  run summary §15 (D10)
        tier tabs / ack / new     precision/recall + survival         funnel + movers + cost
```

What's still open (calibration at scale, EDGAR/IR sources, FD/PFW cap, provider swap, scheduler) is in
`ROADMAP_remaining.md`.
