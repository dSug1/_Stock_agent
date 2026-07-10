# 8_Early_stage_biotechs — Decisions log

*Newest first. The overall design lives in `disruptive-biotech-early-detection-spec.md`; this file
records where the build deviates from it and why. Phase-1 build detail is in
`phase1_universe_build_spec.md`.*

---

## D3 — Scoring framework: define the "stack-convergence" rubric fresh inside Module 8
**Spec ref:** §1, §5.4 ("feeds into … the existing `stack-convergence-biotech-screen-spec.md` scoring
framework"). **Decision:** that companion spec and the Satellos worked-example do **not** exist in this
repo (they came from a different session). Rather than block, Module 8 will **define its own**
stack-convergence rubric in `config/` when the scoring layer is built (Phase 2+), calibrated later
against known cases (Satellos/MSLE — which Module 6 already surfaced at rank 20 — plus others).
**Why:** unblocks universe + signals now; the scoring schema (§5.4) is the last thing built and the
easiest to slot in once real signal data exists. **Status:** deferred to Phase 2+, recorded here so the
dangling dependency is explicit and not silently assumed.

## D2 — Reuse Module 6's universe as a seed + priority tier; do not rebuild it, do not write into it
**Spec ref:** §2 (universe construction), §2.4 (integrate existing universe). **Decision:** Module 6
(`6_Biotech_platform_discoverer/data/store.db`, 614 live companies) is read **read-only** as the
"existing user universe" of §2.4. Its rows seed Module 8's entity table tagged
`in_existing_universe = true` (the §2.4 priority tier). Module 8 **never writes into M6's store** —
M6's cardinal-rule guarantee (delete only for `mktcap_out_of_band`/`not_live`) must stay intact.
**Why:** M6 already does multi-market listed-biotech enumeration + tiered Claude scoring and even
surfaced Satellos; rebuilding that wholesale is waste. **But M6 is not sufficient** — it is ~95% US
(584/614), floored at $50M–3B, and has **no CIK column and no founder-lineage fields**, which every
§3 signal join needs. So Module 8 keeps its **own** store and enriches beyond M6. **Status:** adopted;
M6-reader is a Phase-1 universe provider.

## D1 — Data store: SQLite (repo convention), not Postgres
**Spec ref:** §4 ("Postgres, not SQLite, given multi-market/multi-language volume"). **Decision:** use
**SQLite** (one `data/early_detection.db`, WAL mode, additive migrations tracked by `PRAGMA
user_version`), matching every other module in this repo (0/2/3/4/5/6/7). **Why:** the spec's own
volume estimate (§5.5: ~2–4k tracked entities, low-hundreds of signals/day after dedup) sits
comfortably inside SQLite's envelope; Postgres would add a server dependency and break the repo's
uniform "one gitignored `*.db` per module" convention for no measured benefit at this scale.
`tsvector`/OpenSearch full-text (§4) is likewise deferred — SQLite FTS5 covers ad-hoc query need if it
arises. **Revisit if:** concurrent scheduled writers genuinely contend, or the entity/signal volume
grows an order of magnitude beyond the §5.5 estimate. **Status:** adopted for Phase 1+.

---

### Standing constraints inherited from the repo (not Module-8-specific decisions)
- **Security** (repo-wide, per `SECURITY_AUDIT.md`): all fetched/scraped content is untrusted →
  SSRF allow-list + private-IP block on user/config URLs, 64 MiB capped reads, `defusedxml`,
  `html.escape` + http(s)-only hrefs in any report, secrets from `.env` never logged, parameterized
  SQL. Module 8 reuses M6's hardened `clients/_net.py` rather than reimplementing fetch.
- **yfinance is local-only** until a licensed provider is swapped in (repo-wide ToS constraint). Any
  Phase-1 market-cap read that uses it inherits this flag.
- **Claude cost discipline** (when Phase 2+ scoring lands): mandatory `[y/N]` cost gate before any
  dispatch, prompt caching of the static discipline prompt, Batch API for bulk, cheap tier for
  extraction / expensive tier only past a rules-based pre-filter (§5.5).
