# 0_Renderer — Overall Specification

## 1. Purpose

`0_Renderer` is an interactive 3D stock-visualizer desktop app. It renders a ring of floating "billboards", each an independent iframe hosting a live candlestick / line chart for one ticker. The user orbits, zooms, adds/removes tickers, edits entry-price targets, and pans through nine standard time periods (1d → max). Under the hood, a local Flask server fronts a SQLite cache wrapping yfinance data and Yahoo's ticker-search API.

Everything runs on `localhost` against a single Python process. There is no authentication, no remote state, no cloud component. The app is a local personal tool.

---

## 2. How to run

### First-time setup (once)

```bat
python 0_Renderer\1_setup_venv.py
```

This creates `.venv/` at the repo root and installs [../../requirements.txt](../../requirements.txt) (Flask, yfinance, pandas, requests, …).

### Every subsequent launch

Double-click [run.bat](../run.bat), or:

```bat
.venv\Scripts\python.exe 0_Renderer\2_stock_visualizer.py
```

The launcher:
1. Initializes the SQLite cache ([_outputs/cache/prices.db](../_outputs/cache/prices.db)).
2. Synchronously calibrates the system clock against internet time (see §8).
3. Starts a background thread that re-calibrates every 30 minutes.
4. Opens the default browser at `http://localhost:5000/` ~1.4 s later.
5. Runs a Flask dev server (`threaded=True`, no reloader) on port 5000.

No CLI arguments. The browser picks up the last session from `localStorage` (§7). A fresh install lands on six blank billboards at period `1d`.

Stop with `Ctrl+C` in the terminal (or close the `run.bat` window).

---

## 3. Architecture

### 3.1 Components

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (Chromium-based recommended)                           │
│                                                                 │
│  ┌─────────────── 0_Renderer/index.html ─────────────────────┐  │
│  │  Three.js scene (r160) — WebGL ring of billboards          │  │
│  │  OrbitControls, manual CSS projection (NOT CSS3DRenderer)  │  │
│  │                                                            │  │
│  │  Billboards layer (HTML, absolutely positioned):           │  │
│  │  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐                    │  │
│  │  │iframe│  │iframe│  │iframe│  │iframe│  …                  │  │
│  │  │ BCYC │  │ TCRX │  │ NVDA │  │ AAPL │                    │  │
│  │  └──────┘  └──────┘  └──────┘  └──────┘                    │  │
│  │     │          │           │          │                    │  │
│  │     ↓ each iframe loads 1_chart_template.html              │  │
│  │                                                            │  │
│  │  UI overlays: hamburger menu, period bar, giraffe/chat     │  │
│  │  panel, price overlay, ring controls (← − + →)             │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              ▲                                  │
│                              │ HTTP (JSON / HTML)               │
└──────────────────────────────┼──────────────────────────────────┘
                               │
┌──────────────────────────────┼──────────────────────────────────┐
│  Local Flask server (port 5000)                                 │
│                                                                 │
│    2_stock_visualizer.py                                        │
│      ├── Clock sync thread       (§8)                           │
│      ├── SQLite cache layer      (§4)                           │
│      ├── yfinance fetch layer    (per-ticker + batch)           │
│      ├── Yahoo search resolver   (ticker ←→ company name)       │
│      └── market_calendars.py     (30+ exchanges, holidays)      │
│                                                                 │
│    _outputs/cache/prices.db  (SQLite — meta, bars, period_fetch)│
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 File layout

```
0_Renderer/
├── 1_setup_venv.py               Creates .venv at repo root, installs deps
├── 2_stock_visualizer.py         Flask server — routes, cache, yfinance, clock
├── market_calendars.py           Schedules + holidays for 30+ exchanges
├── index.html                    Top-level page — Three.js scene, HUD, chat panel
├── run.bat                       Activates venv + launches the server
├── _outputs/
│   ├── cache/
│   │   └── prices.db             SQLite: meta, bars, period_fetch
│   └── templates/
│       └── 1_chart_template.html Per-iframe chart (TradingView Lightweight Charts)
├── Utilities/
│   └── Renderer/
│       ├── logo.png              Giraffe button icon
│       └── Screenshot …          Reference art
└── spec/                         This folder — Overall_specification.md, decisions.md
```

### 3.3 Split of responsibility

- **`index.html`** owns everything visual and everything per-session: camera state, ring layout, which ticker is in which slot, theme, chart-type toggle, entry-price targets, marker texts, sort mode. Persisted entirely in `localStorage`.
- **`1_chart_template.html`** owns a single chart: period buttons, candle/line switch, live-poll loop, OHLC tooltip, status badge, two target-price labels. Communicates with the parent via `postMessage`.
- **`2_stock_visualizer.py`** owns all network-facing operations and the SQLite cache. It is the **only** process that talks to yfinance or to Yahoo's search API.
- **`market_calendars.py`** is a pure library — no I/O. It maps a yfinance exchange code (`NYQ`, `LSE`, `TYO`, …) to a schedule dict and tells whether a given UTC datetime falls inside an open session.

---

## 4. SQLite cache — schema, updates, TTL

### 4.1 Database

One file: [_outputs/cache/prices.db](../_outputs/cache/prices.db). Created automatically on first launch by `_init_db()` in [2_stock_visualizer.py](../2_stock_visualizer.py). WAL journal mode.

### 4.2 Tables

| Table | PK | Purpose |
|---|---|---|
| `meta` | `ticker` | Company info (short_name, long_name, sector, industry, currency, exchange, website, cik). One row per ticker. |
| `bars` | `(ticker, interval, t)` | OHLCV bars. `t` is Unix epoch (seconds, UTC). Shared across all periods that use the same interval. |
| `period_fetch` | `(ticker, period)` | Per-period freshness bookkeeping — records when each `(ticker, period)` was last refreshed. |

Index: `idx_bars_lookup` on `(ticker, interval, t)`.

### 4.3 Period → interval mapping

Defined by `PERIOD_INTERVAL` in [2_stock_visualizer.py:43-53](../2_stock_visualizer.py#L43):

| Period | Interval | Notes |
|---|---|---|
| `1d`  | `1m`  | Today's session — 1-minute bars |
| `5d`  | `5m`  | Five trading days — 5-minute bars |
| `1mo` | `1h`  | One month — hourly bars |
| `3mo` | `1d`  | Three months — daily bars |
| `6mo` | `1d`  | Six months — daily bars |
| `1y`  | `1d`  | One year — daily bars |
| `2y`  | `1wk` | Two years — weekly bars |
| `5y`  | `1wk` | Five years — weekly bars |
| `max` | `1mo` | Full history — monthly bars |

Bars are **keyed by interval**, not by period. Fetching `1y @ 1d` automatically warms `6mo` and `3mo`, which share the `1d` interval. `db_mark_period_fresh()` walks `PERIOD_ORDER` and marks every shorter period at the same interval as fresh when the longer one returns.

### 4.4 TTLs

Base TTLs per interval, in `CACHE_TTL` ([2_stock_visualizer.py:59-66](../2_stock_visualizer.py#L59)):

| Interval | Base TTL | During market hours (intraday only) |
|---|---|---|
| `1m`  | 6 s | **6 s** |
| `5m`  | 30 s | **6 s** |
| `1h`  | 5 min | **6 s** |
| `1d`  | 1 day | — |
| `1wk` | 7 days | — |
| `1mo` | 30 days | — |

`_effective_ttl()` drops the three intraday intervals (`1m`, `5m`, `1h`) to 6 s whenever the resolved exchange is currently open. That is what drives the "live" feel of the chart — the frontend re-polls every 6 s while the market is open, and each poll hits a warm cache unless ≥ 6 s have elapsed since the last upstream fetch.

Metadata TTL: **7 days** (`META_TTL = 604_800`). `_ensure_meta_fresh()` refreshes company info on a weekly cadence. A blank name or exchange also triggers a refresh.

### 4.5 Period window

`PERIOD_WINDOW_DAYS` in [2_stock_visualizer.py:185-195](../2_stock_visualizer.py#L185) defines how far back the DB is queried when serving bars for a period:

```
1d:2, 5d:10, 1mo:40, 3mo:100, 6mo:200, 1y:400, 2y:750, 5y:1900, max:None
```

These are generous buffers that absorb yfinance's inclusive date semantics. A 2-day window for `1d` means the chart iframe may receive bars from both the current and the previous session; the iframe anchors on the **last bar's date** to pick the correct session and extracts the prior-day close for the % baseline (see decisions D5).

### 4.6 Fresh vs SWR modes

Every cache-reading path accepts `mode="fresh"` or `mode="swr"`:

- **fresh** (default) — refresh from yfinance if TTL is exceeded, then return. Always returns bars.
- **swr** (stale-while-revalidate) — zero network calls. Returns whatever is cached with `stale: true|false`. If nothing is cached, returns `None` and the frontend re-requests in fresh mode. Used by the frontend on every period switch: cached data renders instantly, a fresh call arrives in the background.

SWR is enforced by `get_chart_data()` and `get_chart_data_batch()`; the HTTP layer (`/api/data?mode=swr`) is a thin pass-through.

---

## 5. HTTP API (Flask routes)

| Route | Method | Returns |
|---|---|---|
| `/` | GET | Serves `index.html` (the Three.js scene). |
| `/_outputs/templates/1_chart_template.html` | GET | The per-iframe chart template (via Jinja — but no variables are templated; it is effectively static HTML). |
| `/utilities/renderer/<path>` | GET | Static assets from `Utilities/Renderer/` (logo, screenshots). |
| `/api/data?ticker=&period=&mode=` | GET | Full payload for one ticker (see §5.1). `mode` ∈ `fresh`, `swr`. |
| `/api/data_batch?tickers=A,B,C&period=&mode=` | GET | `{ period, tickers: { A: {…}, B: {…}, … } }`. Stale tickers are refreshed in a **single** `yf.download()` call — this is what makes page-load and period-switch fast (typically ~300 ms for six tickers vs ~2 s per-ticker). |
| `/api/search?q=` | GET | Up to 8 `{symbol, name, type}` dicts from Yahoo's search API. Filtered to EQUITY / ETF / MUTUALFUND / INDEX. |
| `/api/resolve?q=` | GET | `{ticker}` — best-effort ticker resolution. `≤5` alpha chars passes through as-is; otherwise the first search result is used. |
| `/api/market_status` | GET | `{open: bool}` for the default exchange (NYSE). |
| `/api/time_info?ticker=` | GET | Internet-calibrated clock + exchange schedule. The browser uses this to decide market-open locally between polls (see §8). |

### 5.1 `/api/data` payload shape

```json
{
  "meta":              { "ticker": "BCYC", "short_name": "Bicycle Therapeutics",
                         "long_name": "...", "sector": "Healthcare",
                         "industry": "Biotechnology", "currency": "USD",
                         "exchange": "NMS", "website": "...", "cik": "",
                         "fetched_at": "2026-04-24T14:12:03" },
  "interval":          "1m",
  "period":            "1d",
  "fetched_at":        "2026-04-24T14:30:06",
  "records":           [ {"t":1713965400,"o":7.08,"h":7.09,"l":7.05,"c":7.07,"v":1234}, … ],
  "market_open":       true,
  "exchange_schedule": { "code":"NMS","name":"NASDAQ","tz":"America/New_York",
                         "open":[9,30],"close":[16,0],"break_start":null,
                         "break_end":null,"approximate":false },
  "stale":             false
}
```

---

## 6. Frontend — user interface

### 6.1 Top-level layout

- **WebGL layer** (behind, `pointer-events: none`) — Three.js scene: background sprite, camera.
- **Billboards layer** (in front, `pointer-events: auto`) — absolutely-positioned iframes whose `left/top/transform` are rewritten every frame by a manual projector.
- **HUD overlays** (above everything):
  - Hamburger menu (top-left) → slide-in panel with theme, chart-type, camera reset, orbit-speed, scroll-gain sliders.
  - Period bar (top-center by default; moves to bottom-center when chat is open) → `1d 5d 1mo 3mo 6mo 1y 2y 5y max`.
  - Giraffe button (bottom-right) → opens the chat panel.
  - Price overlay (bottom, or right-1/3 column when chat is open) — see §6.4.

### 6.2 Ring of billboards

- Default size: 6 billboards (`CIRCLE_COUNT`), ring radius 7 wu.
- Radius scales linearly past 8 billboards to preserve arc-spacing.
- Each iframe is 800×500 px scaled by `BILLBOARD_SCALE = 0.004` → 3.2×2 wu.
- **Manual projection**, not `CSS3DRenderer`: every frame, the billboard's 3D world position is projected to 2D screen coordinates and applied as `translate(x, y) scale(s)`. Reason: Chromium hit-tests CSS3D-transformed iframes by the AABB of the transformed quad, which leaks far beyond the visible rectangle and breaks clicks on neighbouring billboards. The manual-projection approach gives each iframe a pixel-accurate hit-box.
- **Closest-billboard** detection — every frame, the billboard with the smallest distance to the camera ray is marked `.closest` and drives the price overlay.

### 6.3 Ring controls (bottom-center when chat is open)

- `←` / `→` — Swap the closest billboard with its left/right neighbour; camera follows so the closest billboard stays closest. Triggers a ticker-list reorder and a `persistSession()`.
- `−` — Remove the closest billboard.
- `+` — Add a blank billboard at the closest position.
- `Ticker` input + `Go` button — Resolve free-text input to a ticker (via `/api/resolve`) and assign it to the closest billboard.
- `↕ %` / `↕ T` sort toggle — Default (`%`) preserves user-input order; toggled (`T`) sorts ticker rows ascending by `(current − entry) / current` (tickers without an entry price stay on top in user order).

### 6.4 Chat panel (giraffe button)

Three states, toggled by the `+` / `←` buttons in the top-right of the overlay:

| State | Description |
|---|---|
| **Bottom bar** (default) | Not "chat-open". Single-row `po-row1` + `po-row2` (pre/post-market) price overlay at the bottom. |
| **State 1** (chat-open) | Right 1/3 of the screen. Shows a scrollable ticker-list — one row per ring billboard, closest on top. Each row shows ticker / company / price / change plus a `+` button that opens an inline entry-target input. |
| **State 2** (chat-open, chat-expanded) | Full-height overlay. Only the closest billboard's row is shown (ticker, company, price, change, status); the `#chat-body` panel below it holds only the entry-target input list — no duplicated ticker/company header. Used for deep edit of a single ticker. |

State-2 UI also includes a **second entry price / text field** — if it parses as a positive float it is broadcast to the chart iframe as `target2`, which renders a second target label (blue if current < target, orange if current > target).

### 6.5 Per-iframe chart controls

Inside each iframe ([1_chart_template.html](../_outputs/templates/1_chart_template.html)):

- **Top row** — ticker, company, price, change-vs-baseline, target label(s), market-status badge (OPEN / CLOSED with exchange name).
- **Main chart** — TradingView Lightweight Charts v4.2.0. Candlestick or line/area (user-switchable). Volume pane below.
- **OHLC tooltip** (top-left) — appears on crosshair hover, shows open / high / low / close / Δ / Δ% / volume for the bar under the cursor.
- **Cursor price/volume label** — attached to the time-axis, follows the crosshair.
- **Footer** — live status dot ("Live (6 s)" when open, "Live updates off" when closed), last-fetched clock, interval label.
- **Period row** — nine buttons mirroring the parent's period bar. Clicking a button inside an iframe broadcasts the new period to the parent via `sv-period`, which then redistributes to all iframes.
- **Interactions forwarded to parent**:
  - Wheel → `sv-wheel` (camera zoom);
  - Right-button drag → `sv-rmb-move` (camera orbit — bypasses OrbitControls so drags that start inside the chart still rotate the scene);
  - Double-click → `sv-dblclick` (snap camera to this billboard).

### 6.6 Period bar

Nine buttons: `1d · 5d · 1mo · 3mo · 6mo · 1y · 2y · 5y · max`. Clicking one:
1. Updates the active class locally.
2. Calls `/api/data_batch?period=<p>&mode=swr` — renders cached bars instantly on every iframe.
3. In parallel, calls `/api/data_batch?period=<p>&mode=fresh` — updates each iframe with fresh bars via `sv-period-with-data` when they arrive.
4. Calls `persistSession()` so the chosen period survives reload.

### 6.7 Display menu (hamburger)

- **Theme**: Dark / Light / Auto — overrides `prefers-color-scheme`. Broadcast to iframes via `sv-mode`.
- **Chart type**: Candle / Line — broadcast via `sv-charttype`.
- **Reset view** — recenters the camera.
- **Orbit speed** slider — gain on OrbitControls rotate.
- **Scroll gain** slider — gain on the scroll-to-zoom handler.

### 6.8 Marker text per billboard

Each billboard can carry a free-text marker (displayed above the iframe). Keyed by ticker in `localStorage`, so the marker follows the ticker across ring swaps / adds / removes.

---

## 7. Data caching between sessions (localStorage)

Every user-tunable setting is persisted to `localStorage` on every change and restored on startup. The browser comes up exactly as it was left.

| Key | Type | Purpose |
|---|---|---|
| `sv.session` | JSON `{period, ringTickers[]}` | Period + ring in angular-visual order. `ringTickers` is sorted by `atan2(worldPos.z − center.z, worldPos.x − center.x)` so ←/→ swaps (which mutate `worldPos` but not array index) survive reload. |
| `sv.orbitSpeed` | number (0.1–3) | OrbitControls rotate gain. |
| `sv.scrollGain` | number (0.1–3) | Zoom gain. |
| `sv.mode` | `light` \| `dark` \| _null_ | Theme override. Null = follow OS. |
| `sv.chartType` | `candle` \| `line` | Broadcast to all iframes. |
| `sv.tickerSortMode` | `default` \| `percent` | Ticker-list sort mode (§6.3). |
| `sv.chatBottomHidden` | `0` \| `1` | Whether the ring-controls row is collapsed. |
| `sv.markerText.<TICKER>` | string | Per-ticker marker text. |
| `sv.entryTargets.<TICKER>` | JSON `string[]` | Per-ticker entry-target values (first is the primary target; second, if a positive float, becomes `target2` on the chart). |
| `sv.tickerSource.<TICKER>` | `"manual"` \| `"auto"` | Per-ticker provenance tag. Missing ≡ `manual`. Drives the pink color applied to the ticker symbol when `auto`. Written by the Go button, URL-launch, and initial-ring bootstrap (all as `manual`); `markTickerAuto()` is reserved for a future ingestion script and refuses to overwrite an existing entry (user actions always win). Applies only to `.tl-ticker` / `.po-ticker`; company / price / target colors are unchanged. |

`persistSession()` is called after every user-driven mutation (period change, ticker add/remove/swap/edit) and once on first load so a URL-launched session is captured.

URL query params (`?tickers=A,B,C&period=1mo`) override the cache **for this session** but are written back so the next blank launch uses them.

---

## 8. Internet clock calibration

The local system clock is not trusted to decide whether a market is open. Reason: users with a skewed clock, or a VM with stale time, would otherwise see garbled market-status and stale live polls.

Strategy (`_fetch_clock_offset()`):
1. Fire a `HEAD` request to `https://www.google.com` (fallback `https://finance.yahoo.com`).
2. Read the RFC 2822 `Date:` response header — always UTC.
3. Measure the round-trip time and subtract half to centre on the server's actual timestamp.
4. Store the offset (`_clock_offset_s`) under a lock.
5. Re-sync every 30 min in a background thread.

The first sync is **synchronous** and happens before Flask starts accepting requests, so `is_market_open()` is correct on the very first call.

The offset is published to the browser via `/api/time_info`. The frontend calibrates `Date.now()` once at load and then decides market-open locally (avoids a round-trip on every 6-s poll). The server's `market_open` is a cross-check.

---

## 9. Market calendars

`market_calendars.py` maps yfinance `exchange` codes to a schedule dict. 30+ exchanges supported:

- **Full algorithmic holidays**: US, GB, EU (Euronext), DE (XETRA), CH (SIX), CA, AU.
- **Approximate only** (`APPROXIMATE_REGIONS`): JP, HK, CN, IN, KR, SG, TW, BR, MX — lunar / religious / government-announced holidays are not computed. The schedule payload sets `approximate: true` so the UI can show a hint.
- **Lunch breaks**: Tokyo, Osaka, HKEX, Shanghai, Shenzhen, Singapore.

`is_exchange_open(code, dt_utc)` handles weekend detection, local-time conversion, holiday lookup, and midday halt. `schedule_for_json()` emits a JSON-safe dict for the frontend.

---

## 10. iframe ↔ parent postMessage protocol

The parent (`index.html`) and each iframe (`1_chart_template.html`) are same-origin, but every data exchange still goes through `postMessage` so the iframe could in principle be hosted anywhere.

| Type | Direction | Payload |
|---|---|---|
| `sv-price-info` | iframe → parent | `{ticker, company, price, priceNum, change, positive, status, statusText}`. Sent after every chart render. Parent uses it for the overlay + ticker-list. |
| `sv-period` | bidirectional | `{period}`. iframe → parent when user clicks a period button inside the iframe. Parent → iframe to broadcast a period change. |
| `sv-period-with-data` | parent → iframe | `{period, data}` — period switch with bars pre-fetched by the parent's batched call. Saves the iframe one HTTP round-trip. |
| `sv-mode` | parent → iframe | `{light: bool}` — theme override. |
| `sv-detail` | parent → iframe | `{show: bool}` — hide/show chart chrome (used when a billboard is very small). |
| `sv-charttype` | parent → iframe | `{chartType: 'candle'\|'line'}`. |
| `sv-target` | parent → iframe | `{target, target2}` — primary + secondary entry-price targets for this ticker. |
| `sv-wheel` | iframe → parent | `{deltaY, deltaMode}` — forwards wheel events so the parent can zoom the camera. |
| `sv-rmb-move` | iframe → parent | `{deltaX, deltaY}` — right-button drag → camera orbit. |
| `sv-dblclick` | iframe → parent | no payload — snaps camera to this billboard. |

---

## 11. Data providers & licensing

- **yfinance** (MIT, unofficial scraper) — used for OHLCV and metadata. Yahoo's ToS **prohibit** redistribution and commercial use of their data.
- **Yahoo search API** — used for ticker ↔ company-name resolution. Same ToS caveat.
- **TradingView Lightweight Charts** (Apache 2.0) — commercial use allowed, no royalties.
- **Three.js** (MIT) — commercial use allowed, no royalties.

**Public-deploy constraint** (memory: _Switch off yfinance before public deploy_): before hosting this app on the open internet, yfinance must be replaced with a licensed provider (Polygon.io, Twelve Data, Finnhub, Alpha Vantage, or IEX Cloud — all have free tiers and commercial tiers with explicit redistribution rights). The swap surface is confined to `fetch_prices()`, `fetch_prices_batch()`, `fetch_meta()`, and `search_tickers()` in [2_stock_visualizer.py](../2_stock_visualizer.py). All cache paths and frontend code are provider-agnostic.

---

## 12. Known limitations

1. **yfinance ToS** — already discussed; app is local-personal only until provider is swapped.
2. **Holiday accuracy** — approximate for 9 Asian + Latin-American regions (§9).
3. **Session-anchor edge case** — if the SQLite cache only has a prior session's 1m bars (new DB, weekend), the chart will anchor on that session until the next 1-minute fetch returns today's first bar. Resolves automatically on first successful live poll.
4. **One port** — hardcoded to 5000 (`PORT = 5000` in [2_stock_visualizer.py:761](../2_stock_visualizer.py#L761)). No CLI flag.
5. **No auth** — Flask binds to `0.0.0.0`, so anyone on the LAN can read the cached data. Acceptable for a personal-tool on a trusted network; not acceptable for a deployed service.

---

## 13. Entry points in the code (quick map)

| Concern | File : symbol |
|---|---|
| Cache TTLs | [2_stock_visualizer.py:59](../2_stock_visualizer.py#L59) `CACHE_TTL` |
| Period→interval | [2_stock_visualizer.py:43](../2_stock_visualizer.py#L43) `PERIOD_INTERVAL` |
| DB schema | [2_stock_visualizer.py:202](../2_stock_visualizer.py#L202) `_init_db()` |
| Fresh vs SWR | [2_stock_visualizer.py:476](../2_stock_visualizer.py#L476) `get_chart_data()` |
| Batch fetch | [2_stock_visualizer.py:515](../2_stock_visualizer.py#L515) `get_chart_data_batch()` |
| Clock sync | [2_stock_visualizer.py:99](../2_stock_visualizer.py#L99) `_fetch_clock_offset()` |
| Exchange schedules | [market_calendars.py:22](../market_calendars.py#L22) `EXCHANGE_SCHEDULES` |
| Ring layout & projection | [index.html:1052](../index.html#L1052) (Three.js module block) |
| localStorage keys | [index.html:1084](../index.html#L1084) onward |
| Ticker list build | [index.html:2016](../index.html#L2016) `buildTickerListRows()` |
| Price-overlay update | [index.html:1943](../index.html#L1943) `updatePriceOverlay()` |
| Session persistence | [index.html:1247](../index.html#L1247) `persistSession()` |
| Chart render | [1_chart_template.html:840](../_outputs/templates/1_chart_template.html#L840) `renderChart()` |
| Target labels | [1_chart_template.html:408](../_outputs/templates/1_chart_template.html#L408) `_renderOneTarget()` |
