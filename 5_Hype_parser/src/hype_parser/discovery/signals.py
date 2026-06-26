"""jury_signals storage + local embedding (theme discovery, schema v8).

A *jury signal* is one expert recognition — an award/finalist/breakthrough-designation/RFS that
names a company or technology. Parsers (``parsers.py``) emit dicts; this module dedups them into the
``jury_signals`` table and embeds the short ``item_text`` locally (the same MiniLM encoder the
diffusion engine uses), so convergence (``convergence.py``) is cosine geometry over a few thousand
tiny vectors — not volume clustering.

Idempotent: dedup on ``(source_id, year, item_hash)`` where ``item_hash`` is a stable digest of the
recognition's text. Re-ingesting an unchanged edition writes nothing. Fail-open is the parsers' job;
here we just store.
"""

import hashlib
import logging

from ..db import now_iso
from ..embed import from_blob, get_embedder, to_blob

log = logging.getLogger(__name__)


def signal_hash(item_text: str, entity: str | None = None) -> str:
    """Stable dedup key for a recognition within a (source_id, year). Entity (if any) anchors it so
    a reworded blurb for the same company doesn't double-count."""
    basis = (entity or item_text or "").strip().lower()
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def upsert_signals(conn, signals: list[dict]) -> dict:
    """Insert jury signals (dedup on source_id+year+item_hash). Returns {inserted, skipped}.

    Each signal dict: source_id, item_text (required); optional diffusion_position, jury_credibility,
    year, entity, entity_type, url. Position/credibility default to whatever the parser passed (usually
    copied from the source registry row). Embedding is filled later by ``embed_pending``.
    """
    now = now_iso()
    inserted = skipped = 0
    for s in signals:
        text = (s.get("item_text") or "").strip()
        if not s.get("source_id") or not text:
            continue
        h = signal_hash(text, s.get("entity"))
        cur = conn.execute(
            """
            INSERT INTO jury_signals
                (source_id, diffusion_position, jury_credibility, year, item_text, entity,
                 entity_type, url, item_hash, ingested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (source_id, year, item_hash) DO NOTHING
            """,
            (s["source_id"], s.get("diffusion_position"), s.get("jury_credibility"),
             s.get("year"), text, s.get("entity"), s.get("entity_type"), s.get("url"), h, now),
        )
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


def annotate_from_registry(conn) -> int:
    """Backfill diffusion_position / jury_credibility on any signal whose source row carries them but
    the parser left blank (so the credibility weighting in convergence always has the registry value).
    Returns rows updated."""
    cur = conn.execute(
        """
        UPDATE jury_signals
           SET diffusion_position = COALESCE(jury_signals.diffusion_position,
                   (SELECT diffusion_position FROM sources s WHERE s.source_id = jury_signals.source_id)),
               jury_credibility   = COALESCE(jury_signals.jury_credibility,
                   (SELECT jury_credibility FROM sources s WHERE s.source_id = jury_signals.source_id))
         WHERE diffusion_position IS NULL OR jury_credibility IS NULL
        """
    )
    conn.commit()
    return cur.rowcount


def embed_pending(conn, embedder=None) -> int:
    """Embed every signal lacking a current-model embedding. Returns count embedded."""
    embedder = embedder or get_embedder()
    rows = conn.execute(
        "SELECT signal_id, item_text FROM jury_signals "
        "WHERE embedding IS NULL OR embed_model IS NOT ? OR embed_model IS NULL",
        (embedder.name,),
    ).fetchall()
    if not rows:
        return 0
    vecs = embedder.encode([r["item_text"] for r in rows])
    for r, v in zip(rows, vecs):
        conn.execute(
            "UPDATE jury_signals SET embedding=?, embed_model=?, embed_dim=? WHERE signal_id=?",
            (to_blob(v), embedder.name, int(len(v)), r["signal_id"]),
        )
    conn.commit()
    log.info("embedded %d jury signals with %s", len(rows), embedder.name)
    return len(rows)


def load_embedded_signals(conn, model: str) -> list[dict]:
    """Signals with an embedding under ``model``, decoded to numpy vectors — convergence input."""
    rows = conn.execute(
        "SELECT signal_id, source_id, diffusion_position, jury_credibility, year, item_text, "
        "       entity, entity_type, url, embedding, embed_dim "
        "FROM jury_signals WHERE embedding IS NOT NULL AND embed_model = ? "
        "ORDER BY signal_id",
        (model,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["vec"] = from_blob(r["embedding"], r["embed_dim"])
        out.append(d)
    return out


def signal_counts(conn) -> dict:
    """Coverage summary: total + per diffusion_position + distinct sources."""
    total = conn.execute("SELECT COUNT(*) FROM jury_signals").fetchone()[0]
    by_pos = {r["diffusion_position"] or "?": r["n"] for r in conn.execute(
        "SELECT diffusion_position, COUNT(*) AS n FROM jury_signals GROUP BY diffusion_position")}
    n_sources = conn.execute("SELECT COUNT(DISTINCT source_id) FROM jury_signals").fetchone()[0]
    return {"total": total, "by_position": by_pos, "n_sources": n_sources}
