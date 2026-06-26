#!/usr/bin/env python
"""Wave-1 diffusion-radar pipeline (M2) + HTML render. ZERO Claude (Features section 8).

  ingest (arXiv specialist + GDELT mainstream + Wikipedia level)
    -> local embeddings (pluggable; sentence-transformers if installed, else hashing)
    -> membership to theme centroid -> N_spec; GDELT -> N_main
    -> beta_spec / p_main / diffusion_ratio  -> _intermediate_outputs/radar_report.html

Run from inside 5_Hype_parser/ (PYTHONPATH=src):
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_radar.py --open-browser
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_radar.py --theme rag --refresh -v
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_radar.py --no-fetch        # recompute from cache
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_radar.py --render-only      # just re-render stored series
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from hype_parser import db, diffusion as D, embed as E, render_radar
from hype_parser import themes as T
from hype_parser.ingest import (arxiv, clinicaltrials, edgar_fts, europepmc, gdelt,
                                hackernews, nih_reporter, nsf, patentsview, sbir, wikipedia)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "data/hype.db"
THEMES_CFG = "config/themes_seed.yaml"
DIFF_CFG = "config/diffusion.yaml"
OUT = "_intermediate_outputs/radar_report.html"

log = logging.getLogger("radar")


def _load_yaml(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _patents_api_key():
    """PatentsView API key from env or the repo-root .env (None -> patents source is skipped)."""
    key = os.environ.get("PATENTSVIEW_API_KEY")
    if key:
        return key
    for envp in (ROOT / ".env", ROOT.parent / ".env"):
        try:
            for line in envp.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("PATENTSVIEW_API_KEY"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except (FileNotFoundError, IndexError):
            continue
    return None


def _theme_is_stale(conn, theme_id, ttl_days):
    row = conn.execute(
        "SELECT MAX(computed_at) AS c FROM theme_series WHERE theme_id=?", (theme_id,)
    ).fetchone()
    if not row or not row["c"]:
        return True
    try:
        last = datetime.fromisoformat(row["c"])
    except ValueError:
        return True
    age = datetime.now(timezone.utc) - last
    return age.days >= ttl_days


def _ensure_embeddings(conn, embedder, doc_ids, batch=64):
    todo = []
    for did in doc_ids:
        r = conn.execute(
            "SELECT doc_id, title, abstract, embedding, embed_model FROM documents WHERE doc_id=?",
            (did,),
        ).fetchone()
        if r and (r["embedding"] is None or r["embed_model"] != embedder.name):
            todo.append(r)
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        texts = [f"{(c['title'] or '')}. {(c['abstract'] or '')}" for c in chunk]
        vecs = embedder.encode(texts)
        dim = vecs.shape[1]
        for c, v in zip(chunk, vecs):
            T.store_embedding(conn, c["doc_id"], E.to_blob(v), embedder.name, dim)
    conn.commit()
    return len(todo)


def _candidate_ids(conn, theme_id):
    return [r["doc_id"] for r in conn.execute(
        "SELECT doc_id FROM theme_documents WHERE theme_id=?", (theme_id,))]


def _compute_membership(conn, theme_id, candidate_ids, embedder, descriptor, keywords, tau):
    centroid = E.centroid(embedder.encode([descriptor] + list(keywords or [])))
    links, member_months = [], []
    for did in candidate_ids:
        r = conn.execute(
            "SELECT embedding, embed_dim, published_month FROM documents WHERE doc_id=?",
            (did,)).fetchone()
        if not r or r["embedding"] is None:
            continue
        vec = E.from_blob(r["embedding"], r["embed_dim"])
        cos = float(np.dot(vec, centroid))
        is_member = cos >= tau
        links.append((did, cos, is_member))
        if is_member and r["published_month"]:
            member_months.append(r["published_month"])
    T.link_theme_documents(conn, theme_id, links)
    return Counter(member_months)


def _process_theme(conn, t, *, embedder, params, ing, do_fetch):
    tid = t["id"]
    # --- N_main (GDELT) and Wikipedia: fresh or from stored series ---
    if do_fetch:
        candidate_ids = []
        end_year = datetime.now(timezone.utc).year
        if t.get("arxiv_query"):
            docs = arxiv.fetch_windows(
                t["arxiv_query"], start_year=ing["arxiv_start_year"], end_year=end_year,
                max_per_window=ing["arxiv_max_per_year"], sleep_s=ing["arxiv_sleep"])
            if docs:
                T.upsert_documents(conn, docs)
                candidate_ids += [d["doc_id"] for d in docs]
        # Wave 2 biomedical specialist sources (Europe PMC = bioRxiv/medRxiv/PubMed; ClinicalTrials)
        if t.get("europepmc_query"):
            epmc = europepmc.fetch_windows(
                t["europepmc_query"], start_year=ing["arxiv_start_year"], end_year=end_year,
                max_per_window=ing.get("europepmc_max_per_year", 150))
            if epmc:
                T.upsert_documents(conn, epmc)
                candidate_ids += [d["doc_id"] for d in epmc]
        if t.get("ctgov_query"):
            ct = clinicaltrials.fetch(
                t["ctgov_query"], max_results=ing.get("ctgov_max_results", 300),
                sleep_s=ing.get("ctgov_pause_s", 0))
            if ct:
                T.upsert_documents(conn, ct)
                candidate_ids += [d["doc_id"] for d in ct]
        # Wave 3 builder source: Hacker News (a document source -> feeds N_spec via membership)
        if t.get("hn_query"):
            hn = hackernews.fetch_windows(
                t["hn_query"], start_year=ing["arxiv_start_year"], end_year=end_year,
                max_per_window=ing.get("hn_max_per_year", 200))
            if hn:
                T.upsert_documents(conn, hn)
                candidate_ids += [d["doc_id"] for d in hn]
        # Wave 4 public-capital + IP sources (all document sources -> feed N_spec via membership)
        gmax = ing.get("grants_max_per_year", 100)
        for src_key, mod in (("nih_query", nih_reporter), ("nsf_query", nsf), ("sbir_query", sbir)):
            if t.get(src_key):
                gdocs = mod.fetch_windows(t[src_key], start_year=ing["arxiv_start_year"],
                                          end_year=end_year, max_per_window=gmax)
                if gdocs:
                    T.upsert_documents(conn, gdocs)
                    candidate_ids += [d["doc_id"] for d in gdocs]
        if t.get("patents_query"):
            pdocs = patentsview.fetch_windows(
                t["patents_query"], start_year=ing["arxiv_start_year"], end_year=end_year,
                api_key=_patents_api_key(), max_per_window=gmax)
            if pdocs:
                T.upsert_documents(conn, pdocs)
                candidate_ids += [d["doc_id"] for d in pdocs]
        candidate_ids = list(dict.fromkeys(candidate_ids))  # dedupe, preserve order
        if not candidate_ids:  # all fetches empty -> fall back to prior candidates
            candidate_ids = _candidate_ids(conn, tid)

        # Wave 3 EDGAR FTS: a SEPARATE yearly filing-count (theme_edgar) + ticker linkage.
        # Per-year for robustness against the endpoint's intermittent 500s. Not part of N_spec.
        if t.get("edgar_query"):
            edgar_yearly, edgar_tickers = edgar_fts.fetch_yearly(
                t["edgar_query"], start_year=ing["arxiv_start_year"], end_year=end_year,
                sleep_s=ing.get("edgar_pause_s", edgar_fts.RATE_LIMIT_SLEEP),
                ticker_pages=ing.get("edgar_ticker_pages", 1))
            if edgar_yearly:
                T.write_theme_edgar(conn, tid, edgar_yearly)
            if edgar_tickers:
                T.write_theme_tickers(conn, tid, edgar_tickers)
        end_dt = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        if t.get("gdelt_query"):
            if ing.get("gdelt_pause_s"):
                time.sleep(ing["gdelt_pause_s"])           # pace the rate-limited GDELT API
            gdelt_monthly = gdelt.fetch(t["gdelt_query"], start=ing["gdelt_start"], end=end_dt,
                                        retries=ing.get("gdelt_retries", 2),
                                        backoff_s=ing.get("gdelt_backoff_s", 5.0))
        else:
            gdelt_monthly = {}
        wiki_end = datetime.now(timezone.utc).strftime("%Y%m%d") + "00"
        wiki_monthly = wikipedia.fetch(t["wiki_article"], start=ing["wiki_start"], end=wiki_end) \
            if t.get("wiki_article") else {}

        # Non-destructive: a failed fetch (GDELT 429 / wiki 404 / EDGAR error) must not wipe data.
        stored = {r["period"]: r for r in T.read_series(conn, tid)}
        if not gdelt_monthly and stored:
            gdelt_monthly = {p: r["n_main"] for p, r in stored.items() if r["n_main"]}
            if gdelt_monthly:
                log.info("theme %s: GDELT fetch empty; kept %d prior N_main months",
                         tid, len(gdelt_monthly))
        if not wiki_monthly and stored:
            wiki_monthly = {p: r["wiki_views"] for p, r in stored.items() if r["wiki_views"]}
    else:
        candidate_ids = _candidate_ids(conn, tid)
        stored = T.read_series(conn, tid)
        gdelt_monthly = {r["period"]: r["n_main"] for r in stored}
        wiki_monthly = {r["period"]: r["wiki_views"] for r in stored}

    n_embedded = _ensure_embeddings(conn, embedder, candidate_ids)
    keywords = json.loads(t["keywords"]) if isinstance(t.get("keywords"), str) else t.get("keywords", [])
    n_spec_by_month = _compute_membership(
        conn, tid, candidate_ids, embedder, t.get("descriptor") or t["label"],
        keywords, params["tau_member"])

    all_months = set(n_spec_by_month) | set(gdelt_monthly) | set(wiki_monthly)
    months = D.month_range(min(all_months), max(all_months)) if all_months else []
    series = {mo: {"n_spec": n_spec_by_month.get(mo, 0),
                   "n_main": int(gdelt_monthly.get(mo, 0)),
                   "wiki_views": int(wiki_monthly.get(mo, 0))} for mo in months}
    T.write_series(conn, tid, series)
    T.set_theme_embed_model(conn, tid, embedder.name)
    log.info("theme %s: %d candidate docs (%d newly embedded), %d member-months, %d months",
             tid, len(candidate_ids), n_embedded, len(n_spec_by_month), len(months))


def _build_blocks(conn, theme_rows, params):
    blocks = []
    for t in theme_rows:
        tid = t["theme_id"]
        series = [dict(r) for r in T.read_series(conn, tid)]
        periods = [r["period"] for r in series]
        n_spec = {r["period"]: r["n_spec"] for r in series}
        n_main = {r["period"]: r["n_main"] for r in series}
        summary = D.summarize(periods, n_spec, n_main, L=params["L"],
                              tau_member=params["tau_member"],
                              beta_min=params["beta_min"], p_max=params["p_max"])
        kws = json.loads(t["keywords"]) if t["keywords"] else []
        n_docs = conn.execute(
            "SELECT COUNT(*) FROM theme_documents WHERE theme_id=?", (tid,)).fetchone()[0]
        src_counts = T.member_source_counts(conn, tid)
        blocks.append({
            "theme": {"label": t["label"], "keywords_str": ", ".join(kws)},
            "series": series, "summary": summary,
            "members": [dict(m) for m in T.member_docs(conn, tid, limit=8)],
            "n_docs": n_docs,
            "source_counts": src_counts,
            "tickers": [dict(r) for r in T.read_theme_tickers(conn, tid, limit=15)],
            "edgar_yearly": [dict(r) for r in T.read_theme_edgar(conn, tid)],
        })
    return blocks


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hype Parser diffusion radar (Wave 1).")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--themes-config", default=THEMES_CFG)
    p.add_argument("--diffusion-config", default=DIFF_CFG)
    p.add_argument("--out", default=OUT)
    p.add_argument("--theme", action="append", help="limit to theme id(s); repeatable")
    p.add_argument("--refresh", action="store_true", help="force refetch (ignore SWR TTL)")
    p.add_argument("--no-fetch", action="store_true", help="recompute from cache, no network")
    p.add_argument("--render-only", action="store_true", help="re-render stored series only")
    p.add_argument("--max-arxiv", type=int, help="override arxiv_max_per_year")
    p.add_argument("--arxiv-sleep", type=float, default=3.0, help="seconds between arxiv pages")
    p.add_argument("--open-browser", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    themes_cfg = _load_yaml(args.themes_config)["themes"]
    diff_cfg = _load_yaml(args.diffusion_config)
    params = {**diff_cfg["membership"], **diff_cfg["diffusion"]}
    ing = dict(diff_cfg["ingest"])
    ing["arxiv_sleep"] = args.arxiv_sleep
    if args.max_arxiv:
        ing["arxiv_max_per_year"] = args.max_arxiv

    if args.theme:
        wanted = set(args.theme)
        themes_cfg = [t for t in themes_cfg if t["id"] in wanted]

    conn = db.connect(args.db)
    embedder = None
    try:
        for t in themes_cfg:
            T.upsert_theme(conn, t)

        if not args.render_only:
            embedder = E.get_embedder(diff_cfg["embed"].get("model"))
            log.info("embedder: %s (semantic=%s)", embedder.name, embedder.semantic)
            ttl = ing.get("cache_ttl_days", 7)
            for t in themes_cfg:
                if args.no_fetch:
                    do_fetch = False
                else:
                    do_fetch = args.refresh or _theme_is_stale(conn, t["id"], ttl)
                _process_theme(conn, t, embedder=embedder, params=params, ing=ing,
                               do_fetch=do_fetch)

        # render (always reads back from DB -> single render path)
        ids = [t["id"] for t in themes_cfg]
        rows = [r for r in T.list_themes(conn) if r["theme_id"] in set(ids)]
        blocks = _build_blocks(conn, rows, params)
        try:
            reg = conn.execute(
                "SELECT label FROM registry_versions ORDER BY version_id DESC LIMIT 1"
            ).fetchone()
            reg = reg["label"] if reg else None
        except Exception:
            reg = None
        out_path = ROOT / args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        emb_name = embedder.name if embedder else (rows[0]["embed_model"] if rows else "n/a")
        semantic = embedder.semantic if embedder else False
        render_radar.render(blocks, embedder_name=emb_name or "n/a", semantic=semantic,
                            params=params, out_path=str(out_path), registry_version=reg)
        print(f"rendered {len(blocks)} themes -> {out_path}")

        if args.open_browser:
            url = out_path.as_uri()
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
