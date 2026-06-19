@echo off
REM ============================================================
REM 4_List_renderer — live board server (M7, Phase 4).
REM
REM Starts the local Curator server on http://127.0.0.1:8765 and opens
REM the browser. Unlike run_4_List_render.bat (one-shot sidecar write),
REM this serves a LIVE board: the page fetches GET /api/board, and your
REM like / hide / dwell / click signals POST to /api/interact and persist
REM to data\list_renderer.db (the learning log for ranking, Phase 5).
REM
REM localhost only (single-user, trusted) — no sign-in. Ctrl-C to stop.
REM
REM Pass-through args, e.g.:
REM   run_4_List_server.bat --port 9000
REM   run_4_List_server.bat --seed        (re)seed sources.yaml first
REM   run_4_List_server.bat --no-open
REM ============================================================
cd /d "%~dp0"

set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [FATAL] venv python not found at "%PYTHON%"
    pause
    exit /b 1
)

set "PYTHONPATH=src"

echo === Curator board server -> http://127.0.0.1:8765/  (Ctrl-C to stop) ===
"%PYTHON%" scripts\4_serve.py %*
if errorlevel 1 (
    echo [FATAL] server exited with an error.
    pause
    exit /b 1
)
