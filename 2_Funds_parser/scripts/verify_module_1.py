"""Smoke test for Module 1. Loads config, logs a few lines, prints resolved state.

Usage:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/verify_module_1.py
"""
from __future__ import annotations

import sys

from module_1 import ConfigError, get_logger, load_config


def main() -> int:
    try:
        config = load_config()
    except (ConfigError, FileNotFoundError) as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    log = get_logger("verify_module_1", config)
    log.info("Module 1 loaded.")
    log.debug("Debug line (visible only if mode=debug).")

    print("--- Resolved config ---")
    print(f"mode:     {config.mode}")
    print(f"quarter:  {config.quarter}")
    print(f"db:       {config.paths.fundparser_db}")
    print(f"reports:  {config.paths.reports_dir}")
    print(f"interim:  {config.paths.intermediate_outputs_dir}")
    print(f"outputs:  {config.paths.outputs_dir}")
    print(f"logs:     {config.paths.logs_dir}")
    print(f"tables:   {config.db_schema}")
    print(f"cache:    ttl={config.cache.ttl_days}d, "
          f"clear_on_debug_start={config.cache.clear_on_debug_start}")
    print(f"debug:    row_limit={config.debug.row_limit}, "
          f"prompt_api={config.debug.prompt_before_api_calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
