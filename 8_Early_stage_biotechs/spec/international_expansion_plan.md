# International expansion plan — Canada, Europe, Nordic, Japan, Korea

*Design note (not yet built). How to extend the pipeline beyond US to the remaining spec §2.1 markets.
Grounds the spec §8 phasing in what is actually buildable per market. Each source below should be
**probed live before coding** (the discipline that this session validated CT.gov and invalidated
PatentsView's "no key" claim) — they are recommended, not yet verified.*

---

## The architecture makes this a provider problem, not a rewrite
The pipeline is source-pluggable by design (D2 / `identity.py`): `providers/*` yield `Listing` records →
identity union-find (over LEI / ISIN / CIK / ticker+country) → canonical `entity` → the signal layer →
scoring → digest → §9 validation. **The identity, scoring, digest, and validation layers are already
market-agnostic.** Adding a market = a new **universe provider** + one or more **signal sources**; nothing
downstream changes. So the work per market decomposes into four questions:

1. **Universe** — how to enumerate the market's small/micro-cap biotechs (exchange + sector filter).
2. **Capital signal** — the local 5%-ownership-disclosure + financing regime (the §3.5 analogue).
3. **Designations** — the local regulator's expedited-pathway list (the §3.4 analogue).
4. **Clinical** — already global via CT.gov; local registries supplement.

## What already works everywhere (no per-market build)
- **Literature / independent-citation (§3.1, OpenAlex)** — global; a founder in any country resolves the
  same way. Zero change.
- **Founder extraction (§5.1, Claude web_search)** — language-capable; works globally.
- **Clinical trials (§3.2, CT.gov v2)** — a *global* registry; already captures many ex-US sponsors.
- **Identity (GLEIF LEI + ISIN + ticker+country union-find)** — built global from day one.
- **FDA designations via 6-K (§3.4 / M17)** — any foreign issuer that cross-lists on a US venue AND
  seeks an FDA designation is **already captured** (it discloses in a 6-K, which M17 searches).
- **Cross-listed foreign capital (§3.5 / D16)** — any foreign private issuer filing 6-K/Form-D/F-10/
  SCHEDULE-13G with the SEC is **already captured**. This is why the *valuable* cross-listed names in
  every market below already have partial coverage; the gap is the domestic-only names.

So the per-market build is really: **(1) a domestic universe enumerator + (2) a domestic capital signal
+ (3) a domestic designation list.** Everything else is inherited.

## Per-market plan

| Market | Universe source | Capital signal (§3.5) | Designations (§3.4) | Buildability |
|---|---|---|---|---|
| **Canada** | TMX/TSX + TSXV + CSE listing directories (Health-Care filter). **No clean free API** — scrape, or a licensed feed. | **SEDI** (insider) + **SEDAR+** early-warning reports (10% rule). **SEDAR+ has no API + is bot-protected** — the hard part. | Health Canada NOC / Priority Review (+ FDA-via-6-K already captured). | **Cross-listed: DONE** (D16/M17/CT.gov). **TSX-only: hard** — blocked on SEDAR+/TMX. |
| **Europe (EU/UK/CH)** | **ESMA FIRDS** (free EU-wide instrument reference data: ISIN + CFI equity filter + issuer LEI) — the EU-wide enumerator. UK: LSE/AIM directory. CH: SIX directory. | EU Transparency-Directive major-holding notifications, filed **per national regulator** (FCA/BaFin/AMF/…) — **fragmented, no single API**. UK RNS is scrapeable. | **EMA** orphan designations + **PRIME** scheme — published + downloadable (the EU §3.4 analogue). | **Universe + designations: buildable** (FIRDS + EMA). **Capital: hard** (per-country). |
| **Nordic (SE/DK/NO/FI)** | Nasdaq Nordic + **Spotlight/NGM** (Sweden's biotech-heavy junior venue) + Oslo Børs. Also in ESMA FIRDS (EEA). | **More accessible than the rest of EU**: Swedish **FI** insider (PDMR) + major-holdings (flaggning) registers are downloadable; DK/NO Finanstilsynet similar. A good first EU capital target. | EMA (as EU). | **Most tractable EU slice** — FIRDS universe + accessible FI capital registers + EMA. |
| **Japan** | **JPX listed-company file** (free Excel/CSV; TSE **Growth** market = the small-cap/startup tier; 33-sector "Pharmaceutical" filter). | **EDINET API** (FSA's EDGAR-equivalent, **free, documented**) — large-shareholding reports (大量保有報告書, the 5% rule) + securities registration statements (financings). A real §3.5 analogue. | **PMDA**: SAKIGAKE (≈Breakthrough) + orphan designation lists (Japanese, scrapeable). | **More buildable than assumed** — EDINET is a free filing API. Cost = Japanese-language + JP universe file. |
| **Korea** | **KRX / KIND** listings (KOSDAQ = the biotech-heavy junior market; pharma/bio sector filter; free). | **DART OpenAPI** (FSS's EDGAR-equivalent, **free API + key + English fields**) — large-holding (5%) reports + financings. Spec §8 already flagged DART as workable. | **MFDS** orphan + expedited-pathway lists (+ FDA-via-6-K). | **Buildable** — DART is a clean free API with English. Cost = KR universe file + MFDS scrape. |

## The key insight that revises the spec's phasing
Spec §8 sequenced JP/KR **last** (assumed hardest). But the hardest part of any market is the **capital
signal**, and **Japan (EDINET) and Korea (DART) both expose it as a free, documented filing API** — real
EDGAR analogues — whereas **Canada (SEDAR+) and continental EU (per-country major-holdings) do not**. So
by *buildability of a complete vertical*, the order inverts:

1. **Nordic** (Sweden-first) — FIRDS universe + accessible FI capital registers + EMA designations +
   CT.gov. The most complete EU slice with real capital data.
2. **Korea** — KRX universe + **DART API** (capital) + MFDS + CRIS/CT.gov. A clean free filing API.
3. **Japan** — JPX universe + **EDINET API** (capital) + PMDA + jRCT/CT.gov. Same shape, + language cost.
4. **Europe (broad)** — FIRDS universe + EMA designations + CT.gov, with per-country capital added
   opportunistically (start where regulators are open).
5. **Canada (TSX-only)** — lowest *incremental* value (the cross-listed names are already covered) and
   highest friction (SEDAR+ no-API). Do the **TMX universe scrape** for coverage, lean on the inherited
   CT.gov + FDA-via-6-K signals, and treat SEDAR+/SEDI capital as a later scrape or licensed-feed item.

## Concrete build shape (per market)
- `providers/<market>.py` — enumerate listings → `Listing` records (name, ticker, exchange, country,
  ISIN, LEI, local-id, sector). Feeds the existing `identity.reconcile` unchanged.
- `clients/<source>.py` — one client per source (edinet / dart / firds / ema / tmx), each: hard-coded
  host (no SSRF), `_net.safe_json_retry`, 64 MiB cap, fail-open — the established client contract.
- **Capital**: `signals/capital_markets.py` grows a per-market backend (EDINET/DART return the 5%-rule
  reports; map to the same `capital_markets` / `ownership_crossing` signal types).
- **Designations**: `signals/designations.py` grows an EMA/PMDA/MFDS backend alongside the EDGAR one
  (same `regulatory_designation` signal type, `authority` field distinguishes FDA/EMA/PMDA/MFDS).
- **Clinical**: add jRCT/CRIS/CTIS as supplementary backends to `signals/clinical.py` (same signal type).
- Market-cap enrich: the pending yfinance→licensed-provider swap covers all markets at once (global
  quotes), so it's a shared prerequisite for non-US caps.

## Cross-cutting prerequisites (do once, benefit all markets)
- **Licensed market-data provider** (replaces yfinance) — needed for non-US market caps + the public-deploy
  ToS constraint. One swap, all markets.
- **Language handling** — JP/KR filings + designations are non-English. The structured fields (EDINET/DART
  large-holding reports, JPX/KRX sector codes) are tractable without translation; free-text designation
  scrapes need a light translate/normalize step (or a cheap Claude pass on the matched excerpt).
- **Per-authority `designation`/`capital` provenance** — add an `authority`/`source_market` tag so the
  digest + scoring can weight (e.g. an EMA PRIME ≈ an FDA Breakthrough) and the §9 harness can slice by market.

## Honest limitations (carry forward, per spec §9)
- **Coverage will stay uneven**: US/CA-cross-listed/JP/KR (via EDINET/DART) will be well-instrumented on
  capital; continental-EU-domestic and TSX-only names will have the weakest capital coverage until the
  per-country/SEDAR+ scrapes land. A "no signal" from those markets is not informative yet.
- **Every source above is UNVERIFIED** — probe each endpoint live before building (CT.gov worked,
  PatentsView's "no key" did not). Build-first API parsers, then wire the provider.
