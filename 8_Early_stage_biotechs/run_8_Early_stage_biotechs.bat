@echo off
REM Module 8 — Early-stage biotechs: Phase-1 universe build (US + Canada + Module-6 seed).
REM Nothing here spends money (no Claude). Requires USER_AGENT (name + email) in the repo-root .env
REM for SEC fair-access compliance, or the EDGAR enumeration returns empty.

setlocal
cd /d "%~dp0"
set PYTHONPATH=src

echo ============================================================
echo   Module 8  -  Phase 1 universe builder
echo ============================================================

"..\.venv\Scripts\python.exe" scripts\8_universe.py --verbose %*

echo.
echo Done. Stats:
"..\.venv\Scripts\python.exe" scripts\8_universe.py --stats

echo.
echo Rendering pipeline status + interactive digest...
call "%~dp0run_8_render.bat"

endlocal
