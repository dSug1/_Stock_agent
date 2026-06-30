"""Stage 0a — weekly Claude universe discovery (spec Decision C; reuses the M3 dispatch infra).

One cost-gated Claude call (web_search) proposes the candidate basket → persisted to `discovery`. Injectable
`scorer` (the real `AnthropicScorer.complete`, or a fake) so it's offline-testable. Cost-gated like Stage 3:
`--dispatch` is the spend switch; a dry run just prints the estimate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .scoring import discovery
from .scoring.cost import WEB_SEARCH_USD, _tok_usd
from .store import Store


def _estimate(cfg: dict) -> float:
    """Realtime, web_search-heavy call: web_search content is re-billed as input across pause_turn
    continuations, so effective input (~96k) dwarfs the base prompt. Uses a discovery-specific token
    model + calibration (1.0) — the 0.10 SCORING fudge does NOT apply here (no Batch, no cache reuse).
    Tuned to a real invoice (~$0.39 @ sonnet/6 searches, D-5)."""
    cl = cfg.get("claude", {})
    model = cl.get("discovery_model") or cl.get("rubric_model")
    dc = cl.get("cost", {}).get("discovery", {})
    in_tok = float(dc.get("in_tok", 96000))
    out_tok = float(dc.get("out_tok", 2500))
    calib = float(dc.get("calibration_factor", 1.0))
    searches = int(cl.get("discovery_searches", 8))
    return round((_tok_usd(model, in_tok, out_tok) + searches * WEB_SEARCH_USD) * calib, 4)


def run(store: Store, cfg: dict, run_id: str, scorer, dispatch: bool = False, log=print) -> dict:
    est = _estimate(cfg)
    log(f"[0a] discovery estimate ${est}")
    if not dispatch:
        log("[0a] dry run — pass --dispatch to spend.")
        return {"estimated": est, "discovered": 0, "dispatched": False}

    timeout = float(cfg.get("claude", {}).get("discovery_timeout_s", 300))
    res = scorer.complete(discovery.build_request(cfg)["params"], timeout=timeout)
    if res.get("error"):
        log(f"[0a] discovery FAILED (fail-soft): {res['error']}")
        return {"estimated": est, "discovered": 0, "dispatched": True, "error": res["error"]}

    cands = discovery.clean(res.get("parsed", {}).get("candidates", []), cfg)
    store.write_discovery(run_id, cands, datetime.now(timezone.utc).isoformat())
    log(f"[0a] discovered {len(cands)} candidates · {res.get('web_searches', 0)} web searches")
    return {"estimated": est, "discovered": len(cands), "dispatched": True,
            "web_searches": res.get("web_searches", 0), "tickers": [c["ticker"] for c in cands]}
