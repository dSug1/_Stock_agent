"""Tiering — group the universe by market cap × company age, UPSTREAM of the Claude call (spec §5.6).

The analyst wants the IPO-date signal used as a *filter/tier* before the expensive Stage-4 scoring,
not only as a post-score ranking tilt. So each live company is bucketed into one of four tiers by two
cheap, already-captured signals — market cap (vs a `$400M` threshold) and years since IPO (vs a `20yr`
threshold) — and the operator chooses which tiers to spend Claude budget on:

  | tier | market cap        | age since IPO | read                                  |
  |------|-------------------|---------------|---------------------------------------|
  | 1    | < threshold       | < threshold   | small & young — the "early but real" zone (priority) |
  | 2    | ≥ threshold       | < threshold   | large & young                         |
  | 3    | ≥ threshold       | ≥ threshold   | large & old                           |
  | 4    | < threshold       | ≥ threshold   | small & old — the ship has likely sailed |
  | 0    | (cap or IPO date missing) | —     | UNTIERED — can't bucket yet; surfaced, never hidden (recall-safe) |

Tier is computed on the fly (never persisted) so it always reflects the current cap/IPO data. Tier 0
is the recall-safe home for missing data: a company is never dropped or mis-sorted into a deprioritized
tier just because its `ipo_date`/cap hasn't been enriched yet — it sits in its own bucket the operator
can still choose to score. (`ipo_date` populates on the next Stage-0b `--enrich-yf`; see decisions.md
D4/D5.)
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from .models import Company

# The four real tiers + the untiered bucket. Order = display/selection order.
ALL_TIERS = (1, 2, 3, 4, 0)

_TIER_LABELS = {
    1: "Tier 1 · small & young",
    2: "Tier 2 · large & young",
    3: "Tier 3 · large & old",
    4: "Tier 4 · small & old",
    0: "Untiered · missing cap/IPO",
}


def tier_label(tier: int) -> str:
    return _TIER_LABELS.get(tier, f"Tier {tier}")


def age_years(ipo_date: Optional[str], today: Optional[date] = None) -> Optional[float]:
    """Years between ``ipo_date`` (ISO 'YYYY-MM-DD' or full ISO) and ``today``; None if unparseable."""
    if not ipo_date:
        return None
    try:
        d = date.fromisoformat(str(ipo_date)[:10])
    except ValueError:
        return None
    return max(0.0, ((today or date.today()) - d).days / 365.25)


def _thresholds(config: dict) -> tuple[float, float]:
    t = (config.get("tiers", {}) or {})
    return (float(t.get("mktcap_threshold_usd", 400_000_000)),
            float(t.get("ipo_age_threshold_years", 20)))


def compute_tier(company: Company, config: dict, *, today: Optional[date] = None) -> int:
    """Bucket a company into tier 1-4, or 0 when market cap OR IPO age is unknown (recall-safe).

    small = cap < threshold; young = age < threshold (so the boundary value counts as large/old).
    """
    cap_threshold, age_threshold = _thresholds(config)
    cap = company.mktcap_usd_fd
    age = age_years(company.ipo_date, today)
    if cap is None or age is None:
        return 0
    small = cap < cap_threshold
    young = age < age_threshold
    if small and young:
        return 1
    if not small and young:
        return 2
    if not small and not young:
        return 3
    return 4   # small and old


def tier_breakdown(companies, config: dict, *, today: Optional[date] = None) -> dict[int, int]:
    """{tier: count} over the given companies, covering all of ALL_TIERS (zeros included)."""
    counts = {t: 0 for t in ALL_TIERS}
    for c in companies:
        counts[compute_tier(c, config, today=today)] += 1
    return counts


def parse_tier_selection(text: str) -> set[int]:
    """Parse '1,2' / '1 2' / 'all' / '' into a set of tier ints. 'all' → every tier (incl. 0)."""
    s = (text or "").strip().lower()
    if not s or s == "all":
        return set(ALL_TIERS)
    out: set[int] = set()
    for part in s.replace(",", " ").split():
        try:
            n = int(part)
        except ValueError:
            continue
        if n in ALL_TIERS:
            out.add(n)
    return out
