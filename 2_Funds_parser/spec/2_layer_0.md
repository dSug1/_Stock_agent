# 2_Funds_parser — Layer 0: Fund registry

**Scope.** Hold the closed list of funds we want to follow, with
one canonical SEC CIK per fund. Every later layer (filing fetch,
holdings parse, etc.) reads from this table; nothing writes to it
except the seeder.

**Design intent.** Deliberately simpler than the 1_Stock_Picker
`institutions` table: no tier, no multiplier, no tier_label, no
primary_coverage, no processing_status. A fund either is in the
registry or isn't. Downstream layers decide what to do with it;
the registry itself carries no policy.

---

## Data source

`2_Funds_parser/Input/list_of_funds.xlsx`, sheet
`Confirmed Funds`. Columns:

| Column | Header           | Meaning                                              |
|--------|------------------|------------------------------------------------------|
| A      | `Selected funds` | User-preferred display name. Non-empty cell = selected. |
| B      | `Legal Name`     | SEC legal name of the filer.                         |
| C      | `CIK`            | 10-digit zero-padded SEC CIK.                        |

The Excel file is the source of truth. Selection is driven by the
presence of a value in column A — blank column A means "do not
track," regardless of whether B and C are populated.

As of the seed performed on 2026-04-21: **22 funds selected**
(sheet has 177 data rows in total).

---

## Schema

File: [src/database/schema.sql](../src/database/schema.sql)

```sql
CREATE TABLE funds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cik          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    legal_name   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX idx_funds_name ON funds(name);
```

- `cik` is the natural key; `id` exists for join convenience only.
- `cik` is stored as a 10-character zero-padded string to match SEC
  conventions and to keep leading zeros (`0001263508`, not
  `1263508`).
- `name` is the user's display label (column A).
- `legal_name` is the SEC legal filer name (column B); kept verbatim
  from the Excel, including the `/NY` suffix style SEC uses.
- Timestamps are ISO-8601 UTC, produced by
  [`database.db.now_iso`](../src/database/db.py).

No migration framework yet. When the schema evolves, edit
`schema.sql` in place; the table uses `CREATE TABLE IF NOT EXISTS`
so re-running is idempotent on fresh DBs. Additive changes on
existing DBs will need a one-off `ALTER TABLE` until a proper
migration driver lands.

---

## Seed procedure

Script: [scripts/2_seed_funds.py](../scripts/2_seed_funds.py)

```
python 2_Funds_parser/scripts/2_seed_funds.py
```

Behaviour:
- Reads the `Confirmed Funds` sheet, keeps rows with a non-empty
  column A.
- Upserts each row into `funds` keyed on `cik`. On conflict,
  `name` and `legal_name` are refreshed from the Excel and
  `updated_at` is bumped; `created_at` is preserved.
- Aborts if any selected row has a missing `cik` or `legal_name`
  (prevents silent partial seeds).

Re-run the seed after any edit to column A or B of the Excel.

---

## Data quality notes

A few entries carry legal-name strings that look surprising at
first glance. They are correct at the SEC-filer level — the
Excel records the filer entity, not the marketing brand.

| Display name          | SEC legal name                          | Notes                                                   |
|-----------------------|-----------------------------------------|---------------------------------------------------------|
| Versant Ventures      | `HERSHEY TRUST CO`                      | Versant files via the Hershey Trust Company filer CIK.  |
| ARCH Venture Partners | `ARCH VENTURE CORP`                     | Filer name differs from the public fund-family brand.   |
| Athos KG              | `Athos Capital Ltd`                     | Strüngmann family office files through Athos Capital.   |
| Boxer Capital         | `Boxer Capital, LLC`                    | This is the legacy CIK `0001465837`, not the Tavistock entity at `0002018299` (which was deselected). |

These are not errors to "fix" — they reflect the SEC
filer-registration structure and must be preserved verbatim so
downstream EDGAR lookups work.

---

## Deviations from 1_Stock_Picker

For traceability to the prior project:

| Concern                   | 1_Stock_Picker (Layer −1.1)                                  | 2_Funds_parser (Layer 0)            |
|---------------------------|--------------------------------------------------------------|--------------------------------------|
| Table                     | `institutions`                                               | `funds`                              |
| Tier / multiplier         | `tier`, `tier_label`, `multiplier` columns (1A..4, 0.0×..4.0×) | Not modelled.                        |
| Processing tier           | `processing_tier` derived later into `twos_scores`           | Not modelled.                        |
| Coverage note             | `primary_coverage` column                                    | Not modelled.                        |
| EDGAR filer name          | `edgar_name`                                                 | `legal_name` (same concept, renamed). |
| Source of truth           | Hardcoded Python seed (`institution_registry.py`)            | Excel file in `Input/`.              |
| Seeder                    | `scripts/sync_institution_seed.py`                           | `scripts/2_seed_funds.py`.           |

The tier / multiplier / coverage columns were dropped on purpose;
they were policy inputs to the TWOS calculation, which is not
part of this project. If a later layer needs them, add a
side-table (e.g. `fund_weights`) rather than re-introducing
policy fields on `funds`.

---

## Out of scope for Layer 0

- Live EDGAR validation of CIKs (the registry trusts the Excel
  until we build a resolver).
- Deduplication beyond the `UNIQUE(cik)` constraint.
- Per-fund filing ingestion, holdings ingestion, signal
  computation — all later layers.
- Scheduling. Seeding is a manual operation; the daily `.bat`
  (now wired to Layer 1 ingest + report) does **not** call the
  seeder — the Excel registry is changed by hand, not by the runner.

---

## File layout

```
2_Funds_parser/
├── 2_fundparser.db                     (SQLite; gitignored by *.db)
├── run_2_Funds_parser.bat              (daily driver; Layer 1 + report)
├── Input/
│   └── list_of_funds.xlsx              (source of truth for Layer 0)
├── Outputs/                            (generated by Layer 1 report)
│   ├── 2_funds_report.html
│   └── 2_funds_report.cache.json
├── scripts/
│   ├── 2_seed_funds.py                 (Layer 0 — manual)
│   ├── 2_import_from_stockpicker.py    (Layer 1 — one-shot)
│   ├── 2_ingest_13f.py                 (Layer 1 — run daily)
│   └── 2_build_report.py               (Layer 1 — run daily)
├── spec/
│   ├── 2_layer_0.md                    (this file)
│   └── 2_layer_1.md
└── src/
    ├── database/
    │   ├── __init__.py
    │   ├── db.py                       (get_connection, now_iso,
    │   │                                MARKET_VALUE_RAW_USD_CUTOFF)
    │   └── schema.sql                  (funds + holdings + ...)
    └── layer_1/
        ├── __init__.py
        ├── cusip_resolver.py           (OpenFIGI)
        └── edgar_13f.py                (fetch + parse + ingest)
```

**Naming convention footnote.** The project directive is
"all content starts with `2_`". Applied to spec files, scripts,
the .bat and the .db. Not applied to importable Python modules
(`db.py`, `__init__.py`) because Python does not permit module
names that start with a digit. This is a deliberate carve-out,
flagged here so it is not re-litigated.
