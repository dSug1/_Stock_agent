@echo off
REM ============================================================================
REM Hype Parser - FULL PIPELINE + render + open report.
REM   1) forward archive (OD-2): snapshot forward_only sources (self-skips if run
REM      within the last 7 days, so this is cheap to call every time)
REM   2) diffusion radar: ingest arXiv + GDELT + Wikipedia -> embed -> diffusion
REM      -> render _intermediate_outputs/radar_report.html -> open in browser
REM Zero Claude. First run hits the network; later runs use the SWR cache.
REM Pass extra radar flags through, e.g.:  run_5_Hype_parser.bat --refresh
REM To render WITHOUT running the pipeline, use run_5_Hype_render.bat instead.
REM ============================================================================
setlocal
cd /d "%~dp0"
set PYTHONPATH=src

echo [1/2] Forward archive (OD-2)...
"..\.venv\Scripts\python.exe" scripts\5_archive.py --run --if-stale-days 7 -v

echo.
echo [2/2] Diffusion radar pipeline + render...
"..\.venv\Scripts\python.exe" scripts\5_radar.py --open-browser -v %*
if errorlevel 1 (
    echo.
    echo Pipeline failed.
    pause
)
endlocal
