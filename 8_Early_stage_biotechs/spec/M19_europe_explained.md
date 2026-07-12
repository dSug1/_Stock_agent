# M19 — Europe-broad universe provider (§2.2), explained

*House-style milestone note. Companion to `decisions.md` D19, `M18_nordic_explained.md`, and
`international_expansion_plan.md`. The second Wikidata market — and the point at which the per-market
logic became a shared helper (the reuse the plan promised).*

---

## Same shape as Nordic, now factored for reuse
M18 proved the thesis (a universe provider lights up the inherited market-agnostic signals). M19 is the
same Wikidata query over a different country set, so the logic moved into
`providers/_wikidata_universe.py` — a shared `load_biotech_listings(country_qids, provenance)` carrying
the ISIN/ticker admission rail, the LEI-is-not-listed guard, dedup, and fail-soft. `providers/nordic.py`
and `providers/europe.py` are now thin wrappers supplying their country QIDs. Adding the next Wikidata
market (should one prove clean) is ~10 lines.

## Scope
Non-Nordic Western Europe: Germany, France, UK, Switzerland, Netherlands, Belgium, Italy, Spain, Ireland,
Austria. Nordic stays its own provider; identity union-find merges any overlap (and any EU name that also
files as a US ADR — e.g. Immunocore's `US…` ISIN — merges with its EDGAR entity automatically).

## Source + honest limits (unchanged from M18)
Wikidata SPARQL is the one EU-wide source that probed clean (ESMA FIRDS returned HTML; EMA's designation
files 404'd / are antibot). It's a **SEED, large-skewed** — 318 EU biotech/pharma rows, **89 with a
security id**, deduping to the admitted set. No market cap → names enter `mktcap_unknown`, so the provider
is **opt-in** (`--europe`) and a European cap-enrich must run before the $3B ceiling can drop the EU
mega-caps. The EU capital signal (per-country major-holdings, fragmented) and EMA orphan/PRIME designations
are deferred; but the inherited signals fire immediately.

## Live proof (2026-07-12)
`8_universe.py --europe` → **63 EU biotech entities** (DE 15, UK 14, IE 10, CH 10, ES 7, BE 6, FR 5…),
0 reconciliation-queued. Inherited CT.gov clinical verified live on the new names: **Argenx** 16
company-led trials (Phase 1-4), **Abivax** 20 (Phase 1-3), **Immunocore** 15, **MorphoSys** 11 — real,
clinical-stage EU biotechs, covered with zero EU-specific signal code.

## How to run / verify
```
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_nordic.py -q     # covers nordic + europe
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --europe --no-us --no-ca --no-m6
```

## Deferred / next (same as the plan)
- European cap-enrich (licensed provider) so the ceiling can exclude EU mega-caps.
- Fuller coverage via ESMA FIRDS (needs the heavy XML parse; probed as HTML via the search UI).
- EU capital signal: start with the accessible national regulators (Nordic FI registers), not the
  fragmented whole.
- EMA orphan/PRIME designation backend (a data endpoint, not the antibot HTML page).
