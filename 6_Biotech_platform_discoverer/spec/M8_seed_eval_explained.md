# M8 explained — seed-eval validation harness (§13)

*Companion to `SPEC_acrivon_pattern_screener.md` §13 and `decisions.md` D6.*

---

## In one sentence

M8 is the **trust gate**: it scores the labeled seed set through the funnel and reports precision /
recall + per-stage survival, so a known positive that goes missing — especially one *deleted at the
hard cut* — is caught loudly instead of silently. Per the spec, **the screen is not trusted until this
passes.**

## How to run

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_eval.py                 # → Outputs/seed_eval.md
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_eval.py --threshold 0.65 --persist-labels
```

Read-only and free — it evaluates whatever scores already exist in the store (no API). It is also
wired into the run `.bat` after Stage 5.

## The labels (`config/seed_labels.csv`)

Analyst-editable; `ticker,label,name,note` with three labels:

| label | seeds | role |
|---|---|---|
| **positive** | ACRV, TNGX, IDYA, BOLD | must be recovered |
| **borderline** | RXRX, SDGR, RLAY | AI-discovery; reported separately, never counted in P/R |
| **negative** | CRL, MEDP, ICLR (CROs), BRKR, A (tools) | must be rejected |

Borderline is a deliberate third class — those names are genuinely ambiguous, so forcing them into
pos/neg would distort the metric. (The store's `seed_labels` table still only accepts positive|negative
— borderline lives only in the CSV/eval.)

## What it reports

**Per-seed funnel position** is the headline. For every seed it resolves where it stands:

- `deleted` at a stage (with reason — recovered from the **audit log**, since a deleted company's row
  is gone) · `absent` (never entered the universe) · `unscored` (in-band, retained, not yet scored) ·
  `scored` (with tier + composite).

**The load-bearing check (§13):** a **positive deleted at the hard cut** sets `spec_failure=True` and
prints a banner — that is a Stage-0 bug to fix, because Stage 0 is the only place a candidate is lost
invisibly. A positive that is merely `unscored` is **not** a failure (scoring costs money and runs on
selected tiers).

**Precision / recall** are computed over the pos/neg seeds that have a composite, at
`seed_eval.decision_threshold` (default 0.6): `composite ≥ threshold ⇒ predicted positive`. The report
shows the full confusion matrix (TP/FP/FN/TN), precision, recall, F1, and a per-group survival table
(in-store / deleted / tagged / with-evidence / scored / predicted-positive). Metrics persist to
`run_meta.metrics_json`.

## The calibration loop it enables

1. Run a tiered Stage-4 scoring pass that covers the in-band seeds.
2. `6_eval.py` → read precision/recall + which positives/negatives landed where.
3. Tune funnel thresholds (market-cap band, tier gate, composite weights/penalties, decision
   threshold) until positives recover and negatives reject.
4. Repeat. The screen earns trust only when this is green.

## Tests

`tests/test_seed_eval.py` (10): CSV parse (+ skips bad rows), the confusion matrix, the
lost-positive `spec_failure` flag, survival counts, threshold sensitivity, `unscored`/`absent` status,
and the report + `run_meta` side effects. Whole suite: **147 pass**.
