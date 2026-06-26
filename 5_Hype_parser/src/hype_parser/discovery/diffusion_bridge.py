"""Diffusion bridge — give each DISCOVERED theme zero-Claude queries so the existing diffusion engine
can measure its corpus β_spec / p_main curve (spec §4 step 4 / §9 step 5).

Convergence (D27) promotes themes with a descriptor + entity keywords but **no diffusion queries**, so
the radar can't ingest a literature corpus for them. This derives keyword queries from the theme's
convergent **label** (the MIT-TR-style topic — weighted heavily) enriched with salient terms from the
member jury texts. The radar then measures a discovered theme exactly like a hand-seeded one, which is
what the §9-step-5 back-test compares.

The derivation is a **labelled-crude heuristic** (frequency over content terms) — spec §6 explicitly
allows one optional cheap Claude call to *name*/query a promoted theme; that is the quality upgrade.
Zero Claude here. Query text columns already exist on `themes` (migration_3), so no schema change.
"""

import json
import logging
import re
from collections import Counter

from ..db import now_iso

log = logging.getLogger(__name__)

# Framing/filler dropped before extracting a topic — MIT-TR headline scaffolding + English stopwords +
# startup-blurb generics (so member-text terms aren't all "platform/data/ai-powered").
_STOP = set("""
a an the of for to and or in on at by with from as is are be been being was were will would can could
this that these those it its their our your his her you we they he she them us me my mine i
whats what s next new now how why who when where which into out up down over under about
future rise era age world first best top leading meet inside guide explained
company companies startup startups platform platforms solution solutions service services product
products technology technologies tech app apps tool tools data ai powered based using build building
make making help helping enable enabling provider providers software hardware system systems
giving real people get getting use used more most very real
""".split())

_WORD = re.compile(r"[a-z0-9][a-z0-9\-]+")


def _content_tokens(text: str) -> list[str]:
    toks = _WORD.findall((text or "").lower())
    return [t for t in toks if len(t) > 2 and not t.isdigit() and t not in _STOP]


def _phrases(text: str) -> list[str]:
    """Content unigrams + adjacent content bigrams from one text (bigrams are more topic-specific)."""
    toks = _content_tokens(text)
    bigrams = [f"{a} {b}" for a, b in zip(toks, toks[1:])]
    return toks + bigrams


def _dedupe_overlap(terms: list[str]) -> list[str]:
    """Drop a unigram already contained in a kept (longer) phrase, preserving order/priority."""
    kept = []
    for t in terms:
        if " " not in t and any(t in p.split() for p in kept if " " in p):
            continue
        if t not in kept:
            kept.append(t)
    return kept


def derive_query(label: str, member_texts=None, *, top_k: int = 4) -> dict:
    """Zero-Claude diffusion queries for a discovered theme. **Label-first**: the convergent label IS
    the topic, so the corpus query is built from it; member jury texts (startup blurbs) only fill in
    when the label is too sparse — they DRIFT (e.g. YC sector tags 'defense'/'saas' pull an off-topic
    corpus), so they never override the label. Bigrams rank above unigrams; near-duplicates dropped.

    Returns {keywords, arxiv_query, gdelt_query, wiki_article, descriptor}."""
    label_counts = Counter()
    for ph in _phrases(label):
        label_counts[ph] += 2 if " " in ph else 1                   # prefer specific bigrams
    terms = _dedupe_overlap([t for t, _ in label_counts.most_common()])[:top_k]

    if not terms and member_texts:                                  # ONLY a content-less label enriches
        member_counts = Counter()                                   #   from members (else they drift —
        for text in member_texts:                                   #   YC sector tags pull off-topic)
            for ph in _phrases(text):
                member_counts[ph] += 2 if " " in ph else 1
        terms = _dedupe_overlap([t for t, _ in member_counts.most_common()])[:top_k]
    if not terms:                                                   # degenerate -> raw label fallback
        terms = [(_content_tokens(label) or [label.strip().lower() or "theme"])[0]]
    arxiv = " OR ".join(f'all:"{t}"' for t in terms)
    gdelt = " OR ".join(f'"{t}"' for t in terms)
    return {
        "keywords": terms,
        "arxiv_query": arxiv,
        "gdelt_query": gdelt,
        "wiki_article": terms[0].title(),                           # may 404; radar handles wiki misses
        # A TOPIC-facing centroid descriptor. The promote() descriptor is the jury *blurbs*
        # (startup-speak) — too far from research abstracts at tau_member, so membership came back empty.
        # The radar centroid is encode([descriptor] + keywords); a topic descriptor + the query terms
        # aligns it with the literature instead.
        "descriptor": f"{label.strip()}. " + ", ".join(terms),
    }


def theme_member_texts(conn, theme_id: str, *, limit: int = 400) -> list[str]:
    """Member jury item_texts backing a discovered theme (capped — enrichment only)."""
    return [r["item_text"] for r in conn.execute(
        "SELECT s.item_text FROM theme_convergence tc JOIN jury_signals s ON s.signal_id=tc.signal_id "
        "WHERE tc.theme_id=? LIMIT ?", (theme_id, limit))]


def assign_diffusion_queries(conn, *, top_k: int = 4, overwrite: bool = False) -> int:
    """Populate arxiv/gdelt/wiki queries + keywords on every discovered theme (so the radar can measure
    it). Skips themes that already have an arxiv_query unless overwrite. Returns rows updated."""
    rows = conn.execute(
        "SELECT theme_id, label, arxiv_query FROM themes WHERE discovered_from='jury_convergence'"
    ).fetchall()
    n = 0
    for r in rows:
        if r["arxiv_query"] and not overwrite:
            continue
        q = derive_query(r["label"], theme_member_texts(conn, r["theme_id"]), top_k=top_k)
        conn.execute(
            "UPDATE themes SET keywords=?, arxiv_query=?, gdelt_query=?, wiki_article=?, "
            "descriptor=?, updated_at=? WHERE theme_id=?",
            (json.dumps(q["keywords"]), q["arxiv_query"], q["gdelt_query"], q["wiki_article"],
             q["descriptor"], now_iso(), r["theme_id"]))
        n += 1
    conn.commit()
    log.info("assigned diffusion queries to %d discovered themes", n)
    return n


def load_discovered_radar_themes(conn) -> list[dict]:
    """Discovered themes shaped as radar theme-dicts (the `_process_theme` contract: id + *_query +
    descriptor + keywords). Only those with an arxiv_query (i.e. assign_diffusion_queries has run)."""
    rows = conn.execute(
        "SELECT theme_id, label, keywords, descriptor, arxiv_query, gdelt_query, wiki_article "
        "FROM themes WHERE discovered_from='jury_convergence' AND arxiv_query IS NOT NULL "
        "ORDER BY theme_id").fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["theme_id"], "label": r["label"],
            "keywords": json.loads(r["keywords"]) if r["keywords"] else [],
            "descriptor": r["descriptor"],
            "arxiv_query": r["arxiv_query"], "gdelt_query": r["gdelt_query"],
            "wiki_article": r["wiki_article"],
        })
    return out
