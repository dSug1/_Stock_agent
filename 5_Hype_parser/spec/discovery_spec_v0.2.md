# Theme Discovery — module specification v0.2 (DRAFT)

**Status:** draft. Replaces v0.1 (broad-ingest-and-cluster — rejected: too big, too noisy, missed the
real edge). This version discovers themes from the **convergence of independent expert juries** —
not from document volume. Footprint target: **a couple of MB.** Reuses the D4 source registry, the D5
forward archive, the diffusion engine, and the `2_Funds_parser` pipeline.

> Read `decisions.md` D21/D22 first, then `config/sources.yaml` (the `edge_type: awards` rows — the
> juries are already catalogued, with `jury_credibility` + `diffusion_position`).

---

## 0. The idea (in one paragraph)

A theme worth catching early is one that **people with deep field knowledge are already recognizing**
while the mainstream is not. Those experts leave sparse, public, structured traces: early-stage
technology/biotech **award shortlists**, **breakthrough designations**, **agency priority calls**, and
the **new positions of specialist investment funds**. When *several independent* such juries point at
the **same** nascent area, that convergence is the discovery signal. This is tiny data (juries publish
annually, dozens of entries each), high signal, and forward-looking — the opposite of clustering
millions of papers.

---

## 1. Why volume-clustering was wrong (and this is small)

- **Storage:** juries are annual and sparse. ~10–15 jury sources × ~10–100 entries/year × ~10 years ≈
  **a few thousand rows**, each a short text + metadata ≈ **1–3 MB total** (embeddings of the *signal
  texts* only, not a corpus). Meets the couple-of-MB budget. No bulk paper/patent corpus exists in
  this design.
- **Noise:** experts pre-filter. An R&D 100 or Fierce 15 shortlist is already a curated judgment; we
  inherit that filtering instead of fighting it with clustering. **Patents are dropped** from
  discovery (the user's point: too broad/noisy) — at most a later *targeted* confirmation per theme.

---

## 2. The jury set (already in the registry — D4)

The signal sources exist in `config/sources.yaml` as `edge_type: awards` (+ FDA + agency), each tagged
`jury_credibility` and, crucially, `diffusion_position` — which says *where on the curve* the jury
fires:

| `diffusion_position` | role | sources (registry) |
|---|---|---|
| **leading** — FINDS nascency | the discovery drivers (early, long runway) | MIT TR-10 Breakthrough, R&D 100, Fierce 15, RSA Innovation Sandbox, DARPA/IARPA BAAs |
| **bridge** — mid-diffusion | confirmation + biomedical gate | FDA Breakthrough Therapy (BTD), FDA guidance, Falling Walls |
| **denominator** — CONFIRMS, does NOT find | sets the clock / measures lateness | Nobel, Turing, Lasker, Breakthrough Prize, Collier, QE Prize |

**The Pouzin nuance, encoded:** a foundational award (Nobel/Turing-class, or the 2001 internet award to
Louis Pouzin) is a **denominator** signal — recognition has *arrived*; it dates the theme and bounds
the remaining runway, it does not *find* an emerging theme. Discovery is driven by **leading** juries;
denominator juries only adjust the **time horizon** (§5) and curve position.

**Expanded leading-jury catalogue (D23):** the registry now holds **~79 leading sources** across all
the industries the user named — semis (EE Times Silicon 100, SEMI S3, DARPA ERI), sensors (Best of
Sensors), photonics/opto-electronics (SPIE Startup Challenge, SPIE Prism), batteries/energy (BNEF
Pioneers, ARPA-E), robotics (RBR50), quantum (Quantum Insider), materials (JEC Composites), fintech
(Forbes Fintech 50, CB Insights), cyber (SC Awards Emerging, Black Hat Arsenal, Gartner Cool Vendors),
software/cloud (CNCF Sandbox, GitHub Accelerator), consumer/marketplaces (a16z Marketplace 100, Product
Hunt), cross-industry private-firm lists (WEF Tech Pioneers, Forbes AI 50 / Next-Billion, CNBC
Disruptor 50, In-Q-Tel, LinkedIn Top Startups), biotech/medtech (Endpoints 11, MedTech Innovator,
Fierce Medtech), and EU/US gov priority programs (EIC Accelerator/Pathfinder, NSF Convergence, ARPA-H).
**Build-first = the machine-readable (`access_method: api`) feeds** — YC (yc-oss JSON), CNCF
(`landscape.yml`), ARPA-E (data.gov CSV), Product Hunt API, EIC/CORDIS, SBIR/NSF, Nobel — the rest are
scraped from the OD-2 forward-archive snapshots. Many of these **name PRIVATE/unlisted firms** (the
silicon_photonics goal): In-Q-Tel, SEMI S3, SPIE Startup Challenge, EIC, Fierce Medtech, MedTech
Innovator, the Forbes/CB-Insights private-only lists.

**Plus specialist smart-money (a capital jury) — the fund cross-reference (D25):** specialist funds'
**new** 13F positions are an independent expert jury — money from people who study one field — and a
**confirmation** signal on a theme's *listed* (Track-A) constituents. Mechanism (see §4b).

---

## 3. How ingest is managed (tiny, reuse-first)

- **The forward archive already captures these.** Most jury sources are `forward_only` + `scrape`, and
  the D5 OD-2 forward archive (`5_archive.py`) **already snapshots them** (34/39 fetchable). Discovery
  does **not** add a new bulk ingest — it **parses the existing snapshots** into structured rows.
- **Per-source parsers** turn each annual snapshot into `jury_signals` rows: `(source_id, year,
  item_text, entity, entity_type, url)` where `entity` is the recognized technology/company/therapy.
  Small, append-only, idempotent (dedup on `(source_id, year, item_hash)`).
- **A few have clean APIs** (Nobel; some via Wikidata) — use those directly instead of scraping.
- **Specialist-fund signals** are read from `2_Funds_parser`'s DB (quarterly), not re-fetched.
- **Cadence:** annual for awards, quarterly for funds — so the whole thing advances a few hundred rows
  per year. Watermarked per source; fail-open.

---

## 3a. First-run historical backfill — last 10 years of awardees (builds the sub-theme DB)

The forward archive (D5) only captures going *forward*. To **seed the sub-theme database on the first
run**, we backfill each jury's **past ~10 years of winners/finalists** — this is what gives every
discovered sub-theme a `β_spec`/`p_main` *history* to gate nascency on. Backfill route depends on the
source (cheap, one pass, tiny):

- **(a) Own "past winners" archives (most awards):** the winners pages are public and multi-year — SPIE
  Prism (2008→), BNEF Pioneers (2010→26, one page, 176 names), Fierce 15 / Endpoints 11 (per-year
  microsites), Forbes/CB-Insights lists (per-year slugs), MIT TR-10, RBR50 (2021→). A per-source parser
  walks the **URL-pattern-per-year** (`/winners/2019`, year-slug, or per-year press release) → one fetch
  per year (~10 fetches/source).
- **(b) Wikidata / Wikipedia (structured prizes + some lists):** a single **SPARQL** query returns an
  award's winners with dates across all years (good for Nobel/Turing/denominators and any award with a
  Wikidata item) — one call, all history.
- **(c) Wayback Machine CDX (sources that overwrite each year, e.g. Best of Sensors):** query the CDX
  API (`web.archive.org/cdx/search/cdx?url=<winners_url>&from=2015&output=json`) to enumerate past
  annual snapshots, then fetch + parse each. Universal fallback for any URL with no clean archive.
- **(d) Machine-readable APIs with full history (build these first):** **YC** (yc-oss JSON, batches back
  to 2005), **ARPA-E** (data.gov full portfolio), **CNCF** (`landscape.yml` git history / acceptance
  dates), **Nobel** API, **CORDIS** (EU projects, historical), **Product Hunt** (historical
  leaderboards). One pull = the whole history.

Output: `jury_signals` rows tagged by `year`. The diffusion engine then computes each emergent
cluster's 10-year specialist + funding slope → the nascency gate ranks them. **Storage stays tiny**
(~10–15 sources × ~10 yrs × tens of entries ≈ low-thousands of rows). After backfill, the forward
archive takes over incrementally. `history_availability` in the registry flags which route applies
(`queryable` = API/Wikidata; `forward_only` awards = own-archive or Wayback).

## 4. Convergence → candidate theme (zero-Claude)

1. **Embed** each jury signal's short text locally (MiniLM, already in `embed.py`) — a few thousand
   tiny vectors.
2. **Link** signals that point at the same area by cosine similarity (≥ `tau_converge`), forming
   **convergence groups**. A group is a *candidate theme*.
3. **Score** each group by **jury convergence**, not volume:
   `score = Σ jury_credibility_weight × independence` — i.e. how many *independent, credible, leading*
   juries point here. A theme backed by MIT-TR10 **and** a DARPA BAA **and** specialist-fund new
   positions outranks one with a single mention. (Denominator juries add little to discovery score —
   they mostly move the horizon.)
4. **Promote** the top groups to `themes` rows (descriptor = the convergent signal texts). The existing
   diffusion engine then measures each promoted theme's `β_spec` / `p_main` curve on its (targeted)
   literature — exactly the current pipeline, just with discovered instead of hand-seeded themes.
5. **Constituents, classified LISTED vs PRIVATE** (so listed = investable, private = track) — see §4a.

---

## 4a. Two output tracks: INVESTABLE (listed) vs WATCHLIST (private)

Every company entity surfaced by a jury is **classified by listing status** so the two are never mixed:

- **Resolution:** each surfaced `org_name` is matched against the SEC ticker map
  (`company_tickers.json`, already loaded by `fundamentals.py`) — and, for foreign names, an ADR/exchange
  lookup. A confident match ⇒ **listed** (carry the ticker); no match ⇒ **private** (no ticker). Low-
  confidence matches are marked `unknown` and queued for a one-off manual/Claude resolution.
- **Track A — INVESTABLE (listed):** has a ticker → flows into the diffusion/panel/screener pipeline as a
  constituent you can act on now.
- **Track B — WATCHLIST (private/unlisted):** no ticker → recorded and **monitored for a listing event**.
  The screener watches SEC EDGAR for an **S-1 / F-1 / 424B / S-4 (SPAC)** naming that org (the
  `edgar_fts` client already does keyword/issuer search) → on first filing, the org **flips
  private → listed**, a `listing_watch` flag fires, and it enters Track A. The IPO itself is often the
  re-rating catalyst, so the flip is a signal, not just bookkeeping.
- This is exactly the silicon_photonics case: an unlisted photonics firm surfaced by SPIE/EIC sits in
  Track B with its theme + first-seen jury, and you get alerted the moment it files to list.

## 4b. Specialist-fund cross-reference (smart-money confirmation, D25)

A module that crosses **specialist-fund new 13F positions** against a theme's **listed** constituents,
the way `2_Funds_parser` does for biotech (its 21-fund `funds` table). The curated universe lives in
**`config/specialist_funds.yaml`**, organized per theme-sector.

- **Hybrid universe (research finding):** the dedicated-specialist 13F ecosystem is **thick only in
  biotech and software/growth**; in semis / cyber / fintech / energy / space / materials most
  specialists are **private VCs that file no 13F**. So each sector pairs (a) the **specialist/crossover
  13F funds** that exist with (b) a **thematic-ETF fallback** (read the ETF's holdings deltas) where
  they don't — e.g. semis → SMH/XSD, cyber → CIBR/BUG, fintech → Ribbit + FINX, batteries → LIT/BATT.
- **Signal:** reuse `2_Funds_parser/module_3` **`new_positions`** (and quarter-over-quarter holder
  count) per security. For each theme, intersect {specialist-fund new buys ∪ ETF additions} with the
  theme's Track-A tickers. **A specialist taking a NEW position in a theme constituent while the theme
  is still early = capital-jury confirmation** (a `ThematicHeat`/`NarrativeRealization` input later).
- **Biotech reuses 2_Funds directly** (D3 — read its `funds` + `holdings` DB); the other sectors use
  `specialist_funds.yaml` (CIKs to be EDGAR-verified at build). Crossover funds (Coatue/Tiger/Lone
  Pine) carry non-theme noise → weight them below pure specialists.
- **Caveat:** 13F is US-listed, quarterly, ~45-day-lagged, longs-only, and crossover funds' private
  books are invisible — so this is **confirmation on listed names, not discovery of private ones**
  (private discovery comes from the award juries → Track B).

## 5. Time horizon (your explicit requirement)

Every discovered theme carries an **estimated runway** — years from now until broad recognition /
re-rating — because expert recognition can lead the market by *years*:

- **Curve position** from the diffusion engine (`p_main` low = early) **+ which jury tiers have fired**:
  - only **leading** juries fired, `p_main` low → **early, long runway (≈ 2–5 yr)** — enter, hold long.
  - **bridge** fired (e.g. FDA BTD) → **mid (≈ 1–3 yr)**.
  - **denominator** fired (Nobel/Turing-class) / `p_main` high → **recognition arrived, runway ≈ 0** —
    likely too late; flag as such.
- The horizon is **calibrated from history**: measure, on past themes, the lag from first-leading-jury
  to mainstream inflection (`p_main` crossing) — a distribution, not a point. Each theme gets a horizon
  estimate + confidence band.
- **Consequence for the panel:** the forward-return / hold horizon must extend beyond the current
  13/26/52-week windows to **multi-year** for long-runway themes — Protocol §2.1's `H` becomes
  **theme-horizon-aware**, not a fixed 3–18 months. (Spec change to flag for the panel.)

---

## 5a. Weekly operation, caching, and the release calendar (D26)

**What a weekly run does — and what it skips.** Most juries publish **once a year**, so re-scraping all
of them weekly is wasteful and risks bans. The weekly run consults **`config/discovery_calendar.yaml`**
and fetches a source only when it is **DUE** = its publication window is open AND this period's edition
isn't yet captured (per-source watermark). So in a typical week the run is a **near no-op**: it polls
only the **continuous** feeds (YC, Product Hunt, CNCF, ARPA-E — incrementally, via watermark), plus any
**award currently in its publish window**, plus the **specialist-fund cross-ref only in the ~3 weeks
after a 13F deadline** (Feb/May/Aug/Nov). An annual jury outside its window costs **zero network** —
its `jury_signals` are served from the DB. Convergence/nascency are recomputed only if new signals
actually landed; otherwise the cached themes stand.

**Caching — yes, maximized; the mechanisms already built (and reused here):**
- **SWR on the diffusion engine** (`diffusion.yaml::cache_ttl_days=7`): `5_radar.py` skips a theme's
  refetch if its series was computed within the TTL; `--no-fetch` recomputes from cache with **no
  network** at all.
- **Non-destructive on failure:** a 429/500 keeps the prior cached value (GDELT/SBIR), so a flaky week
  never erases data.
- **Content-hash forward archive (OD-2):** snapshots store **only when the content hash changes**
  (award pages move ~annually) — a re-fetch of an unchanged page writes nothing.
- **Conditional-GET (ETag/If-Modified-Since):** the SEC fundamentals client already does this; the
  jury parsers will too — an unchanged page returns 304, no parse, no write.
- **Foundational caches never cleared:** the embedding store + ticker/CIK caches survive debug clears;
  embeddings are recomputed only when the model name changes.
- **One-time historical backfill (§3a):** the 10-year awardee history is fetched **once**; thereafter
  only new editions are added.

Net: the durable `data/hype.db` is the source of truth; the weekly run is mostly DB reads + a few
due-this-week fetches.

## 6. What is explicitly NOT in this design

- **No broad document corpus, no clustering of papers/patents** — patents dropped (noise); the
  multi-GB risk is gone.
- **No paid subscriptions** — all juries are public; funds reuse `2_Funds_parser`; embeddings local.
- **No Claude in the hot path** — convergence is embedding cosine; one optional cheap Claude call may
  *name* a promoted theme.

---

## 7. Schema (additive migration v8, proposed — tiny)

- `jury_signals (signal_id, source_id, diffusion_position, jury_credibility, year, item_text, entity,
   entity_type, url, embedding BLOB, ingested_at)` — the parsed expert recognitions.
- `theme_convergence (theme_id, signal_id, similarity)` — which signals back a discovered theme.
- `theme_orgs (org_id, theme_id, org_name, source_id, listing_status, ticker, cik, country,
   resolution_confidence, listing_watch, first_seen, last_seen, became_listed_at)` — the constituent
   roster, **classified `listing_status ∈ {listed, private, unknown}`**. `listed` rows carry `ticker`/
   `cik` and feed Track A (investable); `private` rows have `listing_watch=1` and feed Track B (monitor
   EDGAR for S-1/F-1/424B/S-4); `became_listed_at` stamps the private→listed flip (the listing event).
   `unknown` = low-confidence name match, queued for manual/Claude resolution.
- `themes`: add `horizon_years REAL`, `horizon_confidence TEXT`, `discovered_from TEXT` (vs hand-seeded).

A few thousand small rows total. No corpus tables.

---

## 8. Open parameters (⚙)

- **Jury set + weights:** which `edge_type: awards` rows are `leading` vs `denominator` (the registry
  already classifies; weights ⚙); which `2_Funds_parser` funds count as "specialist."
- `tau_converge`, min independent juries to promote, `top_k_themes`.
- Horizon calibration window + tiers (§5).
- Snapshot-parser robustness per source (taxonomies shift YoY — OD-2 already flags this).

---

## 9. Build order (when approved)

1. Parsers: archived jury snapshots → `jury_signals` (start with the **leading** set: MIT-TR10, R&D
   100, Fierce 15, RSA Sandbox, DARPA BAAs) + Nobel/Wikidata API for denominators.
2. `2_Funds_parser` specialist new-position reader.
3. Embed + convergence + score → promote candidate themes; build `theme_orgs` and **classify each org
   listed vs private** (SEC ticker-map resolution) → Track A (investable) / Track B (watchlist + EDGAR
   listing-watch).
4. Horizon estimator (calibrate on the existing hand-seeded themes as a back-test).
5. Feed promoted themes into the diffusion engine + panel; compare against the hand-seeded baseline.

Smallest first slice that proves the idea: **parse the ~5 leading juries + one specialist-fund feed,
embed, and show the convergence groups for the last few years** — a couple of MB, no Claude, no new
heavy infra.
