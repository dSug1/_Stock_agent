# Labeled panel + kill-switch — what's built (Protocol 2-4)

**Status:** infrastructure built 2026-06-26 (decision D12). This is the **gating artifact** of the
whole program: nothing downstream (Modules A/B, parameter fitting) is justified until a real panel
exists and the kill-switch passes. What's built is the engine + the 7 anchors + the harness — **not
yet a valid panel** (that needs n>=100).

---

## 1. Why this is the priority

The program rests on one unproven claim: **narrative legibility x thematic heat drives the re-rating,
beyond cheap + catalyst.** The kill-switch (Protocol 4) tests exactly that:

```
forward_return ~ (Legibility x ThematicHeat) + controls(free_float, time_to_catalyst,
                                                         drawdown, sector, era)
```

If the narrative composite has no incremental power after controls, the premise is false and the
program collapses to a generic value+momentum screen — **stop before building Modules A/B.** It is
the cheapest high-value experiment in the project, so it comes before the scoring modules.

---

## 2. What's built

| Piece | File | What |
|---|---|---|
| Forward-return engine | `src/hype_parser/prices.py` | yfinance (injectable): per-horizon fwd return + max draw-up/down from t0; skips horizons that haven't elapsed |
| Panel storage | `src/hype_parser/panel.py` | `panel`/`panel_returns`/`panel_features` (schema v6) + `seed_anchors` |
| Kill-switch | `src/hype_parser/killswitch.py` | numpy OLS of `fwd_return ~ narrative + controls`; pre-registered pass condition; refuses a verdict when underpowered |
| Anchors | `config/panel_anchors.yaml` | the 7 calibration cases with pre-registered verdicts |
| Config | `config/panel.yaml` | horizons + the **pre-registered** kill-switch condition (positive coef, \|t\|>=2, n>=100) |
| CLI | `scripts/5_panel.py` | `--seed-anchors / --returns / --killswitch / --list` |

81 tests pass.

---

## 3. The anchor result (live forward returns)

Computed real forward returns from each anchor's `t0`:

| Anchor | label | 13w / 26w / 52w | reads as |
|---|---|---|---|
| ROKU | positive | +111 / +188 / **+312%** | strong run ✓ |
| NET | positive | +35 / +114 / **+346%** | strong run ✓ |
| SNAP | positive | +16 / +127 / **+218%** | strong run ✓ |
| CRSP | positive | −33 / +34 / **+158%** | run after a dip ✓ |
| TCRX | hard_negative | +3 / −1 / (n/a) | flat ✓ (the TScan trap) |
| SOFI | positive | −37 / −24 / **−67%** | did NOT validate |
| ELTX | positive | −52 / −34 / −2% | did NOT validate at this t0 |

**4/6 positives validate strongly and TCRX confirms as flat — but SOFI and ELTX do not.** That is
not a bug; it is the finding: their hand-picked `t0` is wrong (SOFI 2021-06 was near the post-SPAC
*top*, not the base; ELTX's run sits outside the 2024-06 + 52w window). This is exactly **Protocol
2.2's lesson** — `t0` must be set **mechanically** (the first week the mispricing + nascency gates
both fire), never by hand, because hand-placement smuggles in look-ahead *and* mis-timing. We
deliberately do **not** massage the t0s/labels to look right (that would be the hindsight
overfitting Protocol 11 warns against).

---

## 4. The kill-switch cannot run yet (correctly)

`--killswitch` reports **underpowered** (n=7 < 100) with **no PIT features** (the narrative composite
+ controls aren't reconstructed for the anchors). The harness is correct and ready; it refuses to
emit a verdict until real data exists.

---

## 5. What it takes to get a real verdict (the genuine remaining work)

This is large and partly needs decisions/data from you:

1. **Panel to n>=100**, delisted-inclusive, with **mechanical `t0`** (Protocol 2.2): requires a
   historical ticker universe + PIT mispricing + nascency gates run as-of each candidate week.
2. **PIT feature reconstruction** per panel row: a crude Legibility x ThematicHeat composite +
   controls (free_float, time_to_catalyst, drawdown, sector, era), all as-of `t0`. The diffusion
   engine (Waves 1-4) can supply theme-level PIT signals; mapping a name to its theme + computing
   the B1/B2 proxies as-of `t0` is the work.
3. **`m_share` labelling** (multiple-expansion share of return) for derived labels — needs historical
   fundamentals (a data-provider decision; yfinance covers prices, not clean historical multiples).
4. **Run the kill-switch**, then gate all Module A/B + parameter fitting behind it (Protocol 8).

Until step 4 passes, every ⚙ parameter stays unfit and no output is a trading signal.

---

## 6. Run it

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --seed-anchors
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --returns -v      # yfinance fwd returns
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --killswitch       # underpowered until n>=100
```
(yfinance is local-personal only until a licensed provider is swapped — repo memory.)

---

## 7. Crude indicative panel (decision D14 — Phase 1 of "start crude, escalate")

Rather than jump to the full n≥100 rigorous panel, we first built a **crude indicative panel** to
prove the mechanical-t0 + PIT-feature pipeline works end-to-end and to surface problems early. It is
**NOT a verdict** — every quantity is a flagged proxy and the kill-switch stays underpowered.

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --build-crude --rebuild -v
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --build-crude --theme rag    # one theme
```

**How a crude row is made** (`src/hype_parser/panel_builder.py`, config `panel.yaml::crude`):
1. **Candidates** = the EDGAR ticker linkage (`theme_tickers`) across the 4 seeded themes (~89
   names; 56 survive the yfinance history filter). Name→theme is *current*, not PIT — crude.
2. **Mechanical t0** (Protocol 2.2) = first month BOTH fire: nascency (β_spec ≥ β_min, PIT from
   `theme_series`) AND a mispricing proxy (≥25% drawdown below the trailing-12-mo high, PIT from
   price). An estimability guard (`min_history_months=6`) blocks left-edge t0s.
3. **PIT features** as-of t0: `narrative = log1p(n_spec) × β_spec` (Legibility×ThematicHeat proxy)
   + controls `drawdown / sector / era / free_float` (the last is a current-float proxy,
   median-imputed on lookup failure). `time_to_catalyst` is dropped (no PIT calendar); the `p_main`
   gate is disabled (uneven GDELT `N_main` coverage).
4. **Label** (price-only): positive if 52w fwd ≥ +100%, else hard_negative. No `m_share`.
5. **Crude kill-switch** = the same OLS over the crude rows with the crude control set.

**What the run found (56 rows, kill-switch correctly underpowered):** the narrative coefficient is
*negative*, but that says nothing about the premise — it is dominated by four problems the exercise
was built to expose:
- the **drawdown proxy fires on market-wide selloffs** (t0 clusters at COVID-2020-10 / 2017), not
  on theme mispricing → Module A (real value/re-rating gate) is needed;
- **β_min=0 makes nascency vacuous** → t0 fires at theme *birth* where n_spec≈0 → `narrative=0.000`
  for ~20/56 rows. Needs β_min>0 and/or a minimum n_spec level (left as a calibration finding,
  **not tuned to chase a sign**);
- **2021 biotech-crash era confound** → high-β 2021 entries all fell, so higher narrative looked
  *worse* (why era/regime stratification + walk-forward are mandatory);
- **theme-level narrative vs name-level outcome** → within-theme collinearity a 4-theme panel
  can't break.

**Escalation (Phase 2, only if warranted):** n≥100 delisted-inclusive + stratified; a real
Module-A mispricing gate; β_min>0 / n_spec-level nascency; `m_share` labels via a historical-
fundamentals provider (open decision); restore `time_to_catalyst` + the `p_main` gate. The crude
harness is the reusable skeleton. See `decisions.md` D14.
