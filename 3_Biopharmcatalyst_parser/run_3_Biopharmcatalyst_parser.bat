@echo off
REM ============================================================
REM Biopharmcatalyst Parser orchestrator.
REM Lives inside 3_Biopharmcatalyst_parser/. Shared venv at ..\.venv\.
REM
REM Build order (per spec §9): funds-refresh -> M0a (docx->csv) -> M0b (db init)
REM   -> M1 -> M5 -> M4 -> M2 -> M3 -> M6.  Each module gated by y/N.
REM M0a + M0b always run (idempotent, free).
REM ============================================================

setlocal EnableDelayedExpansion

REM --- Resolve parser folder (this .bat's directory) ----------
set "PARSER_ROOT=%~dp0"
cd /d "%PARSER_ROOT%"

REM --- Activate the shared venv at the repo root --------------
call "%PARSER_ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    exit /b 1
)

REM --- Set import root (cwd is already 3_Biopharmcatalyst_parser\) -
set "PYTHONPATH=src"

REM --- Ensure runtime dirs exist ------------------------------
if not exist "data" mkdir "data"
if not exist "_csv_source\archive" mkdir "_csv_source\archive"
if not exist "_intermediate_outputs" mkdir "_intermediate_outputs"
if not exist "Outputs" mkdir "Outputs"
if not exist "logs" mkdir "logs"

REM ============================================================
REM Funds auto-refresh — runs 2_Funds_parser modules 2..5 + the
REM consensus_builds HTML if today is within ±7 days of a 13F filing
REM deadline (2/14, 5/15, 8/14, 11/14) OR if today is past the
REM most recent deadline window but the funds DB is still on a
REM previous quarter. Exits 0 (skip) when neither condition holds —
REM downstream pipeline continues either way.
REM ============================================================
echo.
echo === Funds auto-refresh check (13F calendar) ===
python scripts\3_auto_refresh_funds.py
if errorlevel 1 (
    echo [WARN] funds auto-refresh reported a failure; continuing with the
    echo        biopharmcatalyst pipeline. Re-run 2_Funds_parser manually if
    echo        the consensus_builds report is critical for this snapshot.
)

REM ============================================================
REM Module 0a — convert any new BPC catalyst .docx exports into the
REM CSV format M1 expects. Auto-skipped when each CSV is newer than
REM its sibling .docx; idempotent.
REM ============================================================
echo.
echo === Module 0a: converting any new _csv_source/*.docx -^> .csv ===
python scripts\3_0_convert_docx_to_csv.py
if errorlevel 1 (
    echo [WARN] docx-to-csv conversion reported a failure; continuing.
    echo        If M1 then fails because a CSV is missing or malformed,
    echo        re-run with --force to rebuild from the source docx.
)

REM ============================================================
REM Module 0b — initialize schema (always; idempotent)
REM ============================================================
echo.
echo === Module 0b: initializing data\biotech.db ===
python scripts\3_0_init_db.py
if errorlevel 1 (
    echo [FATAL] Module 0 failed.
    endlocal ^& exit /b 1
)

REM ============================================================
REM Module 1 — ingest BPC catalyst CSV
REM Default: most recent *catalyst*.csv in _csv_source/ at today's UTC snapshot date.
REM Override the snapshot date with --snapshot-date YYYY-MM-DD if back-loading.
REM ============================================================
echo.
set /p RUN_M1="Proceed to Module 1 (ingest BPC catalyst CSV from _csv_source/)? [y/N]: "
if /i "%RUN_M1%"=="y" (
    python scripts\3_1_ingest_catalysts.py
    if errorlevel 1 (
        echo [FATAL] Module 1 failed.
        endlocal ^& exit /b 1
    )
)

REM ============================================================
REM Module 5 — compute catalyst timing
REM Default: processes the most recent snapshot in catalyst_snapshots.
REM Use --all-snapshots after a RULES_VERSION bump (spec §7.12).
REM ============================================================
echo.
set /p RUN_M5="Proceed to Module 5 (compute catalyst timing)? [y/N]: "
if /i "%RUN_M5%"=="y" (
    python scripts\3_5_compute_timing.py
    if errorlevel 1 (
        echo [FATAL] Module 5 failed.
        endlocal ^& exit /b 1
    )
    REM --- Render HTML report (default Y) -------------------------
    echo.
    set /p RUN_RENDER="Render Outputs\catalyst_timings.html? [Y/n]: "
    if /i not "%RUN_RENDER%"=="n" (
        python scripts\3_5_render_timings.py
        if errorlevel 1 (
            echo [WARN] HTML render failed, continuing.
        )
    )
)

REM ============================================================
REM Module 4 — ingest BPC insider supplement CSV
REM Default: most recent *insider*.csv in _csv_source/ at today's UTC date.
REM Override the snapshot date with --snapshot-date YYYY-MM-DD if back-loading.
REM ============================================================
echo.
set /p RUN_M4="Proceed to Module 4 (ingest BPC insider supplement from _csv_source/)? [y/N]: "
if /i "%RUN_M4%"=="y" (
    python scripts\3_4_ingest_bpc_insider.py
    if errorlevel 1 (
        echo [FATAL] Module 4 failed.
        endlocal ^& exit /b 1
    )
)

REM ============================================================
REM Module 2 — EDGAR Form 4 ingest
REM Default: every ticker in the most recent catalyst snapshot,
REM 365-day lookback, incremental (skips accessions already in DB).
REM ============================================================
echo.
set /p RUN_M2="Proceed to Module 2 (EDGAR Form 4, ~9.5 req/sec to SEC)? [y/N]: "
if /i "%RUN_M2%"=="y" (
    python scripts\3_2_ingest_edgar_form4.py --lookback-days 365
    if errorlevel 1 (
        echo [FATAL] Module 2 failed.
        endlocal ^& exit /b 1
    )
)

REM ============================================================
REM Module 3 — EDGAR 13D/13G metadata
REM Lightweight: no XML parsing, just the submissions JSON walk.
REM Same 9.5 req/sec rate budget as M2; per-ticker incremental floor.
REM ============================================================
echo.
set /p RUN_M3="Proceed to Module 3 (EDGAR 13D/13G, ~9.5 req/sec to SEC)? [y/N]: "
if /i "%RUN_M3%"=="y" (
    python scripts\3_3_ingest_edgar_13dg.py --lookback-days 365
    if errorlevel 1 (
        echo [FATAL] Module 3 failed.
        endlocal ^& exit /b 1
    )
)

REM ============================================================
REM Module 6 — Score & rank (filters H1-H5 + insider/momentum/funds composite).
REM Reads catalyst_snapshots + catalyst_timing + v_executive_open_market_trades
REM and ATTACH-es 2_Funds_parser/2_fundparser.db for the funds signal.
REM Free (no API spend); ~1 second per snapshot.
REM ============================================================
echo.
set /p RUN_M6="Proceed to Module 6 (score catalysts; reads 2_Funds_parser DB)? [y/N]: "
if /i "%RUN_M6%"=="y" (
    python scripts\3_6_score_catalysts.py
    if errorlevel 1 (
        echo [FATAL] Module 6 failed.
        endlocal ^& exit /b 1
    )
    REM --- Render scored shortlist HTML (default Y) ---------------
    echo.
    set /p RUN_RENDER6="Render Outputs\catalyst_scores.html? [Y/n]: "
    if /i not "%RUN_RENDER6%"=="n" (
        python scripts\3_6_render_scores.py
        if errorlevel 1 (
            echo [WARN] HTML render failed, continuing.
        )
    )
)

echo.
echo === run_3_Biopharmcatalyst_parser complete ===
endlocal
