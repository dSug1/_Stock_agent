"""Local HTTP server (M7) — serves the board + the /api/* contract.

Stdlib `http.server` (mirrors 0_Renderer / 3_Biopharmcatalyst_parser; no new
dependency). Binds 127.0.0.1 only — localhost is single-user and trusted, so
there is no auth/session in v1 (D27 §16.5). The same `/api/*` JSON contract is
framework-agnostic, so the hosted FastAPI port (D25) is a swap, not a rewrite.

Endpoints (D6/D15/§13.7):
  GET  /                       -> the stable template (Outputs/list_results.html)
  GET  /list_results_data.js   -> the static sidecar (file-mode parity)
  GET  /manifest.webmanifest   -> PWA manifest (D26)
  GET  /service-worker.js      -> PWA service worker (offline/instant paint)
  GET  /icon.svg               -> app icon
  GET  /api/board              -> LIST_DATA JSON  (?refresh=1, ?no_fetch=1)
  GET  /api/sources            -> all sources + on-board flags (selection UI)
  POST /api/sources            -> {source_id, enabled} | {selections:[...]} toggle
  POST /api/interact           -> {events:[...]} append-only signal capture (D19)
  POST /api/interest           -> {kind?, value} capture an interest (discovery=Phase 6)
  GET  /api/health             -> {ok:true}

Each request opens its own SQLite connection (ThreadingHTTPServer → one thread
per request; SQLite connections are not shareable across threads).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db as listdb
from . import interactions
from . import interests as interests_mod
from . import ranking
from .pipeline import build_board
from .render import render_sidecar
from .sources import seed_from_config, set_on_board

log = logging.getLogger("4_serve")

ROOT = Path(__file__).resolve().parents[2]          # 4_List_renderer/
OUTPUTS = ROOT / "Outputs"
TEMPLATE = OUTPUTS / "list_results.html"
SIDECAR = OUTPUTS / "list_results_data.js"
MANIFEST = OUTPUTS / "manifest.webmanifest"
SERVICE_WORKER = OUTPUTS / "service-worker.js"
ICON = OUTPUTS / "icon.svg"
SOURCES_CONFIG = ROOT / "config" / "sources.yaml"

# Static GET routes -> (file path, content-type). Served verbatim from Outputs/.
_STATIC: dict[str, tuple[Path, str]] = {
    "/": (TEMPLATE, "text/html; charset=utf-8"),
    "/list_results.html": (TEMPLATE, "text/html; charset=utf-8"),
    "/list_results_data.js": (SIDECAR, "application/javascript; charset=utf-8"),
    "/manifest.webmanifest": (MANIFEST, "application/manifest+json; charset=utf-8"),
    "/service-worker.js": (SERVICE_WORKER, "application/javascript; charset=utf-8"),
    "/icon.svg": (ICON, "image/svg+xml"),
}


def _bool_param(qs: dict, name: str) -> bool:
    vals = qs.get(name)
    if not vals:
        return False
    return vals[0].lower() in ("1", "true", "yes", "on")


class Handler(BaseHTTPRequestHandler):
    server_version = "Curator/1.0"

    # --- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):  # route through logging, quieter
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _conn(self):
        return listdb.connect(self.server.db_path)

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if content_type.startswith(("text/html", "application/javascript")):
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    # --- GET ------------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                return self._send_json({"ok": True})
            if path == "/api/board":
                return self._get_board()
            if path == "/api/sources":
                return self._get_sources()
            if path == "/api/interests":
                return self._get_interests()
            if path == "/api/debug":
                return self._get_debug()
            if path in _STATIC:
                return self._serve_static(path)
            self._send_json({"error": "not found", "path": path}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:  # never crash the server on one bad request
            log.exception("GET %s failed", path)
            self._send_json({"error": str(exc)}, 500)

    def _serve_static(self, path: str) -> None:
        file_path, ctype = _STATIC[path]
        if not file_path.exists():
            return self._send_json({"error": "missing file", "path": path}, 404)
        self._send_bytes(file_path.read_bytes(), ctype)

    def _get_board(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        refresh = _bool_param(qs, "refresh")
        conn = self._conn()
        try:
            # The explicit Refresh button re-learns from accumulated signals so
            # the re-ranked board reflects what you just liked/hid (M6). Passive
            # board loads do NOT re-fit (cheap + no surprise re-ordering).
            if refresh:
                rep = ranking.fit_affinities(conn, self.server.user_id)
                if rep.get("fitted"):
                    log.info("re-fit on refresh: %d interaction(s) -> %d source / %d topic affinities",
                             rep["n_interactions"], rep["n_sources"], rep["n_topics"])
                else:
                    log.info("re-fit on refresh: cold-start (%d/%d interactions)",
                             rep.get("n_interactions", 0), rep.get("min", 0))
            payload = build_board(
                conn,
                user_id=self.server.user_id,
                board_id=self.server.board_id,
                refresh=refresh,
                no_fetch=_bool_param(qs, "no_fetch"),
            )
        finally:
            conn.close()
        # Keep the on-disk sidecar in sync so file:// open shows the same board.
        try:
            render_sidecar(payload, OUTPUTS)
        except OSError:
            pass
        self._send_json(payload)

    def _get_debug(self) -> None:
        """Live snapshot of capture + learned state — the troubleshooting view."""
        conn = self._conn()
        try:
            uid, bid = self.server.user_id, self.server.board_id
            total = conn.execute(
                "SELECT COUNT(*) FROM interactions WHERE user_id=?", (uid,)
            ).fetchone()[0]
            by_action = {
                r["action"]: r["n"] for r in conn.execute(
                    "SELECT action, COUNT(*) AS n FROM interactions WHERE user_id=? "
                    "GROUP BY action ORDER BY n DESC", (uid,)
                ).fetchall()
            }
            recent = [
                {"item_id": r["item_id"], "action": r["action"],
                 "dwell_ms": r["dwell_ms"], "at": r["created_at"]}
                for r in conn.execute(
                    "SELECT item_id, action, dwell_ms, created_at FROM interactions "
                    "WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)
                ).fetchall()
            ]
            affinities = ranking.load_affinities(conn, uid)
            seen = interactions.seen_item_ids(conn, user_id=uid, board_id=bid)
            liked = interactions.liked_item_ids(conn, user_id=uid, board_id=bid)
            hidden = interactions.hidden_item_ids(conn, user_id=uid, board_id=bid)
        finally:
            conn.close()
        self._send_json({
            "interactions_total": total,
            "by_action": by_action,
            "recent": recent,
            "learned_affinities": {k: round(v, 4) for k, v in affinities.items()},
            "seen": len(seen), "liked": len(liked), "hidden": len(hidden),
        })

    def _get_sources(self) -> None:
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT s.id, s.kind, s.adapter, s.name, s.label, s.origin,
                       COALESCE(sub.enabled, 0) AS on_board
                FROM sources s
                LEFT JOIN subscriptions sub
                  ON sub.source_id = s.id AND sub.user_id=? AND sub.board_id=?
                WHERE s.enabled=1
                ORDER BY s.name
                """,
                (self.server.user_id, self.server.board_id),
            ).fetchall()
        finally:
            conn.close()
        self._send_json({
            "sources": [
                {
                    "id": r["id"], "kind": r["kind"], "adapter": r["adapter"],
                    "name": r["name"], "label": r["label"], "origin": r["origin"],
                    "on_board": bool(r["on_board"]),
                }
                for r in rows
            ]
        })

    def _get_interests(self) -> None:
        conn = self._conn()
        try:
            rows = interests_mod.list_interests(conn, user_id=self.server.user_id)
        finally:
            conn.close()
        self._send_json({"interests": rows})

    # --- POST -----------------------------------------------------------
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_body()
            if path == "/api/interact":
                return self._post_interact(body)
            if path == "/api/sources":
                return self._post_sources(body)
            if path == "/api/interest":
                return self._post_interest(body)
            self._send_json({"error": "not found", "path": path}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            log.exception("POST %s failed", path)
            self._send_json({"error": str(exc)}, 500)

    def _post_interact(self, body: dict) -> None:
        events = body.get("events")
        if events is None and body.get("action"):
            events = [body]            # allow a single event too
        events = events or []
        conn = self._conn()
        try:
            n = interactions.record_batch(
                conn, events,
                user_id=self.server.user_id, board_id=self.server.board_id,
            )
        finally:
            conn.close()
        if n:
            actions = ", ".join(f"{e.get('action')}:{e.get('item_id')}" for e in events
                                if e.get("action"))
            log.info("interact: captured %d event(s) [%s]", n, actions)
        self._send_json({"ok": True, "recorded": n})

    def _post_sources(self, body: dict) -> None:
        # Accept either {source_id, enabled} or {selections:[{source_id,enabled}]}
        selections = body.get("selections")
        if selections is None and "source_id" in body:
            selections = [{"source_id": body["source_id"],
                           "enabled": body.get("enabled", True)}]
        selections = selections or []
        conn = self._conn()
        try:
            for sel in selections:
                set_on_board(
                    conn, sel["source_id"], bool(sel.get("enabled", True)),
                    user_id=self.server.user_id, board_id=self.server.board_id,
                )
        finally:
            conn.close()
        self._send_json({"ok": True, "updated": len(selections)})

    def _post_interest(self, body: dict) -> None:
        # M5 (Phase 6): classify -> resolve -> register a discovered source, then
        # store the interest. Non-interactive + free: site feeds are discovered via
        # the free RSS shortcut; topics/tickers become a Google-News search source.
        # A site with no declared feed is stored 'pending' (billed Claude resolution
        # is deferred to the gated 4_resolve_source.py CLI, D4). Optional explicit
        # `kind` overrides classification.
        value = (body.get("value") or "").strip()
        kind = (body.get("kind") or "").strip() or None
        if not value:
            return self._send_json({"error": "value required"}, 400)
        conn = self._conn()
        try:
            report = interests_mod.add_interest(
                conn, value, kind=kind,
                user_id=self.server.user_id, board_id=self.server.board_id,
            )
        finally:
            conn.close()
        if report.get("source_id"):
            log.info("interest %r -> %s source %s", value, report["status"], report["source_id"])
        self._send_json({"ok": report.get("status") != "error", "value": value, **report})


def _refresh_loop(db_path, user_id, board_id, interval, stop_event):
    """Background SWR refresh (Phase 7): every `interval` seconds, re-fetch the
    board's network sources (warms the cache past TTL) and re-fit ranking from
    accumulated interactions. Out of the request path; daemon thread."""
    while not stop_event.wait(interval):
        try:
            conn = listdb.connect(db_path)
            try:
                build_board(conn, user_id=user_id, board_id=board_id, refresh=True)
                ranking.fit_affinities(conn, user_id)
            finally:
                conn.close()
            log.info("scheduled refresh: cache warmed + ranking re-fit")
        except Exception as exc:  # never let the scheduler kill the server
            log.warning("scheduled refresh failed: %s", exc)


def serve(
    db_path: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    user_id: str = listdb.LOCAL_USER_ID,
    board_id: str = listdb.DEFAULT_BOARD_ID,
    seed: bool = False,
    open_browser: bool = True,
    refresh_interval: int = 0,
) -> None:
    """Start the board server (blocking). Seeds sources on first run.
    `refresh_interval` > 0 enables the background SWR refresh scheduler."""
    db_path = Path(db_path)
    conn = listdb.connect(db_path)
    try:
        n_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        if seed or n_sources == 0:
            n = seed_from_config(conn, SOURCES_CONFIG)
            log.info("Seeded %d source(s) from %s", n, SOURCES_CONFIG.name)
        # Re-fit the interest model from accumulated signals (free, offline). Each
        # restart applies what the user did last session (M6 / Phase 5). Cold-start
        # below the data-sufficiency gate is a no-op.
        rep = ranking.fit_affinities(conn, user_id)
        if rep.get("fitted"):
            log.info("Ranking re-fit: %d interaction(s) -> %d source / %d topic affinities",
                     rep["n_interactions"], rep["n_sources"], rep["n_topics"])
    finally:
        conn.close()

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.db_path = db_path
    httpd.user_id = user_id
    httpd.board_id = board_id

    stop_event = threading.Event()
    if refresh_interval and refresh_interval > 0:
        threading.Thread(
            target=_refresh_loop,
            args=(db_path, user_id, board_id, refresh_interval, stop_event),
            daemon=True,
        ).start()
        log.info("Background refresh scheduler on: every %ds", refresh_interval)

    url = f"http://{host}:{port}/"
    log.info("Curator board server -> %s  (Ctrl-C to stop)", url)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        stop_event.set()
        httpd.server_close()
