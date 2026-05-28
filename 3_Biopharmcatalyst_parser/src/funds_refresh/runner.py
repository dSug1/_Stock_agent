"""Subprocess-based runner for 2_Funds_parser modules 0..5.

Called by ``scripts/3_auto_refresh_funds.py`` when
:func:`funds_refresh.decision.decide` says we should auto-refresh.
Mirrors the y/N-gated sequence inside ``run_2_Funds_parser.bat`` but
runs each step non-interactively. Stops at Module 5 (Module 6 calls the
Anthropic API and must remain user-gated).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# Order matters; each tuple is (label, script_relative_to_2FP_scripts, optional CLI args).
_STEPS: list[tuple[str, str, list[str]]] = [
    ("M2 ingest 13F-HR",         "2_ingest_13f.py",          []),
    ("M2 build report",          "2_build_report.py",        []),
    ("M3 build universe",        "3_build_universe.py",      ["-v"]),
    ("M4a hard filters",         "4_run_hard_filters.py",    ["-v"]),
    ("M4b ranking",              "4_rank.py",                ["-v"]),
    ("M4c fundamentals",         "4c_enrich_fundamentals.py", ["-v"]),
    ("M5 context packs",         "5_build_context_packs.py", ["-v"]),
]


@dataclass
class StepResult:
    label: str
    script: str
    returncode: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    fatal: bool = False


@dataclass
class RunSummary:
    steps: list[StepResult] = field(default_factory=list)
    consensus_html_path: Path | None = None
    aborted_at: str | None = None


def _funds_parser_root() -> Path:
    """3_Biopharmcatalyst_parser/ → its sibling 2_Funds_parser/."""
    here = Path(__file__).resolve()
    # src/funds_refresh/runner.py → 3_Biopharmcatalyst_parser/src/funds_refresh/runner.py
    project_root = here.parents[2]
    return project_root.parent / "2_Funds_parser"


def _venv_python() -> str:
    """The repo's shared .venv Python, same one that ran us."""
    return sys.executable


def latest_period_in_db(funds_db: Path | None = None) -> str | None:
    """Read 2_Funds_parser/2_fundparser.db::holdings → MAX(period_of_report).
    Returns None if DB is missing, empty, or unreadable.
    """
    if funds_db is None:
        funds_db = _funds_parser_root() / "2_fundparser.db"
    if not funds_db.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{funds_db}?mode=ro", uri=True)
        try:
            row = c.execute(
                "SELECT MAX(period_of_report) FROM holdings"
            ).fetchone()
            return row[0] if row and row[0] else None
        finally:
            c.close()
    except sqlite3.Error:
        return None


def _run_step(label: str, script_name: str, extra_args: list[str],
              funds_root: Path) -> StepResult:
    """Invoke one 2_Funds_parser script as a subprocess."""
    script = funds_root / "scripts" / script_name
    env = os.environ.copy()
    env["PYTHONPATH"] = str(funds_root / "src")

    cmd = [_venv_python(), str(script), *extra_args]
    log.info("[auto-funds] running: %s  (cwd=%s)", " ".join(cmd), funds_root)
    proc = subprocess.run(
        cmd,
        cwd=funds_root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail_lines = 20
    stdout_tail = "\n".join(proc.stdout.strip().splitlines()[-tail_lines:])
    stderr_tail = "\n".join(proc.stderr.strip().splitlines()[-tail_lines:])
    return StepResult(
        label=label,
        script=script_name,
        returncode=proc.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        fatal=proc.returncode != 0,
    )


def _run_consensus_report(funds_root: Path,
                          target_q: str, prev_q: str) -> StepResult:
    """Run _q1_consensus_report.py with --quarter/--prev-quarter."""
    script = funds_root / "scripts" / "_q1_consensus_report.py"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(funds_root / "src")
    cmd = [_venv_python(), str(script), "--quarter", target_q, "--prev-quarter", prev_q]
    log.info("[auto-funds] consensus report: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd, cwd=funds_root, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return StepResult(
        label="Consensus builds HTML",
        script="_q1_consensus_report.py",
        returncode=proc.returncode,
        stdout_tail="\n".join(proc.stdout.strip().splitlines()[-20:]),
        stderr_tail="\n".join(proc.stderr.strip().splitlines()[-20:]),
        fatal=proc.returncode != 0,
    )


def run_refresh(*, target_quarter_end: str, previous_quarter_end: str) -> RunSummary:
    """Run modules M2..M5 + consensus report. Stops on the first
    non-zero exit code from a step we deem essential (M2..M5). The
    consensus report is fail-open — its failure does not abort.

    Args
    ----
    target_quarter_end : str
        ISO date of the quarter we expect to be filing, e.g. ``"2026-03-31"``.
    previous_quarter_end : str
        ISO date of the prior quarter for the consensus delta report.
    """
    funds_root = _funds_parser_root()
    if not funds_root.exists():
        raise FileNotFoundError(
            f"Expected sibling project at {funds_root}; cannot auto-refresh."
        )
    summary = RunSummary()
    for label, script, args in _STEPS:
        result = _run_step(label, script, args, funds_root)
        summary.steps.append(result)
        if result.fatal:
            summary.aborted_at = label
            log.error("[auto-funds] step %r failed (exit %d); aborting refresh.",
                      label, result.returncode)
            return summary

    # Consensus report is best-effort.
    consensus = _run_consensus_report(funds_root, target_quarter_end, previous_quarter_end)
    summary.steps.append(consensus)
    if not consensus.fatal:
        # The script writes Outputs/<YYYYQn>_consensus_builds.html based on
        # the later (target) quarter — e.g. 2026Q1_consensus_builds.html.
        from .decision import quarter_label
        from datetime import date as _date
        q = quarter_label(_date.fromisoformat(target_quarter_end))
        candidate = funds_root / "Outputs" / f"{q}_consensus_builds.html"
        if candidate.exists():
            summary.consensus_html_path = candidate
    return summary
