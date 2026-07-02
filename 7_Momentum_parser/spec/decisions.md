# 7_Momentum_parser — decisions log

*Newest first. Records what was built and where it deviates from / refines the spec.*

---

## M20 — Leading microstructure signals (v0.5, Tier-1 forward inputs) (2026-07-01) — BUILT
The deferred "leading INPUT signals" lever, Tier-1 slice: give the M19 forward-driver rubric real *forward*
data (it had the mandate to reason forward but a backward input set). Feasibility scoped: Tier-1 = OHLCV-only
(no new provider) → BUILT now; Tier-2 = short-interest (yfinance.info, stale ~2x/mo) + options-implied
(yfinance.option_chain, delayed) — feasible-but-caveated, DEFERRED; RS-vs-benchmark needs SPY cached in bars
(easy follow-up); Tier-3 = borrow/live-flow = paid, OUT. `microstructure.py` (pure, OHLCV): `coil` (vol
compression 0..1, 1=tightly wound), `cmf` (Chaikin Money Flow accumulation/distribution -1..1),
`bullish_divergence` (accumulating into price weakness), `breakout_pressure` (range position, vol-confirmed),
`leading_features`→ combined leading_score. Wired as a `leading` block in the rubric bundle
(`rubric._leading`, computed from bars) + a prompt section telling Claude these ANTICIPATE a move (use them
to GENERATE high-novelty forward_drivers, e.g. tight coil + accumulation + no catalyst = pre-breakout). Config
`leading:` block (windows). No store/schema change; feeds the rubric only (p_model integration = optional later).
Live check: CLOV coil 0.80 / cmf +0.10 (coiled, mildly accumulating). **164 tests** (+6). Explainer
`spec/M20_leading_microstructure_explained.md`. NEXT: RS inflection (cache benchmark), then Tier-2 short/options.

## M19 — Forward-driver generation (v0.5, Decision M) (2026-07-01) — BUILT
Operator: even v0.4 output is "still not enough predictive and still too oriented on past milestone catalysts
and trades" (CLOV memo organized around the June-9 ruling / May-6 print + the run-up = backward mean-reversion
in variant-perception clothing). Root cause: the rubric EVALUATES known information for pricing over a
backward input set; it never GENERATES a forward hypothesis. Chosen lever (of 3): reframe the rubric.
Built: `scoring/rubric.py` PRIMARY output is now `forward_drivers` (1-3 generated unpriced drivers — need not
be scheduled: emergent narrative / flow-squeeze unwind / sympathy / technical break / 2nd-order macro — each
with probability, expected_impact, novelty) + top-level `forward_novelty`. System prompt leads with GENERATE
(not evaluate), AGGRESSIVELY sets novelty≈0 for scheduled/public/past milestones and run-up recaps, grounds
p_up in the drivers. `clamp_parsed` now GATES conviction on `forward_novelty` (raw × forward_novelty →
milestone/recap thesis collapses to ≈base-rate; falls back to v0.4 variant_strength if absent) — this is the
structural fix, forward_novelty dominates even a high variant_strength (the CLOV case). Persisted in
variant_json (forward_drivers + forward_novelty); render shows the drivers (with p/impact/novelty) atop the
Claude panel + a "forward novelty" tag. PROMPT_VERSION m19. **158 tests** (+5). Explainer
`spec/M19_forward_driver_generation_explained.md`. Honest: 1-week forward is near-efficient — goal is a
differentiated forward hypothesis with modest ledger-validated edge, not certainty. NEXT levers (deferred,
operator to choose): leading/forward INPUT signals (coil/vol-compression, short-interest/borrow, options-
implied, RS inflection — need providers); driver-TYPE feedback learning (categorize + learn which pan out).

## D-7 — expected_return must track p_final, not the raw p_model leg (2026-07-01) — FIX
First live `--dispatch` run (run_20260701T151823Z, 30 names) surfaced a contradiction (operator caught it):
CLOV showed **exp.ret +1.28%** next to **p_final 43%** and a thoroughly bearish memo. Cause: `model.p_up`
computed `expected_return = typical·2·(p_model−0.5)` from the code leg (p_model 0.58, bullish) and
`stage5_blend` stored that raw value while `p_up=p_final` (0.43, after the bearish p_claude 0.33 blended it
down). So the probability got blended but the expected return never did. Fix: `model.p_up` exports `typical`
in comps; `stage5_blend` re-derives `exp_ret = typical·2·(p_final−0.5)` so the reported return is sign-
consistent with the reported probability (and identical for the model-only path where p_final=p_model).
Re-blended+re-rendered the live run → CLOV −1.22%; 0 rows with a sign contradiction (was many). **153 tests**
(+2 assertions in test_stage5_blend). NOTE: this run is the first REAL ledger data — v0.4 variant-perception +
macro layer confirmed working live (CLOV: genuine variant view + real style_factors/ai_crowding headwinds
pulling p_final down). Claude's own `expected_return` output is still unused (future: blend magnitudes too).

## M18 — Outputs polish (2026-06-30) — BUILT · v0.4 FEATURE-COMPLETE
Surfaces v0.4 signals in the deliverables (§11). `signals.md` (`stage4_export._row_extras`): +**Days→cat**
(next_catalyst) +**Our view (variant)** (scores.variant_json our_view, truncated/table-safe). `momentum_report.html`
(`render`): +**regime banner** (`_regime_banner`: latest-harvest regime + anticipated surprises) +**forward-ledger
panel** (`_ledger_panel`: metrics.summary Brier/base-rate/hit-rate + UNDERPOWERED flag; graceful empty). **153
tests** (+2). Verified on live run (new cols render "—" for pre-M14/M15 run; panels degrade gracefully).
Explainer `spec/M18_outputs_polish_explained.md`. **v0.4 (M11–M18) COMPLETE — 8 milestones, schema v9.**
Next (not a milestone): live `--dispatch` run to exercise the new prompt + start filling the ledger the M16
loop learns from.

## M17 — Post-mortem fixes folded into v0.4 (2026-06-30) — BUILT
Four first-run defects fixed now the rubric/model are rewritten. **(1/2) Absent dims ≠ negative:**
`features.media/search_features` emit `no_data`(score None) when no usable feed (media below
`media_min_volume`); `rubric._clean_dim` collapses to `{status:no_data}` + prompt says treat as absent/
coverage-reducing, never bearish, prefer own web_search over suspected artifacts. **(3) Dead Opus tier:**
`stage3_score` escalates ACTIONABLE survivors (p_claude≥`finalize_min_p`0.45, ranked by p_up, capped
`finalize_max_names`6) instead of the symmetric band that caught nobody once p_claude compressed. **(4)
Megacap drift:** `stage0_gate` new `megacap` status when $-ADV>`universe.max_adv_usd`(~$2B) — flag-not-drop,
0=disabled; crude low-float proxy (ownership feed later). **151 tests** (+6 new, 2 updated to new semantics).
Honest limits: MRNA-style GDELT artifact not fully code-detectable (rubric distrusts + volume floor); $-ADV
proxy coarse. Explainer `spec/M17_postmortem_fixes_explained.md`.

## M16 — The feedback loop (2026-06-30) — BUILT
The keystone (§4b.3): top-down priors become self-improving. `feedback.py` — pure `signal_skill`
(shrunk hit-rate → [-1,1]) / `updated_weight` (w_prior·(1+skill), clamp [0, weight_max]) / `attribute`
(market/factor/idio split). Store-facing: `settle_catalyst_hypotheses` (fill realized_drift once horizon
elapses → closes M15 predictions); `learn` (per settled ledger row, look up archived top-down read, score a
directional HIT sign(β·surprise)==sign(realized) per (signal,regime)+(signal,'all'), update `signal_weights`
+ record `attribution`). A reliable anticipator earns weight up to `topdown.weight_max`(0.5); coin-flip stays
at prior (shrinkage `learning.shrinkage_prior_n`=30); anti-signal → 0. Weights read straight back by
topdown_model (M13) ⇒ model grows. β stays the M13 regression (return-based β learning = later). Wired into
daily settle + `--settle` CLI. Graceful no-op when nothing settled / no archived read. **145 tests** (+6).
Verified no-op on live DB. Explainer `spec/M16_feedback_loop_explained.md`. LOOP CLOSED end-to-end.

## M15 — Catalyst redefinition: forward fact + falsifiable hypothesis (2026-06-30) — BUILT
Answers operator bullet 4 (§2.1-G revised). A catalyst is now (1) a forward FACT — `pre_event_accumulation`
(price drift × volume confirmation into the known date, [-1,1]); and (2) a falsifiable HYPOTHESIS —
`analog_drift` (expected_drift ± dispersion from the ticker's fwd-return dist) RECORDED in `catalyst_hypotheses`
for M16 settlement. `catalyst_signal.py` (pure: accumulation/analog_drift/hypothesis[forward-only, drops
past/beyond horizon]/catalyst_score). Store **v9** `catalyst_hypotheses` + upsert/settle/open. `stage2_harvest`
catalyst block computes hypothesis → enriched `catalyst` evidence (features carry accumulation/expected_drift/
dispersion/falsifiable) + persists hypothesis; proximity fallback only when nothing in horizon. Key semantic
shift: **a bare known date is no longer bullish by itself** (that was the recap) — the signal is positioning
INTO it + the calculated guess. Existing harvest test updated to new semantics. Config `catalyst_accum_window`
(5)/`catalyst_accum_scale`(0.1). **139 tests** (+6). Live DB migrated 8→9. Estimate-revision feed for true
beat/miss deferred (proxy for now). Explainer `spec/M15_catalyst_redefinition_explained.md`.

## M14 — Variant-perception rubric + top-down context (2026-06-30) — BUILT
The `p_claude` side of v0.4 (§6). `scoring/rubric.py`: OUTPUT_SCHEMA + system prompt gain `consensus_view/
our_view/mispricing/why_now/variant_strength/macro_exposure`; `PROMPT_VERSION→m14`. **Delta-scaled conviction**
(`clamp_parsed`): effective conviction = raw_conviction × variant_strength, so a pure recap (variant_strength
≈0) collapses to ≈base-rate STRUCTURALLY (flows into blend.confidence) — recap can't buy a high-conf slot;
raw+strength kept for transparency. `build_bundle` adds `_topdown_context` (regime + anticipated signals +
this ticker's β exposures; None when no harvest = absence≠signal) and the prompt tells Claude to weigh it.
Store **v8** (additive guarded ALTER): `scores.variant_json`; `write_score`+`_persist` pack it. `render.py`
`_variant_html` shows Consensus/Our view/Mispricing/Why now/Macro + strength tag; legacy NULL-variant scores
render gracefully. **133 tests** (+5). Live DB migrated 7→8; report re-renders. No live Claude call yet
(offline/fakes) — first `--dispatch` exercises the new prompt. Explainer `spec/M14_variant_rubric_explained.md`.

## M13 — Regime prior + p_model top-down term (2026-06-30) — BUILT
The top-down harvest now moves the code leg: `logit(p_model) += gain·Σ w_s·β_{t,s}·surprise_s` (§4b.4).
`probability.apply_logit_delta` (log-odds shift, clamp, 0=no-op) + `model.p_up(topdown_logit=0.0)` (default
no-op keeps the PIT backtest — no macro archive — unchanged). `loadings.py`: β INIT = OLS of ticker trailing
returns on factor-spread returns (ai_basket−market / growth−value / hibeta−lowvol); market-scope β=1,
uninitialized factor β=`beta_default` 0. `topdown_model.py`: pure `topdown_score` (Σ w·β·surprise; weight =
regime-conditional `signal_weights` → 'all' → 0) + `logit_for` (gain·score). CONSUMER wired: `stage5_blend`
computes the shift per ticker → `model.p_up`. `daily.py` runs Stage 2b + `loadings.refresh_universe` after
harvest — **continuous legs FREE + always; paid dated econ call only on --dispatch** (shared/cached, $5 cap).
`store.latest_macro_asof`. Config: `topdown.model_gain`(1.5)/`beta_default`(0)/`loadings_window`(60).
**128 tests** (+6). First-pass signs/weights; M16 loop learns them. Explainer `spec/M13_topdown_model_term_explained.md`.

## M12 — Top-down harvest / Stage 2b (2026-06-30) — BUILT
Populates `macro_signals` with the day's ANTICIPATED top-down read (§4b, §2.3 forward-only). `topdown.py`
(pure): `classify_regime` (VIX + HY/IG trend → risk_on/neutral/risk_off) + `continuous_surprises` (6 signals
risk_regime/rates_usd/commodities/ai_crowding/style_factors/sector_flows → signed [-1,1] from the recent
SHIFT; first-pass signs the M16 loop will learn; missing proxy → 0). `clients/econ_calendar.py`: ONE shared
web_search/day → dated FOMC/CPI/NFP/GDP + consensus → signed surprise; `parse_events` forward-only (drops
past + beyond-horizon, soonest-per-signal, clamp). `stage2b_topdown.py`: fetch proxies (injectable, fail-open
per role) → write continuous (active) + dated (active, horizon_days) + geopolitical stub (active=0). **Operator
provider decisions (handoff §2): (1) yfinance proxies (^VIX/^TNX/DX-Y/CL=F/GC=F/HYG/LQD/BOTZ/IWF/IWD/SPHB/
SPLV/SPY; swap pre-deploy), (2) shared cached web_search calendar+consensus (only net-new spend, amortized),
(3) geopolitical stubbed+flagged.** **122 tests** (+7). NOT wired into daily.py yet (no consumer until M13/M14
— avoid paying for unconsumed signals); β loadings not yet initialized (first M13 task). Explainer
`spec/M12_topdown_harvest_explained.md`.

## M11 — Top-down taxonomy + store v7 (2026-06-30) — BUILT
First v0.4 milestone (Decision L / §4b): the offline foundation for the top-down layer. `config/
signals_taxonomy.yaml` (18 signals across all 5 classes, priors only) + `taxonomy.py` (safe_load + validate:
unique ids, enum membership, `w_prior∈[0,1]`, path config-overridable) + store schema **v7** (additive 6→7):
`macro_signals` (anticipated state, `active` flag = §2.3 anticipate-not-recense) · `signal_weights`
(learned `w_s`, regime-conditional, `regime='all'` fallback) · `ticker_loadings` (learned `beta_{t,s}`) ·
`attribution` (settled-move decomposition). `seed_signal_weights` uses `ON CONFLICT DO NOTHING` so a
re-seed NEVER clobbers a learned value; `models.py` dataclasses added. **115 tests** (was 103; +12). Live
`momentum.db` migrated 6→7 cleanly (37 predictions intact) + seeded 72 weight rows. Explainer
`spec/M11_topdown_taxonomy_explained.md`. NEXT = M12 (harvest/Stage 2b), gated on provider decisions
(handoff §2). Invariant held: priors in config, learned values in store.

## D-6 — v0.4 redesign: top-down layer + catalyst redefinition + variant-perception (2026-06-30)
Operator reviewed the first real run (`run_20260630T160212Z`, 37 names) and **rejected the analysis as
low-value: descriptive, not predictive.** It recaps momentum indicators + **already-public, already-priced**
catalysts (a forward *date* like "PDUFA Aug-5" is a calendar lookup, not a forecast). Two gaps named:
(1) **no substantive top-down meat** — zero macro (rate path, CPI/NFP/FOMC), zero market-level signals
(peace talks, tariffs, **rotation from AI**), which dominate a 1-week move in a high-beta retail basket;
(2) catalysts are stock-specific facts, not **calculated guesses that feedback-loop**. Operator additions:
the top-down layer must (a) define + **assign weights** to a **broader** list of market-/ticker-perturbing
signals and **feedback-loop those weights**; (b) stay **forward-looking** — *anticipate the arrival* of a
signal, don't recense it once it materialized. And catalysts may be **a forward fact** too (pre-event
accumulation/drift, expected earnings beat/miss) **or** a falsifiable-quantified hypothesis — always forward.

**Decisions locked → spec bumped to v0.4:**
- **New Decision L — top-down market-perturbation layer** (§4b): a weighted, forward-looking, feedback-looped
  **signal taxonomy** (MONETARY / GEOPOLITICAL / CROSS-ASSET / ROTATION + ticker catalysts) with per-signal
  learnable weights `w_s` and per-ticker learnable loadings `β_{t,s}`; a regime prior + `Σ w_s·β·surprise`
  term feeds both `p_model` and the Claude rubric. First-draft taxonomy + prior weights tabled in §4b.2.
- **§2.3 anticipate-never-recense** principle; **Decision G revised** (catalyst = forward fact OR quantified
  hypothesis, both anticipatory; fired = attribution-only).
- **§6 variant-perception rubric** — new structured fields `consensus_view / our_view / mispricing / why_now
  / macro_exposure`; conviction scored on the **delta**, recap → base rate.
- **Store v0.4** — `macro_signals / signal_weights / ticker_loadings / attribution` tables.

**Decided NOW (operator):** build order = **top-down taxonomy + regime layer leads** (then catalysts/thesis,
then bug-fixes). Build starts next session; see `.claude/7_Momentum_parser_v0.4_build_handoff.md` (the TODO).

**Also fixed now (redesign-independent, D-6a — test pollution):** `tests/test_daily.py::
test_daily_dry_run_end_to_end` ran the real `stage4_export` with `outputs_dir()` hardcoded to `ROOT/Outputs`,
so **every `pytest` run clobbered the real `Outputs/signals.md`** with `run day1 / GOOD1 / GOOD2`. Fixed:
`config.outputs_dir` now honors `cfg["outputs"]["dir"]`; the test routes to `tmp_path`. Regenerated the real
`signals.md` from `run_20260630T160212Z` (37 names). **103 tests pass.** The other post-mortem defects
(stub-dims-leak-as-negative, GDELT artifacts, dead Opus tier, discovery retail-gate) are folded into the
v0.4 build (the rubric/model they touch is being rewritten — fixing twice is waste).

## D-5 — Stage-0a (discovery) cost estimate decoupled from the scoring fudge (2026-06-30)
First real discovery invoice came in at **$0.39**, but the dry-run preview printed **~$0.01** (~31× low).
Root cause: `_estimate` borrowed the Stage-3 `cost_calibration_factor` (0.10), which is the **Batch-scoring**
fudge (M6: scoring estimate ran ~10× hot because of Batch −50% + prompt-cache reuse + inflated tokens).
Discovery is the **opposite profile** — a realtime, web_search-heavy, multi-continuation call where each
`pause_turn` re-bills the accumulated web_search result content as input, so effective input (~96k tok)
dwarfs the base prompt (the old 9k guess). Fix: gave discovery its **own** cost model — `claude.cost.discovery`
(`in_tok: 96000`, `out_tok: 2500`, `calibration_factor: 1.0`); the 0.10 scoring fudge no longer applies here.
New estimate prints **$0.3855** vs actual $0.39 (<1%). Token model tuned to ONE invoice — re-tune `in_tok`
as more bills land. No new milestone (cost-calibration fix); **103 tests** still pass.

**D-5b — Stage-3 scoring bill checked, calibration HELD (2026-06-30).** First real scoring invoice **$0.22**
for run `run_20260630T160212Z` (37 names: 37 Haiku triage → 29 Sonnet/Batch rubric → 0 Opus finalize).
Estimator printed **$0.16** for n=37 (only ~1.4× low — in-ballpark, unlike discovery's 31×). The two
structural guesses offset: it under-counted survivors (assumed `triage_pass_frac` 0.5 = 18.5, real **0.78** =
29) but added phantom finalize (`contested_frac` 0.2, real 0). Backing calibration out of the actual tier
shape ⇒ implied factor **0.131** vs configured **0.10** — at the upper edge of the documented 0.075–0.13
band (6_Biotech bills 0.051/0.097). **No change made**: per the cost memory's rule, move the factor only when
bills are *consistently* outside the band; one boundary sample isn't enough, and $0.22 ≪ the $5/day gate.
Revisit `cost_calibration_factor` (→ ~0.13, err-high is safe) after 2–3 more scoring bills.

## D-4 — Daily-run UX + HTML report v0.3 + Windows unicode fix (2026-06-30)
Operator ran `--daily` (DRY): appeared "stuck then closes" + HTML report stale (no Claude reasoning). Causes
+ fixes: (1) the slow network steps (prices yfinance, GDELT harvest) were **silenced** in `daily.run` → looked
frozen — now they print per-ticker progress + a GDELT "first run ~5s/call, cached after" notice; `--no-fetch`
now also skips GDELT (fast offline preview). (2) `render.py` was still the M0 scaffold (P(up)/composite only)
— **rewritten to v0.3**: expandable per-ticker cards with the blend columns (p_final/p_claude/p_model/Δ/conf/
review) + a **Claude reasoning panel** (memo + per-dimension reads from `scores`), or a "model-only — run
`--dispatch`" notice when unscored (DRY runs have NO Claude call by design = no spend). (3) **Windows cp1252
console crashed on unicode** (σ/×/→/⚠) — `7_momentum.py` now `sys.stdout/stderr.reconfigure(encoding="utf-8")`
so a stray glyph can never abort a run. Clarified to operator: DRY = no Claude/no spend; Claude reasoning
only after `--dispatch`. **103 tests** (+render test). No new milestone — UX/robustness hardening of M8/M10.
**D-4b — interactive .bat menu:** double-clicking `run_7_Momentum_parser.bat` passed no args → always DRY,
no way to choose live. Rewrote the bat: NO-arg run shows a **[D]ry / [P]roduction menu** (P requires typing
YES to confirm a billed run) + `pause` at end (window stays open). Passing any flag (scheduler/power-user)
skips the menu and uses it as-is. (Couldn't exec the .bat in the Git-Bash sandbox — cmd bridge broken;
verified by review + the underlying `--daily`/`--daily --no-fetch`/`--dispatch` python paths are tested.)

## M10 — GDELT news provider (real media dimension, 429-safe) (2026-06-30) — BUILT
First real data provider — Stage-2 `media` dimension no longer a stub. `clients/gdelt.py` (DOC 2.0
TimelineVolRaw=volume + TimelineTone=tone → `features.media_features`). **429-avoidance (5_Hype lesson,
extended), in impact order:** (1) same-day disk cache `data/gdelt_cache` TTL 24h — re-run never re-hits;
(2) proactive `min_interval_s` 5s spacing between ANY calls; (3) retry+backoff honoring Retry-After on 429,
then FAIL OPEN; (4) 64MiB cap + hardcoded HTTPS (no SSRF). **Precision: query by company NAME** (from
discovery via `OPTS['names']`), not ticker — validated: "GME stock"→~0 but "GameStop"→30/31 days w/ news
(2-16/day), "Tesla"→277-581/day. Wired via `sources.media_provider: gdelt`. Parser handles real format
`20260603T000000Z`. HONEST: GDELT rate-limits hard even at 5-6s on first contact → some calls fail-open
under load (cache fixes re-runs; fail-open=data-coverage fairness). **101 tests pass** (was 94). Explainer
`spec/M10_gdelt_provider_explained.md`. Search/catalyst dims still stubs; PIT media archive for backtest
deferred.

## M9 — out-of-sample (walk-forward) calibration validation (2026-06-30) — BUILT
Resolves the M6 in-sample caveat — the biggest lever on trust. `cv.py` (pure) `walk_forward`: time-ordered
expanding-window CV — fit calibrator only on decisions preceding each held-out fold, aggregate OOS Brier
(raw vs calibrated), no look-ahead. `7_calibrate.py` now reports IN-SAMPLE + OOS + GENERALIZES verdict;
backtest rows carry `asof`. **REAL 16-name universe (1,392 decisions): IN-SAMPLE Brier 0.2467→0.2040;
OUT-OF-SAMPLE 0.237→0.204 (GENERALIZES, n_oos=1114) — calibration NOT overfitting.** Backtest no longer
underpowered: base_rate 0.292, up_call_hit_rate 0.330>base (small real edge), consistent overconfidence
(top bin 0.85 pred→0.44 obs) calibration corrects. HONEST: modest skill (Brier ~0.20 vs 0.25 baseline),
measured BEFORE media/search dims wired (neutral stubs) + before Claude-leg forward validation → headroom.
**p_model leg now OUT-OF-SAMPLE VALIDATED.** Full p_final still forward-only (ledger n≥100). **94 tests pass**
(was 90). Explainer `spec/M9_oos_validation_explained.md`.

## M8 — daily orchestrator + runner reconcile (2026-06-30) — BUILT
The seven stage-flags become one daily command (Objective #1). `daily.py` sequences 0a discover(WEEKLY,
release-calendar gated via `discovery_due`)→1 prices→0b gate→2 harvest→3 score(gated)→5 blend→§9 settle→
render. Scoring auto-gated by `--dispatch` + `max_usd_per_run` $5 (the unattended replacement for [y/N]); a
DRY run still yields a free model-only signals.md. CLI `--daily`/`--daily --dispatch`; `run_7_Momentum_parser.bat`
now `--daily %*` (DRY default; live = `--dispatch`) — module 7's daily runner (per `feedback_update_daily_runner`;
`1_not_used/daily_orchestrator.py` is module-1-specific, untouched). Pipeline self-feeds: discovery→gate→
`investable_tickers`→harvest/score/blend (no `--tickers` needed). Verified offline `--daily --no-fetch` dry
E2E (0a skipped not-due, gate 2 pass, model-only blend, 4 artifacts written). **90 tests pass** (was 86).
Explainer `spec/M8_daily_orchestrator_explained.md`. Legacy `--stage 1..4` kept for debug (superseded).

## M7 — Stage 0a discovery + 0b gate (the universe front) (2026-06-30) — BUILT
The last pipeline stage; it PRODUCES the universe (Decision C). 0a `stage0_discovery.py`: one cost-gated
Claude call (web_search) proposes the basket → `discovery` (reuses M3 `AnthropicScorer.complete`). 0b
`stage0_gate.py`: prunes by liquidity (ADV≥$100k×mult) / volatility (σ≥floor) / price (≥penny); flag never
drop (pass/penny/illiquid/placid/no_data) → `universe_gate`. `load_universe(cfg,store)` now prefers
`investable_tickers` (gate-pass) over seed CSV. Schema **v6** (discovery+universe_gate). CLI `--discover`/
`--gate`. **REAL: 42 candidates (GME/AMC/HOOD/MRNA/VKTX/biotech…, 5 searches, leading-signal reasons);
gated 10 → all pass (real ADV/σ/px).** **86 tests pass** (was 80). Explainer
`spec/M7_stage0_discovery_gate_explained.md`.
**LESSON (→ [[feedback_claude_web_search_variant_and_output_cap]]):** open-ended discovery under a forced
JSON schema returned EMPTY w/ 0 searches — the model emitted the schema without searching (4.6+ conservative
on tools). Fix = prescriptive "you MUST web_search before answering" directive (system + search-first user
turn). Multi-search call exceeded 120s → own `discovery_timeout_s` 300 + trim `discovery_searches` to 6.
ALL 7 PIPELINE STAGES NOW EXIST.

## M6 — probability calibration (isotonic) (2026-06-30) — BUILT
First step INDICATIVE→trusted + static seed of the v2 feedback loop. `calibration.py` (pure): isotonic via
PAV (no sklearn), `Calibrator` step-fn (json-serializable), `fit_isotonic(pairs, min_n=50)` = identity until
enough samples. Schema **v5** `calibration` table (per-leg) + `store.save/load_calibrator`. `stage5_blend`
loads model+claude calibrators → `blend.calibrate(p,leg)` dispatcher so every `p_final` is calibrated.
`scripts/7_calibrate.py` fits the MODEL leg off the backtest (CLAUDE leg = identity, calibrated forward off
the ledger — no historical web state). **REAL MRNA: Brier 0.2507→0.2013; overconfident 0.86→0.43 (region's
observed up-rate).** ⚠ in-sample (n=87, 1 ticker) — mechanism proven, not edge. **80 tests pass** (was 73).
Explainer `spec/M6_calibration_explained.md`. Also recorded operator's **v2 self-growing feedback loop**
roadmap (a-posteriori checks, per-dimension weight learning, regime reweighting, accumulating) in
`spec/ROADMAP_remaining.md` + handoff — M6 is its static seed.

## M5 — §9 validation: ledger settle + PIT backtest (2026-06-30) — BUILT
The last core piece — measures "INDICATIVE" instead of asserting it (Decision E). `metrics.py` (pure):
Brier/base-rate/up-call-hit-rate/reliability/underpowered(n<100). **Part a** `backtest.py` + `7_backtest.py`:
historical PIT backtest of `p_model` over NON-OVERLAPPING bars (step=horizon), honest PIT (σ + empirical use
only past/realized data), composites+labels precomputed once. **Part b** `validation.py` `settle_pass`:
resolves open `ledger` rows once horizon elapsed (realized return → `targets.label_move` w/ stored σ_week →
`store.settle_ledger`) → `build_report` → `Outputs/validation.md`. CLI `--settle`; `store.settled_ledger`.
**REAL backtest on MRNA (87 decisions): Brier 0.2507 ≈ 0.25 (uncalibrated p_model ~no skill yet) +
overconfident-on-upside reliability curve → harness correctly keeps screen INDICATIVE** (honest, not
rubber-stamped). **73 tests pass** (was 68). Explainer `spec/M5_validation_explained.md`. To reach TRUSTED:
calibrate p_model (isotonic on the reliability curve → `blend.calibrate`), accrue forward ledger n≥100,
earn `w`, economic P&L, wire media/search PIT archives. Core pipeline now whole (M1–M5); remaining =
calibration + Stage 0a + providers + daily-runner, not new architecture.

## M4 — Stage 4/5 code-side p_model + hybrid blend (2026-06-30) — BUILT
Closes the prediction loop. `model.py` = the `p_model` leg re-targeted to the M1 vol-normalized label
(logistic over composite+drift blended with historical label-up-rate via `targets.label_series`; pure).
`blend.py` (pure) = `p_final=w·p_claude+(1−w)·p_model` + disagreement→review + confidence J (conviction×
coverage×(1−disagree)×history; calibrate hook, identity default → INDICATIVE). `stage5_blend.py` reads
`p_claude` (`store.latest_score`), blends, writes the blended `Prediction` (schema **v4**: predictions +
p_claude/p_model/disagreement/review via guarded idempotent ALTER) + opens a `ledger` row (directional call
`p_final≥up_call_threshold`). Export ranks long-only by p_final w/ leg breakdown + ⚠ review, labelled
INDICATIVE. CLI `--blend`. **REAL debug smoke test passed** (MRNA: triage→rubric, 2 web searches, p_up 0.38,
memo cited forward Aug-5 PDUFA + "already priced in" past events — design validated against the live API).
**68 tests pass** (was 59). Explainer `spec/M4_stage5_blend_explained.md`. Remaining core: §9 ledger settle
pass + PIT backtest (earns w + calibrators → moves INDICATIVE→trusted) + Stage 0a discovery.

## D-3 — Token cap vs run budget clarified + debug mode (2026-06-30)
Operator asked whether the 12500 token cap is too low and how to debug without the $5 gate. Clarified the
conflation: `max_output_tokens` (12500) is a per-CALL truncation ceiling — **billed on actual tokens, not
the cap** — orthogonal to `max_usd_per_run` ($5), the per-RUN spend gate. 12500 is NOT too low (momentum's
JSON schema is small; ~500–3000 real output tokens incl. thinking/search); **kept at 12500**. Constraint:
stay <16000 or the SDK requires streaming — raise only WITH streaming if `stop_reason==max_tokens` ever
appears. Cost lever in debug is NAMES, not tokens. Built **`--debug`** (`stage3_score.run(debug=True)`):
bypasses the `max_usd_per_run` abort, caps to `claude.debug.max_names` (default 3), forces real-time (no
Batch wait); keeps 12500. Debug on 1–3 names ≈ cents (calibrated est ~$0.004/name). 59 tests.

## M3 — Stage 3 tiered Claude scoring (2026-06-30) — BUILT
The first paid stage + dispatch centerpiece. Tiers (Decision D): Haiku triage (realtime, search-free,
recall-safe `triage_floor` 0.35) → Sonnet rubric (+web_search, **Batch API**) → Opus finalize (realtime)
on the contested band [0.45,0.65]. **All prior Claude-API lessons implemented:** Batch submit/poll split
with `batch_id` persisted BEFORE polling (schema **v3** `batch_jobs`) + `--resume`; crash-safe incremental
`write_score` per result (on_result callback, never bulk); bounded SDK timeout 120s (the "1hr hang" fix);
basic `web_search_20250305` + bounded `max_uses` + 12500 output cap; prompt caching on the stable system
prefix; async bounded concurrency (`score_many_realtime`); pause_turn handling; cost estimate + `--dispatch`
gate + `max_usd_per_run` $5 cap + calibration factor 0.10; re-score skip via `config_hash`+
`evidence_fingerprint`. Pure/tested: `scoring/{rubric,cost,identity}.py`; orchestration `stage3_score.py`
tested via `FakeScorer` (3-method interface). Networked-only `clients/anthropic_client.py` (not unit-tested).
Strict JSON schema → `clamp_parsed` to [0.01,0.99]. Untrusted-content delimiting + allow-list. CLI
`--score`/`--dispatch`/`--resume`/`--force-rescore`; nothing bills without `--dispatch` (claude.enabled
false by default). **58 tests pass** (was 43). Explainer `spec/M3_stage3_claude_explained.md`. Unblocks
Stage 4/5 (`p_claude` leg) + §9 ledger. NOTE: real Claude path unexercised by tests — first `--dispatch`
should be a tiny smoke test.

## M2 — Stage 2 multidimensional harvest (2026-06-30) — BUILT
Populates `evidence` (dimensions media/search/catalyst) + `catalysts` (forward dates). Three layers:
pure feature math (`features.py` surge_ratio/slope/media_features/search_features; `catalysts.py`
days_to_next/proximity_score — offline), fail-open provider seams (`clients/{news,search_interest,
catalysts_client}.py` — providers DEFERRED: GDELT/Trends-style/FDA-ct.gov-earnings, all PIT-archivable),
and `stage2_harvest.py` (injectable clients → store). Numbers only ⇒ no prompt-injection surface here.
Forward-only catalysts (G): proximity ramps 1.0→0 over `catalyst_horizon_days`=21; `asof` passed in (no
wall-clock). Fail-open: writes neutral evidence when no provider wired (data-coverage fairness). CLI
`--harvest` (additive; orchestrator stage-renumber to v0.3 deferred to when Claude stages land). **43
tests pass** (was 31). Explainer `spec/M2_stage2_harvest_explained.md`. Unblocks Stage 3 bundle + §9
backtest dimensions.

## M1 — Move-target label + store schema v2 (2026-06-30) — BUILT
First build milestone of v0.3. `targets.py` = the vol-normalized dead-band label (Decision F): `up` if
fwd-5d return > +0.5×σ_week, `down` if < −0.5×σ_week, else `flat`; σ_week = std of **non-overlapping**
weekly simple returns (trailing 12wk); `label_series` is point-in-time (no look-ahead); fail-open `None`
when σ/forward-window missing. Store **schema v2** (additive `user_version` 1→2): `evidence`, `catalysts`
(forward dates only), `scores` (config_hash+evidence_fingerprint+prompt_version re-open keys), `ledger`
(open→realized lifecycle) + DAO methods. **31 tests pass** (was 20). v1→v2 upgrade verified idempotent.
Explainer: `spec/M1_targets_and_schema_explained.md` (per [[feedback_milestone_explainer_md]]). Unblocks
Stage 2 (evidence/catalysts), Stage 3 (scores), Stage 4 (re-target probability.py to label_series), §9
(ledger + backtest).

## D-2 — Spec completed to v0.3: six decisions locked + author defaults (2026-06-30)
Resolved the open architecture via operator MCQs (recommendations accepted/overridden):
- **C — Universe = CLAUDE-DISCOVERY-FIRST.** A weekly Claude web_search call proposes the retail-heavy /
  hype-prone basket from current chatter + search trends; a daily code **liquidity + volatility + price**
  gate prunes it (flag, never silent-drop). (Hand-curated CSV from D-1 is dropped.)
- **D — Budget = TIERED + BATCH under $5/day.** Haiku triage (search-free, all) → Sonnet rubric
  (+web_search, Batch −50%) → Opus on the contested band. ~30–40 names/day. Estimate+`[y/N]`+
  `max_usd_per_run` gate. Pricing (per MTok): Opus 4.8 5/25 · Sonnet 4.6 3/15 · Haiku 4.5 1/5 ·
  web_search ~$0.01/search (confirmed via claude-api skill).
- **E — Validation = HYBRID.** Historical backtest of code model+blend on PIT archives (search-interest
  history + news archive + OHLCV) + forward live prediction-vs-realized ledger from day one. Non-overlapping
  /block-bootstrapped windows; beat-base-rate + economic-P&L baselines.
- **F — Target = VOL-NORMALIZED DEAD-BAND, LONG-ONLY.** `up` if fwd-5d return > +0.5×σ_week, `down` if
  < −0.5×σ_week, else `flat`. Long-only acts on `up`. (Resolves the undefined-label + benchmark/vol
  critiques.)
- **B (refined) — Sources: SOCIAL MEDIA REMOVED** (too costly/noisy). Kept: media + web-search stats +
  technical composite + **forward corporate catalysts** (re-included). Removing social makes the historical
  backtest feasible (Trends/GDELT archives are PIT-reconstructable).
- **A (kept) — Probability = HYBRID** (`p_final=w·p_claude+(1−w)·p_model`, w=0.6, disagreement→review).

**Author defaults (override anytime):** **G** catalysts FORWARD-only (scheduled readouts/PDUFA/earnings
dates — anticipatory; past results stay out); **H** calibrate-then-combine each leg (isotonic/Platt) before
blending; **I** single-sample structured output — the chosen models reject `temperature`, so determinism is
adaptive-thinking+strict-schema, not a sampling knob (multi-sample out of budget); **J** confidence =
conviction × data_coverage × (1−disagreement) × history_depth; **K** long-only decision rule = rank by
`p_up`, gate on `p_up` + confidence floors, secondary sort expected_return.

Spec rewritten to **v0.3**. web_search variant stays the basic `web_search_20250305` (project memory:
measured to honor max_uses + ~10× faster than the dynamic variant).

## D-1 — COURSE-CORRECTION: LLM-centric, not zero-LLM (2026-06-30)
The D0 scaffold went too far ahead and built a *zero-LLM statistical* predictor as the core. The
operator's actual design (spec v0.2) makes **Claude the multidimensional analyser**; the OHLCV composite
is only **one feature**. Re-centred the spec on the daily LLM pipeline. Three decisions locked with the
operator:
- **A — Probability = HYBRID.** Claude's `p_claude` **blended** with a calibrated code-side `p_model`;
  `p_final = w·p_claude + (1−w)·p_model` (default w=0.6); disagreement `|p_claude−p_model|` over a
  threshold → confidence penalty + `review` flag.
- **B — Sources = media + web-search stats + SOCIAL retail sentiment (Reddit/WSB, StockTwits, X) +
  technical composite.** Predictive-only: **corporate announcements excluded** (a-posteriori, look-ahead).
- **C — Universe = HAND-CURATED seed list** (`config/universe.csv`) + daily **$100k/session liquidity
  gate** (flag, never silent-drop).

**Code re-alignment (not a rewrite):** `signals.py` → technical-composite feature; `probability.py` →
the `p_model` leg; store/orchestrator/report/bat/20 tests stay. **To build:** Stage 2 media/search/social
harvest, Stage 3 Claude analysis (structured `p_up`/`p_down`/expected_return, `web_search` allow-listed,
Batch + cost gate + `--resume`), Stage 4/5 model→blend split, `evidence`+`scores` tables, §10 backtest.
**NEW security surface:** scraped media/social enters a Claude prompt ⇒ prompt-injection; delimit +
treat-as-data + allow-list + 64MiB cap (unlike 5_Hype's zero-LLM discovery).

## D0 — Scaffold (2026-06-30)
Built the module skeleton end-to-end, matching repo conventions (renamed package `momentum_parser`
under `src/`, staged orchestrator, SQLite store with `user_version` migrations, yaml config, offline
pytest, `.bat` entry points, self-contained HTML report).

- **Zero-LLM by default.** Signals + probability are deterministic and pure (no network, no pandas in
  core) so the engine is fully offline-testable. Provider isolated behind `clients/market.py`.
- **Signal basket:** SMA cross, ROC, RSI(14), MACD histogram, volume surge, Donchian breakout →
  weighted `composite ∈ [-1,1]`. Chosen for transparency; weights in config.
- **Probability = logistic baseline blended with a ticker-specific empirical conditional up-rate.**
  20 unit tests (signals monotonicity/bounds, empirical estimator, store round-trip, config) — all pass.
  Verified end-to-end on injected synthetic series: uptrend→P(up)=0.98, downtrend→0.01, correct ranking.

## D1 — PENDING (next): calibrate the probability model
The baseline `beta`/`bias` are placeholders. Build a **walk-forward backtest** (`stage5_backtest.py` +
`scripts/7_backtest.py`): for each historical day compute the composite, realize the forward-5-day
outcome, then fit `beta`/`bias` and report **Brier score + reliability curve + base-rate**. Until this
lands, all probabilities are labelled **INDICATIVE** in the report (same bar 5_Hype_parser set for its
first panel). *The screen is not trusted until this passes.*

## Open items (see handoff ROADMAP)
- Wire `run_7_Momentum_parser.bat` into `daily_orchestrator.py` (18:00 live-data entry point) once
  calibrated — repo convention is to wire each completed step into the daily runner.
- Swap yfinance → licensed provider before any public deploy (repo-wide ToS policy).
- Universe source: graduate from the seed CSV to pulling from 6's shortlist / 2_Funds holdings /
  5's discovered themes.
