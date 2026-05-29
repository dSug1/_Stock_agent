# Module 8 — Catalyst rescue + re-dispatch

**Status:** built 2026-05-29 (D35). Schema migrations live; rescue
compute populated 231 catalysts; dispatch + estimate scripts wired;
renderer surfaces a "Rescued" tab; tests passing (23 new + 445 prior).

**Spec date:** 2026-05-29
**Decision:** [decisions.md § D35](decisions.md)

---

## 1. Purpose

M6 rejects ~83% of BPC catalysts via H1-H6 hard filters before scoring.
A subset of those rejections are **false negatives in the user's
investing thesis**:

- Sub-$30M micro-caps may be the most asymmetric biotech bets (H1 fail).
- Catalysts with imminent dates or missing BPC dates are precisely the
  ones where independent date resolution is most valuable (H3 fail).
- Regulatory Decision events (PDUFA) are binary catalysts even though
  their `stage`/`type` don't match the standard data-readout enum (H5
  fail).

Module 8 re-admits these into the Claude deep-dive feed, with a separate
prompt-version label so Anthropic's prompt cache stays isolated from M7
hits. For B/C rescues, Claude is asked to first resolve the catalyst
date from primary sources (per M7's EVIDENCE HIERARCHY), then score.

H2 (no timing precision at all) and H6 (delisted ticker) are NOT
rescued: those rows stay hard-excluded.

---

## 2. Rescue classification (D35)

| Class | Filter | Trim | Count (2026-05-29) |
|---|---|---|---:|
| A | H1 fail AND mcap ∈ [$0, $2B] | full | 43 |
| B | H3 fail AND H1 pass | full (incl. H4 overlap) | 171 + 5 BC |
| C | H5 fail AND H1 pass | `Regulatory Decision` OR NULL type only | 12 + 5 BC |
| **Total unique** | | | **231 catalysts / 177 tickers** |

A catalyst can match multiple classes simultaneously — the resulting
`rescue_class` string concatenates letters in sorted order
(`A`/`B`/`C`/`AB`/`AC`/`BC`/`ABC`).

**H4 overlap (catalyst window in past):** per user decision, NOT
filtered out — Claude's HARD RULE #8 catches these as
`catalyst_already_passed` errors and they land in `deep_dive_errors`.
Costs ~$0.04 per false-positive call but preserves the audit signal.

**C scope trim:** User decision was to drop `Submission`, `End of Phase
Meeting`, and `phase0` Conference Presentations from C — these are
rarely binary near-term catalysts and Claude has no good scoring
framework for them. Only `Regulatory Decision` (PDUFA) + NULL
`next_catalyst_type` rows survive.

---

## 3. Architecture

### 3.1 Schema additions (additive only)

**`biotech.db.catalyst_scores`** (added via `database/db.py::_apply_additive_migrations`):
- `rescued INTEGER DEFAULT 0` — convenience boolean
- `rescue_class TEXT` — 'A' / 'B' / 'C' / 'AB' / 'AC' / 'BC' / 'ABC'

**`claude_deep_dives.db.deep_dives`** (added via `module_7/deep_dives_db.py::_ADDITIVE_MIGRATIONS`):
- `claude_resolved_catalyst_date TEXT` — ISO date from M8 rescue (B/C)
- `catalyst_date_source TEXT` — citation string for the date
- `rescue_class TEXT` — copied from `catalyst_scores.rescue_class`

`deep_dive_runs.mode` continues to accept `'sync'` or `'batch'`; M8 runs
are tagged via `gate_config_json.dispatch_kind = 'm8_rescue'`.

### 3.2 Code layout

```
src/module_8/
├─ __init__.py
├─ rescue_filter.py     — classify_catalyst(), classify_rows()
├─ config.py            — Module8Config (subclasses Module7Config)
└─ prompt.py            — load_rescue_prefix()

config/
├─ module_8.yaml                              — prompt_version_label='m8-rescue-v1'
└─ module_8_system_prompt_prefix.md           — date-retrieval preamble

scripts/
├─ 3_8_compute_rescue.py                      — populates catalyst_scores.rescued
├─ 3_8_estimate_cost.py                       — cost preview (no spend)
└─ 3_8_rescue_dispatch.py                     — Claude API call (BILLED)

src/module_7/context_pack.py                  — adds fetch_rescue_candidates()
                                              + augment_pack_for_rescue()
src/module_7/parsing.py                       — accepts optional
                                              claude_resolved_catalyst_date
                                              + catalyst_date_source fields
scripts/3_6_render_scores.py                  — new "Rescued" tab + rescue
                                              chip + date 📅 indicator
scripts/3_7_serve_selection.py                — live-price allowlist now
                                              hard_pass=1 OR rescued=1
run_3_Biopharmcatalyst_parser.bat             — calls 3_8_compute_rescue +
                                              gated 3_8_rescue_dispatch

tests/
├─ test_module8_rescue_filter.py              — 18 tests on classification
└─ test_module8_parsing_extension.py          — 5 tests on D35 fields
```

### 3.3 Reused M7 machinery (D35 design rule: M8 ⊂ M7)

M8 reuses without copy-paste:
- `module_7.dispatch.{submit_batch, poll_and_collect_batch, dispatch_sync}` — Anthropic API plumbing
- `module_7.cache.{group_candidates_by_drug, partition_drug_groups_by_cache}` — D23 drug-level dedup + D17 catalyst-identity cache
- `module_7.cost_estimate.estimate_cost` — three-scenario estimator + cost-ceiling guard
- `module_7.parsing.parse_deep_dive` — same 10 HARD RULES + the two new optional fields
- `module_7.scoring.compute_expectancy` — same modifier math; M8 just feeds Claude's resolved date into `weeks_to_catalyst`
- `module_7.deep_dives_db.*` — same DB tables, just additional columns
- `module_7.live_price.get_live_prices` — same yfinance fetch at dispatch time

The ONLY pure-M8 logic is `rescue_filter.classify_catalyst()`, the
prompt preamble file, and the wiring scripts.

---

## 4. Prompt engineering

### 4.1 Preamble structure

```
[M8 rescue preamble]   ← config/module_8_system_prompt_prefix.md
[M7 system prompt]     ← config/module_7_system_prompt.md (unchanged)
[M7 few-shots]         ← config/module_7_few_shots.md     (unchanged)
                         ↑ single ephemeral cache_control breakpoint
```

The preamble lands BEFORE the cached block, so edits to it correctly
invalidate the prompt cache (mixing the preamble file's SHA into
`prompt_version` enforces this — see `module_8.config::load_module_8_config`).

### 4.2 Date-retrieval task

For B/C rescues, the preamble instructs Claude to walk this priority
order BEFORE scoring:

1. SEC filings & company press releases (10-K, 10-Q, 8-K, S-3, PR wire)
2. Regulatory primary (fda.gov, clinicaltrials.gov `primary_completion_date`)
3. Peer-reviewed journals + conference proceedings (ASCO, ASH, AACR)
4. Industry trade press (Fierce, Endpts, BioSpace)

If no tier 1-4 source supplies a date, Claude falls back to a best-guess
month/quarter from the most authoritative public signal AND marks
`catalyst_date_source` with `"best-guess: <evidence>"`. As a last
resort, Claude uses snapshot+12mo midpoint with `"unable to resolve —
placeholder +12 months from snapshot"`. **Claude never refuses to score
in rescue mode** (per user decision); a human reviewer triages
no-source flags from the renderer.

### 4.3 Two new output fields

The standard M7 JSON schema is extended with:
```json
{ ... all M7 fields ...,
  "claude_resolved_catalyst_date": "YYYY-MM-DD",
  "catalyst_date_source": "<short citation, ≤200 chars>"
}
```

The M7 parser (`parsing.py`) was extended to accept these as **optional
strings** — regular M7 responses omit them entirely without violating
the schema.

---

## 5. Renderer changes

The catalyst_scores HTML report (`scripts/3_6_render_scores.py`) gains:

- **New "Rescued" tab** between Hard pass and Excluded.
- **Rescue-class chip** (purple, monospace) on each rescued row, e.g.
  `A`, `BC`, `ABC`.
- **Original fail chips** rendered at 55% opacity next to the rescue
  chip so the user can see WHY it was rescued.
- **Date column** prefers `claude_resolved_catalyst_date` over
  `date_min`, with a 📅 marker + hover-tooltip showing the source.
- **Excluded tab** filter changes from `!hard_pass` to
  `!hard_pass AND !rescued` so rescued rows leave Excluded automatically.
- **Rescue legend** shown only on the Rescued tab (mirrors the H-gate
  legend pattern on Excluded).
- **Live-price polling** widened from `hard_pass=1` to
  `hard_pass=1 OR rescued=1` (server allowlist + JS filter both updated).
- **Sort default** now `hard_pass DESC, rescued DESC, composite_score DESC`
  so hard-pass rows still lead.

---

## 6. Run flow

```bash
# Free — populate rescued/rescue_class on catalyst_scores
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_compute_rescue.py

# Free — preview cost before dispatching
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py

# BILLED — gated by mandatory [y/N] (per D19; --yes bypasses)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_rescue_dispatch.py

# Filter to one class:
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_rescue_dispatch.py --classes A

# Single ticker:
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_rescue_dispatch.py --tickers EVMN --yes

# Crash-recovery:
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_rescue_dispatch.py --resume-run N

# Re-render the HTML to populate the Rescued tab
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_render_scores.py
```

`run_3_Biopharmcatalyst_parser.bat` wires this automatically after M7:
M8.0 compute is unconditional (free); M8.1 dispatch is gated by `[y/N]`.

---

## 7. Cost model

Pre-flight estimate against the current 231-catalyst rescue pool:

| Scenario | USD | Notes |
|---|---:|---|
| no-optim | $33.43 | every input billed at list rate |
| cache-only | $29.41 | 81% cache_read ratio |
| **cache+batch (prod)** | **$15.84** | batch_discount=0.50 on top of cache |
| cost ceiling (`module_8.yaml`) | $50.00 | WITHIN budget |

Calibration factor 0.10 matches M7 (D22). Sync-mode forecasts will
over-estimate by ~8× (acceptable safer direction).

---

## 8. Open questions / future work

- **Auto-rerun on BPC refresh.** When M1 ingests a new BPC docx, the
  rescue eligibility for existing catalysts may shift (e.g. a date now
  parses cleanly, demoting B back to hard_pass). `3_8_compute_rescue.py`
  is idempotent and the orchestrator bat calls it post-M6, so this is
  handled automatically.
- **Cache invalidation on rescue-class shift.** If a catalyst's
  rescue_class changes between runs (A → AB), the deep_dive cache hit
  is still valid because `prompt_version` doesn't depend on
  rescue_class — same prompt, same expected output. The rescue_class is
  metadata only.
- **Per-class cost tracking.** `deep_dive_runs.gate_config_json` carries
  `dispatch_kind='m8_rescue'` + `classes_filter`. A future post-hoc
  query could aggregate cost-per-class to retune the C scope decision.
- **Module 9 (iOS dashboard)** is the renumbered ex-M8. Not built yet.
- **`config/module_7.yaml::modifiers.momentum`** is still present as
  inert config — removable in a future config-only edit (per D33's
  "future cleanup, low priority" item).

---

## 9. Test coverage

| File | Tests |
|---|---:|
| `tests/test_module8_rescue_filter.py` | 18 |
| `tests/test_module8_parsing_extension.py` | 5 |
| **M8 total** | **23** |
| Full repo suite | 468 passed, 4 skipped (no regressions) |
