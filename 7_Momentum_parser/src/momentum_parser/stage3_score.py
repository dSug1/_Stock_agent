"""Stage 3 — tiered Claude scoring with the dispatch lessons applied (spec v0.3 §6, Decision D).

Pipeline per run:
  Tier 1 Haiku triage (real-time, search-free)  -> drop names clearly going nowhere (recall-safe floor)
  Tier 2 Sonnet rubric (+web_search, Batch API) -> the main analysis on survivors
  Tier 3 Opus finalize (+web_search, real-time) -> adversarial pass on the contested band only

Each result is written to `scores` the instant it completes (crash-safe incremental persist). The rubric
Batch's id is persisted BEFORE polling so a killed run can `--resume`. Already-scored (config+evidence
unchanged) names are skipped. A dry run prints the cost estimate; `--dispatch` is the spend gate, bounded
by `max_usd_per_run`.

The `scorer` is injected (the real `AnthropicScorer`, or a fake in tests) — only its three methods
(`score_many_realtime`, `submit_batch`, `poll_batch`) are used, so this whole module is offline-testable.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .scoring import cost
from .scoring.identity import config_hash, evidence_fingerprint
from .scoring.rubric import PROMPT_VERSION, build_bundle, build_request
from .store import Store


def _tiers(cfg: dict) -> dict:
    cl = cfg.get("claude", {})
    return {
        "triage": {"model": cl.get("triage_model"), "use_search": False, "thinking": False, "use_batch": False},
        "rubric": {"model": cl.get("rubric_model"), "use_search": True, "thinking": True,
                   "use_batch": bool(cl.get("use_batch", True))},
        "finalize": {"model": cl.get("finalize_model"), "use_search": True, "thinking": True, "use_batch": False},
    }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist(store: Store, res: dict, run_id: str, tier: str, chash: str, asof_fp: dict) -> None:
    if res.get("error") or not res.get("parsed"):
        return
    p, cid = res["parsed"], res["custom_id"]
    asof, fp = asof_fp[cid]
    variant = {k: p.get(k, "") for k in
               ("consensus_view", "our_view", "mispricing", "why_now", "macro_exposure")}
    variant["variant_strength"] = p.get("variant_strength")
    variant["raw_conviction"] = p.get("raw_conviction")
    variant["forward_novelty"] = p.get("forward_novelty")           # v0.5/M19
    variant["forward_drivers"] = p.get("forward_drivers") or []     # the GENERATED forward theses
    store.write_score(
        cid, asof, run_id, tier=tier, p_up=p.get("p_up"), p_down=p.get("p_down"), p_flat=p.get("p_flat"),
        expected_return=p.get("expected_return"), conviction=p.get("conviction"),
        dimensions_json=json.dumps(p.get("dimensions", {})), memo=p.get("memo", ""),
        config_hash=chash, evidence_fingerprint=fp, prompt_version=PROMPT_VERSION,
        variant_json=json.dumps(variant),
    )


def _track(res: dict, p_claude: dict) -> None:
    if res.get("error") is None and res.get("parsed", {}).get("p_up") is not None:
        p_claude[res["custom_id"]] = res["parsed"]["p_up"]


def _drain_batch(store: Store, scorer, batch_id: str, tier: str, run_id: str, chash: str,
                 asof_fp: dict, p_claude: dict, log, poll_interval_s: float, max_polls: int) -> bool:
    """Poll a submitted batch until it ends, persisting each result incrementally. Returns done?."""
    for _ in range(max_polls):
        results = scorer.poll_batch(batch_id)
        if results is None:
            time.sleep(poll_interval_s)
            continue
        for res in results:
            _persist(store, res, run_id, tier, chash, asof_fp)
            _track(res, p_claude)
        store.set_batch_status(run_id, tier, "done")
        return True
    log(f"[3] batch '{tier}' still processing after {max_polls} polls — re-run with --resume")
    return False


def run(store: Store, tickers: list[str], cfg: dict, run_id: str, scorer,
        dispatch: bool = False, force: bool = False, resume: bool = False, debug: bool = False,
        poll_interval_s: float = 5.0, max_polls: int = 120, log=print) -> dict:
    cl = cfg.get("claude", {})
    dbg = cl.get("debug", {})
    chash = config_hash(cfg, PROMPT_VERSION)
    tiers = _tiers(cfg)

    # candidates = live tickers with cached bars + their (asof, evidence fingerprint)
    cands = []
    for t in tickers:
        asof = store.latest_bar_date(t)
        if not asof:
            continue
        cands.append((t, asof, evidence_fingerprint(store.get_evidence(t, asof))))
    asof_fp = {t: (a, f) for (t, a, f) in cands}
    p_claude: dict = {}

    # --resume: re-poll any open batch from a prior killed run first
    if resume:
        for job in store.open_batches(run_id):
            _drain_batch(store, scorer, job["batch_id"], job["tier"], run_id, chash,
                         asof_fp, p_claude, log, poll_interval_s, max_polls)

    # skip names already scored at this exact (config, evidence) state
    to_score = [(t, a, f) for (t, a, f) in cands
                if force or not store.has_fresh_score(t, a, chash, f)]

    # --debug: cap names (keeps spend ~cents) + real-time (no Batch wait); token cap stays 12500.
    if debug:
        nmax = int(dbg.get("max_names", 3) or 0)
        if nmax:
            to_score = to_score[:nmax]
        if dbg.get("force_realtime", True):
            tiers["rubric"]["use_batch"] = False

    est = cost.estimate(len(to_score), cfg)
    cap = float(cl.get("cost", {}).get("max_usd_per_run", 5.0))
    log(f"[3] {len(to_score)}/{len(cands)} to score · estimate ${est['total']} (cap ${cap}) · {est}"
        + (" · DEBUG" if debug else ""))
    if not dispatch:
        log("[3] dry run — pass --dispatch to spend.")
        return {"estimated": est, "candidates": len(to_score), "scored": 0, "dispatched": False}
    over = est["total"] > cap
    if over and not (debug and dbg.get("ignore_budget", True)):
        log(f"[3] ABORT: estimate ${est['total']} exceeds max_usd_per_run ${cap}.")
        return {"estimated": est, "candidates": len(to_score), "scored": 0, "dispatched": False,
                "aborted": "over_budget"}
    if over and debug:
        log(f"[3] DEBUG: estimate ${est['total']} > cap ${cap} but ignoring max_usd_per_run (debug).")

    # --- Tier 1: triage (real-time, search-free, recall-safe floor) -----------------------------
    floor = float(cl.get("triage_floor", 0.35))
    survivors: list[str] = []

    def on_triage(res):
        _persist(store, res, run_id, "triage", chash, asof_fp)
        _track(res, p_claude)
        pu = res.get("parsed", {}).get("p_up")
        if res.get("error") is None and pu is not None and pu >= floor:
            survivors.append(res["custom_id"])

    triage_reqs = [build_request(build_bundle(store, t, a, cfg), tiers["triage"], cfg)
                   for (t, a, f) in to_score]
    scorer.score_many_realtime(triage_reqs, on_triage)

    # --- Tier 2: rubric (Batch API by default) --------------------------------------------------
    rubric_reqs = [build_request(build_bundle(store, t, asof_fp[t][0], cfg), tiers["rubric"], cfg)
                   for t in survivors]
    if rubric_reqs:
        if tiers["rubric"]["use_batch"]:
            batch_id = scorer.submit_batch(rubric_reqs)
            store.record_batch(run_id, "rubric", batch_id, _utcnow())     # PERSIST BEFORE POLL
            _drain_batch(store, scorer, batch_id, "rubric", run_id, chash,
                         asof_fp, p_claude, log, poll_interval_s, max_polls)
        else:
            scorer.score_many_realtime(
                rubric_reqs, lambda res: (_persist(store, res, run_id, "rubric", chash, asof_fp),
                                          _track(res, p_claude)))

    # --- Tier 3: Opus adversarial pass on the ACTIONABLE names (real-time) ----------------------
    # M17 fix: the old symmetric contested band [0.45,0.65] caught nobody once p_claude compressed below
    # 0.45 (dead tier). Instead adversarially vet the highest-p_up survivors above a floor, capped for cost —
    # a second opinion where it matters (the names we'd actually go long), not a narrow dead zone.
    fin_min = float(cl.get("finalize_min_p", 0.45))
    fin_max = int(cl.get("finalize_max_names", 6))
    contested = [t for _, t in sorted(((p_claude.get(t, 0.0), t) for t in survivors
                                       if p_claude.get(t, 0.0) >= fin_min), reverse=True)[:fin_max]]
    fin_reqs = [build_request(build_bundle(store, t, asof_fp[t][0], cfg), tiers["finalize"], cfg)
                for t in contested]
    scorer.score_many_realtime(
        fin_reqs, lambda res: (_persist(store, res, run_id, "finalize", chash, asof_fp),
                               _track(res, p_claude)))

    return {"estimated": est, "candidates": len(to_score), "survivors": len(survivors),
            "contested": len(contested), "scored": len(p_claude), "dispatched": True,
            "web_searches": getattr(scorer, "web_searches", 0)}
