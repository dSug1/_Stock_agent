# Features Specification — v0.2

**Depends on:** Framework Spec v0.1. **Pairs with:** Protocol Spec v0.2.
**Scope:** operational definitions and formulas for every feature, including the full `diffusion_ratio` instrument (framework artifact #2, folded in here because B2/B3 and the ThemeNascency gate are undefinable without it). **All free constants are marked ⚙ and deferred to the labeled panel (Protocol §2–3); none are invented here.**

---

## 0. Conventions

- **Point-in-time (PIT).** Every feature at evaluation week `t` uses only data timestamped ≤ `t`. No restated fundamentals, no survivorship. The source registry version is pinned per run.
- **Normalization method (fixed; parameters fit later).** Each raw feature → [0,1] by **cross-sectional percentile within the week's candidate pool**, unless a within-name/within-theme transform is specified. Logistic squashes use ⚙ params fit on the panel. Percentile is preferred over logistic where the pool is large enough (≥ ⚙ `N_min_pool`), because it is regime-robust.
- **Parameter notation.** ⚙ = fit or selected on the labeled panel (Protocol). Symbols collected in §5.
- **Regime tag.** Each name is tagged A (discrete-catalyst) or B (continuous-confirmation); some are A+B. Several features branch on regime.

---

## 1. Theme instruments (`diffusion_ratio` and derivatives)

### 1.1 Embedding space & centroids
- Embed every ingested document with a **local** sentence-transformer (no LLM generation). Model choice ⚙; frozen per registry version.
- A **sub-theme** is represented by a centroid vector `c`. Seed: short descriptor text + optional seed docs → embed → mean. Refine: mean of top-matching docs over the seed window (one-cluster k-means; ≤ ⚙ `iter_max` iterations).
- **Synonymy is handled by the embedding geometry**, not rules ("CTV" ≈ "connected TV" ≈ "OTT ads" cluster together). No synonym dictionary is maintained.
- **Unsupervised discovery.** Weekly, cluster all recent specialist docs (HDBSCAN on embeddings, ⚙ min_cluster_size). A cluster that is (a) growing and (b) cosine-distant from every existing centroid (max sim < ⚙ `τ_novel`) is a **candidate new theme** → one Claude call to label it (Protocol-bounded cost).

### 1.2 Membership & mention counts
Document `d` is a member of theme `c` at granularity if `cos(embed(d), c) ≥ τ_member` (⚙). For source class `S` (specialist) and `M` (mainstream, per registry `diffusion_position` field):

```
N_spec(c,t) = | { d ∈ S : timestamp(d) ∈ (t−W_spec, t] ,  cos(embed(d),c) ≥ τ_member } |
N_main(c,t) = | { d ∈ M : timestamp(d) ∈ (t−W_main, t] ,  cos(embed(d),c) ≥ τ_member } |
```
Windows `W_spec`, `W_main` ⚙ (trailing weeks). Bridge-class sources counted into a separate `N_bridge` for the survival filter (§1.6).

### 1.3 Specialist momentum (the numerator slope)
Per-theme, scale-free:
```
β_spec(c,t) = OLS slope of  ln(1 + N_spec(c, t−k))  on  k,   k = 0 … L−1     (L ⚙)
```
Optionally smooth `N_spec` with EWMA (half-life ⚙) before the fit. `β_spec > 0` ⇔ specialist attention accelerating.

### 1.4 Mainstream penetration (the S-curve position)
```
p_main(c,t) = N_main(c,t) / ( N_spec(c,t) + N_main(c,t) )
```
`p_main` is the single quantity tracking diffusion-curve position: **low = nascent; rising = diffusing; high = saturated (late-stage sell signal).** It drives Regime-B `phase_on_curve` (§1.7).

The literal `diffusion_ratio` of v0.1 is `N_spec/N_main`; `p_main` is its bounded monotone transform and is used operationally because it is in [0,1] and behaves at the N_main→0 boundary.

### 1.5 ThemeNascency gate
```
ThemeNascency(c,t) = 1   iff   β_spec(c,t) ≥ β_min  AND  p_main(c,t) ≤ p_max     (β_min, p_max ⚙)
```
A name passes the gate iff it belongs to ≥1 theme with `ThemeNascency = 1`.

### 1.6 Theme survival filter (Stage 2 — mandatory)
A candidate theme advances to constituent expansion only if **both**:
- **Persistence:** `β_spec ≥ β_min` sustained over ≥ `W_survive` consecutive weeks (⚙) — kills one-week spikes.
- **Leakage:** `N_bridge` strictly rising over the same window (theme is escaping insularity, not a permanently niche cul-de-sac — the TScan structural trap, detected at theme level).
Themes failing either are dropped before any constituent/Claude cost is incurred.

### 1.7 phase_on_curve
```
Regime B:  base        if p_main ≤ p0  and β_spec > 0
           early-markup if p0 < p_main ≤ p1
           late-markup  if p1 < p_main ≤ p2
           pre-resolution(saturated) if p_main > p2          (p0<p1<p2 ⚙)

Regime A:  base/early/late/pre-resolution by time-to-catalyst bands  T0>T1>T2 (months, ⚙)
```
**Only `base` and `early-markup` are actionable.** Field emitted per name.

---

## 2. Module A — Mispricing gate

### 2.1 Multiple auto-selection (per name, PIT)
```
if EBITDA_ttm > 0:                 multiple = EV / EBITDA
elif revenue_ttm > 0:              multiple = EV / Sales     (software: EV / gross_profit)
else:                              multiple = EV / cash      (also track P / tangible_book)
```

### 2.2 Value mode — fire ≥ 2 of 4 floors; record operative floor
| Floor | Definition | Fires |
|---|---|---|
| Drawdown | `DD = 1 − P_t / max(P over trailing D_hi days)` ; `D_hi` ∈ [252,756] ⚙ | `DD ≥ 0.50` ⚙ |
| Own-history pctile | percentile of current `multiple` vs own trailing `D_hist` days (⚙ 756–1260) | `≤ q_own` (⚙, ~10th) |
| Sector-relative pctile | percentile of `multiple` vs PIT peer set (GICS sub-industry ∪ theme cohort) | `≤ q_sect` (⚙, ~25th) |
| Hard floor | `EV/cash < 1.0` (neg/near-zero EV); non-bio: `P/NCAV < 1` or `P/tangible_book < 1` | as written |

### 2.3 Re-rating-headroom mode — **all three** required (earliness triple-lock)
1. **Multiple in lower half of own forward range:** `fwd_multiple_t ≤ median(fwd_multiple over own trailing D_fwd)` (PIT forward estimates; ⚙ window).
2. **Realized attention still low:** `level(att) ≤ a_lo` (⚙) — uses the *level* component of B3 (§3.3), cross-sectional bottom tertile.
3. **Theme pre-mainstream:** `p_main(theme) ≤ p_max` (same as §1.5).
If any lock unmeasurable PIT → **drop the mode for that name** (do not impute).

```
Mispricing_gate = value_mode  OR  rerating_mode
```

---

## 3. Module B — Hype factors (each → [0,1])

### 3.1 B1 — Legibility
Components:
- `one_sentence` — **Claude-scored** 0–1: can a generalist state the bull thesis in one sentence? (irreducible-judgment LLM call, per surviving name only).
- `keyword_fame` — does the theme keyword have a standing Wikipedia article and non-trivial Google-Trends baseline at `t`? (scaled 0–1).
- `designation_imprimatur` — FDA **BTD** present (+⚙ boost) or **Fast Track** present (+ smaller ⚙ boost); generalizes to any authority stamp.
- `analogy_present` — "the next ___" framing detected in corpus (0/1, ⚙ weight).
```
B1 = squash( w11·one_sentence + w12·keyword_fame + w13·designation + w14·analogy )   (w1·, ⚙)
```
*Anchor check:* KRAS → high; HA-2 minor-histo post-HCT maintenance → ~0.1.

### 3.2 B2 — ThematicHeat (category re-rating)
Inputs (each normalized cross-sectionally, then weighted ⚙):
- `peer_momentum` — median trailing 3–6mo return of theme cohort **excluding the name**, z-scored vs market.
- `etf_signal` — thematic-ETF price momentum + net flows, if an ETF exists (its *existence* also down-weights nascency — see Protocol).
- `in_theme_MA` — count of in-theme M&A/licensing **at premiums**, trailing 6–12mo.
- `keyword_slope` — reuse `β_spec` of the theme (news + EDGAR full-text emergence).
- `vc_funding` — $ into theme trailing 4Q (Form D + announced rounds).
- `designation_cluster` — count of BTD/Fast Track in same target/modality class, trailing 12mo.
```
B2 = squash( Σ w2i · norm(input_i) )    (w2·, ⚙)
```
*Anchor check:* cancer-vaccine/KRAS cascade → high; in-vivo TCR-T "strategically isolated" → ~0.1.

### 3.3 B3 — AttentionAccel (slope up, level low)
Composite realized-attention series `att(t)` = weighted blend (⚙) of: Google Trends, Reddit/StockTwits/X cashtag velocity, news-count, options volume vs trailing, unusual share volume vs ADV.
```
accel = norm( OLS slope of att over trailing La weeks )        (La ⚙)
level = cross-sectional percentile of att(t)                    # also feeds §2.3 lock #2
B3    = sigmoid(accel) · (1 − level)                            # high iff accelerating AND still low
```
**Penalizes already-peaked names by construction.** `accel` high with `level` high → B3 suppressed.

### 3.4 B4 — Convexity = max(microstructure, re-rating)
**Microstructure (Regime A):**
```
free_float_$ = price · FD_float            # FD_float INCLUDES pre-funded warrants
micro = squash( w41·(−ln free_float_$) + w42·SI_pct_float + w43·days_to_cover + w44·(−ln ADV_$) )
```
**Re-rating (Regime B):**
```
rerate = norm( (theme_implied_multiple − current_multiple)/current_multiple · g · runway )
```
`theme_implied_multiple` = median multiple of mature in-theme comps (or analyst-TAM-implied); `g` = revenue growth; `runway` = narrative-duration estimate (yrs).
```
B4 = max(micro, rerate)
```
*Discipline:* a specialist-fund price floor (Lynx1-type) is **excluded from B4** and recorded only in `downside_floor$`.

### 3.5 B5 — NarrativeRealization = max(discrete, continuous)
**Discrete (Regime A):**
```
discrete = exists_event · time_fit(Δt) · event_legibility
time_fit(Δt) = bump peaking in the 6–24-month window (Δt = months to event), → 0 outside [⚙ T_lo, T_hi]
```
`exists_event`: dated readout / PDUFA / **BTD or Fast Track decision date** / contract award / index inclusion. Date as a **distribution** from first-principles estimation (enrollment pace + follow-up + LPLV→topline 8–14wk, conference-deadline constrained). `event_legibility`: binary-ness / generalist-understandability (lottery convexity scores higher).

**Continuous (Regime B):**
```
continuous = norm( w51·KPI_beat_streak + w52·guidance_raise_freq + w53·analyst_TAM_raises + w54·cohort_sympathy )
```
```
B5 = max(discrete, continuous)
```
*Anchor check:* SOFI exercises **both** channels (SPAC/bank-charter discrete + member-growth cadence) — the `max()` must see both live.

### 3.6 HYPE_score
```
HYPE_score = B1 · B2 · B3 · B4 · B5            ∈ [0,1]
```
Multiplicative by design (framework §2.2): any factor ≈ 0 ⇒ score ≈ 0. **No additive fallback.** The TScan anchor (B1≈B2≈B3≈0 despite passing the value gate, having a catalyst, and a floor) is the regression test for this property.

---

## 4. Combiner / gating

```
QUALIFY ⇔ ThemeNascency_gate ∧ Mispricing_gate ∧ (HYPE_score ≥ θ)        (θ ⚙)
RANK    ⇔ HYPE_score
Actionable ⇔ QUALIFY ∧ phase_on_curve ∈ {base, early-markup}
```
Weekly output row (schema from v0.1 §9), plus mandatory `falsification_field` populated from the weakest live factor (e.g., "B3 level rising — entry window closing"; "designation cluster not forming"; "p_main crossed p1 — diffusing, late").

---

## 5. Parameter register (all ⚙ — fit/selected on labeled panel)

| Symbol | Meaning | Fit method |
|---|---|---|
| embed model, `τ_member`, `τ_novel`, `iter_max`, `min_cluster_size` | embedding/clustering | grid + silhouette/label agreement |
| `W_spec`, `W_main`, `L`, EWMA half-life | diffusion windows | maximize PIT predictive AUC |
| `β_min`, `p_max`, `W_survive` | nascency gate + survival | ROC on theme-level labels |
| `p0,p1,p2`, `T0,T1,T2` | phase bands | align to realized run timing on positives |
| `D_hi,D_hist,D_fwd,q_own,q_sect` | value/forward floors | panel value-mode calibration |
| `a_lo` | attention-low lock | bottom-tertile on panel |
| `w1·…w5·`, sigmoid/squash params | factor weights | regularized logistic on panel label |
| `θ` | qualify threshold | precision/recall operating point |
| `T_lo=6, T_hi=24` (mo) | catalyst sweet spot | sensitivity-checked, not assumed |

**No constant in this document is set.** Setting any of them requires the panel.

---

## 6. Source → feature dependency map (PIT registry roles)

| Feature | Primary sources |
|---|---|
| `diffusion_ratio` / β_spec / p_main | specialist (arXiv/bioRxiv/HN/patents/awards/FDA-designations) ÷ GDELT + Wikipedia pageviews |
| B1 Legibility | Wikipedia/Trends (keyword_fame), FDA designations (8-K/GlobeNewswire), Claude (one_sentence) |
| B2 ThematicHeat | peer prices, thematic-ETF, M&A trackers, Form D/announced rounds, EDGAR FTS, designation cluster |
| B3 AttentionAccel | Trends, StockTwits/Reddit/X, news-count, options vol, share volume |
| B4 Convexity | EDGAR (FD float incl PFW), FINRA short interest, ADV; comps + estimates (re-rating) |
| B5 NarrativeRealization | ClinicalTrials.gov/PDUFA/FDA designations/contract-award/index calendars (discrete); transcripts/KPIs (continuous) |
