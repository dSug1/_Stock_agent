"""Module 7 — local HTTP server for the selection editor + raw_text serving.

Two responsibilities:

  1. Selection-checkbox round-trip — GET/POST `/api/selection`. Reads/writes
     `Outputs/catalyst_scores_selection.json`. The HTML's checkbox-change
     handlers POST here so changes survive across pipeline runs.

  2. Raw Claude reply on-demand — GET `/api/raw_text?run_id=N`. The sidecar
     `_data.js` carries a `raw_text_id` (the run_id of the latest deep_dive
     for that PK) so the "view raw JSON" link can fetch the verbatim text
     lazily. Storing 30-80 KB raw_text in the .js sidecar would 10-30× the
     file size for no first-paint value.

Static files (the HTML + the .js sidecar) are served from `Outputs/` so a
browser pointed at `http://localhost:7034/catalyst_scores.html` loads the
report.

Per memory `feedback_browser_writes_via_local_server` — use a local HTTP
server, not the FSA API.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_serve_selection.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_serve_selection.py --port 7034
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
OUTPUTS = PROJECT_ROOT / "Outputs"
DEEP_DIVES_DB = PROJECT_ROOT / "data" / "claude_deep_dives.db"
SELECTION_JSON = OUTPUTS / "catalyst_scores_selection.json"


def _load_selection() -> dict:
    if not SELECTION_JSON.exists():
        return {"version": 1, "selected_tickers": [], "all_tickers": []}
    try:
        return json.loads(SELECTION_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "selected_tickers": [], "all_tickers": []}


def _save_selection(payload: dict) -> None:
    SELECTION_JSON.parent.mkdir(parents=True, exist_ok=True)
    SELECTION_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _fetch_raw_text(run_id: int) -> tuple[str, str]:
    """Return (ticker, raw_text) for the deep_dives row at run_id.

    Returns ("", "") when not found (caller emits 404).
    """
    if not DEEP_DIVES_DB.exists():
        return ("", "")
    with sqlite3.connect(str(DEEP_DIVES_DB)) as cx:
        cx.row_factory = sqlite3.Row
        row = cx.execute(
            "SELECT ticker, raw_text FROM deep_dives WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()
    if row is None:
        return ("", "")
    return (row["ticker"] or "", row["raw_text"] or "")


class _Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):                                  # quieter logs
        sys.stdout.write(f"[serve] {self.address_string()} - " + (fmt % args) + "\n")

    # ── routing ─────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/selection":
            return self._json(200, _load_selection())
        if parsed.path == "/api/raw_text":
            qs = parse_qs(parsed.query or "")
            try:
                run_id = int((qs.get("run_id") or ["0"])[0])
            except ValueError:
                return self._json(400, {"error": "run_id must be int"})
            ticker, text = _fetch_raw_text(run_id)
            if not text:
                return self._json(404, {"error": f"no raw_text for run_id={run_id}"})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("X-Ticker", ticker)
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))
            return
        # Static fall-through — serve files under Outputs/.
        return self._serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/selection":
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except json.JSONDecodeError:
                return self._json(400, {"error": "body must be valid JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "body must be a JSON object"})
            # Merge keys we care about.
            current = _load_selection()
            for k in ("selected_tickers", "all_tickers", "modifier_weights"):
                if k in body:
                    current[k] = body[k]
            current["version"] = current.get("version") or 1
            _save_selection(current)
            return self._json(200, {"ok": True, "saved": current})
        return self._json(404, {"error": "not found"})

    # ── helpers ─────────────────────────────────────────────

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        # Map '/' → 'catalyst_scores.html' for convenience.
        rel = path.lstrip("/") or "catalyst_scores.html"
        target = OUTPUTS / rel
        # Path-traversal guard.
        try:
            resolved = target.resolve()
            outputs_resolved = OUTPUTS.resolve()
            if not str(resolved).startswith(str(outputs_resolved)):
                return self._json(403, {"error": "forbidden"})
        except Exception:
            return self._json(404, {"error": "bad path"})
        if not target.exists() or not target.is_file():
            return self._json(404, {"error": f"file not found: {rel}"})
        mime = {
            ".html": "text/html; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
        }.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=7034)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"[3_7_serve_selection] http://{args.host}:{args.port}/catalyst_scores.html")
    print(f"  selection sidecar: {SELECTION_JSON}")
    print(f"  deep_dives db:     {DEEP_DIVES_DB}")
    print(f"  Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        print("[3_7_serve_selection] stopping.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
