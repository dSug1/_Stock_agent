# Module 1 — Configuration & Foundation

**Status:** Implemented (2026-04-22)
**Last updated:** 2026-04-22
**Runtime:** Non-runtime. Produces state, not files.
**Code:** [src/module_1/](../src/module_1/) — `config.py`, `logging_setup.py`, `quarter.py`, `paths.py`.
**Smoke test:** [scripts/verify_module_1.py](../scripts/verify_module_1.py).

Module 1 is the foundation layer every runtime module (Modules 3–7) imports at startup. It owns the canonical YAML config schema, loads and validates every config at import time, resolves paths, configures logging, and exposes quarter-format helpers. Misconfiguration here silently corrupts every downstream module — so validation is strict and errors fail loudly with file + line.

Module 2 (13F ingest, Layer 0 + Layer 1) predates Module 1 and will **not** be retrofitted. It remains self-contained with its own `src/database/db.py` path resolution. Module 1 reads the DB path from config and defers to Module 2's connection helper; new modules (3–7) import Module 1 directly.

---

## Role

Own and expose:

1. **Canonical config schema.** One `pipeline.yaml` for global settings, plus per-module YAMLs (`filters.yaml`, `archetypes.yaml`, `ranking.yaml`, `calibration.yaml`, and future `enrichment.yaml` / `llm_scoring.yaml` / `outcome_tracking.yaml`). Module 1 owns the global schema; per-module schemas are owned by their respective specs and only validated for YAML parseability here.
2. **Load-time validation.** Parse every YAML, check schema, check paths resolvable, check DB tables present, check required `.env` keys. Fail on any error with file + line.
3. **Logging setup.** `get_logger(module_name)` returns a preconfigured logger with dual file+console output; verbosity controlled by debug flag.
4. **Quarter helpers.** Convert dates to `YYYYQn` format; resolve `"auto-latest"` to the most recent quarter present in the DB.
5. **Path resolution.** Single source of truth for `2_fundparser.db`, `prices.db`, `outcomes.db`, `_intermediate_outputs/`, `_outputs/`, `logs/` — all resolved relative to `2_Funds_parser/` regardless of the caller's cwd.
6. **Debug vs. production mode.** Single flag in `pipeline.yaml`; Module 1 exposes it as a typed field. Each runtime module reads it and adjusts behavior (row limits, cache clearing, log verbosity).

Module 1 does **not** own: DB connection handling (Module 2's [src/database/db.py](../src/database/db.py)), HTTP clients (per-module), output writers (per-module), or EDGAR / yfinance / OpenFIGI / Anthropic clients.

---

## Inputs

- `2_Funds_parser/config/pipeline.yaml` — global config (schema below).
- `2_Funds_parser/config/<module_N>.yaml` — per-module configs. Module 1 validates they exist and parse as YAML; field-level schemas are enforced by the owning module.
- `.env` at **repo root** (`C:\Users\sugit\Documents\Claude\_Stock_agent\.env`) — API keys. Shared with `1_Stock_Picker/`; not duplicated inside `2_Funds_parser/`.

## Outputs

None on disk. `load_config()` returns a frozen `PipelineConfig` object.

---

## `pipeline.yaml` schema

```yaml
pipeline:
  mode: debug                    # "debug" | "production"
  quarter: auto-latest           # "auto-latest" | "YYYYQn" (e.g., "2026Q1")

paths:
  fundparser_db: 2_fundparser.db                 # resolved relative to 2_Funds_parser/
  prices_db: prices.db                           # Module 4b
  outcomes_db: outcomes.db                       # Module 7
  reports_dir: Outputs                           # human-browsable HTML/Excel reports (Module 2's report lives here)
  intermediate_outputs_dir: _intermediate_outputs  # Parquet plumbing between modules (machine-only)
  outputs_dir: _outputs                          # pipeline's final machine-readable artifacts
  logs_dir: logs
  fund_list_source: Input/list_of_funds.xlsx

db_schema:
  funds_table: funds
  filings_table: filings_log
  holdings_table: holdings
  cusip_map_table: cusip_ticker_map

cache:
  ttl_days: 30                   # hard cap for per-module caches; core lookups (cusip_ticker_map, funds) are exempt
  clear_on_debug_start: true     # debug mode only; production never auto-clears

logging:
  level: INFO                    # forced to DEBUG in debug mode regardless of this value
  file_rotation_days: 14
  console: true

debug:
  row_limit: 50                  # per-module cap on rows processed when mode=debug
  prompt_before_api_calls: true  # Module 6 always prompts regardless; this adds prompts for Module 5 yfinance bulk fetches
```

All fields required unless defaulted in the schema module.

## Environment file

`load_config()` calls `dotenv.load_dotenv()` on the **repo-root `.env`** (`C:\Users\sugit\Documents\Claude\_Stock_agent\.env`) automatically. Callers do not pre-load or know the path. This is safe: `python-dotenv` only reads the file into the current process's environment variables — no file mutation, no network, no persistence beyond the process.

A companion **`.env.example`** file lives at repo root, committed to git, documenting the required keys. `.env` itself stays gitignored.

### Required keys

- `ANTHROPIC_API_KEY` — required only if Module 6 runs. Module 1 warns (not errors) if missing, so running Modules 1–5 in isolation stays frictionless.
- `OPENFIGI_API_KEY` — optional; already used by Module 2's CUSIP resolver. Validated here for a uniform error surface.

---

## Directory structure owned

Three distinct output folders, each with a specific purpose. Locked 2026-04-22:

- **`Outputs/`** — Human-browsable HTML and Excel reports. Module 2's `2_funds_report.html` already lives here; future modules' human-facing reports (Module 4's filter summary and ranking report, Module 6's final integrated report) go here too.
- **`_intermediate_outputs/`** — Parquet files that flow between pipeline modules (Module 3's universe, Module 4a's survivors, Module 4b's ranked candidates, Module 5's context pack). Machine-only plumbing; not meant for manual inspection.
- **`_outputs/`** — The pipeline's final machine-readable artifacts that aren't intermediate plumbing (e.g., Module 6's `llm_scores_{quarter}.parquet`, Module 7's snapshot exports). Durable per-run results.

```
2_Funds_parser/
├─ config/                       (new; owned by Module 1)
│   ├─ pipeline.yaml             (global)
│   ├─ filters.yaml              (Module 4a)
│   ├─ archetypes.yaml           (Module 4b)
│   ├─ ranking.yaml              (Module 4b)
│   └─ calibration.yaml          (Module 4b)
├─ Outputs/                      (exists; extended to hold all human-facing HTML/Excel reports)
├─ _intermediate_outputs/        (new; per-module Parquet plumbing)
├─ _outputs/                     (new; pipeline's final machine-readable artifacts)
├─ logs/                         (exists; Module 1 formalizes layout)
└─ src/
    └─ module_1/                 (new; Python package — no leading digit allowed)
        ├─ __init__.py
        ├─ config.py             (load_config, PipelineConfig, validation)
        ├─ logging_setup.py      (get_logger)
        ├─ quarter.py            (YYYYQn helpers)
        └─ paths.py              (canonical path resolution)
```

Note: Module 4's spec currently writes `filter_summary_{quarter}.html` and `ranking_report_{quarter}.{html,xlsx}` to `_outputs/`. Per this three-folder decision, those land in `Outputs/` instead. Module 4 spec to be patched at implementation time.

---

## Function signatures

```python
# src/module_1/config.py — implementation uses frozen dataclasses + manual validation.

@dataclass(frozen=True)
class PipelineConfig:
    mode: Literal["debug", "production"]
    quarter: str                            # resolved to YYYYQn even if input was "auto-latest"
    paths: PathsConfig
    db_schema: DbSchemaConfig
    cache: CacheConfig
    logging: LoggingConfig
    debug: DebugConfig


@lru_cache(maxsize=None)
def load_config(
    pipeline_yaml_path: str | Path = "config/pipeline.yaml",
    project_root: Path | None = None,
) -> PipelineConfig:
    """Read pipeline.yaml, resolve paths, load repo-root .env, validate DB schema, return frozen config.
    Raises ConfigError with file (+ line where possible) on any validation failure."""
```

```python
# src/module_1/logging_setup.py

def get_logger(module_name: str, config: PipelineConfig | None = None) -> logging.Logger:
    """Return a logger configured per pipeline.logging. Idempotent: same name returns the same
    logger without re-adding handlers. Debug mode forces DEBUG regardless of config.logging.level."""
```

```python
# src/module_1/quarter.py

def date_to_quarter(date: pd.Timestamp | str) -> str:
    """'2026-03-31' → '2026Q1'. Uses the quarter the date falls in."""

def resolve_quarter(config: PipelineConfig, db_conn: sqlite3.Connection | None = None) -> str:
    """If config.quarter == 'auto-latest', read MAX(period_of_report) from the holdings table and
    return its quarter. Else return config.quarter after validating YYYYQn format."""
```

```python
# src/module_1/paths.py

def resolve(relative: str, project_root: Path | None = None) -> Path:
    """Resolve a relative path against 2_Funds_parser/ regardless of cwd."""

def ensure_dir(path: Path) -> Path:
    """Create directory if missing; return the path."""
```

---

## Validation rules

On `load_config()`:

1. **pipeline.yaml exists.** Missing → fatal with expected path.
2. **YAML parses.** Syntax error → fatal with file + line.
3. **Required fields present.** Missing field → fatal with field path (e.g., `pipeline.mode missing`).
4. **Type check.** `mode` is literal enum, `ttl_days` is int > 0, paths are strings, etc. Wrong type → fatal.
5. **Paths resolvable.** Parent of every path exists or is creatable. Non-writable destination → fatal.
6. **DB schema match.** Open `2_fundparser.db` via Module 2's `get_connection`, run `PRAGMA table_info(<table>)` for each mapped table. Missing table → fatal with the expected name.
7. **.env loadable.** Module 1 calls `dotenv.load_dotenv()` on the repo-root `.env` automatically. Missing `.env` file → warning only (caller workflows that only run Modules 1–5 remain frictionless).
8. **Quarter format.** If `config.pipeline.quarter != "auto-latest"`, regex-check `^\d{4}Q[1-4]$`.

Validation is idempotent: `load_config()` is LRU-cached at module scope; calling twice in one process returns the same instance without re-reading files.

---

## Debug vs. production mode

| Aspect                   | `debug`                                           | `production`                         |
|--------------------------|---------------------------------------------------|--------------------------------------|
| Row limit                | `debug.row_limit` (default 50)                    | None                                 |
| Log level                | `DEBUG` (overrides `logging.level`)               | `logging.level` (default `INFO`)     |
| Stage caches             | Cleared on startup if `cache.clear_on_debug_start: true` | Never auto-cleared            |
| API cost prompts         | Always on                                          | Only for paid API calls (Module 6)   |
| Output paths             | Same as production (no debug suffix)              | —                                    |

Core lookup caches (`cusip_ticker_map`, `sec_company_tickers.json`, `funds`) are **never** cleared in either mode. Debug mode affects per-module stage caches only.

---

## Logging conventions

- One logger per module: `logger = get_logger("module_3")`, `"module_4a"`, `"module_4b"`, etc.
- Format: `%(asctime)s %(levelname)s [%(name)s] %(message)s`.
- File destination: `logs/pipeline.log`, rotated at midnight via `TimedRotatingFileHandler`; `logging.file_rotation_days` rotated files retained (default 14).
- Console: always on when `logging.console: true`. Stream is stdout for all levels.
- Debug mode forces `DEBUG` regardless of `logging.level`.
- `get_logger(name)` is idempotent — repeated calls for the same name return the same logger without re-adding handlers.

---

## Quarter convention

- Internal format: `YYYYQn` (e.g., `"2026Q1"`).
- Derivation rule: quarter is inferred from `period_of_report` (the 13F reporting period-end date), **never** from `filings_log.filing_date`. This matches the cross-cutting rule in [Overall_specification.md](Overall_specification.md) and Module 2's existing canonical-date decision.
- `period_of_report = 2026-03-31` → `2026Q1`. `period_of_report = 2025-12-31` → `2025Q4`.
- `resolve_quarter(..., "auto-latest")` runs `SELECT MAX(period_of_report) FROM holdings`. `holdings` chosen over `filings_log` because it is the richer table Module 3 will read from and both carry `period_of_report`.

---

## Integration with existing Module 2 code

Module 2 currently uses its own path resolution in [src/database/db.py:14-15](../src/database/db.py):

```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "2_fundparser.db"
```

Module 1 does **not** replace this. Instead:

- `module_1.paths.resolve("2_fundparser.db")` returns the same absolute path — both roots land on `2_Funds_parser/`.
- `load_config()` calls Module 2's `get_connection(config.paths.fundparser_db)` during DB schema validation.
- Module 2's existing scripts (`2_ingest_13f.py`, `2_build_report.py`, `2_seed_funds.py`) continue to work without importing Module 1. Only new modules (3–7) and any new Module 2 enhancements import Module 1.

This lets Module 1 land without touching any existing Module 2 code.

---

## Failure modes

| Condition                                        | Behavior                                         |
|--------------------------------------------------|--------------------------------------------------|
| `pipeline.yaml` missing                          | `FileNotFoundError` with expected path           |
| YAML syntax error                                | `ConfigError` with filename + line number        |
| Required field missing                           | `ConfigError` with field path                    |
| Type mismatch on a field                         | `ConfigError` with field path + expected type    |
| DB schema mismatch                               | `ConfigError` with expected table name           |
| `ANTHROPIC_API_KEY` missing                      | `WARNING` logged; `load_config()` succeeds       |
| `logs/` not writable                             | `ConfigError`                                    |
| `quarter` not in `YYYYQn` format                 | `ConfigError`                                    |

All `ConfigError` instances include a filesystem pointer so the user can open and fix.

---

## Caching

- `load_config()` is LRU-cached at module scope; repeated calls in one process return the same `PipelineConfig` instance.
- No on-disk artifact.

---

## Acceptance tests

1. **Happy path.** Valid `pipeline.yaml` + all referenced module YAMLs present → `load_config()` returns a frozen `PipelineConfig`.
2. **Missing required field.** Delete `pipeline.mode` from YAML → raises `ConfigError` mentioning `pipeline.mode`.
3. **Malformed YAML.** Inject a tab/space mix → raises with file and line number.
4. **Invalid quarter.** Set `quarter: 2026Q5` → raises `ConfigError`.
5. **Auto-latest quarter resolution.** Set `quarter: auto-latest`; with the current DB, resolves to the quarter of `MAX(period_of_report)` in `holdings`.
6. **DB schema mismatch.** Set `db_schema.holdings_table: nonexistent` → raises `ConfigError` mentioning the missing table.
7. **Debug flag propagation.** `mode: debug` → `get_logger("test").level == logging.DEBUG`.
8. **Idempotent load.** `load_config()` called twice returns equal objects; no file re-reads after the first call.

---

## Locked decisions (resolved 2026-04-22)

- **Three output folders.** `Outputs/` (human-browsable HTML/Excel), `_intermediate_outputs/` (Parquet plumbing), `_outputs/` (pipeline-final machine artifacts).
- **`.env.example` at repo root** ([../../.env.example](../../.env.example)), committed to git, documents required keys. `.env` stays gitignored.
- **Module 1 loads `.env` automatically** in `load_config()` via `dotenv.load_dotenv(REPO_ROOT / ".env")`. Safe: dotenv only reads into the current process's env vars.
- **Config directory at `2_Funds_parser/config/`** (sibling of `src/`, `Input/`, `Outputs/`).
- **Validation library = frozen dataclasses + manual validation.** No Pydantic dependency; errors carry file + field path (and line number for YAML-parse errors). Swappable later without touching callers.
- **Log file = `logs/pipeline.log`**, `TimedRotatingFileHandler`, `when="midnight"`, `backupCount=logging.file_rotation_days` (default 14).
- **`auto-latest` quarter source = `holdings.period_of_report`.** Chosen over `filings_log.period_of_report` because `holdings` is the richer table Module 3 will read from; both agree in practice.
