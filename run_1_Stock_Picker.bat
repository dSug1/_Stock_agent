@echo off
REM ============================================================
REM Stock Picker daily driver.
REM Schedule in Windows Task Scheduler (Task -> Actions -> Start
REM a program -> this .bat). Run as the user who owns the venv.
REM Current working directory for the task must be this folder
REM (_Stock_agent/) so relative paths below resolve.
REM
REM Cadence (Step 1.2 scope):
REM   * Every day       heartbeat log entry only.
REM   * Every Saturday  ingest 13F-HR filings + recompute TWOS.
REM Scheduler suggestion: daily trigger at 07:00 local time.
REM The Saturday branch runs when weekday() == 5 (see
REM scripts\daily_orchestrator.py INGEST_WEEKDAY).
REM ============================================================

setlocal

REM --- Resolve repo root (folder this .bat lives in) ----------
set "REPO_ROOT=%~dp0"
cd /d "%REPO_ROOT%"

REM --- Activate the shared venv -------------------------------
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at .venv\
    exit /b 1
)

REM --- Step into the picker and set import root ---------------
cd /d "%REPO_ROOT%1_Stock_Picker"
set "PYTHONPATH=src"

REM --- Ensure log dir exists ----------------------------------
if not exist "logs" mkdir "logs"

REM --- Hand off to the Python orchestrator --------------------
REM It decides, based on today's date, whether to run 13F
REM ingest + TWOS recompute, or just heartbeat.
python scripts\daily_orchestrator.py
set "ORCH_EXIT=%ERRORLEVEL%"

endlocal & exit /b %ORCH_EXIT%
