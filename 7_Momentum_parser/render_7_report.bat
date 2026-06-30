@echo off
REM Render the latest momentum run to a self-contained HTML report and open it.
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"
call "%ROOT%..\.venv\Scripts\activate.bat" || (echo [FATAL] venv & exit /b 1)
set "PYTHONPATH=src"
python scripts\7_render.py --open-browser %*
endlocal
