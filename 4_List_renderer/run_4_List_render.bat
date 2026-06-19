@echo off
REM ============================================================
REM 4_List_renderer — render-only entrypoint.
REM
REM Regenerates Outputs/list_results_data.js and opens the stable
REM Outputs/list_results.html template in your default browser.
REM
REM Default source = registry: renders the enabled source set on the board
REM from data\list_renderer.db (seeded on first run from config\sources.yaml),
REM all through the same template (adaptive HTML display).
REM
REM No server is needed: the template loads its sidecar via a
REM relative <script> tag, which works over file://.
REM
REM Pass-through args override the defaults, e.g.:
REM   run_4_List_render.bat --seed                    (re)seed sources.yaml
REM   run_4_List_render.bat --source news --top 5     single-source override
REM   run_4_List_render.bat --source biopharm --top 10
REM ============================================================
cd /d "%~dp0"

set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [FATAL] venv python not found at "%PYTHON%"
    pause
    exit /b 1
)

set "PYTHONPATH=src"

echo === Rendering Outputs\list_results.html + sidecar (registry: board source set) ===
"%PYTHON%" scripts\4_render_list.py --open-browser %*
if errorlevel 1 (
    echo [FATAL] render exited with an error.
    pause
    exit /b 1
)
