@echo off
REM ============================================================
REM Stock Picker daily driver.
REM Lives inside 1_Stock_Picker/ (moved here from repo root so
REM the whole picker project is self-contained). The shared
REM venv is still at the repo root (..\.venv\).
REM
REM Schedule in Windows Task Scheduler:
REM   Actions tab -> Start a program -> this .bat
REM   Start in:   ...\_Stock_agent\1_Stock_Picker
REM   Run as the user who owns the venv.
REM
REM Cadence (Step 1.2 scope):
REM   * Every day       heartbeat log entry only.
REM   * Every Saturday  ingest 13F-HR filings + recompute TWOS.
REM Scheduler suggestion: daily trigger at 18:00 local time.
REM The Saturday branch runs when weekday() == 5 (see
REM scripts\daily_orchestrator.py INGEST_WEEKDAY).
REM ============================================================

setlocal

REM --- Resolve picker folder (folder this .bat lives in) ------
set "PICKER_ROOT=%~dp0"
cd /d "%PICKER_ROOT%"

REM --- Activate the shared venv at the repo root --------------
call "%PICKER_ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    exit /b 1
)

REM --- Set import root (cwd is already 1_Stock_Picker\) -------
set "PYTHONPATH=src"

REM --- Ensure log dir exists ----------------------------------
if not exist "logs" mkdir "logs"

REM --- Hand off to the Python orchestrator --------------------
REM It decides, based on today's date, whether to run 13F
REM ingest + TWOS recompute, or just heartbeat.
python scripts\daily_orchestrator.py
set "ORCH_EXIT=%ERRORLEVEL%"

endlocal & exit /b %ORCH_EXIT%
