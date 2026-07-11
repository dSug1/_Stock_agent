@echo off
REM ============================================================
REM Render the early-detection digest to HTML — WITHOUT re-running.
REM
REM Reads data\early_detection.db as-is and writes (then opens)
REM Outputs\digest.html (+ digest_data.js sidecar). Use this to
REM re-open or refresh the digest any time without re-fetching,
REM re-extracting, or re-scoring. (Rendering is decoupled from the run.)
REM
REM Flags pass through, e.g.:
REM   run_8_render.bat --no-open
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

python scripts\8_render.py %*
if errorlevel 1 (
    echo [FATAL] render failed. Has the pipeline scored anything yet? ^(scripts\8_score.py^)
    pause
    endlocal & exit /b 1
)

echo [8] Digest: Outputs\digest.html
endlocal
