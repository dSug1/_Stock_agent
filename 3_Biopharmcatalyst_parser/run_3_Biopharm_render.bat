@echo off
REM ============================================================
REM Biopharmcatalyst — render-only entrypoint (D25 + D29).
REM
REM Re-renders Outputs/catalyst_scores.html + sidecar from the EXISTING
REM databases. NO pipeline run, NO Anthropic call, NO BPC ingest.
REM
REM After render, starts the local HTTP server AND auto-launches your
REM default browser at the report URL.
REM ============================================================

setlocal EnableDelayedExpansion

set "PARSER_ROOT=%~dp0"
cd /d "%PARSER_ROOT%"

REM --- Activate the shared venv -------------------------------
if not exist "%PARSER_ROOT%..\.venv\Scripts\activate.bat" (
    echo [FATAL] venv not found at %PARSER_ROOT%..\.venv\
    echo         Expected ..\.venv\Scripts\activate.bat
    pause
    exit /b 1
)
call "%PARSER_ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    pause
    exit /b 1
)

set "PYTHONPATH=src"

echo.
echo === Re-rendering catalyst_scores.html ===
python scripts\3_6_render_scores.py
if errorlevel 1 (
    echo [FATAL] renderer failed.
    pause
    exit /b 1
)

echo.
echo === Starting local server + auto-launching browser ===
echo   URL: http://127.0.0.1:7034/catalyst_scores.html
echo   Live yfinance prices refresh every 60s during US market hours.
echo   Ctrl-C in this window to stop the server.
echo.

python scripts\3_7_serve_selection.py --port 7034 --open-browser
if errorlevel 1 (
    echo [FATAL] server exited with an error.
    pause
    exit /b 1
)
