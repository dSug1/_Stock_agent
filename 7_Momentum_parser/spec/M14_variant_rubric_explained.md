# M14 — Variant-perception rubric + top-down context (explained)

*Fourth v0.4 milestone (spec §6). The `p_claude` side of the redesign: the Stage-3 rubric now earns
conviction from a differentiated view, not a recap, and reads the top-down context. Built 2026-06-30.*

## What it is
The Claude rubric is reframed so a stock only earns conviction when it has a **variant perception** —
a stated `consensus_view` vs `our_view`, the `mispricing` between them, and the `why_now` trigger that
closes it inside the 5-day window — and it is handed the **top-down context block** (anticipated macro
events + this name's exposures) to weigh. Pure recap now collapses to base-rate conviction *structurally*.

## Why (the design)
The first run's core failure was low-value recap (D-6). Two fixes here:
1. **Earn conviction from the delta.** The schema gains `consensus_view / our_view / mispricing / why_now`
   plus a `variant_strength ∈ [0,1]` (how differentiated/falsifiable the view is). In `clamp_parsed`,
   **effective conviction = raw_conviction × variant_strength** — so restating public, priced facts
   (variant_strength≈0) collapses conviction toward the base rate *by construction*, not just by asking.
   `raw_conviction` + `variant_strength` are kept for transparency, and the delta-scaled `conviction` flows
   straight into `blend.confidence`, so a recap can't earn a high-confidence slot.
2. **See the top-down.** `build_bundle` now attaches a `topdown` block (regime + anticipated signals with
   days-out + THIS ticker's β exposures), and the system prompt tells Claude to weigh it — a high-beta name
   into a hawkish CPI or an AI-rotation unwind can be dominated by the macro, not its own story.

## How it works
- **`scoring/rubric.py`** — `OUTPUT_SCHEMA` + `_SYSTEM` gain the variant + macro_exposure fields and the
  top-down instructions; `PROMPT_VERSION → m14`. `build_bundle` adds `_topdown_context(store, ticker)`
  (None when no harvest — absence ≠ signal). `clamp_parsed` delta-scales conviction.
- **Store v8** (additive `ALTER`, guarded) — `scores.variant_json` holds the variant fields + strengths.
  `write_score` persists it; `stage3_score._persist` packs it (via the injected clamp path).
- **`render.py`** — `_variant_html` shows Consensus / Our view / Mispricing / Why now / Macro + a
  variant-strength tag in each Claude card. Legacy pre-M14 scores (NULL variant) render gracefully.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_variant_rubric.py -q
```
Full suite: **133 passing** (was 128; +5). Covers the schema/prompt demands, delta-scaled conviction
(recap→~0, differentiated→kept), backward-compatible clamp, the top-down block in the bundle, and
persistence. Verified the live DB migrates 7→8 and the report renders with pre-M14 (NULL-variant) scores.

## What's NOT here (next)
- **M15** — catalyst redefinition (forward fact: pre-event accumulation, expected beat/miss + falsifiable
  hypothesis). The rubric asks for forward catalysts; M15 supplies the *calculated* ones.
- **M16** — the feedback loop that learns `w_s`/`β_{t,s}` (and, with the ledger, would let variant calls be
  scored for realized edge).
- No live Claude call was run this milestone (offline, fake-scored) — first live `--dispatch` will exercise
  the new prompt/schema end-to-end.
