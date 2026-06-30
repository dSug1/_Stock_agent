"""Stage 4 — rank the run's predictions and export the house-format Markdown signal list (FREE)."""

from __future__ import annotations

import json
from pathlib import Path

from .config import outputs_dir
from .store import Store


def run(store: Store, cfg: dict, run_id: str, out: str | None = None, log=print) -> Path:
    rows = store.predictions_for_run(run_id)
    min_conf = float(cfg.get("export", {}).get("min_confidence", 0.0))
    min_p = float(cfg.get("export", {}).get("min_p_up", 0.0))
    rows = [r for r in rows if r["confidence"] >= min_conf and r["p_up"] >= min_p]

    path = Path(out) if out else outputs_dir(cfg) / "signals.md"
    horizon = rows[0]["horizon_days"] if rows else cfg.get("probability", {}).get("horizon_days", 5)
    lines = [
        f"# Momentum signals — run {run_id}  (LONG-ONLY · **INDICATIVE**)",
        "",
        f"_{len(rows)} ranked tickers · horizon {horizon} trading days · "
        "P(up) = P(forward-5d return > +0.5×σ_week) = blended **p_final** (w·p_claude + (1−w)·p_model). "
        "Indicative until the §9 backtest calibrates the blend._",
        "",
        "| Rank | Ticker | p_final | p_claude | p_model | Δ | Exp.ret | Conf. | Review | Fired signals |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    def _pct(x):
        return f"{x:.0%}" if x is not None else "—"

    for i, r in enumerate(rows, 1):
        fired = ", ".join(s["name"] for s in json.loads(r["signals_json"] or "[]") if s.get("fired")) or "—"
        d = r["disagreement"]
        lines.append(
            f"| {i} | {r['ticker']} | {r['p_up']:.0%} | {_pct(r['p_claude'])} | {_pct(r['p_model'])} | "
            f"{(f'{d:.2f}' if d is not None else '—')} | {r['expected_return']:+.2%} | "
            f"{r['confidence']:.2f} | {'⚠' if r['review'] else ''} | {fired} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"  [export] {len(rows)} rows -> {path}")
    return path
