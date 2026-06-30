@echo off
REM ============================================================
REM 7_Momentum_parser — DAILY pipeline (spec v0.3).
REM Order: 0a discover (weekly) -> 1 prices -> 0b gate -> 2 harvest
REM   -> 3 tiered Claude score (gated) -> 5 blend -> §9 settle -> render.
REM Shared venv at the repo root (..\.venv\).
REM
REM DRY by default (no spend). The scheduled LIVE run passes --dispatch
REM (protected by claude.cost.max_usd_per_run = $5/day):
REM   run_7_Momentum_parser.bat --dispatch
REM Other flags pass through (e.g. --no-fetch, --tickers).
REM ============================================================

setlocal EnableDelayedExpansion
set "ROOT=%~dp0"
cd /d "%ROOT%"

call "%ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    exit /b 1
)

set "PYTHONPATH=src"
if not exist "data" mkdir "data"
if not exist "Outputs" mkdir "Outputs"

echo [7] DAILY momentum pipeline (pass --dispatch for the live, billed run)...
python scripts\7_momentum.py --daily %*
if errorlevel 1 (
    echo [FATAL] daily run failed.
    endlocal & exit /b 1
)

echo [7] done. Signals: Outputs\signals.md  Report: Outputs\momentum_report.html  Validation: Outputs\validation.md
endlocal
