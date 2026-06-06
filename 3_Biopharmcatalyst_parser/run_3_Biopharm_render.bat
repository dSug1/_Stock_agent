@echo off
REM ============================================================
REM Biopharmcatalyst — render-only entrypoint (D25 + D29 + D38#3 + D39#1).
REM
REM Re-renders Outputs/catalyst_scores.html + sidecar from the EXISTING
REM databases. NO pipeline run, NO Anthropic call, NO BPC ingest.
REM
REM After render, starts the local HTTP server AND auto-launches your
REM default browser at the report URL.
REM
REM D39 follow-up #1 (2026-06-05) — adopted 0_Renderer/run.bat's pattern:
REM (a) invoke the venv's python.exe directly (skips activate.bat ~0.5s);
REM (b) one Python process via --render (skips a second startup ~0.2s).
REM ============================================================
cd /d "%~dp0"

set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [FATAL] venv python not found at "%PYTHON%"
    pause
    exit /b 1
)

set "PYTHONPATH=src"

echo === Re-rendering catalyst_scores.html + starting server ===
echo   URL: http://127.0.0.1:7034/catalyst_scores.html
echo   Live yfinance prices refresh every 60s during US market hours.
echo   Ctrl-C in this window to stop the server.
echo.

"%PYTHON%" scripts\3_7_serve_selection.py --port 7034 --open-browser --render
if errorlevel 1 (
    echo [FATAL] render/server exited with an error.
    pause
    exit /b 1
)
