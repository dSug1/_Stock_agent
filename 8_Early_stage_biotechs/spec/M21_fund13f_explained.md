# M21 — 13F-holdings seed provider (from 2_Funds_parser), explained

*House-style milestone note. Companion to `decisions.md` D23. A cross-module bridge: the specialist-fund
holdings that `2_Funds_parser` already tracks become a universe seed that closes real coverage gaps.*

---

## Why it exists (the audit that motivated it)
A coverage audit cross-referenced the Q1-2026 holdings of the **21 specialist biotech funds** tracked in
`2_Funds_parser` (Baker Bros, OrbiMed, RA Capital, Perceptive, RTW, Deerfield, BVF, Cormorant, EcoR1,
Avoro, Boxer, Sofinnova, PFM, SIO, Redmile, + the biotech VCs) against module 8's universe. Result: **79%
covered** — 55% in the active universe, 24% correctly excluded as >$3B large-caps (Lilly, Merck, Amgen…).
But **~10 genuine small/mid US therapeutics were missing entirely** despite being held by these funds —
Apellis, Celcuity, Terns, Day One, Arcellx, Soleno, Definium, Sensei, Amicus, Centessa (UK). Root cause:
module 8's EDGAR SIC enumeration is **page-capped** (`--max-pages 30`), so not every filer under a biotech
SIC is reached. **A name a top specialist fund holds is thesis-relevant by construction** — so it belongs
in the universe. This provider makes that true automatically.

## What it does (mirrors the M6 seed — D2)
`providers/fund13f.py` opens `2_Funds_parser/2_fundparser.db` **READ-ONLY** (`mode=ro`; module 8 never
writes to it) and emits a `Listing` per specialist-fund-held name. **Default-ON** (`use_fund13f=True`,
`--no-fund13f` to skip), exactly like the M6 seed. Two disciplines keep it clean:

1. **Exclude broad / mis-scoped filers.** A filer holding more than `max_holdings_per_filer` (500) distinct
   names in the latest quarter is a diversified manager, not a biotech specialist — this generic rule
   auto-drops the mis-scoped *"Janus Henderson Group PLC"* entry (its CIK is the whole $-manager: a
   ~2,300-position 13F full of Broadcom/Nvidia/Netflix/Boeing, not the biotech fund). That's a
   `2_Funds_parser` data issue (it should retarget the CIK to the biotech sub-fund); the threshold rule
   protects module 8 regardless, so its non-biotech positions never pollute the universe.
2. **Authoritative resolve + biotech filter by SIC.** Each held ticker is looked up via EDGAR
   ``browse-edgar?action=getcompany&ticker=…``, which returns the **CIK + SIC + name in one call and
   covers ALL listed filers** — this matters because SEC's ``company_tickers.json`` is *incomplete* (it has
   only ~9,300 entries and is missing e.g. Apellis/Terns/Day One), which is itself a root cause of the
   original gaps (a CIK the EDGAR enumeration finds but that's absent from that file is silently dropped).
   A name is admitted only if its SIC is in a **biotech-adjacent** allow-set — therapeutics (2833/2834/
   2836), diagnostics/labs (**2835/8071** — SEC classifies real biotechs like Celcuity under 8071),
   tools/research (8731/3826/3827), devices (3841/3845). Broader than the EDGAR-enumeration SIC set on
   purpose: the holdings are already pre-filtered to specialist-fund conviction, so the full drug/dx/tools/
   device span is admitted while utilities (49xx) / insurers / ADRs are rejected by SIC. Because admission
   is CIK-keyed, union-find merges a held name with its EDGAR/M6 twin when one exists; a genuine gap (no
   EDGAR entity) becomes a **new** entity, ``mktcap_unknown`` until the enrich stage prices it.

*Debugging note:* the first cut resolved via SEC's ``company_tickers_exchange.json`` and used the narrow
EDGAR SIC set — it admitted 331 names but **added the actual gaps as 0 new entities**, because (a) the SEC
ticker file was missing Apellis/Terns/Day One and (b) Celcuity's 8071 SIC failed the narrow filter. Both
were caught by verifying against the real gap tickers (the "diagnose on the real call" discipline), then
fixed with browse-edgar resolution + the broadened SIC span.

Fail-soft throughout: a missing DB, an unresolved ticker (warrants/units aren't in the SEC common-ticker
map → skipped), or a SIC-fetch failure just skips that name — never aborts the build.

## Cost & placement
It runs at build time alongside the other providers. The one network cost is a per-CIK SIC lookup
(submissions feed) for each resolved held ticker (~hundreds, paced at SEC's rate) — comparable to the
EDGAR enumeration's own pagination cost, and only at build time, not per signal-run.

## How to run / verify
```
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_fund13f.py -q          # 4 offline tests
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --no-us --no-ca --no-m6  # 13F seed only (additive)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py                          # full build incl. 13F (default)
```

## Related fixes shipped with this milestone (D23)
- **EDGAR provider incompleteness fixed.** The SEC-ticker-file gap this audit surfaced was a latent bug in
  `sec.build_us_listings` too — it dropped any SIC-enumerated CIK absent from the ~9.3k-entry ticker file.
  Now `resolve_missing=True` falls back to the submissions feed for unmapped CIKs, recovering ALL biotech
  filers (not just specialist-held). This ran in the full universe re-build.
- **Render wired into the main bat.** `run_8_Early_stage_biotechs.bat` now calls `run_8_render.bat` after
  the build, so a run ends with the status+digest HTML rendered and opened.
- **2_Funds_parser** `sec_ticker_resolver` docstring corrected (it's a last-resort fallback and the SEC
  file is genuinely incomplete); the broad-filer "Janus Henderson Group PLC" seed entry was removed.

## Deferred / next
- A specialist-fund-held name is a strong *conviction* prior, not just coverage — a future signal could
  weight names by how many specialists hold them (distinct from the M8 ownership-crossing signal).
- `2_Funds_parser` should retarget the Janus CIK to its biotech sub-fund (its own data fix), after which
  the broad-filer exclusion becomes belt-and-suspenders.
- Consider all-time (not just latest-quarter) holdings if historical specialist conviction is wanted.
