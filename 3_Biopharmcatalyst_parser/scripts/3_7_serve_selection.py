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
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUTS = PROJECT_ROOT / "Outputs"
BIOTECH_DB = PROJECT_ROOT / "data" / "biotech.db"
DEEP_DIVES_DB = PROJECT_ROOT / "data" / "claude_deep_dives.db"
SELECTION_JSON = OUTPUTS / "catalyst_scores_selection.json"

# D25 — live-price refresh.
from module_7.live_price import get_live_prices                  # noqa: E402

# D28 — hard-pass ticker allowlist for /api/live_price.
# Cached for HARD_PASS_TTL_S to avoid hitting biotech.db on every poll.
import threading                                                  # noqa: E402
import time as _time                                              # noqa: E402

_HARD_PASS_TTL_S = 300.0       # 5 min — refresh ~once per pipeline run
_HARD_PASS_LOCK = threading.Lock()
_hard_pass_cache: dict = {"tickers": frozenset(), "fetched_at": 0.0}


def _refresh_hard_pass_tickers() -> frozenset[str]:
    """Read the current rolling-view hard-pass + M8-rescued ticker set
    from biotech.db. D35 — extended to include `rescued = 1` so the
    Rescued tab's live-price polling reaches yfinance (matches the JS
    filter `r.hard_pass || r.rescued` in 3_6_render_scores.py)."""
    if not BIOTECH_DB.exists():
        return frozenset()
    try:
        with sqlite3.connect(str(BIOTECH_DB)) as cx:
            cx.row_factory = sqlite3.Row
            # Tolerate older biotech.db files that pre-date D35 (no
            # `rescued` column). Try the new form first, fall back.
            try:
                rows = cx.execute(
                    """
                    WITH latest AS (
                        SELECT ticker, drug, nct_number, next_catalyst_type,
                               MAX(snapshot_date) AS max_snap
                        FROM catalyst_scores
                        GROUP BY ticker, drug, nct_number, next_catalyst_type
                    )
                    SELECT DISTINCT cs.ticker
                    FROM catalyst_scores cs
                    JOIN latest l USING (ticker, drug, nct_number, next_catalyst_type)
                    WHERE cs.snapshot_date = l.max_snap
                      AND (cs.hard_pass = 1 OR cs.rescued = 1)
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                rows = cx.execute(
                    """
                    WITH latest AS (
                        SELECT ticker, drug, nct_number, next_catalyst_type,
                               MAX(snapshot_date) AS max_snap
                        FROM catalyst_scores
                        GROUP BY ticker, drug, nct_number, next_catalyst_type
                    )
                    SELECT DISTINCT cs.ticker
                    FROM catalyst_scores cs
                    JOIN latest l USING (ticker, drug, nct_number, next_catalyst_type)
                    WHERE cs.snapshot_date = l.max_snap AND cs.hard_pass = 1
                    """
                ).fetchall()
        return frozenset(r["ticker"].upper() for r in rows if r["ticker"])
    except Exception:
        return frozenset()


def _hard_pass_allowlist() -> frozenset[str]:
    """Return the cached set, refreshing past TTL."""
    with _HARD_PASS_LOCK:
        if (_time.monotonic() - _hard_pass_cache["fetched_at"]) > _HARD_PASS_TTL_S:
            _hard_pass_cache["tickers"] = _refresh_hard_pass_tickers()
            _hard_pass_cache["fetched_at"] = _time.monotonic()
        return _hard_pass_cache["tickers"]


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
        if parsed.path == "/api/live_price":
            # D25 — yfinance live prices, 60s TTL.
            # D28 — restricted to the current hard-pass ticker set
            # (biotech.db). Non-hard-pass tickers are silently dropped
            # so a misconfigured client can't burn yfinance budget on
            # the ~500 excluded catalysts.
            qs = parse_qs(parsed.query or "")
            tickers_raw = (qs.get("tickers") or [""])[0]
            requested = [t.strip().upper()
                         for t in tickers_raw.split(",") if t.strip()]
            if not requested:
                return self._json(400, {"error": "missing ?tickers=AAA,BBB"})
            allowlist = _hard_pass_allowlist()
            allowed = [t for t in requested if t in allowlist]
            dropped = [t for t in requested if t not in allowlist]
            force = (qs.get("force") or ["0"])[0] in ("1", "true")
            results = get_live_prices(allowed, force=force) if allowed else {}
            payload = {
                "prices": {t: {
                    "price_usd":      lp.price_usd,
                    "fetched_at_utc": lp.fetched_at_utc,
                    "source":         lp.source,
                    "error":          lp.error,
                } for t, lp in results.items()},
                "n_requested":   len(requested),
                "n_allowed":     len(allowed),
                "n_hard_pass_dropped": len(dropped),
                "dropped_sample": dropped[:5],
            }
            return self._json(200, payload)
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
    parser.add_argument("--open-browser", action="store_true",
                        help="Auto-launch the default browser at the catalyst_scores.html URL "
                             "~1.5s after the server starts (mirrors 0_Renderer pattern).")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), _Handler)
    url = f"http://{args.host}:{args.port}/catalyst_scores.html"
    print(f"[3_7_serve_selection] {url}")
    print(f"  selection sidecar: {SELECTION_JSON}")
    print(f"  deep_dives db:     {DEEP_DIVES_DB}")
    print(f"  Ctrl-C to stop.")

    if args.open_browser:
        # D29 — delayed browser launch via daemon Timer so the server has
        # time to bind before the browser issues its first GET.
        import webbrowser as _webbrowser
        def _open():
            try:
                _webbrowser.open(url)
                print(f"[3_7_serve_selection] launched default browser at {url}")
            except Exception as e:                               # noqa: BLE001
                print(f"[3_7_serve_selection] could not auto-launch browser: {e}")
        timer = threading.Timer(1.5, _open)
        timer.daemon = True
        timer.start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        print("[3_7_serve_selection] stopping.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
