@echo off
REM ============================================================
REM Render the early-detection pipeline status + interactive digest
REM to a SINGLE self-contained HTML — WITHOUT re-running the pipeline.
REM
REM Reads data\early_detection.db as-is, writes Outputs\pipeline_status.html
REM (status dashboard + interactive digest), then OPENS it in your browser.
REM Supersedes the old digest.html (obsoleted). Refresh any time, no re-fetch.
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
if not exist "Outputs" mkdir "Outputs"

"%PY%" scripts\8_status.py %*
if errorlevel 1 (
    echo [FATAL] render failed — see the error above.
    pause
    endlocal & exit /b 1
)

echo [8] Pipeline status + digest: Outputs\pipeline_status.html  (opening...)
start "" "%ROOT%Outputs\pipeline_status.html"
endlocal
