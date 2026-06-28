# M2b explained — automating the full listed universe

*What milestone 2b adds: replacing the hand-seeded CSV with an automated, multi-market universe.
Companion to `SPEC_acrivon_pattern_screener.md` §5.1 and §6 (`listings.py` `[BUILDER DECISION]`).*

---

## The gap it closes

M2 shipped Stage 0a's union-of-nets logic but only the `seed_list` net had a data provider, so the
universe was just the analyst's CSV — which defeats the point of *discovering* across the whole
biotech market. M2b builds the **`ListingDirectoryProvider`**: the automated net that enumerates the
full listed universe across **US + Sweden + Denmark + the broader EU**, so the `sector` and
`name_keyword` nets fire on thousands of real companies. The seed CSV stays as one *additional* net —
the highest-precision analyst names always survive; the directory is additive, never a replacement.

## The free hybrid (no API key)

| Source | Covers | How |
|---|---|---|
| **SEC EDGAR** (`clients/sec_sic.py`) | **US** listed companies | `browse-edgar?action=getcompany&SIC=…&output=atom` enumerates every filer under each broad SIC; `company_tickers_exchange.json` maps CIK → (ticker, exchange), **preferring the common-stock ticker** (shortest) so a warrant/unit row can't clobber the common (the Ginkgo `DNA`/`DNABW` bug). A CIK with no ticker = not listed → skipped. **SIC set (12, broadened 2026-06-28):** 2833/2834/2835/2836 (medicinals/pharma/IVD/biologics), 8071 (molecular-diagnostics labs — Grail/Castle/CareDx/Fulgent), 8731 (research), 3826/3827/3829 (lab instruments), 3841/3842/3845 (medical devices/electromedical). |
| **Wikidata** (`clients/wikidata.py`) | **Europe + Nordic** (incl. SE/DK) | SPARQL: companies whose *industry* is biotech/pharma, *country* is in-scope, with a *stock-exchange listing* carrying a *ticker*. |
| **yfinance** (`clients/market.py`) | per-ticker enrichment | basic market cap, currency, country, liveness for each enumerated ticker. |

All three are wired through `clients/_net.py`: 64 MiB capped reads, the repo `USER_AGENT` (loaded
from `.env` — **SEC's `cgi-bin` 403s a UA without a contact**, so a real name+email in `.env` is
required), per-source rate limiting, `defusedxml`, and **fail-open** everywhere (a dead source yields
nothing rather than crashing the run). Endpoints are hard-coded public APIs — no SSRF surface.

### Two real-world quirks handled
- **SEC `ARRAY(0x..)` bug:** the multi-result atom feed serializes company *names* as Perl array
  refs. We don't trust the atom name — we extract only the **CIK** (reliable in `<cik>`, the `<id>`
  urn, and the link href) and take the clean name + ticker from the cik↔ticker map.
- **Yahoo symbols for non-US:** Wikidata gives a bare ticker; `directory.yahoo_symbol` attaches the
  market suffix from the exchange label (else country) and normalizes — e.g. Copenhagen `GMAB` →
  `GMAB.CO`, Stockholm `BIOA B` → `BIOA-B.ST` — so yfinance can resolve it.

## Market cap at this stage — basic, not fully-diluted

Per the analyst's instruction, market cap here is **basic** (yfinance `marketCap`, shares × price).
**Pre-funded warrants / full dilution are NOT included yet** — `market_cap.use_fully_diluted` is
`false` and the `mktcap_usd_fd` column carries the basic cap for now. Refining to FD/PFW share counts
(cross-checked against filings) is a deferred TODO. Non-USD caps are FX-normalized to USD via
`FXConverter` (static prototype rates).

## How it runs — enumerate fast, enrich separately

Enumeration and enrichment are **decoupled** (this was a real bug fix — see below):
1. **Stage 0a enumerates and PERSISTS the whole universe in seconds** (SEC SIC + Wikidata; no
   per-ticker network). ~900 companies land in the store immediately, each flagged `mktcap_unknown`.
2. **Stage 0b enriches** (`--enrich-yf`): one yfinance call per company to fill cap/liveness, then
   the band applies. This is the slow step (minutes at universe scale; Yahoo may throttle) — but the
   universe is already saved, so an interrupted enrich never loses it. Bound it with
   `stage0b.max_enrich` (0 = unlimited); unenriched companies stay `mktcap_unknown` (kept, never
   dropped).

```bat
run_6_Biotech_platform_discoverer.bat            :: --universe --enrich-yf + render (default)
```
For a fast seed-only run, call `scripts/6_screen.py --stage 0` without `--universe`. To see the full
universe *without* waiting for enrichment: `scripts/6_screen.py --stage 0a --universe` then render.

> **Bug fixed (2026-06-28):** the directory originally enriched *inside* `fetch()`, so Stage 0a
> wouldn't persist anything until ~900 yfinance calls finished — a stall there left the store empty.
> Enrichment moved to Stage 0b so the universe persists first.

## Validated live (2026-06-28)

Bounded probes against the real APIs:
- SEC SIC 2836 (2 pages) → **76 real US biotech listings** (4D Molecular, Acumen, Adaptimmune…), each
  with ticker + SIC.
- Wikidata DK → **9 records** (Novo Nordisk, Genmab, Zealand, Bavarian Nordic, Lundbeck…) with
  exchange + sector tag, mapped to `.CO` Yahoo symbols.
- **Full enumeration** (12 SICs + SE/DK/EU, no enrich) → **1247 records → 1216 admitted & persisted
  at Stage 0a in seconds**, all via the `sector` net. This is the real universe size — not a handful.
- **Coverage check vs 2_Funds (2026-06-28):** of the 21 biotech funds' surfaced candidates, comp6
  covers **126/489** — the other 363 are correctly out of scope (banks, REITs, software, oil — the
  funds' diversified non-biotech holdings). The check found 25 biotech names mis-coded outside the
  original 5-SIC set (esp. 8071 diagnostics) + the `DNA` warrant bug; both fixed, recovering 24/25
  (the last, `EYE`/National Vision, is optical retail — intentionally out of scope).

## Honest limitations (refinement backlog)

- **Wikidata is notable-entity-biased** — strong on mid/large and many small caps, but misses some
  nano-caps. It's a real free pan-European net to start; a **licensed vendor screener** (FMP/EODHD)
  remains the eventual upgrade for exhaustive EU coverage.
- **yfinance at universe scale is slow and rate-limited**, and ToS-limited for public deploy — the
  same swap-before-hosting caveat as the rest of the repo.
- **Basic market cap only** (PFW/FD deferred, above).
- **Live schemas can drift** — the parsers are defensive and fail-open; the parsers are unit-tested,
  and the SEC + Wikidata shapes were validated live on 2026-06-28.

## What's proven (tests)

`tests/test_directory.py` — 15 tests (**51 total** in the component), all offline: the SEC atom parser
(incl. the ARRAY-bug shape), the cik↔ticker join, the Wikidata SPARQL parser + region→QID expansion +
GICS tagging, the Yahoo-symbol mapping table, and directory composition with enrichment (network
monkeypatched). Live endpoint shapes were validated by direct probe.
