"""Module 8 — §9 validation harness CLI. Back-test conviction output vs a labeled set of known cases.

ZERO-SPEND: reads only what the pipeline has already scored (no Claude calls, no network). Joins
`validation/known_cases.yaml` against the store, then reports the funnel (survivorship), classification
metrics (precision/recall/F1 over the scored subset), and a conviction-score threshold sweep.

Run (from the component dir):
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_validate.py                 # console summary + report
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_validate.py --rule score --threshold 65
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_validate.py --json Outputs\validation.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from early_detection.config import COMPONENT_ROOT, load_config
from early_detection.store import Store
from early_detection.validation import evaluate, load_cases, write_report

_CASES = COMPONENT_ROOT / "validation" / "known_cases.yaml"
_REPORT = COMPONENT_ROOT / "Outputs" / "validation_report.md"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Module 8 §9 validation harness (zero-spend back-test).")
    ap.add_argument("--cases", default=str(_CASES), help="labeled ground-truth YAML")
    ap.add_argument("--rule", choices=("flag", "score"), default="flag",
                    help="decision rule: 'flag' (deep-dive-candidate ⇒ positive) or 'score' (>= threshold)")
    ap.add_argument("--threshold", type=float, default=70.0, help="conviction_score cutoff for --rule score")
    ap.add_argument("--report", default=str(_REPORT), help="Markdown report output path")
    ap.add_argument("--json", default=None, help="also write the full report as JSON to this path")
    ap.add_argument("--no-report", action="store_true", help="print the console summary only, write nothing")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    store = Store(cfg.db_path)

    cases = load_cases(args.cases)
    rep = evaluate(store, cfg, cases, rule=args.rule, threshold=args.threshold)
    store.close()

    m = rep.classification
    print(f"§9 validation — prompt {rep.prompt_version} · rule={rep.rule}"
          f"{f' (≥{rep.threshold:.0f})' if rep.rule == 'score' else ''}")
    print(f"  labeled cases:       {len(rep.evals)}  ({rep.total_positives}+ / {rep.total_negatives}-)")
    print(f"  matched to store:    {rep.matched}/{len(rep.evals)}")
    print(f"  scored subset:       {rep.scored_positives}+ / {rep.scored_negatives}-")
    print(f"  funnel recall (E2E): {_pct(rep.funnel_recall)} "
          f"({rep.scored_positives}/{rep.total_positives} positives scored)")
    print(f"  precision / recall:  {_pct(m.get('precision'))} / {_pct(m.get('recall'))}   "
          f"F1 {_pct(m.get('f1'))}   (TP={m.get('tp',0)} FP={m.get('fp',0)} "
          f"FN={m.get('fn',0)} TN={m.get('tn',0)})")
    if not rep.sufficient:
        print(f"  ⚠️  INSUFFICIENT DATA — need ≥3 positive / ≥2 negative SCORED cases to trust the "
              f"metrics. Score more of the labeled universe (see report work-list).")

    if not args.no_report:
        write_report(rep, args.report)
        print(f"\n  report → {args.report}")
        if args.json:
            from pathlib import Path
            p = Path(args.json)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(rep.to_dict(), indent=2), encoding="utf-8")
            print(f"  json   → {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
