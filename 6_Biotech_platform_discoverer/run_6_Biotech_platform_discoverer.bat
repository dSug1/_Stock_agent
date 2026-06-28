@echo off
REM ============================================================
REM Acrivon-Pattern Listed-Biotech Screener — run + render.
REM Lives inside 6_Biotech_platform_discoverer/. The shared venv
REM is at the repo root (..\.venv\).
REM
REM Runs the pipeline (currently Stage 0a universe union -> Stage
REM 0b hard cuts) then renders the HTML funnel report and opens it.
REM More stages (1 TA-tag, 2 harvest, 3 embed, 4 Claude, 5 export)
REM are appended here as later milestones land.
REM
REM Flags pass through, e.g.:
REM   run_6_Biotech_platform_discoverer.bat --enrich-yf
REM ============================================================

setlocal EnableDelayedExpansion

REM --- Resolve project folder (folder this .bat lives in) -----
set "ROOT=%~dp0"
cd /d "%ROOT%"

REM --- Activate the shared venv at the repo root --------------
call "%ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    exit /b 1
)

REM --- Import root (cwd is already the project folder) --------
set "PYTHONPATH=src"

REM --- Ensure runtime dirs exist ------------------------------
if not exist "data" mkdir "data"
if not exist "Outputs" mkdir "Outputs"

REM --- Stage 0: universe union + hard cuts -------------------
REM Idempotent: re-running re-admits the union and re-applies the
REM only two allowed deletions (mktcap_out_of_band / not_live).
echo [6] Stage 0: assembling universe and applying hard cuts...
python scripts\6_screen.py --stage 0 %*
if errorlevel 1 (
    echo [FATAL] stage 0 failed.
    endlocal & exit /b 1
)

REM --- Render the HTML funnel report and open it -------------
echo [6] Rendering HTML report...
python scripts\6_render.py --open-browser
if errorlevel 1 (
    echo [FATAL] render failed.
    endlocal & exit /b 1
)

echo [6] done. Report: Outputs\screener_report.html
endlocal
