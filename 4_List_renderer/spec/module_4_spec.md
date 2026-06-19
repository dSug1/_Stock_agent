# Module 4 — Normalization & dedup

**Status:** ✅ built (L1/L2 attributes + cross-source dedup, D34) · **Dir:** `4_List_renderer/`
**Decisions:** D33 (staged attribute extraction), D34 (this build), D19 (context snapshot uses
topics), D21 (rules name topics), D5 (recency/topic ranking features). Spec §6 M4.

M4 is the layer between **fetch (M2)** and **rank (M6)**: it turns the merged, heterogeneous
Highlight list into normalized, attributed, de-duplicated rows. It is deterministic, local, and
has **no network and no LLM** (D33 layers L1+L2); it **fails open** (any error → the input list
unchanged, never blanks the board).

---

## 1. Components

| Piece | File | Role |
|---|---|---|
| Vocabulary | `config/topics.yaml` | Controlled, evolving topic → keywords map (L2). |
| Normalizer | `src/list_renderer/normalize.py` | `tag_topics`, `enrich`, `canonical_url`, `dedup`, `normalize`. |
| Adapter L1 | `adapters/_rss_parse.py::item_date_iso`, `adapters/http_api.py::_to_iso` | Emit ISO `published_at`. |
| Wiring | `pipeline.build_payload_from_registry` | `normalize()` after merge, before rank. |

---

## 2. Attributes (D33 L1 + L2)

- **L1 structural (free, from the source).** `published_at` — ISO-8601 UTC, emitted by the
  adapters (RSS `pubDate`/Atom `updated`; JSON epoch or ISO via `fields.published`). Feeds the
  `recency` ranking feature (neutral 0.5 when a feed omits a date). `domain` is derived in
  `enrich`. (`language` detection is deferred — passed through, NN-3.)
- **L2 keyword topic tagging.** `tag_topics(text, vocab)` does case-insensitive **word-boundary**
  matching of the controlled vocabulary over title+snippet → `item.topics`. Feeds `topic_affinity`
  + topic `interest_match` (M6) and gives D21 mute/boost named topics. The vocab is
  **controlled-but-evolving** (D33): seed small, add over time, review in batches — never
  hand-frozen; prune topics that never correlate with signal.

Both attributes flow into the sidecar (`topics`) and into interaction `context_json`, so
`ranking.fit_affinities` now learns per-topic affinity (D19).

---

## 3. Cross-source dedup

`dedup(items)` collapses the same story arriving from multiple feeds (it previously
double-rendered). Three tiers, first occurrence wins (preserves feed/ranking order):
1. **Exact canonical URL** — `canonical_url` lowercases host, drops `www.`, strips trailing slash
   and tracking params (`utm_*`, `fbclid`, …), sorts the rest.
2. **Exact normalized title** — whitespace-collapsed, lowercased.
3. **Near-duplicate title** — token-set Jaccard ≥ 0.85, guarded by a ≥4-token floor to avoid
   merging short, generic headlines.

Merged source ids are recorded on `dup_sources` (for a future "also from X" UI / telemetry).
Dedup is **lexical** (URL/title); semantic near-dups across very different headlines wait for the
L3 embedding engine (v2).

---

## 4. Order in the pipeline

```
fetch each source (M2, SWR cache) → stamp stable id → normalize (enrich + dedup, M4)
   → rank descending (M6) → build_payload → sidecar / GET /api/board
```

Enrichment mutates only the per-render dicts (the SWR cache is untouched), so re-tagging is always
current with the vocabulary and never requires a re-fetch.

---

## 5. Not in M4 (later)

`language` detection (needs a lib) · persisting `topics_json`/`domain` to the `items` table
(render-time tagging suffices for ranking today) · **L3 embeddings** (taxonomy-free similarity +
semantic dedup, the v2 follow-on) · **L4 generative-LLM tagging** for stance/novelty/quality
(optional, batched + cached per item behind the D24 seam).
