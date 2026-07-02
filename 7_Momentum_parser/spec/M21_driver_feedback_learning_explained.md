# M21 — Driver-type feedback learning (explained)

*Third v0.5 milestone. Closes the loop on forward-driver generation (M19) and leading inputs (M20) and, most
importantly, **empirically validates whether any of it adds edge**. Built 2026-07-01.*

## Why
M19 made the rubric generate forward drivers; M20 gave it leading inputs. But nothing yet checked whether
those forward theses actually precede moves — or whether high-`forward_novelty` calls beat low-novelty ones.
Without that, the whole v0.5 reframe is faith. M21 settles the generated drivers against realized outcomes and
learns which **types** work and whether **novelty pays**.

## How it works
- **Rubric self-tags each driver** with a `type` (enum: squeeze, breakout, sympathy, narrative, macro, flow,
  mean_reversion, catalyst_drift, other), so categorization is free (no extra LLM pass).
- **Store v10** — `driver_outcomes` (one settled row per driver: `pred_dir = sign(expected_impact)`,
  `realized_return`, `hit`) + `driver_stats` (learned per-type skill).
- **`feedback.learn_drivers`** — for each settled ledger row, joins the SAME run's score
  (`store.score_for(ticker, asof, run_id)`), pulls its `forward_drivers`, scores a directional **hit**
  (`sign(expected_impact) == sign(realized 5-day move)`), and aggregates:
  - **per type** → `skill = 2·(shrunk_hit_rate − 0.5)` (reuses M16's `signal_skill`, shrinkage to 0.5);
  - **per novelty bucket** (low/med/high) → the **novelty-edge** check: does forward-ness pay?
  Recomputes from the full settled history each run (idempotent, like M16). Wired into `feedback.run`
  (daily settle + `--settle`).
- **`validation.build_report`** gains a "Forward drivers — which TYPES precede moves" table + a "Novelty edge"
  block, so the payoff is visible in `validation.md`.

## What it tells us (the point)
Once the ledger fills, this answers the question behind all of v0.5:
- **Which driver types have real skill** (e.g. does `breakout`/`squeeze` beat `mean_reversion`?) — a basis to
  later weight conviction by type.
- **Does novelty pay** — if `high`-novelty drivers beat `low`-novelty ones, the forward-generation reframe
  (M19) is earning its keep; if not, the "forward" theses are no better than recaps and the design needs
  rethinking. Either way it's an honest, measurable verdict rather than faith.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_driver_learning.py -q
```
Full suite: **171 passing** (+5). Covers the type enum, per-type hit/skill signs (right direction → +skill,
wrong → −skill), zero-impact skip, novelty bucketing, the `feedback.run` wiring, and the report section. Live
DB migrated 9→10.

## Honest scope
- **INDICATIVE until the ledger fills.** Per-type skill and the novelty edge need ~100+ settled drivers before
  they mean anything (shrinkage keeps them near 0 until then). This is the same forward-only constraint as the
  rest of the screen.
- **Learned stats are diagnostic, not yet acting.** Per-type skill is recorded but does not yet feed back into
  conviction/ranking — that coupling should wait until the skill is significant. Deferred with Tier-2 inputs.
