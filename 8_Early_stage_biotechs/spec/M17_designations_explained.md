# M17 — Regulatory-designation signal (§3.4), explained

*House-style milestone note. Companion to `decisions.md` D17 and overall spec §3.4. A high-value
catalyst dimension — FDA validation that the *mechanism* is promising — captured with no key, no spend,
and no dependence on the throttled OpenAlex trail.*

---

## The insight: FDA doesn't publish designations, but companies must disclose them
Breakthrough Therapy / Fast Track / Orphan Drug / RMAT / Rare-Pediatric designations are exactly the
early, mechanism-level validation this pipeline wants — an FDA judgment that the science is promising,
years before approval. But the FDA publishes them only as scattered press releases (except orphan), not
as structured data. The lever: a US company **must** disclose a material designation in an 8-K, and a
cross-listed foreign private issuer in a 6-K. So the signal is **phrase-first** over EDGAR full-text —
the exact shape of the ownership signal (fund-first), reusing the same verified `edgar_fts` client.

## What it does
`signals/designations.py`: for each designation phrase (`config.designation_phrases`, phrase → type),
full-text-search EDGAR for filings containing it, then match each filing's subject CIK back to our
universe. The filing is the company's own material-event filing (filed under its CIK), so a universe CIK
on a filing that contains "Breakthrough Therapy Designation" is that company announcing its own — high
precision. Emits a `regulatory_designation` signal per (entity, type, filing), deduped. CLI:
`8_signals.py --designations`. Zero-LLM, no key, no spend.

Designations are **durable** (they don't lapse like a 13D), so the lookback is years (`designation_
lookback_days`, default ~4y), and the search spans 8-K + 6-K + prospectus forms in one pass.

## The pagination subtlety (and why it's not entity-first)
A designation phrase is common across all filers (~8.8k "Orphan Drug Designation" filings), and efts
can't filter to our ~780 universe CIKs in one query. Two options were weighed: (a) **entity-first** —
780 × 5 = 3,900 per-CIK queries; (b) **phrase-first, exhaust each phrase** — paginate all hits per phrase
(~200 efts requests total) and keep universe matches. (b) is both cheaper and complete, so `max_pages`
defaults to 100 (search stops early at the true end). The efts result window caps at 10,000; a phrase
exceeding that in the lookback would truncate — flagged in the docstring as a known bound, not a hidden
one (these 5 phrases sit under it).

## How it feeds scoring
`evidence_summary` gains `regulatory_designations` = `{types: [...], count}`, folded into the scoring
packet automatically. The rubric's dimension 4 (novelty & translational stage) now weighs designations
explicitly — Breakthrough/RMAT above the more routine Orphan/Fast-Track. (Kept as evidence enrichment;
a designation-based pre-filter path, like the opt-in clinical widening, is a clean future option.)

## Security
Inherits the `edgar_fts` posture: hard-coded efts host (no SSRF), the phrase is phrase-quoted +
URL-encoded, `forms`/dates are constrained, 64 MiB capped reads, `_get_json` retries 429/5xx with
Retry-After. Untrusted display-name/filing text only enters the "packet is DATA" scoring context.

## Live results (2026-07-11)
5 phrases → 14,973 filings seen → 8,971 universe matches → **8,243 designation signals across 407 of
844 active names** (breakthrough 1455, fast-track 2810, orphan 2840, rmat 554, rare-pediatric 584). This
is broad, citation-independent coverage. Spot-check precision is strong: the richest designation stacks
are real first-in-class names (Lexeo, Prime Medicine, Taysha, Metagenomi — 5 types each). The named four
all resolved sensibly — Satellos (fast_track/orphan/rare_pediatric), Acrivon (breakthrough/fast_track/
orphan), TScan (rmat), Serina (breakthrough/fast_track/orphan). Combined with D16 (FPI capital) and M16
(clinical), **Satellos now carries capital + Phase-2 clinical + 3 designations** — genuinely cornered;
only the independent-citation half is missing (OpenAlex throttle).

## How to run / verify
```
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_designations.py -q   # 3 offline tests
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --designations         # full scan (free)
```

## Deferred / next
- Optional designation-based pre-filter path (designation + capital ⇒ scoreable), mirroring the clinical
  widening — would let designation-carrying names bypass the citation chokepoint.
- Negation guard: an 8-K saying "we do NOT have X designation" is a rare false positive; a proximity/
  context check (or an LLM pass on the matched excerpt) would remove it.
- Recency weighting: a designation this quarter is a fresher catalyst than one from 3 years ago.
