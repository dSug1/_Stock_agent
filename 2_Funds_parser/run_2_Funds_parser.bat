@echo off
REM ============================================================
REM Funds Parser daily driver.
REM Lives inside 2_Funds_parser/. The shared venv is at the repo
REM root (..\.venv\).
REM
REM Schedule in Windows Task Scheduler:
REM   Actions tab -> Start a program -> this .bat
REM   Start in:   ...\_Stock_agent\2_Funds_parser
REM   Run as the user who owns the venv.
REM
REM NOTE ON STRUCTURE: this driver uses goto-label flow rather than
REM nested parenthesised if-blocks. cmd.exe parses an entire ( ... )
REM block up front, so a stray ) inside a REM comment or a set /p
REM prompt string (e.g. "(user-gated)") silently terminates the block
REM and crashes with ": was unexpected at this time." Goto flow also
REM lets each gate read its answer with delayed !VAR! expansion, which
REM percent-expansion inside a block cannot do.
REM ============================================================

setlocal EnableDelayedExpansion

REM --- Resolve parser folder (folder this .bat lives in) ------
set "PARSER_ROOT=%~dp0"
cd /d "%PARSER_ROOT%"

REM --- Activate the shared venv at the repo root --------------
call "%PARSER_ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 goto :fail_venv

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
if errorlevel 1 echo [WARN] ingest reported an error; continuing to report build.

REM --- Build the per-fund HTML report -------------------------
REM Skips the rewrite if no fund has a new latest filing.
echo [2_Funds_parser] Building fund report...
python scripts\2_build_report.py
if errorlevel 1 goto :fail_report

echo [2_Funds_parser] done. Open Outputs\2_funds_report.html

REM --- Module 3: build per-ticker universe (user-gated) -------
echo.
echo === Module 2 complete. ===
echo.
set "RUN_M3="
set /p RUN_M3=Proceed to Module 3 (build universe)? [y/N]:
if /i not "!RUN_M3!"=="y" goto :done

echo [2_Funds_parser] Module 3: building universe...
python scripts\3_build_universe.py -v
if errorlevel 1 goto :fail_m3

echo.
set "RUN_LIST="
set /p RUN_LIST=Run list_unresolved_cusips.py to surface ticker gaps? [y/N]:
if /i "!RUN_LIST!"=="y" python scripts\list_unresolved_cusips.py

REM --- Module 4a: hard filters + snapshot fetch (user-gated) --
echo.
set "RUN_M4A="
set /p RUN_M4A=Proceed to Module 4a [hard filters + snapshot fetch]? [y/N]:
if /i not "!RUN_M4A!"=="y" goto :done

echo [2_Funds_parser] Module 4a: applying hard filters...
python scripts\4_run_hard_filters.py -v
if errorlevel 1 goto :fail_m4a

REM --- Module 4b: price history + ranking (user-gated) --------
echo.
set "RUN_M4B="
set /p RUN_M4B=Proceed to Module 4b [price history + archetype ranking]? [y/N]:
if /i not "!RUN_M4B!"=="y" goto :done

echo [2_Funds_parser] Module 4b: fetching prices and ranking...
python scripts\4_rank.py -v
if errorlevel 1 goto :fail_m4b
echo.
echo [2_Funds_parser] Open Outputs\ranking_report_*.html

REM --- Module 4c: fundamentals enrichment (D54, free SEC EDGAR) ---
REM Default Y; skipping is harmless. M5 then emits packs without the
REM fundamentals block and M6 falls back to web_search.
echo.
set "RUN_M4C="
set /p RUN_M4C=Proceed to Module 4c [free SEC fundamentals enrichment]? [Y/n]:
if /i "!RUN_M4C!"=="n" goto :after_m4c
echo [2_Funds_parser] Module 4c: enriching fundamentals from SEC EDGAR...
python scripts\4c_enrich_fundamentals.py -v
if errorlevel 1 echo [WARN] Module 4c reported errors; continuing to M5.
:after_m4c

REM --- Module 5: build context packs (user-gated) ------------
echo.
set "RUN_M5="
set /p RUN_M5=Proceed to Module 5 [build context packs]? [y/N]:
if /i not "!RUN_M5!"=="y" goto :done

echo [2_Funds_parser] Module 5: building context packs...
python scripts\5_build_context_packs.py -v
if errorlevel 1 goto :fail_m5
echo.
echo [2_Funds_parser] Open Outputs\enrichment_report_*.html

REM --- Module 6: LLM scoring (user-gated, paid API) ----------
echo.
echo [2_Funds_parser] Module 6 will call the Anthropic API.
echo                  You will be prompted to set D21/D27/D28/D29 gates,
echo                  shown a cost estimate, and asked to approve dispatch.
set "RUN_M6_EST="
set /p RUN_M6_EST=Run cost estimator first [no API call, no charges]? [Y/n]:
if /i "!RUN_M6_EST!"=="n" goto :after_est
python scripts\6_estimate_cost.py
if errorlevel 1 echo [WARN] cost estimator failed; you can still run scoring.
echo.
echo [2_Funds_parser] Open Outputs\cost_estimate_*.html
:after_est

echo.
set "RUN_M6="
set /p RUN_M6=Proceed to Module 6 [LLM scoring, billed]? [y/N]:
if /i not "!RUN_M6!"=="y" goto :done

echo [2_Funds_parser] Module 6: dispatching to Anthropic...
python scripts\6_score.py -v
if errorlevel 1 goto :fail_m6
echo.
echo [2_Funds_parser] Final ranking written to Outputs\final_ranking_*.html
echo.

REM --- Module 7-alpha: forward-price collection (D55) --------
REM Snapshots are auto-captured by 6_score.py step 10. Here we just
REM sweep forward_prices for elapsed windows. Free, idempotent, fail-open.
echo [2_Funds_parser] Module 7-alpha: collecting forward prices...
python scripts\7_track_outcomes.py -v
if errorlevel 1 echo [WARN] Module 7 reported errors; continuing.

echo.
set "RUN_M6_EDIT="
set /p RUN_M6_EDIT=Open the selection editor [local HTTP server]? [y/N]:
if /i not "!RUN_M6_EDIT!"=="y" goto :done

REM D49 - local HTTP server + sidecar JSON. Serves the HTML and writes
REM Outputs\final_ranking_<quarter>_selection.json atomically on each
REM browser checkbox toggle. Press CTRL+C in the spawned window to stop.
echo [2_Funds_parser] Starting selection editor server.
echo                  Browser opens automatically. Press CTRL+C
echo                  in the server output to stop, then return here.
python scripts\6_serve_report.py

echo.
set "RUN_M6_RESCORE="
set /p RUN_M6_RESCORE=Re-score from your edited selection? [y/N]:
if /i not "!RUN_M6_RESCORE!"=="y" goto :done

set "M6_QUARTER="
for /f "usebackq tokens=*" %%Q in (`python -c "import sqlite3; c=sqlite3.connect(r'context_packs.db'); print(c.execute('SELECT quarter FROM context_packs GROUP BY quarter ORDER BY quarter DESC LIMIT 1').fetchone()[0])"`) do set "M6_QUARTER=%%Q"
if not defined M6_QUARTER goto :fail_quarter
echo [2_Funds_parser] Module 6 (selective): reading sidecar for !M6_QUARTER!
python scripts\6_score.py --selection-from-html "Outputs\final_ranking_!M6_QUARTER!.html" -v
if errorlevel 1 echo [WARN] Module 6 selective re-score failed; original report is unchanged.
goto :done

REM ============================================================
REM Exit points
REM ============================================================
:fail_venv
echo [FATAL] could not activate venv at ..\.venv\
exit /b 1

:fail_report
echo [FATAL] report build failed.
exit /b 1

:fail_m3
echo [FATAL] Module 3 failed.
exit /b 1

:fail_m4a
echo [FATAL] Module 4a failed.
exit /b 1

:fail_m4b
echo [FATAL] Module 4b failed.
exit /b 1

:fail_m5
echo [FATAL] Module 5 failed.
exit /b 1

:fail_m6
echo [FATAL] Module 6 failed.
exit /b 1

:fail_quarter
echo [WARN] could not resolve latest quarter from context_packs.db; skipping re-score.
goto :done

:done
echo.
echo [2_Funds_parser] Pipeline run complete.
exit /b 0
