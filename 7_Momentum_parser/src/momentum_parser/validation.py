"""Forward ledger settle pass + report (spec §9.2, Decision E part b).

Each day, any open `ledger` row whose 5-trading-day horizon has now elapsed gets its **realized** outcome:
the forward return is computed from cached bars, labelled with the SAME vol-normalized dead-band
(`targets.label_move`, using the σ_week stored at prediction time), and written back via
`store.settle_ledger`. The settled rows are the module's live track record — scored with `metrics`.
Pure-ish (no network). INDICATIVE / underpowered until enough non-overlapping outcomes accrue (n≥100).
"""

from __future__ import annotations

from pathlib import Path

from . import metrics, targets
from .config import outputs_dir
from .store import Store


def settle_pass(store: Store, cfg: dict, log=print) -> dict:
    prob = cfg.get("probability", {})
    horizon = int(prob.get("horizon_days", 5))
    band = float(prob.get("target", {}).get("vol_band_mult", 0.5))

    settled = still_open = missing = 0
    cache: dict = {}
    for row in store.open_ledger():
        t = row["ticker"]
        if t not in cache:
            bars = store.get_bars(t)
            cache[t] = (bars, {b.date: i for i, b in enumerate(bars)})
        bars, idxmap = cache[t]
        i = idxmap.get(row["asof"])
        if i is None:
            missing += 1
            continue
        if i + horizon < len(bars):
            base = bars[i].close
            realized = (bars[i + horizon].close / base - 1.0) if base else 0.0
            label = targets.label_move(realized, row["sigma_week"], band)
            store.settle_ledger(t, row["asof"], row["run_id"], realized, label, bars[i + horizon].date)
            settled += 1
        else:
            still_open += 1
    log(f"  [settle] settled={settled} still_open={still_open} missing_bars={missing}")
    return {"settled": settled, "still_open": still_open, "missing_bars": missing}


def build_report(store: Store, cfg: dict) -> tuple[Path, dict]:
    rows = [dict(r) for r in store.settled_ledger()]
    min_n = int(cfg.get("validation", {}).get("min_samples", 100))
    s = metrics.summary(rows, min_n=min_n)
    path = outputs_dir(cfg) / "validation.md"

    lines = ["# Forward validation — live ledger  (**INDICATIVE**)", ""]
    if s.get("n", 0) == 0:
        lines += ["_No settled predictions yet — the ledger fills in as each 5-day horizon elapses._", ""]
    else:
        flag = " ⚠ UNDERPOWERED (n<%d)" % min_n if s["underpowered"] else ""
        lines += [
            f"_{s['n']} settled non-overlapping predictions{flag}._", "",
            f"- **Brier**: {s['brier']}  _(0.25 = always-0.5 baseline; lower is better)_",
            f"- **Base rate** (P realized=up): {s['base_rate']}",
            f"- **Up-call hit-rate**: {s['up_call_hit_rate']} over {s['up_calls']} long calls "
            f"→ **{'beats' if s['beats_base_rate'] else 'does NOT beat'}** base rate",
            "", "| Pred. bin | n | mean pred | observed up-rate |", "|---|---|---|---|",
        ]
        for b in s["reliability"]:
            lines.append(f"| {b['bin']} | {b['n']} | {b['mean_pred']} | {b['obs_rate']} |")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path, s
