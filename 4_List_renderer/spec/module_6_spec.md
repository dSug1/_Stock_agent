# Module 6 — Learning & ranking

**Status:** ✅ built (Phase 5, D32) · **Dir:** `4_List_renderer/`
**Decisions:** D5 (transparent weighted-feature model), D19 (context snapshot = trainable),
D21 (deterministic mute/boost rules), D22 (seen/unread). Spec §8.

M6 turns the captured signals (M7) into **order**: each highlight gets a predicted-interest
`score` and the board is rendered descending. The model is a plain weighted sum — no black box —
so every number is inspectable and the weights are user-tunable.

---

## 1. Components

| Piece | File | Role |
|---|---|---|
| Config | `config/ranking.yaml` | Base feature weights, recency decay, signal weights, learn gate, rules. |
| Scorer | `src/list_renderer/ranking.py` | Features + weighted-sum `score_item`/`rank_results`; rules; **fail-open**. |
| Offline fit | `ranking.fit_affinities` + `scripts/4_learn_ranking.py` | Re-fit source/topic affinities from `interactions`. |
| Wiring | `pipeline.build_payload_from_registry(rank=True)` + `server.serve` startup fit | Order results; re-fit on restart. |
| State | `ranking_state` (`user_id, feature, weight`) | Learned per-source / per-topic affinities. |

---

## 2. The model (D5)

`score(item) = Σ weight_f · feature_f`, weights from `ranking.yaml::weights`:

| Feature | Meaning | Source |
|---|---|---|
| `position_prior` | natural feed-order anchor (cold-start = unranked; recency proxy until M4) | render index |
| `recency` | `exp(-ln2 · age_h / half_life)`; `0.5` when undated | item `published_at` |
| `source_affinity` | learned net engagement with the item's source | `ranking_state` |
| `interest_match` | matches a declared interest (site/topic/query) | `interests` (M5) |
| `topic_affinity` | learned net engagement with the item's topics | `ranking_state` |
| `length` | mild preference for richer snippets | snippet length |
| `seen_penalty` | de-prioritize already-seen items (D22) | seen set from `interactions` |

Then **rules (D21)** apply: `mute_keywords` drop matching items (hard filter, pre-score);
`boost_topics` add a weight bump (post-score). Stable sort → equal scores keep feed order.

**Cold start.** With no signals/interests/dates, `position_prior` dominates and the board ≈ its
natural source/feed order; ordering only diverges as the user acts.

**Fail open.** Any error (missing config, bad row) returns the input order — ranking never blanks.

---

## 3. Offline learning (spec §8)

`4_learn_ranking.py` (free, offline — no network, no Claude) reads the append-only `interactions`
log and attributes each signal to the item's `source_id`/`topics` via the **`context_json`
snapshot (D19)** — which is exactly why that snapshot is stored. Signal weights:

```
like +3 · read_more +2 · open +1 · dwell≥30s +1 · scroll_past −0.5 · hide −3 · impression 0
```

Per source/topic net signal → `affinity = tanh(net / affinity_scale)` ∈ [-1, 1], written to
`ranking_state` (full re-fit, replacing prior rows). A **data-sufficiency gate**
(`learn.min_interactions = 15`) keeps the model cold-start until enough signal accrues (the
2_Funds M7-β/γ discipline). The server auto-re-fits on startup; the script can also run on a
schedule or after a reading session.

```bash
python scripts/4_learn_ranking.py            # fit, print per-source/topic affinities
python scripts/4_learn_ranking.py --dry-run  # report only, write nothing
```

---

## 4. Tuning & explainability

All weights are in `ranking.yaml` — edit and re-render, no code change. `rank_results(...,
include_breakdown=True)` attaches a `score_breakdown` (per-feature value + contribution + boost)
per item; it passes through `normalize_result` but is **off by default** to keep the sidecar lean
(a Phase-7 "why am I seeing this" toggle will surface it — spec §1b.1, §8 explainability).

---

## 5. Not in M6 (later)

Cross-source **dedup (M4)** — same story can still double-render · graduating the naïve
per-feature affinity to a real regression once rows accrue (spec §8) · `recency`/`topic` features
await M4 emitting `published_at`/`topics` · the Tier-B `rules` table + management UI (D21 v1 reads
rules from config) · interest **discovery** (M5 = Phase 6).
