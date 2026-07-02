# M19 — Forward-driver generation (explained)

*First v0.5 milestone (Decision M, spec §6). Reorients Stage 3 from *evaluating known catalysts* to
*generating unpriced forward drivers*. Built 2026-07-01, after the first live run still read as backward.*

## The problem it fixes
Even v0.4's variant-perception output was organized around **past milestones and past trades**: the CLOV memo
anchored on the June-9 court ruling and May-6 Q1 print ("stale, priced in"), the ~40% run-up ("pressing the
52-week high"), and a generic macro headwind — i.e. **mean-reversion off known milestones in variant clothing.**
Root cause: the rubric *evaluates known information for mispricing* over a backward input set (past price,
published media, scheduled/public catalysts, current regime); it never *generates* a forward hypothesis. The
operator's verdict: "still not enough predictive and still too oriented on past milestone catalysts and trades."

## What it does
The rubric's **primary job is now to GENERATE forward drivers**, not to judge whether known catalysts are priced.

- **`forward_drivers`** (1–3) — the most probable specific things that will move THIS stock in the next 5 days
  that consensus is NOT positioned for. A driver **need not be scheduled**: an emergent narrative/theme, a
  positioning/flow or short-squeeze unwind, a sympathy move off a peer, a technical break with follow-through,
  or a second-order effect of an anticipated macro move. Each carries `{driver, unpriced_why, probability,
  expected_impact, novelty}`.
- **`forward_novelty`** (0..1) — the overall forward-ness of the thesis. The prompt **aggressively sets
  novelty ≈ 0** for anything that is a scheduled/public catalyst, a past announcement/result, or a run-up
  recap ("already +40%", "pressing the 52-week high"). `p_up` must be grounded in the drivers, not past price.
- **Conviction is gated on forward-ness** — `clamp_parsed`: `effective_conviction = raw_conviction ×
  forward_novelty`. A milestone/recap thesis collapses to ≈base-rate **structurally**, and — critically —
  `forward_novelty` **dominates even a high `variant_strength`**, so the CLOV pattern (differentiated but
  backward) no longer earns conviction. Falls back to the v0.4 `variant_strength` gate when absent.

## How it works
- `scoring/rubric.py` — `OUTPUT_SCHEMA` gains `forward_drivers` (array, strict item schema) + `forward_novelty`;
  `_SYSTEM` leads with "GENERATE FORWARD DRIVERS … NOT … EVALUATE KNOWN CATALYSTS" + the novelty rules;
  `clamp_parsed` gates conviction; `PROMPT_VERSION → m19`.
- `stage3_score._persist` packs `forward_drivers` + `forward_novelty` into `scores.variant_json` (no migration).
- `render._variant_html` shows the generated drivers (with p / impact / novelty) atop the Claude panel + a
  "forward novelty" tag.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_forward_drivers.py -q
```
Full suite: **158 passing** (+5). Covers the schema/prompt demands, novelty-gated conviction, `forward_novelty`
dominating a high `variant_strength` (the CLOV case), the v0.4 fallback, and persistence + render.

## Honest scope
- A 1-week forward move in liquid names is near-efficient — the realistic goal is a **differentiated forward
  hypothesis with a modest, ledger-validated edge**, not certainty. M19 makes the system *try* to generate
  forward drivers (it wasn't before) and refuses to pay conviction for backward theses.
- **This is the prompt/generation lever only.** The forward reasoning still stands on a mostly backward input
  set. The two deferred levers (operator to choose next) give it real forward inputs and close the loop:
  1. **Leading/forward INPUT signals** — vol-compression/coil, accumulation-distribution divergence,
     short-interest & borrow (squeeze), options-implied move / IV, RS inflection (need options/short-data feeds).
  2. **Driver-TYPE feedback learning** — categorize the generated drivers and learn which types actually
     precede moves (extends M16); today `forward_novelty` is persisted so novelty-vs-realized can be validated.
