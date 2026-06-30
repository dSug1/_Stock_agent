@echo off
REM ============================================================
REM 7_Momentum_parser - DAILY pipeline (spec v0.3).
REM Order: 0a discover (weekly) -> 1 prices -> 0b gate -> 2 harvest
REM   -> 3 tiered Claude score (gated) -> 5 blend -> sec9 settle -> render.
REM Shared venv at the repo root (..\.venv\).
REM
REM Double-click / run with NO arguments  -> interactive menu (Dry or Production).
REM Pass flags (e.g. for Task Scheduler)   -> menu skipped, flags used as-is:
REM   run_7_Momentum_parser.bat --dispatch              (live, billed, capped $5/day)
REM   run_7_Momentum_parser.bat --no-fetch              (fast offline dry preview)
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

set "EXTRA=%*"
set "INTERACTIVE="
REM If the user passed any argument, honour it and skip the menu (scheduler / power-user path).
if not "%~1"=="" goto run

set "INTERACTIVE=1"
echo.
echo  ======================================================
echo    7_Momentum_parser  -  choose run mode
echo  ======================================================
echo    [D] Dry run      free preview: model-only, NO Claude call, NO spend
echo    [P] Production   LIVE: calls Claude, BILLED, capped at $5/day
echo.
set /p "MODE=Enter D or P [default D]: "
if /i not "!MODE!"=="P" goto run

echo.
echo  WARNING: a Production run calls the Claude API and may cost up to $5 (hard-capped).
echo  The first run is also slow (fetches prices + news for the whole basket; cached after).
set /p "CONFIRM=Type YES to proceed with a billed run: "
if /i "!CONFIRM!"=="YES" (
    set "EXTRA=--dispatch"
) else (
    echo  Cancelled production - running DRY instead.
    set "EXTRA="
)

:run
echo.
echo "%EXTRA%" | findstr /C:"--dispatch" >nul && (echo [7] PRODUCTION run ^(--dispatch, billed^)...) || (echo [7] DRY run ^(free preview, no spend^)...)
python scripts\7_momentum.py --daily %EXTRA%
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
    echo [7] done. Report: Outputs\momentum_report.html   Signals: Outputs\signals.md   Validation: Outputs\validation.md
) else (
    echo [7] run exited with error code %RC%.
)
if defined INTERACTIVE pause
endlocal & exit /b %RC%
