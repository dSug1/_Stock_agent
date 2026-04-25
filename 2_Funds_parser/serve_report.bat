@echo off
REM ============================================================
REM Module 6 selection editor — one-click launcher.
REM
REM Double-click this file (or run from a cmd window) to start
REM the local HTTP server that serves the latest final-ranking
REM HTML report and silently auto-saves your checkbox selection
REM to Outputs\final_ranking_<quarter>_selection.json.
REM
REM The browser opens automatically. Press CTRL+C in this window
REM when done editing.
REM
REM Pass extra flags through, e.g.:
REM   serve_report.bat --quarter 2025Q4
REM   serve_report.bat --port 5000
REM   serve_report.bat --no-browser
REM ============================================================

setlocal

REM --- Resolve parser folder (folder this .bat lives in) ------
set "PARSER_ROOT=%~dp0"
cd /d "%PARSER_ROOT%"

REM --- Activate the shared venv at the repo root --------------
call "%PARSER_ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    pause
    exit /b 1
)

REM --- Set import root (cwd is already 2_Funds_parser\) -------
set "PYTHONPATH=src"

REM --- Run the server; pass through any extra CLI args --------
python scripts\6_serve_report.py %*

REM --- Keep the window open so the user can see exit messages -
echo.
echo [serve_report] Server stopped. Press any key to close this window.
pause >nul

endlocal & exit /b 0
