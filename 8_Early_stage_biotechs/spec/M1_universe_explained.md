# Phase 1 — Universe module, explained (M1–M5)

*House-style milestone note: what was built, why, how it works, how to run/verify. Companion to
`phase1_universe_build_spec.md` (the target) and `decisions.md` (D1–D3). Covers milestones M1–M5 —
the whole Phase-1 universe layer.*

---

## What Phase 1 delivers
A deduplicated master list of biotech/pharma/life-sciences companies for **US + Canada**, merged with
Module 6's universe as a priority tier, keyed for the downstream literature/patent/trial/filing joins
that later phases add. It writes one SQLite store, `data/early_detection.db`, and spends **no money**
(no Claude in Phase 1).

Concretely: `providers → identity.reconcile → cap floor → store`, orchestrated by `universe.py` and
driven by `scripts/8_universe.py`.

## Why it's shaped this way (the three decisions)
- **SQLite, not Postgres (D1).** The spec asked for Postgres; the repo is SQLite everywhere and the
  spec's own volume estimate (~2–4k entities, low-hundreds of signals/day) fits SQLite comfortably.
  One `data/early_detection.db`, WAL mode, additive migrations on `PRAGMA user_version`.
- **Reuse Module 6, don't rebuild it (D2).** M6 already enumerates listed biotech across markets and
  scores it; it even surfaced Satellos. Module 8 reads M6's `store.db` **read-only** as the
  existing-universe priority tier (`in_existing_universe=1`) and never writes to it, keeping M6's
  cardinal-rule guarantee intact. But M6 is ~95% US, floored at $50M–3B, and — critically — has **no
  CIK** and no founder-lineage. So Module 8 keeps its own store and enriches beyond M6.
- **Own scoring rubric later (D3).** The spec's scoring layer referenced a companion
  "stack-convergence" spec that isn't in this repo. Deferred to Phase 2+; Phase 1 is pure universe.

## How it works

### The entity, and why CIK matters
M6 keys companies on `hash(name|listing)` and carries LEI/ISIN/ticker but **no CIK**. Every §3 signal
in later phases (EDGAR filings, patent assignments, trial sponsors) joins on CIK or LEI. So the
Module-8 `entity` table (schema v1) adds `cik` (zero-padded 10) and `lei` as first-class columns, plus
`in_existing_universe` (the priority-tier flag) and `sector_code_normalized` (a single taxonomy:
therapeutics / diagnostics / tools_platform / devices / agbio / other). Dual/cross-listings live in a
separate `listing` table rather than array columns.

### Providers (fail-soft, independent — spec §6)
- **`m6_seed`** opens M6's `store.db` with a `mode=ro` URI and emits one priority-tier `Listing` per
  live company. Country labels ("Japan", "United Kingdom") are normalized to ISO-ish codes so
  jurisdiction filters line up. A missing/locked M6 store logs and yields `[]` — Module 8 still builds.
- **`edgar_us`** enumerates every listed SEC filer under the biotech SIC set (2834/2836/8731/3826/3841)
  via `browse-edgar?...&output=atom`, then maps CIK→(ticker,exchange) through
  `company_tickers_exchange.json`. Adapted from M6's proven `sec_sic.py`, but **populates the CIK**
  M6 dropped and maps SIC→normalized sector. Common-ticker-preferred logic stops a warrant (`DNABW`)
  clobbering the common (`DNA`).
- **`edgar_canada`** runs the same SIC enumeration filtered to Canadian province location codes,
  catching TSX/TSXV names that also file with the SEC as foreign private issuers (40-F/20-F/6-K) —
  **without SEDAR+ scraping**, which is deferred (spec §2.5) and flagged as a known coverage gap.

### Identity resolution — union-find, not a single cascade key (the load-bearing bit)
Naive minting ("take each listing's strongest key") **fails** here: M6 gives a company its LEI+ticker
while EDGAR gives the *same* company its CIK+ticker. Picking the strongest key per-listing mints
`lei:…` for one and `cik:…` for the other → the same company becomes two entities, and the
priority-tier flag never reaches the EDGAR-discovered row. (The first live run showed exactly this:
614 + 76 listings collapsed only 4.)

So `identity.reconcile` runs **union-find over all shared hard keys** — LEI, ISIN, CIK, and a
`ticker+country` composite. Two listings sharing *any* key collapse into one entity, whose id is the
strongest key in the whole component (`lei:` > `isin:` > `cik:` > `tkc:`). The composite is
`ticker+country`, **not** ticker+exchange, because exchange labels disagree across sources ("Nasdaq"
vs "NASDAQ") while country is stable — that's the edge that actually stitches an M6 seed to its EDGAR
row. After the fix, a partial live run cross-linked 113 M6 companies to EDGAR CIKs (a full 5-SIC run
links far more).

Discipline (spec §2.3): a listing with **no** hard key is never merged on a name alone — it goes to
`reconciliation_queue` (with a weak-name-match hint if one exists) for manual review, because a false
merge is worse than a duplicate row. A component with conflicting LEIs is kept merged (a shared hard
key links it) but records a `conflicting_lei` review row.

### Cap floor — a flag, not a delete
The $10M floor (operator decision) is applied as a **flag**, never a deletion: a known cap below $10M
sets `below_floor=1` (schema v2) but the entity stays `is_live`; an **unknown** cap is kept + flagged
`mktcap_unknown` and is *not* treated as below-floor (missing data ≠ small). This mirrors the
repo-wide recall-safe posture (M6's cardinal rule).

### Market-cap enrich (M5) — making the floor actually bite
The universe build alone can't apply the floor to EDGAR-discovered names: `company_tickers.json`
carries **no market cap**, so those names enter `mktcap_unknown` and the floor has nothing to filter
(the first full build showed `below_floor: 0` with 561 unknown caps). The **enrich stage**
(`enrich.py` + `scripts/8_enrich.py`) closes that gap: for every live entity with a ticker but no USD
cap, it fetches the cap via **yfinance** (`clients/market.py`), converts to USD (`fx.py`, static
illustrative rates incl. CAD), and persists `market_cap_usd` + `below_floor` + `mktcap_ccy` + `ipo_date`.

Two disciplines matter here:
- **Persist each ticker immediately** (repo rule for long external-API loops) — a mid-batch crash must
  not discard fetched work. `store.apply_cap` commits per ticker; the pass is resumable (it only picks
  up entities that still lack a cap) and interruptible.
- **A miss is kept, not dropped** — a dead/dataless ticker stays `mktcap_unknown` (never below-floor,
  never deleted) with `enriched_at` stamped so it isn't retried every run.

`below_floor` is a **queryable column**, not just an audit row, so the *active universe* Phase-2 jobs
gate on is a simple predicate: `is_live=1 AND below_floor=0 AND mktcap_unknown=0`. yfinance is
**local-only** (ToS) — swap for a licensed provider before any public deploy. `--recompute-floor`
re-derives `below_floor` from stored caps after a floor-config change without re-fetching.

### GLEIF LEI backfill (M6) — the cross-market join key, done carefully
Later phases join literature/patent/trial signals across markets on the LEI (spec §2.3, LEI-first). But
LEI is also Module-8's **strongest identity key**, so a wrong LEI would silently merge two companies on
the next build. The GLEIF enrich (`clients/gleif.py` + `enrich.enrich_lei`, `scripts/8_enrich.py --lei`)
is therefore **high-precision, low-recall**: for each LEI-less entity it fulltext-searches GLEIF
(free, no key) filtered by country, then `pick_lei` accepts a result **only if exactly one candidate's
normalized legal name equals the entity's** (ISSUED-status tiebreak). Everything else is a miss (LEI
stays null). Two safety rails: `set_lei` never overwrites an existing LEI, and a picked LEI already
held by a *different* entity is **not set** — it queues a `gleif_lei_collision` review row (usually a
real store duplicate), so we never create two rows sharing one LEI. Work-list is ordered M6-tier +
international first (they benefit most). Live spot-check confirmed it matches `Genmab→GENMAB A/S` and
`Zealand→ZEALAND PHARMA A/S` while rejecting fuzzy fulltext noise for `Acumen`.

### Security (inherited, free)
`clients/_net.py` is copied from M6: 64 MiB capped reads, the repo `USER_AGENT` on every request,
`defusedxml`, per-host rate limiting, hard-coded public endpoints (no SSRF surface). SQL is
parameterized throughout; config is `yaml.safe_load` only.

## How to run / verify
```
cd 8_Early_stage_biotechs
# offline unit tests (no network) — 43 passing across store/identity/providers/universe/enrich
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\ -q

# full build (needs USER_AGENT w/ email in repo-root .env for SEC):
run_8_Early_stage_biotechs.bat
#   or bounded/partial:
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --no-ca --max-pages 5 --verbose
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --stats
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --dry-run   # no DB writes

# market-cap enrich (yfinance, local-only) — makes the $10M floor bite:
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py --limit 25    # smoke a batch
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py               # full pass (resumable)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py --recompute-floor

# LEI backfill (GLEIF, free) — high-precision cross-market join key:
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py --lei --limit 20   # smoke
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py --lei              # full pass
```
`--stats` prints the funnel: live entities, priority-tier count, unknown-cap count, reconciliation-queue
depth, and breakdowns by sector and country.

## What's explicitly deferred (so scope stays honest)
- All §3 signals, §5 Claude scoring/extraction, §7 digest — Phase 2+.
- `founder_scientists[]` / `academic_affiliations[]` columns — a later additive migration when the
  §5.1 extraction job runs.
- EU/Nordic/JP/KR universe — Phases 4–5 (they arrive via M6's seed today, but not yet enumerated
  natively by Module 8).
- SEDAR+/SEDI Canada scraping — only EDGAR-visible Canadian dual-listers are covered.
- ~~GLEIF LEI enrichment~~ **done (M6, high-precision).** Two-way export back to M6, FTS5 ad-hoc index — deferred.
- ~~Per-CIK market caps~~ **done (M5 enrich)** — yfinance fills unknown caps, `below_floor` computed.
  Full-dilution / pre-funded-warrant caps stay basic (shares×price), a deferred refinement from M6.
