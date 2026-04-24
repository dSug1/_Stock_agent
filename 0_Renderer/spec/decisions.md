# 0_Renderer — Implementation Decisions Log

**Purpose.** Record design + implementation decisions made while building the 0_Renderer pipeline. [Overall_specification.md](Overall_specification.md) describes the target system; this log explains *why* the code makes the choices it does. Ground truth is the current codebase — when this log disagrees with code, fix the log.

**Update discipline.** At the end of any non-trivial change: if something was calibrated, renamed, newly introduced, or deviates from spec, append or revise the relevant entry with a link to the code line of record (`[file:NN](../file#L<n>)`).

**Last updated:** 2026-04-24 (spec folder bootstrapped; entries D1–D11 captured from existing code and chat history).

---

## D1 — Manual 2D projection for iframes instead of `CSS3DRenderer`

**Problem.** Three.js's `CSS3DRenderer` hit-tests transformed iframes using the axis-aligned bounding box of the transformed quad. Once the camera gets close to a billboard, that AABB leaks far beyond the visible rectangle, and pointer events meant for a neighbouring billboard fall through to the wrong iframe.

**Decision.** Don't use `CSS3DRenderer`. Every frame, compute each billboard's screen-space `(x, y, scale)` in JS and apply a plain 2D `transform: translate(x,y) scale(s)`. Hit-box is now pixel-accurate.

**Where:** `index.html` module block, comment at [index.html:70-83](../index.html#L70) and the projection loop inside the render function.

**Trade-off.** We lose free CSS3D perspective. Chosen anyway — correct pointer events are non-negotiable for an interactive app.

---

## D2 — SQLite cache keyed by `interval`, not `period`

**Rule.** Bars live in one table (`bars`) keyed by `(ticker, interval, t)`. Periods (`1d`, `5d`, `3mo`, …) share bars with any other period at the same interval.

**Why.** Fetching `1y @ 1d` returns ~250 daily bars. Those same bars cover the `6mo` and `3mo` periods for free. Keying by period would have required three separate fetches and duplicated storage.

**How it's tracked.** `period_fetch(ticker, period, fetched_at)` records freshness per-period, not per-bar. `db_mark_period_fresh()` walks `PERIOD_ORDER` and marks every shorter-or-equal period at the same interval as fresh when the longer one returns.

**Where:** [2_stock_visualizer.py:312-327](../2_stock_visualizer.py#L312) `db_mark_period_fresh()`; [2_stock_visualizer.py:216-221](../2_stock_visualizer.py#L216) bars table schema.

---

## D3 — Intraday TTL drops to 6 s during market hours

**Rule.** `_effective_ttl()` returns **6 s** whenever the interval is `1m` / `5m` / `1h` **and** the resolved exchange is currently open. Otherwise the base TTL applies (30 s for `5m`, 5 min for `1h`, etc.).

**Why.** The chart iframe polls `/api/data` every 6 s during market hours. Without a matching TTL, every poll would hit yfinance, which is unfriendly and slow. With the 6-s TTL, the second poll inside a 6-s window returns cached data instantly.

**Where:** [2_stock_visualizer.py:469-473](../2_stock_visualizer.py#L469) `_effective_ttl()`.

---

## D4 — Stale-while-revalidate on period switches

**Rule.** The frontend always sends two requests on a period switch:
1. `mode=swr` — zero network, returns cached bars instantly.
2. `mode=fresh` — refreshes stale tickers from yfinance in parallel.

This matches the user memory _Apply stale-while-revalidate by default_. The UI is never blocked by the network.

**Implementation.** `/api/data_batch?mode=swr` short-circuits all fetches. Tickers with no cache row are omitted from the response (not `None`). The frontend notices the omission and re-requests only those tickers in fresh mode.

**Where:** [2_stock_visualizer.py:529-540](../2_stock_visualizer.py#L529) (SWR branch of `get_chart_data_batch`); frontend consumer in [index.html](../index.html) period-button handler around line 2740.

---

## D5 — 1D session anchor uses the LAST bar's date, not the first

**Problem.** `PERIOD_WINDOW_DAYS["1d"] = 2` — the DB returns up to 2 days of 1-minute bars so that fresh cache rows from yesterday's session don't fall out of the window. Consequence: when the chart renders, `records[0]` may be a stale pre-session bar from yesterday. If the chart uses `records[0]` to compute session-open epoch and % baseline, it shows the **wrong session** and computes % vs. "the day before yesterday's close".

**Decision (2026-04-24).** Anchor on the last bar's timezone-local date:
- `sessionOpenEpoch` = today's exchange open in the last bar's tz day.
- `prevClose1d` = last bar **before** `sessionOpenEpoch` — i.e., yesterday's true close.
- Filter `records` to only bars with `t >= sessionOpenEpoch` before building candles / line / volume series.
- % baseline for 1D is `prevClose1d` if available, else `first.o` (new-install fallback).

**Where:** [1_chart_template.html:870-884](../_outputs/templates/1_chart_template.html#L870) (session anchor + filter); [1_chart_template.html:906-913](../_outputs/templates/1_chart_template.html#L906) (% baseline in on-chart panel); matching block in the parent broadcast ~line 950.

**Alternative considered.** Shrink `PERIOD_WINDOW_DAYS["1d"]` to less than 1 day server-side. Rejected: it would fail over weekends and holidays when the most recent session may be ≥2 calendar days old.

---

## D6 — Internet-calibrated clock, not system clock

**Rule.** `is_market_open()` never reads `datetime.utcnow()` directly. It calls `calibrated_utcnow()`, which adds `_clock_offset_s` measured against `https://www.google.com`'s RFC 2822 `Date:` header.

**Why.** Users with a skewed system clock (laptops that haven't synced NTP in weeks, VMs with drifted time) would otherwise see wrong market-open state, and the 6-s live poll would re-fetch outside actual trading hours.

**Sync policy.** First sync is **synchronous** before Flask starts accepting requests. A background thread re-syncs every 30 min.

**Browser side.** The calibrated offset is published via `/api/time_info`. The frontend calibrates `Date.now()` once at load and computes `isMarketOpenNow()` locally — no round-trip per poll.

**Where:** [2_stock_visualizer.py:99-149](../2_stock_visualizer.py#L99).

---

## D7 — All preferences in localStorage, not on server

**Rule.** Every user-tunable setting is persisted client-side. The server is pure-data and stateless wrt. users (matches the personal-tool scope).

**Why.** The app is local-single-user. Adding a server-side settings store would complicate the architecture for no gain. localStorage also means a fresh browser profile starts clean without DB cleanup.

**Consequence.** Opening the app in a different browser or profile yields an empty ring. Acceptable — users can reimport via the optional `?tickers=…&period=…` URL query.

**Where:** localStorage keys enumerated in [Overall_specification.md §7](Overall_specification.md#7-data-caching-between-sessions-localstorage).

---

## D8 — Ring tickers persisted in **angular-visual order**, not array order

**Problem.** `←` / `→` buttons swap the `worldPos` of two billboards but do **not** reorder the `billboards[]` array. If `persistSession()` just wrote `billboards.map(b => b.ticker)`, the post-reload ring would re-place tickers at `i / n · 2π` angles in array order — and the swap would vanish.

**Decision.** Before serializing, sort the ring by `atan2(worldPos.z − center.z, worldPos.x − center.x)`. What is persisted is the sequence the user **sees** going around the ring, not the internal array order.

**Where:** [index.html:1247-1265](../index.html#L1247) `persistSession()`.

---

## D9 — Dirty-check DOM writes in the ticker-list

**Rule.** `updateTickerListData()` is called every frame when the chat is open, but only writes to a DOM node when the underlying value has changed. `buildTickerListRows()` is called only when `ring.length` or `_closestInteractive` changes (tracked via `_lastTickerListGen` + `_lastClosestForList`).

**Why.** Writing to 20+ DOM nodes every frame (~60 fps) stutters on weaker machines. Dirty-checks cut writes to what actually changes — typically just the "closest" billboard's price + change.

**Rebuild triggers.** Beyond gen/closest changes, a rebuild is also forced when the sort-mode signature changes (for `percent` sort) and when `←/→` swaps a billboard (which otherwise wouldn't change gen or closest).

**Where:** [index.html:2016](../index.html#L2016) `buildTickerListRows()`; rebuild trigger on swap at [index.html:3329-3333](../index.html#L3329) added 2026-04-24.

---

## D10 — Manual camera-dirty flag gates heavy work

**Rule.** `updatePriceOverlay()`, projection math, and ticker-list rebuilds only run when `_cameraDirty = true`. The flag is set on every camera move, sort-mode toggle, billboard add/remove/swap, and iframe-load event.

**Why.** A static scene with no user input should consume near-zero CPU — stay below Chrome's "this tab is using a lot of energy" threshold.

**Consequence of missing it.** Any new event that reorders rows or changes what the overlay should show **must** remember to set `_cameraDirty = true`, or the change won't render until the next camera move. This has been the root cause of several "the toggle isn't working" bugs.

**Where:** render-loop gate around [index.html:3681](../index.html#L3681).

---

## D11 — Second entry-price (`target2`) flows through the same channel as the primary target

**Problem.** Users wanted a second entry price that also renders on the chart (different colour, same formula).

**Decision.** Reuse the `sv-target` message — just add a `target2` field. The chart iframe keeps two target-price variables (`_targetPrice`, `_targetPrice2`) and renders both labels. A non-float second input is silently ignored (parses to `null`).

**Why not a new `sv-target2` message.** A single message guarantees both values are updated atomically; there's no window where target1 is fresh and target2 is stale.

**Where:** broadcast at [index.html:2364-2372](../index.html#L2364) `broadcastTargetPrice()`; consumer at [1_chart_template.html:1033-1040](../_outputs/templates/1_chart_template.html#L1033).

---

## D12 — Hard-coded port 5000

**Rule.** `PORT = 5000` in [2_stock_visualizer.py:761](../2_stock_visualizer.py#L761). No CLI flag, no env var.

**Why it's acceptable.** Local personal tool, one instance at a time, no conflict concern.

**Why it's logged.** Any refactor that wants to parametrize this should note that the browser-open URL (`http://localhost:{PORT}/`) and any future docs need to follow.

---

## D13 — No authentication, binds to `0.0.0.0`

**Rule.** Flask listens on `0.0.0.0:5000`. Anyone on the LAN can read the cached data.

**Why it's acceptable.** Trusted-network personal use. No credentials, no PII, no trading actions — the app only displays market data.

**Boundary.** If this ever gets deployed beyond the local machine (see the licensing memory), `host` must drop to `127.0.0.1` **and** an auth layer must be added **before** any public exposure — in the same PR that swaps yfinance for a licensed provider.

---

## D14 — Marker-input row: blur + clear selection before DOM removal

**Problem (2026-04-24).** Clicking the `−` button to close a ticker row's marker-text input left a blinking caret on screen. The caret persisted into chat state 2 and even into the bottom-bar mode after the chat was closed; only a page refresh cleared it.

**Root cause.** `toggleMarkerInputRow()` opens the input with `input.focus()` + `input.select()`. On close, it called `inputRowEl.remove()` directly. Two artefacts were left behind:
1. The focused element vanishing from the DOM does not always move the focus ring cleanly — some Chromium builds keep drawing the caret at the last position.
2. `input.select()` creates a range in `window.getSelection()` that outlives the input's removal.

**Fix.** Before removing the row, call `inputEl.blur()` and `window.getSelection()?.removeAllRanges()`. Same two steps applied to the stale-entry cleanup path in `buildTickerListRows()` (so a billboard removed from the ring while its input is focused doesn't leak a caret).

**Where:** [index.html:2287-2302](../index.html#L2287) `toggleMarkerInputRow()`; [index.html:2076-2086](../index.html#L2076) stale-entry loop in `buildTickerListRows()`.

**Invariant for future changes.** Any code path that removes a focused input from the DOM must blur it first and clear the window selection. Treat `input.select()` as leaving residue; pair it with an explicit cleanup on close.
