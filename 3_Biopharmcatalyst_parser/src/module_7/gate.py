"""D38 — Pre-dispatch ticker gate.

Once the user has reviewed a catalyst (ticked the ack-toggle in the
HTML), they've already done their analysis pass on that ticker. They
DON'T want any further Claude API calls on it — even for a different
drug for the same company.

Mechanism:
  * The HTML's localStorage acknowledged set is keyed at the
    (ticker|drug|nct|type) PK level (D35b).
  * The JS ack-handler (D38) bulk-applies the toggle across every PK
    that shares the ticker — so ticking one row marks the whole ticker
    as reviewed.
  * The live-price server (D38) exposes
    `POST /api/save_acknowledged_tickers` which the JS calls after
    every ack change. The server writes the deduped ticker set to
    `data/acknowledged_tickers.json`.
  * Before each dispatch (M7 + M8 + both cost estimators), the
    dispatcher loads that file and DROPS any candidate whose ticker
    is in the set. The default behaviour blocks; pass
    `--override-ack-gate` on the CLI to force-dispatch an acked
    ticker (rare; only when the user actively wants to re-run).

Net effect: re-running the pipeline at any cadence is safe — the gate
keeps Anthropic cost focused on tickers the user hasn't yet reviewed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ACK_TICKERS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "acknowledged_tickers.json"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_acknowledged_tickers(path: Path | str | None = None) -> frozenset[str]:
    """Read the acknowledged-tickers JSON file.

    Returns an empty set when the file is missing, unreadable, or has
    a corrupt body — so the gate fails OPEN (dispatcher proceeds without
    filtering rather than crashing).
    """
    p = Path(path) if path else DEFAULT_ACK_TICKERS_PATH
    if not p.exists():
        return frozenset()
    try:
        raw = p.read_text(encoding="utf-8")
        obj = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return frozenset()
    if not isinstance(obj, dict):
        return frozenset()
    tickers = obj.get("tickers") or []
    if not isinstance(tickers, list):
        return frozenset()
    return frozenset(
        str(t).strip().upper() for t in tickers if isinstance(t, str) and t.strip()
    )


def save_acknowledged_tickers(
    tickers: set[str] | frozenset[str] | list[str],
    *,
    path: Path | str | None = None,
) -> None:
    """Write the ticker set to disk. Atomic via tmp+rename.

    `tickers` is normalised to uppercase + deduplicated + sorted before
    writing so the file diffs cleanly between renders.
    """
    p = Path(path) if path else DEFAULT_ACK_TICKERS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    normalised = sorted({
        str(t).strip().upper()
        for t in tickers
        if isinstance(t, str) and t.strip()
    })
    payload = {
        "saved_at_utc": _now_iso(),
        "tickers": normalised,
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)


def apply_ticker_gate(
    candidates: list[dict],
    acknowledged: frozenset[str] | set[str],
    *,
    ticker_key: str = "ticker",
) -> tuple[list[dict], list[dict]]:
    """Split candidates into (kept, dropped) based on `acknowledged`.

    A candidate is DROPPED iff its `ticker_key` value (upper-cased) is
    in the acknowledged set.

    Empty set → all kept (no-op gate). NULL/missing ticker on a
    candidate → kept (defensive; we never silently drop based on
    a missing identifier).
    """
    if not acknowledged:
        return list(candidates), []
    ack = frozenset(str(t).strip().upper() for t in acknowledged)
    kept: list[dict] = []
    dropped: list[dict] = []
    for cand in candidates:
        t = cand.get(ticker_key)
        if not isinstance(t, str) or not t.strip():
            kept.append(cand)
            continue
        if t.strip().upper() in ack:
            dropped.append(cand)
        else:
            kept.append(cand)
    return kept, dropped
