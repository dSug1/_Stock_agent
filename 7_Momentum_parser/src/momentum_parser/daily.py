"""Daily orchestrator — runs the whole v0.3 pipeline in order with the right cadence (spec §5).

  0a discover (WEEKLY, paid)  ->  1 prices  ->  0b gate  ->  2 harvest  ->  3 score (gated)  ->
  5 blend + export  ->  §9 settle

Discovery is release-calendar gated (only when due). Scoring runs only with ``dispatch=True`` and is
protected by `max_usd_per_run` (the automation replacement for the interactive [y/N] gate). A dry run
(no ``--dispatch``) still produces a model-only `signals.md` for free. Stage modules are imported lazily.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .store import Store


def discovery_due(store: Store, cfg: dict) -> bool:
    """True if no discovery has run within `universe.discovery.cadence_days` (weekly release calendar)."""
    cad = int(cfg.get("universe", {}).get("discovery", {}).get("cadence_days", 7))
    rows = store.latest_discovery()
    if not rows:
        return True
    try:
        last = datetime.fromisoformat(rows[0]["discovered_at"])
    except (ValueError, TypeError):
        return True
    return (datetime.now(timezone.utc) - last).days >= cad


def run(store: Store, cfg: dict, run_id: str, scorer, dispatch: bool = False,
        fetch: bool = True, log=print) -> dict:
    from . import stage0_discovery, stage0_gate, stage1_prices, stage2_harvest, stage3_score, \
        stage4_export, stage5_blend, validation
    from .universe import UniverseRow, load_universe

    funnel: dict = {"dispatch": dispatch}

    # --- 0a discovery (weekly, paid) ----------------------------------------------------------
    if discovery_due(store, cfg):
        log("[daily] 0a discovery due")
        funnel["discovery"] = stage0_discovery.run(store, cfg, run_id, scorer, dispatch=dispatch)
    else:
        log("[daily] 0a discovery not due (skipped)")

    cands = [r["ticker"] for r in store.latest_discovery()] or [u.ticker for u in load_universe(cfg)]
    if not cands:
        log("[daily] no candidates — run --discover or populate the seed CSV")
        return funnel

    # --- 1 prices (fetch the candidate set so the gate + scoring have bars) --------------------
    if fetch:
        log(f"[daily] 1 prices · fetching OHLCV for {len(cands)} candidates (yfinance — slow on first run)...")
        funnel["prices"] = stage1_prices.run(store, [UniverseRow(t, "") for t in cands], cfg, log=log)

    # --- 0b gate -> investable -----------------------------------------------------------------
    log("[daily] 0b liquidity/vol/price gate")
    funnel["gate"] = stage0_gate.run(store, cands, cfg, log=lambda m: None)
    investable = store.investable_tickers()
    log(f"[daily] investable: {len(investable)} names  ({funnel['gate']['by_status']})")

    if not investable:
        log("[daily] nothing passed the gate — no scoring/blend this run")
        return funnel

    # --- 2 harvest (media via GDELT is slow on a COLD cache; cached after, fail-open) -----------
    harvest_cfg = cfg
    if not fetch:                                   # --no-fetch = fast offline preview (skip GDELT too)
        harvest_cfg = {**cfg, "sources": {**cfg.get("sources", {}), "media_provider": "none"}}
        log("[daily] 2 harvest · --no-fetch -> media stub (skipping GDELT for a fast preview)")
    elif cfg.get("sources", {}).get("media_provider", "none") == "gdelt":
        log(f"[daily] 2 harvest · media via GDELT for {len(investable)} names — first run ~5s/call "
            "(rate-limit spacing); cached after, fail-open on 429. Working...")
    funnel["harvest"] = stage2_harvest.run(store, investable, harvest_cfg, log=log)

    # --- 3 score (gated; $5 cap protects the automated dispatch) -------------------------------
    if dispatch:
        log("[daily] 3 tiered Claude scoring (dispatch)")
        funnel["score"] = stage3_score.run(store, investable, cfg, run_id, scorer, dispatch=True,
                                           log=log)
    else:
        log("[daily] 3 scoring SKIPPED (dry run — pass --dispatch); blend will be model-only")

    # --- 5 blend + export ----------------------------------------------------------------------
    funnel["blend"] = stage5_blend.run(store, investable, cfg, run_id, log=lambda m: None)
    path = stage4_export.run(store, cfg, run_id, log=lambda m: None)
    funnel["signals_md"] = str(path)

    # --- §9 settle elapsed predictions + report -----------------------------------------------
    funnel["settle"] = validation.settle_pass(store, cfg, log=lambda m: None)
    validation.build_report(store, cfg)

    log(f"[daily] done · investable={len(investable)} · signals -> {path}")
    return funnel
