# M17 — Post-mortem fixes, folded into v0.4 (explained)

*Seventh v0.4 milestone. The four data-quality/behaviour defects from the first-run post-mortem, fixed now
that the rubric/model are rewritten (fixing them earlier would have been thrown away). Built 2026-06-30.*

## The four fixes

### 1 & 2 — Absent/artifact dimensions no longer read as negative evidence
Post-mortem: the stubbed `search` feed and thin/artifact GDELT `media` were entering the prompt as
*negative* dimension scores (e.g. search −0.30, media −0.6), depressing `p_up` — a "rule 10" violation
(a signal you can't find ≠ evidence against). Fix:
- `features.search_features` / `media_features` now emit **`no_data` (score `None`)** when there is no usable
  feed (no history, or recent article volume below `harvest.media_min_volume`) — never a negative number.
- `rubric._clean_dim` collapses such a dimension to `{"status": "no_data"}` in the bundle, and the system
  prompt instructs Claude to treat it as **absent and coverage-reducing, scored ~0, never bearish**, and to
  prefer its own web_search over a pipeline score it suspects is an artifact.
- Coverage (`stage5_blend._data_coverage`) already keys off `surge_ratio is not None`, so a `no_data`
  dimension correctly lowers confidence instead of faking bearish evidence.

### 3 — The Opus tier is no longer dead
Post-mortem: the symmetric contested band `[0.45, 0.65]` on `p_claude` caught **nobody** once `p_claude`
compressed below 0.45. Fix: `stage3_score` now escalates the **actionable** survivors — those with
`p_claude ≥ finalize_min_p` (0.45), ranked by `p_up` and capped at `finalize_max_names` (6) — so Opus
adversarially vets the names we'd actually go long, bounded for cost. (The variant-perception rubric, M14,
should also decompress `p_claude`.)

### 4 — Mega-cap discovery drift is flagged
Post-mortem: discovery drifted to heavily-institutional mega-caps (NVDA/MU/ILMN…) against Decision C's
retail-heavy intent. Fix: `stage0_gate` adds a **`megacap`** status when `$-ADV > universe.max_adv_usd`
(~$2B/day) — flag-not-drop (the cardinal rule), reversible via config (`0` = disabled). `$-ADV` is a crude
low-float proxy; a proper ownership/float feed is a later provider.

## How to run / verify
```
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_m17_fixes.py -q
```
Full suite: **151 passing** (was 145; +6 new, 2 existing updated to the new semantics — an absent feed is
`no_data` not 0, and Opus vets the actionable top not a dead band).

## Honest limits
- The MRNA-style GDELT artifact (real-but-wrong-entity coverage with a spurious negative tone) isn't fully
  code-detectable; the `media_min_volume` floor catches near-empty coverage, and the rubric is told to
  distrust suspected artifacts — a proper GDELT query-precision fix is a data task (handoff §8).
- `$-ADV` mega-cap proxy is coarse (ILMN/GME have similar ADV); a float/ownership feed would be exact.

## What's NOT here (next)
- **M18** — outputs polish (days-to-catalyst + our_view in `signals.md`, ledger Brier + regime header in the
  HTML). Then v0.4 is feature-complete and the natural next step is a live `--dispatch` run.
