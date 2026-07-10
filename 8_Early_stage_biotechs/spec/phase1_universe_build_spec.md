# Phase 1 build spec — Universe module (US + Canada + M6 merge)

*Concrete build target for Phase 1 of `disruptive-biotech-early-detection-spec.md` §8. Read that spec
for the why; read `decisions.md` for D1–D3. This file is the what/how for the first milestone set — it
should be enough to implement against without re-deriving choices. Nothing here spends money (no Claude
calls in Phase 1).*

**Scope (spec §8 Phase 1):** build and maintain a deduplicated master entity list of
biotech/pharma/life-sciences companies for **US + Canada**, merged with Module 6's universe as a
priority tier, keyed for downstream literature/patent/trial/filing joins. Signals (§3), scoring (§5),
and other markets (EU/Nordic/JP/KR) are **out of scope** for Phase 1 — but the schema is designed so
they slot in additively.

---

## 1. Package & layout (repo convention)

Directory begins with a digit, so the Python package is renamed (same trick as
`5_Hype_parser`→`hype_parser`, `6_…`→`platform_discoverer`):

```
8_Early_stage_biotechs/
  src/early_detection/
    __init__.py
    config.py              # loads config/config.yaml; paths; floors; feature flags
    models.py              # frozen dataclasses: Entity, Listing, Signal, ReconRow, AuditEntry
    store.py               # SQLite DAO + additive migrations (PRAGMA user_version)
    identity.py            # LEI→ISIN→ticker+exchange reconciliation + entity_id minting
    universe.py            # orchestrates providers → reconcile → upsert
    providers/
      __init__.py
      m6_seed.py           # reads 6_.../data/store.db READ-ONLY → priority-tier listings
      edgar_us.py          # SEC company_tickers.json + SIC filter (US)
      edgar_canada.py      # EDGAR FPI 40-F/20-F/6-K filter (dual-listed Canadian names)
      gleif.py             # GLEIF LEI lookup (cross-market join key) — optional-enrich
    clients/
      _net.py              # COPIED from platform_discoverer (SSRF allow-list + 64MiB cap)
      sec.py               # company_tickers.json, submissions, SIC — adapted from M6 clients
      gleif_api.py         # GLEIF /lei-records REST (free)
  config/
    config.yaml            # floors, SIC set, market toggles, paths
    sic_biotech.yaml       # SIC→normalized-sector map
  data/                    # early_detection.db (gitignored)
  scripts/
    8_universe.py          # CLI entry: build/refresh universe; --dry-run, --market, --stats
  spec/                    # this file + decisions.md + overall spec + Mx_*_explained.md
  tests/                   # offline unit tests (fixtures, no network)
  run_8_Early_stage_biotechs.bat
```

**Run convention:** `PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py …` from the
component dir. Shared root `.venv` and `.env` (`USER_AGENT` w/ email for SEC, `ANTHROPIC_API_KEY` for
later phases). Tests: `..\.venv\Scripts\python.exe -m pytest tests\ -q`.

---

## 2. Store schema v1 (SQLite, `data/early_detection.db`)

WAL mode; additive migrations keyed on `PRAGMA user_version` (mirror `platform_discoverer/store.py`).
Migration 1 creates:

### `entity` — canonical company (spec §2.3)
| col | type | note |
|---|---|---|
| `entity_id` | TEXT PK | internal stable id — **minted from the strongest available key** (see §4); *not* a random UUID, so re-runs are idempotent |
| `legal_name` | TEXT NOT NULL | |
| `common_name` | TEXT | |
| `ticker_primary` | TEXT | |
| `exchange_primary` | TEXT | |
| `isin` | TEXT | |
| `lei` | TEXT | GLEIF Legal Entity Identifier — cross-market join key |
| `cik` | TEXT | SEC CIK, zero-padded 10 — **the join key M6 lacks**; needed for §3 filings/patents |
| `jurisdiction` | TEXT | ISO country |
| `filer_type` | TEXT | domestic / FPI / other |
| `sector_code_raw` | TEXT | source-native (SIC / GICS) |
| `sector_code_normalized` | TEXT | one of {therapeutics, diagnostics, tools_platform, devices, agbio, other} |
| `market_cap_usd` | REAL | nullable; source-tagged in `raw` |
| `in_existing_universe` | INTEGER NOT NULL DEFAULT 0 | §2.4 priority tier — 1 if seeded from M6 |
| `is_live` | INTEGER NOT NULL DEFAULT 1 | |
| `first_seen` / `last_seen` | TEXT | ISO |
| `source_provenance` | TEXT | JSON array — which providers contributed |

`ticker_secondary[]`, `exchange_secondary[]`, `founder_scientists[]`, `academic_affiliations[]` from
§2.3 are **deferred**: secondary listings collapse into `listing` rows (below); founder/affiliation
fields are populated by the Claude extraction job in Phase 2 (§5.1), so they're added by a **later
additive migration**, not schema v1. Recording that here so v1 stays lean and honest about what Phase 1
actually fills.

### `listing` — one row per (entity, tradable listing)
Handles dual/cross-listings without array columns: `entity_id` FK, `ticker`, `exchange`, `country`,
`isin`, `is_primary`, `mic`, `provenance`. Reconciliation (§4) groups listings under one `entity_id`.

### `signal` — spec §3 shared table (created now, unused until Phase 2)
`signal_id` PK, `entity_id` FK (nullable — unmatched signals allowed), `signal_type`, `source`,
`raw_payload` (JSON), `detected_at`, `event_date`, `language`. Created in v1 so Phase-2 ingest is a
pure insert, no migration.

### `reconciliation_queue` — unmatched / ambiguous entities for manual review (spec §2.3)
`row_id` PK, `candidate_json`, `reason` (`no_key_match` / `ambiguous_multi_match` /
`conflicting_lei`), `added_at`, `resolved` (INTEGER DEFAULT 0). **Never force-merge** — a possible dup
that can't be resolved by a hard key goes here, per §2.3 ("false merges are worse than duplicate rows").

### `audit_log` — provenance spine
`ts`, `run_id`, `entity_id`, `stage`, `action` (`admitted` / `merged` / `flagged` / `queued` /
`refreshed`), `reason`, `detail_json`. Mirrors M6's audit table so the same observability tooling
pattern applies.

### `run_meta` — per-run funnel
`run_id`, `started`, `finished`, `market`, `counts_json` (admitted / queued / merged / by-source),
`config_hash`.

---

## 3. Universe providers

Each provider yields `Listing` records (pre-reconciliation). Providers are **fail-soft and
independent** (spec §6: a Canada scrape failure must not block the EDGAR pull) — each wrapped in
try/except that logs to `audit_log` and continues.

### 3.1 `m6_seed` — Module 6 store reader (priority tier, D2)
- Opens `../6_Biotech_platform_discoverer/data/store.db` **read-only** (`file:…?mode=ro` URI).
- Selects `is_live=1` companies → emits `Listing(name, ticker, exchange, country, isin, lei,
  mktcap_usd_fd, provenance=["m6"])`, each destined to set `in_existing_universe=1`.
- Path is config-driven and existence-checked; absent M6 store → log + skip (Module 8 still builds from
  EDGAR alone).

### 3.2 `edgar_us` — SEC company_tickers + SIC filter (US)
- Fetch `https://www.sec.gov/files/company_tickers.json` (CIK↔ticker↔name) via `clients.sec`
  (needs `USER_AGENT` w/ email or SEC 403s).
- Classify sector by **SIC** from each CIK's submissions metadata: keep
  **2834** (pharma prep), **2836** (biological products), **8731** (commercial research),
  **3826** (lab instruments), **3841** (medical devices — tools cos). Map SIC→`sector_code_normalized`
  via `config/sic_biotech.yaml`.
- Emits `Listing` with `cik` populated. This is the source that fills the CIK gap M6 has.

### 3.3 `edgar_canada` — dual-listed Canadian names (clean free path)
- EDGAR foreign-private-issuer filter: entities filing **40-F / 20-F / 6-K** with a Canadian business
  address. Catches TSX/TSXV names that are also SEC filers automatically, **without SEDAR+ scraping**
  (spec §2.5 defers the messy scrape path — flagged as a known Phase-1 coverage gap, not silently
  dropped).

### 3.4 `gleif` — LEI enrichment (optional)
- For entities missing an LEI, query GLEIF `/lei-records` (free, no key) by legal name + jurisdiction to
  backfill `lei`. Best-effort enrich, not a gate. Rate-limited + capped read.

---

## 4. Identity resolution & entity_id minting (spec §2.3, LEI-first)

`identity.py` groups incoming `Listing` records into entities by a **hard-key cascade**:
1. **LEI** (if present on both) — strongest cross-market key.
2. else **ISIN**.
3. else **CIK** (US/FPI).
4. else **ticker + exchange** composite.
5. else **normalized name + jurisdiction** (accent-folded, suffix-stripped — reuse M6's `dedup`
   normalization idiom) — but a name-only match is treated as **weak**: it goes to
   `reconciliation_queue` for review rather than auto-merging (§2.3 invariant).

`entity_id` is minted deterministically from the **first hard key that exists**, in the cascade order
above (e.g. `lei:5493...`, else `isin:US…`, else `cik:0001…`, else `tkx:ACRV|NASDAQ`). Deterministic
minting ⇒ re-runs are idempotent and the same company keeps the same id across refreshes. Conflicting
hard keys (two different LEIs claimed for one name, etc.) → `reconciliation_queue` with
`reason=conflicting_lei`, never a silent pick.

**Existing-universe merge (§2.4):** M6 seed listings run through the *same* reconciliation. A match
sets `in_existing_universe=1` on the merged entity; an M6 name that matches nothing new still enters as
its own entity with the flag set (M6 rows are trusted, not queued).

---

## 5. Cap floor & filters (Phase-1 policy, decision D-floor)

- **Market-cap floor: $10M USD** (operator decision). Below-floor names are **excluded from the active
  universe but recorded** with an audit row (`action=flagged, reason=below_cap_floor`) so the choice is
  visible and reversible — consistent with the repo's recall-safe, flag-don't-delete posture. No upper
  ceiling in Phase 1 (early-detection thesis is about the small end; M6 already owns $50M–3B scoring).
- Unknown market cap → **KEEP + flag** `mktcap_unknown` (missing data is never a silent drop).
- Sector filter is the SIC allow-list in §3.2; a non-matching SIC is excluded-with-audit, not deleted.

---

## 6. Milestones (Phase 1)

| M | Deliverable | Done-when |
|---|---|---|
| **M1** | `store.py` + `models.py` + migration 1 (all v1 tables) + DAO upsert/query + `test_store.py` | schema v1 creates clean; upsert/reconcile-queue round-trip tested offline |
| **M2** | `identity.py` reconciliation + entity_id minting + `test_identity.py` | LEI/ISIN/CIK/ticker cascade + name-weak→queue proven on fixtures |
| **M3** | providers `m6_seed` + `edgar_us` + `clients/{_net,sec}` (copied/adapted) + fixtures | US universe builds offline from a fixture `company_tickers.json`; M6 seed reads a fixture store; `in_existing_universe` set correctly |
| **M4** | `universe.py` orchestrator + `scripts/8_universe.py` CLI + `edgar_canada` + `run_…​.bat` + `run_meta` funnel + `M1_universe_explained.md` | `--dry-run` builds full US+CA universe end-to-end; `--stats` prints funnel; live smoke run populates `early_detection.db` |

Each milestone ships offline tests (network mocked with fixtures — repo convention: the whole suite
runs without network). GLEIF enrich (§3.4) is a stretch inside M4 or slips to Phase 2.

---

## 7. Explicitly deferred (so Phase 1 scope stays honest)
- All §3 signal ingestion, §5 Claude scoring/extraction, §7 digest — Phase 2+.
- `founder_scientists[]` / `academic_affiliations[]` columns — later additive migration when §5.1 runs.
- EU/Nordic/JP/KR universe — Phases 4–5.
- SEDAR+/SEDI Canada scraping (only EDGAR-visible Canadian dual-listers covered in Phase 1) — §2.5.
- Two-way export back to M6 (§2.4) — deferred; Phase 1 is one-way (M6→8) read only.
- FTS5 ad-hoc query index (§4/§7) — add if/when a query need appears.

---

## 8. Open questions to resolve before/while building
1. **GLEIF enrich in Phase 1 or defer?** Adds cross-market key quality but also a provider + rate-limit
   surface. Leaning: best-effort in M4, non-blocking.
2. **How aggressively to enumerate sub-$50M US names?** `company_tickers.json` + SIC gives the full US
   filer set; the $10M floor needs a market-cap source per CIK. yfinance (local-only) vs. deferring cap
   to a later enrich pass — Phase 1 can admit-with-`mktcap_unknown` and floor lazily.
3. **Confirm the M6 store path** stays `../6_Biotech_platform_discoverer/data/store.db` (config-driven,
   existence-checked — safe if it moves).
