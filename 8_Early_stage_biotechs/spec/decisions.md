# 8_Early_stage_biotechs — Decisions log

*Newest first. The overall design lives in `disruptive-biotech-early-detection-spec.md`; this file
records where the build deviates from it and why. Phase-1 build detail is in
`phase1_universe_build_spec.md`.*

---

## D30 — CIK backfill recovers no-CIK US names into the capital gate (data-completeness, not a market gap)
**Spec ref:** §3.5, cross-cutting. **Trigger:** during the full-universe sweep, 279/930 active names lacked
a capital-markets signal (the pre-filter's hard gate) → "digestible universe" capped at 651. Investigating
the 279 (`no-CIK 167 + has-CIK-but-no-material-filing 112`) showed the no-CIK bucket wasn't all foreign: ~87
were jurisdiction=US, and several (Assertio, KalVista, Avanos, Biodesix…) are **genuine SEC registrants we
were simply missing a CIK for** — they entered via the fund13f/Wikidata providers without one, and the CIK
never got backfilled. The capital signal gates on CIK, so they were invisible to it despite active SEC
filing. **Decision:** `scripts/8_cik_backfill.py` — for each active entity with no CIK but a ticker, resolve
ticker→CIK via EDGAR `browse-edgar getcompany` (reuses `fund13f._lookup_ticker`; free, no key) and set it
**only when no other stored entity already holds that CIK** (collision guard → never a false union-find
merge). Idempotent, fail-soft, `--dry-run`/`--us-only`/`--limit`. Then re-run `8_signals --capital-markets`
(+`--designations`/`--ownership`) to reach the new CIKs. **NOT a universe rebuild** — it patches the CIK on
existing rows so the downstream signals work; the union-find merge only matters on the next full build.
**Live (2026-07-14, --us-only):** 87 scanned → **43 resolved (all 43 gained a capital signal)** + 13
collisions (skipped — CIK already held = store dup) + 31 unresolved (genuinely foreign/non-filer).
**Digestible universe 651 → 694**; scoring pool 294 → 310 eligible (+16 newly-clearing). The residual ~236
without capital = truly-foreign (deferred native sources) + CA-FPI routine-only + quiet US shells — a real
limit, not a data gap. Backfilled names were **already extracted (step 2a)** — CIK is signal-only, no
re-extraction. **Status:** built + run 2026-07-14 (`scripts/8_cik_backfill.py`). Operational script (EDGAR
I/O), no unit test; collision guard + dry-run are the safety. [[project_8_early_stage_biotechs_app]]

## D29 — Crossref-ONLY mode: drop the 10-credit search backstop for cold micro-cap tranches
**Spec ref:** §3.1/§5.5, follow-up to D26. **Operator ask:** "build the cross-ref only." **Trigger:** the
first credit-safe OpenAlex pass on the 306 cold-first micro-cap founders (2026-07-14) resolved **38 authors,
34 via the free Crossref/ORCID fallback (89%)** — but burned **963 credits for only 101 founders (~9.5
each)** and stopped on the reserve with 205 left. Decomposed: the ~34 free resolutions were cheap (~1–3
credits), the drain was the **~63 non-academic founders each paying the 10-credit backstop search** (~630
credits) before failing — CEOs/VCs with no publication trail that the OpenAlex author-search can't resolve
either. The backstop's marginal recall was tiny: only **4** founders resolved via the primary search vs 34
via Crossref (~4% of the pool) at 630 credits. **Decision:** `config.author_search_backstop` (default
**True** = keep the backstop, safe) — when False (or `8_openalex_daily.py --crossref-only`), crossref-first
skips the 10-credit OpenAlex author search when the free resolver misses and stamps no-match directly. A
Crossref miss on a non-academic founder then makes **zero** OpenAlex calls (Crossref finds no works → the
resolver's DOI→work map never fires), so non-academic founders cost ~0 credits instead of 10 → **~3–5× more
founders per ~1000-credit window**. **Recall tradeoff (honest):** loses founders Crossref misses but the
OpenAlex search would catch (~4% empirically); acceptable for cold micro-cap tranches where non-academic
founders dominate, hence default-off. **Fail-open kept:** a transient Crossref/OpenAlex-map failure still
returns `retry` (unstamped, next-window retry) — only a genuine miss stamps no-match. Logic in
`literature._resolve` (a `backstop` gate on the crossref-first branch); `--crossref-only` overrides the
frozen cfg via `dataclasses.replace`. **Status:** built 2026-07-14 — `config.author_search_backstop` +
`_resolve` gate + `8_openalex_daily.py --crossref-only` + `test_author_resolution.py` (3 crossref-only
cases: miss→no-match-no-search, free-path resolves, transient→retry). **181 tests.** Zero spend.
[[reference_openalex_credit_quota]]

## D26 — Promote the Crossref/ORCID resolver to PRIMARY (crossref-first); the 10-credit search is a backstop
**Spec ref:** §3.1/§5.5, completes the D25 "make the fallback primary" deferral. **Operator ask:** "promote
the fallback to primary for credit savings." **Evidence it's safe:** the 10-ticker batch (2026-07-13)
resolved 10/10 real scientific founders (Walter Gilbert, LeRoy Hood, Jeffrey Ravetch, Colin Masters…) and
rejected 13/13 non-scientist founders (CEOs/VCs/inventors) — precision held, so the free resolver can lead.
**Decision:** `config.author_crossref_first` (default **True**) inverts the order — try the free
Crossref(+ORCID)→OpenAlex-author-id resolver FIRST, and pay the 10-credit OpenAlex author search only as a
recall **backstop** when the free path can't resolve. **Why it saves:** a Crossref miss on a non-academic
founder makes ZERO OpenAlex calls; an academic founder resolves for ~1 credit (DOI→work map; author-profile
verification is free). **Live-measured: resolving Tibor Keler cost 1 credit vs ~10** (a 10× saving); the
free path also resolved Stuart Rich / Stephen Elledge at ~1–2 credits each.

**Recall preserved (no loss vs search-first):** when the free path misses, the backstop runs the full
10-credit search + `pick_author` exactly as before, so any founder the search would have resolved still is.
The only cost case is a founder Crossref surfaces but can't institution-verify AND the search then also
misses — a few wasted credits, rare. **Fail-open contract kept:** a transient Crossref failure falls through
to the backstop; if the backstop ALSO can't resolve while Crossref was transiently down, the founder is left
UNSTAMPED for retry (never a false no-match); OpenAlex budget-exhaustion short-circuits both paths → retry.
Set `author_crossref_first: false` in `config.yaml` to revert to search-first (free resolver as a
post-search fallback). **Implementation:** the resolution order lives in a `literature._resolve` helper
returning `resolved|no_match|retry`; both orders share the pubs+citation emission. **Status:** built
2026-07-13 — `config.author_crossref_first` + `literature._resolve` rewrite + `test_author_resolution.py`
crossref-first cases (search skipped when free path resolves; backstop fires on a miss) + legacy literature
tests pinned offline. **178 tests.** `spec/D25_author_resolution_fallback_explained.md` updated.
[[reference_openalex_credit_quota]]

## D25 — Free author-resolution fallback (Crossref + optional ORCID) relieves the 10-credit chokepoint
**Spec ref:** §3.1 (literature signal; author resolution is its bottleneck), follow-up to D24. **Operator
ask:** "build free ORCID/Crossref taking into consideration cybersecurity." **Why:** author resolution is
the bottleneck twice over — OpenAlex `/authors?search=` costs **10 credits** (D24: ~100/day free) AND
resolves only ~40% of founders. **Decision:** when the primary OpenAlex author search fails to resolve a
founder (`pick_author` → None), fall back to a FREE resolver that yields an OpenAlex author id without a
second 10-credit search:
1. **Crossref** `query.author` (free, keyless, **no quota**) → the founder's works in **author-relevance**
   order (NOT a citation sort — `query.author` is a loose token search, and a global citation sort surfaces
   mega-cited consortium papers merely CONTAINING a name token, e.g. "Stuart Pocock" for "Stuart Rich",
   burying the real author). Keep only works whose author list truly name-matches (`openalex._name_match`);
   sort THAT subset by citations locally.
2. **(optional) ORCID** — if `ORCID_CLIENT_ID`/`_SECRET` are in the ENV, keep only DOIs the founder
   authored per their ORCID record (persistent-identity precision). Absent creds → Crossref-only, fully
   functional.
3. For each candidate DOI (most-cited first, capped 3): map **DOI → OpenAlex work** (`filter=doi:`, **~1
   credit**), find the name-matching author, verify the institution hint against that author's **whole-
   career** institutions via the OpenAlex **author profile** (`/authors/{id}` = **FREE, 0 credits**) — not
   the one paper's affiliation (a foundational paper predates the founder's current institution). First
   verified match wins. The existing cheap OpenAlex path (pubs + `cites:`) then continues on that author id.

**Net cost ≈ 1–2 OpenAlex credits per recovered founder vs 10**, and it resolves founders the search
missed. **Precision-first** (wrong author → wrong citations → false signal): surname always required, +
career-institution match when a hint is known, else only an unambiguous unique name; ambiguous → no
resolution. **Fail-open/lossless:** any transient fetch failure (Crossref down / OpenAlex credit-exhausted
/ ORCID token fail) → `fetch_failed` → founder left UNSTAMPED for retry, never a false no-match.

**Cybersecurity (3 new fetch surfaces — Crossref/ORCID/OpenAlex-DOI, built to `SECURITY_AUDIT.md`):**
(a) **No SSRF** — hosts are hard-coded HTTPS constants; only query VALUES vary, percent-encoded; no
user/config URL fetched. (b) **DOI path-injection guard** — `openalex.valid_doi` regex-validates a
third-party DOI to `10.<4-9>/…`, **rejects `..`** + non-DOI junk (`javascript:`/`http://`), then it's
encoded into a query VALUE not a path (validation + encoding, defence-in-depth). (c) **ORCID secrets from
ENV only**, never yaml/CLI, secret+body+token **never logged** (error logs carry only the exception type),
memory-only. (d) **JSON-only** (no XML → no entity-expansion). (e) 64 MiB capped reads + bounded rows.
(f) Response content is DATA (matched+stored; never executed/SQL/unescaped). (g) `mailto` regex-validated
before use. (h) per-source limiters + Retry-After + fail-open.

**Live-verified:** Stuart Rich → `A5011075689`, Stephen Elledge → `A5025914907` (whole-career institution
match), a nonexistent name correctly unresolved; ~1–2 credits/founder. **Status:** built 2026-07-13 —
`clients/crossref.py` + `clients/orcid.py` (opt-in) + `signals/author_resolution.py` + `openalex.work_by_doi`
/`authors_of_work`/`author_by_id`/`valid_doi` + literature wiring + `config.author_fallback_enabled`/
`_max_candidates` + `test_author_resolution.py` (18). **176 tests**, all offline. See
`spec/D25_author_resolution_fallback_explained.md`. [[reference_openalex_credit_quota]]. **Deferred:**
OpenCitations for a zero-OpenAlex-credit citation path; making the fallback PRIMARY (try Crossref before
the 10-credit search) once precision is trusted at scale.

## D24 — OpenAlex enforces a credit/USD quota now; a credit-budget guard + credit-safe daily runner
**Spec ref:** §3.1/§5.2 (the OpenAlex-backed literature + independence signals), operator asks
(2026-07-13): "check if OpenAlex is still 429; run a daily pass that stays within the credit budget and
doesn't lose results on a 429; run it for the existing pipeline." **Finding:** the 2026-07-11 IP lockout
has cleared (probed live: `200 OK`), but OpenAlex now enforces a **credit/USD quota ON TOP of the 5/s
rate limit** — every response carries `X-RateLimit-Limit` (~**1000 credits**/window), `-Remaining`,
`-Reset` (~**21.6h ≈ daily**), `-Limit-USD` (~**$0.10**), `-Remaining-USD`, and `-Prepaid-Remaining-USD`/
`-Onetime-Remaining` (a **prepay** path). **Cost is PER-ENDPOINT, not per-call:** `/authors?search=` =
**10 credits**, a `/works` list/`cites:` page = **1 credit** — so the free tier is only ~**100 author
searches/day**, and author RESOLUTION (the digest bottleneck) is also the budget hog. The handoff's prior
framing (OpenAlex = purely a 5/s rate limit) is now stale.

**Decision — read the budget and stop before exhausting it; never hardcode the prices.** Built:
1. **`_net` header observation** — `get_json_retry`/`safe_json_retry` take an optional `on_headers`
   callback, invoked with the response headers on BOTH success and a retryable HTTPError (so a 429's
   `remaining=0` is observed too). Defensive (`getattr(resp,"headers",None)`, swallows callback errors) —
   never breaks the fetch; no change to the JSON return contract.
2. **`openalex.CreditTracker` + module-shared `CREDITS`** — `note(headers)` updates remaining/limit/reset/
   USD; `exhausted(reserve)` is False while unknown (first call always allowed → it populates the tracker)
   and True once observed remaining ≤ reserve. `_get` feeds every response into it and short-circuits to
   None (→ callers treat it as a throttle → founder left UNSTAMPED for retry) once the budget is truly
   gone. **We read the server's own remaining count after each call, so per-endpoint prices self-correct
   — nothing is hardcoded.** `probe_credits()` = one cheap 1-credit `/works` call to populate the budget
   before a run.
3. **Per-founder budget gate** — `config.openalex_credit_reserve` (default **40**, sized to comfortably
   finish a founder in flight: author search ~10 + a few 1-credit pages). The literature + independence
   loops check it at the TOP of each founder iteration (never mid-founder → a half-processed founder is
   never stamped/poisoned) and STOP, leaving the rest unstamped; results carry `stopped_early`/`budget_left`.
4. **`scripts/8_openalex_daily.py` (+ `run_8_openalex_daily.bat`)** — probe budget → literature then
   independence IN ONE PROCESS (shared `CREDITS` → the budget carries across both passes) → report
   remaining credits + reset + backlog counts. Bails early (no expensive sweep) if already at the reserve.
   This is now the canonical way to run OpenAlex; the individual `8_signals --literature/--independence`
   commands also print the budget.

**"Don't lose results on interruption" is satisfied by:** per-founder persistence (unchanged) + the
anti-poisoning None-on-throttle contract (D14/M16) + the between-founders-only gate (never stamps a
partial founder) → a re-run next window resumes losslessly. **Free author-resolution fallbacks** that
sidestep the 10-credit search (recorded for the deferred build): **ORCID Public API** (free; needs free
OAuth public-API creds, NOT paid membership) + **Crossref REST** (free, NO key, `mailto` polite pool, no
credit quota). **Live drain (2026-07-13):** literature 36/36 founders (4 authors resolved, 759 independent
citations) + independence 46/46 (5821 independent post-refinement); 987→454 credits, well within budget;
backlog 0/0. **NEXT (paid, operator-gated):** `8_score.py --force` to fold the new citations into
conviction. **Status:** built 2026-07-13 — `_net.on_headers` + `openalex.CreditTracker`/`probe_credits` +
`config.openalex_credit_reserve` + literature/independence gates + `8_openalex_daily.py` + bat +
`test_openalex_credits.py` (8). **158 tests**, all offline. Zero LLM spend (OpenAlex credits only).
[[reference_openalex_credit_quota]]

## D23 — 13F-holdings seed provider (from 2_Funds_parser) closes EDGAR-enumeration gaps; render consolidated
**Spec ref:** §2.2/§2.4. **Trigger:** a coverage audit of the 21 specialist biotech funds tracked in
`2_Funds_parser` (Baker Bros/OrbiMed/RA Capital/Perceptive/RTW/…) vs the module-8 universe found **79%
covered** (55% active + 24% correctly-excluded >$3B large-caps), but **~10 genuine small/mid US
therapeutics missing** despite being held by top specialists — Apellis, Celcuity, Terns, Day One, Arcellx,
Soleno, Definium, Sensei, Amicus, Centessa(UK). Root cause: the EDGAR SIC enumeration is **page-capped**
(`--max-pages 30`), so not every filer under a biotech SIC is reached. A name a specialist fund holds is
thesis-relevant by construction. **Decision:** add `providers/fund13f.py` — mirrors the M6 seed (D2): opens
`2_Funds_parser/2_fundparser.db` **READ-ONLY**, emits a `Listing` per specialist-fund-held name.
**Default-ON** (`use_fund13f=True`, `--no-fund13f` to skip), like M6. Two disciplines: (1) **exclude broad
filers** — any filer holding > `max_holdings_per_filer` (500) distinct names in the latest quarter is a
diversified manager, not a specialist (auto-drops the mis-scoped "Janus Henderson Group PLC" whole-company
13F with ~2,300 mostly-non-biotech positions — a `2_Funds_parser` data issue, not ours). (2) **resolve + SIC filter via
EDGAR** — each held ticker → `browse-edgar getcompany` (one call → CIK+SIC+name; covers ALL filers, unlike
SEC's incomplete `company_tickers.json` which is missing Apellis/Terns/Day One — itself a root cause of the
gaps + a latent bug in the EDGAR provider that uses the same file). Admit only biotech-adjacent SICs
(therapeutics 2833/34/36, diagnostics/labs **2835/8071**, tools 8731/3826/3827, devices 3841/3845 — broader
than the enumeration set on purpose; SEC classifies real biotechs like Celcuity under 8071). SIC (not a name
keyword) catches keyword-less biotechs (Celcuity, Arcellx) and cleanly excludes utility/insurer/ADR positions.
**First cut caught a real bug** (resolved via the incomplete SEC file + narrow SIC → added 0 gap entities);
fixed after verifying against the actual gap tickers. **Live:** 442 held → 349 resolved → 291 admitted →
**+33 net-new specialist-held biotechs** (Apellis/Terns/Day One/Celcuity/Arcellx/Amicus/Soleno/Centessa…),
mktcap_unknown pending enrich.

**The SEC-ticker-file incompleteness is ALSO fixed in the EDGAR provider itself.** `sec.build_us_listings`
dropped any SIC-enumerated CIK absent from `company_tickers_exchange.json` (only ~9.3k entries, verified
missing Apellis CIK 1492422) — so those companies never entered the universe even under a covered SIC.
Fixed: `resolve_missing=True` (default) falls back to the **submissions feed** (`sec.resolve_via_
submissions`, authoritative per-company `tickers`/`exchanges`) for any enumerated CIK not in the ticker
map. Test `test_build_us_listings_submissions_fallback_recovers_missing`. This closes the gap for ALL
biotech filers, not just specialist-held ones. **2_Funds_parser side:** its `sec_ticker_resolver` (a
LAST-RESORT name→ticker fallback; OpenFIGI/CUSIP is the comprehensive primary) uses the same incomplete
file — docstring corrected to state it's genuinely incomplete (misses Apellis/Terns/Day One), not a
delisting proof; primary path unaffected. (The Janus-Group-PLC broad-filer entry in 2_Funds's seed —
whole-company 13F, not the biotech fund — was removed by the operator; module 8's broad-filer exclusion
also guards it.) **Also (render):** `run_8_render.bat` fixed + **wired into `run_8_Early_stage_biotechs.bat`**
(the main bat now renders + opens the status/digest after the build). CIK-keyed → union-find merges with EDGAR/M6 twins; genuine gaps
become new entities (mktcap_unknown until enriched). Fail-soft (missing DB / unresolved ticker / SIC failure
→ skip). **Also (render consolidation):** the single `Outputs/pipeline_status.html` (`scripts/8_status.py`)
now merges the status dashboard + the full interactive digest; `run_8_render.bat` runs 8_status, calls the
venv python directly (robust; `activate.bat` dependency removed) and **auto-opens** the HTML (the "not
working" report = it no longer opened). `render.py`/`8_render.py`/`test_render.py`/`digest.html`/
`digest_data.js` deleted. **Status:** built 2026-07-12 — `providers/fund13f.py` + `config.fund_store_path`
+ universe wiring + `--no-fund13f` + `test_fund13f.py` (4). Note: `2_Funds_parser` should retarget the
Janus CIK to its biotech sub-fund (its own data fix). Tests green.

## D22 — Clinical stage is ASYMMETRY, not a gate: phase-agnostic widening + asymmetry rubric (prompt v2)
**Spec ref:** §3.2/§5.4. **Operator insight:** a Phase-2 pre-filter floor *eliminates* early-stage names —
but early clinical stage = higher risk AND higher asymmetry, and is the *target* of early detection, not a
weakness. The data confirmed it: a Phase-2 floor would drop **29 early-stage-only names** (Phase 1 /
first-in-human, no Phase 2 yet) — the exact high-asymmetry bets the pipeline exists to surface. **Decision:**
(1) **Phase-agnostic widening** — `prefilter_clinical_min_phase` default changed **0 → 1**, where 1 = "ANY
clinical stage" (`min_rank=1`, EARLY_PHASE1 and up), so any name with a MEANINGFUL (active/completed)
company-led trial + a capital signal reaches the Claude call regardless of phase; 2..4 raise the floor if an
operator ever wants it. Phase is a FLOOR you can raise, no longer a wall that drops early names. (2) **Rubric
enrichment (prompt v1 → v2)** — dimension 4 reframed as "Mechanism novelty & CLINICAL-STAGE ASYMMETRY": the
scorer is told early stage is higher-risk/higher-asymmetry and the *target*, NOT to penalize a name for being
early/small, that a first-in-class Phase-1 with independent validation is the ideal asymmetric bet, and to
REWARD the novel-mechanism × early-stage mismatch; dimension 5 reserves "deprioritize" for a WEAK/UNVALIDATED
mechanism, NEVER merely for being early-stage. Clinical stage/status already flows into the packet
(`evidence_summary.clinical_trials`) — this makes the model USE it for asymmetry. Clinical runs BEFORE the
Claude call (free signal), enriching it. **Live effect (no spend):** candidate pool 27 (citation-only) →
**263** (any clinical stage), of which **29 are early-stage (Phase-1 lead)** kept in (Inovio/Palisade/ProMIS/
Sutro/Caribou/Immutep…). **v2 note:** the 12 v1 scores are preserved but under the old rubric; the v2 digest
is empty until a re-score under the asymmetry rubric (the paid step — ~263 candidates ≈ $2–7 Sonnet batch;
may need `max_usd_per_run` raised or `--limit`). **Status:** built 2026-07-12 — config + `store.scoring_
candidates` min_rank map + scoring SYSTEM_PROMPT + `test_clinical.py` (early-stage inclusion). 147 tests. Zero spend.

## D21 — Foreign cap enrichment via ISIN → OpenFIGI → yfinance; the ceiling now works for all markets
**Spec ref:** cross-cutting (M18/M19/D20 follow-up). **Problem:** the Wikidata-seeded foreign names
entered `mktcap_unknown` (Wikidata has no cap), so the $3B ceiling couldn't drop their mega-caps — the one
thing gating the foreign markets from scoring (why the providers were opt-in). The US cap path keys on
`ticker_primary`+bare-ticker; foreign names have an **ISIN** and yfinance needs an **exchange-suffixed**
symbol. **Decision:** `clients/openfigi.py` (free, no key) maps ISIN → FIGI records; the **first record is
the primary venue** (verified Zealand→ZEAL/DC, Argenx→ARGX/BB, Abivax→ABVX/FP), whose Bloomberg exch code
→ yfinance suffix (`DC→.CO`, `BB→.BR`, `FP→.PA`, `SS→.ST`, `GY→.DE`…); pan-EU MTF/composite codes are
skipped. `enrich.enrich_caps_isin` (+ `store.entities_needing_cap_isin`, `8_enrich.py --isin`): ISIN →
symbol → yfinance cap → USD → `apply_cap` (floor+ceiling flags), per-entity persist, fail-open (no
symbol/cap → stamped, kept mktcap_unknown, not retried). Extended `_net.get_json_retry` to support a POST
`data` body (OpenFIGI is POST). OpenFIGI keyless ≈25/min → paced 0.4/s + retry. **Live-verified before
wiring:** Zealand $2.83B (active), Camurus $3.16B (ceiling-excluded), Vicore $0.34B / Synact $0.12B
(micro active), Argenx $51B / Abivax $11.6B (excluded) — the ceiling now separates foreign small/mid-caps
from mega-caps exactly as for US. **Limits:** static FX [INF] (live rates come with the licensed provider
that also replaces local-only yfinance); unresolved names stay uncapped (recall-safe, pre-filter backstop);
ticker-only-no-ISIN foreign names uncovered. **Status:** built 2026-07-12 — `clients/openfigi.py` +
`enrich_caps_isin` + store work-list + `--isin` CLI + `test_enrich_isin.py` (5). 146 tests. Zero spend.

## D20 — Canada (TSX) Wikidata provider — thin, and it empirically confirms the SEDAR+ gap
**Spec ref:** §2.2. **Decision:** add a Wikidata Canada provider (`providers/canada.py`, `--ca-wikidata`,
reusing the shared builder) as the TSX/TSXV complement to `edgar_canada` (FPI path). **Probed first:**
Wikidata CA returned ~8 listed names, **most already covered** (Zymeworks/Arbutus/AbCellera/Bausch are
NYSE/Nasdaq cross-listed → already in via EDGAR/D16). Live build added **6 entities** — the genuinely-new
TSX-only value is minimal. This **empirically confirms** the plan's finding: the TSX-only universe is
poorly served by keyless sources; real coverage needs a TMX/SEDAR+ listing scrape (no clean API, bot-
protected — deferred). Also a minor known dedup gap: cross-listed CA names came in as new entities rather
than merging with their EDGAR twins, because our EDGAR entities key on CIK+ticker while Wikidata keys on
ISIN (no shared key → union-find can't merge); an ISIN backfill on EDGAR entities would close it. Opt-in +
mktcap_unknown, same as M18/M19. **Status:** built 2026-07-12 (`providers/canada.py` + `--ca-wikidata` +
`test_nordic.py` canada case). 141 tests. Completes the operator's international market list; Korea/Japan
remain free-API-key-gated (DART/EDINET); TSX-only + EU per-country capital + EMA designations deferred.

## D19 — Europe-broad universe via Wikidata; per-market logic factored into a shared builder
**Spec ref:** §2.2, `international_expansion_plan.md`. **Decision:** extend the M18 Wikidata approach to
non-Nordic Western Europe (DE/FR/UK/CH/NL/BE/IT/ES/IE/AT). Since it's the same query over a different
country set, the logic was factored into `providers/_wikidata_universe.py` (`load_biotech_listings(
country_qids, provenance)` — ISIN/ticker admission rail, LEI-not-listed guard, dedup, fail-soft);
`nordic.py`/`europe.py` are thin wrappers. **Probed first:** Wikidata gave **89 EU listed biotechs with a
security id** (vs Korea's 0 — KR needs DART's key); ESMA FIRDS returned HTML and EMA designation files
404'd, so those stay deferred. Opt-in (`--europe`, default OFF) for the same reason as Nordic (no cap →
mktcap_unknown → mega-caps can't be ceiling-dropped until a European cap-enrich exists). **Live:** 63 EU
entities, 0 queued; inherited CT.gov clinical verified (Argenx 16 company-led trials, Abivax 20,
Immunocore 15, MorphoSys 11) — the provider-lights-up-signals thesis holds a second time. Korea was
attempted first but **blocked**: its universe + capital both route through DART, which needs a free API
key (no keyless path; Wikidata KR has 0 security ids) — operator opted to skip to Europe. **Status:** built
2026-07-12 — `providers/_wikidata_universe.py` + `europe.py` + refactored `nordic.py` + `8_universe.py
--europe` + `test_nordic.py` (4, now covers both). 140 tests, all offline. Zero spend.

## D18 — First non-US market (Nordic) via Wikidata SPARQL; a universe provider lights up inherited signals
**Spec ref:** §2.2 (non-US enumeration), `international_expansion_plan.md`. **Decision:** the pipeline is
source-pluggable, so a new market is primarily an ENUMERATOR — once a name is an entity, the market-
agnostic signals (CT.gov clinical, FDA-designations-via-6-K + FPI capital for cross-listed, OpenAlex
literature, GLEIF LEI) cover it for free. Nordic source chosen by PROBING (CT.gov-style discipline):
ESMA FIRDS + EMA returned HTML/antibot and the Nasdaq-Nordic feed timed out — **only Wikidata SPARQL
returned clean JSON**. So `clients/wikidata.py` (SPARQL, hard-coded host, safe_json_retry, capped) +
`providers/nordic.py` enumerate SE/DK/NO/FI/IS biotech+pharma → `Listing` → identity flow. **Precision
rail:** admit only on a SECURITY id (ISIN or ticker), NOT LEI alone — every legal entity (incl. PRIVATE
cos like LEO Pharma/Fertin) can hold an LEI, but this is a LISTED universe; that fix tightened 29→15
genuinely-listed names. **Opt-in (`--nordic`), default OFF:** Wikidata gives no market cap, so names enter
`mktcap_unknown` (kept, recall-safe) and the $3B ceiling can't yet drop the Nordic mega-caps — so the
provider must not pollute the default US/CA build until a Nordic cap-enrich exists. **Honest limits (§9):**
Wikidata skews large/known → a SEED, not complete micro-cap coverage (Spotlight/NGM scrape deferred);
cap-enrich (licensed provider) is the gating follow-up. **Live:** 15 Nordic entities, 0 queued; inherited
CT.gov clinical verified on them (Zealand 18 company-led trials, Bavarian Nordic 15, Camurus 9) — the
provider-lights-up-signals thesis proven. **Status:** built 2026-07-12 — `clients/wikidata.py` +
`providers/nordic.py` + `universe`/`8_universe.py --nordic` (opt-in) + `test_nordic.py` (4) + M18
explainer. 140 tests, all offline. Zero spend (no key, no LLM). `clients/wikidata.py` reusable for KR/JP/EU seeds.

## D17 — §3.4 regulatory-designation signal is phrase-first EDGAR full-text (FDA doesn't publish designations)
**Spec ref:** §3.4. **Decision:** FDA doesn't publish Breakthrough/Fast-Track/Orphan/RMAT/Rare-Pediatric
designations as structured data (only scattered PRs) — but a company MUST disclose a material designation
in an 8-K (FPI: 6-K). So the signal is **phrase-first over EDGAR full-text**, the exact shape of the
ownership signal, reusing the verified `edgar_fts` client: for each designation phrase → filings
containing it → match subject CIK to universe → `regulatory_designation` signal tagged with type. The
filing is the company's OWN material-event filing, so a universe CIK on a filing containing the phrase =
that company announcing its own designation (high precision). **Pagination:** a phrase is common across
all filers (~8.8k orphan filings) and efts can't filter to our ~780 CIKs in one query; **phrase-first,
exhaust each phrase** (~200 requests, `max_pages=100`, stops early) beats entity-first (780×5=3900
queries) on both cost and completeness. efts caps at a 10k result window — flagged as a known bound.
Designations are DURABLE (don't lapse), so lookback is ~4 years. `evidence_summary.regulatory_designations`
{types,count} folds into the scoring packet + rubric dim 4 (Breakthrough/RMAT weighted above Orphan/Fast-
Track). Kept as evidence enrichment (a designation-based pre-filter path is a future opt-in, like clinical
widening). Security inherits edgar_fts (hard-coded host, phrase-quoted+encoded, capped reads, retry).
**Live:** 5 phrases → 8,243 signals across **407 of 844 active names** — broad, citation-INDEPENDENT
coverage; precision strong (richest stacks = Lexeo/Prime/Taysha/Metagenomi). Satellos now has capital
(D16) + clinical (M16) + 3 designations (M17). **Status:** built 2026-07-11 — `signals/designations.py`
+ config phrases/lookback/forms + evidence + `--designations` CLI + `test_designations.py` (3) + M17
explainer. 136 tests, all offline. Zero spend (no key, no LLM).

## D16 — Capital-markets signal covers foreign private issuers (6-K/F-10/Form-D) + modernized 13D/G labels
**Spec ref:** §3.5. **Trigger:** Satellos (the anchor case) kept failing the pre-filter's capital gate,
blamed on a "Canada / no-EDGAR gap." Verifying the actual EDGAR submissions feed (the "diagnose on the
real data" discipline) disproved that: Satellos DOES file with the SEC as a **foreign private issuer** —
19× **6-K**, 8× **Form D** (financings), 2× **SCHEDULE 13G** (fund crossings), **40-F**, **F-10** (shelf)
— all in-window. The capital signal missed them because `material_forms` listed only US-domestic forms
AND the **stale `SC 13G` label** (SEC's 2024-25 modernization renamed it `SCHEDULE 13G` — the same
relabeling M8/D7 already found, never applied to M7's form list). **Decision:** extend `material_forms`
with the FPI + modernized set — `SCHEDULE 13D/G(/A)`, `6-K` (FPI 8-K equivalent), `F-1/F-3/F-10(/A)` (FPI
registration/shelf/offering), `D/D-A` (Reg-D private placements). **Deliberately EXCLUDE routine annuals
`40-F`/`20-F`:** every FPI files one, so admitting them would satisfy the capital gate trivially without
conviction. Also upgraded `edgar_signals` to `_net.safe_json_retry` (SEC 429s under load → retry with
Retry-After, don't drop the filer). This is a coverage + correctness fix using the existing EDGAR client
(no new source, no spend); it gives every cross-listed non-US name (Canadian, EU/UK ADRs) a real capital
signal instead of silence. **Not a scoring-policy change** — the two-part gate (convergence AND capital)
is unchanged; foreign names simply stop being invisible on the capital half. Regression test
`test_recent_material_filings_matches_fpi_and_modernized_forms`. **Status:** built 2026-07-11 (config
`material_forms` + `edgar_signals` retry + test); full re-scan run to backfill FPI signals. 133 tests.

## D15 — §3.2 clinical-trials signal (ClinicalTrials.gov v2, free/no-key) + opt-in pre-filter widening
**Spec ref:** §3.2. **Trigger:** the digest is bottlenecked by author resolution (~21/168 founders →
only ~12 names clear the pre-filter). Needed an INDEPENDENT convergence dimension that doesn't require
the OpenAlex citation trail. **Chose clinical trials over the planned §3.3 patents** because — verified
empirically before coding — PatentsView's legacy no-key API is deprecated (returns HTML) and the current
`search.patentsview.org` needs an API key and didn't resolve from here, whereas **CT.gov v2 is genuinely
free, no key, reachable** (probed live: returned Acrivon's ACR-368 Phase 2). **Decision:** `clients/
clinicaltrials.py` (query.spons → parse nested protocolSection → flat studies) + `signals/clinical.py`
(§3.2). Precision rails mirror the ownership signal: the company name, corporate/industry suffix stripped
to its stem, must match the **lead sponsor** (role=lead) or a **collaborator** (role=collaborator) by
containment with a `_MIN_CORE` length guard — because a bare query.spons hit can be an investigator-
sponsored trial merely *using* the company's drug (real case: a Moffitt-led Phase 2 of an Acrivon
compound). Reads use the shared `_net.safe_json_retry` (429/5xx + Retry-After) and `search_studies`
returns **None on fetch-failure vs [] on no-result** — the same anti-poisoning contract as the OpenAlex
fix. `evidence_summary` gains a `clinical_trials` block (trial_count / as_lead / active_trials /
highest_phase[_as_lead]) folded into the scoring packet + rubric dimension 4. **Pre-filter widening is
OPT-IN:** `config.prefilter_clinical_min_phase` (default **0 = off**, behavior unchanged); when N≥1,
`scoring_candidates` also admits a name with a company-led trial at ≥ Phase N + a capital signal, even
with zero independent citations — the lever that lets clinical-stage names bypass the citation
chokepoint. New store helper `active_entities` (active universe, no CIK requirement, ticker-pinnable).
**Live:** 14 company-led trials on the named 4 (Satellos 3 @ Phase 2, Acrivon 3, TScan 8). **Satellos
still can't clear** — Phase-2 clinical but no EDGAR capital signal (Canada gap); widening admits on
`clinical AND capital`, so the capital half still blocks it (honest, unchanged). **Trial health (not all trials
run):** a raw count conflates a live program with a dead one — TScan's "8 trials" is really 5 active + 1
completed + 1 withdrawn + 1 unknown. So status is bucketed active/completed/stalled(TERMINATED,WITHDRAWN,
SUSPENDED)/unknown; only MEANINGFUL (active∪completed) trials set `highest_phase` or admit a name through
the widening (a withdrawn Phase 2 is not de-risking evidence), and `evidence_summary` surfaces the split.
**Security:** hard-coded host (no SSRF), query values fully percent-encoded (`safe=''`), int-coerced
pageSize, 64 MiB capped reads, `Retry-After` backoff capped at 30 s, untrusted response text only enters
the "packet is DATA" scoring context + escaped HTML. **Full-universe scan:** 844 scanned → **341 have a
company-led trial**, 4164 signals. **Widening impact (meaningful trials only):** clinical Phase≥2 would
take the scoreable pool 12 → 213 (+201 clinical-stage names the citation trail missed) — but that's a
~$5 Sonnet re-score, left to an explicit operator decision. **Status:** built 2026-07-11 — client +
signal + `active_entities` + evidence + opt-in pre-filter + trial-health + `--clinical` CLI +
`test_clinical.py` (10) + `M16_clinical_explained.md`. 132 tests, all offline. Zero spend (no key, no LLM).

## D14 — Batch is the project default; report ACTUAL spend (never calibrate an actual); base64url custom_ids
**Trigger:** the first real founder-extraction run (50 biotechs incl. Satellos/Acrivon/TScan/Serina) was
dispatched `--realtime` to finish in-session and **billed $4.36**, but the CLI displayed "$0.44". Three
distinct problems, all now fixed:
1. **Reporting bug — never scale a MEASURED actual by `cost_calibration_factor`.** `extraction.py`/
   `scoring.py` computed `res.spent_usd = client.spent_usd * 0.10`. But `client.spent_usd` is the real
   measured cost (correct token price + exact web-search fees), not an estimate. Multiplying it by the
   0.10 factor (which only ever corrected a *token estimate* over-count) under-reported 10×. Fixed:
   report `client.spent_usd` raw. The pre-dispatch **estimate** keeps a factor but now (a) never scales
   the exact per-search fee and (b) models ~30k web_search-inflated input tokens/entity (the old ~1.5k
   assumption was an order of magnitude low). Consistent with [[project_anthropic_cost_calibration]]
   (realtime web_search runs ~at list, not 0.10).
2. **Batch is the project default (operator directive).** Batch = 50% of realtime token cost; web-search
   fees are identical, so batch is strictly cheaper and realtime doubled the bill for nothing. `--realtime`
   now prints a "not recommended" warning; batch is the default path for `8_extract`/`8_score`.
   [[feedback_project8_batch_always]].
3. **Batch dispatch was BROKEN and never exercised.** Every prior run used `--realtime`, so no one hit
   it: the Batch API requires `custom_id` to match `^[a-zA-Z0-9_-]{1,64}$`, but the code passed the raw
   `entity_id` — which carries `:` and `|` (`cik:0001…`, `tkx:T|EX`). Fixed with base64url encode/decode
   (`anthropic_client._enc_cid/_dec_cid`) — reversible with NO stored map, so submit→collect and
   `--resume` round-trip cleanly. Regression test `test_anthropic_client.py`.

**Also this session:** `8_extract --tickers MSLE,ACRV,…` PINS named companies to the front of the
work-list (guaranteed inside `--limit`) — needed because default ordering buries non-M6 names (Serina)
at the tail. And the §5.2 independence refinement had a **poisoning bug**: on an OpenAlex 429 it stamped
`independence_at` with score 0, marking the founder "refined" so `only_missing` skipped it forever — one
throttled run permanently blocked retry. Fixed: a transient fetch failure leaves the founder unstamped
for retry. **Live run result:** 50 extracted (Batch after the fix), literature → 984 independent-lab
citations, scoring (Batch, $0.10 actual) → **Acrivon deep-dive-candidate (68)** (Jesper Olsen /
Copenhagen phosphoproteomics, RA Capital), **TScan surveil (58)** (Elledge/Harvard T-cell antigen
discovery, 194 independent citations). Satellos/Serina didn't clear the pre-filter — Satellos has NO
EDGAR capital signal (TSX-listed; real Canada-coverage gap), Serina's chemist founders didn't resolve to
independent citations. §9 harness on the run: Acrivon = 1 true-positive (precision/recall 100% on n=1,
correctly flagged INSUFFICIENT-DATA). **Status:** all fixed + 8 new tests (123 total, all offline).

## D13 — §9 validation harness back-tests conviction vs a labeled set; zero-spend; two recalls kept separate
**Spec ref:** §9 (the scoring/independence/novelty calls are "unverified against a labeled dataset" —
back-test against known cases, Satellos included, before trusting a flag). **Decision:** build a
**zero-spend** harness (`validation.py` + `scripts/8_validate.py`) that reads only what the pipeline
has already scored — no Claude calls, no network — and joins a hand-labeled ground-truth set
(`validation/known_cases.yaml`: positives that re-rated on an under-recognized mechanism vs
controls that should NOT flag) against the store. It reports three things, deliberately un-conflated:
- **Funnel / survivorship** — how far each case travelled (`not_in_universe → below_floor/above_ceiling
  → in_universe → prefilter_cleared → scored`). Losses in early stages cap recall *upstream of the
  model*; no scoring quality recovers them. This makes the §9 survivorship bias measurable.
- **Classification** — precision/recall/F1/confusion **over the scored subset only** (the call's
  discrimination given it saw the case), deep-dive-candidate ⇒ predicted-positive (or a
  `conviction_score` threshold via `--rule score`).
- **Threshold sweep** — precision/recall across score cutoffs (D3 ranks the digest on the score).

**Two recalls are reported and must not be conflated:** `funnel_recall` (scored positives / *all*
positives — end-to-end, includes survivorship loss) vs `model_recall` (TP/(TP+FN) over the scored
subset). Undefined metrics render as `—`/`None`, never a fake 0; a `sufficient` flag (≥3 pos / ≥2 neg
scored) gates whether the numbers are trustworthy and prints a loud INSUFFICIENT-DATA banner otherwise.
Matching is highest-precision-first: CIK → ticker/alias (exchange-suffix tolerant) → suffix-normalized
name (drops corporate forms like *Inc/Ltd*, KEEPS industry words like *therapeutics* to preserve
precision). **Real finding from the first live run:** of 6 seed cases, Satellos (MSLE) matched and sits
`in_universe` **unscored** (a concrete work-list item — run extract→literature→score on it), while
Arcus/Cytokinetics are **`above_ceiling`** — already re-rated past the $3B small-cap ceiling. That
exposes a structural limit for back-testing *known winners*: winners outgrow the active universe, so a
faithful precision/recall needs **point-in-time (as-of) caps** in the labeled set, or a ceiling-relaxed
validation mode — noted, not hacked into the live gate. **Label quality is operator-owned:**
`verified: false` rows are marked ⚠︎ and excluded from trust; only Satellos ships verified (the spec's
anchor). **Status:** built 2026-07-11 (`validation.py`, `scripts/8_validate.py`, `validation/known_cases.yaml`,
store `entities_by_ticker`/`get_score`, `test_validation.py` ×11, `M15_validation_explained.md`). Zero new spend.

## D12 — §5.2 independence refinement is zero-LLM co-authorship-graph classification, not a Claude call
**Spec ref:** §5.2. **Decision:** M10's string heuristic calls a citation "independent" whenever it's
from a different institution — but a founder's former **co-authors/trainees** who moved elsewhere aren't
independent validators. The refinement uses the **OpenAlex co-authorship graph** (free) rather than a
Claude judgment — because "has this citing author ever co-published with the founder?" is a graph FACT,
more reliable (and cheaper) than an LLM guess. `signals/independence.py` reclassifies each citation of a
founder's foundational paper into **self · collaborator · same_institution · industry · independent**
(precedence in that order; `industry` = a citing institution of OpenAlex type `company`), and computes a
recency-decayed `independence_score = Σ 0.5^(age/5yr)` over the genuinely-independent citations. The
refined `independence` **overwrites** the coarse M10 tag on the same signal_id, so `evidence_summary`'s
`independent_citations` (and thus the scoring pre-filter + packet) automatically tighten to the TRUE
count. Schema **v7** adds `founder.{independence_score, independence_at}`. Zero-LLM; per-founder persist;
idempotent. **Live result (13 founders, 1,780 citations, free, 71s):** 193 collaborator + 124 industry
citations that M10 had counted as "independent" were downgraded (~18%); e.g. **Tenax 198→133 independent
(53 were ex-co-authors)**, while CSBR (182 indep, 1 collab) and cold-discovery Vistagen (191 indep,
score 124.8) proved cleaner. **NOTE:** existing conviction scores were computed on the coarse counts —
re-score with `8_score.py --force` to fold the refined evidence into conviction. **Status:** built
2026-07-11 (`8_signals.py --independence`). **Deferred:** advisor/grant-network relationships (a Claude
§5.2 call could add these on top); patents via **free PatentsView** (no subscription needed).

## D11 — Upper cap ceiling ($3B) + cold-discovery targeting + two-way export (§2.4); surfaced by a real run
**Spec ref:** §2.1 (small/micro-cap), §2.4 (two-way sync), §9 (survivorship). **Trigger:** the first
cold-first sweep (`in_existing_universe ASC` ordered by `entity_id`) surfaced **mega-caps** — Pfizer,
Eli Lilly, BMY, Abbott — whose M9 founders were 19th-century industrialists (Eli Lilly the 1876
apothecary) with no OpenAlex profile. Two root causes, both fixed:
- **No upper ceiling.** Phase 1 had only the $10M floor, so 153 mega-caps (>$3B) sat in the active
  universe — wrong for a small/micro-cap thesis (the scorer could surface Pfizer). Added
  `mktcap_ceiling_usd = $3B` (matches M6's band) + an `above_ceiling` column (schema **v6**), set at
  enrich/recompute. **Recall-safe: flag, not delete.** Active universe = `is_live=1 AND below_floor=0
  AND above_ceiling=0`; added to every thesis work-list (signals/extract/literature/score/ownership).
- **Cold-first now targets small caps.** `cold_first` orders `in_existing_universe ASC, market_cap ASC`
  (smallest known cap first) — ordering by `entity_id` alone surfaced mega-caps.
- **Two-way export (§2.4).** `write_watchlist` → `Outputs/watchlist.csv` (ticker/exchange/flag/score/…),
  the deep-dive+surveil names in a format the existing pipeline can ingest — the module feeds back out,
  not just one-way in. `8_score.py --digest`/run writes it.

**Live result:** after applying the ceiling (153 mega excluded → 844 active) and re-running cold-first
on the smallest names (Vistagen/Theriva/Jaguar/Kiora, all ~$10M), 4 micro-cap founders resolved → 386
independent citations → **2 genuine cold-discovery candidates in the digest** (Theriva 52, Vistagen 42,
both $10M, non-M6) alongside the existing-universe names. **§9 survivorship confirmed:** the module
resolves founder-lineage names far better than no-academic-pedigree first-time founders — an honest,
unfixed limitation. **Status:** built 2026-07-11 (`8_extract.py --cold-first`, ceiling in enrich,
`8_score` watchlist). Cost note: the wasted ~$0.09 mega-cap extraction was the price of finding the bug.

## D10 — Stack-convergence scoring is the capstone; rules-pre-filter → Claude → ranked digest (§5.4/§7)
**Spec ref:** §5.4, §5.5, §7; decision D3 (rubric defined fresh). **Decision:** the capstone fuses the
assembled evidence per candidate into one routable conviction. It runs **only on candidates that clear
a rules-based pre-filter** (§5.5) — active universe, ≥`prefilter_min_independent` independent-lab
citations, AND ≥1 ownership/capital signal, not already scored — so the expensive full-model call is
reserved for genuinely-cornered names, never the whole universe. The **stack-convergence rubric is
defined fresh** (D3): five dimensions mapped to the evidence Module 8 produces — (1) independent
scientific validation (the literature independent-citation signal), (2) capital-markets conviction
(specialist-fund crossings + insiders), (3) academic pedigree/founder lineage, (4) mechanism novelty &
translational stage, (5) base-rate discipline (conviction must come from **variant perception**, not one
loud signal; absence ≠ negative evidence; [V]/[INF] tagging; never invent). Model = **`claude-sonnet-5`**
(stronger than the Haiku extraction tier, §5.5); forced §5.4 schema + a `conviction_score` for ranking;
reuses the M9 client (Batch default, `[y/N]` gate, `max_usd_per_run`, per-candidate persist, skip-cache
on `scoring_prompt_version`, batch_id→file for `--resume`). Output: a ranked Markdown **digest** (§7,
deep-dive first, `Outputs/digest.md`) — raw markdown (an HTML-render step must escape at that boundary).
Schema **v5** adds the `score` table. **Live-validated (6 pre-filtered candidates, ~$0.03):** disciplined
analyst-grade output — Tenax → deep-dive-candidate (71) on a clean 198:2:0 independent-citation ratio +
Perceptive/Venrock holders; 5 → surveil, each correctly noting mechanism/stage gaps and that
`in_existing_universe` names cut against the "under-recognized" premise. **Status:** built 2026-07-11
(`scoring.py`, `scripts/8_score.py`). **Deferred:** §5.2 Claude independence refinement feeding the
scorer; two-way export of deep-dive names back to the existing pipeline (§2.4); an HTML digest render.

## D9 — Literature/citation signal via OpenAlex, founder-keyed; independent-citation heuristic; zero-LLM
**Spec ref:** §3.1 (the module's thesis signal). **Decision:** for each M9 founder, resolve the OpenAlex
author (free, no key — the spec's recommended source for author + citation-network data) and emit
`literature` signals: recent **publications** by the founder + **citations of the founder's foundational
paper**, each tagged by a cheap independence heuristic (self / same_institution / independent). The
**independent** citations are what the module exists to surface (an independent lab building on a
founder's science = under-recognized corroboration). Zero-LLM; Claude's §5.2 classification refines the
heuristic later. **Re: M6's D9 OpenAlex dismissal** — that was for *company→institution* matching (thin
small-biotech coverage); Module 8 uses *author→works→citations*, OpenAlex's forte, disambiguated on the
founder's institution — the dismissal doesn't transfer. The rate-limit lesson does: polite pool
(`mailto`), shared limiter, fail-open. **Author disambiguation is high-precision** (`pick_author`:
name-token match + institution/company hint; unique or most-cited, else skip) — a wrong author →
wrong papers → wrong signal. Foundational paper = the author's most-cited work; citing works pulled via
`cites:` (capped `literature_max_citing`). Idempotent, per-founder persist, skip already-resolved
(`literature_at`). Schema **v4** adds `founder.{openalex_author_id, foundational_work_id, literature_at}`.
**Live-validated:** Stuart Rich (Tenax) resolved → foundational 1991 paper (3,507 cites) → **198
independent-lab citations** + 11 recent pubs, 12s. The 2 founders with "Unknown" institution correctly
skipped (recall conservative by design). **Status:** built 2026-07-11 (`clients/openalex.py`,
`signals/literature.py`, `8_signals.py --literature`). **Deferred:** §5.2 Claude independence refinement;
the full sweep depends on the full M9 founder sweep first.

## D8 — Founder-lineage extraction is the first Claude spend; cheap Haiku tier, gated, lessons-applied
**Spec ref:** §5.1, §5.5. **Decision:** the first Claude call in Module 8 is **founder-lineage
extraction** — per active-universe entity, Claude researches the scientific founders / key inventors /
SAB via web_search and returns a structured roster → the `founder` table (schema **v3**), the join
surface the literature/independent-citation signal (§3.1/§5.2) will key on. Runs on the **cheap Haiku
tier** (`claude-haiku-4-5`, §5.5) with the **basic `web_search_20250305`** variant — it honors
`max_uses`, is ~10× faster than the dynamic `web_search_20260209`, and is the only web_search valid on
Haiku ([[feedback_claude_web_search_variant_and_output_cap]]). All the ported dispatch lessons apply
([[feedback_reuse_claude_dispatch_patterns]], [[feedback_persist_during_long_api_batches]]): forced
structured output (json_schema) sized to full `max_output_tokens` so JSON isn't truncated→dropped;
**Batch API default** (50%) with the batch_id persisted to `data/extract_batch_id.txt` before polling
for `--resume`; realtime async fan-out for small runs; **per-entity persist**; **skip-cache on identity
+ prompt version** (`extraction_prompt_version` bump re-opens everyone); a mandatory `[y/N]` cost gate +
`max_usd_per_run` hard guard; untrusted company text delimited as data (model output only writes the DB).
Cost estimate scaled by `cost_calibration_factor=0.10` [[project_anthropic_cost_calibration]]. Model IDs
+ pricing are current API facts (claude-api skill). **Live-validated (2 entities, ~$0.01, 22s):** real,
verifiable founders (Stuart Rich@Northwestern for Tenax; Michael Hays for NRC Health); where no
institution was found it returned "Unknown" — the never-invent discipline held on a real call.
**Status:** built 2026-07-11 (`clients/anthropic_client.py`, `extraction.py`, `scripts/8_extract.py`).
**Deferred:** literature signal (§3.1 OpenAlex, keyed on these founders), independence classification
(§5.2), the D3 stack-convergence scoring rubric (§5.4). Full sweep not yet run (~$0.10–0.50 at scale).

## D7 — Ownership-crossing signal via EDGAR full-text (efts), fund-first; the M7 13D/G gap closed
**Spec ref:** §3.5 headline. **Decision:** the specialist-fund 5%+ crossings that M7 structurally
couldn't get (13D/G index under the *investor's* CIK, not the subject's) are captured via the EDGAR
**full-text** API (`efts.sec.gov`), whose hits carry *all* associated CIKs. **Fund-first**: search per
watchlist fund (~19 queries, not one per company) for SC/SCHEDULE 13D/G in the lookback window, then
match each filing's CIKs back to our universe (a fund isn't a biotech, so it never self-matches). Two
precision rails: the fund must appear in the filing's `display_names` (not a stray body mention), and
only universe CIKs are emitted. `signal_type="ownership_crossing"`, `source="edgar_fts"`; idempotent
(hash(entity, adsh, fund)); per-fund commit; in-run dedup for efts pagination overlap.

**KEY FINDING (empirical, 2026-07-10):** SEC **relabeled the forms** in its 2024–25 EDGAR
modernization — the old `SC 13D`/`SC 13G` labels return **zero** hits for 2026 (they match only
pre-~2025 filings); recent filings are `SCHEDULE 13D`/`SCHEDULE 13G`. We query **both** old+new labels
(`edgar_fts.OWNERSHIP_FORMS`) so the window spans the transition. Also: efts intermittently 500s under
load → `edgar_fts._get_json` retries 429/5xx (a dropped fund = a whole fund's crossings lost, e.g. Baker
Bros). **Live result:** 19 funds → 278 crossings on our universe in 180d (RA Capital 69, Perceptive 36,
Deep Track 33, OrbiMed 26, Baker Bros 18, …). **Status:** built 2026-07-10 (`clients/edgar_fts.py`,
`signals/ownership.py`, `8_signals.py --ownership`). **Deferred:** classify 13D (active) vs 13G
(passive) and new-position vs amendment (/A); fund watchlist is US-focused — add EU/JP/KR specialists.

## D6 — Phase 2 starts with the capital-markets signal (EDGAR), zero-LLM, keyed on CIK
**Spec ref:** §3.5, §8 Phase 2. **Decision:** the first signal ingester is **capital-markets** —
"your highest-value, most reliable structured source" (§3.5) and fully free/zero-LLM, so no spend and
no prompt-injection surface. It reads each active-universe entity's recent SEC filings (submissions
API, one call per CIK) and writes a `signal` row per **material form** within a lookback window (default
180d): SC 13D/G (5%+ ownership crossings), Form-4 (insiders), 8-K (material events), S-1/S-3/424B5/424B3
(registration/shelf/ATM raises). **Scope = active universe** (`is_live=1 AND below_floor=0 AND cik NOT
NULL`) — includes unknown-cap names (a fresh 13D on an unpriced micro-cap is exactly the signal), skips
known-below-floor. Idempotent (signal_id = hash(entity, accession, form)); per-entity commit
(crash-safe); bounded-concurrency fetch, main-thread persist.

**KEY FINDING (full run, 880 entities → 17,645 signals):** by-form = Form-4 12,544 · 8-K 4,413 · 424B5
311 · 424B3 185 · S-3 151 · S-1 41 — and **zero SC 13D/13G**. This is structural, not absence of events:
a 13D/G is filed under the *investor's* CIK (the fund), not the subject company's, so the submissions
API for a company never returns 13D/G *about* it. Form-4 (insider) IS indexed under the issuer CIK, so
those come through. **Consequence:** M7 captures insider activity, material 8-Ks, and capital raises —
but the §3.5 headline signal (specialist-fund 5%+ ownership crossings) requires the **EDGAR full-text
search API** (`efts.sec.gov`) keyed by subject + fund watchlist, which is now the top Phase-2 refinement
(not optional). SC 13D/G stay in `material_forms` (harmless; they'll match once efts is added).
Secondary: Form-4 volume is high (~14/entity/180d) → needs clustering before scoring.
**Status:** built 2026-07-10 (`signals/capital_markets.py`, `scripts/8_signals.py`); efts 13D/G = next.

## D5 — GLEIF LEI backfill is high-precision (exact-name), never overwrites, collisions → review queue
**Spec ref:** §2.3 (LEI-first join), phase1 §3.4/§8 Q1. **Decision:** backfill the Legal Entity
Identifier from GLEIF (free, no key) for entities lacking one — but **LEI is Module-8's strongest
identity key**, so a wrong LEI would cause a false merge on the next universe build. Therefore matching
is deliberately **high-precision / low-recall**: `gleif.pick_lei` accepts an LEI only when exactly one
candidate's *normalized* legal name equals the entity's (ISSUED-status tiebreak); zero/ambiguous → no
LEI. `set_lei` **never overwrites** an existing LEI. If the picked LEI is already held by a *different*
stored entity, it is **not set** — a `gleif_lei_collision` row is queued instead (it usually means the
two rows are the same company, a store duplicate to reconcile by hand), so we never create two rows
sharing one LEI. Live-validated: cleanly matches `Genmab→GENMAB A/S`, `Zealand→ZEALAND PHARMA A/S`;
correctly rejects fuzzy fulltext garbage (`Acumen`→PotNetwork/Rexam). Recall is conservative by design;
improve later (fuzzy-completions endpoint / country-less fallback) only if precision holds. **Status:**
built 2026-07-10; `scripts/8_enrich.py --lei`, opt-in, re-runnable (only touches LEI-less rows).

## D4 — Market-cap enrich via yfinance; the floor is a queryable `below_floor` flag, not a delete
**Spec ref:** phase1 §5, §8 Q2. **Decision:** `company_tickers.json` carries no market cap, so the
universe build leaves EDGAR-discovered names `mktcap_unknown`. A separate **enrich stage**
(`enrich.py` + `scripts/8_enrich.py`) fetches each unknown-cap entity's cap via **yfinance**
(LOCAL-ONLY ToS — swap for a licensed provider before public deploy, [[project_data_provider_switch]]),
converts to USD (`fx.py`, static illustrative rates incl. CAD for TSX names), and persists
`market_cap_usd` + a new **`below_floor`** column (schema **v2**, migration 2). The floor is now a
*queryable flag* (active universe = `is_live=1 AND below_floor=0 AND mktcap_unknown=0`), not merely an
audit row — so Phase-2 signal jobs can gate on it. Each ticker is **persisted immediately** (repo rule
for long API loops, [[feedback_persist_during_long_api_batches]]); a miss stays KEPT + `mktcap_unknown`
(missing ≠ small ≠ delete) with `enriched_at` stamped so it isn't retried every run. **Status:** built
2026-07-10; enrich is opt-in (`scripts/8_enrich.py`), re-runnable, resumable.

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
