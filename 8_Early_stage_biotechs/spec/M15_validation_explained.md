# M15 — §9 validation harness (back-testing conviction), explained

*House-style milestone note. Companion to `decisions.md` D13 and overall spec §9. This is the layer
that makes the pipeline's flags **trustworthy rather than merely plausible** — by measuring them
against cases where the right answer is already known.*

---

## Why this is the milestone that matters
Every prior milestone *produced* signal; this one *audits* it. Spec §9 is blunt: the
independence/novelty/scoring calls are "unverified against a labeled dataset" — before trusting a
`deep-dive-candidate` flag at face value, back-test against 10–15 known cases (Satellos included) where
the outcome is already known, to get precision/recall. Without that, a conviction score is a plausible
number, not a trustworthy one. This harness is that back-test. It is **zero-spend**: it makes no Claude
calls and no network requests — it reads only what the pipeline has already scored and joins it against
a hand-labeled ground-truth set.

## What it does
`validation.py` + `scripts/8_validate.py` join `validation/known_cases.yaml` (positives that re-rated on
an under-recognized mechanism, e.g. Satellos = MSLE; negatives/controls that should NOT flag) against
the store, then report three things — kept deliberately un-conflated:

1. **Funnel (survivorship view).** How far each labeled case travelled:
   `not_in_universe → inactive/below_floor/above_ceiling → in_universe → prefilter_cleared → scored`.
   A loss in an early stage caps end-to-end recall **upstream of the scoring call** — no model quality
   can recover a positive that never entered the universe. This is what makes the §9 *survivorship bias*
   measurable instead of hand-waved.
2. **Classification (scored subset only).** Precision / recall / F1 / accuracy + the TP/FP/FN/TN
   confusion, computed **only over cases that actually reached the scoring call** — i.e. the model's
   discrimination *given* it saw the case. Default decision rule: `deep-dive-candidate ⇒ predicted
   positive`; `--rule score --threshold N` switches to a `conviction_score` cutoff.
3. **Threshold sweep.** Precision/recall across `conviction_score` cutoffs (0→100), because D3 ranks the
   digest on the *score*, not just the flag — this shows where to set the actionable bar.

## The one distinction everything hinges on: two recalls
- **`funnel_recall`** = scored positives / **all** positives — end-to-end, *includes* survivorship loss.
  Answers: "of the winners we know about, how many does the whole pipeline even surface?"
- **`model_recall`** = TP / (TP+FN) over the **scored subset** — the scoring call's discrimination.
  Answers: "given the model saw the case, did it call it right?"

Conflating them would let a great model hide a leaky funnel (or vice-versa). The report prints both and
labels them. Undefined metrics render as `—`/`None`, **never a fake 0** (precision over zero predicted
positives is undefined, not zero). A `sufficient` flag (≥3 positive / ≥2 negative *scored*) gates trust:
below it, a loud **INSUFFICIENT-DATA** banner fires and the numbers are marked indicative-only.

## Matching a labeled case to a stored entity
Highest-precision key first, so a loose name never overrides a hard identifier:
**CIK → ticker/alias → normalized name.** Ticker matching is exchange-suffix-tolerant (`MSLE.V` → `MSLE`).
Name matching drops corporate-form suffixes (`Inc`, `Ltd`, `Corp`, `Holdings`, `AG`, …) but **keeps
industry words** (`therapeutics`, `bio`, `sciences`) — dropping those would collapse genuinely-distinct
companies and cost precision. A case that resolves to nothing is reported as `not_in_universe` — which is
itself informative (a coverage/survivorship gap), but contributes nothing to precision/recall until it
resolves. Two tiny store helpers back this: `entities_by_ticker` and `get_score`.

## What the first live run revealed (the harness earning its keep)
Six seed cases against the live store — and the honest state is *insufficient data* (0 scored), which is
exactly the §9 point: the flags are not yet trustworthy, and here is the precise gap:
- **Satellos Bioscience (MSLE)** matched by ticker and sits **`in_universe` but unscored** → a concrete
  work-list item: run `8_extract → 8_signals --literature → 8_score` on it and it becomes the anchor
  back-test case.
- **Arcus (RCUS) and Cytokinetics (CYTK)** are **`above_ceiling`** — already re-rated *past* the $3B
  small-cap ceiling. This exposes a real structural limit for back-testing *known winners*: winners
  outgrow the active universe by definition, so a faithful precision/recall needs **point-in-time
  (as-of) market caps** in the labeled set, or a ceiling-relaxed validation mode. Noted here, **not**
  hacked into the live gate — the funnel stage `above_ceiling` reports it honestly.
- **Cassava (SAVA)** didn't match at all (`not_in_universe`) — a coverage note for the operator.

The report's **"Unscored positives (the work-list)"** section turns all of this into the exact next
actions, split by whether the fix is *universe expansion* (`not_in_universe`) or *just run the sweep*
(`in_universe`/`prefilter_cleared`).

## Label quality is operator-owned (and honest about it)
The machinery is complete and tested; the *labels* are data the operator curates. Every case carries a
`verified` flag — un-verified rows are marked **⚠︎** in the report and excluded from trust. Only
Satellos ships `verified: true` (the spec's named anchor); the rest are clearly-marked illustrative
seeds to expand and fact-check toward the §9 target of 10–15 verified cases. `known_cases.yaml` is parsed
with `yaml.safe_load` only (repo security discipline) and validates every label on load.

## How to run / verify
```
cd 8_Early_stage_biotechs
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_validation.py -q      # 11 offline tests (no spend)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_validate.py                       # console summary + Outputs\validation_report.md
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_validate.py --rule score --threshold 65
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_validate.py --json Outputs\validation.json
```

## Deferred / next (honest scope)
- **Point-in-time caps** in the labeled set (or a `--ignore-ceiling` validation mode) so re-rated
  winners aren't lost to `above_ceiling` — the one thing blocking a clean winners back-test.
- **Grow the labeled set to ≥10–15 verified cases** and fill CIK/ticker so they match; then score the
  matched-but-unscored positives (Satellos first) to make `sufficient` flip true and the metrics real.
- **Per-signal ablation** — once scored, attribute conviction hits/misses to which signal (literature vs
  capital vs pedigree) carried them, to calibrate the rubric weights (D3).
