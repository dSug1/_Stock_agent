"""Convergence — group jury signals that point at the same nascent area, score by *how many
independent credible leading juries* converge, and promote the winners to ``themes`` (spec §4).

This is the discovery signal: not document volume, but the agreement of independent experts. A group
backed by, say, MIT-TR10 **and** a DARPA call **and** specialist-fund new buys outranks one with a
single mention. Denominator juries (Nobel/Turing) add nothing to the *discovery* score — they only
move the time horizon (§5), the Pouzin nuance.

Zero Claude: grouping is greedy cosine clustering over the local MiniLM embeddings; scoring is a
weighted independent-source count; promotion writes ``themes`` + ``theme_convergence`` so the existing
diffusion engine can then measure each discovered theme's β_spec / p_main curve.
"""

import logging
import re

import numpy as np

from ..db import now_iso

log = logging.getLogger(__name__)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def _greedy_pass(signals: list[dict], tau: float) -> list[list[dict]]:
    """One greedy single-pass clustering: each signal joins the existing group whose centroid is most
    similar (cosine ≥ ``tau``), else seeds a new group. Centroids update incrementally. Order is by
    signal_id (deterministic). Each signal dict must carry a normalized ``vec`` (np.ndarray)."""
    clusters: list[dict] = []                    # {members: [...], sum: vec, centroid: vec}
    for s in sorted(signals, key=lambda x: x["signal_id"]):
        v = np.asarray(s["vec"], dtype=np.float32)
        best, best_sim = None, tau
        for c in clusters:
            sim = _cosine(c["centroid"], v)
            if sim >= best_sim:
                best, best_sim = c, sim
        if best is None:
            clusters.append({"members": [s], "sum": v.copy(), "centroid": v.copy()})
        else:
            best["members"].append(s)
            best["sum"] += v
            n = np.linalg.norm(best["sum"])
            best["centroid"] = best["sum"] / n if n else best["sum"]
    return [c["members"] for c in clusters]


def _split_oversized(members: list[dict], tau: float, *, max_size: int, step: float,
                     ceiling: float) -> list[list[dict]]:
    """Recursively split an oversized group by re-clustering its members at a TIGHTER tau. This is
    self-correcting: a genuinely cohesive theme stays one group when tightened (so we stop), while a
    drifted mega-cluster (e.g. the centroid-drift 'all AI startups' blob) fragments into sub-themes."""
    if len(members) <= max_size or tau >= ceiling:
        return [members]
    sub = _greedy_pass(members, tau + step)
    if len(sub) <= 1:                            # didn't separate -> genuinely cohesive, keep whole
        return [members]
    out = []
    for g in sub:
        out.extend(_split_oversized(g, tau + step, max_size=max_size, step=step, ceiling=ceiling))
    return out


def cluster_signals(signals: list[dict], tau: float, *, max_cluster_size: int | None = None,
                    split_step: float = 0.08, tau_ceiling: float = 0.85) -> list[list[dict]]:
    """Greedy cosine clustering at ``tau`` (see ``_greedy_pass``), then — when ``max_cluster_size`` is
    set — split any oversized group by re-clustering it tighter (anti-mega-cluster; the centroid-drift
    failure mode where one loose 'AI' centroid absorbs hundreds of distinct startups)."""
    groups = _greedy_pass(signals, tau)
    if not max_cluster_size:
        return groups
    out = []
    for g in groups:
        out.extend(_split_oversized(g, tau, max_size=max_cluster_size, step=split_step,
                                    ceiling=tau_ceiling))
    return out


def score_group(members: list[dict], cfg: dict) -> dict:
    """Convergence score + diagnostics for one group. Independence is by DISTINCT source_id (one jury
    naming many entities counts once); score = Σ_source credibility_weight × position_weight."""
    cw = cfg["credibility_weight"]
    pw = cfg["position_weight"]
    by_source = {}                               # source_id -> (position, credibility)
    for m in members:
        by_source.setdefault(m["source_id"], (m.get("diffusion_position"), m.get("jury_credibility")))
    score = 0.0
    positions = set()
    leading_sources = set()
    for sid, (pos, cred) in by_source.items():
        positions.add(pos)
        if pos == "leading":
            leading_sources.add(sid)
        score += cw.get(cred or "na", cw.get("na", 0.5)) * pw.get(pos or "leading", 0.0)
    return {
        "score": round(score, 4),
        "n_signals": len(members),
        "n_sources": len(by_source),
        "n_leading_juries": len(leading_sources),
        "leading_sources": sorted(leading_sources),
        "positions": positions,
        "sources": sorted(by_source),
    }


def estimate_horizon(positions: set, cfg: dict) -> dict:
    """Runway estimate from which jury tiers fired (spec §5). Heuristic until calibrated against past
    p_main inflection (⚙). Denominator firing ⇒ recognition arrived ⇒ ~0 runway (likely too late)."""
    h = cfg["horizon"]
    if "denominator" in positions:
        tier = h["denominator_fired"]
    elif "bridge" in positions:
        tier = h["bridge_fired"]
    else:
        tier = h["leading_only"]
    return {"years": tier["years"], "band": tier["band"], "confidence": h.get("confidence", "heuristic")}


_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    s = _SLUG.sub("-", (text or "").lower()).strip("-")
    return s[:48] or "theme"


def _clean(text: str) -> str:
    """Strip the U+FFFD replacement char (mojibake from non-UTF8 archived snapshots) + tidy whitespace."""
    stripped = "".join(ch for ch in (text or "") if ord(ch) != 0xFFFD)
    return re.sub(r"\s+", " ", stripped).strip()


def _group_label(members: list[dict]) -> str:
    """A human label for the group: the entity of its most-credible leading signal, else any entity."""
    order = {"high": 0, "medium": 1, "low": 2, "na": 3}
    lead = [m for m in members if m.get("diffusion_position") == "leading" and m.get("entity")]
    pool = lead or [m for m in members if m.get("entity")] or members
    pick = min(pool, key=lambda m: order.get(m.get("jury_credibility") or "na", 3))
    return _clean(pick.get("entity") or pick.get("item_text") or "theme")[:64] or "theme"


def _eligible(diag: dict, cfg: dict) -> bool:
    """A group is a candidate theme if it has enough signals AND enough INDEPENDENT leading juries.

    The independence bar is normally ``min_leading_juries`` (≥2) so no single award's idiosyncratic
    pick promotes a theme. Exception (D38, user decision): a ``solo_leading_sources`` jury — a
    high-credibility *regulatory/consensus* jury whose every signal is ALREADY a multi-expert
    decision (FDA priority approval) — can promote on its own, because the within-jury consensus
    substitutes for cross-jury independence. ``min_signals`` still applies (a real cluster, not one
    drug). Empty ``solo_leading_sources`` ⇒ the strict ≥2 rule, unchanged."""
    c = cfg["convergence"]
    if diag["n_signals"] < c["min_signals"]:
        return False
    if diag["n_leading_juries"] >= c["min_leading_juries"]:
        return True
    solo = set(c.get("solo_leading_sources") or [])
    return bool(solo and (set(diag.get("leading_sources", [])) & solo))


def build_convergence(signals: list[dict], cfg: dict) -> list[dict]:
    """Cluster + score + rank, no DB writes (pure, testable). Returns eligible candidate themes,
    score-desc, capped at top_k_themes. Each: {label, slug, score, diag, horizon, members,
    similarities}."""
    cc = cfg["convergence"]
    tau = cc["tau_converge"]
    groups = cluster_signals(signals, tau, max_cluster_size=cc.get("max_cluster_size"),
                             split_step=cc.get("split_tau_step", 0.08),
                             tau_ceiling=cc.get("tau_ceiling", 0.85))
    candidates = []
    for members in groups:
        diag = score_group(members, cfg)
        if not _eligible(diag, cfg):
            continue
        centroid = np.mean([np.asarray(m["vec"], dtype=np.float32) for m in members], axis=0)
        sims = {m["signal_id"]: _cosine(centroid, m["vec"]) for m in members}
        candidates.append({
            "label": _group_label(members),
            "slug": _slug(_group_label(members)),
            "score": diag["score"],
            "diag": diag,
            "horizon": estimate_horizon(diag["positions"], cfg),
            "members": members,
            "similarities": sims,
        })
    candidates.sort(key=lambda c: (-c["score"], -c["diag"]["n_leading_juries"]))
    return candidates[: cfg["convergence"]["top_k_themes"]]


def promote(conn, candidates: list[dict], *, embed_model: str | None = None) -> list[str]:
    """Write candidates as discovered ``themes`` (+ ``theme_convergence`` membership). Returns the
    theme_ids created. Idempotent on theme_id; re-running refreshes membership for that theme."""
    import json
    now = now_iso()
    created = []
    # Suffix only to avoid collisions WITHIN this run: the slug is stable per cluster, so the same
    # theme across runs keeps its theme_id and is refreshed via ON CONFLICT (idempotent). Two distinct
    # same-slug clusters in one run get -2/-3. (Suffixing against pre-existing DB themes would instead
    # spawn a duplicate every run — the D27 build bug.)
    assigned = set()
    for cand in candidates:
        base = f"disc:{cand['slug']}"
        theme_id = base
        i = 2
        while theme_id in assigned:
            theme_id = f"{base}-{i}"
            i += 1
        assigned.add(theme_id)
        descriptor = " | ".join(dict.fromkeys(m["item_text"] for m in cand["members"]))[:2000]
        keywords = list(dict.fromkeys(
            m["entity"] for m in cand["members"] if m.get("entity")))[:20]
        conn.execute(
            """
            INSERT INTO themes (theme_id, label, keywords, descriptor, embed_model,
                                horizon_years, horizon_confidence, discovered_from,
                                created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(theme_id) DO UPDATE SET
                label=excluded.label, keywords=excluded.keywords, descriptor=excluded.descriptor,
                horizon_years=excluded.horizon_years, horizon_confidence=excluded.horizon_confidence,
                discovered_from=excluded.discovered_from, updated_at=excluded.updated_at
            """,
            (theme_id, cand["label"], json.dumps(keywords), descriptor, embed_model,
             cand["horizon"]["years"], cand["horizon"]["confidence"], "jury_convergence", now, now),
        )
        conn.execute("DELETE FROM theme_convergence WHERE theme_id=?", (theme_id,))
        conn.executemany(
            "INSERT INTO theme_convergence (theme_id, signal_id, similarity) VALUES (?,?,?)",
            [(theme_id, m["signal_id"], round(float(cand["similarities"][m["signal_id"]]), 4))
             for m in cand["members"]],
        )
        created.append(theme_id)
    conn.commit()
    return created


def list_discovered(conn) -> list[dict]:
    """Discovered themes with their convergence diagnostics (for the CLI / report)."""
    rows = conn.execute(
        "SELECT theme_id, label, horizon_years, horizon_confidence FROM themes "
        "WHERE discovered_from='jury_convergence' ORDER BY theme_id"
    ).fetchall()
    out = []
    for r in rows:
        sigs = conn.execute(
            "SELECT s.diffusion_position AS pos, COUNT(DISTINCT s.source_id) AS n_src, COUNT(*) AS n "
            "FROM theme_convergence tc JOIN jury_signals s ON s.signal_id=tc.signal_id "
            "WHERE tc.theme_id=? GROUP BY s.diffusion_position", (r["theme_id"],)).fetchall()
        d = dict(r)
        d["by_position"] = {x["pos"] or "?": {"sources": x["n_src"], "signals": x["n"]} for x in sigs}
        out.append(d)
    return out
