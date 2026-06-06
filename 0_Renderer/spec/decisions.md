# 0_Renderer — Implementation Decisions Log

**Purpose.** Record design + implementation decisions made while building the 0_Renderer pipeline. [Overall_specification.md](Overall_specification.md) describes the target system; this log explains *why* the code makes the choices it does. Ground truth is the current codebase — when this log disagrees with code, fix the log.

**Update discipline.** At the end of any non-trivial change: if something was calibrated, renamed, newly introduced, or deviates from spec, append or revise the relevant entry with a link to the code line of record (`[file:NN](../file#L<n>)`).

**Last updated:** 2026-06-05 (added **D17** — market-closed launch shows the previous close, not 0.00; **D18** — drop NaN bars that crashed 3mo+ charts at market close; **D19** — canonical current price identical across periods; **D20** — coarse chart bars made current via trailing-bar pin + shorter 1wk/1mo TTLs — and re-synced every `2_stock_visualizer.py` / `index.html` / `1_chart_template.html` line anchor against live code; see handoff §7/§11–14). Prior: 2026-04-24 (spec folder bootstrapped; entries D1–D11 captured from existing code and chat history).

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

**Where:** [2_stock_visualizer.py:345-360](../2_stock_visualizer.py#L345) `db_mark_period_fresh()`; [2_stock_visualizer.py:233-239](../2_stock_visualizer.py#L233) bars table schema.

---

## D3 — Intraday TTL drops to 6 s during market hours

**Rule.** `_effective_ttl()` returns **6 s** whenever the interval is `1m` / `5m` / `1h` **and** the resolved exchange is currently open. Otherwise the base TTL applies (30 s for `5m`, 5 min for `1h`, etc.).

**Why.** The chart iframe polls `/api/data` every 6 s during market hours. Without a matching TTL, every poll would hit yfinance, which is unfriendly and slow. With the 6-s TTL, the second poll inside a 6-s window returns cached data instantly.

**Where:** [2_stock_visualizer.py:554-558](../2_stock_visualizer.py#L554) `_effective_ttl()`.

---

## D4 — Stale-while-revalidate on period switches

**Rule.** The frontend always sends two requests on a period switch:
1. `mode=swr` — zero network, returns cached bars instantly.
2. `mode=fresh` — refreshes stale tickers from yfinance in parallel.

This matches the user memory _Apply stale-while-revalidate by default_. The UI is never blocked by the network.

**Implementation.** `/api/data_batch?mode=swr` short-circuits all fetches. Tickers with no cache row are omitted from the response (not `None`). The frontend notices the omission and re-requests only those tickers in fresh mode.

**Where:** [2_stock_visualizer.py:648-659](../2_stock_visualizer.py#L648) (SWR branch of `get_chart_data_batch`); frontend consumer in [index.html](../index.html) period-button handler `broadcastPeriodWithBatch()` around line 2885.

---

## D5 — 1D session anchor uses the LAST bar's date, not the first

**Problem.** `PERIOD_WINDOW_DAYS["1d"] = 2` — the DB returns up to 2 days of 1-minute bars so that fresh cache rows from yesterday's session don't fall out of the window. Consequence: when the chart renders, `records[0]` may be a stale pre-session bar from yesterday. If the chart uses `records[0]` to compute session-open epoch and % baseline, it shows the **wrong session** and computes % vs. "the day before yesterday's close".

**Decision (2026-04-24).** Anchor on the last bar's timezone-local date:
- `sessionOpenEpoch` = today's exchange open in the last bar's tz day.
- `prevClose1d` = last bar **before** `sessionOpenEpoch` — i.e., yesterday's true close.
- Filter `records` to only bars with `t >= sessionOpenEpoch` before building candles / line / volume series.
- % baseline for 1D is `prevClose1d` if available, else `first.o` (new-install fallback).

**Where:** [1_chart_template.html:860-873](../_outputs/templates/1_chart_template.html#L860) (session anchor + filter); [1_chart_template.html:940](../_outputs/templates/1_chart_template.html#L940) (% baseline in on-chart panel); matching block in the price-info broadcast ~line 980. (Note: the displayed price itself now comes from the canonical `last_price`, D19; `prevClose1d` is still the 1d baseline.)

**Alternative considered.** Shrink `PERIOD_WINDOW_DAYS["1d"]` to less than 1 day server-side. Rejected: it would fail over weekends and holidays when the most recent session may be ≥2 calendar days old.

---

## D6 — Internet-calibrated clock, not system clock

**Rule.** `is_market_open()` never reads `datetime.utcnow()` directly. It calls `calibrated_utcnow()`, which adds `_clock_offset_s` measured against `https://www.google.com`'s RFC 2822 `Date:` header.

**Why.** Users with a skewed system clock (laptops that haven't synced NTP in weeks, VMs with drifted time) would otherwise see wrong market-open state, and the 6-s live poll would re-fetch outside actual trading hours.

**Sync policy.** First sync is **synchronous** before Flask starts accepting requests. A background thread re-syncs every 30 min.

**Browser side.** The calibrated offset is published via `/api/time_info`. The frontend calibrates `Date.now()` once at load and computes `isMarketOpenNow()` locally — no round-trip per poll.

**Where:** [2_stock_visualizer.py:115-165](../2_stock_visualizer.py#L115).

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

**Where:** [index.html:1286](../index.html#L1286) `persistSession()`.

---

## D9 — Dirty-check DOM writes in the ticker-list

**Rule.** `updateTickerListData()` is called every frame when the chat is open, but only writes to a DOM node when the underlying value has changed. `buildTickerListRows()` is called only when `ring.length` or `_closestInteractive` changes (tracked via `_lastTickerListGen` + `_lastClosestForList`).

**Why.** Writing to 20+ DOM nodes every frame (~60 fps) stutters on weaker machines. Dirty-checks cut writes to what actually changes — typically just the "closest" billboard's price + change.

**Rebuild triggers.** Beyond gen/closest changes, a rebuild is also forced when the sort-mode signature changes (for `percent` sort) and when `←/→` swaps a billboard (which otherwise wouldn't change gen or closest).

**Where:** [index.html:2057](../index.html#L2057) `buildTickerListRows()`; rebuild trigger on swap at [index.html:3414](../index.html#L3414) added 2026-04-24.

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

**Where:** broadcast at [index.html:2425](../index.html#L2425) `broadcastTargetPrice()`; consumer at [1_chart_template.html:1087](../_outputs/templates/1_chart_template.html#L1087).

---

## D12 — Hard-coded port 5000

**Rule.** `PORT = 5000` in [2_stock_visualizer.py:903](../2_stock_visualizer.py#L903). No CLI flag, no env var.

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

**Where:** [index.html:2340](../index.html#L2340) `toggleMarkerInputRow()`; [index.html:2123](../index.html#L2123) stale-entry loop in `buildTickerListRows()`.

**Invariant for future changes.** Any code path that removes a focused input from the DOM must blur it first and clear the window selection. Treat `input.select()` as leaving residue; pair it with an explicit cleanup on close.

**Extended 2026-04-24** to the state-2 entry-target inputs (`.cb-target-input` inside `#chat-body`). Same bug class: every state-2 exit path (`chat-state-btn` close, `chat-back-btn` to state 1, giraffe `ticker-go` toggle, and the closest-ticker flip inside `updateTickerDetail` that calls `$chatBody.replaceChildren()`) now funnels through a shared helper `clearChatBodyCaret()` that blurs the focused descendant and clears window selection before the DOM mutation or class flip happens. Helper at [index.html:2742](../index.html#L2742); call sites at [index.html:2554](../index.html#L2554), [2751](../index.html#L2751), [2760](../index.html#L2760), [2785](../index.html#L2785).

**Generalised invariant.** When hiding or rebuilding any panel that may contain a focused text input, call the matching `clear*Caret()` helper *before* the DOM / class mutation. Removing the input after it has lost focus is safe; removing it while focused — or hiding its container with focus still inside — can leak a phantom caret on Chromium.

---

## D15 — Closest-billboard is identity-based, and overridden by `_clickOrbitTarget` during rotation

**Problem (2026-04-24).** Two separate bugs compounded when the ticker list was in percent-sort mode:

1. **`.closest` was keyed on visual row index (`i === 0`).** In percent-sort mode the top visual row is the most-negative-%Δ billboard, not the angularly-closest one. State 2's CSS (`body.chat-open.chat-expanded .tl-row:not(.closest) { display: none; }`) therefore showed the wrong row as the "only one".
2. **`_closestInteractive` is recomputed each frame from camera distance.** When the user clicked a giraffe button on a non-closest row, `_clickOrbitTarget` queued a camera rotation — but the clicked billboard does not become geometrically closest until the rotation completes. During the transition, the overlay, state-2 detail, and `.closest` row all pointed at the old billboard. The giraffe click appeared to jump to the wrong ticker.

**Decision.**
- `updateBillboardProjection()` still computes `_closestInteractive` from camera distance, then **overrides** it with `_clickOrbitTarget` while a rotation is in flight. This makes `_closestInteractive` an "intent-aware" pointer: what the UI should be anchored on *right now*, not strictly what's geometrically closest.
- `buildTickerListRows()` marks `.closest` by **identity** (`bb === _closestInteractive`) instead of visual position. In default sort mode the two are equivalent (closest sorts to index 0); in percent-sort mode the identity check keeps state 2 showing the correct billboard.

**Where:** [index.html:3578](../index.html#L3578) (`updateBillboardProjection()` override block); [index.html:2207](../index.html#L2207) (`.closest` identity check in `buildTickerListRows()`).

**Invariant for future changes.** `_closestInteractive` is the single source of truth for "which billboard does the UI represent". Any new UI surface that needs to track the selected billboard must read `_closestInteractive`, not re-derive from distance or from ticker-list row position.

---

## D16 — Ticker source: string-enum, per-ticker, user-action-wins

**Feature (2026-04-24).** Tickers gain a `source` attribute — `manual` for ones the user typed via Go / URL-launch, `auto` for ones a future ingestion script will seed. Auto tickers render in pink; manual keeps the default text color.

**Design choices.**

1. **String enum, not numeric codes.** `TICKER_SOURCE = { MANUAL: 'manual', AUTO: 'auto' }` (frozen). Strings serialize transparently to `localStorage`, read cleanly in DevTools, and a future `'watchlist'` / `'algorithmic'` value slots in without a migration.

2. **Keyed per-ticker, not per-slot.** Storage key `sv.tickerSource.<TICKER>` mirrors the existing `sv.markerText.*` / `sv.entryTargets.*` pattern. A ticker's provenance doesn't change when the user moves it around the ring or reshuffles billboards.

3. **Missing key ≡ `manual`.** Backwards-compatible default: every ticker currently in any user's localStorage was entered by hand, so absence-means-manual is safe. The auto-script must opt into pink by writing `"auto"` explicitly.

4. **User-action-wins conflict policy.** `markTickerManual()` force-overwrites any existing entry (including `auto`). `markTickerAuto()` refuses to write if an entry already exists. Result: if the user types a symbol that was previously ingested, it flips back to manual / default color; the auto-script can never "downgrade" a user's ticker to pink.

5. **Rendering — ticker symbol only.** Only `.tl-ticker` (ticker-list rows, states 1 + 2) and `.po-ticker` (bottom bar, state 0) get the `.source-auto` CSS class. Company, price, change, target labels all keep their existing color rules. The chart-iframe's `#ticker-label` is left alone — the spec scopes this purely to the chat-container surfaces.

6. **Manual-stamping call sites (today).**
   - Go button success path — `markTickerManual(ticker)` after resolution. Also invalidates `_lastTickerListGen` so the row's pink class re-evaluates on the next frame when a manual write overwrites a prior auto.
   - URL-launch `?tickers=A,B,C` / cached-session boot — initial-ring loop calls `markTickerManual()` on any ticker that has no existing source entry. Makes the storage state explicit from day 1 so the future auto-script has a clean default to diff against.
   - Ring-plus button (`+`) — creates a blank billboard, no ticker. The eventual Go that assigns a ticker is what stamps manual.

**Alternative considered:** storing source on the billboard object rather than in localStorage. Rejected — billboards are transient UI objects rebuilt from `ringTickers`; storage must outlive them.

**Where:**
- Enum + helpers: [index.html:1118-1135](../index.html#L1118) (`TICKER_SOURCE`, `getTickerSource`, `markTickerManual`, `markTickerAuto`).
- Initial-ring bootstrap: [index.html:1255-1259](../index.html#L1255).
- Go handler: [index.html:3014](../index.html#L3014).
- CSS: [index.html:759-763](../index.html#L759) (`.source-auto` rule).
- Ticker-list render: [index.html:2296](../index.html#L2296) (dirty-check + class toggle).
- Price-overlay render: [index.html:1991](../index.html#L1991).

**Future integration.** When the auto-ingest script ships, it should call `markTickerAuto(symbol)` for every ticker it adds. It must NOT call `markTickerManual`, and it must NOT bypass the helpers and write to localStorage directly — both would defeat the user-action-wins policy.

---

## D17 — Market-closed launch must show the previous close, not 0.00 (2026-06-05)

**Symptom.** Launching the app while the market is closed showed a blank / `0.00` price on every
billboard. The price only appeared after the user manually clicked a *different* period — which then
displayed correctly.

**Root cause (three compounding factors).**
1. The parent does **not** batch-fetch on initial load; each iframe self-loads via `loadChart()` using its
   `src` query params, which default to **period `1d` / interval `1m`**.
2. yfinance's `history(period="1d", interval="1m")` returns an **empty** frame when there is no *current*
   session (market closed / pre-open). So the fresh fetch returned `[]`, nothing was cached, and the chart
   had no bars. Clicking another period (`5d`, `1mo`, …) requests a window yfinance *does* return even when
   closed — hence the "switch period to fix it" workaround.
3. Even when prior-session 1m bars were cached, the `1d` serving window was only **2 days**, so the last
   session fell out of range over a weekend / holiday.

**Decision.**
- **Intraday empty-fetch fallback.** `fetch_prices()` / `fetch_prices_batch()` retry once with a wider
  window (`INTRADAY_FALLBACK_PERIOD = {1m:"5d", 5m:"1mo", 1h:"3mo"}`) whenever an intraday request comes back
  empty. The frontend already anchors on the last bar's session (D5), so the extra bars don't change the
  drawing — they just make the last session (and thus the previous close) available.
- **Widen `PERIOD_WINDOW_DAYS["1d"]` 2 → 5.** Covers a 3-day holiday weekend so the previous session is
  always in the serving window.
- **Relax the chart price-panel guard `records.length >= 2` → `>= 1`.** A closed-market session can collapse
  to a single visible bar; we still render its close (with `first` falling back to `last` for the % math)
  instead of leaving the panel blank.

Together these mean the **launch path now renders the previous close immediately** — no manual period
switch needed.

**Why fallback + window, not just one.** The fallback gets the bars *into* the cache; the wider window keeps
them *reachable* when serving `1d` after a multi-day gap. Either alone leaves a hole (fresh-empty cache, or
post-weekend launch respectively).

**Termination.** `fetch_prices_batch()` recurses with the fallback period; it terminates because the
fallback period maps to itself for that interval (`fb == period` → no further retry).

**Trade-off.** Market-hours `1d` polls can now return up to 5 days of cached 1m bars instead of 2. On
localhost the payload delta (~1–2k small rows) is negligible; the frontend filters to the last session
regardless.

**Where:** [2_stock_visualizer.py:64](../2_stock_visualizer.py#L64) `INTRADAY_FALLBACK_PERIOD`;
[2_stock_visualizer.py:465](../2_stock_visualizer.py#L465) `fetch_prices()` fallback;
[2_stock_visualizer.py:499](../2_stock_visualizer.py#L499) `fetch_prices_batch()` fallback;
[2_stock_visualizer.py:202](../2_stock_visualizer.py#L202) `PERIOD_WINDOW_DAYS["1d"]`;
[1_chart_template.html:936](../_outputs/templates/1_chart_template.html#L936) and
[1_chart_template.html:978](../_outputs/templates/1_chart_template.html#L978) (price-panel + broadcast guards).

---

## D18 — Drop NaN bars: yfinance's incomplete-period row crashed the chart at market close (2026-06-05)

**Symptom.** With the market closed, switching to **3mo or any longer period** (intervals `1d` / `1wk` /
`1mo`) threw `Failed to render: Cannot read properties of null (reading 'toFixed')`. Intraday periods
(`1d` / `5d` / `1mo`) were unaffected.

**Root cause.** For a daily/weekly/monthly request, yfinance appends a row for the **current, incomplete
period** (today / this week / this month). When that period hasn't traded yet (market closed / pre-open) its
OHLC are `NaN`. `_df_to_records` only skipped `c == 0`, and `NaN != 0`, so the row slipped through:
`float(row["Close"]) → nan` → `round(nan,4) → nan` → `db_upsert_bars` writes it → **SQLite coerces NaN to
NULL** → `db_get_bars` returns `c = None` → JSON `null` → the client's `last.c.toFixed(2)` throws. Intraday
intervals don't emit a NaN trailing row (yfinance just omits future bars), which is why only 3mo+ broke.

**Decision — defence in depth (3 layers).**
1. **Ingest filter.** `_df_to_records()` skips any row whose O/H/L/C isn't fully finite (`pd.isna`), and
   coerces a NaN volume to 0. No NaN/NULL bar is ever written again.
2. **Serve filter.** `db_get_bars()` adds `AND c IS NOT NULL`, so NULL rows an **older build already wrote**
   into a user's `prices.db` are ignored rather than served. (They remain as harmless dead rows; the next
   fresh fetch simply doesn't re-emit them.)
3. **Client guard.** `renderChart()` filters `data.records` to finite-close bars before use, so no malformed
   payload from any source can reach `toFixed`.

**Why all three.** #1 stops the bleak going forward; #2 neutralises caches already polluted by the old code
(the committed `prices.db` had such rows); #3 is a cheap last line so the chart can never crash on bar data
again. Any one alone leaves a gap (existing NULLs, or a future provider with the same quirk).

**Interaction with the swap surface (§11).** A future licensed-provider swap must keep emitting only finite
bars (or rely on filter #2/#3). The cleaning lives in `_df_to_records`, shared by `fetch_prices` /
`fetch_prices_batch`.

**Where:** [2_stock_visualizer.py:421](../2_stock_visualizer.py#L421) `_df_to_records()` NaN skip;
[2_stock_visualizer.py:296](../2_stock_visualizer.py#L296) `db_get_bars()` `c IS NOT NULL`;
[1_chart_template.html:846](../_outputs/templates/1_chart_template.html#L846) `renderChart()` finite-close filter.

---

## D19 — Current price is canonical (one value per ticker), independent of the selected period (2026-06-05)

**Symptom.** Outside market hours, the headline "current price" changed when switching periods — e.g. AAPL
read 272.53 on 1D, 273.48 on 5D, 272.51 on 3Mo/6Mo/1Y, 270.23 on 2Y/5Y, 260.48 on MAX. The user reasonably
expects the current price to be the same on every period; only the gain/loss over the window should differ.

**Root cause.** The displayed price was `records[last].c` — the close of the **last bar of the selected
period's interval**. Each period uses a different interval (1D→1m, 5D→5m, 1mo→1h, 3–12mo→1d, 2–5y→1wk,
max→1mo), each cached independently with its own TTL. Coarse intervals (`1wk` TTL 7d, `1mo` TTL 30d) keep a
**stale trailing bar** that is a week/month-to-date snapshot from whenever it was last fetched, so the
"current price" disagreed across periods. (Confirmed from the cache: the `1mo` bar was an April-month bar
last fetched ~2 weeks before the `1m`/`1d` data.)

**Decision.** Decouple the headline price from the period's bars. The server attaches a **canonical
`last_price`** (and `last_price_at`) to every payload, read from a single interval per ticker:
- **Market open →** `1m` (the live feed — preserves the ticking 1D experience).
- **Market closed →** `1d` (the official last-session close).

`_price_interval(exchange)` picks the interval; `_ensure_price_interval_fresh()` refreshes it (self-gating on
TTL) on fresh-mode requests when the requested interval isn't already the canonical one, so the value is both
**consistent and current** regardless of which period triggered the request. The client uses `last_price` for
the price label, change, target %, and title; the period's `records` now only drive the **chart shape** and
the **gain/loss baseline** (`first.o`, or `prevClose1d` for 1d). Change = `last_price − baseline`, so only the
gain/loss varies per period — exactly the expected behaviour.

**Why interval-switched rather than always-daily.** Always-daily would freeze the live 1D headline during
market hours (daily TTL is 1 day, so it wouldn't tick). Always-intraday would be stale/missing for tickers
only ever viewed at long periods. Keying off market state gives the right source in both regimes; tickers on
different exchanges resolve independently (the batch path groups refreshes by `(interval, anchor)`).

**Fallback.** `last_price` is `null` only when the canonical interval has nothing cached yet (e.g. first SWR
paint before the fresh call lands); the client falls back to the period's last bar for that one frame.

**Consequence / known wrinkle (RESOLVED by D20).** Originally the chart's right-edge bar on a coarse period
could still be a stale month/week close while the headline showed the fresh canonical price. **D20** closes
that gap — the trailing bar is now pinned to the canonical price and the `1wk`/`1mo` TTLs were shortened.

**Interaction with the swap surface (§11/D17).** `last_price` is derived purely from cached bars via
`db_get_last_close`, and the canonical interval is filled by the same `fetch_prices`/`fetch_prices_batch`
used everywhere — a future licensed-provider swap needs no special handling here.

**Where:** [2_stock_visualizer.py:312](../2_stock_visualizer.py#L312) `db_get_last_close()`;
[2_stock_visualizer.py:517](../2_stock_visualizer.py#L517) `_build_payload()` (`last_price`/`last_price_at`);
[2_stock_visualizer.py:568](../2_stock_visualizer.py#L568) `PRICE_INTERVAL_ANCHOR` /
[571](../2_stock_visualizer.py#L571) `_price_interval()` / [575](../2_stock_visualizer.py#L575)
`_ensure_price_interval_fresh()`; fresh-path hooks in `get_chart_data` and `get_chart_data_batch`;
client `currentPrice` at [1_chart_template.html:882](../_outputs/templates/1_chart_template.html#L882),
used in the price panel ([936](../_outputs/templates/1_chart_template.html#L936)) and broadcast
([978](../_outputs/templates/1_chart_template.html#L978)).

---

## D20 — Coarse chart bars made current: trailing-bar pin + shorter 1wk/1mo TTLs (2026-06-05)

**Context.** D19 made the *headline* price canonical, but left a "known wrinkle": on a coarse period at
market close the chart's right-edge bar could still be a stale week/month-to-date close (e.g. MAX ending at
260.48 while the headline read 272.51), because `1wk`/`1mo` data was refreshed at most every 7/30 days and the
trailing bar is the only one that moves day-to-day. The user asked to make the coarse chart bars themselves
current.

**Decision — two complementary changes.**
1. **Shorter coarse TTLs.** `CACHE_TTL["1wk"]` and `["1mo"]` dropped from 7 days / 30 days to **1 day**. Older
   bars in a weekly/monthly series never change, so a daily re-fetch (one cheap yfinance call when the period
   is viewed) keeps the trailing bar — and any just-completed week/month — current without missing bars.
2. **Trailing-bar pin.** `_build_payload()` overwrites the **last served bar's close** with the canonical
   `last_price` (widening high/low so the candle stays valid), but **only when `last_price_at` is newer than
   that bar** (`last["t"] > records[-1]["t"]`). This makes the chart's right edge equal the headline exactly,
   and during market hours it tracks the live 1m tick.

**Why the timestamp guard matters.** It targets exactly the bars that lag: a coarse `1wk`/`1mo` trailing bar
(stamped at week/month start) and a still-forming daily bar during open (stamped at day start, older than the
live 1m). It deliberately does **not** rewrite an intraday series' last bar (already the freshest point), so
the 1D/5D charts keep their real last tick. For the daily periods (3–12 mo) at close the trailing daily bar
already equals the canonical daily close, so the guard is a no-op there.

**Why both, not just one.** The pin alone fixes the trailing *close* but can't add a complete week/month bar
that went stale (cache older than one period → a missing bar, leaving a gap); the shorter TTL refetches those.
The shorter TTL alone leaves the trailing bar up to ~1 day behind during market hours; the pin makes it live.
Together the coarse chart is both complete and current.

**Verified.** With the committed cache at market close, the served trailing close for 2Y/5Y/MAX is now 272.51
(was 270.23 / 260.48), candles remain valid (`low ≤ close ≤ high`), and it matches the headline.

**Trade-off.** The pinned trailing bar's high/low may slightly overstate the true week/month extreme if the
canonical price is an outlier (it widens the range to include the current price). This only affects the
single in-progress bar and is the expected behaviour of a "current period so far" candle.

**Where:** [2_stock_visualizer.py:75](../2_stock_visualizer.py#L75) `CACHE_TTL` (`1wk`/`1mo` = 1 day);
[2_stock_visualizer.py:517](../2_stock_visualizer.py#L517) `_build_payload()` trailing-bar pin (the
`last["t"] > records[-1]["t"]` block).
