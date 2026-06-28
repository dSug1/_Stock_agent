# M2 explained — Stage 0: building the universe and the only deletions

*What milestone 2 actually does, in plain terms. Companion to `SPEC_acrivon_pattern_screener.md`
§5.1–§5.2 and §6.*

---

## In one sentence

M2 turns lists of companies into the screener's starting universe by **casting a deliberately wide
net (Stage 0a)** and then applies the **only two deletions the whole pipeline is ever allowed
(Stage 0b)** — market-cap out of band, and not-live — keeping and flagging everything else.

## The shape of Stage 0

```
  listing sources                 Stage 0a                       Stage 0b
  (seed CSV today,        union of 4 nets +              the ONLY deletions:
   market-data vendor     ADR/dual-listing      ─────►   • mktcap out of band
   later)          ─────► identity resolve               • not live
                          → companies rows               missing cap → KEEP + flag
```

Recall-safe upstream, precision-safe downstream: Stage 0 is intentionally over-inclusive, because
anything dropped here is dropped invisibly. Narrowing happens later, cheaply and reversibly.

## Stage 0a — assemble the universe (union of nets)

A company is admitted if **any** net catches it (a UNION, never an intersection). Four nets, all
configured in `config.yaml → stage0a_nets`:

| Net | Hits when… |
|---|---|
| `sector` | its SIC / GICS industry / ICB code is in the **broad** sector set (biotech **+** pharma **+** life-science tools **+** diagnostics **+** healthcare equipment). |
| `index` | it is a member of an in-scope health index (NBI, Nordic Health, Euronext Health). |
| `name_keyword` | its name contains a keyword (`therapeutics`, `bio`, `pharma`, `oncolog`, `genomic`, `proteomic`, …). |
| `seed_list` | it appears in the analyst's tracked-universe CSV. |

The broad sector set is deliberate: **the Acrivon-pattern company is frequently mis-coded** as
Tools/Equipment/Software, so a strict "Biotechnology" filter would drop exactly the targets we want.
Schrödinger is the worked example in the seed data — coded as software, no biotech keyword in its
name — and it is still admitted, via the `seed_list` net. Each company records *which* nets caught it
(`companies.source_nets`), so the provenance is visible in the report.

Admission is **not** a deletion: a record that matches no net simply isn't added (it was never a
company in our universe), and nothing existing is ever removed here.

### Identity resolution before admission (ADR / dual listings)

One real company can have several listings — a US listing plus a European ADR, say. The spec asks us
to "keep the primary, link the secondaries." We do this **before** company rows exist
(`dedup.collapse`), grouping records by ISIN (else normalized name), choosing one primary
(prefers an explicit primary flag → exchange priority → larger market cap), and **unioning** the
group's sector codes, index memberships, provenance, and liveness onto that primary. The other
listings are recorded in `secondary_listings`.

**Why before, not in Stage 0b:** the cardinal rule forbids deleting a company for being a duplicate
— `delete_company(reason="duplicate_listing")` would (correctly) raise. Resolving identity up front
means each real company becomes exactly one row, so Stage 0b never has to delete a duplicate, and no
sector code or seed membership carried only by a secondary listing is ever lost.

## Stage 0b — the hard cuts (the only deletions anywhere)

For every company in the store:

1. **Liveness.** If it's delisted / halted / an acquired shell → `delete_company(reason="not_live")`.
2. **Missing market cap.** If the cap is unknown → **keep it** and flag `mktcap_unknown`. Never
   dropped. (This is the cardinal rule's "missing data is kept and flagged" in action.)
3. **Market-cap band.** If the fully-diluted, FX-normalized USD cap is outside `[50M, 3B]`:
   - **within `near_band_tolerance` (15%) of a bound** → keep + route to `review_queue` (a human
     confirms borderline names like IDYA at 6.7% over the ceiling);
   - **beyond the tolerance** → `delete_company(reason="mktcap_out_of_band")`.

Both deletions go through the M1 guardrail, so this stage is *physically incapable* of deleting for
any other reason. The market-cap band and FX rates live in `config.yaml`; unknown currencies resolve
to an unknown cap (→ keep + flag), never a wrong number.

### Where market-cap and liveness data come from

`listings.py` ships two providers:
- **`SeedCSVProvider`** — offline, deterministic; reads `config/tracked_universe.csv`. This is the
  backbone (and what the tests run on).
- **`yfinance_enricher`** — *optional* per-company enrichment of cap/liveness over the network,
  enabled with `--enrich-yf`. It is **fail-open**: any import or fetch error returns nothing, so the
  company is kept and flagged `mktcap_unknown` rather than dropped. yfinance is fine for a **local**
  prototype but is ToS-limited for public deploy — swap for a licensed provider (FMP / EODHD) before
  hosting. (This is the one `[BUILDER DECISION]` the spec left open; the seam is provider-agnostic.)

## What you get out — the report

`scripts/6_render.py` writes a single self-contained HTML file, `Outputs/screener_report.html`: the
**funnel** (records in → admitted → kept, with deletion and flag counts), the retained companies with
their nets/flags/cap, **exactly what was deleted and why** (the cardinal-rule audit, with names), and
the review queue. A composite-scores section appears automatically once Stage 4 (M6) fills it in. The
report has no scripts and no external resources, and every value is HTML-escaped.

## Real caps vs. the locked band — the §13 alarm, and how it's resolved

The market caps in `config/tracked_universe.csv` are real and current. With the locked `[50M, 3B]`
band (§0.1), four of the spec's seven seed *positives* fall outside it — the §13 alarm (*a known
positive lost at Stage 0 is a spec-level issue*). The band is a **locked §0.1 decision**, so rather
than widen it we added a **near-band tolerance** (`market_cap.near_band_tolerance`, default **15%**):
a company within `tol` of a bound is **not deleted** — it is kept and routed to the `review_queue`
for a human call. Beyond the tolerance, the band deletes as written.

| Ticker | Real cap | Distance from bound | Verdict |
|---|---|---|---|
| ACRV / RXRX / SDGR | $71.5M / $1.69B / $1.25B | in band | kept |
| IDYA | $3.20B | 6.7% over ceiling | **kept → review_queue** (within 15%) |
| RLAY | $4.04B | 35% over | deleted `mktcap_out_of_band` |
| TNGX | $5.12B | 71% over | deleted `mktcap_out_of_band` |
| BOLD | $32.1M | 36% under floor | deleted `mktcap_out_of_band` |

So the real run is **7 admitted → 4 kept** (ACRV, IDYA, RXRX, SDGR; IDYA flagged `review`), 3 deleted.
This honors both clauses: the small-cap thesis stands (RLAY/TNGX/BOLD genuinely outgrew/undershot the
window), while a borderline name like IDYA isn't silently lost — it surfaces for review. The Acrivon
*pattern* these names illustrate remains the calibration lesson (Appendix B) regardless of today's cap.

## What's proven (tests)

Tests run on a **synthetic fixture** (`tests/fixtures/universe_fixture.csv`) with stable in-band caps
and edge cases — deliberately decoupled from live market caps (which drift daily) so the suite is
deterministic. `pytest tests/test_listings.py tests/test_stage0.py` — 17 tests on top of M1's 18
(**35 total**):
- union-of-nets matching (any single net admits; a mis-coded name still gets in via `seed_list`);
- dual-listing collapse (one company, primary chosen, secondary linked, fields unioned, live-if-any);
- the recall mechanism — with **in-band** caps, all fixture positives survive Stage 0 (the *real*-cap
  band tension is the separate, surfaced decision above — not coupled to this unit test);
- missing-cap company kept and flagged;
- every Stage-0b deletion carries an allowed reason (the cardinal rule, end-to-end);
- the enricher can fill an unknown cap (band then applies) or mark a company dead (→ `not_live`).

A live run over the fixture: **14 admitted → 11 kept** (2 `mktcap_out_of_band`, 1 `not_live`
deleted; 1 `mktcap_unknown` flagged-and-kept). The real tracked universe (`config/tracked_universe.csv`)
currently yields **7 admitted → 3 kept** (4 `mktcap_out_of_band`) — see the open decision above.

## How to run

```bat
:: from 6_Biotech_platform_discoverer\  — run the pipeline then open the report:
run_6_Biotech_platform_discoverer.bat
::   ...or with live market-cap enrichment:
run_6_Biotech_platform_discoverer.bat --enrich-yf

:: re-render the latest results to HTML without re-running:
render_6_report.bat
```
