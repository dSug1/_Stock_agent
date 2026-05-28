@echo off
REM ============================================================
REM Biopharmcatalyst — render-only entrypoint (D25).
REM
REM Re-renders Outputs/catalyst_scores.html + sidecar from the EXISTING
REM databases. NO pipeline run, NO Anthropic call, NO BPC ingest.
REM
REM After render, starts the local HTTP server so the page can poll
REM /api/live_price every 60s during market hours.
REM
REM Use this when you want a fresh view of cached deep-dive data with
REM intraday-refreshed yfinance prices.
REM ============================================================

setlocal EnableDelayedExpansion

set "PARSER_ROOT=%~dp0"
cd /d "%PARSER_ROOT%"

REM --- Activate the shared venv -------------------------------
call "%PARSER_ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    exit /b 1
)

set "PYTHONPATH=src"

echo.
echo === Re-rendering catalyst_scores.html ===
python scripts\3_6_render_scores.py
if errorlevel 1 (
    echo [FATAL] renderer failed.
    exit /b 1
)

echo.
echo === Starting local server for /api/live_price + selection sidecar ===
echo.
echo   Open http://127.0.0.1:7034/catalyst_scores.html in your browser.
echo   Live yfinance prices refresh every 60s during US market hours.
echo   Ctrl-C to stop.
echo.

python scripts\3_7_serve_selection.py --port 7034
