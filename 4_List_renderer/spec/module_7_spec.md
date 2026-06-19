# Module 7 — Local server & UI interactions

**Status:** ✅ built (Phase 4, D31) · **Dir:** `4_List_renderer/`
**Decisions:** D6 (server + append-only signals), D15/§13.7 (delivery), D19 (context snapshot),
D22 (seen/unread), D25/D26 (stack + PWA), D27 §16.5 (localhost = trusted).

M7 turns the one-shot renderer into a **live, interactive board**: a local server delivers the
board over HTTP and captures what the user does with it. Those signals are the training data the
ranking model (M6, Phase 5) fits against.

---

## 1. Components

| Piece | File | Role |
|---|---|---|
| Server | `src/list_renderer/server.py` | stdlib `http.server` (`ThreadingHTTPServer`), 127.0.0.1; serves the template + `/api/*`. |
| CLI | `scripts/4_serve.py` | Arg-parse + `serve(...)`; `--port/--host/--seed/--no-open/-v`. |
| Launcher | `run_4_List_server.bat` | Sets `PYTHONPATH=src`, runs the server, opens the browser. |
| Signals | `src/list_renderer/interactions.py` | Append-only writes + `hidden/seen/liked` derived views. |
| Delivery | `src/list_renderer/pipeline.py::build_board` | Registry render + hide-filter + seen/liked/meta. |
| Client | `Outputs/list_results.html` (v2) | Loader shim, interaction hooks, source panel, PWA. |
| PWA | `Outputs/{manifest.webmanifest, service-worker.js, icon.svg}` | Installable + offline. |

---

## 2. The `/api/*` contract (framework-agnostic — D25 guardrail)

| Method + path | Body / query | Returns |
|---|---|---|
| `GET /api/board` | `?refresh=1`, `?no_fetch=1` | `LIST_DATA` (§4.3) + `meta.{mode,seen,liked,n_hidden}`. Also re-writes the on-disk sidecar. |
| `GET /api/sources` | — | `{sources:[{id,kind,adapter,name,label,origin,on_board}]}`. |
| `POST /api/sources` | `{source_id,enabled}` or `{selections:[…]}` | `{ok,updated}` — toggles board subscription. |
| `POST /api/interact` | `{events:[{item_id,action,value?,dwell_ms?,context?}]}` | `{ok,recorded}` — append-only. |
| `POST /api/interest` | `{kind?,value}` | `{ok,…}` — stores `interests` (`pending`); discovery=Phase 6. |
| `GET /api/health` | — | `{ok:true}`. |

The same contract is the hosted target (§14); only the impl (FastAPI + Postgres + workers) and
auth change. Local v1 is stdlib + SQLite + no auth (localhost trusted, D27 §16.5).

---

## 3. Interaction model

- **Actions:** `impression, open, read_more, like, hide, dwell, scroll_past` (+`unhide`).
- **Append-only (D6):** never overwritten — the audit trail keeps ranking re-fits honest.
- **Context snapshot (D19):** every row stores `context_json` = the item's verbatim
  feature/score context at event time (`source_id, position, score, topics, published_at,
  title, seen`), so the model is reconstructible even after content/recipes churn.
- **Capture (client):** `IntersectionObserver` → `impression` (≥50% visible, once) + banked
  `dwell` (≥2 s, flushed on timer/`pagehide`) + `scroll_past` (impressed, left upward, never
  opened); title/Read-more clicks → `open`/`read_more`; kebab menu → `like` (one-way positive)
  / `hide` (removes row + persists). Events buffer in `localStorage`, flush batched via `fetch`
  (timer) and `navigator.sendBeacon` (`pagehide`).
- **Derived board state:** `hide` filters the item out of `/api/board` (immediate deterministic
  override; the learned-ranking equivalent is D21). `seen` dims rows (D22). `liked` shows a heart.

---

## 4. Delivery & two modes (D15)

One data contract, two transports. Over **http** the template fetches `GET /api/board` and POSTs
interactions; over **`file://`** it reads the `window.LIST_DATA` sidecar and hides all
server-only controls (so the static render stays the regression check). The server keeps the
on-disk sidecar in sync on every board request, so switching from served to `file://` shows the
same board.

## 5. PWA (D26)

`manifest.webmanifest` (standalone, theme `#1e1f22`, SVG icon) + a `service-worker.js`
(app-shell cache-first; `GET /api/board` network-first with cache fallback — the SWR spirit on
the client) registered only in served mode. CSS is mobile-first responsive (wrapping toolbar,
≥40px tap targets, ≤600px tuning).

## 6. Run

```bash
cd 4_List_renderer
run_4_List_server.bat                         # serve 127.0.0.1:8765 + open browser
python scripts/4_serve.py --port 9000 --seed  # custom port, (re)seed first
python scripts/4_serve.py --no-open -v        # headless-ish, verbose
```

## 7. Not in M7 (later phases)

Ranking/order (M6 = Phase 5; consumes this `interactions` log) · cross-source dedup (M4) ·
interest **discovery** (M5 = Phase 6; capture only here) · hosted auth/workers/Postgres (§14).
