# M5 explained — Stage 4: tiered Claude scoring

*What milestone 5 does. Companion to `SPEC_acrivon_pattern_screener.md` §9–§10 and `decisions.md` D1.*

---

## In one sentence

M5 takes each retained company's evidence bundle, has Claude **map its activity onto the in-scope
mechanisms and score how genuinely it embodies the Acrivon pattern** (proprietary data engine +
inference + external validation + mechanism + translational bridge), and ranks the universe by a
code-computed composite — using a cheap→expensive model funnel so the whole run costs a few dollars.

## Why there's no embedding stage

The spec's Stage 3 embedding pre-rank was a cost-control cut before expensive Claude scoring. At ~589
companies it isn't needed — a full Claude run over *everything* is ~$2.57. So we skipped it and score
directly (see `decisions.md` D1). The cheap Haiku tier does the recall-safe first cut instead.

## The three tiers (spec §9.1)

| Tier | Model | Over | Does |
|---|---|---|---|
| 1 — triage | **Haiku 4.5** | every live company (~589) | cheap keep/kill + 0–1 prior; recall-safe ("when unsure, KEEP"); reads full evidence |
| 2 — rubric | **Sonnet 4.6** (Batch) | triage survivors (~294) | the full §9.3 rubric JSON — the real score |
| 3 — finalize | **Opus 4.8** | contested band [0.55, 0.75] (~59) | adversarial re-score ("argue against it first") |

Batch API halves the Sonnet cost; the shared rubric system prompt is **prompt-cached** so it's billed
once, not N times. Full-universe estimate ≈ **$3 batch / ~$6 real-time** (`--no-batch` ≈ 2× the
Sonnet/Opus tiers), vs the `max_usd_per_run: 50` ceiling. **Real first run (10 tickers, real-time):
$1.32** (incl. a wasted timed-out attempt; clean ~$0.82) — the estimate knobs are calibrated to it.

## First real dispatch (2026-06-28) — the rubric validated

Ran on 10 tickers (`--tickers … --dispatch --yes --no-batch`). The scores differentiate exactly as
the Acrivon pattern intends:

| Ticker | composite | A B C D E | moat / substance | read |
|---|---|---|---|---|
| **ACRV** | **0.904** | 5·3·5·4·5 | data / substantive | the calibration anchor — **hit the Appendix B targets** (B capped at 3, data moat, InViKA C=5, OncoSignature E=5) |
| GRAL | 0.760 | 5·3·4·**1**·5 | data / substantive | real data moat + companion Dx, but no specific mechanism (D=1) |
| ABSI | 0.704 | 4·4·4·2·3 | mixed | generative-AI antibody design (B=4) |
| RXRX | 0.62–0.66 | 4·4·3·3·2 | mixed / **mixed** | strong platform, unproven translation |
| **SDGR** | 0.462 | 3·3·4·2·3 | **architecture** / mixed | physics engine flagged commoditizable → **penalized** (the data-vs-architecture lesson) |
| SANA | 0.402 | 3·**1**·3·3·3 | architecture | cell therapy, no AI engine |

Plus 4 pure-diagnostics/conventional names triaged out by Haiku. Two caveats on that run: OpenAlex
rate-limited the session (publications/pedigree empty — re-harvest to populate), and ACRV/RXRX appear
twice (seed-CSV vs SEC name → two company-ids; the Stage-5 dedup fixes it).

## What one call reads and returns

**Input** (`rubric.build_bundle`): a compact ~900-token JSON carrying the full evidence set —
identity + cap + business description + Stage-1 `ta_tags`, and the harvested signals:
- **publications** — OpenAlex works / h-index / top research concepts
- **pedigree** (§5.6.1) — the company's top OpenAlex authors (by h-index) + any **prestige-awardee
  matches** (Nobel / NAS / Lasker / Breakthrough-Prize / foundation-model-in-bio names from
  `config/prestige_labs.yaml`) — the founder/SAB pedigree signal, the hardest-to-fake tell
- **patent_estate** — counts split method/platform vs composition-of-matter (the moat shape)
- **clinical** — ClinicalTrials phases / conditions / biomarker-Dx language
- **fda_designations** — Breakthrough Therapy/Device, RMAT, Fast Track, Orphan, PRIME… scanned from
  trial text + the description (the "awards & recognitions" axis)

**System prompt** (`rubric.build_rubric_system`): the §9.5 skeptical-analyst rubric + the Acrivon
calibration anchor + the **37-mechanism controlled vocabulary** (so `D_mechanism.mechanism_ids` uses
valid ids), plus explicit rules to weigh **founder pedigree** as a high-precision lift on A, treat
**FDA breakthroughs/awards** as corroboration (not proof) for C/E, and weight **method/platform
patents** over composition. **Output** (structured-outputs JSON, §9.3): the five axes A–E (0–5), plus
the two adversarial calls — `moat_location` (data vs commoditizable architecture) and `substance_check`
(substantive vs marketing, naming the disconfirming evidence) — plus a memo that names any pedigree
recognition.

**Fairness to young companies (rubric rule 9):** the prompt tells Claude *not* to penalize an
early/recently-public platform for thin clinical or translation evidence — score the platform
(A/B/pedigree/validation); a low E only modestly lowers a clearly-young company. Youth doesn't drag
the composite (analyst request 3a). The complementary "deprioritize old / ship-sailed" preference is
the optional Stage-5 lifecycle multiplier (decisions.md D2), not the score.

The mechanism mapping (axis D) is the *judgment* refinement of Stage 1's keyword `ta_tags`: Claude
reads the evidence and assigns the real mechanism(s), e.g. recognizing a diagnostics company anchors to
MHC-I antigen presentation even when no keyword fired. But mapping alone doesn't separate a real
platform from an "AI-pharma shell" — that's what A (proprietary data), C (external validation), and the
moat/substance checks decide.

## Composite & confidence — computed in code, not trusted from the model (§10)

`composite.compute_composite` does the weighted, normalized Σ wᵢ·(scoreᵢ/5) over A–E, then applies the
penalties: ×0.75 if the moat is a commoditizable *architecture*, ×0.50 if substance is *marketing*.
`compute_confidence` rises with evidence completeness and Opus-finalization, falls on a *mixed*
verdict. The model's own arithmetic is never trusted — only its judgments are.

## Cost gate (spec §14)

`scripts/6_screen.py --stage 4` prints the **cost estimate and stops** (no API). Adding `--dispatch`
shows the estimate, asks `[y/N]`, and only then calls the API. `AnthropicClient` tracks running spend
and raises `BudgetExceeded` past `max_usd_per_run`. The key is read from `.env`, never logged.

```bat
scripts\6_screen.py --stage 4                                  :: estimate only (free)
scripts\6_screen.py --stage 4 --dispatch                       :: estimate -> [y/N] -> score (Batch)
scripts\6_screen.py --stage 4 --tickers ACRV,IDYA --dispatch --yes --no-batch  :: a few, real-time
scripts\6_screen.py --stage 4 --dispatch --force-rescore       :: re-score even within the TTL
```
Flags: `--tickers` restricts the set; `--no-batch` scores the Sonnet pass in real time (faster for a
handful, ~2× cost); `--yes` skips the gate; `--force-rescore` overrides the rescore-TTL.

## Don't repeat within a year (rescore-TTL)

A ticker scored within `rescore_ttl_days` (default **365**) is **skipped** on the next run — no repeat
analysis, no repeat charge (`stage4._scored_within` + `store.last_scored_at`). `--force-rescore` is the
reset. The pre-dispatch estimate counts only **due** tickers, so re-running the same set shows a tiny
cost (verified: 13 → 5 due after one run).

## Recall posture

Candidate set = **all live companies** (`score_live_excluded: true`) that are **due** (past the
rescore-TTL). Stage 1's description-only exclusion does **not** gate scoring — the Haiku triage (which
sees full evidence) is the cut instead. So a company Stage 1 parked for lack of a keyword still gets a
real read here. `ta_tags` are just one input field that D_mechanism refines.

## What's proven (tests)

`tests/test_scoring.py` (**98 total** in the component), all offline: bundle assembly; the rubric
system prompt carries the vocab + pedigree/FDA guidance; rubric validation (rejects >5 scores / bad
enums / missing axes); composite math + both penalties; confidence monotonicity; the cost estimate
scales and stays under the cap; **rescore-TTL skips a recently-scored ticker and `force` re-includes
it**; and Stage-4 tier routing end-to-end against a **fake client** — triage kills the non-match,
Sonnet scores survivors, the contested one is Opus-finalized, scores persist + audited. The live API
is never called in tests or during build — only the user-gated `--dispatch` makes real calls (one real
10-ticker run was done: see above).

## Not yet built

Stage 5 (rank/dedup/export to the analyst's house format) and the §13 seed-eval harness
(precision/recall on Acrivon/Tango/IDEAYA/Boundless vs shells) are the next milestones — and the
seed-eval is what tells you whether the scores can be trusted before acting on them.
