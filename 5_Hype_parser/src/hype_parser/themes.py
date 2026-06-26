"""Theme registry + corpus/series storage for the diffusion engine (M2).

Storage only; the diffusion math is in diffusion.py and embeddings in embed.py.
"""

import json
import logging

from .db import now_iso
from .diffusion import month_key

log = logging.getLogger(__name__)


def upsert_theme(conn, t: dict) -> None:
    now = now_iso()
    conn.execute(
        """
        INSERT INTO themes (theme_id, label, keywords, descriptor, arxiv_query,
                            gdelt_query, wiki_article, embed_model, created_at, updated_at)
        VALUES (:theme_id, :label, :keywords, :descriptor, :arxiv_query,
                :gdelt_query, :wiki_article, :embed_model, :now, :now)
        ON CONFLICT(theme_id) DO UPDATE SET
            label=excluded.label, keywords=excluded.keywords, descriptor=excluded.descriptor,
            arxiv_query=excluded.arxiv_query, gdelt_query=excluded.gdelt_query,
            wiki_article=excluded.wiki_article, updated_at=excluded.updated_at
        """,
        {
            "theme_id": t["id"],
            "label": t.get("label", t["id"]),
            "keywords": json.dumps(t.get("keywords", [])),
            "descriptor": t.get("descriptor"),
            "arxiv_query": t.get("arxiv_query"),
            "gdelt_query": t.get("gdelt_query"),
            "wiki_article": t.get("wiki_article"),
            "embed_model": t.get("embed_model"),
            "now": now,
        },
    )
    conn.commit()


def set_theme_embed_model(conn, theme_id, model):
    conn.execute("UPDATE themes SET embed_model=?, updated_at=? WHERE theme_id=?",
                 (model, now_iso(), theme_id))
    conn.commit()


def list_themes(conn):
    return conn.execute("SELECT * FROM themes ORDER BY theme_id").fetchall()


def get_theme(conn, theme_id):
    return conn.execute("SELECT * FROM themes WHERE theme_id=?", (theme_id,)).fetchone()


def upsert_documents(conn, docs: list[dict]) -> int:
    """Insert/refresh documents (dedup on doc_id). Returns count written."""
    now = now_iso()
    n = 0
    for d in docs:
        conn.execute(
            """
            INSERT INTO documents (doc_id, source_id, title, abstract, url, published_at,
                                   published_month, fetched_at)
            VALUES (:doc_id, :source_id, :title, :abstract, :url, :published_at,
                    :published_month, :now)
            ON CONFLICT(doc_id) DO UPDATE SET
                title=excluded.title, abstract=excluded.abstract, url=excluded.url,
                published_at=excluded.published_at, published_month=excluded.published_month
            """,
            {
                "doc_id": d["doc_id"],
                "source_id": d["source_id"],
                "title": d.get("title"),
                "abstract": d.get("abstract"),
                "url": d.get("url"),
                "published_at": d.get("published_at"),
                "published_month": month_key(d.get("published_at", "")),
                "now": now,
            },
        )
        n += 1
    conn.commit()
    return n


def store_embedding(conn, doc_id, blob, model, dim):
    conn.execute(
        "UPDATE documents SET embedding=?, embed_model=?, embed_dim=? WHERE doc_id=?",
        (blob, model, dim, doc_id),
    )


def docs_without_embedding(conn, model):
    return conn.execute(
        "SELECT doc_id, title, abstract FROM documents "
        "WHERE embedding IS NULL OR embed_model IS NOT ? OR embed_model IS NULL",
        (model,),
    ).fetchall()


def link_theme_documents(conn, theme_id, rows):
    """rows: list of (doc_id, cosine, is_member). Replaces this theme's links."""
    conn.execute("DELETE FROM theme_documents WHERE theme_id=?", (theme_id,))
    conn.executemany(
        "INSERT INTO theme_documents (theme_id, doc_id, cosine, is_member) VALUES (?,?,?,?)",
        [(theme_id, did, float(cos), int(bool(mem))) for (did, cos, mem) in rows],
    )
    conn.commit()


def member_docs(conn, theme_id, limit=10):
    return conn.execute(
        "SELECT d.doc_id, d.title, d.url, d.published_month, td.cosine "
        "FROM theme_documents td JOIN documents d ON d.doc_id = td.doc_id "
        "WHERE td.theme_id=? AND td.is_member=1 ORDER BY td.cosine DESC LIMIT ?",
        (theme_id, limit),
    ).fetchall()


def write_series(conn, theme_id, series: dict):
    """series: {period: {n_spec, n_main, wiki_views, edgar_filings}}. Replaces the theme's rows."""
    now = now_iso()
    conn.execute("DELETE FROM theme_series WHERE theme_id=?", (theme_id,))
    conn.executemany(
        "INSERT INTO theme_series "
        "(theme_id, period, n_spec, n_main, wiki_views, edgar_filings, computed_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [(theme_id, p, v.get("n_spec", 0), v.get("n_main", 0), v.get("wiki_views", 0),
          v.get("edgar_filings", 0), now) for p, v in sorted(series.items())],
    )
    conn.commit()


def read_series(conn, theme_id):
    return conn.execute(
        "SELECT period, n_spec, n_main, wiki_views, edgar_filings FROM theme_series "
        "WHERE theme_id=? ORDER BY period", (theme_id,)
    ).fetchall()


def write_theme_tickers(conn, theme_id, ticker_counts: dict):
    """ticker_counts: {ticker: n_mentions}. Replaces the theme's ticker linkage rows."""
    now = now_iso()
    conn.execute("DELETE FROM theme_tickers WHERE theme_id=?", (theme_id,))
    conn.executemany(
        "INSERT INTO theme_tickers (theme_id, ticker, source_id, n_mentions, updated_at) "
        "VALUES (?,?,?,?,?)",
        [(theme_id, tk, "edgar_fts", int(n), now) for tk, n in ticker_counts.items()],
    )
    conn.commit()


def read_theme_tickers(conn, theme_id, limit=15):
    return conn.execute(
        "SELECT ticker, n_mentions FROM theme_tickers WHERE theme_id=? "
        "ORDER BY n_mentions DESC, ticker LIMIT ?", (theme_id, limit)
    ).fetchall()


def write_theme_edgar(conn, theme_id, yearly_counts: dict):
    """yearly_counts: {year:int -> n_filings:int}. Replaces the theme's EDGAR yearly rows."""
    now = now_iso()
    conn.execute("DELETE FROM theme_edgar WHERE theme_id=?", (theme_id,))
    conn.executemany(
        "INSERT INTO theme_edgar (theme_id, year, n_filings, updated_at) VALUES (?,?,?,?)",
        [(theme_id, int(y), int(n), now) for y, n in sorted(yearly_counts.items())],
    )
    conn.commit()


def read_theme_edgar(conn, theme_id):
    return conn.execute(
        "SELECT year, n_filings FROM theme_edgar WHERE theme_id=? ORDER BY year", (theme_id,)
    ).fetchall()


def member_source_counts(conn, theme_id):
    """{source_id: n_member_docs} for a theme — which specialist sources feed N_spec."""
    return {r["source_id"]: r["n"] for r in conn.execute(
        "SELECT d.source_id, COUNT(*) AS n FROM theme_documents td "
        "JOIN documents d ON d.doc_id = td.doc_id "
        "WHERE td.theme_id=? AND td.is_member=1 GROUP BY d.source_id ORDER BY n DESC",
        (theme_id,))}
