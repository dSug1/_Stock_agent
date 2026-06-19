@echo off
REM ============================================================
REM 4_List_renderer — render-only entrypoint.
REM
REM Regenerates Outputs/list_results_data.js and opens the stable
REM Outputs/list_results.html template in your default browser.
REM
REM Default source = news: 3 articles each from FierceBiotech, Le Figaro
REM and CNBC (live RSS), displayed with the same template (adaptive
REM HTML display).
REM
REM No server is needed: the template loads its sidecar via a
REM relative <script> tag, which works over file://.
REM
REM Pass-through args override the defaults, e.g.:
REM   run_4_List_render.bat --top 5
REM   run_4_List_render.bat --source biopharm --top 10
REM   run_4_List_render.bat --source file --input data\sample_input.json
REM ============================================================
cd /d "%~dp0"

set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [FATAL] venv python not found at "%PYTHON%"
    pause
    exit /b 1
)

set "PYTHONPATH=src"

echo === Rendering Outputs\list_results.html + sidecar (news: 3 per site) ===
"%PYTHON%" scripts\4_render_list.py --source news --open-browser %*
if errorlevel 1 (
    echo [FATAL] render exited with an error.
    pause
    exit /b 1
)
