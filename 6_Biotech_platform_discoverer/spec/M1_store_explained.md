# M1 explained — the evidence store and the cardinal rule

*What milestone 1 actually does, in plain terms. Companion to `SPEC_acrivon_pattern_screener.md`
§7 and §0.2.*

---

## In one sentence

M1 builds the **SQLite store that every later stage reads and writes**, and bakes the screener's one
non-negotiable rule — *you may only ever delete a company for market-cap or liveness* — into the
data layer so no later code can violate it by accident.

## Why this is milestone 1 (and why it matters most)

The spec's cardinal rule (§0.2): **Stage 0 is the only stage that can lose a candidate invisibly.**
Every later stage *scores* companies it has already seen, so a mistake there is debuggable — you can
look at the score. But if a company is wrongly dropped at the front door, it simply never appears
again, and nothing flags that it's missing. So the very first thing built is the fence that makes an
invisible loss *impossible*: deletion is allowed for exactly two reasons, and the database refuses
anything else.

## The pieces

| File | Role |
|---|---|
| `src/platform_discoverer/store.py` | The SQLite store + the `Store` data-access object (DAO). Holds the schema and the guardrail. |
| `src/platform_discoverer/models.py` | Typed records (`Company`, `Evidence`, `Score`, `AuditEntry`, plus `ListingRecord` used by M2) so stages pass structured data, not loose dictionaries. |
| `src/platform_discoverer/config.py` | Loads `config/config.yaml` and `config/taxonomy.yaml` (safe YAML only). |
| `config/config.yaml` | Every tunable (market-cap band, nets, weights, model ids, cost cap). Re-runs with different thresholds need no code change. |
| `config/taxonomy.yaml` | The controlled mechanism vocabulary — **37 mechanisms** (24 oncology, 8 autoimmune, 5 GPCR). |
| `tests/test_store.py` | 18 tests; the load-bearing one proves an illegal delete is refused. |

## The data model (seven tables)

One SQLite file, `data/store.db`. Schema is versioned with `PRAGMA user_version` and grows only by
*additive migrations* (repo convention) — so upgrading never rewrites old data.

- **`companies`** — one row per company: identity (name, ticker, exchange, country, ISIN/LEI),
  `mktcap_usd_fd`, the `mktcap_unknown` / `stage1_excluded` / `is_live` flags, `source_nets` (which
  Stage-0a nets caught it), `ta_tags` (mechanism tags, filled in M3), `first_seen`/`last_seen`.
- **`evidence`** — one harvested bundle per (company, source), with a `cursor` for incremental
  re-runs. Populated in M4.
- **`scores`** — one rubric result per (company, run). Populated in M6.
- **`audit_log`** — the spine: every flag, exclusion, cut, deletion, and score writes a row here with
  a `reason` and a timestamp. This is what makes "show me everything it removed and why" answerable.
- **`review_queue`** — borderline cases parked for human review (e.g. no mechanism tag), promotable
  back into scoring without re-harvesting.
- **`seed_labels`** — the known positives/negatives the validation harness (M8) grades each run on.
- **`run_meta`** — per-run metadata (config hash, cost, metrics).

## The guardrail — the heart of M1

The `Store` DAO exposes exactly one way to remove a company:

```python
store.delete_company(company_id, reason="mktcap_out_of_band")   # ok
store.delete_company(company_id, reason="not_live")             # ok
store.delete_company(company_id, reason="no_ta_tag")            # raises IllegalDeletionError
```

The allowed-reason set comes from config but is **intersected with a hard-coded cardinal set**
(`{mktcap_out_of_band, not_live}`) in `config.deletion_allowed_reasons`. That direction matters: a
misconfigured YAML can *narrow* what may be deleted, but **can never widen it** to authorize an
illegal deletion. A refused delete leaves the row exactly as it was and writes no `deleted` audit row
— there is no trace of a removal because no removal happened.

Everything that *isn't* a deletion is a **flag + an audit row**, and is reversible:
- `flag_company(id, "mktcap_unknown")` — missing data is kept and marked, never dropped.
- `exclude_stage1(id, reason)` — sets `stage1_excluded=1`; the row stays, it's just left out of the
  *default* harvest set, and a config switch re-admits it without re-harvesting anything.
- `add_to_review_queue(id, reason)` — parks a borderline case for a human.

The store also ships the §11 audit queries: `why_excluded(id)`, `removed_at_stage(stage, reason)`,
`review_queue_dump()`.

## What's proven (tests)

`pytest tests/test_store.py` — 18 tests. The ones that matter:
- an illegal delete **raises and preserves the row** and writes **no** `deleted` audit row;
- both legal deletes succeed and are audited;
- a config that tries to *add* an illegal reason gets it intersected away;
- a missing market cap is kept and flagged, never dropped;
- a Stage-1 exclusion is reversible (the row survives);
- migrations are idempotent (re-opening the DB does nothing).

## How to run

```bat
:: from 6_Biotech_platform_discoverer\
set PYTHONPATH=src
..\.venv\Scripts\python.exe -m pytest tests\test_store.py -q
```

There is no user-facing output at M1 — it is the foundation the pipeline (M2+) is built on.
