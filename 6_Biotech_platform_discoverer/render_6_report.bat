@echo off
REM ============================================================
REM Render the screener results to HTML — WITHOUT re-running.
REM
REM Reads data\store.db as-is and writes (then opens)
REM Outputs\screener_report.html. Use this to re-open or refresh
REM the report any time without re-harvesting or re-scoring.
REM (Reversibility principle: rendering is decoupled from the run.)
REM
REM Flags pass through, e.g.:
REM   render_6_report.bat --no-open
REM   render_6_report.bat --out Outputs\snapshot.html
REM ============================================================

setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

call "%ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    pause
    exit /b 1
)

set "PYTHONPATH=src"
if not exist "Outputs" mkdir "Outputs"

REM Opens the report by default; pass --no-open to just write it.
python scripts\6_render.py %*
if errorlevel 1 (
    echo [FATAL] render failed. Has the pipeline run yet? ^(run_6_Biotech_platform_discoverer.bat^)
    pause
    endlocal & exit /b 1
)

echo [6] Report: Outputs\screener_report.html
endlocal
