@echo off
REM ============================================================
REM 4_List_renderer — render-only entrypoint.
REM
REM Regenerates Outputs/list_results_data.js from the input JSON
REM (default: data/sample_input.json) and opens the stable
REM Outputs/list_results.html template in your default browser.
REM
REM No server is needed: the template loads its sidecar via a
REM relative <script> tag, which works over file://.
REM
REM Pass-through args go to the script, e.g.:
REM   run_4_List_render.bat --input data\my_results.json
REM ============================================================
cd /d "%~dp0"

set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [FATAL] venv python not found at "%PYTHON%"
    pause
    exit /b 1
)

set "PYTHONPATH=src"

echo === Rendering Outputs\list_results.html + sidecar ===
"%PYTHON%" scripts\4_render_list.py --open-browser %*
if errorlevel 1 (
    echo [FATAL] render exited with an error.
    pause
    exit /b 1
)
