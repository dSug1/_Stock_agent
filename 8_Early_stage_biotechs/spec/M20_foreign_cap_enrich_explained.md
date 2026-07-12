# M20 — Foreign (ISIN-keyed) market-cap enrichment, explained

*House-style milestone note. Companion to `decisions.md` D21. The cross-cutting piece that lets the $3B
ceiling finally apply to the Wikidata-seeded foreign markets — unblocking Nordic/EU/CA names for scoring.*

---

## The gap this closes
M18/M19/D20 seeded Nordic, EU, and Canadian names from Wikidata, but Wikidata carries no market cap, so
they entered `mktcap_unknown` — and the `--nordic`/`--europe`/`--ca-wikidata` providers were opt-in
precisely because, uncapped, the $3B ceiling couldn't drop their mega-caps (Novo, Genmab, AstraZeneca).
This milestone fills those caps so the ceiling works and the small/mid-caps become the active foreign
universe.

## Why the US cap path didn't just work
The M5 cap enrich (`enrich_caps`) keys on `ticker_primary` and calls `yfinance(ticker)`. Foreign names
have an **ISIN, not a yfinance-ready ticker**, and yfinance needs an **exchange-suffixed symbol**
(Zealand = `ZEAL.CO`, Argenx = `ARGX.BR`, Abivax = `ABVX.PA`). So the foreign path needs an ISIN → symbol
resolver first.

## The chain: ISIN → OpenFIGI → yfinance → USD → ceiling
- **`clients/openfigi.py`** (free, no key): POST an ISIN to OpenFIGI's mapping API → its FIGI records.
  The **first record is the primary home venue** (verified: Zealand→`ZEAL/DC`, Argenx→`ARGX/BB`,
  Abivax→`ABVX/FP`); its Bloomberg exchange code is translated to a yfinance suffix (`DC→.CO`, `BB→.BR`,
  `FP→.PA`, `SS→.ST`, `GY→.DE`, …). Pan-European MTF/composite codes (EO/XH/XF…) are **not** real
  exchanges and are skipped, so the picker lands on the real listing.
- **`enrich.enrich_caps_isin`**: for each `entities_needing_cap_isin` (live, has ISIN, no cap, not yet
  enriched) → resolve symbol → `market.fetch_market_cap` → `fx.to_usd` → `store.apply_cap` (which sets the
  floor + ceiling flags). Persists per entity; fail-open (no symbol / no cap → stamped `enriched_at`,
  kept `mktcap_unknown`, not retried). CLI: `8_enrich.py --isin`.

## Reliability + security
- OpenFIGI's keyless tier is ~25 req/min → the run is paced at **0.4/s** with a shared limiter; the POST
  goes through `_net.get_json_retry` (extended this milestone to support a POST `data` body), so 429/5xx
  retry with `Retry-After`. Hard-coded host, capped reads, fail-open — the standard client contract.
- **yfinance stays LOCAL-ONLY** (ToS) — the licensed-provider swap remains the pre-public-deploy task and
  would replace both the US and this foreign cap path (and give live FX vs the static illustrative rates).

## Live proof (2026-07-12)
End-to-end on real names before wiring the stage: Zealand `ZEAL.CO` **$2.83B** (below ceiling → active),
Camurus `CAMX.ST` **$3.16B** (just above → correctly excluded), Vicore `VICO.ST` **$0.34B**, Synact
`SYNACT.ST` **$0.12B** (micro-caps → active), Argenx `ARGX.BR` **$51.3B** and Abivax `ABVX.PA` **$11.6B**
(mega → excluded). So the ceiling now separates the foreign small/mid-caps (the thesis) from the mega-caps
(noise) exactly as it does for US.

## Honest limitations
- **Static FX** (`fx.py`, illustrative [INF]) — a name near the $3B boundary could sit on the wrong side;
  live rates come with the licensed provider.
- **A few unresolved names stay uncapped** — if OpenFIGI's primary venue code isn't in the suffix map (or
  the ISIN doesn't resolve), the name keeps `mktcap_unknown` (recall-safe) and can still reach scoring;
  the pre-filter's convergence+capital gate is the backstop. Extend `EXCH_SUFFIX` as misses surface.
- **Ticker-only foreign names** (no ISIN) aren't covered by this path — most Wikidata names have an ISIN.

## How to run / verify
```
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_enrich_isin.py -q      # 5 offline tests
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py --isin                    # foreign cap pass
# then the ceiling gates them:  active = is_live=1 AND below_floor=0 AND above_ceiling=0
```
