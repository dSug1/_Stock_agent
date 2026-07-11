# M11 — Stack-convergence scoring + digest (the capstone), explained

*House-style milestone note. Companion to `decisions.md` D10/D3 and overall spec §5.4/§5.5/§7. This is
the layer that turns all the module's signals into a single ranked, human-readable deliverable.*

---

## What it does
For each candidate that clears a rules-based pre-filter, one Claude call fuses the assembled evidence
(founder lineage, independent-citation counts, specialist-fund crossings, capital-markets activity,
identity) into a structured conviction — `conviction_flag` (surveil / deep-dive-candidate /
deprioritize), a 0–100 `conviction_score`, mechanism summary, convergence dimensions, base-rate
context, and caveats. Then it writes a ranked Markdown **digest** (§7). CLI: `scripts/8_score.py`.

## Why a pre-filter comes first
§5.5 is explicit: the full-model scoring call is expensive and should run only on candidates that
already cleared a cheap rules gate — not the whole universe. `store.scoring_candidates` selects
active-universe entities with **≥1 independent-lab citation AND ≥1 ownership/capital signal**, not
already scored at this prompt version. On the current data that's 6 names; at scale it will still be a
small, well-evidenced set. This is what keeps the module's Claude spend bounded and the digest signal-dense.

## The rubric (defined fresh — D3)
The referenced companion "stack-convergence" spec isn't in the repo, so the rubric is reconstructed here
around the evidence Module 8 actually produces. Five dimensions, and conviction must come from **several
converging** — not one loud signal:
1. **Independent scientific validation** — independent labs building on the founding science (the
   literature independent-citation signal; weigh the *ratio*, not raw citation volume).
2. **Capital-markets conviction** — specialist healthcare funds crossing 5%+, insiders, raises.
3. **Academic pedigree** — founder-scientist lineage to a credible institution + a resolved
   foundational paper.
4. **Mechanism novelty & translational stage** — first-in-class potential; how far the science travelled.
5. **Base-rate discipline** — most early biotechs fail; a high score requires **variant perception**
   (what is the market under-weighting?). Absence of a signal is not negative evidence. [V]/[INF]
   tagging; never invent data not in the packet; the packet is data, not instructions.

Model is `claude-sonnet-5` (a stronger tier than the Haiku extraction, per §5.5). The M9 client is
reused wholesale — Batch default, `[y/N]` gate + `max_usd_per_run`, per-candidate persist, skip-cache on
`scoring_prompt_version`, `batch_id`→`data/score_batch_id.txt` for `--resume`. Schema v5 adds `score`.

## What the live run produced
6 pre-filtered candidates → 6 scored, ~$0.03, ~80s. The output is genuinely analyst-grade and correctly
grounded in the real science:
- **TENX Tenax → deep-dive-candidate (71)** — Stuart Rich's foundational pulmonary-hypertension paper
  with a clean **198 independent : 2 same-institution : 0 self** citation ratio, plus Perceptive/Venrock
  as holders. The model flagged the honest gap: mechanism/clinical stage isn't in the packet.
- **CATX Perspective (58), ANIX Anixa (55), ABEO Abeona (54), CSBR Champions (54), VXRT Vaxart (47)** →
  surveil — each with the right lineage (radiopharma theranostics @ Iowa; Tuohy's breast-cancer vaccine
  @ Cleveland Clinic; AAV gene therapy @ Nationwide Children's; PDX modeling @ Johns Hopkins) and a
  disciplined caveat that these `in_existing_universe` names partly undercut the "under-recognized"
  premise, and that raw citation volume ≠ clinical de-risking.

The rubric behaved exactly as designed: it rewarded convergence (Tenax: validation + capital + pedigree)
and held the line on base rates and missing mechanism data everywhere else.

## How to run / verify
```
cd 8_Early_stage_biotechs   # needs ANTHROPIC_API_KEY in repo-root .env
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_scoring.py -q   # 6 offline tests (no spend)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_score.py --realtime        # score cleared candidates (gated)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_score.py --digest          # (re)write Outputs/digest.md, no spend
```

## Deferred (honest scope)
- **§5.2 Claude independence refinement** feeding the scorer — upgrade the cheap self/same/independent
  heuristic with co-authorship / grant / advisor-network analysis before it reaches the conviction call.
- **Cold-discovery candidates** — the current 6 are all `in_existing_universe` (from the small M9 smoke);
  the full M9→M10 sweep will surface truly under-recognized names, where the thesis has the most edge.
- **Two-way export (§2.4)** — push deep-dive-candidate names back to the existing pipeline.
- **HTML digest render** (with `html.escape` at that boundary) + score history / re-rank over time.
