@echo off
REM ============================================================
REM Daily, credit-budget-safe OpenAlex run (literature + independence).
REM
REM OpenAlex now enforces a ~1000-credit / $0.10 daily quota (author search
REM = 10 credits, works/citation page = 1). This runner probes the budget,
REM runs the literature + independence passes in ONE process (shared budget),
REM and STOPS before the quota is gone — leaving the rest of the backlog
REM UNSTAMPED so tomorrow's run resumes losslessly. Zero LLM spend.
REM
REM Safe to schedule daily; drains the founder backlog a window at a time.
REM ============================================================

setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

REM Call the venv python directly (more robust than activate.bat).
set "PY=%ROOT%..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [FATAL] venv python not found at %PY%
    echo         expected a shared venv at ..\.venv\ ^(repo root^).
    pause
    endlocal & exit /b 1
)

set "PYTHONPATH=src"
"%PY%" scripts\8_openalex_daily.py %*
endlocal
