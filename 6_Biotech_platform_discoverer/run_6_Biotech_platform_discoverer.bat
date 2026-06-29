@echo off
REM ============================================================
REM Acrivon-Pattern Listed-Biotech Screener — run + render.
REM Lives inside 6_Biotech_platform_discoverer/. The shared venv
REM is at the repo root (..\.venv\).
REM
REM Runs the pipeline (Stage 0a universe union -> Stage 0b hard
REM cuts -> Stage 5 rank/dedup/export) then renders the HTML report
REM and opens it. Stages 1 (TA-tag), 2 (harvest) and 4 (Claude
REM scoring, gated/costs money) are run on demand via 6_screen.py.
REM
REM Flags pass through, e.g.:
REM   run_6_Biotech_platform_discoverer.bat --enrich-yf
REM ============================================================

setlocal EnableDelayedExpansion

REM --- Resolve project folder (folder this .bat lives in) -----
set "ROOT=%~dp0"
cd /d "%ROOT%"

REM --- Activate the shared venv at the repo root --------------
call "%ROOT%..\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [FATAL] could not activate venv at ..\.venv\
    exit /b 1
)

REM --- Import root (cwd is already the project folder) --------
set "PYTHONPATH=src"

REM --- Ensure runtime dirs exist ------------------------------
if not exist "data" mkdir "data"
if not exist "Outputs" mkdir "Outputs"

REM --- Stage 0: universe union + hard cuts -------------------
REM --universe enumerates the FULL listed universe (SEC US by SIC +
REM Wikidata EU/Nordic) and PERSISTS it at Stage 0a in seconds.
REM --enrich-yf then fills market cap/liveness via yfinance at Stage
REM 0b (NETWORK-HEAVY: minutes; one call per ticker, bounded by
REM config stage0b.max_enrich). The universe is saved before
REM enrichment runs, so an interrupted enrich never loses it.
REM Fast seed-only run: drop --universe. Idempotent re-runs re-apply
REM the only two allowed deletions (mktcap_out_of_band / not_live).
echo [6] Stage 0: enumerating full universe + applying hard cuts...
python scripts\6_screen.py --stage 0 --universe --enrich-yf %*
if errorlevel 1 (
    echo [FATAL] stage 0 failed.
    endlocal & exit /b 1
)

REM --- Stage 5: rank + dedup + export the shortlist ----------
REM Free + idempotent (no API): re-ranks whatever Stage 4 has scored, collapses duplicate
REM company-ids into one row per real company, refreshes the review queue, and writes the
REM house-format shortlist to Outputs\shortlist.md. Scoring (Stage 4, costs money) is run
REM separately/gated; this just re-exports the current scores. Safe when nothing is scored yet.
echo [6] Stage 5: ranking + dedup + exporting shortlist...
python scripts\6_screen.py --stage 5
if errorlevel 1 (
    echo [FATAL] stage 5 failed.
    endlocal & exit /b 1
)

REM --- Seed-eval validation harness (spec §13) --------------
REM FREE + read-only: scores the labeled seed set through the funnel and reports precision/recall +
REM per-stage survival to Outputs\seed_eval.md. Flags loudly if a known positive was deleted at the
REM hard cut (a spec-level failure). The screen is not trusted until this passes.
echo [6] Seed-eval validation (precision/recall + survival)...
python scripts\6_eval.py
if errorlevel 1 (
    echo [WARN] seed-eval failed (non-fatal) — continuing.
)

REM --- Run summary / observability (spec §15) ---------------
REM FREE + read-only: per-run Markdown digest (funnel + tier breakdown + shortlist + top movers vs
REM last run + seed validation + cost) -> Outputs\run_<id>_summary.md + Outputs\run_summary.md.
echo [6] Writing run summary...
python scripts\6_summary.py
if errorlevel 1 (
    echo [WARN] run summary failed (non-fatal) — continuing.
)

REM --- Render the HTML report and open it --------------------
echo [6] Rendering HTML report...
python scripts\6_render.py --open-browser
if errorlevel 1 (
    echo [FATAL] render failed.
    endlocal & exit /b 1
)

echo [6] done. Report: Outputs\screener_report.html  Summary: Outputs\run_summary.md  Seed-eval: Outputs\seed_eval.md
endlocal
