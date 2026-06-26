# Cheap-with-Hype-Potential Screener — Framework Specification

**Working product name:** Hype Parser (cheap-with-hype-potential weekly screener)
**Module dir:** `5_Hype_parser/`
**Version:** 0.1 (framework only — features & protocol deferred by design)
**Status:** Draft for review. Conceptual model, pipeline architecture, source registry, calibration
cases locked; feature formulas, labeling protocol, thresholds, and backtest mechanics intentionally
OUT of scope until the framework is locked.
**Last updated:** 2026-06-26

> Repo conventions are inherited (SQLite-only durable state; cost-gated, cache-first Claude via a
> single runner + ledger; `Outputs/`/`data/`/`_intermediate_outputs/` folder split; shared venv at
> `..\.venv\` with `PYTHONPATH=src`; spec + `decisions.md` updated at every step). See
> `spec/decisions.md` for the decision log and the cross-cutting rules.

---

## 1. Purpose

Identify, on a weekly cadence and across any sector, equities that are (a) priced below what a latent narrative implies and (b) positioned for an attention-driven re-rating ("hype"), entered **early on the diffusion curve** — before mainstream recognition, not after.

The program is a research / signal-generation tool. All trades are executed manually. It outputs a ranked watchlist with a falsification field per name, not buy orders.

---

## 2. Core model

### 2.1 Cheapness and hype-potential are orthogonal
Two independent modules plus a combiner. Cheapness gates; narrative + attention is the alpha. This is the central lesson of the two anchor cases: both Elicio and TScan were cheap with a catalyst; only one had hype.

### 2.2 Hype is multiplicative, not additive

```
HYPE_score = Legibility × ThematicHeat × AttentionAccel × Convexity × NarrativeRealization
```

If any factor ≈ 0, the product ≈ 0. An additive score would wrongly reward "cheap + catalyst + floor" (the TScan trap). The multiplicative form is what makes a cheap, catalyst-bearing, floor-protected name with an illegible story correctly score ~zero.

### 2.3 You buy potential energy minus kinetic energy
Target the gap between **latent** narrative (a legible, hot story exists) and **realized** attention (the crowd hasn't noticed yet). This is why the operative question is "would Elicio have qualified *two years ago*" — at the base of the curve, not mid-run.

### 2.4 Theme-first, not stock-first
Do not screen the whole market and ask "which has hype potential." Invert: detect nascent **themes** first; each theme defines its own constituent list; screen only the union. Themes span sectors (multi-sector coverage) but their constituents number in the hundreds (bounded universe). The theme radar simultaneously **defines the universe** and **times the entry** — one mechanism, two jobs.

### 2.5 Two hype regimes
- **Regime A — discrete catalyst** (biotech readouts, contract awards, court rulings, FDA designations): a dated, ~binary event resolves a pre-built narrative. Convexity is event-driven; risk is sell-the-news.
- **Regime B — continuous confirmation** (ROKU, NET, SNAP, SOFI): a secular-TAM story validated by a *cadence* of proof-points (KPI beats, guidance raises, analyst-day TAM expansions, cohort sympathy). No single event; **theme-diffusion stage is the timing instrument**.

The catalyst is therefore NOT a hard gate (v1 error — it excluded every Regime-B name). It is generalized into a factor via `max()` (see §5).

---

## 3. Pipeline architecture

```
Stage 1  Theme radar          → detect nascent sub-themes (diffusion_ratio: slope↑, level low)
Stage 2  Theme survival filter → drop themes that don't sustain / begin leaking to semi-mainstream
Stage 3  Constituent expansion → theme → company list (embeddings prefilter + Claude judgment)
Stage 4  Module A — Mispricing → value-mode OR re-rating-headroom-mode gate
Stage 5  Module B — Hype score → 5 multiplicative factors, dual-regime
Stage 6  Combine + rank        → gates AND, rank by HYPE_score, attach phase_on_curve
Stage 7  Weekly output         → ranked watchlist + falsification field per name
```

Granularity rule (load-bearing): the radar runs at **sub-theme** grain. "Oncology" is uselessly broad; "mKRAS off-the-shelf vaccines" is correct. "Streaming" is too broad; "CTV advertising" is correct. Wrong granularity → either everything is nascent or nothing is.

---

## 4. Module A — Mispricing (gate)

Reframed from "cheap" to **"un-bid relative to latent narrative"** so it covers both value names and early-S-curve growth names. Unifying test: *does the price embed expectations below what the latent narrative implies?* A name must pass one of two modes.

### 4.1 Value mode (literal discount)
Passes ≥2 of 4 floors; flag the operative one:
- **Drawdown** ≥ 50% below trailing 1–3yr high.
- **Own-history percentile**: relevant multiple (EV/S, EV/EBITDA, FCF yield, or EV/cash) in bottom decile of own 3–5yr range.
- **Sector-relative percentile**: bottom quartile vs. point-in-time peer set.
- **Hard floor**: negative/near-zero EV (EV/cash < ~1.0); or P/NCAV, P/tangible-book < 1 for non-bio.

The floor *sizes the stop*; it does **not** earn hype points.

### 4.2 Re-rating-headroom mode (early on S-curve)
For story stocks not cheap on any multiple (ROKU/NET at base). "Un-bid" = the theme multiple has not yet been awarded. **Earliness triple-lock** (all three required, all point-in-time, or drop the mode):
1. multiple in lower half of its own forward range, **and**
2. realized attention still low, **and**
3. theme still pre-mainstream (diffusion_ratio numerator rising, denominator low).

The triple-lock is what prevents this mode degenerating into "buy any expensive growth stock."

---

## 5. Module B — Hype score (alpha)

Five factors, multiplicative, each normalized to [0,1].

| Factor | Definition | Dual-regime note |
|---|---|---|
| **Legibility** | Can a generalist state the bull thesis in one sentence? Proxies: famous/searchable theme keyword; analogy availability ("the next ___"); generalist (not just specialist) coverage; FDA designation imprimatur; thematic-listicle/ETF appearance. | regime-independent |
| **ThematicHeat** | Is the *category* re-rating? Proxies: peer-group + sector-ETF momentum & flows, in-theme M&A/licensing at premiums, theme-keyword slope (news + EDGAR full-text), VC funding into theme, **designation clustering by target/modality**. | regime-independent |
| **AttentionAccel** | Second derivative of realized attention positive, **level still low**. Proxies: Google Trends slope, Reddit/StockTwits/X cashtag velocity, news-count slope, options volume vs. trailing, unusual share volume vs. ADV. Penalize already-peaked. | regime-independent |
| **Convexity** | `max(microstructure, re-rating)`. *Microstructure* (Regime A): small FD float incl. pre-funded warrants, short interest % float, days-to-cover, illiquidity. *Re-rating* (Regime B): gap between current and theme-implied multiple × growth × narrative-duration runway. | dual |
| **NarrativeRealization** | `max(discrete, continuous)`. *Discrete* (Regime A): dated ~binary legible event (readout, PDUFA, **BTD/Fast Track**, contract award, index inclusion). *Continuous* (Regime B): knowable cadence of confirming proof-points keeping the narrative alive. | dual |

Notes:
- **Float is computed fully-diluted** (incl. pre-funded warrants); naive float screens get this wrong.
- A specialist-fund floor (Lynx1-type) is logged under **downside sizing, NOT Convexity**. A floor without a story is a longer leash on dead money.

---

## 6. Source registry (theme radar + signal inputs)

### 6.1 Selection principle
**Leading-indicator value is inversely correlated with packaging/cost.** Nascent signal lives in upstream, unpackaged, primary sources. Packaged analyst houses (Gartner/Forrester/CB Insights/PitchBook/Grand View) are the *mainstream denominator*, not the specialist numerator — paying premium for a lagging indicator that fights the thesis. Buy almost nothing.

A credible **juried award / FDA designation = donated specialist curation**: an expert panel has already performed the "is this a real emerging thing" judgment. The **citation / rationale / indication text is the label** and routes to the embedding/clustering step.

### 6.2 Registry schema (fields each source must carry)
`source_id · edge_type · access_method · diffusion_position {leading | bridge | denominator} · signal_type {threshold_event | volume} · jury_credibility · cadence · rate_limits · scrapeability_verified · add_date`

Threshold events (a new category, a WG charter, a first designation in a class) outrank volume signals: a jury recognizing a *boundary crossing* is purer than counting honorees. **Category-diffing requires storing each year's taxonomy point-in-time** — start archiving now; last year's categories can't be reconstructed later.

### 6.3 Tier 0 — free, official, API/bulk (backbone)

| Edge | Sources |
|---|---|
| Science | arXiv, bioRxiv/medRxiv, Europe PMC, ClinicalTrials.gov v2, openFDA, FDA guidance RSS |
| IP | USPTO PatentsView, EPO OPS (free tier), Google Patents public dataset, USPTO trademarks (TSDR/TESS) |
| Capital (leading) | NIH RePORTER, SBIR.gov, NSF awards, **SEC Form D** (private placements, underused), EDGAR full-text search |
| Builder | Hacker News (Algolia API), GitHub star velocity, Stack Overflow tags |
| Early-market | thematic-ETF registrations (N-1A on EDGAR — a filing *is* nascency), ETF holdings |
| **Denominator** | **GDELT** (news volume/themes/tone), **Wikipedia pageviews API** |

### 6.4 FDA designations (NEW — biotech validation layer)

| Designation | Signal role | Stage / bar | Access reality |
|---|---|---|---|
| **Breakthrough Therapy (BTD)** | strong validation; FDA-as-jury; lifts Legibility + discrete NarrativeRealization; **cluster-by-target = ThematicHeat** | mid-stage (needs preliminary *clinical* evidence) | **No clean real-time public feed.** Real-time signal = company 8-K / GlobeNewswire PR (already in stack); reconcile against FDA periodic reports. |
| **Fast Track** | weaker validation; earlier-stage | low bar / **high volume** → weight well below BTD | company PR / 8-K driven; FDA grants are numerous |
| *Orphan Drug (adjacent)* | not requested; noted because it has the one **cleanly searchable FDA database** | early | FDA Orphan Drug Designations DB |

FDA designations are `diffusion_position = bridge` (specialist origin, often triggers some mainstream pickup), `signal_type = threshold_event` at the cluster level.

### 6.5 Awards universe (juried, anti-vanity)
Exclude pay-to-enter programs. Prefer awards that name *technologies / early-stage companies / prototypes* (numerator) over *Company-of-the-Year / lifetime* (denominator). **Diffing an award's category taxonomy year-over-year is a purer nascency detector than the honoree list.**

**Tier A — theme-level / very-early:** MIT Tech Review 10 Breakthrough Technologies; R&D 100; Fierce 15 (biotech); RSA Innovation Sandbox; Global Cleantech 100; Y Combinator batch + Requests for Startups; Automotive News PACE (supplier picks-and-shovels).
**Tier B — strong, company/product-level:** CES Innovation Awards (watch category adds); TIME Best Inventions; PopSci Best of What's New; Fast Company MIC + World Changing Ideas; MIT TR35; TechCrunch Battlefield; Finovate Best-of-Show; iF/Red Dot (incl. Design Concept)/IDEA/Core77; Falling Walls; IGF.
**Tier C — lagging, denominator/ignition only:** Nobel / Breakthrough / Lasker / Turing / Collier / QE Prize. (A Nobel can *trigger* mainstream diffusion — e.g., CRISPR 2020 cohort move — but does not *find* nascency.)

*Cadence caveat:* awards are annual → sparse, laggy. Slow confirmation layer, never the weekly-timing layer.

### 6.6 Other automatable sources

**Threshold events (highest signal):** standards-body WG formation (IETF charters, IEEE PAR, W3C groups); new conference tracks/workshops (diff agendas YoY); first Wikipedia article + subreddit *creation* event; government priority-naming (DARPA/IARPA BAAs, SAM.gov, DoD SBIR topics, EU CORDIS/Horizon).
**Capital-commitment:** job-posting velocity via ATS endpoints (Greenhouse/Lever JSON); trademark filings; **13F new-position clustering** (wire in existing pipeline output).
**Mid-diffusion bridges (calibrate ratio midpoint):** earnings-call transcript keyword emergence; Product Hunt; lobbying / regulatory-comment dockets (regulations.gov).
**VC RSS/Substack (theme-naming only, talk-their-book — cross-check vs Form D / announced rounds):** a16z, USV/AVC, Bessemer, First Round Review, Lux, NfX, Founders Fund.

### 6.7 Discipline
Registry is the moat *and* the bias. **Freeze and version the source list before any backtest window; reconstruct diffusion_ratio point-in-time; timestamp every add_date.** Scrapeability is *claimed, not verified* until checked per-source at lock time.

---

## 7. diffusion_ratio (core instrument)

```
diffusion_ratio(theme, t) = specialist_mention_volume(theme, t) / mainstream_mention_volume(theme, t)
```
Nascency gate: **numerator slope positive AND denominator level low.** Mainstream saturation = late-stage sell signal (diffusion analogue of biotech sell-the-news).

Synonymy ("CTV" = "connected TV" = "OTT ads") is solved with **embeddings, not generation**: embed every document, cluster by cosine similarity to a sub-theme centroid, count membership over time. (Full definition — window, smoothing, centroid construction, thresholds — deferred to the features artifact.)

---

## 8. Analysis architecture (build economics)

Push the LLM to the narrow end of the funnel.

**No LLM (wide top):** ingestion (plain Python/feedparser/official clients); **the diffusion engine itself** (counting + embeddings, run **locally** via sentence-transformers — effectively free after ingestion). *The instrument that defines the universe needs no Claude API.*

**Claude API — irreducible judgment only (narrow bottom, bounded cost, Batch + prompt caching, ~$5–15 envelope):** theme labeling (per emergent cluster, dozens not millions); constituent extraction (10-K/S-1 exposure judgment, embeddings-prefiltered then Batch); Legibility scoring (per surviving name).

MVP sequencing: keyword + embeddings for the entire diffusion engine with **zero Claude**, then add Claude for labeling/legibility once the engine works. De-risks the build.

---

## 9. Gating, scoring, output

```
QUALIFY ⇔ ThemeNascency_gate  AND  Mispricing_gate  AND  HYPE_score ≥ θ
RANK    ⇔ HYPE_score
```
- `ThemeNascency_gate`: name ∈ ≥1 theme with diffusion_ratio slope↑, mainstream level low, survived the survival filter.
- `Mispricing_gate`: passes Value-mode OR Re-rating-headroom-mode.
- `phase_on_curve` ∈ {base, early-markup, late-markup, pre-resolution}; only **base / early-markup** are actionable. Regime B reads phase off specialist→mainstream diffusion position; Regime A off catalyst proximity.

**Weekly output row:**
`ticker | theme(s) + diffusion_position | mispricing_mode + operative_floor | HYPE_score + factor breakdown (B1–B5) | regime {A|B} | NarrativeRealization (date distribution OR confirmation cadence) | phase_on_curve | one_sentence_narrative | downside_floor($) | falsification_field`

`falsification_field` is mandatory: what would make this NOT hype (theme cooling, attention already peaked, float not actually small post-PFW, designation cluster failing to form).

### Hype S-curve & timing
1. **Base/accumulation** — cheap/un-bid, story nascent, attention low. ← *qualify here.*
2. **Markup** — theme heats, attention compounds, multiple expands ahead of fundamentals (Elicio's ~2yr run).
3. **Resolution** — Regime A binary event = usually sell-the-news unwind (Elicio crushed *on* data). The program finds **entries, not holds**; exit discipline before resolution is non-negotiable.

Hype runway: target NarrativeRealization **~6–24 months out** (Regime A) or an intact confirmation cadence (Regime B). Too-near catalyst = IV rich, crowd already in. No catalyst + no cadence = drifts cheap indefinitely (TScan's structural problem).

---

## 10. Calibration cases

The two biotech anchors set the **structure** (multiplicative form); the five cross-sector cases set **regime coverage**. None of these sets parameters — that requires a labeled panel (§11).

| Name (era) | Theme (sub-grain) | Regime | Mispricing | Verdict | Mechanism |
|---|---|---|---|---|---|
| **Elicio (ELTX)** ~mid-2024 | mKRAS off-the-shelf vaccine | A | value (tiny FD float) | **QUALIFY** | Legibility hi (KRAS), ThematicHeat hi (cancer-vaccine/BioNTech cascade), AttentionAccel inflecting from low, Convexity hi (tiny float + PFW), NarrativeRealization hi (Phase 2 binary readout, multi-quarter runway). Product lights up at base. |
| **TScan (TCRX)** ~Dec 2025 | in-vivo TCR-T | A | value — **passes** (below net cash) | **DISQUALIFY** | Legibility ≈0.1 (HA-2 minor-histo post-HCT maintenance), ThematicHeat ≈0.1 (in-vivo TCR-T strategically isolated, no cascade), AttentionAccel ≈0 (didn't move on good data). Multiplicative product ≈0 *despite* strongest cheap signal + catalyst + Lynx1 floor. An additive scorer fails here; the multiplicative form is validated. |
| **ROKU** 2019 | CTV advertising | B | re-rating headroom | QUALIFY | specialist tech press hot / mainstream low; continuous KPI cadence; huge EV/S headroom. Demonstrates Regime B + re-rating Convexity. |
| **NET** 2020 | edge compute / zero-trust | B | re-rating headroom | QUALIFY | developer-mindshare nascent; analyst-day TAM raises; pre-expansion multiple. Earliness triple-lock catches it at base, not 2021 peak. |
| **SNAP** 2020 | digital-ad recovery + AR | B | value→re-rating | QUALIFY | washed-out base + earnings-beat confirmation cadence reviving narrative. |
| **SOFI** 2021 | fintech / neobank | A+B | value | QUALIFY | SPAC + bank-charter (discrete) *and* member-growth cadence (continuous) — exercises the `max()` on both generalized factors. |
| **CRISPR cohort** ~2020 (CRSP/NTLA/BEAM) | gene editing | A+B | re-rating | QUALIFY (basket) | Nobel + trial registrations (discrete) on a *cohort* moving together. Surfaces **basket-level themes** — hype accrues to the cohort; theme-first constituent expansion handles natively. |

**Two structural features the cases force into the spec:** (1) basket/cohort themes (CRISPR row); (2) the `max()` generalization of both Catalyst→NarrativeRealization and Combustibility→Convexity (SOFI row needs both channels live).

---

## 11. Failure modes & discipline

- **n=2 → n=7 is still overfitting.** Hand-picked cases set structure, never weights/θ. Before trusting parameters, build a **labeled point-in-time panel** (≥50–100 names: ran-on-hype vs stayed-cheap) and validate out-of-sample. *(Next-phase artifact.)*
- **Hindsight in source/theme selection.** "These sources would have caught CTV/KRAS" is n=2 overfitting moved to the theme level. Freeze the registry *before* the backtest window; reconstruct diffusion_ratio point-in-time.
- **Look-ahead in ThematicHeat.** "KRAS was hot" is obvious only in hindsight — score B2 point-in-time or it's cheating. Most likely place the model secretly encodes the answer.
- **Re-rating-headroom mode is the dangerous one.** One loose parameter from "buy expensive growth." If all three earliness locks can't be measured point-in-time, **drop the mode** rather than fudge.
- **Theme false-positive rate is brutal.** Most nascent specialist themes die pre-mainstream. The survival filter (Stage 2) is mandatory or you screen 200 names/week off dead themes.
- **Adverse selection on attention.** AttentionAccel + tiny float + binary catalyst is also the pump-and-dump / informed-leakage signature. B3 spikes with **no B1/B2 support = red flag, not buy.**
- **Catalyst = sell-the-news.** Entries not holds; exit before resolution. Elicio's money was made in Phase 1–2, lost in Phase 3.
- **VC sources talk their book; awards/designations lag.** Cross-check VC theme-naming against Form D / announced rounds; never let the annual-cadence layers drive weekly timing.
- **Whole-project falsification test (run first):** on the labeled panel, do legible-hot-theme cheap names outperform illegible-cheap names *after* controlling for float and catalyst proximity? If not, the premise (narrative drives the re-rate) is false and the program collapses to a generic small-cap value+momentum screen with no edge.

---

## 12. Locked vs. open

**Locked in v0.1:** orthogonality; multiplicative hype; potential−kinetic framing; theme-first dynamic universe; dual-regime via `max()`; un-bid-ness replacing cheapness; source-registry taxonomy + FDA layer; diffusion_ratio as core instrument; embeddings-for-engine / Claude-for-judgment split; calibration verdicts.

**Open (next artifacts, in order):**
1. Frozen source-registry schema instance + per-source scrapeability verification (Tier-A awards + FDA layer first).
2. `diffusion_ratio` full definition (window, smoothing, centroid/synonym construction, slope/level thresholds).
3. Labeled point-in-time panel + labeling protocol.
4. Feature formulas + normalization per B1–B5.
5. Backtest mechanics + the §11 whole-project falsification test.
