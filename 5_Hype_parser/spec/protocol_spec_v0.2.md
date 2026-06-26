# Protocol Specification — v0.2

**Depends on:** Framework Spec v0.1, Features Spec v0.2.
**Purpose:** how to construct the labeled panel, fit every ⚙ parameter without look-ahead, validate the system, and run it weekly. This document is where the numbers in the Features spec come from — and where the project can be killed before feature engineering if the premise fails.

---

## 1. The cardinal rule — point-in-time (PIT) everything

Every feature for a panel name at its observation week `t0` is computed using **only** data timestamped ≤ `t0`, against the **source registry version frozen as of ≤ t0**. Specifically:
- No restated/as-reported-later fundamentals; use first-print filings.
- No survivorship: the panel universe is drawn from a **delisted-inclusive** historical ticker set (bankruptcies, buyouts, going-dark must be present, or hard-negatives are systematically missing).
- No post-hoc theme knowledge: "KRAS was hot," "CTV won" must be reconstructed from `β_spec`/`p_main` at `t0`, never assumed.
- Registry add-dates gate source availability: a source added in 2023 cannot inform a 2020 `t0`.

A single PIT violation invalidates the parameter it touches. This rule outranks every convenience.

---

## 2. Labeled panel

### 2.1 What a label is
Binary outcome over horizon `H` (⚙, ~3–18 mo from `t0`), assigned **only after** `t0` features are frozen:

- **Positive ("ran-on-hype"):** forward return ≥ `R_hit` (⚙, e.g. ≥100% or ≥ k·sector-vol) over `H`, **AND** the run is narrative/attention-driven — operationalized as **multiple-expansion share of return ≥ `m_share`** (⚙): decompose return into Δmultiple and Δfundamental; require the re-rating, not earnings, to dominate. This `m_share` clause is what separates hype from ordinary value-realization and is non-negotiable.
- **Hard negative ("looked like a setup, fizzled"):** cheap at `t0` **AND** had a catalyst or live theme **AND** some attention — i.e., *resembles a positive on the cheap/catalyst axes* — but did **not** clear `R_hit·`(⚙ lower bar) over `H`. **TScan is the archetype.** These are the discriminating examples; the model learns nothing from easy negatives.
- **Easy negative (context only, capped quota):** cheap, no theme, no catalyst, no attention. Included sparingly to anchor the floor.

### 2.2 `t0` selection — mechanical, leak-free
**The danger:** choosing `t0` "just before the run" uses outcome knowledge to set timing — look-ahead on the most important axis. **The rule that removes it:**

```
t0 := first week at which a name mechanically satisfies
        Mispricing_gate(name, t)  AND  ThemeNascency_gate(name's theme, t)
```
`t0` is thus determined **entirely by PIT features**; the forward outcome is then measured over `[t0, t0+H]` to assign the label. Feature-set and label are causally separated. If a name satisfies the gates in multiple disjoint episodes, each episode is a separate panel row.

### 2.3 Sampling, stratification, anti-clustering
- **Quota:** hard-negative : positive ≥ 1 : 1. Easy-negative ≤ ⚙ cap.
- **Temporal stratification:** positives must **not** cluster in 2020–2021. Cap any 12-month window at ⚙ share of positives, to avoid learning "the COVID liquidity bubble" as if it were a signal.
- **Sector stratification:** spread across ≥ ⚙ sectors so the model is not a disguised biotech screener.
- **Regime balance:** both A and B (and A+B) represented; the `max()` generalizations are only tested if Regime-B and dual names are present.
- **Size:** target n ≥ 100 labeled episodes (≥ 50 positive). The n=2→n=7 anchors set *structure*; only the panel can set *weights*. Report n and bootstrap CIs on every fitted quantity.

### 2.4 Seeded anchors (must appear, with verdicts pre-registered)
ELTX(~mid-2024)=positive · ROKU(2019), NET(2020), SNAP(2020), SOFI(2021)=positive · CRISPR cohort(~2020)=positive basket · **TCRX(Dec-2025)=hard-negative**. Anchors are sanity rails, **not** the training set — they are too few and were hand-chosen.

---

## 3. Backtest mechanics

### 3.1 Temporal split (never random)
Random k-fold leaks regime information across folds. Use **walk-forward**: fit on `[…, T_cut]`, validate on `(T_cut, …]`; roll `T_cut` forward in ⚙ blocks. Report out-of-sample only.

### 3.2 What gets fit, and the free-parameter budget
- Fit: factor weights `w·`, squash/sigmoid params, gate thresholds (`β_min, p_max, q_own, q_sect, a_lo`), `θ`, phase bands.
- **Regularization is mandatory** (L1/L2 on weights) given modest n; prefer fewer effective parameters. Maintain an explicit **free-parameter count**; if it approaches n, the fit is overfit by construction — simplify the feature set, do not add data-snooped epicycles.
- Constants the Features spec hard-codes by reasoning (e.g., catalyst window `T_lo=6, T_hi=24`) are **sensitivity-tested**, not frozen on faith: sweep ±, confirm the result is not knife-edge.

### 3.3 Evaluation — not just classification accuracy
Primary: **forward return of the QUALIFY set vs four benchmarks** at matched `t0`:
1. cheap-only (Module A pass, ignore hype),
2. random cheap names,
3. hot-theme-only (B2 high, ignore cheap),
4. cheap + catalyst (no narrative factors).
The system earns its existence only if it beats **all four**, especially (4) — beating (4) is the proof that B1×B2×B3 add over "cheap with a catalyst" (the TScan/Elicio distinction made quantitative).
Secondary: precision/recall at `θ`; **payoff asymmetry** (these are low-hit-rate, convex-payoff by nature — judge on expectancy, not hit rate); monotonicity (do higher HYPE_score deciles → higher forward returns?).

### 3.4 Calibration
Reliability curve of HYPE_score vs realized positive rate. A score that does not rank-order outcomes monotonically is mis-specified regardless of headline AUC.

---

## 4. The kill-switch test — run BEFORE feature engineering

The whole project rests on one unproven claim: **narrative legibility + thematic heat drive the re-rating, beyond cheap + catalyst.** Test it first, cheaply, on the panel:

```
forward_return  ~  Legibility×ThematicHeat  +  controls(free_float, time_to_catalyst, drawdown, sector, era)
```
If the narrative composite has **no incremental explanatory power** (coefficient indistinguishable from 0 after controls): the premise is false, the program collapses to a generic value+momentum screen, and **you stop here** rather than build five feature modules around a dead hypothesis. Pre-register the pass condition (⚙ effect size + sign) before looking.

This is the cheapest high-value experiment in the project. Gate all of §3 (full fitting) behind it.

---

## 5. Manipulation / adverse-selection filter

`AttentionAccel(B3) high + tiny float(B4 micro) high + binary catalyst(B5)` is **also** the pump-and-dump / informed-leakage signature. Operational rule (carried from framework §11):
```
if B3 high AND (B1 low OR B2 low):   flag = "attention without narrative — red flag, not buy"
```
Such names are surfaced **with the red flag**, never auto-ranked into actionable. Additionally exclude on ⚙ liquidity/known-promotion blocklist. The screener must not become a pump amplifier.

---

## 6. Weekly operational protocol

Reuses your established patterns:
- **Cadence:** weekly scheduled run (Windows Task Scheduler). Registry version pinned and logged per run.
- **Pipeline order:** ingest → embed (local) → diffusion/theme radar → survival filter → constituent expansion (embeddings prefilter → Claude **Batch** + prompt caching) → Module A → Module B (Claude only for B1 `one_sentence`, on survivors) → combine/rank → output.
- **Cost gating:** credit-balance check **before** any Claude call; mid-run monitor; cost estimate reflects the **actual survivor subset**, not the universe. Target envelope ~$5–15/run.
- **Caching:** per-type JSON caches with TTLs + 30-day hard TTL; **debug mode clears pipeline caches but never foundational caches** (embeddings store, ticker/cusip caches). Embedding store is foundational.
- **Timing:** `module_timer()` per stage; pipeline summary table + JSON timing log.
- **Outputs:** dated files to `_outputs/`; intermediates to `_intermediate_outputs/`; inline-styled HTML watchlist for cross-device portability.
- **Decision log:** every calibration choice and PIT deviation → `spec/implementation_decisions.md`.

---

## 7. Exit / sell-the-news discipline (entries, not holds)

The system emits **entries**, and `phase_on_curve ∈ {base, early-markup}` is the only actionable state. The output must also carry an **exit trigger** per name, because for Regime-A names the catalyst is usually a sell-the-news unwind (Elicio: gains in Phase 1–2, losses in Phase 3):
- Regime A: exit flag fires as `time_to_event` enters the final ⚙ weeks (IV-rich, crowd-in) — explicitly **before** resolution.
- Regime B: exit flag fires when `p_main` crosses `p2` (mainstream saturation) or the confirmation cadence breaks (a KPI miss / guidance cut).
The program never advises holding through a binary readout.

---

## 8. Acceptance criteria (go/no-go to lock features → build)

Proceed to implementation only if **all** hold:
1. Kill-switch (§4) passes pre-registered effect size.
2. QUALIFY set beats **all four** benchmarks (§3.3), out-of-sample, including cheap+catalyst.
3. HYPE_score deciles are monotone in forward return (§3.4).
4. Anchor verdicts reproduce: ELTX/ROKU/NET/SNAP/SOFI/CRISPR = positive at their `t0`; **TCRX = hard-negative** (its multiplicative score ≈ 0 despite passing the value gate).
5. Free-parameter count comfortably below n; results survive ±sensitivity sweeps on the reasoned constants.
6. Manipulation filter (§5) demonstrably flags known historical pump cases in the panel era.

Failing 1 → stop. Failing 2–4 → the feature set is mis-specified; revise before any production build. Failing 5 → simplify. Failing 6 → do not ship.

---

## 9. Build-order dependency (corrected critical path)

```
Source registry instance (#1)  ─┐
diffusion_ratio params (#2, Features §1) ─┤→ both feed the panel
Labeled PIT panel (this doc §2) ──────────┘
        │
        ▼
Kill-switch test (§4)  ──fail──▶ STOP
        │ pass
        ▼
Fit features (Features §5 ⚙ via §3) → Acceptance (§8) → production weekly run (§6)
```
The panel is the gating artifact: nothing downstream is justified until it exists, and it is also what the kill-switch consumes. Registry (#1) and diffusion params (#2) can be built in parallel since neither depends on the panel.
