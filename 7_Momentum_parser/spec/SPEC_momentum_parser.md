# 7_Momentum_parser — Specification

*Daily, LLM-centric pipeline that anticipates one-week moves in retail-driven, hype-prone stocks.*
*Analyst-authored target. Status: spec **v0.5** (2026-07-01) — after the first live run still read as
backward-looking, the Stage-3 rubric is reoriented from *evaluating known catalysts* to **GENERATING
forward drivers** (Decision M, §6); conviction is gated on forward-ness. Builds on **v0.4** (2026-06-30):
the **top-down market-perturbation layer** (Decision L) + catalyst redefinition + variant-perception rubric,
after the first real run was judged a low-value recap (`decisions.md` D-6). M1–M10 = bottom-up base (built);
M11–M18 = v0.4 (built); M19+ = v0.5.*

---

## 1. Motivation

**Real-world case.** On Friday 26 June 2026 (US close) and Monday 29 June 2026 there were surges of
**volume** on biotech stocks with **sharp price increases**, preceded by a build-up of *leading*
signals — rising attention, search interest, and price/volume micro-structure — before the price fully
moved.

**Assumption.** An **LLM is best suited to analyse a large amount of multidimensional data parsed daily
from the web** to detect the signal configurations that *anticipate* such weekly moves, and to turn that
analysis into a **probabilistic** assessment for specific stocks.

**Objective.**
1. An **automated pipeline that runs daily**.
2. It **parses the web for multidimensional information** and uses a **Claude API call to analyse** it.
3. It **computes the probability of a stock move over one week** (5 trading days):
   **P(close_{t+5d}  >  or  <  today's close)** plus an **expected-return estimate**. **Trading is
   long-only** — the actionable output is the up-move probability.

---

## 2. Locked decisions (with the operator, 2026-06-30)

| # | Decision | Choice |
|---|---|---|
| **A** | Probability computation | **Hybrid** — Claude's `p_claude` blended with a calibrated code-side `p_model`; disagreement flagged |
| **B** | Web sources | mainstream **media** + **web-search statistics** (à la 5_Hype_parser) + **forward corporate catalysts** + **OHLCV technical composite**. **NO social media** (removed — too costly/noisy) |
| **C** | Universe | **Claude-discovery-first** — a weekly Claude call proposes the basket from current retail chatter + search-interest trends, then a code **liquidity + volatility filter** prunes it |
| **D** | Budget / dispatch | **Tiered + Batch API** under a **$5/day** ceiling: Haiku triage → Sonnet rubric (+web_search, Batch −50%) → Opus on the contested band (~30–40 names/day) |
| **E** | Validation | **Hybrid** — historical backtest of the code model+blend on point-in-time archives, **plus** a forward live prediction-vs-realized ledger from day one |
| **F** | Move target (label) | **Volatility-normalized + dead-band** — `up` if fwd-5d return > +0.5×σ_week, `down` if < −0.5×σ_week, else `flat`; long-only acts on `up` |
| **L** | **Top-down layer** (v0.4) | A **weighted, forward-looking, feedback-looped taxonomy of market-perturbing signals** (macro / geopolitical / cross-asset / rotation) conditions the whole basket and each name — *in addition to* the bottom-up four dimensions. Signals are **anticipated before they materialize** (never recapped after), each carries a **learnable weight**, and each name a **learnable loading (β)**. §4b. |
| **M** | **Forward-driver generation** (v0.5) | The rubric's PRIMARY job is to **generate 1–3 unpriced forward drivers** (a driver need not be scheduled — emergent narrative, flow/squeeze unwind, sympathy move, technical break, second-order macro), each with probability + expected impact + **novelty**. **Conviction is gated by `forward_novelty`**, so a thesis built on past/scheduled milestones or a run-up recap earns ≈0. Cataloging known catalysts and judging "priced-in?" is explicitly NOT the task. §6. |

### 2.1 Defaults set by the author (override any of these)
- **G — Catalysts are FORWARD-only, and are EITHER a forward fact OR a quantified hypothesis (v0.4).**
  In scope, both anticipatory: **(1) a forward fact** — the *anticipation* of a scheduled event is the
  signal: **pre-event accumulation/drift** into a known date (PDUFA / readout / earnings), or an
  **expected beat/miss** derived from estimate-revision trends + whisper-vs-consensus. **(2) a
  falsifiable-quantified hypothesis** — a derived prediction (expected drift `+Y% ± c` from historical
  analogs) that the ledger settles vs. realized and **feeds back** to learn. The *date/expectation/
  hypothesis* is anticipatory; the *result/announcement itself* stays OUT (a-posteriori; §2.2). A catalyst
  that has already fired is **attribution-only** (it trains weights, it never re-enters as a fresh predictor).
- **H — Blend = calibrate-then-combine.** Each leg is calibrated (isotonic/Platt on the backtest) and
  then combined `p_final = w·p_claude + (1−w)·p_model` (`blend.claude_weight`, default 0.6). Disagreement
  `|p_claude−p_model| > blend.disagree_threshold` → confidence penalty + `review` flag. `w` is a config
  default until the backtest earns it (§9).
- **I — Single-sample, structured output.** The chosen models (Opus 4.8 / Sonnet 4.6 / Haiku 4.5) **do
  not accept `temperature`/`top_p`** — determinism comes from adaptive thinking + a strict JSON schema,
  not a sampling knob. One sample per name (multi-sample averaging is out of budget at $5/day).
- **J — Confidence formula** (§8): `confidence = claude_conviction × data_coverage × (1 − disagreement)
  × history_depth_factor`, clamped [0,1].
- **K — Decision rule (long-only):** rank by `p_up`, surface names with `p_up ≥ export.min_p_up` AND
  `confidence ≥ export.min_confidence`; secondary sort by `expected_return`. No short side.

### 2.2 Predictive-only source principle (operator-stated)
Every source must support **anticipation**. **Past corporate announcements / press releases / earnings
results are OUT** — the stock reacts to them *a posteriori*, and they would leak look-ahead into the
backtest. In scope: leading attention, search-interest build-up, price/volume micro-structure, and
**forward** catalyst calendars (a *scheduled* readout next week is anticipatory; the *result* is not).

### 2.3 Anticipate the ARRIVAL of a signal, never recense it (v0.4, operator-stated)
The first real run failed because it **recapped already-public, already-priced** information (momentum +
dated catalysts every Benzinga reader has). A forward *date* is not a forecast. So, for **every** signal —
top-down (§4b) or bottom-up (§4) — the system models the **arrival and the surprise** of the signal
*before* it materializes, and the prediction is made on the **anticipated** state. The moment a signal
prints and is priced, it leaves the forward predictor set and is used **only** for ledger attribution and
weight-learning (§4b). Value comes from a **differentiated view** (where consensus is mispriced), not from
re-listing public facts (enforced in the rubric, §6).

---

## 3. Stock universe (Decision C + the liquidity/volatility gate)

- **Weekly Claude discovery (`stage0_discovery`):** a Claude call (web_search, allow-listed) proposes a
  candidate basket of stocks that are **retail-heavy** (large retail ownership → volatility + the chance
  to *outsmart retail*), **hype-prone**, and plausibly **liquid**. Output is a candidate ticker list +
  rationale, persisted weekly.
- **Daily code gate (`stage0_gate`, free):** prune the basket to names that pass
  - **Liquidity:** average daily dollar volume ≥ `liquidity.min_session_usd` × `liquidity.safety_mult`
    (default $100k × 20 = $2M ADV, so a $100k clip is ≲5% of a session);
  - **Volatility floor:** weekly σ ≥ `universe.min_weekly_vol` (the thesis *needs* volatility);
  - **Price floor:** last close ≥ `universe.min_price` (drop sub-$1 noise).
  Below a floor → **flag + exclude from scoring** (reversible), never silently dropped; missing data →
  keep + flag.
- The basket is **machine-owned** (Decision C) but the discovery call is **cost-gated** like any Claude
  call (§9) and runs **weekly**, not daily (release-calendar style, à la 5_Hype_parser).

---

## 4. Multidimensional data harvest (Decision B, predictive-only — NO social)

Per stock per day, assemble an **evidence bundle** from four dimensions. Quantifiable features are
harvested in code; rich/contextual reading is delegated to Claude's `web_search` at the scoring stage
(same split as 6_Biotech).

| Dimension | Leading signals (quantified) | Source |
|---|---|---|
| **Technical** | bullishness **composite** ∈ [-1,1] (SMA cross, ROC, RSI, MACD, **volume surge**, breakout) + **weekly σ** | OHLCV (`signals.py`, built) |
| **Mainstream media** | article/headline **volume** + **tone**, rising-attention slope | news/web search (Claude `web_search`, allow-listed); historical = news archive (GDELT-style) for the backtest |
| **Web-search statistics** | **search-interest surge** vs baseline (the 5_Hype_parser idea) | search-interest feed / 5_Hype reuse; historical = Trends-style archive |
| **Forward catalysts** | **days-to-next-scheduled-catalyst**, catalyst type (PDUFA / readout / earnings date) | catalyst calendar (Decision G; *scheduled* only) |

> **Security (NEW surface vs 5_Hype's zero-LLM discovery).** Scraped media text **enters a Claude
> prompt** → **prompt-injection surface**. All untrusted fetched content MUST be **delimited and labelled
> as data, never instructions**; `web_search` runs with an **allow-list** (`claude.allowed_domains`);
> reads are **64MiB-capped**; secrets from `.env`, never logged. (Project memory
> `feedback_security_by_default` + `feedback_reuse_claude_dispatch_patterns`.)

---

## 4b. Top-down market-perturbation layer (Decision L — v0.4, the build target)

A 1-week move in a **high-beta retail basket** is driven as much by **top-down** forces — macro regime,
scheduled data surprises, geopolitics, factor rotation — as by any one name's news. The bottom-up four
dimensions (§4) are blind to all of it. v0.4 adds a **shared, daily, top-down layer** that conditions the
**basket** (a regime prior) and **each name** (per-ticker loadings).

### 4b.1 The signal model
The system maintains a **taxonomy of market-perturbing signals** (`config/signals_taxonomy.yaml`). Each
signal `s` has:
- **scope** — `market` (moves everything), `sector`/`factor` (moves a cohort), or `ticker` (idiosyncratic);
- **schedule type** — `dated` (known date: FOMC, CPI, earnings), `continuous` (a level/trend: VIX, spreads),
  or `probabilistic` (unscheduled, has arrival odds: peace deal, tariff);
- **prior weight** `w_s` — analyst-assigned impact magnitude on P(up), **learnable** (§4b.3);
- a **forward anticipation source** — how we know it is *coming* and estimate its *surprise* before it prints.

Each name `t` carries a **loading vector** `β_{t,s}` — its sensitivity to each signal (rate-beta, oil-beta,
AI-crowding-beta, …). Initialized from sector/factor classification; **learned** from the ledger (§4b.3).

> **Forward-only (Decision §2.3).** A signal enters the predictor **only while anticipated** — we forecast
> its arrival + surprise, we do not recap it after it fires. Once a dated signal occurs and is priced, it is
> **attribution-only**.

### 4b.2 The taxonomy (first draft — weights are PRIORS the loop will normalize + learn)

| Class | Signal | Scope | Schedule | Forward anticipation source | `w_s` prior |
|---|---|---|---|---|---|
| **MONETARY** | FOMC decision / dot-plot / Fed speakers | market | dated | FOMC calendar + fed-funds-futures-implied move + surprise risk | 0.18 |
| MONETARY | CPI / PCE / PPI | market | dated | BLS/BEA release calendar + consensus vs whisper | 0.15 |
| MONETARY | NFP / jobless claims / JOLTS / ADP | market | dated | BLS calendar + consensus | 0.10 |
| MONETARY | Growth: GDP / ISM-PMI / retail sales / confidence | market | dated | release calendar + consensus | 0.06 |
| **GEOPOLITICAL** | War / peace talks | market | probabilistic | prediction-market odds + leading-news LLM scan | 0.08 |
| GEOPOLITICAL | Tariffs / trade policy | market/sector | probabilistic | scheduled hearings + news scan | 0.06 |
| GEOPOLITICAL | Shutdown / debt-ceiling | market | dated/probabilistic | legislative calendar | 0.04 |
| GEOPOLITICAL | Elections / major regulatory rulings | market/sector | dated | electoral + regulatory calendar | 0.04 |
| GEOPOLITICAL | OPEC / energy shock | sector | dated/continuous | OPEC schedule + oil term structure | 0.04 |
| **CROSS-ASSET** | Risk regime: VIX term structure, HY/IG credit spreads | market | continuous | term-structure / spread **shift** (anticipate, not level) | 0.12 |
| CROSS-ASSET | Rates/USD: 2s10s, 10Y, DXY | market | continuous | trend shift | 0.06 |
| CROSS-ASSET | Oil / gold / BTC (risk-appetite proxies) | market/sector | continuous | trend shift | 0.04 |
| **ROTATION** | AI-trade crowding & momentum (incl. unwind risk) | factor | continuous | crowding / breadth + momentum-unwind risk | 0.14 |
| ROTATION | Growth↔value, hi-beta↔low-vol | factor | continuous | factor-spread trend | 0.08 |
| ROTATION | Sector flows / relative strength | sector | continuous | RS-flow trend | 0.06 |
| **TICKER** | Forward catalyst (PDUFA/readout/earnings DATE) + pre-event accumulation | ticker | dated | catalyst calendar + drift-into-date (Decision G-1) | 0.20 |
| TICKER | Expected earnings beat/miss | ticker | dated | estimate-revision trend + whisper vs consensus (G-1) | 0.10 |
| TICKER | Index add/delete, lockup expiry | ticker | dated | index / lockup calendar | 0.05 |

`market`-scope signals shift the **basket regime prior**; `factor`/`sector` apply per-name via `β_{t,s}`;
`ticker` are the idiosyncratic catalysts (Decision G). All weights are illustrative starting priors.

### 4b.3 Weights + loadings feedback loop (the self-growing engine — generalizes the v2 roadmap)
1. **Anticipate** — each day, score every active (anticipated) signal's *expected surprise* and combine into
   a regime prior + per-name top-down contribution `Σ_s w_s · β_{t,s} · surprise_s`.
2. **Settle** — when a 5-day outcome lands, **attribute** the realized move into **market (regime)** +
   **factor/sector (β-weighted)** + **idiosyncratic** components.
3. **Learn** — update `w_s` and `β_{t,s}` toward what actually moved the name vs. the predicted contribution
   — **shrinkage-regularized to the priors** at small `n`, and **regime-conditional** (a weight can differ
   risk-on vs risk-off). M6 isotonic calibration is the seed; this extends it to per-signal weights + betas.

### 4b.4 Integration
- **`p_model` (§8)** gains a top-down term: `regime_prior + Σ_s w_s·β_{t,s}·surprise_s`, same vol-normalized
  target so it stays comparable.
- **The Claude rubric (§6)** receives a **top-down context block** — the anticipated market-moving events in
  the 5-day window + this name's exposures — and must read them (variant perception, §6).
- **Security:** numeric macro/cross-asset feeds are public APIs (low injection risk, allow-listed, 64MiB-
  capped); the **geopolitical/news LLM scan is a prompt-injection surface** → delimit + treat-as-data (§4).
  Macro series must be **point-in-time** for the backtest (no revised-figure look-ahead).

---

## 5. Pipeline (orchestrator `scripts/7_momentum.py --stage N`)

| Stage | Name | Cadence / Cost | What |
|---|---|---|---|
| 0a | universe discovery | **weekly**, API ($) | Claude proposes the candidate basket (web_search) |
| 0b | liquidity/volatility gate | daily, free | prune basket → flag illiquid / placid / penny |
| 1 | technical harvest | daily, network | OHLCV → composite + weekly σ |
| 2 | media/search/catalyst harvest | daily, network | quantify media/search features + forward catalyst calendar → `evidence` |
| 2b | **top-down signal harvest** (v0.4) | daily, network/shared | anticipate active macro/geopolitical/cross-asset/rotation signals → regime prior + per-ticker `β` contribution → `macro_signals` (§4b) |
| 3 | **Claude analysis** | daily, **API ($)** | tiered Haiku→Sonnet→Opus (Batch); receives top-down context; → `p_claude` + variant-perception memo + per-dim/macro reads |
| 4 | code-side model | daily, free | calibrated model over quantified features → `p_model` |
| 5 | **blend + rank + export** | daily, free | `p_final`, disagreement flag, confidence → `Outputs/signals.md` (long-only ranking) |
| — | render | daily, free | self-contained `Outputs/momentum_report.html` |
| — | ledger | daily, free | append predictions to the forward validation ledger (§9) |

Daily entry point: `run_7_Momentum_parser.bat` (after US close). Stage 0a runs weekly. Stage 3 is
**cost-gated** (estimate + `[y/N]` + `max_usd_per_run`), uses the **Batch API**, prompt caching, and
crash-safe incremental persist + `--resume` (reuse 3_Biopharmcatalyst patterns).

---

## 6. The Claude call (Stage 3) — tiered, Batch, cost-bounded

Per stock, build the evidence bundle (§4) = identity + technical composite + weekly σ + harvested
media/search features + forward catalyst calendar.

**Tiers (Decision D):**
- **Haiku 4.5 triage** (search-free, all names) — drop names with no plausible setup. Cheap (~$0.005/name).
- **Sonnet 4.6 rubric** (+`web_search`, **Batch API**) — the main analysis on survivors. Researches
  current media/search context live; returns the structured assessment.
- **Opus 4.8 finalize** (+`web_search`) — adversarial pass only on the **contested band** (e.g.
  `p_claude ∈ [0.45, 0.65]`).

**System prompt:** skeptical analyst rubric anchored on the §1 case; read **leading** signals only;
**ignore past announcements**, weigh **forward catalysts** + attention/search build-up + retail crowding;
never invent a source; treat all fetched content as untrusted data.

**Variant-perception discipline (v0.4).** Conviction must be earned from a **differentiated, falsifiable
claim**, not a recap. The rubric MUST state where its view diverges from consensus and why *now*; if the
only content is already-public, already-priced facts, `conviction` collapses to base-rate by construction
(scored on the *delta*, §8). This directly fixes the "low-value recap" failure (D-6).

**Structured output (JSON, strict schema):**
```
forward_drivers # (v0.5/M) 1-3 GENERATED unpriced forward drivers, each:
                #   {driver, unpriced_why, probability, expected_impact, novelty}
                #   novelty~0 for a scheduled/past/public milestone or run-up recap; ~1 for emergent+unpriced
forward_novelty # (v0.5/M) 0..1 overall forward-ness; conviction is SCALED by this (milestone/recap -> ~0)
p_up            # P(fwd-5d return > +0.5*sigma_week)         (Decision F target), grounded in forward_drivers
p_down          # P(fwd-5d return < -0.5*sigma_week)
p_flat          # 1 - p_up - p_down
expected_return # signed fractional point estimate over 5 trading days
conviction      # model's own 0..1 confidence — scored on the consensus<->our_view DELTA, not recap
consensus_view  # (v0.4) what the market currently expects / has priced
our_view        # (v0.4) our differentiated call
mispricing      # (v0.4) the specific gap between the two, and its direction
why_now         # (v0.4) the forward trigger that closes the gap inside the 5-day window
dimensions      # per-dimension read: {technical, media, search, catalyst} score + note
macro_exposure  # (v0.4) which top-down signals (§4b) in the window matter for THIS name + sign
memo            # short rationale; cites LEADING/anticipated signals only (no a-posteriori recap)
```
The probability is the **`p_claude`** leg of the blend (§8), not trusted blind.

---

## 7. (merged into §6 / §8)

## 8. Hybrid probability + blend + confidence (Decisions A, F, H, J)

- **`p_claude`** — Stage 3 structured output (target = vol-normalized dead-band, §2 F).
- **`p_model`** — code-side estimator (`probability.py`, built): logistic over the technical composite +
  standardized momentum, extended to ingest quantified media/search/catalyst features. **(v0.4)** plus a
  **top-down term** `regime_prior + Σ_s w_s·β_{t,s}·surprise_s` (§4b.4). Same vol-normalized target so the
  two legs are comparable.
- **Calibrate-then-combine (H):** each leg is calibrated (isotonic/Platt on the backtest), then
  `p_final = w·p_claude + (1−w)·p_model` (`blend.claude_weight`, default 0.6).
- **Disagreement:** `d = |p_claude − p_model|`; `d > blend.disagree_threshold` → confidence penalty +
  `review` flag.
- **Confidence (J):** `claude_conviction × data_coverage × (1 − d) × history_depth_factor`, clamped [0,1].
  `data_coverage` = fraction of the four dimensions actually populated (6_Biotech "rule 10": a signal you
  can't find ≠ negative evidence).
- **Output per stock:** `p_up`, `p_down`, `expected_return`, `confidence`, `p_claude`/`p_model`/`d`,
  direction, memo. Clamped [0.01, 0.99] — no move sold as a certainty.

---

## 9. Cost model + validation (Decisions D, E)

### 9.1 Budget ($5/day, Decision D)
Pricing (per MTok): Opus 4.8 $5/$25 · Sonnet 4.6 $3/$15 · Haiku 4.5 $1/$5 · web_search ≈ $0.01/search ·
**Batch −50% on tokens** · cache-reads ≈ 0.1×. With Haiku triage + Sonnet-Batch rubric (~$0.06/name incl.
~3 searches) + Opus on a contested few, **$5/day covers ~30–40 names**. Hard gate: estimate →
`[y/N]` → `claude.cost.max_usd_per_run`; the `cost_calibration_factor` (project memory) scales the script
estimate toward the real invoice. web_search billed per search → `claude.max_searches_per_company` bounds it.

### 9.2 Validation (Decision E — hybrid; gating milestone)
**Indicative until proven.**
- **Historical backtest (PIT archives):** reconstruct each past day's inputs from point-in-time archives
  (search-interest history + news archive + OHLCV), score the **code model + blend**, and report
  **Brier score, reliability curve, hit-rate, base-rate** — separately for `p_model` (and, where archives
  allow, `p_claude`). Earns the blend weight `w`. Use **non-overlapping / block-bootstrapped** windows
  (daily cadence + 5-day horizon → autocorrelation).
- **Forward live ledger (from day one):** every daily prediction is logged and scored against the realized
  5-day outcome as it arrives — the real validator for the full Claude pipeline. Report against a baseline
  (beat base-rate, beat `p_model`, beat always-long) and an economic P&L net of costs/slippage for the
  $100k clip.
The report says **INDICATIVE** until these pass.

---

## 10. Store (`data/momentum.db`, SQLite, additive `user_version` migrations)
`bars` (OHLCV) · `signals` (technical) · **`evidence`** (per ticker/asof/dimension quantified features) ·
**`catalysts`** (forward calendar) · **`scores`** (Claude output + config_hash + evidence fingerprint) ·
`predictions` (blended `p_final` + components) · **`ledger`** (prediction → realized outcome, for §9.2).
**v0.4 additions:** **`macro_signals`** (per asof: signal_id, anticipated surprise, regime read — §4b) ·
**`signal_weights`** (learned `w_s`, regime-conditional, with prior + n) · **`ticker_loadings`** (learned
`β_{t,s}` per ticker×signal) · **`attribution`** (settled move decomposed market/factor/idiosyncratic — §4b.3).
Parameterized SQL throughout. A re-tune (`config_hash`) or new evidence (fingerprint) re-opens a name
(6_Biotech D11/D19 pattern). Prompt/rubric version stamped on every score.

## 11. Outputs
- `Outputs/signals.md` — long-only ranking: P(up), expected return, confidence, disagreement, fired
  signals, days-to-catalyst, one-line memo.
- `Outputs/momentum_report.html` — self-contained, **`html.escape`d** diagnostic (per-dimension panel +
  Claude memo collapsible + the live ledger's running Brier/hit-rate).

## 12. Invariants
- **No magic numbers in code** — all thresholds/weights in `config/config.yaml` (+ `signals_taxonomy.yaml`).
- **Predictive-only sources** (§2.2); past announcements excluded; catalysts forward-only (G).
- **Anticipate, never recense** (§2.3, v0.4) — every signal is modelled on its *anticipated* state before it
  prints; a fired signal is attribution-only. The output must be a **differentiated view**, not a recap.
- **Signal weights `w_s` + loadings `β_{t,s}` are LEARNED, never hand-frozen** (§4b.3) — config holds priors;
  the ledger feedback loop owns the live values (shrinkage-regularized, regime-conditional).
- **Fail-open on data**; liquidity/volatility/price are the only Stage-0 exclusions (flag, reversible).
- **Untrusted-content discipline** (§4) — this module has a prompt-injection surface.
- **Cost-gated Claude** — estimate + `[y/N]` + `max_usd_per_run`, Batch API, caching, `--resume`.
- **Long-only** — no short signals acted on.
- Security (repo-wide): parameterized SQL, `yaml.safe_load`, escaped reports, 64MiB-capped reads,
  `web_search` allow-list, secrets from `.env` never logged.

## 13. Out of scope (v0.3)
Social-media sentiment (removed, Decision B) · short side (Decision F) · intraday signals · options-implied
probabilities · automated order execution · multi-sample LLM averaging (budget).

---

### Appendix — code re-alignment from the v0.1 scaffold (status)
The 2026-06-30 scaffold built a *zero-LLM* statistical predictor. Under v0.3 its parts are **repurposed**:
`signals.py` → the **technical composite + weekly σ** feature; `probability.py` → the **`p_model`** leg
(re-targeted to the vol-normalized label); store/orchestrator/report/bat/tests stay. **To build:** Stage 0a
Claude discovery, Stage 0b gate (vol/price floors), Stage 2 (media/search harvest + forward-catalyst
calendar), Stage 3 (tiered Haiku→Sonnet→Opus Batch analysis), Stage 4/5 model→blend split with calibration,
`evidence`/`catalysts`/`scores`/`ledger` tables, the §9 backtest + forward ledger. Tracked in `decisions.md`
+ the handoff.
