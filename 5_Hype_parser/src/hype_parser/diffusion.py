"""diffusion_ratio math (Features v0.2 section 1) — pure functions, no I/O.

  N_spec(c,t) = # specialist docs whose embedding is within tau_member of the centroid
  N_main(c,t) = # mainstream mentions (GDELT news volume)
  beta_spec   = OLS slope of ln(1 + N_spec) over the last L periods  (numerator slope)
  p_main      = N_main / (N_spec + N_main)                            (S-curve position, [0,1])
  diffusion_ratio = N_spec / N_main                                   (literal v0.1 ratio)

All thresholds (tau_member, L, beta_min, p_max) are deferred parameters (Features section 5);
defaults live in config/diffusion.yaml and are INFORMATIONAL until fit on the labeled panel.
"""

import math
from datetime import date


def month_key(iso_date: str) -> str | None:
    """'2024-03-17...' -> '2024-03'. None for empty/unparseable."""
    if not iso_date or len(iso_date) < 7:
        return None
    return iso_date[:7]


def month_range(start: str, end: str) -> list[str]:
    """Contiguous inclusive list of 'YYYY-MM' from start to end (so slopes have no gaps)."""
    if not start or not end:
        return []
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def cosines_to_centroid(doc_matrix, centroid_vec):
    """doc_matrix (n,d) normalized rows, centroid_vec (d,) normalized -> cosines (n,)."""
    return doc_matrix @ centroid_vec


def membership_mask(cosines, tau_member: float):
    return cosines >= tau_member


def beta_spec(periods_sorted: list[str], n_spec_by_period: dict, L: int) -> float:
    """OLS slope of ln(1+N_spec) over the last L contiguous periods. 0 if < 2 points."""
    window = periods_sorted[-L:] if L and L > 0 else periods_sorted
    ys = [math.log1p(n_spec_by_period.get(p, 0)) for p in window]
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    xm = sum(xs) / n
    ym = sum(ys) / n
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs)
    return num / den if den else 0.0


def p_main(n_spec: int, n_main: int) -> float:
    total = n_spec + n_main
    return (n_main / total) if total > 0 else 0.0


def diffusion_ratio(n_spec: int, n_main: int) -> float:
    if n_main > 0:
        return n_spec / n_main
    return math.inf if n_spec > 0 else 0.0


def nascency_gate(beta: float, p_main_latest: float, *, beta_min: float, p_max: float) -> bool:
    """Features 1.5: beta_spec >= beta_min AND p_main <= p_max. INFORMATIONAL (params unfit)."""
    return beta >= beta_min and p_main_latest <= p_max


def summarize(periods_sorted, n_spec_by_period, n_main_by_period, *, L, tau_member,
              beta_min, p_max):
    """Bundle the instrument outputs for one theme (latest-period p_main/ratio + beta)."""
    latest = periods_sorted[-1] if periods_sorted else None
    ns = n_spec_by_period.get(latest, 0) if latest else 0
    nm = n_main_by_period.get(latest, 0) if latest else 0
    beta = beta_spec(periods_sorted, n_spec_by_period, L)
    pm = p_main(ns, nm)
    return {
        "latest_period": latest,
        "n_spec_latest": ns,
        "n_main_latest": nm,
        "n_spec_total": sum(n_spec_by_period.values()),
        "beta_spec": beta,
        "p_main": pm,
        "diffusion_ratio": diffusion_ratio(ns, nm),
        "nascency_gate": nascency_gate(beta, pm, beta_min=beta_min, p_max=p_max),
        "L": L,
        "tau_member": tau_member,
    }
