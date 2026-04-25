@echo off
REM ============================================================
REM Funds Parser daily driver (stub).
REM Lives inside 2_Funds_parser/. The shared venv is at the repo
REM root (..\.venv\).
REM
REM Schedule in Windows Task Scheduler:
REM   Actions tab -> Start a program -> this .bat
REM   Start in:   ...\_Stock_agent\2_Funds_parser
REM   Run as the user who owns the venv.
REM ============================================================

setlocal

REM --- Resolve parser folder (folder this .bat lives in) ------
set "PARSER_ROOT=%~dp0"
cd /d "%PARSER_ROOT%"

REM --- Activate the shared venv at the repo root --------------
call "%PARSER_ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    exit /b 1
)

REM --- Set import root (cwd is already 2_Funds_parser\) -------
set "PYTHONPATH=src"

REM --- Ensure runtime dirs exist ------------------------------
if not exist "logs" mkdir "logs"
if not exist "Outputs" mkdir "Outputs"
if not exist "_intermediate_outputs" mkdir "_intermediate_outputs"
if not exist "data" mkdir "data"

REM --- Layer 1: ingest any new 13F-HR filings -----------------
REM Idempotent; existing filings are skipped without re-downloading.
echo [2_Funds_parser] Ingesting 13F-HR filings...
python scripts\2_ingest_13f.py
if errorlevel 1 (
    echo [WARN] ingest reported an error; continuing to report build.
)

REM --- Build the per-fund HTML report -------------------------
REM Skips the rewrite if no fund has a new latest filing.
echo [2_Funds_parser] Building fund report...
python scripts\2_build_report.py
if errorlevel 1 (
    echo [FATAL] report build failed.
    endlocal & exit /b 1
)

echo [2_Funds_parser] done. Open Outputs\2_funds_report.html

REM --- Module 3: build per-ticker universe (user-gated) -------
echo.
echo === Module 2 complete. ===
echo.
set /p RUN_M3="Proceed to Module 3 (build universe)? [y/N]: "
if /i "%RUN_M3%"=="y" (
    echo [2_Funds_parser] Module 3: building universe...
    python scripts\3_build_universe.py -v
    if errorlevel 1 (
        echo [FATAL] Module 3 failed.
        endlocal ^& exit /b 1
    )
    echo.
    set /p RUN_LIST="Run list_unresolved_cusips.py to surface ticker gaps? [y/N]: "
    if /i "%RUN_LIST%"=="y" (
        python scripts\list_unresolved_cusips.py
    )

    REM --- Module 4a: hard filters + snapshot fetch (user-gated) --
    echo.
    set /p RUN_M4A="Proceed to Module 4a (hard filters + snapshot fetch)? [y/N]: "
    if /i "%RUN_M4A%"=="y" (
        echo [2_Funds_parser] Module 4a: applying hard filters...
        python scripts\4_run_hard_filters.py -v
        if errorlevel 1 (
            echo [FATAL] Module 4a failed.
            endlocal ^& exit /b 1
        )

        REM --- Module 4b: price history + ranking (user-gated) ---
        echo.
        set /p RUN_M4B="Proceed to Module 4b (price history + archetype ranking)? [y/N]: "
        if /i "%RUN_M4B%"=="y" (
            echo [2_Funds_parser] Module 4b: fetching prices and ranking...
            python scripts\4_rank.py -v
            if errorlevel 1 (
                echo [FATAL] Module 4b failed.
                endlocal ^& exit /b 1
            )
            echo.
            echo [2_Funds_parser] Open Outputs\ranking_report_*.html

            REM --- Module 5: build context packs (user-gated) ---
            echo.
            set /p RUN_M5="Proceed to Module 5 (build context packs)? [y/N]: "
            if /i "%RUN_M5%"=="y" (
                echo [2_Funds_parser] Module 5: building context packs...
                python scripts\5_build_context_packs.py -v
                if errorlevel 1 (
                    echo [FATAL] Module 5 failed.
                    endlocal ^& exit /b 1
                )
                echo.
                echo [2_Funds_parser] Open Outputs\enrichment_report_*.html

                REM --- Module 6: LLM scoring (user-gated, paid API) ---
                echo.
                echo [2_Funds_parser] Module 6 will call the Anthropic API.
                echo                  You will be prompted to set D21/D27/D28/D29 gates,
                echo                  shown a cost estimate, and asked to approve dispatch.
                set /p RUN_M6_EST="Run cost estimator first (no API call, no charges)? [Y/n]: "
                if /i not "%RUN_M6_EST%"=="n" (
                    python scripts\6_estimate_cost.py
                    if errorlevel 1 (
                        echo [WARN] cost estimator failed; you can still run scoring.
                    )
                    echo.
                    echo [2_Funds_parser] Open Outputs\cost_estimate_*.html
                )
                echo.
                set /p RUN_M6="Proceed to Module 6 (LLM scoring, billed)? [y/N]: "
                if /i "%RUN_M6%"=="y" (
                    echo [2_Funds_parser] Module 6: dispatching to Anthropic...
                    python scripts\6_score.py -v
                    if errorlevel 1 (
                        echo [FATAL] Module 6 failed.
                        endlocal ^& exit /b 1
                    )
                    echo.
                    echo [2_Funds_parser] Open Outputs\final_ranking_*.html
                )
            )
        )
    )
)

endlocal & exit /b 0
