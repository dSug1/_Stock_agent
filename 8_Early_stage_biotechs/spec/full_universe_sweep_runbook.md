# Full-universe digest — operator runbook (steps 2–4)

*Goal: extend the digest from the ~294 currently-cornered names to full coverage of the 930-name active
universe, by giving every entity its founder/citation dimension. Written 2026-07-14 after step 1 (the
`--force` re-score of the 294). Run the steps in order; each is safe to interrupt and resume.*

**Why this shape:** you never score all 930 — the §5.5 pre-filter (an independent citation OR clinical
widening, plus a capital signal) reserves the expensive Sonnet call for genuinely cornered names. This
sweep grows the pre-filter *inputs* (founders → OpenAlex citations) across the whole universe, then scores
only whatever newly clears.

**Run each command from the component dir** `8_Early_stage_biotechs/`.
`PY = ..\.venv\Scripts\python.exe`, `PYTHONPATH=src`.

---

## Cost & time budget (whole sweep)

| Step | Action | $ (real) | Time |
|---|---|---|---|
| 2 | extract remaining 648 (Haiku batch) + free signals | ~$3.3 | 1 batch (~10–30 min) + minutes |
| 3 | OpenAlex crossref-only drain (free) | $0 | ~2–3 daily windows (budget resets ~every 19–22h) |
| 4 | score newly-cleared candidates (Sonnet batch) + render | ~$3–5 | 1 batch + seconds |
| — | **total incremental** | **~$7–9** | ~2–3 days (gated by the OpenAlex windows) |

Batch web-search cost is now honestly reported (D28 calibration); scoring is token-only (no calibration).

---

## Step 2 — extract the remaining 648 + free signals  (PAID Haiku, ~$3.3 real)

```
PYTHONPATH=src %PY% scripts\8_extract.py --cold-first --limit 648 --yes
```
- One batch fits the $15 `max_usd_per_run` cap (estimate ~$3.97; real ~$3.3). `--cold-first` keeps
  smallest-cap-first ordering. `--yes` skips the gate (remove it to review the estimate first).
- Batch_id → `data/extract_batch_id.txt`; if the poll is interrupted, re-attach with `8_extract.py --resume`.

Then light up the FREE signals (no spend). **`8_signals.py` runs only the FIRST flag passed (each is an
if-return), so run them ONE AT A TIME:**
```
PYTHONPATH=src %PY% scripts\8_signals.py --capital-markets    (the pre-filter GATE — most important)
PYTHONPATH=src %PY% scripts\8_signals.py --clinical
PYTHONPATH=src %PY% scripts\8_signals.py --designations
PYTHONPATH=src %PY% scripts\8_signals.py --ownership
```
The capital-markets signal is the one that matters most: a name only clears the pre-filter with citation/
clinical evidence AND a capital signal. (The 648 are already in the active universe, so they already have
market caps — no enrich needed.)

---

## Step 3 — OpenAlex crossref-only drain  (FREE, ~2–3 windows)

```
run_8_openalex_daily.bat --crossref-only
```
- Resolves the new founders + citations. `--crossref-only` (D29) drops the 10-credit OpenAlex author-search
  backstop, so non-academic founders (CEOs/VCs, no publication trail) cost **~0 credits** instead of 10 —
  ~3–5× more founders per window. On the last run it cleared 205 literature + 93 independence in one
  window (~845 credits).
- **Budget-gated:** each window is ~1000 credits; the runner probes, drains until the reserve, and stops
  losslessly. ~648 entities × ~2.1 founders ≈ ~1,360 new founders → **~2–3 windows**. The runner prints
  `founders awaiting literature resolution: N` at the end — **re-run the same command after the budget
  resets** (~19–22h) until that hits 0.
- Nothing is lost between windows (per-founder persist + anti-poisoning None-on-throttle). No dollar cost.

---

## Step 4 — score ONLY the newly-cleared candidates + render  (PAID Sonnet, ~$3–5)

```
PYTHONPATH=src %PY% scripts\8_score.py --yes
run_8_render.bat
```
- **Plain `8_score` — NO `--force`.** It scores only candidates not yet scored at the current prompt
  version, so the 294 already scored in step 1 are **skipped — you do not re-pay for them**. Only the new
  cold-first names that cleared the pre-filter get scored.
- **Do NOT change `scoring_prompt_version` (stays `v2`) between step 1 and here** — bumping it invalidates
  every prior score and would force a full re-score.
- `run_8_render.bat` rebuilds `Outputs/pipeline_status.html` (dashboard + interactive digest) from the DB.

---

## Notes / gotchas
- **Sonnet-5 intro pricing ends 2026-08-31** — after that, flip `anthropic_client.PRICING["claude-sonnet-5"]`
  back to `{in:3, out:15}` (comment in the table). Until then $2/$10 is correct.
- **`max_usd_per_run` is 15** (raised for these tranches, config.yaml). Lower toward ~5 for routine small
  runs if you want a tighter guard.
- **Cap dropped names:** a name only reaches the digest if it clears the pre-filter. Un-extracted or
  no-academic-founder names stay out by design (§9 survivorship) — that's correct, not a gap.

## Korea / Japan — AFTER this sweep (additive, not a prerequisite)
KR (DART) and JP (EDINET) are **market expansion**: they add new entities to the universe (both need a free
API key — the current blocker). They are orthogonal to digesting the present 930 (US/CA/Nordic/EU) and flow
through this exact extract→OpenAlex→score pipeline once added. Finish this sweep first; bolt on KR/JP later
with no rework. See `spec/international_expansion_plan.md`.
