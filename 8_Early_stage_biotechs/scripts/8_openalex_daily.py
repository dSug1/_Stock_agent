"""Module 8 — daily, credit-budget-safe OpenAlex run.

Why this exists: as of mid-2026 OpenAlex enforces a CREDIT/USD quota on top of its per-second rate
limit — each response carries ``X-RateLimit-Remaining`` (credits left, ~1000/window), ``-Reset``
(seconds to reset, ~daily) and ``-Remaining-USD`` (~$0.10). Cost is per-endpoint: an author search
costs ~10 credits, a works/citation page ~1 — so the free tier is ~100 author searches/day. A naive
sweep of the full founder backlog blows that budget and gets hard-429'd, wasting retry/backoff time.

What it does: probe the remaining budget, then run the literature signal and (unless ``--no-independence``)
the §5.2 independence refinement IN ONE PROCESS, so the shared ``openalex.CREDITS`` tracker carries the
budget across both passes. Each pass STOPS before starting a founder it can't afford to finish
(``config.openalex_credit_reserve``), leaving the rest UNSTAMPED so tomorrow's run resumes losslessly.
Per-founder persistence + the anti-poisoning "None-on-throttle" contract mean a mid-run 429 never
discards completed work. Zero LLM spend; the only budget consumed is OpenAlex credits.

Run daily (from the component dir), e.g. from the scheduled 18:00 runner:
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_openalex_daily.py
    ... --limit N          cap founders attempted this run (also bounded by the credit reserve)
    ... --no-independence  literature pass only

Scoring is NOT run here (it is a paid Claude step, operator-gated): when the backlog is drained this
script prints the ``8_score.py --force`` command to fold the new citation evidence into conviction.
"""

from __future__ import annotations

import argparse
import logging
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from early_detection.clients import openalex
from early_detection.config import load_config
from early_detection.signals.independence import refine_independence
from early_detection.signals.literature import ingest_literature
from early_detection.store import Store


def _fmt_budget(st: dict) -> str:
    if st.get("remaining") is None:
        return "unknown (no response observed yet)"
    hrs = (st["reset_s"] / 3600.0) if st.get("reset_s") else None
    reset = f", resets in ~{hrs:.1f}h" if hrs is not None else ""
    return f"{st['remaining']}/{st.get('limit')} credits (${st.get('remaining_usd')} USD{reset})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily credit-budget-safe OpenAlex run (literature + §5.2).")
    ap.add_argument("--limit", type=int, default=None, help="cap founders attempted (also budget-bounded)")
    ap.add_argument("--no-independence", action="store_true", help="run the literature pass only")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    store = Store(cfg.db_path)
    mailto = cfg.openalex_mailto or ""

    # 1) Probe the budget with one cheap (~1-credit) call before committing to the expensive sweep.
    print("Probing OpenAlex credit budget…")
    st = openalex.probe_credits(mailto)
    print(f"  budget: {_fmt_budget(st)}")
    if openalex.CREDITS.exhausted(cfg.openalex_credit_reserve):
        hrs = (st.get("reset_s") or 0) / 3600.0
        print(f"\n  Budget at/below the {cfg.openalex_credit_reserve}-credit reserve — nothing run.")
        print(f"  Re-run after the window resets (~{hrs:.1f}h). No work lost; the backlog is untouched.")
        store.close()
        return 0

    # 2) Literature pass (author resolution + citations) — the expensive author searches.
    print("\n[1/2] literature/citation signal (OpenAlex, founder-keyed)…")
    lit = ingest_literature(store, cfg, limit=args.limit)
    print(f"  founders processed:    {lit.processed}/{lit.founders}")
    print(f"  authors resolved:      {lit.authors_resolved}  (free Crossref/ORCID fallback: {lit.authors_resolved_fallback})")
    print(f"  independent citations: {lit.independent_citations}")
    print(f"  budget now: {_fmt_budget(openalex.CREDITS.status())}")
    if lit.stopped_early:
        print(f"  ⚠ stopped on reserve — {lit.budget_left} founders left for the next window.")

    # 3) Independence refinement — only if budget remains and not disabled. Same process → same budget.
    if not args.no_independence and not openalex.CREDITS.exhausted(cfg.openalex_credit_reserve):
        print("\n[2/2] §5.2 independence refinement (co-authorship graph)…")
        ind = refine_independence(store, cfg, limit=args.limit)
        print(f"  founders refined:      {ind.refined}/{ind.founders}")
        print(f"  by relationship:       {ind.by_relationship}")
        print(f"  budget now: {_fmt_budget(openalex.CREDITS.status())}")
        if ind.stopped_early:
            print(f"  ⚠ stopped on reserve — {ind.budget_left} founders left for the next window.")
    elif args.no_independence:
        print("\n[2/2] independence refinement skipped (--no-independence).")
    else:
        print("\n[2/2] independence refinement skipped — credit reserve reached in the literature pass.")

    # 4) Backlog report so the operator knows whether another daily window is needed.
    lit_left = len(store.founders_for_literature(only_missing=True))
    ind_left = len(store.founders_for_independence(only_missing=True))
    print("\nBacklog after this run:")
    print(f"  founders awaiting literature resolution: {lit_left}")
    print(f"  resolved founders awaiting independence: {ind_left}")
    if lit_left or ind_left:
        hrs = (openalex.CREDITS.reset or 0) / 3600.0
        print(f"  → re-run this daily pass after the budget resets (~{hrs:.1f}h) to drain the backlog.")
    print("\nWhen the backlog is drained, re-score to fold new evidence into conviction:")
    print("  PYTHONPATH=src ..\\.venv\\Scripts\\python.exe scripts\\8_score.py --force")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
