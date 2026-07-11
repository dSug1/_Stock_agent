# M10 — Literature & citation signal (OpenAlex), explained

*House-style milestone note. Companion to `decisions.md` D9 and overall spec §3.1. This is the module's
**thesis signal** — it turns founder names (M9) into evidence of independent scientific corroboration.*

---

## What it does
For each founder (from M9's `founder` table), resolve the OpenAlex author and write `literature`
signals on the company: recent **publications** by the founder-scientist, and **citations of the
founder's foundational paper**, each tagged independent / same-institution / self. Zero-LLM, free. CLI:
`scripts/8_signals.py --literature`.

## Why this is the whole point
Module 8 exists to surface *independently-corroborated but under-recognized mechanisms*. The sharpest
form of that: a company's founding science (a founder's foundational paper) being **cited and built on
by independent labs**. That's a citation-network query — resolve the founder → find their most-cited
paper → count who cites it and whether the citing lab is independent. M10 computes exactly that.

## How it works
`clients/openalex.py` (free, no key — the spec's recommended source for author + citation-network +
institution data at scale):
- **Author resolution is high-precision.** `pick_author` requires a name-token match (surname + subset,
  so "Stuart Rich" matches "Stuart M. Rich") AND, when the founder's institution or company is known, an
  institution match against the author's `last_known_institutions` / `affiliations`. Unique match →
  accept; several → most-cited; none → skip. A wrong author would poison the whole signal, so recall is
  deliberately conservative.
- **Foundational paper** = the author's most-cited work (`sort=cited_by_count:desc`).
- **Citations** pulled via the `cites:<work_id>` filter, capped at `literature_max_citing`.

`signals/literature.py` runs the sweep, persists per founder (crash-safe), and is idempotent (stable
signal_id; skips founders already resolved via `literature_at`). Each citing work gets a cheap
**independence heuristic** (`classify_independence`): the founder in the citing authors → *self*; the
founder's institution/company among the citing institutions → *same_institution*; otherwise
*independent*. Claude's §5.2 classification refines this later.

## On Module 6's OpenAlex dismissal
M6 dismissed OpenAlex (its D9) because its **institution registry covered only ~29/589 small biotechs**
— but that was *company→institution* matching. Module 8 uses *author→works→citations*, which is
OpenAlex's core strength, and disambiguates on the founder's institution. The dismissal doesn't
transfer. The **rate-limit** lesson does: we use the polite pool (`mailto`), a shared limiter, and
fail-open fetches.

## Live validation
Stuart Rich (Tenax Therapeutics' CMO, from M9) resolved cleanly — OpenAlex's `last_known_institutions`
for the top "Stuart Rich" lists both *Northwestern University* and *Tenax Therapeutics*, so the
company-name hint alone nails the match. His foundational paper (*Survival in Primary Pulmonary
Hypertension*, 1991, 3,507 citations) drew **198 independent-lab citations** (2 same-institution, 0
self) plus 11 recent publications — computed in 12s. The other two founders (institution "Unknown" /
"None identified") correctly did not resolve: with no usable institution hint and a non-unique name,
`pick_author` skips rather than risk a wrong author.

## How to run / verify
```
cd 8_Early_stage_biotechs
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_literature.py -q   # 12 offline tests (no network)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --literature --limit 20 --verbose
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --stats
```

## Deferred (honest scope)
- **The full sweep depends on the full M9 founder sweep** — only 3 founders exist so far (the M9 smoke).
  Run M9 at scale, then M10 resolves far more authors.
- **§5.2 Claude independence classification** — refine the cheap heuristic (co-authorship networks,
  shared grants, advisor relationships) on the citations M10 surfaces.
- Author-resolution recall is conservative; a fuzzy fallback (ORCID, DOI cross-ref) could lift it if
  precision holds. Founders with "Unknown" institution need better M9 extraction to resolve.
- Non-English repositories (J-STAGE, KISTI/RISS, §3.1) for JP/KR founders — later.
