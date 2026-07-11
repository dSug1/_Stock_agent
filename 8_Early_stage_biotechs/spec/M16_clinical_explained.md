# M16 — Clinical-trials signal (§3.2), explained

*House-style milestone note. Companion to `decisions.md` D15 and overall spec §3.2. Adds a clinical-
stage convergence dimension that — unlike the literature signal — does not depend on the OpenAlex
author-resolution trail, so it reaches names the citation chokepoint misses.*

---

## Why this signal, and why now
The digest funnel has one chokepoint: only ~21/168 founders resolve to an OpenAlex author, so only ~12
names get an independent-citation signal, so only ~12 clear the scoring pre-filter. Clinical stage is an
**independent** line of evidence — a company that has advanced its mechanism into human trials is
corroborated by a completely different mechanism (regulators, investigators, enrolled patients) than the
academic-citation trail. It needs no author resolution, so it can surface names citations never reach.

It was also the right *buildable* choice: the originally-planned §3.3 PatentsView signal turned out to
need a (now-required) API key and its host didn't even resolve from here, so building on it would have
been building on sand. **ClinicalTrials.gov v2 is genuinely free, no key, and verified live** — probed
before a line of client code was written (the "diagnose on the real call" discipline).

## What it does
`clients/clinicaltrials.py` queries CT.gov v2 (`/api/v2/studies?query.spons=<name>`) for a company's
trials; `signals/clinical.py` keeps only studies whose **lead sponsor** (strong: the company runs it) or
a **collaborator** (weaker) actually matches the company, and emits one `clinical_trial` signal per NCT,
tagged `role` / `phases` / `phase_rank` / `status`. CLI: `8_signals.py --clinical` (`--tickers` to pin
names, `--limit` to bound). Free, no key, no spend.

**Precision rails** (mirroring the ownership signal): a bare `query.spons` hit can be an investigator-
sponsored trial merely *using* the company's drug (e.g. a cancer-center Phase 2 of an Acrivon compound,
sponsored by the center). So the company name — corporate/industry suffixes stripped to its distinctive
stem (`Acrivon Therapeutics, Inc.` → `acrivon`) — must match the lead sponsor or a collaborator by
containment, guarded by a `_MIN_CORE` length floor so a short stem can't false-match. Idempotent
(`signal_id = hash(entity, nct)`), commits per entity, fail-soft per entity.

## The reliability lesson, applied
CT.gov reads go through the **same `_net.safe_json_retry`** (429/5xx + `Retry-After` backoff) built to
fix the OpenAlex throttling, and `search_studies` returns **`None` on a failed fetch** vs `[]` on a
genuine no-result — so a transient failure is retried/skipped, never silently recorded as "no trials."
This is the anti-poisoning contract now shared across the name/citation signals.

## Not all trials are running — trial health matters
A raw trial count is misleading: CT.gov's status field distinguishes a **live** program from a **dead**
one, and a stopped trial is often a *negative* signal. TScan's "8 trials" is really **5 active + 1
completed + 1 withdrawn + 1 unknown** (a sponsor stops updating a study → status "Unknown"). So the
signal buckets status: `active` (recruiting/enrolling/active), `completed` (finished — still positive),
`stalled` (TERMINATED/WITHDRAWN/SUSPENDED — not positive), `unknown`. Only **meaningful** (active ∪
completed) trials count as de-risking evidence: `highest_phase`/`highest_phase_as_lead` are computed over
those only (a withdrawn Phase 2 can't inflate the phase), the pre-filter widening only admits a **live or
completed** company-led trial, and `evidence_summary` surfaces `active_trials` / `completed_trials` /
`stalled_trials` so the scorer sees the real split rather than a headline count.

## Security posture (API fetch)
Follows the repo baseline (SECURITY_AUDIT invariants): the host+path are the hard-coded constant `BASE`
(no user-controlled host → no SSRF); the only caller-influenced inputs are query-param *values* (sponsor,
pageToken), fully percent-encoded with `safe=''` so they can't break out of the param or inject another;
`pageSize` is int-coerced and `fields` is a fixed constant. Reads use `_net`'s 64 MiB cap; the retry
honors `Retry-After` but caps the backoff at 30 s (a hostile header can't stall the run). The response is
untrusted data — trial titles/sponsor strings flow only into the scoring packet, which the system prompt
treats as DATA to ignore-if-instruction, and the HTML render escapes at its boundary.

## How it feeds scoring
`evidence_summary` gains a `clinical_trials` block — `trial_count`, `as_lead`, `active_trials`,
`highest_phase`, `highest_phase_as_lead` — so it flows into the scoring packet automatically and the
rubric's dimension 4 (mechanism novelty & translational stage) now cites clinical stage explicitly.

**Optional pre-filter widening (opt-in, default OFF).** `config.prefilter_clinical_min_phase` (0 =
off). When set to N≥1, `scoring_candidates` also admits a name with a **company-led** trial at ≥ Phase N
*plus* a capital signal — even with zero independent citations. This is the lever that lets clinical-
stage names bypass the citation chokepoint. Default 0 keeps existing behavior exactly; the operator opts
in per run/config.

## Live results (2026-07-11)
Smoke on the named four (`--tickers MSLE,ACRV,TCRX,SER --limit 4`): 14 company-led trials found —
**Satellos 3 (Phase 2), Acrivon 3 (Phase 2), TScan 8 (Phase 1), Serina 0** — no key, no throttle.
- **Satellos** now has real Phase-2 clinical evidence, but *still* can't clear the pre-filter: it has no
  EDGAR capital signal (TSX-listed — the standing Canada-coverage gap). Clinical widening admits on
  `clinical AND capital`; Satellos fails the `capital` half. An honest, unchanged limitation — the fix is
  a non-EDGAR capital source (SEDAR+), not this signal.
- Acrivon/TScan already clear via citations, so clinical *enriches their conviction packet* rather than
  adding them.
The full-universe scan populates clinical signals broadly; with widening enabled, clinical-stage names
that have capital but no citation trail become newly scoreable (the intended digest-widening effect).

## How to run / verify
```
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_clinical.py -q      # 9 offline tests
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --clinical           # full active-universe scan (free)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --clinical --tickers MSLE,ACRV --limit 2
# to let clinical-stage names into the digest, set config prefilter_clinical_min_phase: 2, then 8_score --force
```

## Deferred / next
- Enable `prefilter_clinical_min_phase` after eyeballing the widened candidate list, then re-score.
- §3.4 FDA designations (openFDA) as another key-free convergence dimension.
- Trial *momentum* (status transitions over time) rather than a static snapshot.
