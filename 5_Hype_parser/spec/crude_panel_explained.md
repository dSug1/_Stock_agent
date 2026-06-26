# Crude indicative panel — what it actually does (as built)

**Status:** built 2026-06-26 (decision D14); gates refined the same day (**D15** — blockers 1–2
fixed). Phase 1 of the user's **"start crude, escalate"** path. Zero Claude. Every quantity is a
flagged proxy; the kill-switch stays **underpowered** by design.

> Read `panel_explained.md` first (the panel + kill-switch infrastructure this builds on), then
> Protocol §2 (the panel rules this approximates).

---

## 1. In one sentence

It builds a small, real panel of names — each with a **mechanically-chosen entry week (`t0`)** and a
**point-in-time feature snapshot** — and runs the kill-switch on it, purely to prove the
mechanical-t0 + PIT-feature pipeline works end-to-end and to **surface problems early**, *before* we
pay for the full n≥100 rigorous panel.

It is a **dry run of the gating experiment**, not the experiment itself. The result is not a verdict.

---

## 2. Why "crude," and why do it first

The real panel (Protocol §2) is expensive: n≥100 delisted-inclusive names, a real Module-A mispricing
gate, historical fundamentals for `m_share` labels, stratification across eras/sectors/regimes. That
is multi-session work and needs a data-provider decision.

Before committing to all that, it is worth cheaply answering: *does the machinery even work?* Can we
pick `t0` mechanically (no hindsight), reconstruct features as-of that week without leakage, attach
forward returns, and feed the kill-switch? The crude panel answers yes — and, just as valuably, shows
us **what breaks** when we try.

---

## 3. How a crude row is made

For every candidate name, in five steps (`src/hype_parser/panel_builder.py`):

1. **Candidate universe** — the SEC-filing ticker linkage we already have (`theme_tickers`, from
   Wave 3 EDGAR) across the 4 seeded themes. ~89 names; **56 survive** the yfinance price-history
   filter (junk/illiquid tickers drop out). *Caveat:* this name→theme mapping is **current**, not
   point-in-time — a crude shortcut.

2. **Mechanical `t0`** (Protocol §2.2) — walk month by month and pick the **first month where BOTH
   gates fire**:
   - **Nascency gate** — the theme must *exist and be accelerating* as-of that month (leak-free, from
     `theme_series` ≤ t): `β_spec > 0` (strict — measurably growing) **and** `n_spec ≥ 5` (a real
     specialist corpus). An **estimability guard** (`min_history_months=6`) blocks a theme's first
     months, where a slope is meaningless. *(D15 fix for blocker 2 — was a vacuous `β ≥ 0`.)*
   - **Mispricing gate (proxy)** — by default **benchmark-relative**: the name is **≥10pp below its
     sector ETF's** drawdown (crispr/mkras→XBI, rag/ssm→QQQ) — i.e. idiosyncratically cheap, not just
     falling with the whole sector. Falls back to "≥25% below its own trailing-12-mo high" when no
     benchmark is available. *(D15 fix for blocker 1 — was absolute drawdown only.)*
   `t0` is set **entirely by these PIT features**; the forward outcome is measured *afterward*, so
   the timing carries no look-ahead.

3. **PIT features as-of `t0`** — the snapshot the kill-switch regresses on:
   - `narrative = log1p(n_spec) × β_spec` — the multiplicative **Legibility × ThematicHeat** proxy
     (corpus level × specialist slope). This is the variable under test.
   - controls: `drawdown` (PIT), `sector` (theme proxy: bio=1/tech=0), `era` (years since 2015,
     PIT), `free_float` (a **current**-float proxy, median-imputed when the lookup fails — non-PIT).

4. **Forward returns + label** — yfinance forward returns from `t0` (13/26/52w). Price-only label:
   **positive** if the 52w return ≥ +100%, else **hard_negative**. Because every row passed the
   gates, there are no easy-negatives by construction — exactly the positive-vs-hard-negative contrast
   the panel is meant to draw. No `m_share` (that needs PIT fundamentals — the escalation step).

5. **Crude kill-switch** — the same numpy OLS as the real one
   (`fwd_return ~ narrative + controls`), over the crude rows, with the crude control set.

### Deliberate simplifications (vs the real panel)

| Piece | Crude proxy | Real (escalation) |
|---|---|---|
| Mispricing gate | ≥10pp below sector ETF (D15), abs-drawdown fallback | Module A value / re-rating triple-lock |
| Nascency gate | β_spec>0 strict + n_spec≥5 corpus floor (D15) | fitted β_min / n_spec on the panel |
| Legibility (B1) | `log1p(n_spec)` | Claude one-sentence legibility score |
| `p_main` gate | **disabled** (GDELT N_main is nonzero only for crispr) | enabled, consistent denominator |
| `time_to_catalyst` | **dropped** from controls | real PIT catalyst calendar |
| `free_float` | current float, median-imputed | PIT shares/float as-of t0 |
| Label | price-only (≥+100%) | + `m_share` (re-rating, not earnings, dominates) |
| n | 56 | ≥100, stratified |

---

## 4. Run it

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --build-crude --rebuild -v
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_panel.py --build-crude --theme rag   # one theme
```

Hits yfinance (local-personal only — repo memory `project_data_provider_switch`). `--rebuild` clears
prior `crude_derived` rows first; the 7 pre-registered anchors are left untouched.

---

## 5. What the live run found, and what D15 fixed

The first run (D14, 56 rows) was **correctly UNDERPOWERED** and exposed **four blockers**. Two were
engine-correctness bugs and are now **fixed (D15)**; two are inherently escalation scope.

1. **[FIXED — D15] The drawdown proxy fired on market-wide selloffs, not theme mispricing.** `t0`
   clustered at COVID-2020-10 / 2017 across unrelated names. → replaced by a **benchmark-relative**
   gate (≥10pp below the sector ETF). The COVID cluster is gone.
2. **[FIXED — D15] `β_min=0` made the nascency gate vacuous.** A flat/near-zero birth-of-theme series
   passed (`0 ≥ 0`), so `t0` fired where `n_spec ≈ 0` → `narrative = 0.000` for **~20 of 56 rows**
   (incl. CRSP/EDIT/NTLA). → now requires **`β_spec > 0` (strict) and `n_spec ≥ 5`**. The
   `narrative≈0` rows dropped to **0**; CRSP now carries `narrative=0.89` at a real t0 (2018-06).
3. **[escalation] Era confound.** The 2021-vintage high-`β` entries crashed in the 2021–22 biotech
   drawdown, so higher narrative looked *worse*. → needs era/regime stratification + out-of-sample
   walk-forward (Protocol §2.3/§3.1) on a bigger panel.
4. **[escalation] Theme-level narrative vs name-level outcome.** Every name in a theme shares the
   theme series → near-collinearity a 4-theme panel can't break. → needs many more themes.

**The read after D15 (57 rows, still underpowered, still NOT a verdict):** with the `narrative=0`
corruption removed, the spurious strong-negative coefficient from D14 (`t = −3.57`) collapsed to
`t = −0.67` — i.e. **no signal either way**, which is the honest state below n=100. A residual t0
cluster at 2023-05 (~20 mostly-rag names) reflects the noisy EDGAR rag ticker universe, not a gate
bug. The remaining "no signal" is a **power** problem (n), which is the escalation's job — the
per-row narrative variable is now meaningful.

---

## 6. What the crude panel does NOT do (and what's next)

Same boundary as the rest of the engine: no Claude, no Module A/B, no fitted parameters, no trading
signal. It does **not** deliver a kill-switch verdict — that requires the escalation step.

**Escalation (Phase 2, only if warranted):** n≥100 delisted-inclusive + stratified; a real Module-A
mispricing gate and `β_min>0` / `n_spec`-level nascency (fixes blockers 1–2); `m_share` labels via a
**historical-fundamentals provider** (open decision); restore `time_to_catalyst` and the `p_main`
gate once N_main coverage is consistent. The crude harness (`panel_builder.py` + `--build-crude`) is
the reusable skeleton for it. See `decisions.md` D14.
