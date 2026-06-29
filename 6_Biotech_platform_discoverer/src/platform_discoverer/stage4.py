"""Stage 4 — tiered Claude scoring (spec §5.6, §9, §10). Replaces the Stage-3 embedding pre-rank.

At this universe size (~600), the embedding cut isn't needed for cost (see spec/decisions.md D1), so
the candidate set goes straight into the cheap→expensive Claude funnel:

  Tier 1  Haiku triage   — cheap keep/kill over EVERY live company (recall-safe; sees full evidence)
  Tier 2  Sonnet rubric  — full §9.3 rubric over triage survivors, via the Batch API (50% cost)
  Tier 3  Opus finalize  — re-score only the contested-band composites, with an adversarial pass

Composite + confidence are computed in CODE (composite.py), not trusted from the model. Cost is
estimated up front (no API) and the actual dispatch is gated + bounded by ``max_usd_per_run``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config as cfg
from . import tiering
from .clients.anthropic_client import PRICING
from .models import Score
from .scoring import composite, rubric
from .store import Store, now_iso

log = logging.getLogger(__name__)

_EVIDENCE_SOURCES = ("ctgov", "patents")   # OpenAlex/pedigree dismissed (D9) — Claude web-researches them


# ── candidate set + bundles ─────────────────────────────────────────────────

def _scored_within(store: Store, company_id: str, ttl_days: int,
                   config_hash: Optional[str] = None) -> bool:
    """True if the company has a fresh score that is still VALID — i.e. newer than ``ttl_days`` AND
    scored under the current scoring config. A config change (§12) invalidates the TTL skip so the
    company is re-scored; ``config_hash=None`` keeps the old TTL-only behaviour."""
    last, last_hash = store.last_score_meta(company_id)
    if not last or ttl_days <= 0:
        return False
    if config_hash is not None and last_hash != config_hash:
        return False                                   # scoring config changed → not "fresh" anymore
    try:
        ts = datetime.fromisoformat(last)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts < timedelta(days=ttl_days)


def _candidates(store: Store, config: dict, tickers=None, *, force: bool = False,
                tiers=None) -> list:
    s4 = config.get("stage4_scoring", {}) or {}
    cos = [c for c in store.all_companies() if c.is_live]
    if not s4.get("score_live_excluded", True):
        cos = [c for c in cos if not c.stage1_excluded]   # tagged-only mode
    if tickers:
        tset = {t.upper() for t in tickers}
        cos = [c for c in cos if (c.primary_ticker or "").upper() in tset]
    if tiers is not None:
        # tier gate (spec §5.6): score only the operator-selected market-cap × age tiers. This is the
        # IPO-date filter applied UPSTREAM of the Claude call.
        tset = set(tiers)
        cos = [c for c in cos if tiering.compute_tier(c, config) in tset]
    if not force:
        # rescore-TTL: a ticker scored within the last year UNDER THE CURRENT SCORING CONFIG is not
        # re-analysed (saves cost) unless --force-rescore. A scoring-config change (§12) re-opens it.
        ttl = int(s4.get("rescore_ttl_days", 365))
        chash = cfg.config_hash(config)
        cos = [c for c in cos if not _scored_within(store, c.company_id, ttl, chash)]
    return cos


def due_tier_breakdown(store: Store, config: dict, tickers=None, *, force: bool = False) -> dict:
    """{tier: count} over the DUE candidate set (post ticker/TTL filter, pre tier gate) — drives the
    interactive "which tiers do you want to score?" prompt. Covers all of ``tiering.ALL_TIERS``."""
    due = _candidates(store, config, tickers, force=force)
    return tiering.tier_breakdown(due, config)


def _persist_score(store: Store, cid: str, rj: dict, model: str, *, weights: dict, penalties: dict,
                   evidence_n: int, finalized: bool, run_id: str, config_hash: str) -> dict:
    """Compute composite/confidence for one rubric result and PERSIST it immediately (crash-safe:
    each score is durable the moment it's produced, so a later error/hang never loses earlier work).
    Idempotent on (company_id, run_id) — the Opus finalize upserts over the Sonnet row. Returns the
    in-memory entry used to decide finalize-band membership."""
    comp = composite.compute_composite(rj, weights, penalties)
    conf = composite.compute_confidence(rj, evidence_sources=evidence_n, finalized=finalized)
    ax = composite.axis_scores(rj)
    store.record_score(Score(company_id=cid, run_id=run_id, model=model, json=rj,
                             A=ax["A_proprietary_data"], B=ax["B_compute_engine"],
                             C=ax["C_validation"], D=ax["D_mechanism"], E=ax["E_translation"],
                             composite=comp, confidence=conf), run_id=run_id, config_hash=config_hash)
    return {"rubric": rj, "composite": comp, "confidence": conf, "model": model}


def _evidence(store: Store, company_id: str) -> dict:
    out = {}
    for src in _EVIDENCE_SOURCES:
        ev = store.get_evidence(company_id, src)
        if ev and ev.get("payload"):
            out[src] = ev["payload"]
    return out


def build_bundles(store: Store, config: dict, tickers=None, *, force=False,
                  tiers=None) -> dict[str, dict]:
    """{company_id: evidence-bundle} for every DUE candidate (the Claude input)."""
    by_id = {c.company_id: c for c in _candidates(store, config, tickers, force=force, tiers=tiers)}
    return {cid: rubric.build_bundle(c, _evidence(store, cid)) for cid, c in by_id.items()}


# ── cost estimate (pure, no API) ────────────────────────────────────────────

def estimate_cost(n: int, config: dict) -> dict:
    """Project the tiered run cost for ``n`` candidates from config knobs + the pricing table."""
    s4 = config.get("stage4_scoring", {}) or {}
    in_tok = int(s4.get("est_input_tokens_per_company", 600))
    out_rub = int(s4.get("est_output_tokens_rubric", 400))
    out_tri = int(s4.get("est_output_tokens_triage", 100))
    keep = float(s4.get("est_triage_keep_frac", 0.5))
    contested = float(s4.get("est_contested_frac", 0.2))
    tri_m, sco_m, fin_m = s4["triage_model"], s4["score_model"], s4["finalize_model"]
    batch = bool(s4.get("use_batch_api", True))

    def cost(model, companies, out_tok, batched):
        p = next((v for k, v in PRICING.items() if model.startswith(k)), {"in": 5, "out": 25})
        usd = (companies * (in_tok * p["in"] + out_tok * p["out"])) / 1_000_000
        return usd * (0.5 if batched else 1.0)

    n_survivors = round(n * keep)
    n_contested = round(n_survivors * contested)
    tiers = {
        "triage_haiku": {"companies": n, "usd": round(cost(tri_m, n, out_tri, False), 2)},
        "rubric_sonnet": {"companies": n_survivors,
                          "usd": round(cost(sco_m, n_survivors, out_rub, batch), 2)},
        "finalize_opus": {"companies": n_contested,
                          "usd": round(cost(fin_m, n_contested, out_rub, False), 2)},
    }
    # web research (D9): the rubric + finalize tiers each run a few web searches per company, billed
    # separately from tokens. Triage is search-free.
    wr = s4.get("web_research", {}) or {}
    web_usd = 0.0
    if wr.get("enabled", True):
        per_search = float(wr.get("cost_per_1k_searches_usd", 10)) / 1000.0
        est_searches = float(wr.get("est_searches_per_company", 3))
        n_web = n_survivors + n_contested
        web_usd = round(n_web * est_searches * per_search, 2)
        tiers["web_search"] = {"companies": n_web, "usd": web_usd}
    total = round(sum(t["usd"] for t in tiers.values()), 2)
    return {"candidates": n, "tiers": tiers, "est_total_usd": total,
            "max_usd_per_run": (config.get("cost_controls", {}) or {}).get("max_usd_per_run")}


# ── orchestrator ────────────────────────────────────────────────────────────

def run(store: Store, config: dict, *, dispatch: bool = False, client=None,
        run_id: Optional[str] = None, tickers=None, use_batch: Optional[bool] = None,
        force: bool = False, tiers=None) -> dict:
    """Estimate (dispatch=False) or run (dispatch=True) the tiered scorer.

    ``client`` is an injected ``AnthropicClient`` (tests pass a fake); real runs construct one bounded
    by ``cost_controls.max_usd_per_run``. ``tickers`` restricts to a specific set; ``use_batch``
    overrides ``use_batch_api``. ``force`` re-scores even tickers scored within the rescore-TTL (the
    "reset"); otherwise a ticker scored within the last year is skipped. ``tiers`` (a set of tier ints
    from ``tiering``) gates scoring to the operator-selected market-cap × age tiers (None = all).
    """
    bundles = build_bundles(store, config, tickers, force=force, tiers=tiers)
    if not dispatch:
        est = estimate_cost(len(bundles), config)
        est["note"] = ("counts only tickers DUE for scoring (not scored within "
                       f"{config.get('stage4_scoring', {}).get('rescore_ttl_days', 365)}d"
                       + (f"; tiers {sorted(tiers)}" if tiers is not None else "")
                       + "); use --force-rescore to override")
        est["selected_tiers"] = sorted(tiers) if tiers is not None else None
        return {"mode": "estimate", **est}

    s4 = config["stage4_scoring"]
    weights = config["composite_weights"]
    penalties = config.get("penalties", {}) or {}
    lo, hi = s4.get("finalize_if_composite_between", [0.55, 0.75])
    if use_batch is None:
        use_batch = bool(s4.get("use_batch_api", True))
    taxonomy = cfg.load_taxonomy(s4.get("taxonomy", "config/taxonomy.yaml"))
    from . import prestige as _prestige
    prestige_data = _prestige.load_prestige(
        (config.get("stage2", {}) or {}).get("prestige_labs", "config/prestige_labs.yaml"))
    rubric_system = rubric.build_rubric_system(taxonomy, prestige_data)

    # web research (D9): the rubric/finalize tiers get web_search so Claude researches publications +
    # pedigree itself (replacing OpenAlex). Triage stays search-free. allowed_domains focuses it (D13).
    wr = s4.get("web_research", {}) or {}
    conc = int(s4.get("concurrency", 6))
    web_tool = None
    if wr.get("enabled", True):
        from .clients.anthropic_client import web_search_tool
        web_tool = [web_search_tool(int(wr.get("max_searches_per_company", 5)),
                                    allowed_domains=wr.get("allowed_domains") or None)]

    if client is None:
        from .clients.anthropic_client import AnthropicClient
        client = AnthropicClient(
            max_usd=(config.get("cost_controls", {}) or {}).get("max_usd_per_run", 50),
            web_search_cost_per_search=float(wr.get("cost_per_1k_searches_usd", 10)) / 1000.0)

    # run_id + scoring-config hash fixed up front so every incrementally-persisted score is stamped
    # consistently (§12 config-change re-runs) and shares one run_id.
    rid = run_id or now_iso()
    chash = cfg.config_hash(config)

    # Tier 1 — Haiku triage over every candidate (recall-safe), parallel via async fan-out (D13).
    triage = client.score_realtime_many(s4["triage_model"], rubric.TRIAGE_SYSTEM,
                                        rubric.TRIAGE_SCHEMA, bundles, 256,
                                        validate=rubric.validate_triage, concurrency=conc)
    survivors: dict[str, dict] = {}
    killed = 0
    for cid in bundles:
        t = triage.get(cid)
        if t and t.get("keep"):
            survivors[cid] = bundles[cid]
        else:
            killed += 1
            if t:
                store.audit(stage="stage4", action="cut", company_id=cid, reason="triage_kill",
                            run_id=rid, detail={"prior": t.get("prior"), "why": t.get("reason")})

    # Tier 2 — Sonnet full rubric over survivors. Each score is PERSISTED the instant it's produced
    # (crash-safe — a later hang/error never discards earlier survivors' web-search work). Batch path
    # persists the batch_id on submit so a crashed poll can be resumed (D13).
    scored: dict[str, dict] = {}
    if use_batch:
        def _save_batch_id(bid):
            store.record_run_meta(rid, started=rid, config_hash=chash,
                                  metrics={"stage4_batch_id": bid, "phase": "batch_submitted",
                                           "score_model": s4["score_model"]})
            log.info("stage4: persisted batch_id %s to run_meta(%s) — resume with --resume %s",
                     bid, rid, rid)
        rubric_out = client.score_batch(s4["score_model"], rubric_system, rubric.RUBRIC_SCHEMA,
                                        survivors, 1500, validate=rubric.validate_rubric,
                                        tools=web_tool, on_submit=_save_batch_id)
    else:
        rubric_out = client.score_realtime_many(s4["score_model"], rubric_system,
                                                rubric.RUBRIC_SCHEMA, survivors, 1500,
                                                validate=rubric.validate_rubric, tools=web_tool,
                                                concurrency=conc)
    for cid, rj in rubric_out.items():
        scored[cid] = _persist_score(store, cid, rj, s4["score_model"], weights=weights,
                                     penalties=penalties, evidence_n=len(_evidence(store, cid)),
                                     finalized=False, run_id=rid, config_hash=chash)

    # Tier 3 — Opus finalize on the contested band (adversarial), parallel (D13). Each re-score
    # upserts the same (company_id, run_id) row, so it's also durable immediately.
    contested = {cid: survivors[cid] for cid, s in scored.items() if lo <= s["composite"] <= hi}
    final_out = client.score_realtime_many(
        s4["finalize_model"], rubric_system + rubric.ADVERSARIAL_SUFFIX, rubric.RUBRIC_SCHEMA,
        contested, 1500, validate=rubric.validate_rubric, tools=web_tool, concurrency=conc)
    for cid, rj in final_out.items():
        scored[cid] = _persist_score(store, cid, rj, s4["finalize_model"], weights=weights,
                                     penalties=penalties, evidence_n=len(_evidence(store, cid)),
                                     finalized=True, run_id=rid, config_hash=chash)
    finalized = len(final_out)

    summary = {"mode": "dispatch", "candidates": len(bundles), "triage_killed": killed,
               "scored": len(scored), "opus_finalized": finalized,
               "selected_tiers": sorted(tiers) if tiers is not None else None,
               "cost_usd": round(getattr(client, "spent_usd", 0.0), 2),
               "api_calls": getattr(client, "calls", 0),
               "web_searches": getattr(client, "web_searches", 0)}
    store.audit(stage="stage4", action="scored", reason="scoring_done", run_id=rid, detail=summary)
    log.info("stage4: %s", summary)
    return summary


def resume_stage4(store: Store, config: dict, *, run_id: str, client=None) -> dict:
    """Re-attach a crashed batch run (D13): read the persisted ``stage4_batch_id`` from run_meta,
    poll/collect it (idempotent), and persist the rubric scores. Recovers a run whose poll was
    interrupted without re-submitting (Anthropic keeps batch results ~29 days)."""
    row = store.conn.execute(
        "SELECT metrics_json, config_hash FROM run_meta WHERE run_id=?", (run_id,)).fetchone()
    meta = json.loads(row["metrics_json"]) if row and row["metrics_json"] else {}
    batch_id = meta.get("stage4_batch_id")
    if not batch_id:
        return {"mode": "resume", "run_id": run_id, "error": "no stage4_batch_id in run_meta"}
    s4 = config["stage4_scoring"]
    weights = config["composite_weights"]
    penalties = config.get("penalties", {}) or {}
    model = meta.get("score_model", s4["score_model"])
    chash = (row["config_hash"] if row else None) or cfg.config_hash(config)
    if client is None:
        from .clients.anthropic_client import AnthropicClient
        client = AnthropicClient(
            max_usd=(config.get("cost_controls", {}) or {}).get("max_usd_per_run", 50))
    rubric_out = client.collect_batch(batch_id, model, validate=rubric.validate_rubric)
    n = 0
    for cid, rj in rubric_out.items():
        _persist_score(store, cid, rj, model, weights=weights, penalties=penalties,
                       evidence_n=len(_evidence(store, cid)), finalized=False,
                       run_id=run_id, config_hash=chash)
        n += 1
    summary = {"mode": "resume", "run_id": run_id, "batch_id": batch_id, "scored": n,
               "cost_usd": round(getattr(client, "spent_usd", 0.0), 2)}
    store.record_run_meta(run_id, finished=now_iso(), config_hash=chash,
                          metrics={**meta, "phase": "resumed", "resumed_scored": n})
    store.audit(stage="stage4", action="scored", reason="resume_done", run_id=run_id, detail=summary)
    log.info("stage4 resume: %s", summary)
    return summary
