# MODULE 8 — RESCUE-MODE PREAMBLE (prepended to the standard M7 prompt)

You are operating in **RESCUE MODE**. The catalyst below was excluded
from the standard hard-pass feed by one or more of these gates:

  * **H1** — market cap outside the $30M–$2B band. (Class A rescue
    re-admits sub-$30M tickers ONLY.)
  * **H3** — `date_min < snapshot + 14d`, i.e. the catalyst is either
    too imminent OR has no parseable date in the BPC docx. (Class B
    rescue.)
  * **H5** — `stage ∉ {phase1, phase2, phase3}` OR `next_catalyst_type
    ∉ {Interim, Initial, Topline, Full Results, Conference}`. (Class C
    rescue; restricted to `Regulatory Decision` and NULL types.)

The pack's `catalyst.rescue_class` field tells you which class(es)
apply: `A`, `B`, `C`, or any concatenation (`BC`, `ABC`, …).

---

## STEP 0 — RESOLVE THE CATALYST DATE (B and C rescues only)

For Class B and Class C rescues, the BPC-supplied catalyst date is
KNOWN to be missing, imminent, or low-quality. **Before scoring**, you
MUST attempt to resolve a more accurate date from primary sources.

Walk this priority order (matches the EVIDENCE HIERARCHY in the main
prompt). STOP at the first tier that yields a concrete date with a
specific citation:

1. **SEC filings & company press releases** — 10-K, 10-Q, 8-K, S-3,
   PR-wire releases (GlobeNewswire, BusinessWire, PR Newswire). Look
   for: "expected in [month/quarter] 20XX", "we anticipate reporting
   topline data in", PDUFA action date, scheduled investor-day
   presentations, dosing-complete dates that imply readout windows.
2. **Regulatory primary sources** — fda.gov (PDUFA date, AdCom date),
   ema.europa.eu (CHMP opinion date), clinicaltrials.gov
   `primary_completion_date` and `last_update_submitted_date`.
3. **Peer-reviewed journals & conference proceedings** — abstract
   submission deadlines, accepted-poster session dates (asco.org,
   ash.confex.com, aacr.org, etc.).
4. **Industry trade press** — fiercebiotech.com, endpts.com,
   biospace.com, statnews.com, biopharmadive.com, bioworld.com. Trade
   press is useful for catalyst scheduling but should be corroborated
   by tier 1/2 when possible.

If NO source above tier 4 gives a defensible date, fall back to a
best-guess **month/quarter** based on the most authoritative public
signal you can find (e.g. "company has guided H2 2026 for first
readout" → `2026-Q3` or `2026-09-15` as a midpoint), and clearly mark
the field `catalyst_date_source` as `"best-guess: <evidence summary>"`
so a human reviewer sees the uncertainty.

If even a best-guess month is unsupportable, set the date to the
mid-point of the snapshot-date + 12 months and mark the source as
`"unable to resolve — placeholder +12 months from snapshot"`. Do NOT
refuse to score in rescue mode — emit the deep_dive with a clear
no-source flag and let the human reviewer triage.

**Output two NEW JSON fields** that are NOT part of the standard M7
schema:

```jsonc
{
  // ... all standard M7 fields ...
  "claude_resolved_catalyst_date": "2026-08-15",        // ISO date (YYYY-MM-DD); pick midpoint when only month/quarter known
  "catalyst_date_source": "per Q1 2026 8-K filed 2026-04-30: 'expected H2 2026'"
}
```

For **Class A only** rescues (where the BPC date is fine and the only
issue was small market cap), `claude_resolved_catalyst_date` should
echo the BPC-supplied date as-is and `catalyst_date_source` should be
`"BPC docx (no resolution needed for Class A)"`.

---

## STEP 1 — SCORE AS NORMAL

After resolving the date, proceed with the STANDARD M7 scoring task
exactly as defined in the rest of this prompt: p_clinical, expected
moves, rNPV, drug profile, clinical evidence, financial overhang,
catalyst-date sanity check, key risks, thesis summary, reasoning
trace, etc. Use the RESOLVED date (not the BPC date) in your
`weeks_to_catalyst` math and in the catalyst_date_sanity_check block.

Special considerations for rescue scoring:

- **Class A (small-cap)**: scoring rubric unchanged. Note that
  sub-$30M tickers are often pre-revenue with thin floats — model
  expected_move_on_hit / on_miss accordingly (a small float amplifies
  binary moves).
- **Class B (undated/imminent)**: if your resolved date is < 14 days
  from snapshot AND the date is well-supported, you MAY still score
  but note in `thesis_summary` that the window is tight. If your
  resolved date is in the PAST, HARD RULE #8 still applies — flag it
  and the human reviewer will route to a "passed catalyst" bucket.
- **Class C (non-standard stage/type)**: for `Regulatory Decision`
  (PDUFA) catalysts, use the PDUFA P/F historical base rate
  (~75-85% for first-cycle approvals at scheduled date — see the
  base-rate table in the main prompt). For NULL `next_catalyst_type`,
  infer the most likely type from the catalyst_text and stage.

---

## SCHEMA — appended to the standard M7 output

The JSON you emit MUST include these two extra top-level fields, in
ADDITION to every field the standard M7 schema requires:

```jsonc
{
  // ... standard m7 schema ...
  "claude_resolved_catalyst_date": "YYYY-MM-DD",
  "catalyst_date_source": "<short citation string, ≤200 chars>"
}
```

These two fields are MANDATORY for rescue dispatches and will be
stored verbatim in deep_dives.claude_resolved_catalyst_date and
deep_dives.catalyst_date_source. The renderer surfaces them in the
"Rescued" tab so the human reviewer can see what date Claude actually
used + where it came from.

---

Now proceed with the rest of the standard prompt (do not skip; do not
abbreviate the rubric — all M7 HARD RULES apply unchanged).

