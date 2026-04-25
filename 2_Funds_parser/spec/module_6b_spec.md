# Module 6b — Post-scoring refinement + user-driven selective dispatch

**Status:**
- **Part (b)** — User-driven selective dispatch + mandatory gate: ✅ **Implemented 2026-04-25.** Storage architecture revised same day from "HTML `checked`-attribute as truth + File System Access save" (D48) to "**local HTTP server + sidecar JSON file as truth**" (D49). See [Implementation notes](#implementation-notes-part-b) at the end.
- **Part (a)** — Composite score modifier: 📋 Draft specification, not yet implemented (next session).

**Last updated:** 2026-04-25
**Runtime:** Two parts.
- **(a) Composite score modifier** — pure-Python, deterministic, no external API calls. Reads only from `llm_scores.db` (M6 output).
- **(b) Selective-dispatch control** — read user's per-ticker selection from the rendered HTML report, gate Module 6's Anthropic dispatch to only those tickers, with a mandatory `[y/N]` confirmation.

Module 6b has two responsibilities, both downstream of M6's LLM dispatch:

1. **Composite score modifier (post-LLM).** Apply a deterministic multiplier to the M6 primary score so that material signals already present in the LLM's research brief — competitive crowding, financing risk, insider conviction, mgmt track record, recent failures, etc. — actually move the ranking.

2. **User-driven selective re-dispatch (pre-LLM).** Render the final-ranking HTML with per-ticker checkboxes (default checked) plus a "select all" master toggle. Before any subsequent Anthropic call, the pipeline reads the user's selection from the saved HTML, restricts the dispatch to checked tickers, and shows a `[y/N]` cost-confirmation gate (always — no `--yes` bypass).

**The composite modifier rationale:** The score formula in D46 is mathematically clean but only uses 4 LLM-emitted fields (`target_price_usd`, `time_to_catalyst_weeks`, `probability`, plus `current_price_usd` from M5). The remaining ~15 structured fields the LLM produces are stored for audit but invisible to the rank.

**Concrete motivating cases (see [decisions.md § D47](decisions.md)):**
- **NTLA crowded HAE space** — 5 commercial competitors (3 approvals 2025); the model's −10pp probability cut is bounded by the [0.15, 0.90] range and invisible to scoring.
- **TCRX Lynx1 insider buy** — $30M PFW purchase at 37% premium Dec 2024 by a 10% owner, never reflected in score.
- **NTLA MAGNITUDE patient death + clinical hold (Oct 2025)** + workforce cut — recent operational stress invisible to scoring.

**The selective-dispatch rationale:**
- Once the user has reviewed the first M6 run's HTML report, they often want to **re-score only a focused subset** (e.g. drop tickers that look obviously broken; re-validate borderline calls). Without per-ticker selection, the user has to either re-score everything (paid) or hand-craft `--tickers TCRX,NTLA,…` flags from memory.
- Selection state must persist somewhere accessible to the pipeline. The HTML file itself becomes the source of truth: checkboxes are HTML `<input>` elements, the "Save selection" button writes the modified HTML back to disk, and the pipeline parses the HTML on the next run.
- A mandatory confirmation gate (already enforced by `scripts/6_score.py`, never bypassed even when `stdin` is non-interactive) is part of the M6b contract: the user is reminded of cost and selected ticker count before any billed call.

---

## Role and contract

**Reads.** `llm_scores.db` (M6 output) — `llm_scores.research_brief_json` + the per-horizon scoring fields. Optionally `config/scoring_modifier.yaml` *(new)* for tunable factor maps.

**Writes.** Same DB, same primary keys — additive columns:
- `llm_scores.score_modifier` (REAL)
- `llm_scores.score_modifier_json` (TEXT — per-component breakdown for audit)
- `llm_scores.score_at_current_adjusted_pct_per_month` (REAL — `score_at_current × score_modifier`)
- `final_rankings.score_modifier` (REAL)
- `final_rankings.final_score_adjusted` (REAL)
- `final_rankings.score_modifier_json` (TEXT)

**Ranking key change.** `final_rankings.final_rank` is sorted by `final_score_adjusted` (NOT `final_score`). `final_score` (the raw `score_at_current` at `final_horizon`) is preserved as a reference column.

**Does not write.** `context_packs.db` (read-only), `2_fundparser.db` (untouched), `data/prices.db` (untouched).

---

## Where M6b runs in the pipeline

Three call sites, all in-process (no separate dispatch):

1. **End of `scripts/6_score.py`** — after M6 parses + writes `llm_scores` rows + builds `final_rankings`, run `apply_modifiers_to_run(conn, run_id, quarter)` before rendering reports. Single workflow; user sees adjusted scores in the same run.
2. **End of `scripts/6_recompute_scores.py`** — after the D46 score recompute, also recompute modifiers. Lets historical runs benefit from any factor-map tuning.
3. **Standalone CLI `scripts/6b_apply_modifiers.py`** — for retroactive application across all runs / quarters when only the modifier logic changes (e.g. tuning a factor map). No API cost.

---

## The composite formula

```python
# Per (ticker, quarter, horizon, prompt_version, model) row, AT final_horizon only:
modifier = clip(
    crowding × financing × dilution × insider × mgmt × acquisition × moat × failures × concentration,
    0.50, 1.50,
)
score_at_current_adjusted_pct_per_month = score_at_current_pct_per_month × modifier
```

`final_score_adjusted = max_H(score_at_current_adjusted_H)` where H is `final_horizon` (uses the same horizon as `final_score`).

Clipping at `[0.50, 1.50]` prevents extreme distortions when several penalties chain. Each component returns a factor in roughly `[0.70, 1.10]`, so unclipped modifiers typically land in `[0.45, 1.30]`.

---

## The 9 components

Each component is a pure function `(research_brief: dict, context: dict) -> (factor: float, evidence: dict)` where `evidence` describes what drove the factor (for the audit JSON). Components run independently and combine multiplicatively.

All factor maps below are the **default** mapping. Each is overridable via `config/scoring_modifier.yaml`.

### 1. Competitive crowding

**Source.** `research_brief.competitive_landscape[]` — list of competitors with `stage` field.

**Extraction.** Count entries whose `stage` is `Approved`, `NDA filed`, `Ph3` (i.e. late-stage / commercial competitors). Treat `(incumbent SOC)` and approval-year strings as approved.

**Factor map (default):**

| # rivals at Ph3+/Approved | Factor |
|---:|---:|
| 0 | 1.00 |
| 1–2 | 0.95 |
| 3–4 | 0.90 |
| 5+ | 0.80 |

**Edge cases.** When `competitive_landscape` is empty: factor 1.00 (assume model decided no material competition). When entry has no `stage` field: count as Ph3+/Approved (conservative).

**NTLA example.** 5 entries (Dawnzera Approved, Ekterly Approved, Andembry Approved, Takhzyro incumbent, Orladeyo Approved) → 5 → **0.80**.

### 2. Financing risk (runway)

**Source.** `research_brief.financials.runway_months` (REAL months).

**Factor map (default):**

| Runway | Factor |
|---:|---:|
| ≥ 18 months | 1.00 |
| 12–18 months | 0.95 |
| 6–12 months | 0.85 |
| < 6 months | 0.70 |

**Edge cases.** Missing or zero runway: factor 0.85 (conservative — treat as 6–12mo bucket, since the model didn't report it).

**NTLA example.** 21 months → **1.00**. **TCRX example.** 18 months → **1.00** (boundary inclusive). 17 months would be 0.95.

### 3. Recent dilution

**Source.** `research_brief.financials.recent_capital_raises[]` — list of raises with `date_iso`.

**Extraction.** Count raises where `parse_iso(date_iso) >= today − 180 days`.

**Factor map (default):**

| Raises in last 180 days | Factor |
|---:|---:|
| 0 | 1.00 |
| 1 | 0.97 |
| 2+ | 0.93 |

**Edge cases.** `date_iso` unparseable → ignore that entry. Empty list → 1.00.

**NTLA example.** 0 entries → **1.00**. **TCRX example.** 2 entries (Apr 2024, Dec 2024) — both >180d ago today (2026-04-25) → 0 in window → **1.00**.

### 4. Insider conviction

**Source.** `research_brief.insider_activity.recent_transactions[]` — list of `{date_iso, insider_name, role, type, shares, price_usd}`.

**Extraction.** Detect at least one entry where:
- `type == "buy"` (open-market purchase, NOT `option_exercise` and NOT `gift`)
- `parse_iso(date_iso) >= today − 180 days`
- `role IN ("CEO", "CFO", "Director", "10% owner")`

**Factor map (default):**

| Detected | Factor |
|---|---:|
| ≥ 1 qualifying buy | 1.10 |
| none | 1.00 |

Optional bonus: if the buy was at a **premium to market price** (per `research_brief.insider_activity.last_3y_summary` text — not structurally exposed today), bonus stays 1.10 (no extra). Future enhancement: lift "premium to market" into a structured `discount_pct` field; bonus 1.15 when premium > 25%.

**Edge cases.** Empty list → 1.00. Missing `type` field → don't count.

**NTLA example.** Recent transactions are all `option_exercise` (induction grants) — no buy → **1.00**. **TCRX example.** Lynx1 Dec 2024 buy is **outside the 180d window** (>1y ago) — **1.00 today**. (The factor would have been 1.10 in early 2025.)

### 5. Mgmt track record

**Source.** `research_brief.mgmt_track_record_score.score` (3-band: 0.3 / 0.6 / 0.9).

**Factor map (default):**

| score | Factor |
|---:|---:|
| 0.9 | 1.05 |
| 0.6 | 1.00 |
| 0.3 | 0.90 |

**Edge cases.** Missing score → 1.00 (neutral). Out-of-band score (e.g. 0.5) → snap to nearest band.

**Why also here when prompt already adjusts probability?** The HARD RULE #14 / #15 probability adjustment is bounded by `[0.15, 0.90]`. Mgmt-track quality should compound score, not just probability. Risk of double-counting: small — the prompt's prob adjustment is ≤ ±0.10, so a 0.9-mgmt company gains ~+10pp prob (already in score) AND ×1.05 multiplier (compounds to ~+15% effect). Acceptable given the user's stated preference to weight management quality.

**NTLA example.** 0.6 → **1.00**. **TCRX example.** 0.3 → **0.90**.

### 6. Acquisition optionality

**Source.** `research_brief.acquisition_target.score` (3-band: 0.3 / 0.6 / 0.9).

**Factor map (default):**

| score | Factor |
|---:|---:|
| 0.9 | 1.10 |
| 0.6 | 1.05 |
| 0.3 | 1.00 |

**Rationale.** M&A premium is genuine return optionality not captured in the model's `target_price` (which assumes organic catalyst-driven price). Asymmetric: the modifier never penalises low acquisition probability (factor floored at 1.00), only rewards high.

**Edge cases.** Missing score → 1.00.

**NTLA example.** 0.6 → **1.05**. **TCRX example.** 0.3 → **1.00**.

### 7. Moat durability

**Source.** `research_brief.moat.score` (3-band: 0.3 / 0.6 / 0.9).

**Factor map (default):**

| score | Factor |
|---:|---:|
| 0.9 | 1.05 |
| 0.6 | 1.00 |
| 0.3 | 0.95 |

**Rationale.** A high-moat thesis is worth more than the same target/probability with no moat — durability of post-catalyst price re-rate is higher. Symmetric (penalise weak moats too).

**Edge cases.** Missing score → 1.00.

**NTLA example.** 0.9 → **1.05**. **TCRX example.** 0.6 → **1.00**.

### 8. Recent operational failure

**Source.** `research_brief.past_failures[]` — list of `{date_iso, program, event, impact}`.

**Extraction.** Detect at least one entry where:
- `event IN ("clinical_hold", "endpoint_miss", "CRL", "going_concern")`
- `parse_iso(date_iso) >= today − 365 days`

**Factor map (default):**

| Detected (any of the four event types in last 12 months) | Factor |
|---|---:|
| ≥ 1 | 0.90 |
| none | 1.00 |

**Why 12 months not 6?** Operational stress (workforce reductions, clinical holds, missed pivotals) has a longer half-life than dilution events on investor sentiment. A clinical hold lifted 8 months ago still shapes how the next readout is positioned.

**Edge cases.** `event_kind` outside the four listed (e.g. `partnership_break`, `other`) doesn't trigger penalty. `date_iso` missing or unparseable → ignore.

**NTLA example.** MAGNITUDE clinical_hold (2025-10) within 12 months → **0.90**. **TCRX example.** PLEXI-T discontinued (event=`other`, 2025-11) — `other` doesn't match the four trigger events; check `program` text for "halt" / "discontinued" → still doesn't match. Factor: **1.00**.

**Open Item — TCRX edge case.** The PLEXI-T entry uses `event="other"` for what is functionally a program discontinuation. Either (a) tighten the prompt to use a specific `event` enum value like `program_discontinued`, or (b) extend this component's trigger set to include `other` when `impact` text contains words like "discontinued" / "paused" / "winding down" / "workforce reduction". User to decide. **Default for v1: option (a) — extend HARD RULE in M6 prompt to add `program_discontinued` enum value.**

### 9. rNPV concentration

**Source.** `research_brief.rnpv_by_indication[]` and `rnpv_total_usd`.

**Extraction.** Compute `concentration = max(rnpv_contribution_usd) / rnpv_total_usd`. Skip the `Platform optionality` row from the max calculation (it's by design a residual).

**Factor map (default):**

| Concentration | Factor |
|---:|---:|
| < 50% | 1.00 |
| 50–75% | 0.95 |
| > 75% | 0.85 |

**Rationale.** A thesis where 80% of rNPV depends on one Phase 3 readout has a different risk profile from a thesis where rNPV is spread across multiple programs. Concentration penalty captures the binary nature.

**Edge cases.** Empty `rnpv_by_indication` → 1.00 (no rNPV to concentrate). `rnpv_total_usd == 0` → 1.00.

**NTLA example.** Lonvo-z (HAE) contributes $X / $Y of total rNPV. From the run-4 brief, lonvo-z is the dominant indication; concentration likely > 75% → **0.85**. (Exact value depends on rnpv math — actual computation per row.)

**TCRX example.** TSC-101 dominates ($32M / $52M ≈ 62%) → **0.95**.

---

## Combination + bounds

```python
def combine(components: dict) -> float:
    product = 1.0
    for name, (factor, _evidence) in components.items():
        product *= factor
    return max(0.50, min(1.50, product))
```

Component order is irrelevant (multiplication is commutative). The clip prevents pathological cases:
- All 9 components at minimum → 0.80 × 0.95 × 0.93 × 1.00 × 0.90 × 1.00 × 0.95 × 0.90 × 0.85 = **0.464** → clipped to **0.50**.
- All 9 at maximum → 1.00 × 1.00 × 1.00 × 1.10 × 1.05 × 1.10 × 1.05 × 1.00 × 1.00 = **1.336** (no clip needed).

---

## Per-row evidence JSON

`llm_scores.score_modifier_json` is a single-line JSON object capturing each component's factor and evidence:

```jsonc
{
  "modifier": 0.674,
  "components": {
    "crowding":      {"factor": 0.80, "ph3plus_count": 5,
                      "competitors": ["Dawnzera", "Ekterly", "Andembry", "Takhzyro", "Orladeyo"]},
    "financing":     {"factor": 1.00, "runway_months": 21},
    "dilution":      {"factor": 1.00, "raises_180d_count": 0},
    "insider":       {"factor": 1.00, "qualifying_buys_180d": 0},
    "mgmt":          {"factor": 1.00, "score": 0.6},
    "acquisition":   {"factor": 1.05, "score": 0.6},
    "moat":          {"factor": 1.05, "score": 0.9},
    "failures":      {"factor": 0.90, "events": [{"date_iso": "2025-10", "event": "clinical_hold",
                                                  "program": "nex-z MAGNITUDE"}]},
    "concentration": {"factor": 0.85, "lead_pct_of_rnpv": 0.78}
  },
  "raw_product":   0.673,                  // before clip
  "clipped_to":    null,                   // 0.50 / 1.50 if clipped, else null
  "applied_to_horizon": "12mo",            // matches final_horizon
  "applied_at":    "2026-04-25T13:30:00Z"
}
```

The HTML report's "Score modifier breakdown" detail panel renders this as a table.

---

## Schema additions (additive migrations)

```sql
-- D47-driven additive migrations (run idempotently via _apply_additive_migrations)
ALTER TABLE llm_scores ADD COLUMN score_modifier REAL;
ALTER TABLE llm_scores ADD COLUMN score_modifier_json TEXT;
ALTER TABLE llm_scores ADD COLUMN score_at_current_adjusted_pct_per_month REAL;

ALTER TABLE final_rankings ADD COLUMN score_modifier REAL;
ALTER TABLE final_rankings ADD COLUMN score_modifier_json TEXT;
ALTER TABLE final_rankings ADD COLUMN final_score_adjusted REAL;
```

The migration list lives in `src/module_6/scores_db.py::_ADDITIVE_MIGRATIONS` next to D46's entries (M6b doesn't need its own migration framework; it piggybacks on M6's).

---

## `config/scoring_modifier.yaml` (new)

All factor maps are tunable. v1 defaults match the table values above.

```yaml
# Module 6b — composite score modifier configuration.
# Spec: spec/module_6b_spec.md.

# Hard clip on the combined modifier.
bounds:
  min: 0.50
  max: 1.50

# Lookback windows (days).
windows:
  insider_lookback_days: 180
  dilution_lookback_days: 180
  failures_lookback_days: 365

# Per-component factor maps.
components:

  crowding:
    enabled: true
    bands:               # ph3plus_count → factor
      - { max_count: 0, factor: 1.00 }
      - { max_count: 2, factor: 0.95 }
      - { max_count: 4, factor: 0.90 }
      - { max_count: 999, factor: 0.80 }

  financing:
    enabled: true
    bands:               # runway_months threshold → factor
      - { min_months: 18,  factor: 1.00 }
      - { min_months: 12,  factor: 0.95 }
      - { min_months:  6,  factor: 0.85 }
      - { min_months:  0,  factor: 0.70 }
    missing_factor: 0.85

  dilution:
    enabled: true
    bands:               # raises_in_window → factor
      - { max_count: 0, factor: 1.00 }
      - { max_count: 1, factor: 0.97 }
      - { max_count: 999, factor: 0.93 }

  insider:
    enabled: true
    qualifying_roles: ["CEO", "CFO", "Director", "10% owner"]
    qualifying_types: ["buy"]
    detected_factor: 1.10
    none_factor: 1.00

  mgmt:
    enabled: true
    bands:               # score → factor
      - { score_eq: 0.9, factor: 1.05 }
      - { score_eq: 0.6, factor: 1.00 }
      - { score_eq: 0.3, factor: 0.90 }
    missing_factor: 1.00

  acquisition:
    enabled: true
    bands:
      - { score_eq: 0.9, factor: 1.10 }
      - { score_eq: 0.6, factor: 1.05 }
      - { score_eq: 0.3, factor: 1.00 }
    missing_factor: 1.00

  moat:
    enabled: true
    bands:
      - { score_eq: 0.9, factor: 1.05 }
      - { score_eq: 0.6, factor: 1.00 }
      - { score_eq: 0.3, factor: 0.95 }
    missing_factor: 1.00

  failures:
    enabled: true
    qualifying_events: ["clinical_hold", "endpoint_miss", "CRL", "going_concern"]
    detected_factor: 0.90
    none_factor: 1.00

  concentration:
    enabled: true
    exclude_indication_match: ["Platform", "platform"]    # case-insensitive substring match
    bands:               # lead_pct → factor
      - { max_pct: 0.50, factor: 1.00 }
      - { max_pct: 0.75, factor: 0.95 }
      - { max_pct: 1.00, factor: 0.85 }
    missing_factor: 1.00
```

The `enabled: false` per component allows disabling without code change.

---

## Reports

### Final-ranking HTML

Two new columns added immediately after PRIMARY score column (currently col 8):

| Col 8 (current) | Col 9 (NEW) | Col 10 (NEW) | Col 11 (current — was 8) |
|---|---|---|---|
| Score (PRIMARY, %/mo) | × Modifier | = Adjusted score | Months to catalyst |

`Modifier` cell is colour-coded:
- < 0.85 — red background ("multiple penalties applied")
- 0.85–0.95 — orange
- 0.95–1.05 — neutral
- 1.05–1.20 — green ("positive signals")
- > 1.20 — bright green

`Adjusted score` becomes the **ranking key** — `final_rank` column is sorted by it. The raw `Score (PRIMARY)` is preserved for reference (so user can see the modifier's effect).

### Detail panel — new "Score modifier breakdown" section

Inside each ticker's expanded detail row, **before** the existing horizon card and raw-text/research-brief details, a new collapsible section:

```html
<details open>
<summary>Score modifier breakdown (×0.674)</summary>
<table>
  <tr><th>Component</th><th>Factor</th><th>Evidence</th></tr>
  <tr><td>Crowding</td><td>0.80</td><td>5 Ph3+/Approved competitors: Dawnzera, Ekterly, Andembry, Takhzyro, Orladeyo</td></tr>
  <tr><td>Financing</td><td>1.00</td><td>21 months runway</td></tr>
  ...
</table>
</details>
```

Open by default so the user immediately sees how the modifier was constructed.

### XLSX

Two new columns mirroring HTML, in the same position. Conditional formatting on `final_score_adjusted` (column shifts from H to J). Modifier column gets its own colour scale (red→white→green at [0.7, 1.0, 1.3]).

---

## File map

```
2_Funds_parser/
├── config/
│   └── scoring_modifier.yaml             ← NEW (M6b modifier config)
├── spec/
│   ├── module_6_spec.md
│   └── module_6b_spec.md                 ← THIS FILE
├── src/
│   ├── module_6/
│   │   ├── scores_db.py                  ← extended _ADDITIVE_MIGRATIONS
│   │   └── reports.py                    ← extended: render checkbox column + Save/Copy buttons + JS
│   └── module_6b/                        ← NEW package
│       ├── __init__.py
│       ├── modifiers.py                  ← 9 component fns + combine()    [Part (a)]
│       ├── apply.py                      ← apply_modifiers_to_run(conn, run_id, quarter)
│       └── selection.py                  ← parse_selected_tickers(html_path) [Part (b)]
├── scripts/
│   ├── 6_score.py                        ← extended: --selection-from-html, --yes, mandatory gate, invokes M6b
│   ├── 6_recompute_scores.py             ← extended: also re-applies modifiers
│   └── 6b_apply_modifiers.py             ← NEW standalone CLI for retroactive modifier-only re-apply
└── llm_scores.db                         ← gains 6 additive columns (3 per table × 2 tables)
```

`src/module_6b/modifiers.py` skeleton:

```python
"""Module 6b — 9-component composite score modifier (D47).

Pure-Python, deterministic. No API calls. All factor maps overridable via
config/scoring_modifier.yaml.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import yaml

def _component_crowding(rb: dict, cfg: dict) -> tuple[float, dict]:    ...
def _component_financing(rb: dict, cfg: dict) -> tuple[float, dict]:   ...
def _component_dilution(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]: ...
def _component_insider(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]:  ...
def _component_mgmt(rb: dict, cfg: dict) -> tuple[float, dict]:        ...
def _component_acquisition(rb: dict, cfg: dict) -> tuple[float, dict]: ...
def _component_moat(rb: dict, cfg: dict) -> tuple[float, dict]:        ...
def _component_failures(rb: dict, cfg: dict, now: datetime) -> tuple[float, dict]: ...
def _component_concentration(rb: dict, cfg: dict) -> tuple[float, dict]: ...

_COMPONENTS = {
    "crowding":      _component_crowding,
    "financing":     _component_financing,
    "dilution":      _component_dilution,
    "insider":       _component_insider,
    "mgmt":          _component_mgmt,
    "acquisition":   _component_acquisition,
    "moat":          _component_moat,
    "failures":      _component_failures,
    "concentration": _component_concentration,
}

def compute_modifier(research_brief: dict, cfg: dict,
                     now: datetime | None = None) -> dict:
    """Returns: {"modifier": float, "components": {name: {factor, evidence}},
                 "raw_product": float, "clipped_to": float | None}"""
```

---

## Acceptance tests

| # | Test | Expected |
|---|---|---|
| 1 | NTLA run 4: compute modifier | crowding=0.80, mgmt=1.00, acquisition=1.05, moat=1.05, failures=0.90, concentration=0.85 → product=0.674 → no clip. `score_at_current_adjusted_12mo = 44.68 × 0.674 = 30.11` |
| 2 | TCRX run 4: compute modifier | crowding=0.95, financing=1.00 (18mo boundary), insider=1.00 (Lynx1 outside 180d), mgmt=0.90, failures=1.00 (PLEXI-T `event="other"` doesn't match v1 trigger set), concentration=0.95 → product=0.811. `adjusted = 13.03 × 0.811 = 10.57` |
| 3 | Empty research_brief | All components return their `missing_factor` defaults → modifier = 1.00 (neutral). No row written if `score_at_current` is NULL. |
| 4 | All 9 components at minimum | Combined product = 0.464 → clipped to 0.50. `clipped_to = 0.50` in evidence JSON. |
| 5 | All 9 at maximum | Combined product = 1.336 → no clip. `clipped_to = null`. |
| 6 | Disable a component via `enabled: false` | Component returns factor=1.00 with `evidence={"disabled": true}`; doesn't affect product. |
| 7 | Re-run apply on already-modifiered rows | Idempotent — same inputs produce same modifier (modulo `applied_at` timestamp). |
| 8 | `final_rankings.final_score_adjusted` is present + sort key | NTLA still rank 1, TCRX rank 2, but gap narrowed from 3.4× to 2.85× |
| 9 | HTML report renders new columns + modifier breakdown panel | Visual check: modifier badge colour-coded; breakdown table shows all 9 rows |

---

## Part (b) — User-driven selective dispatch

The HTML final-ranking report becomes a **two-way control surface**: it shows the ranking AND captures the user's per-ticker selection for the next dispatch.

### HTML changes

Add **two checkbox columns** at the leftmost position of the ranking table (before the existing arrow column):

| Position | Header | Function |
|---|---|---|
| Col 0 (header) | `<input type="checkbox" id="select-all" checked>` | Master toggle — checks/unchecks every per-ticker checkbox in the table. State derives from per-row checkboxes (indeterminate when partial). |
| Col 0 (per row) | `<input type="checkbox" name="ticker_select" value="<TICKER>" checked data-ticker="<TICKER>">` | Per-ticker selection. Default checked. |

Default state on first render: **all tickers checked** (so a fresh run of `scripts/6_score.py` with `--selection-from-html` matches a no-filter run unless the user pre-edited the HTML).

### JavaScript behaviour (vanilla, no deps)

1. **Master checkbox click** → set `.checked = master.checked` on every `input[name="ticker_select"]`.
2. **Per-row checkbox click** → recompute master's state:
   - All checked → master `checked=true`, `indeterminate=false`
   - All unchecked → master `checked=false`, `indeterminate=false`
   - Mixed → master `indeterminate=true`
3. **Save selection button** (right of master checkbox in the report header):
   - Walk every `input[name="ticker_select"]` and set its `checked` HTML attribute (not just JS property) to match its current property — so serialised HTML reflects state.
   - Try `window.showSaveFilePicker({ suggestedName: "final_ranking_<quarter>.html", types: [{accept: {"text/html": [".html"]}}] })` to overwrite in place.
   - If FSA API unavailable (Firefox / older browsers), fall back to a Blob download of the same filename — the user manually moves to `Outputs/`.
   - On success, show a toast: "Saved. Run `python scripts/6_score.py --selection-from-html Outputs/final_ranking_<quarter>.html` to re-score the N selected tickers."
4. **Copy CLI command button** (next to Save):
   - Copies `python scripts/6_score.py --selection-from-html Outputs/final_ranking_<quarter>.html -v` to clipboard via `navigator.clipboard.writeText`.
   - Independent of save; useful when the user has already saved and just wants the command.
5. **Selected count badge** (in report header):
   - Live-updates as `<span id="selected-count">98 of 98</span>` whenever any checkbox toggles.
   - Cosmetic only — no impact on dispatch logic.

### Pipeline reading the HTML

New CLI flag on `scripts/6_score.py`:

```
--selection-from-html PATH    Parse PATH (the saved final_ranking HTML) and
                              dispatch only the tickers whose checkboxes are
                              checked. Cannot be combined with --tickers
                              or --ticker.
```

**Parse logic** (no external deps — `html.parser` from stdlib, NOT BeautifulSoup):

```python
import re
from pathlib import Path

# Match <input type="checkbox" name="ticker_select" value="TICKER" ... checked ...>
# 'checked' must appear as an attribute (with or without value), regardless of
# whitespace and attribute order.
_TICKER_CHECKBOX_RE = re.compile(
    r"<input\b[^>]*\bname=['\"]?ticker_select['\"]?[^>]*\bchecked\b[^>]*?>",
    re.IGNORECASE,
)
_VALUE_RE = re.compile(r"\bvalue=['\"]([A-Z0-9.\-]+)['\"]", re.IGNORECASE)

def parse_selected_tickers(html_path: Path) -> list[str]:
    text = html_path.read_text(encoding="utf-8")
    out: list[str] = []
    for tag in _TICKER_CHECKBOX_RE.findall(text):
        m = _VALUE_RE.search(tag)
        if m:
            out.append(m.group(1).upper())
    # Preserve order, dedupe.
    seen, ordered = set(), []
    for t in out:
        if t not in seen:
            ordered.append(t); seen.add(t)
    return ordered
```

Lives at `src/module_6b/selection.py`.

### Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│ Initial run (no selection file yet)                             │
│   $ python scripts/6_score.py        # interactive D30 prompts  │
│   → 98 tickers feed → cost estimate $93 → [y/N] → dispatch      │
│   → llm_scores written → M6b modifier applied                   │
│   → final_ranking_<quarter>.html rendered (all 98 checked)      │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
   User opens HTML, unchecks the rows they don't want re-scored,
   clicks "Save selection" → HTML overwritten on disk with updated
   `checked=""` attributes.
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│ Selective re-run                                                │
│   $ python scripts/6_score.py \                                 │
│       --selection-from-html Outputs/final_ranking_<q>.html      │
│   → parse HTML → 22 selected tickers                            │
│   → tier classify the 22                                        │
│   → cost estimate $X (only for the 22) → [y/N] → dispatch       │
│   → llm_scores updated → M6b re-applied                         │
│   → HTML re-rendered (selection state preserved from input HTML)│
└─────────────────────────────────────────────────────────────────┘
```

**Selection persistence on re-render**: when `scripts/6_score.py` re-renders the HTML at the end of a run, it reads the prior selection from the input HTML (or defaults to "all checked" on first render) and emits checkboxes with the matching `checked` attribute. This means the user's selection survives across re-runs — they don't have to re-tick every time.

### Confirmation gate (mandatory)

Today's `scripts/6_score.py` has a `[y/N] Proceed to dispatch N API calls for $X?` prompt that auto-skips when `sys.stdin.isatty()` is false. This M6b spec **promotes the gate to mandatory**:

- Even in non-interactive contexts (cron, batch file with stdin redirected), the gate STILL prompts and aborts on EOF.
- A new `--yes` flag (single shot) is the only way to bypass — must be explicit, never inferred.
- The pipeline orchestrator [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) currently runs `scripts/6_score.py` from a TTY-attached cmd window so the prompt works naturally; no change needed there.
- The existing `--dry-run` flag remains a no-API alternative when the user just wants the cost estimate.

Updated [scripts/6_score.py](../scripts/6_score.py) confirmation block:

```python
if args.yes:
    pass                                        # explicit bypass
else:
    try:
        msg = (f"\nProceed to dispatch {len(per_ticker)} API calls "
               f"for ${estimate.scenarios[2].total_usd:,.2f}? [y/N] ")
        ans = input(msg).strip().lower() or "n"
    except EOFError:
        ans = "n"                               # closed stdin → abort
    if ans != "y":
        print("Aborted.")
        return 0
```

The previous "non-interactive mode: skipping [y/N] gate" path is removed (D48 — see decisions.md).

### Selective-dispatch + tier classification interaction

When `--selection-from-html` is used, the selected tickers still go through tier classification (D39). For each selected ticker:
- If it qualifies as Tier A (exact cache hit) → dispatched cost is $0; the modifier still re-applies.
- If Tier B → light refresh.
- If Tier C → full scoring.

This means the selection mechanism doesn't override caching — it ADDITIONALLY restricts which tickers are even considered. The pre-flight cost summary makes this transparent:

```
Selection-from-html: 22 tickers selected (of 98 in saved HTML)
Tier breakdown of selected:  A=8  B=10  C=4
Estimated cost: $5.20  (worst-case if all B escalate: $7.80)
Proceed? [y/N]
```

### Schema changes for selective dispatch

None. The selection lives only in the HTML file (and transiently in the in-memory feed during dispatch). `llm_runs.gate_config_json` is extended to include `"selection_source": "html|gates|tickers_flag"` and `"selection_count": N` for run-level audit, but no new SQL columns required.

### Acceptance tests for selective dispatch

| # | Test | Expected |
|---|---|---|
| 10 | Render initial HTML; verify all checkboxes default `checked` | 98 of 98 selected, master checkbox `checked` |
| 11 | Manually edit HTML to uncheck 50 boxes; parse via `parse_selected_tickers()` | Returns 48 ticker symbols |
| 12 | Run `--selection-from-html` with the edited HTML; verify dispatch uses only selected | `len(per_ticker) == 48`; pre-flight summary shows `Selection-from-html: 48 selected` |
| 13 | `--selection-from-html` + `--tickers TCRX` simultaneously | Argparse rejects: "mutually exclusive" |
| 14 | Save-selection JS correctly serialises `checked` HTML attribute (not just property) | Re-load saved HTML in browser; previously-toggled state is preserved |
| 15 | Re-render after a selective dispatch: input had 48 checked, output HTML carries the same 48 `checked` | Selection survives round-trip |
| 16 | Confirmation gate fires even when stdin is piped via `echo y \| ...` | Gate prompts; reads `y`; proceeds |
| 17 | Confirmation gate aborts cleanly on closed stdin (no `--yes`) | `EOFError` caught; "Aborted." printed; exit 0 |
| 18 | `--yes` bypasses the gate | No prompt; dispatches directly |

---

## Open items before implementation

1. **PLEXI-T `event="other"` vs failures trigger set.** TCRX's PLEXI-T discontinuation lands as `event="other"` in run 4, which doesn't fire the failures component. Two fixes: (a) extend M6 HARD RULE #19 to add `program_discontinued` enum value (clean — affects all future runs); (b) extend M6b failures component to also trigger on `impact` text matching (loose — fragile). **v1 default: option (a).**

2. **Insider conviction "premium to market" detection.** The Lynx1 buy at 37% premium is a stronger signal than a regular buy. v1 doesn't distinguish. Future: add structured `discount_to_market_pct` field to `recent_transactions` schema; lift bonus to 1.15 for premium > 25%.

3. **Concentration component double-counting risk.** The model's POS adjustments per indication already reflect risk; the concentration penalty adds a second layer of "binary risk" hit. Some users may consider this double-counting. Make `enabled: false`-able if concerns arise (it already is).

4. **Mgmt-track double-counting.** Discussed in component #5. v1 accepts the double-count; alternative is to disable the mgmt component when the prompt's prob adjustment was applied (but there's no audit field for that).

5. **Bounds tuning.** [0.50, 1.50] is conservative. After running on a larger feed (98 tickers), check the distribution; widen to [0.40, 1.60] if the cap is binding on too many rows.

6. **Per-component weighting.** Not in v1 — all components contribute multiplicatively. Could later add a `weight` config knob: `factor_weighted = factor ** weight` (weight 0 disables, weight 2 doubles the impact). Defer until v1 distribution is observed.

7. **Reports**: should `final_score` (raw) stay visible at column 8 alongside `final_score_adjusted` at column 10? **v1: yes — keeps the modifier's effect transparent.** Could hide raw later if it confuses the user.

---

## Decisions log entries

Two decisions cover Module 6b:

> **D47 — Composite score_modifier (Module 6b Part (a)) — 9 deterministic components from research_brief**
> Spec: [module_6b_spec.md](module_6b_spec.md) § *The 9 components*. Per-component factor maps tunable in `config/scoring_modifier.yaml`. Ranking key changes from `final_score` to `final_score_adjusted`.

> **D48 — User-driven selective dispatch (Module 6b Part (b)) + mandatory confirmation gate**
> Spec: [module_6b_spec.md](module_6b_spec.md) § *Part (b)*. HTML report becomes a two-way control surface (per-ticker + master checkboxes; Save/Copy buttons). New `--selection-from-html PATH` flag on `scripts/6_score.py` parses the HTML and restricts dispatch to checked tickers. Confirmation gate (`[y/N]`) is mandatory regardless of TTY state — only `--yes` bypasses, and only as an explicit single-shot.

Per-component factor maps for the modifier (D47) and the visual styling of the checkboxes (D48) are **NOT** logged as separate D-numbers — they're config / cosmetics and live in YAML / HTML respectively. A spec rev is required to add a new modifier component, change combination logic, change the gate-bypass policy, or alter the selection-from-HTML parsing rules.

---

## Implementation order (when authorised)

### Part (a) — composite modifier
1. Write `config/scoring_modifier.yaml` with v1 defaults.
2. Build `src/module_6b/modifiers.py` — 9 component functions + combine, all with unit-testable signatures. ~200 lines.
3. Build `src/module_6b/apply.py` — `apply_modifiers_to_run(conn, run_id, quarter)`. Walks `llm_scores` rows for the run, computes modifier, writes back.
4. Extend `src/module_6/scores_db.py::_ADDITIVE_MIGRATIONS` with the 6 new columns.
5. Extend `scripts/6_score.py` to call `apply_modifiers_to_run` after writing `final_rankings`. Reports run after — they pick up the new columns.
6. Extend `scripts/6_recompute_scores.py` to also re-apply modifiers after the D46 score recompute.
7. New CLI `scripts/6b_apply_modifiers.py` — same flag shape as `6_recompute_scores.py` (`--run-id`, `--quarter`, `--dry-run`, `-v`). Pure offline.
8. Extend `src/module_6/reports.py::render_final_ranking_html` and `render_final_ranking_xlsx` for the two new columns + the modifier breakdown panel.
9. Extend `scripts/6_score.py`'s "Reports written" stdout summary.

### Part (b) — selective dispatch
10. Build `src/module_6b/selection.py` — `parse_selected_tickers(html_path) -> list[str]` using stdlib `re`/`html.parser`.
11. Extend `src/module_6/reports.py::render_final_ranking_html` further:
    - Insert leftmost checkbox column (header master + per-row checkboxes)
    - Add "Save selection" + "Copy CLI command" buttons in the report header
    - Add the JS for master-toggle / per-row-state / save / copy
    - Re-render path: read prior selection state from input HTML when re-rendering, propagate `checked` attributes
12. Extend `scripts/6_score.py`:
    - New `--selection-from-html PATH` flag (mutually exclusive with `--ticker` / `--tickers`)
    - New `--yes` flag (single-shot bypass of the confirmation gate)
    - Make the confirmation gate **mandatory** — abort cleanly on closed stdin without `--yes`
    - When `--selection-from-html` is set, build `feed_rows` from the parsed ticker list instead of running gate queries
    - Update `llm_runs.gate_config_json` to include `"selection_source"` and `"selection_count"` for audit
13. Update [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) — add a follow-up `[y/N] Re-score from HTML selection?` prompt after the initial M6 run; if yes, invokes `scripts/6_score.py --selection-from-html Outputs/final_ranking_<quarter>.html`.

### Both parts
14. Add **D47** (modifier framework) and **D48** (selective dispatch + mandatory gate) to `spec/decisions.md`.
15. Run `scripts/6b_apply_modifiers.py --run-id 4` against existing run 4 data; confirm NTLA/TCRX numbers match the worked examples.
16. Manual smoke test:
    - Open the re-rendered HTML; verify all 2 tickers (run 4) checked.
    - Uncheck NTLA, click "Save selection", verify HTML on disk has `checked` only on TCRX.
    - Run `python scripts/6_score.py --selection-from-html Outputs/final_ranking_2025Q4.html` — verify dispatch shows `Selection-from-html: 1 ticker selected (of 2)` and only re-scores TCRX (Tier A → $0).

Estimated build effort: ~1 full day total (modifier ~half day + selective dispatch ~half day). No API costs for the test workflow above (Tier A on existing rows).

---

## Implementation notes — Part (b) (2026-04-25)

What ended up in the code, beyond what the spec called for verbatim:

1. **Mandatory gate is short-circuited when `n_dispatch == 0`.** When every selected ticker is Tier A (cache hit, $0 cost), the script prints "All selected tickers are Tier-A cache hits — no API calls; skipping confirmation gate" and proceeds straight to the report re-render. There is nothing to confirm; the original "always prompt" behaviour was nominally correct but UX-hostile in this case. Still, any non-zero dispatch — even $0.01 list price — re-engages the gate.

2. **Merged latest-per-ticker re-render under `--selection-from-html`.** When `--selection-from-html` is in play, the renderer now consumes a *merged view*: for each ticker present in the input HTML (parsed via `parse_all_tickers`), the latest `llm_scores` row across the quarter (any `run_id`) is used. This is what makes "preserving the unchecked NTLA" work — NTLA's row from the prior run stays visible in the re-rendered HTML even though only TCRX was dispatched. The per-run `final_rankings` table is still written for audit, but the HTML/XLSX consume the merged view. The regular full-feed path is unchanged (still per-run).
   - New helpers in `scripts/6_score.py`: `_aggregate_ranking_from_score_rows`, `_latest_score_rows`, `_build_merged_ranking_rows`, `_load_packs_for_tickers`.

3. **`parse_all_tickers` companion in `selection.py`.** The spec only required `parse_selected_tickers` (returns checked). The merged-view re-render needed the full ticker set from the input HTML too, so a small `parse_all_tickers` helper was added — used by the script, not exported as a top-level package symbol.

4. **`gate_config_json` audit fields.** The run record now carries `selection_source` (`"html"` / `"tickers_flag"` / `"gates"`) and `selection_count` (#dispatched). When `--selection-from-html` is used, two extra fields are added: `selection_html_path` and `selection_pool_size` (#checkboxes in the input HTML, regardless of checked state). All of this lives in the `audit_gates` dict passed to `open_run` — no schema change.

5. **Argparse mutex on `--ticker | --tickers | --selection-from-html`.** Spec called for `--selection-from-html` to be mutex with `--ticker` / `--tickers`. The `add_mutually_exclusive_group()` extension also covers `--ticker` vs `--tickers` (which weren't mutex before but always behaved as if they were).

6. **Bat-file follow-up prompt.** [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) gained a second `[y/N] Re-score from HTML selection?` prompt after the initial M6 run. It auto-discovers the latest quarter from `context_packs.db` and invokes `scripts\6_score.py --selection-from-html "Outputs\final_ranking_<quarter>.html" -v`. Required `setlocal EnableDelayedExpansion` to read variables set inside nested `if` blocks.

7. **Visual: selection toolbar above the table.** Implemented as `<div class="toolbar">` with selected-count badge (turns orange on partial, grey on empty), Copy CLI button, and a transient toast (~4-second show/hide) that confirms saves and reports fallback-download events. The CSS is appended in-line to the existing `<style>` block. *(2026-04-25 update — the explicit "Save selection" button was removed in favour of auto-save; see point 8.)*

8. **Auto-save on every checkbox change (re-architected 2026-04-25 — D49).** No explicit Save button. Every checkbox toggle (master or per-row) schedules a save via `scheduleAutoSave()` with a 300 ms debounce, so toggling many rows in quick succession (e.g. clicking the master to deselect all) collapses to a single write.

   **Storage architecture: local HTTP server + sidecar JSON file.** The browser sends `PUT /selection/<quarter>` with the full selection payload to a tiny stdlib `http.server` running on `127.0.0.1` (default port 4609, auto-incremented if taken). The server (`scripts/6_serve_report.py`) atomically writes `Outputs/final_ranking_<quarter>_selection.json` and replies 200 OK. The pipeline (`scripts/6_score.py --selection-from-html PATH`) reads that JSON before dispatch — preferring the sidecar over HTML `checked`-attribute parsing (kept as a back-compat fallback for legacy rendered HTMLs that don't have a sidecar yet).

   **Why a local server.** The original D48 design stored selection state in the HTML's `checked` attributes and used the File System Access API to save. Even with `FileSystemFileHandle` persisted in IndexedDB and the smaller `requestPermission` prompt, the user still hit a browser dialog the first time on each fresh render. A same-origin HTTP `PUT` is silent — no dialog, no permission, no prompt — because browsers don't gate same-origin XHR/fetch behind a filesystem permission. The trade-off is server lifecycle (start/stop) and that the report is no longer openable as a `file://` page if you want auto-save. A `file://` load still renders correctly (showing the most recent selection state from the JSON-seeded HTML attributes); a banner explains how to start the server for editing.

   **Sidecar JSON schema (v1):**

   ```json
   {{
     "schema_version":   1,
     "quarter":          "2025Q4",
     "rendered_at":      "2026-04-25T13:30:00Z",
     "updated_at":       "2026-04-25T14:01:23Z",
     "all_tickers":      ["NTLA", "TCRX"],
     "selected_tickers": ["TCRX"]
   }}
   ```

   **Round-trip preservation.** The renderer (`scripts/6_score.py` step 10) loads the existing sidecar before re-rendering, computes `selected_tickers` via `merge_selection(new_pool, prior)` — keep selections for tickers still in the pool, default new tickers to selected, drop tickers no longer in the pool — then writes the new sidecar atomically. The HTML's initial `checked` attributes mirror this set, so file:// loads still display correct state.

   **Server endpoints:**

   | Method | Path                       | Purpose                                                  |
   |---|---|---|
   | GET    | `/`                        | 302 redirect to `/<latest_quarter>`                      |
   | GET    | `/<quarter>`               | Serve `Outputs/final_ranking_<quarter>.html`             |
   | GET    | `/selection/<quarter>`     | Return sidecar JSON (404 if missing)                     |
   | PUT    | `/selection/<quarter>`     | Atomic write of sidecar JSON; returns 200 + `saved_at`   |

   **Browsers without server access** (file:// load, no daemon running): a yellow banner appears at the top of the report saying "Auto-save disabled — start the local server" with a `<code>` snippet of the exact command. Selection toggles produce a one-shot toast; nothing persists. The Copy CLI / Copy serve buttons stay functional.

What was deliberately *not* changed:
- `scripts/6_recompute_scores.py` — still per-run rendering. The selective-dispatch flow is the only place where the merged-view re-render makes sense.
- `final_rankings` table schema — selection state lives in HTML, not SQL, per D48.
- The standalone `llm_responses_<quarter>.html` viewer — already deleted in M6 step 7; the merged HTML supersedes it.
