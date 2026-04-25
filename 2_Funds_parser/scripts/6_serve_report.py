"""Module 6b Part (b) — local HTTP server for the final-ranking report.

Serves the rendered HTML on http://127.0.0.1:<port>/<quarter> and accepts
silent JSON saves of the user's checkbox selection on
PUT /selection/<quarter>. The browser does this via a fetch() call on
every checkbox toggle; the server writes the sidecar JSON
(``Outputs/final_ranking_<quarter>_selection.json``) atomically.

The pipeline (``scripts/6_score.py --selection-from-html PATH``) reads
that JSON before dispatch, so the user's selection round-trips with no
File System Access dialogs and no permission prompts.

Reads:
  Outputs/final_ranking_<quarter>.html              (served as-is)
  Outputs/final_ranking_<quarter>_selection.json    (returned on GET)

Writes:
  Outputs/final_ranking_<quarter>_selection.json    (atomic, on PUT)

Usage:
  python scripts/6_serve_report.py                  # auto-detect latest quarter
  python scripts/6_serve_report.py --quarter 2025Q4
  python scripts/6_serve_report.py --port 4609
  python scripts/6_serve_report.py --no-browser     # don't auto-open

Stop with CTRL+C. Bind is 127.0.0.1 only — never accessible off-host.

Spec: 2_Funds_parser/spec/module_6b_spec.md § Part (b).
Decision: 2_Funds_parser/spec/decisions.md § D49.
"""
from __future__ import annotations

import argparse
import http.server
import json
import logging
import socket
import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6b.selection_io import (  # noqa: E402
    load_selection_json,
    selection_json_path,
    stamp_update,
)

DEFAULT_PORT = 4609
OUTPUTS = PROJECT_ROOT / "Outputs"

_LOG = logging.getLogger("6_serve_report")


# ─────────────────────────────────────────────────────────────────────────────
# Routing helpers
# ─────────────────────────────────────────────────────────────────────────────


def _split_path(raw_path: str) -> list[str]:
    """Strip query string + leading slash; return path segments."""
    path = raw_path.split("?", 1)[0].split("#", 1)[0].strip("/")
    return path.split("/") if path else []


def _resolve_latest_quarter() -> str:
    """Pick the most recent quarter from context_packs.db (M5 output)."""
    db = PROJECT_ROOT / "context_packs.db"
    if not db.exists():
        raise SystemExit(f"context_packs.db not found at {db}")
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT quarter FROM context_packs GROUP BY quarter "
            "ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise SystemExit("No quarters in context_packs.db")
    return row[0]


# ─────────────────────────────────────────────────────────────────────────────
# Request handler
# ─────────────────────────────────────────────────────────────────────────────


class SelectionHandler(http.server.BaseHTTPRequestHandler):
    """Tiny REST-ish surface:

        GET  /                        → 302 → /<latest_quarter>
        GET  /<quarter>               → final_ranking_<quarter>.html
        GET  /selection/<quarter>     → final_ranking_<quarter>_selection.json
        PUT  /selection/<quarter>     → write JSON, return saved payload
    """

    server_version = "M6Selection/1.0"

    # Suppress noisy default logging; Logger above carries verbose mode.
    def log_message(self, fmt, *args):
        _LOG.debug("%s %s - %s", self.address_string(),
                   self.log_date_time_string(), fmt % args)

    # ── helpers ──
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, content_type: str, body_text: str) -> None:
        body = body_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    # ── routes ──
    def do_GET(self) -> None:                                    # noqa: N802
        parts = _split_path(self.path)

        # GET /
        if not parts:
            self._redirect(f"/{self.server.default_quarter}")
            return

        # GET /<quarter>
        if len(parts) == 1 and parts[0] != "selection":
            quarter = parts[0]
            html_path = OUTPUTS / f"final_ranking_{quarter}.html"
            if not html_path.exists():
                self._send_json(404, {
                    "error": f"No HTML for quarter {quarter}",
                    "expected": str(html_path),
                })
                return
            try:
                body = html_path.read_text(encoding="utf-8")
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            self._send_text(200, "text/html; charset=utf-8", body)
            return

        # GET /selection/<quarter>
        if len(parts) == 2 and parts[0] == "selection":
            quarter = parts[1]
            json_path = selection_json_path(
                OUTPUTS / f"final_ranking_{quarter}.html"
            )
            data = load_selection_json(json_path)
            if data is None:
                self._send_json(404, {
                    "error": f"No selection sidecar for {quarter}",
                    "hint": "Render the HTML report first to seed it.",
                })
                return
            self._send_json(200, data)
            return

        self._send_json(404, {"error": f"Unknown path {self.path}"})

    def do_PUT(self) -> None:                                    # noqa: N802
        parts = _split_path(self.path)

        if len(parts) == 2 and parts[0] == "selection":
            quarter = parts[1]
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 1_000_000:
                self._send_json(400, {"error": "Bad Content-Length"})
                return
            try:
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"error": f"Bad JSON: {e}"})
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "Top-level JSON must be object"})
                return
            # Pin quarter from URL (server is authority); clients can't override.
            payload["quarter"] = quarter
            json_path = selection_json_path(
                OUTPUTS / f"final_ranking_{quarter}.html"
            )
            try:
                saved = stamp_update(json_path, payload)
            except Exception as e:
                _LOG.exception("PUT /selection/%s failed", quarter)
                self._send_json(500, {"error": str(e)})
                return
            sel = saved.get("selected_tickers", [])
            all_t = saved.get("all_tickers", [])
            print(f"  saved: {len(sel)} of {len(all_t)} selected for {quarter}")
            self._send_json(200, {
                "ok": True,
                "saved_at": saved.get("updated_at"),
                "selected_count": len(sel),
                "total_count": len(all_t),
            })
            return

        self._send_json(404, {"error": f"Unknown PUT path {self.path}"})


# ─────────────────────────────────────────────────────────────────────────────
# Server bootstrap
# ─────────────────────────────────────────────────────────────────────────────


def _bind_with_fallback(
    bind: str, port: int, max_attempts: int = 50
) -> http.server.ThreadingHTTPServer:
    """Try `port`, increment up to max_attempts to avoid 'address in use'."""
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try_port = port + attempt
        try:
            srv = http.server.ThreadingHTTPServer((bind, try_port), SelectionHandler)
            return srv
        except OSError as e:
            last_err = e
            continue
    raise SystemExit(
        f"Could not bind to {bind}:{port}-{port + max_attempts - 1}: {last_err}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quarter", default=None,
                        help="Quarter to serve (default: latest in context_packs.db)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Bind address (default: localhost only — do not change)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not auto-open the report in the system browser")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    quarter = args.quarter or _resolve_latest_quarter()
    html_path = OUTPUTS / f"final_ranking_{quarter}.html"
    if not html_path.exists():
        raise SystemExit(
            f"No HTML for {quarter} at {html_path}. Run scripts/6_score.py first."
        )

    server = _bind_with_fallback(args.bind, args.port)
    server.default_quarter = quarter                          # type: ignore[attr-defined]

    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/{quarter}"

    print()
    print("=" * 64)
    print(f"  Module 6 selection editor")
    print(f"  URL:      {url}")
    print(f"  Quarter:  {quarter}")
    print(f"  Sidecar:  {selection_json_path(html_path).relative_to(PROJECT_ROOT)}")
    print()
    print(f"  Edit checkboxes in the browser — changes auto-save silently.")
    print(f"  Press CTRL+C here when you are done editing.")
    print("=" * 64)
    print()

    if not args.no_browser:
        # Defer the browser open so the server is already accepting connections.
        threading.Timer(0.4, lambda: _open_browser(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping selection editor server.")
    finally:
        server.server_close()
    return 0


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as e:
        _LOG.warning("Failed to open browser: %s", e)


# Suppress noisy "address in use" stack on rapid restart from tests.
def _quick_socket_close():                                          # pragma: no cover
    s = socket.socket()
    s.close()


if __name__ == "__main__":
    raise SystemExit(main())
