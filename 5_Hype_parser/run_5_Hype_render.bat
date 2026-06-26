@echo off
REM ============================================================================
REM Hype Parser - RENDER ONLY (no pipeline, no network).
REM Rebuilds _intermediate_outputs/radar_report.html from the cached data in
REM data/hype.db and opens it. Use this to re-view / re-style the report without
REM re-running ingestion. To (re)run the pipeline, use run_5_Hype_parser.bat.
REM ============================================================================
setlocal
cd /d "%~dp0"
set PYTHONPATH=src
"..\.venv\Scripts\python.exe" scripts\5_radar.py --render-only --open-browser -v %*
if errorlevel 1 (
    echo.
    echo Render failed.
    pause
)
endlocal
