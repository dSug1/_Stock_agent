# Module 8 — Unified Claude API dispatch (hard-pass + rescue, D40)

> **⚠ D40 ARCHITECTURE (2026-06-06):** M8 is no longer "rescue dispatch
> only" — it is now the **unified Claude API entry point for the entire
> pipeline**. The pre-D40 split (M7 = hard-pass Claude dispatch, M8 =
> rescue compute + rescue Claude dispatch) was collapsed into:
>
> | Step | Role | Billed? | Script |
> |---|---|---|---|
> | M7 | Rescue-eligibility classification (free pre-step; was M8.0 pre-D40) | free | `scripts/3_7_compute_eligibility.py` |
> | **M8** | **Unified Claude dispatch — submits TWO parallel Anthropic batches** (hard-pass with m7-v2 prompt + rescue with m8-rescue-v1 prompt) under ONE combined `[y/N]` gate, ONE cost estimate, wall time = max(hp, res) | BILLED | `scripts/3_8_claude_dispatch.py` |
>
> What's preserved from this spec (still 100% accurate): the rescue
> classification algorithm (§2), the D35 schema additions (§3.1), the
> rescue prompt preamble (§4.1), the date-retrieval task (§4.2), the
> two added JSON fields (§4.3), and all renderer changes (§5).
>
> What changed: the script layout (§3.2) and run flow (§6) reflect the
> D40 unification. The realized cost history (§7) is the pre-D40 M8
> rescue tally; new D40 runs will add a `dispatch_kind` field
> distinguishing HP vs RES anchors.
>
> Full rationale: [decisions.md § D40](decisions.md).

**Status:** **production, D40 unified architecture (2026-06-06).** Pre-D40 backfill state: 282 deep_dive rows across 7 runs (HP runs 1–4 + rescue runs 5–7). Run #6 covered the full 224-call rescue feed at 93% parse rate (14 of 15 errors are HARD-RULE-#8 catching stale BPC catalysts — real signal, not noise). 100% Claude-resolved-date coverage on rescue dispatches. **518 tests passing** (D40 added 0 new tests on top of D39's 11; D39's tests cover the coverage-gate that wraps both batches now).

**Spec date:** 2026-05-29 (initial D35) — updated 2026-06-06 (D40 unification).
**Decisions:** [decisions.md § D35](decisions.md) (initial M8 build) + [§ D36](decisions.md) (UI consolidation + bug fixes) + [§ D38](decisions.md) (ack-gate) + [§ D39](decisions.md) (coverage-gate) + [§ D40](decisions.md) (D40 unification + numerical-order renumbering).

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

### 3.2 Code layout (D40 unified)

```
src/module_8/
├─ __init__.py
├─ rescue_filter.py     — classify_catalyst(), classify_rows()
├─ config.py            — Module8Config (subclasses Module7Config)
├─ coverage.py          — D39 ticker-coverage gate (tickers_with_existing_dispatch,
│                         apply_coverage_gate)
└─ prompt.py            — load_rescue_prefix()

config/
├─ module_8.yaml                              — prompt_version_label='m8-rescue-v1'
└─ module_8_system_prompt_prefix.md           — date-retrieval preamble

scripts/
├─ 3_7_compute_eligibility.py                 — D40 — rescue classification
│                                                (was scripts/3_8_compute_rescue.py
│                                                pre-D40; renamed for numerical
│                                                execution order). Populates
│                                                catalyst_scores.rescued +
│                                                rescue_class. Free.
├─ 3_8_estimate_cost.py                       — D40 — UNIFIED cost preview
│                                                (HP subtotal + RES subtotal +
│                                                combined total). Single
│                                                Outputs/m8_cost_estimate.html.
└─ 3_8_claude_dispatch.py                     — D40 — UNIFIED Claude dispatcher.
                                                Fans out TWO parallel Anthropic
                                                batches via
                                                ThreadPoolExecutor(max_workers=2):
                                                  • HP feed → m7-v2 prompt
                                                  • RES feed → m8-rescue-v1 prompt
                                                ONE combined [y/N] gate; ONE
                                                combined cost ceiling check
                                                (max of cfg_m7 / cfg_m8).
                                                --resume-run N auto-routes by
                                                prompt_version. New flags:
                                                --skip-hard-pass, --skip-rescue.

src/module_7/context_pack.py                  — adds fetch_rescue_candidates()
                                              + augment_pack_for_rescue()
src/module_7/parsing.py                       — accepts optional
                                              claude_resolved_catalyst_date
                                              + catalyst_date_source fields
src/module_7/gate.py                          — D38 acknowledged-ticker gate
                                              (apply_ticker_gate,
                                              load_acknowledged_tickers,
                                              collapse_to_one_per_ticker)
scripts/3_6_render_scores.py                  — Catalyst tab (D36a merge);
                                              rescue chip + date 📅 indicator;
                                              D39 covered_tickers payload;
                                              D40-aware (no longer prints
                                              "M7 deep-dive" labels — both
                                              feeds appear under one "deep_dive"
                                              block in expand panels)
scripts/3_7_serve_selection.py                — live-price allowlist
                                              hard_pass=1 OR rescued=1;
                                              D39 #1 --render flag (single-process
                                              render + serve)
run_3_Biopharmcatalyst_parser.bat             — D40 strict numerical order:
                                              ... → M6 → M6.5 → M7 (free) →
                                              M8 (BILLED, one [y/N]) →
                                              D37 prune → D40 #2 auto-call to
                                              run_3_Biopharm_render.bat

# DELETED 2026-06-06 (D40):
#   scripts/3_7_deep_dive.py         (functionality merged into 3_8_claude_dispatch.py)
#   scripts/3_7_estimate_cost.py     (functionality merged into 3_8_estimate_cost.py)
#   scripts/3_8_compute_rescue.py    (renamed to 3_7_compute_eligibility.py)
#   scripts/3_8_rescue_dispatch.py   (functionality merged into 3_8_claude_dispatch.py)

tests/
├─ test_module8_rescue_filter.py              — 18 tests on classification
├─ test_module8_parsing_extension.py          — 5 tests on D35 fields
└─ test_module8_coverage.py                   — 11 tests on D39 coverage gate
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

## 5. Renderer changes (final state after D35 + D36)

The catalyst_scores HTML report (`scripts/3_6_render_scores.py`) gained these in D35 and was further consolidated in D36:

### Tab structure (D36a — merged from 3 → 2 tabs)
- **`Catalyst` tab** (default) — shows both hard_pass AND rescued rows in one view, sorted `hard_pass DESC, rescued DESC, composite_score DESC` so the original hard-pass set still leads.
- **`Excluded` tab** — `!hard_pass AND !rescued` (rescued rows leave Excluded automatically).
- State migration: any persisted `state.tab` in old localStorage values (`hard_pass`, `rescued`, `catalyst_date_defined`, `catalyst_date_undefined`) maps to `catalyst` on load.

### Per-row chip column (preserves visual signal)
- Hard-pass rows: timing-bucket chip (green/amber) carrying `precision_tier`.
- Rescued rows: rescue-class chip (purple monospace) carrying `A` / `BC` / `ABC` / etc.
- Both fit the same column width — no inline fail-chips clutter (those moved to the expand panel — see below).

### Pale-green "new" highlight + acknowledgement (D36b)
- Each row whose PK is not in localStorage `catalyst_acknowledged_v1` gets a `class="unack"` → pale-green background (`rgba(52,211,153,0.07)`), tinted on hover/expanded.
- Bootstrap on FIRST EVER load: seed the acknowledged Set with all current `hard_pass=1` PKs. So initially only the 231 rescued rows light up. Future BPC drops adding any new catalyst (hard_pass or rescued) automatically show as new.
- Expand panel top-left: an `.ack-toggle` checkbox. Tick → PK added to Set + saved + renderTable() → row drops the highlight. Untick → highlight returns.
- D31's expand-row reinsertion mirrors the `unack` class onto the inserted `tr.expand-row` so the panel also carries the tint.

### Date column (D35)
- Prefers `claude_resolved_catalyst_date` from the deep_dive payload over `date_min`, with a 📅 marker + hover-tooltip showing `catalyst_date_source`.
- Falls back to BPC's `date_min` for any catalyst without an M8 dispatch.

### Expand panel
- "Signal breakdown" kv: standard composite/insider/momentum/funds + (when rescued) a "Rescue class" line showing the rescue-class chip + a "Original gate failures" line listing the H-codes that triggered M8 admission.
- Insider trades + funds-breakdown sections shown when `r.hard_pass || r.rescued` (D35 widening of the old `r.hard_pass`-only gate).
- M7 deep-dive section unchanged (Claude analysis, target $, live-recompute).

### Filter row (D36d)
- `min composite` (numeric input)
- `insider buy` (any / required / none)
- `fund accum` (any / required / none)
- `stage` (any / per-stage)
- `search` (substring on ticker, name, drug)
- `review` (any / new only / acknowledged only) ← D36d, pinned just left of reset
- `reset` button — clears all filters back to defaults

### Table column widths (D36c — `table-layout: fixed`)
Sum = 100%; long content wraps inside its cell rather than expanding the column past viewport.

| # | Column | % | # | Column | % |
|---|---|---:|---|---|---:|
| 1 | # | 2.5 | 9 | Market cap | 6.5 |
| 2 | Ticker | 4.5 | 10 | Composite | 5 |
| 3 | Name | 10 | 11 | Insider | 5 |
| 4 | Drug | 12 | 12 | Momentum | 5 |
| 5 | Stage | 5 | 13 | Funds | 5 |
| 6 | Catalyst type | 8 | 14 | Probability | 4.5 |
| 7 | Date | 7 | 15 | Share-price-appr. | 6 |
| 8 | Precision/fail | 5 | 16 | Expectancy/week | 9 |

Header font: 11 px → 9.5 px with `white-space: normal` so multi-word headers wrap. Cell horizontal padding 8 px → 5 px.

### Live-price pipeline
- Server-side allowlist (`scripts/3_7_serve_selection.py::_refresh_hard_pass_tickers`) widened to `hard_pass=1 OR rescued=1`.
- JS poll filter (`pollLivePrices`) likewise widened to `r.hard_pass || r.rescued`.
- D35e disk-persistent cache at `data/render_price_cache.json` — first cold render still does the full ~290-ticker yfinance batch (~22s), subsequent renders within 30 min are ~4s. Two TTLs: 30 min for successes, 4 h for failures (delisted-ticker probes are slow).
- New CLI flags: `--no-fetch-prices` (skip yfinance entirely) and `--refresh-prices` (bypass cache; force fresh fetch).

### `bestPriceInfo` price-source priority (D35 + D36 follow-on)
Picks the freshest available source. Any non-BPC source gets the green ● dot + blue 'live-val' class.
```
livePrices[ticker]                  → 'live'              (60s JS poll)
r.yfinance_render_price_usd         → 'render_yfinance'   (D35e disk cache)
r.deep_dive.price_at_api_time_usd   → 'dispatch'          (M7/M8 dispatch-time)
r.price                             → 'bpc'               (BPC docx — no dot)
```

### Rescue legend (D35)
Shown only on the Catalyst tab. Explains the rescue chips, the H-gate eligibility rules, and what 📅 means.

---

## 6. Run flow (D40 unified)

```bash
# === M7 (free) — populate rescued/rescue_class on catalyst_scores ===
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_compute_eligibility.py
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_compute_eligibility.py --dry-run

# === M8 cost preview (free) — UNIFIED HP + RES + combined ===
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py --tickers TCRX,KURA
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py --classes A,B    # rescue feed filter only

# === M8 dispatch — BILLED, gated by ONE mandatory [y/N] ===
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py
# under the hood: ThreadPoolExecutor(max_workers=2) submits HP + RES batches in parallel;
# user sees one [y/N] gate with HP subtotal + RES subtotal + combined total.

# === D40 new flags — replay one feed in isolation ===
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --skip-rescue    # HP only
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --skip-hard-pass # RES only

# Single ticker (applies to BOTH feeds — useful for one-off re-dispatches):
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --tickers EVMN --yes

# Crash-recovery — auto-routes by prompt_version (HP vs RES writeback paths):
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --resume-run N

# Override gates (D38 ack-gate / D39 coverage-gate) — applies to BOTH feeds:
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --override-ack-gate
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --override-coverage-gate
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --one-drug-per-ticker

# Re-render the HTML (run_3_Biopharm_render.bat does this + opens browser):
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_render_scores.py
```

`run_3_Biopharmcatalyst_parser.bat` wires this automatically: M7 runs
unconditionally (free, no gate); M8 has ONE `[y/N]` gate. At end of the
bat, the orchestrator `call`s `run_3_Biopharm_render.bat` (D40 #2) so
the server boots and the browser opens to `catalyst_scores.html`
automatically.

---

## 7. Cost model + realized history

> The cost table below is the **pre-D40 rescue-only tally** (runs 5–7,
> 2026-05-29). Under D40 each `[y/N]` gate covers HP + RES combined, but
> per-call pricing math is unchanged — the unified dispatcher just sums
> the two subtotals into one ceiling check. New runs append to the same
> `deep_dive_runs` table; aggregating by `gate_config_json.dispatch_kind`
> recovers per-feed totals.

### Pre-flight estimator (231-catalyst rescue pool, batch mode)

| Scenario | USD |
|---|---:|
| no-optim | $32.85 |
| cache-only | $28.89 |
| **cache+batch (prod)** | **$15.57** |
| cost ceiling (`module_8.yaml`) | $50.00 ✅ |

Calibration factor 0.10 matches M7 (D22). Sync-mode forecasts over-estimate ~8× (safer direction).

### Realized M8 dispatch history

| Run | Date | Feed | Wall | Estimated | **Actual (calibrated)** | Notes |
|---|---|---|---:|---:|---:|---|
| 5 | 2026-05-29 | ZBIO + BHVN (4 API calls; 4 catalyst rows + 1 D23 copy) | 341 s | $0.28 | **$0.16** | First M8 dispatch. Verification + surfaced [render_join.py bug fix #3](decisions.md#bug-fix-3-renderer--render_joinpy-wasnt-passing-d35-fields-into-the-data-payload). 4/4 rows have Claude-resolved date. |
| 6 | 2026-05-29 | Full rescue feed (224 calls populating 212 rows; 4 cache-hits from run #5) | 466 s | $15.57 | **$9.08** | 209/224 parsed; 14 HARD-RULE-#8 stale-catalyst flags (saved-attention, not failure); 1 transient json_parse_fail (CRBU). 100% date-resolution rate. 957 web-search calls. |
| 7 | 2026-05-29 | CRBU retry | 218 s | $0.07 | **$0.04** | Recovered the run-#6 transient. Wall-time floor ~3.6 min for single-call M8 batches (Anthropic batch infra overhead). |
| **Totals** | | **229 API calls populating 217 rows** | | $15.92 | **$9.28** | |

**Calibration tracker:** ~1.7× safe over-estimate (estimate / actual = 1.74 for run #6). Leave the factor at 0.10 until invoices land.

### Implication for re-dispatch cost on next BPC refresh

Most rescued catalysts will cache-hit on re-run (the D17 catalyst-identity cache is keyed on `prompt_version` + drug signature; neither changes between renders). Only NEWLY-resocored catalysts cache-miss. Expected steady-state weekly cost: ~$0.50–$2 per BPC refresh after the initial $9.08 backfill.

---

## 8. Open questions / future work

- **Auto-rerun on BPC refresh.** When M1 ingests a new BPC docx, the
  rescue eligibility for existing catalysts may shift (e.g. a date now
  parses cleanly, demoting B back to hard_pass). The D40 rescue
  classifier `scripts/3_7_compute_eligibility.py` (was
  `scripts/3_8_compute_rescue.py` pre-D40) is idempotent and the
  orchestrator bat calls it post-M6.5 as M7, so this is handled
  automatically.
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
| `tests/test_module8_coverage.py` (D39) | 11 |
| **M8-specific total** | **34** |
| Full repo suite | **518 passed**, 4 skipped (no regressions through D35 → D36 → D38 → D39 → D40) |

**D40 added 0 new tests.** The unified dispatcher is a thin orchestrator
over the existing M7 dispatch + writeback layers (which the existing
suite already covers) and the existing M8 rescue layers; it adds a
`ThreadPoolExecutor` wrapper around two functions that were already
tested in isolation. The D40 follow-up #1 (bat parens-escape fix) was
verified with a hand-rolled CMD test rather than a pytest entry.

One M6 test (`test_score_snapshot_end_to_end`) was updated by [decisions.md § D36 bug fix #1](decisions.md#bug-fix-1-m6--composite_score-was-null-for-hard-fail-rows): the assertion that hard-fail rows have `composite_score IS NULL` is reversed — they now have a non-null composite in [0, 100] so the Rescued half of the merged Catalyst tab can sort/display by composite.

---

## 10. Operational learnings from runs 5–7

### HARD RULE #8 catches stale BPC catalysts (run #6 — 14 of 15 errors)

When Claude finds primary-source evidence that a catalyst has already happened (or been canceled) before the BPC snapshot, the m7/m8 prompt's HARD RULE #8 fires and the row writes to `deep_dive_errors` with `error_kind='catalyst_already_passed'` and a human-readable note. **These aren't failures — they're free quality signal.** Run #6 surfaced 14 such catalysts that BPC was still tracking as future:

| Ticker | Stale reason |
|---|---|
| TSVT | Asset divested to Regeneron (APA closed 2024-04-01) |
| LIXT | Data already presented at SGC Puerto Rico 2026-04-13 |
| LTRN | Type C meeting outcome already announced via BusinessWire |
| ALGS ×2 | EASL 2026 oral already presented 2026-05-27 |
| PBYI | Ph2 ALISCA-Breast already presented |
| LPCN | ASCP presentation occurred May 26-27, 2026 (before snapshot) |
| BCDA | CardiAMP CMI data at EuroPCR 2026-05-21 |
| GLPG | Program CANCELED per 6-K dated 2026-01-05 |
| AQST | AQST-108 Ph1 topline released 2026-05-13 |
| QURE | EPISOD1 prelim data discontinued |
| IRWD | LINZESS PDUFA approved 2026-05-28 |
| SDGR | SGR-3515 data already at AACR 2026 |
| DTIL | EASL late-breaker poster 2026-05-27 (= snapshot date) |

These rows stay in the Catalyst tab but with a faded representation (M7/M8 fields blank, error chip in expand panel). The user won't waste time researching them.

### Test before scaling: the run #5 → run #6 lesson

The 2-ticker run #5 ($0.16) caught a critical bug ([render_join.py wasn't passing D35 fields](decisions.md#bug-fix-3-renderer--render_joinpy-wasnt-passing-d35-fields-into-the-data-payload)) that would have invisibly broken the 📅 marker for the full 224-row rescue payload from run #6. Cost of catching it small: $0.16. Cost of NOT catching it: 224 rows × 22 KB of unused Claude output + a confused user wondering why no markers appeared. **Pattern: always test new prompt paths on 2–5 representative tickers before scaling.**

### High-conviction picks surfaced (the M8 ROI)

Run #6 surfaced 6 picks with `exp/wk ≥ 6.0%` that the pre-M8 hard-pass-only pipeline would never have shown:

| Ticker | Indication | p_final | exp/wk | Weeks | Reason it was rescued |
|---|---|---:|---:|---:|---|
| **CING** | ADHD (peds + adult, 505(b)(2)) | **0.66** | **+26.89%/wk** | 1 | B-class (date within 14d of snapshot) |
| APRE | PPP2R1A-mutated uterine serous | 0.50 | +9.73%/wk | 1 | B-class |
| IMRX | 1L metastatic pancreatic cancer | 0.57 | +7.14%/wk | 1 | B-class |
| CNTX | Platinum-resistant ovarian | 0.59 | +7.01%/wk | 3 | B-class |
| REPL | RP2 + nivo metastatic uveal melanoma | 0.53 | +6.97%/wk | 1 | B-class |
| CGEM | Rheumatoid Arthritis | 0.58 | +6.74%/wk | 1 | B-class |

CING is the standout: sub-$5 stock, 66% p_final, 1-week catalyst — exactly the kind of trade the M8 rescue path was designed to surface. The pre-D35 pipeline would have left this in the Excluded tab forever.

### Competitor audit (one-off, retained as audit pattern)

A separate audit script (`_tmp_audit_competitor.py` + `_tmp_audit_reverse.py`, deleted after use) compared the BPC + M8 stack against a competitor 14-day catalyst list. Result: our DB covers **~4× more small-cap catalysts** in any given 14-day window. The competitor's apparent depth was concentrated in big-pharma assets that the H1 mcap < $2B gate rejects by design. **Conclusion: stay with BPC + M8 for the small-cap tier.** Audit scripts can be recreated for any future competitor evaluation.

### Pre-dispatch gates (D38 ack + D39 coverage) — apply to BOTH feeds (D40)

Per [decisions.md § D38](decisions.md) + [§ D39](decisions.md), the D40
unified dispatcher (`scripts/3_8_claude_dispatch.py`) and unified
estimator (`scripts/3_8_estimate_cost.py`) apply the same gate stack to
BOTH the hard-pass feed and the rescue feed before drug-group dedup:

1. **D38 ack-gate** — loads `data/acknowledged_tickers.json`, drops any
   candidate whose ticker the user has marked as reviewed in the HTML.
   Bypass per-call with `--override-ack-gate`.
2. **D38 `--one-drug-per-ticker`** (optional) — collapses multi-drug
   catalysts to one candidate per ticker (deterministic: smallest drug
   name alphabetically).
3. **D39 coverage-gate** — drops any candidate whose ticker has at
   least one prior `deep_dives` row (M7-format or M8-format dispatch
   counts). Bypass per-call with `--override-coverage-gate`.

Gates run case-insensitively on ticker. D23 drug-group dedup happens
AFTER the gates. A ticker with 5 catalysts in either feed → 0
dispatches if any gate hit it. The cost estimator mirrors the same
gate stack so the preview reflects what would actually dispatch.

### Retention interaction with M8 data (D37)

Per [decisions.md § D37](decisions.md), `scripts/3_prune_old_data.py` applies quarterly:
- **`deep_dives`** is on the **never-prune** list. Every M8-dispatched analysis (including run #5/6/7) is durable. No reduction in audit value over time.
- **`deep_dive_runs`** is on the **never-prune** list. Cost ledger + batch_id + gate_config_json stay forever.
- **`deep_dive_errors`** has a **90-day TTL**. The 14 HARD-RULE-#8 `catalyst_already_passed` rows from run #6 age out after Aug 27 2026. If you want them kept longer for post-hoc analysis of BPC-stale-catalyst rates, raise `--error-ttl-days` on the prune CLI.
- **`web_search_cache`** has a **90-day TTL**. The 957 URLs from run #6 age out after Aug 27 2026. Re-dispatches don't reuse these (each new prompt_version stores fresh), so the TTL has zero functional impact — purely a disk-space saver.
- **`catalyst_snapshots` / `_timing` / `_scores`** keep the **top-3 snapshots per PK**. A rescued catalyst that drops out of the rolling-view (e.g., its date_max passes, or BPC stops publishing it) won't appear in the renderer regardless — but the most recent 3 of its catalyst_scores rows stay queryable for audit even after it leaves the live UI.
