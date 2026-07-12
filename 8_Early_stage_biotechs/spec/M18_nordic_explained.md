# M18 — Nordic universe provider (§2.2 non-US), explained

*House-style milestone note. Companion to `decisions.md` D18, `international_expansion_plan.md`, and
overall spec §2.2. The first non-US, non-EDGAR market — and a proof that a universe provider is all it
takes to light up the market-agnostic signals.*

---

## The thesis of the build: a market = a universe provider
The pipeline is source-pluggable (D2): once a name is a canonical `entity`, the **inherited** signals are
market-agnostic — CT.gov clinical (global), FDA-designations-via-6-K + FPI capital (cross-listed),
OpenAlex literature, GLEIF LEI. So adding Nordic coverage is, first and foremost, adding an **enumerator**.
Everything downstream is free. This milestone builds that enumerator and proves the claim live.

## Source choice — probed, not assumed
Per the session's discipline (CT.gov worked; PatentsView's "no key" did not), the candidate Nordic
sources were probed before any code: **ESMA FIRDS + EMA returned HTML/antibot, the Nasdaq-Nordic feed
timed out — only Wikidata SPARQL returned clean JSON.** So `clients/wikidata.py` (SPARQL, hard-coded
host, `safe_json_retry`, capped reads) + `providers/nordic.py` query Sweden/Denmark/Norway/Finland/
Iceland biotech + pharma companies (`P452 ∈ {biotech, pharma}`, `P17 ∈ Nordic`), mapping each to a
`Listing` (name, ISIN, LEI, country, ticker, exchange) into the standard identity flow.

## The precision rail: admit on a SECURITY id, not an LEI
First live run admitted 29 rows — but several were **private** companies (LEO Pharma, Fertin, Nomeco,
Xellia) that happen to hold an LEI. LEI ≠ listed: every legal entity can have one. Fixed to admit only on
an **ISIN or ticker** (a tradeable-security identifier); LEI is kept for identity union-find but is not a
sole admission key. That tightened 29 → **15 genuinely-listed names**, no private-company pollution.

## Honest limitations (spec §9 — flagged, not hidden)
1. **Coverage is a SEED, not complete.** Wikidata skews to better-known names, so micro-caps on Spotlight/
   NGM are missing. Fuller coverage needs a Nasdaq-Nordic/Spotlight listing scrape (deferred — those feeds
   didn't probe clean).
2. **No market cap.** Wikidata gives none, so Nordic names enter `mktcap_unknown` (kept + flagged, recall-
   safe). The $3B ceiling therefore can't yet exclude the Nordic mega-caps (Novo/Genmab/Lundbeck/AZ),
   which sit in the seed pending a **Nordic cap-enrich** (the licensed-provider swap covers all markets).
   Because of this, the provider is **opt-in** (`--nordic`) so it never pollutes the default US/CA build.

## Live proof (2026-07-11)
`8_universe.py --nordic` → **15 Nordic listed entities** (DK 7, SE 7, FI 1), 0 reconciliation-queued. The
inherited-signal claim, verified against the live CT.gov API on the new names:
- **Zealand Pharma** — 18 company-led trials (Phase 1-3); **Bavarian Nordic** — 15 (Phase 1-4);
  **Camurus** — 9 (Phase 2-4).
i.e. the moment a Nordic name is in the universe, the clinical signal (and FDA-via-6-K / OpenAlex / GLEIF)
covers it with zero additional per-market code. The seed's small/mid-caps (Bavarian Nordic, Zealand,
Camurus, Synact, Vicore, Sobi) are the value; the mega-caps are flagged for the cap-enrich pass.

## How to run / verify
```
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_nordic.py -q          # 4 offline tests
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --nordic --no-us --no-ca --no-m6
# then the inherited signals work as-is:  8_signals.py --clinical / --designations ; 8_extract ; ...
```

## Deferred / next
- **Nordic cap-enrich** (ticker+exchange-suffix via a licensed provider, or ISIN→ticker via OpenFIGI) so
  the ceiling can exclude the mega-caps — the one thing gating Nordic names' entry to scoring.
- Fuller micro-cap coverage: Nasdaq-Nordic / Spotlight / NGM listing scrape.
- Nordic-native capital signal: Swedish FI (PDMR insider + flaggning major-holdings) — the accessible
  slice of the otherwise-fragmented EU capital regime.
- Reuse `clients/wikidata.py` for the other markets' universe seeds (same query, different country QIDs).
