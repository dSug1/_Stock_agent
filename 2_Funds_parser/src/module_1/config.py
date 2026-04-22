"""Config loader & validator for 2_Funds_parser.

Reads pipeline.yaml, validates every field, resolves paths against PROJECT_ROOT,
loads the repo-root .env, validates the SQLite schema matches expected table names,
and resolves 'auto-latest' quarter against the DB. Returns a frozen PipelineConfig.

Interim implementation choice: frozen dataclasses + manual validation. Swap to
Pydantic v2 is a non-breaking refactor if desired later (see decisions.md).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv

from .paths import PROJECT_ROOT, REPO_ROOT, ensure_dir, resolve as resolve_path

_logger = logging.getLogger(__name__)

_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")
_DEFAULT_YAML = "config/pipeline.yaml"
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(Exception):
    """Validation failure. Message includes file path and, where possible, line."""


@dataclass(frozen=True)
class PathsConfig:
    fundparser_db: Path
    prices_db: Path
    outcomes_db: Path
    reports_dir: Path
    intermediate_outputs_dir: Path
    outputs_dir: Path
    logs_dir: Path
    fund_list_source: Path


@dataclass(frozen=True)
class DbSchemaConfig:
    funds_table: str
    filings_table: str
    holdings_table: str
    cusip_map_table: str


@dataclass(frozen=True)
class CacheConfig:
    ttl_days: int
    clear_on_debug_start: bool


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    file_rotation_days: int
    console: bool


@dataclass(frozen=True)
class DebugConfig:
    row_limit: int
    prompt_before_api_calls: bool


@dataclass(frozen=True)
class PipelineConfig:
    mode: Literal["debug", "production"]
    quarter: str
    paths: PathsConfig
    db_schema: DbSchemaConfig
    cache: CacheConfig
    logging: LoggingConfig
    debug: DebugConfig


# ---------- YAML load + validation helpers ----------

def _load_yaml_with_lines(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        if mark is not None:
            raise ConfigError(
                f"{path}:{mark.line + 1}:{mark.column + 1} YAML syntax error: {e}"
            ) from e
        raise ConfigError(f"{path}: YAML syntax error: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level YAML must be a mapping")
    return data


def _require(data: dict, key: str, file: Path, parent: str = "") -> Any:
    if key not in data:
        path = f"{parent}.{key}" if parent else key
        raise ConfigError(f"{file}: required field '{path}' is missing")
    return data[key]


def _require_type(value: Any, expected: type | tuple, field_path: str, file: Path) -> Any:
    if isinstance(expected, tuple):
        expected_names = "/".join(t.__name__ for t in expected)
    else:
        expected_names = expected.__name__
    # bool is a subclass of int in Python — disambiguate explicitly.
    if expected is int and isinstance(value, bool):
        raise ConfigError(
            f"{file}: field '{field_path}' expected {expected_names}, got bool"
        )
    if not isinstance(value, expected):
        raise ConfigError(
            f"{file}: field '{field_path}' expected {expected_names}, "
            f"got {type(value).__name__}"
        )
    return value


def _get_required_dict(data: dict, key: str, file: Path) -> dict:
    section = _require(data, key, file)
    return _require_type(section, dict, key, file)


def _get_str(section: dict, key: str, file: Path, parent: str) -> str:
    return _require_type(_require(section, key, file, parent), str, f"{parent}.{key}", file)


def _get_int(section: dict, key: str, file: Path, parent: str, *, minimum: int | None = None) -> int:
    value = _require_type(_require(section, key, file, parent), int, f"{parent}.{key}", file)
    if minimum is not None and value < minimum:
        raise ConfigError(
            f"{file}: field '{parent}.{key}' must be >= {minimum}, got {value}"
        )
    return value


def _get_bool(section: dict, key: str, file: Path, parent: str) -> bool:
    return _require_type(_require(section, key, file, parent), bool, f"{parent}.{key}", file)


# ---------- DB-side validation ----------

def _validate_db_schema(db_path: Path, schema: DbSchemaConfig, yaml_path: Path) -> None:
    expected = [
        schema.funds_table,
        schema.filings_table,
        schema.holdings_table,
        schema.cusip_map_table,
    ]
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        present = {r[0] for r in rows}
    finally:
        conn.close()
    missing = [t for t in expected if t not in present]
    if missing:
        raise ConfigError(
            f"{yaml_path}: db_schema tables missing from {db_path}: {missing}"
        )


def _resolve_auto_latest_quarter(db_path: Path, holdings_table: str) -> str:
    from .quarter import date_to_quarter
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            f"SELECT MAX(period_of_report) FROM {holdings_table}"
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        raise ConfigError(
            f"quarter=auto-latest but {holdings_table}.period_of_report is empty"
        )
    return date_to_quarter(row[0])


# ---------- main entry point ----------

@lru_cache(maxsize=None)
def load_config(
    pipeline_yaml_path: str | Path = _DEFAULT_YAML,
    project_root: Path | None = None,
) -> PipelineConfig:
    root = Path(project_root) if project_root else PROJECT_ROOT
    yaml_path = Path(pipeline_yaml_path)
    if not yaml_path.is_absolute():
        yaml_path = (root / yaml_path).resolve()

    data = _load_yaml_with_lines(yaml_path)

    pipeline_section = _get_required_dict(data, "pipeline", yaml_path)
    paths_section = _get_required_dict(data, "paths", yaml_path)
    db_schema_section = _get_required_dict(data, "db_schema", yaml_path)
    cache_section = _get_required_dict(data, "cache", yaml_path)
    logging_section = _get_required_dict(data, "logging", yaml_path)
    debug_section = _get_required_dict(data, "debug", yaml_path)

    # pipeline
    mode = _get_str(pipeline_section, "mode", yaml_path, "pipeline")
    if mode not in ("debug", "production"):
        raise ConfigError(
            f"{yaml_path}: pipeline.mode must be 'debug' or 'production', got '{mode}'"
        )
    quarter_raw = _get_str(pipeline_section, "quarter", yaml_path, "pipeline")
    if quarter_raw != "auto-latest" and not _QUARTER_RE.match(quarter_raw):
        raise ConfigError(
            f"{yaml_path}: pipeline.quarter must be 'auto-latest' or YYYYQn, got '{quarter_raw}'"
        )

    # paths
    def _path_field(key: str) -> Path:
        raw = _get_str(paths_section, key, yaml_path, "paths")
        return resolve_path(raw, root)

    paths = PathsConfig(
        fundparser_db=_path_field("fundparser_db"),
        prices_db=_path_field("prices_db"),
        outcomes_db=_path_field("outcomes_db"),
        reports_dir=_path_field("reports_dir"),
        intermediate_outputs_dir=_path_field("intermediate_outputs_dir"),
        outputs_dir=_path_field("outputs_dir"),
        logs_dir=_path_field("logs_dir"),
        fund_list_source=_path_field("fund_list_source"),
    )

    # ensure writable dirs exist
    for d in (
        paths.reports_dir,
        paths.intermediate_outputs_dir,
        paths.outputs_dir,
        paths.logs_dir,
    ):
        ensure_dir(d)

    # db_schema
    db_schema = DbSchemaConfig(
        funds_table=_get_str(db_schema_section, "funds_table", yaml_path, "db_schema"),
        filings_table=_get_str(db_schema_section, "filings_table", yaml_path, "db_schema"),
        holdings_table=_get_str(db_schema_section, "holdings_table", yaml_path, "db_schema"),
        cusip_map_table=_get_str(db_schema_section, "cusip_map_table", yaml_path, "db_schema"),
    )

    # cache
    cache = CacheConfig(
        ttl_days=_get_int(cache_section, "ttl_days", yaml_path, "cache", minimum=1),
        clear_on_debug_start=_get_bool(
            cache_section, "clear_on_debug_start", yaml_path, "cache"
        ),
    )

    # logging
    level = _get_str(logging_section, "level", yaml_path, "logging").upper()
    if level not in _VALID_LOG_LEVELS:
        raise ConfigError(
            f"{yaml_path}: logging.level must be one of "
            f"{sorted(_VALID_LOG_LEVELS)}, got '{level}'"
        )
    logging_conf = LoggingConfig(
        level=level,
        file_rotation_days=_get_int(
            logging_section, "file_rotation_days", yaml_path, "logging", minimum=1
        ),
        console=_get_bool(logging_section, "console", yaml_path, "logging"),
    )

    # debug
    debug_conf = DebugConfig(
        row_limit=_get_int(debug_section, "row_limit", yaml_path, "debug", minimum=1),
        prompt_before_api_calls=_get_bool(
            debug_section, "prompt_before_api_calls", yaml_path, "debug"
        ),
    )

    # Load repo-root .env (safe: read-only into process env; no file mutation).
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        _logger.warning(
            ".env not found at %s — API-dependent modules will fail at call time",
            env_path,
        )

    # DB schema check (only if DB exists — allows Module 1 to load before Module 2 is seeded).
    if paths.fundparser_db.exists():
        _validate_db_schema(paths.fundparser_db, db_schema, yaml_path)

    # Resolve auto-latest quarter.
    resolved_quarter = quarter_raw
    if quarter_raw == "auto-latest":
        if paths.fundparser_db.exists():
            resolved_quarter = _resolve_auto_latest_quarter(
                paths.fundparser_db, db_schema.holdings_table
            )
        else:
            raise ConfigError(
                f"{yaml_path}: quarter=auto-latest requires {paths.fundparser_db} to exist"
            )

    return PipelineConfig(
        mode=mode,
        quarter=resolved_quarter,
        paths=paths,
        db_schema=db_schema,
        cache=cache,
        logging=logging_conf,
        debug=debug_conf,
    )
