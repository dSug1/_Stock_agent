# 4_List_renderer — Decisions log

Records the *why* behind architecture choices and any deviation from the spec. New
decisions append here as `Dn`. Spec describes the target; this records what was built and
the reasoning. Mirrors the decisions logs in `2_Funds_parser`, `3_Biopharmcatalyst_parser`,
and `0_Renderer`.

---

## D1 (2026-06-19) — Rendering contract: stable template + per-run sidecar

**Decision.** Rendering is a fixed `Outputs/list_results.html` template that reads
`window.LIST_DATA` from a `Outputs/list_results_data.js` sidecar. Only the sidecar is
rewritten per render; the HTML is hash-versioned and changes only on restyle.

**Why.** Matches the repo-wide `feedback_html_template_data_split` and
`feedback_browser_writes_via_local_server` memories and the `0_Renderer` /
`3_Biopharmcatalyst_parser` precedent. Keeps the visual layer stable while data churns,
and makes "adaptive display" (one template, many sources) the core of the product.

**State.** Built in v0.

---

## D2 (2026-06-19) — Adapters keyed by source kind; one template for all

**Decision.** Each data source is fetched by a pluggable adapter keyed by `source.kind`,
returning raw items mapped to the single Highlight schema. Three seed adapters shipped:
`file` (JSON), `biopharm` (reads `3_Biopharmcatalyst_parser/data/biotech.db`, top-N by
composite_score), `news` (RSS for FierceBiotech / Le Figaro / CNBC).

**Why.** Demonstrates the adaptive-display thesis end-to-end before the heavier subsystems
land, and gives M3/M5 a concrete contract to target. `normalize_result` is the single
projection point onto the template's known fields, so a noisy source cannot bloat output.

**Note.** `news` is the first adapter that reaches outside the repo (live RSS at render
time). The parsing quirks it surfaced (FierceBiotech wraps the title in an `<a>`; Le Figaro
UTF-8; HTML entities/CDATA) are exactly the cases D4's Claude resolver will generalize.

**State.** Built in v0.

---

## D3 (2026-06-19) — Single SQLite DB, additive migrations, SQL-only state

**Decision.** All durable state lives in `data/list_renderer.db` (tables: `sources`,
`recipes`, `items`, `interactions`, `interests`, `ranking_state`, `render_runs`), evolved
with additive migrations. The sidecar JSON is the sole on-disk serialization exception
(it is the browser handoff).

**Why.** Consistent with every other module in this repo (SQL-only durable storage, `*.db`
gitignored, additive migration pattern). Keeps the learning signal log and recipe cache
queryable and durable.

**State.** Planned (Phase 1).

---

## D4 (2026-06-19) — Claude resolves *recipes*, not renders; cache-first, cost-gated

**Decision.** The Claude API is used to infer how to **fetch** and **extract/clean** a
source, producing a persisted *recipe*. Renders replay cached recipes at $0. Billed calls
fire only on (a) a new source with no recipe, (b) an adapter parse failure, or (c)
structural drift. Every billed dispatch sits behind a mandatory `[y/N]` gate (only explicit
`--yes` bypasses, single-shot), uses mandatory prompt caching, and batches all pending
resolutions into one run.

**Why.** Keeps Claude out of the per-render hot path so the app stays fast and cheap, while
still self-healing on messy/new sources. Directly mirrors the cost discipline proven in
2_Funds M6 and 3_Biopharm M7/M8 (`claude-api` memory; mandatory cost gates).

**Why not per-render Claude summarization.** Rejected as the default: it would put a paid
call on every refresh. Left as an optional future enhancement, gated separately.

**State.** Planned (Phase 3). Resolver model default: latest Opus (`claude-opus-4-8`),
revisit per cost.

---

## D5 (2026-06-19) — Ranking = transparent weighted-feature model, learned offline

**Decision.** Interest ranking is a transparent weighted sum of per-item features
(`score = Σ weight_f · feature_f`). Human-authored base weights live in
`config/ranking.yaml` (inspectable, user-tunable); learned deltas are fit offline from the
`interactions` log and stored in `ranking_state`. Cold start orders by recency + declared
interest. Re-fit is a free, offline batch step.

**Why.** Reuses the exact philosophy of 2_Funds M6b's 14-component user-tunable modifier —
the user has repeatedly favored transparent, tunable scoring over black boxes. Lets ranking
ship before there is enough data for a real fit, and graduates from naïve empirical lift to
regression once rows accrue (same data-sufficiency gating as M7-β/γ).

**State.** Planned (Phase 5).

---

## D6 (2026-06-19) — Interactions captured via a local server; signals append-only

**Decision.** A local HTTP server (stdlib `http.server`) serves the board and exposes
`/api/*` endpoints for source selection, interest input, and interaction capture
(impression, open, read_more, like, hide, dwell, scroll_past). Durable signals persist to
SQLite append-only; lightweight UI prefs mirror to localStorage with the server as source of
truth.

**Why.** `feedback_browser_writes_via_local_server` (FSA save-picker UX was rejected
project-wide) and `feedback_cache_user_prefs`. Append-only logging mirrors M7-α's snapshot
discipline so retuning the model never destroys the historical signal.

**State.** Planned (Phase 4). Implicit dwell/scroll tracking is pending user confirmation
(spec §11 Q3).

---

## D7 (2026-06-19) — Interest input auto-resolves to sources; audited, not interactive

**Decision.** When the user declares an area of interest (site / topic / query / ticker),
M5 classifies it, resolves it into concrete source(s) (via the M3 resolver for websites,
mappings for topics/queries), registers them (`origin='discovered'`), and adds the topic as
a ranking feature. Resolution is automatic with a confidence + audit row; low-confidence
results are surfaced for optional batched review, never per-item interactive Q&A.

**Why.** `feedback_avoid_multiplying_user_requests` — handle ambiguity in code with flags/
audit files, not runtime prompts. Batched triage is the carved-out exception.

**State.** Planned (Phase 6).

---

## D8 (2026-06-19) — SWR on every fetch path

**Decision.** Renders serve the cached `items` instantly, then refresh sources in the
background and rewrite the sidecar. No fetch blocks first paint.

**Why.** `feedback_swr_pattern` and the `0_Renderer` precedent. Minimizes perceived
latency, which matters once N sources are fetched per board.

**State.** Planned (Phase 2).

---

## D9 (2026-06-19) — Local-first; hosting deferred behind auth + provider review

**Decision.** Curator runs locally: one process, local SQLite, local server, outbound only
for source fetch + Claude. Public hosting is out of scope for v1 and, when pursued, must add
auth and a licensed data provider for any finance source.

**Why.** Matches the whole repo's local-personal posture and the `project_data_provider_switch`
(yfinance ToS) constraint. Avoids baking in assumptions that a hosted multi-user version
would invalidate.

**State.** Standing constraint for the v1 *build*. **Amended 2026-06-19:** the hosted
multi-user architecture is now *designed* (spec §13, D11–D15) so module boundaries don't have
to be reworked later — but local single-user remains the v1 build target.

---

## D10 (2026-06-19) — First real source kinds: News/RSS + Social/forums

**Decision (user-confirmed).** The first production source kinds are **News/RSS** (generic
`rss` + scraped `web`) and **Social/forums** (Reddit, X/Twitter, Hacker News). The repo's own
`biopharm`/finance pipelines remain *demo* adapters, not a v1 build priority.

**Consequences.** M2's generic-adapter order becomes `rss` → `web` → social-feed adapters;
M5 interest-input classification + discovery targets sites/subreddits/handles/HN topics
first. Social APIs bring their own auth/rate-limit concerns (e.g. Reddit OAuth, X API tiers,
HN Firebase API) — capture per-source auth in `sources.config_json`; HN's API is open, Reddit
needs an app token, X is paid-tier gated (flag at adapter time, fail-open to skip).

**Why.** Direct user steer (2026-06-19). News was already prototyped; social/forums are the
declared next priority.

**State.** Planned (Phases 2/6).

---

## D11 (2026-06-19) — Three-tier storage model (shared / per-user / local)

**Decision.** Storage is partitioned by *ownership* into three tiers (spec §13.1):
**A. Shared / user-independent** (Claude recipes, source catalog, fetched content cache +
enrichment) — resolved/fetched once and reused by all; **B. Per-user profile** (subscriptions,
declared interests, interaction log, learned ranking weights, synced settings) — private,
cross-device, isolated by `user_id`; **C. Local / client** (session token, cached last
sidecar, optimistic interaction buffer, transient UI state). In local single-user mode all
three collapse onto one machine (SQLite + localStorage).

**Why.** Cleanly answers "what lives where" for hosting and keeps the per-user layer thin.
Everything a user sees is a *join* of shared Tier-A content with their Tier-B model.

**State.** Designed; built incrementally (Tier A/B tables already implied by the §5 model).

---

## D12 (2026-06-19) — Shared recipe + content layer is the cost firewall

**Decision.** Claude-resolved **recipes** and **fetched content** (items + tags/entities/
dedup/embeddings) are Tier A — keyed by source signature, written only by system workers, and
shared across every subscriber. A source resolves once and fetches once per TTL regardless of
how many users follow it.

**Why.** Makes Claude resolution **O(distinct sources)** and source fetching **O(distinct
sources × cadence)** — neither scales with user count. A new user following known sources
costs ~$0 of Claude and adds no extra outbound fetches. This is the economic justification for
hosting (spec §13.3) and the direct generalization of the user's "how to fetch a particular
website is user-independent" observation.

**State.** Designed.

---

## D13 (2026-06-19) — Hosted cost governance replaces the interactive gate

**Decision.** In hosted mode the local `[y/N]` Claude gate (D4) is replaced by **system-level
budget governance**: a per-day global Claude spend cap, a new-domain resolution quota + queue,
global dedup (resolve a domain once), allow/deny lists, and admin review for low-confidence or
novel domains. Resolution runs in background workers, never in a user request.

**Why.** There is no human at a terminal in a serverless deployment; cost control must be
automatic and global, not per-dispatch interactive. Preserves the cache-first spirit of D4
while fitting a multi-tenant runtime.

**State.** Designed (hosted only). Local mode keeps the D4 `[y/N]` gate.

---

## D14 (2026-06-19) — Multi-tenancy: isolation, system-only shared writes, privacy

**Decision.** (a) Tier-A (shared) tables are written **only by system workers**, never
directly by user requests — prevents recipe/content poisoning via a crafted source. (b) Tier-B
rows are isolated per `user_id` (row-level security). (c) Fetched third-party content is
**untrusted** and sanitized before reaching the template. (d) Interaction data is private;
Tier A holds no per-user PII; cross-user popularity priors are opt-in + anonymized; support
data export + delete.

**Why.** A shared layer that any user can write is an attack surface (poisoning, scraping for
others' signals). System-mediated writes + tenant isolation + untrusted-content handling are
the minimum safe multi-tenant posture.

**State.** Designed (hosted only).

---

## D15 (2026-06-19) — Rendering delivery: file sidecar (local) vs /api/board (hosted)

**Decision.** The Highlight schema (spec §4.3) is identical in both topologies. Local mode
writes `list_results_data.js` and the template loads `window.LIST_DATA` via `<script>`; hosted
mode serves the same JSON from `GET /api/board`. A small loader shim in the template selects
the mode (static sidecar present → use it; else fetch the API).

**Why.** One template, one data contract, two delivery mechanisms — keeps the renderer (M0)
unchanged across topologies and avoids forking the template.

**State.** Designed; the loader shim lands when hosted mode is built.

---

## Resolved open decisions (2026-06-19)

- **OD-b Claude boundary** → ✅ recipe-resolution only (locks D4). No live per-render
  summarization in v1.
- **OD-c Implicit tracking** → ✅ implicit + explicit signals both in scope (locks D6).
- **OD-e Source breadth** → ✅ News/RSS + Social/forums first (see D10).

## D21 (2026-06-19) — Deterministic user rules layer over learned ranking

**Decision (proposed).** Add a small **rules layer** the user controls directly, applied on
top of M6's learned ranking: **mute** rules (hide items matching a keyword/topic/source — a
hard filter) and **boost** rules (prioritize a topic/source — a weight bump). Borrowed from
Feedly Leo / Inoreader.

**Why.** Learned ranking is implicit and slow to correct; users want an immediate, legible
override ("never show me X", "always float Y"). Composes cleanly with D5's transparent model
(rules are just user-pinned feature weights + filters) and the user's preference for tunable
scoring.

**State.** Proposed for Phase 5 (with ranking). Stored as `rules` rows in Tier B.

---

## D22 (2026-06-19) — Per-user seen/unread item state

**Decision (proposed).** Track lightweight per-user **seen/unread** state (derive from
`impression` interactions; optionally an explicit mark-read). Surfaces unread counts and lets
ranking de-prioritize already-seen items.

**Why.** Universal in readers; expected UX. Cheap (reuses the interaction log / a small
per-(user,item) state) and improves ranking by not re-showing consumed items.

**State.** Proposed for Phase 4 (with interactions).

---

## D23 (2026-06-19) — AI per-article summaries excluded from v1

**Decision (proposed).** No per-article LLM summaries/digests in v1. This reinforces D4
(Claude is not in the per-render path). If added later, summaries are **cached Tier-A
enrichment** (computed once per item, shared across users, opt-in, cost-gated) — never a live
per-render call.

**Why.** Summaries are the most-requested AI-reader feature (Particle, Perplexity, Artifact)
but the most expensive if naive. Excluding them v1 keeps the cost model intact; the Tier-A
enrichment path keeps the door open economically (resolve/summarize once, serve all).

**State.** Proposed; excluded v1, optional post-v1 enrichment. The "who pays for the summary
task" question is a deferred decision — see §15 backlog + D24 for the v1 forward-compatibility
seams that keep it addable.

---

## D24 (2026-06-19) — v1 forward-compatibility for paid AI enrichment + billing

**Decision.** AI summaries are deferred (D23), but their future monetization must not require
reworking v1. So v1 builds three **seams** now — each with a single trivial implementation —
so summaries and any "who pays" model plug in later as additions, not rewrites:

1. **One generic LLM task runner.** Every Claude call (v1: only the M3 resolver) goes through
   a single `run_llm_task(task_type, payload, billing_context)` path that applies the cost
   gate/budget, executes, and logs cost. A summary is later just a new `task_type` on the same
   path — no second call path to build.
2. **Pluggable billing/credential context.** The runner gets its API credential + budget from
   a `billing_context` abstraction, never a hardcoded env key. v1 ships exactly one context
   (`self` = the local user's own key from `.env`). The future payer models are additional
   contexts behind the same interface:
   - **(a) user BYOK** — the user's own Anthropic key (stored encrypted, Tier B/local);
   - **(b) app-owner corporate key** — pooled key + volume discount, metered per user;
   - **(c) advertiser-subsidized** — an ad-funded budget pool covers the task in exchange for
     ad surface.
3. **Per-task payer attribution in the ledger.** The cost ledger (NN-4) records `task_type` +
   `billing_context`/`payer` per call (v1 default `self`). With `user_id` (D16) this makes
   usage meterable per payer — the seed of entitlements/quota/billing reconciliation.

**Storage.** Summaries (when built) are **Tier-A, cached per item, shared across users**
(D23) — an additive column/table on `items`, never per-user, never per-render. The result
schema (§4.3) already tolerates extra fields and a future `sponsored`/ad item kind, so the ad
model needs no template fork.

**Why.** The costly part of adding paid AI features later is retrofitting the call path +
attribution + credential routing. Building these seams in v1 — where there is one task and one
payer — is nearly free and removes that retrofit entirely.

**State.** Build the seams in **Phase 3** (the resolver is v1's only consumer). Privacy
reconciliation deferred with the feature: BYOK key storage and any advertiser attention-data
sharing must remain opt-in + anonymized per D14.

---

## D25 (2026-06-19) — Tech stack: Python (FastAPI) backend + responsive PWA client

**Decision (user-confirmed).**
- **Backend = Python / FastAPI.** The board/interaction/interest API is a FastAPI service that
  reuses the whole `_Stock_agent` Python stack (v0 adapters, ranking, biopharm reader) and is
  the natural home for ranking/ML.
- **Client = one responsive PWA** — the existing template, mobile-first, installable on desktop
  + mobile, offline via service worker. Optional later native wrap (Capacitor/Tauri) with no UI
  rewrite.
- **Hosting = any Python-friendly cloud** (Render / Railway / Fly / Azure Container Apps); the
  static PWA served from any CDN (Vercel fine for the *static client*). "Vercel" was
  illustrative, not mandatory (user-confirmed).
- **Local v1 = the same FastAPI app on `localhost`** (the M7 server). Local and hosted run
  identical code; only the storage backends differ (§13) — collapsing the local/hosted gap.
- **C# rejected:** .NET is not a native serverless runtime on Vercel-class platforms and
  diverges from the repo. **TypeScript/Next.js** is the documented fallback *only if*
  Vercel-native deployment ever becomes a hard requirement; the contract guardrail below makes
  that a swap of the API layer, not the client.

**Guardrail.** The backend sits behind a fixed **HTTP/JSON contract** (`/api/board`,
`/api/interact`, `/api/interest`) + the tier interfaces, so the PWA and data shapes never change
if the API language is ever swapped.

**Why.** Repo + user are Python-centric; ranking is Python-natural; "such as Vercel" reads as a
*class* of host. FastAPI deploys cleanly on Python-friendly clouds and runs identically locally.

**State.** Adopt now. FastAPI server is M7; the template is PWA-ized in v1 (responsive is now a
v1 requirement, D26).

---

## D26 (2026-06-19) — Device target: desktop + mobile via one responsive PWA (was OD-a)

**Decision (user-confirmed).** The app targets **both desktop and mobile** through a single
**responsive, installable PWA** — not separate native codebases. Consequently **mobile-first
responsive layout is a v1 requirement** (moved up from Phase-7 polish), and a service worker
(offline + instant cached paint, Tier C) lands with the client.

**Why.** The template+sidecar architecture already makes the client pure web; a responsive PWA
covers both targets with one codebase and satisfies the offline/SWR goal. Deferring responsive
to polish would force a later template reflow.

**State.** Adopt in v1 (template + M7).

---

## D27 (2026-06-19) — Identity, accounts & auth model (forward-compatible from v1)

**Decision.** Design the identity model now so scaling **localhost single-user → cloud
multi-user is additive, not a migration**. Separation of concerns (spec §16):
- **`users`** = identity/account: `id, kind(local|account), email, display_name,
  role(user|admin), status, plan, byok_key_ref`. v1 holds one row — the `local` user
  (`kind='local'`, no auth).
- **`auth_identities`** = *how* a user signs in: `provider(password|google|github|apple|
  magic_link), provider_subject, password_hash`. One user → many linked identities. Empty in
  v1.
- **`sessions`** = active logins: `token_hash` (never the raw token), `ip_hash`, `user_agent`,
  `created/last_seen/expires`, `revoked`. Empty in v1 (localhost is trusted → no session).
- **`login_audit`** = security/audit log (`event, ip_hash, user_agent, ts`); captures IP for
  anomaly detection + rate-limiting.

**Scale path.** Because all per-user data already carries `user_id` (D16), going multi-user
only (a) **turns on auth** (populate `auth_identities`/`sessions`), (b) **swaps SQLite →
Postgres** (§13), and (c) **replaces the single `local` user with real accounts** — **no
historical-data migration**.

**Sign-in methods.** Prefer OAuth/OIDC (Google/GitHub/Apple) + email magic-link; optional
email+password via **argon2id**. `role` gates the admin review surface (D13).

**IP & privacy (D14).** IP is PII → stored **hashed/truncated** (e.g. /24), short retention,
Tier B only, export/delete honored. Used for security/rate-limit/geo — never sold.

**Security baseline (hosted).** HTTPS-only; argon2id hashing; opaque-random or rotating JWT
session tokens (store only a hash); CSRF protection for cookie sessions; rate-limited auth
endpoints; BYOK keys encrypted at rest (D24).

**v1 behavior.** localhost is single-user + trusted: **no sign-in, no sessions**; the `local`
user is implicit. The auth tables exist as **scaffold** (created in the v1 schema, migration 4)
but stay empty. Auth enforcement + providers are built in the hosted phase.

**State.** `users` enriched + `auth_identities`/`sessions`/`login_audit` scaffold created in the
v1 schema (`src/list_renderer/db.py`); unused locally.

---

## D28 (2026-06-19) — Phase 1 built (persistence + source registry)

**Built.**
- `src/list_renderer/db.py` — SQLite at `data/list_renderer.db`; ordered additive migrations
  (schema v4); tiered schema (Tier A: sources/recipes/items/fetch_log/llm_tasks; Tier B:
  users/boards/subscriptions/interests/interactions/ranking_state; auth scaffold:
  auth_identities/sessions/login_audit). Auto-creates the `local` user + `default` board
  (D16/D18). Tables beyond Phase-1 needs are created now so later phases are additive.
- `src/list_renderer/sources.py` — registry: `seed_from_config`, `add_source` (upsert source +
  subscribe board), `set_on_board`, `list_enabled_sources`. All take `user_id`/`board_id`.
- `config/sources.yaml` — seeds news (on-board) + biopharm + sample (registered, off-board).
- `src/list_renderer/adapters/{news,biopharm,file}.py` — each exposes `fetch_results()` →
  Highlight list; standalone `build_payload_*` (v0 paths) retained.
- `src/list_renderer/pipeline.py` — `build_payload_from_registry`: loads the board's enabled
  sources, dispatches by adapter, stamps `source_id`, merges (fail-open), builds the payload.
  Ranking/dedup are later phases (merges in source/position order for now).
- `scripts/4_render_list.py` — new default `--source registry` (+ `--seed`, `--list-db`); v0
  `--source file|biopharm|news` overrides retained. `run_4_List_render.bat` defaults to
  registry.
- `render.normalize_result` now passes through optional identity/ranking fields
  (`id, source_id, published_at, topics, score`) when present.

**Verified.** Fresh DB auto-seeds 3 sources; registry render = news only (9 items); toggling
biopharm on → 19 items merged; v0 overrides intact; auth scaffold empty; DB gitignored.

**State.** Phase 1 complete. Next: Phase 2 (generic adapters + SWR `items` cache).

---

## D29 (2026-06-19) — Phase 2 built (generic adapters + SWR cache)

**Built.**
- **Generic, config-only adapters** (any feed/DB/API addable without code):
  - `rss` (`adapters/rss.py`) — any RSS/Atom feed by `config.feed_url`.
  - `http_api` (`adapters/http_api.py`) — any JSON API mapped via dotted-path `fields`
    (`items_path`, `headers`, ...). Covers the social/forums priority (Reddit/HN JSON, D10).
  - `sqlite` (`adapters/sqlite_source.py`) — any SQLite DB via `query` + column map.
  - Shared RSS parsing extracted to `adapters/_rss_parse.py`; `news` refactored to reuse it.
- **SWR content cache** (`cache.py` + migration 5): `items.payload_json` (renderable Highlight,
  metadata-only per D17) + `source_state` (freshness + reserved ETag/Last-Modified for NN-2).
  Read-through with **serve-stale-on-failure**: network sources re-fetch only past TTL
  (`config.cache_ttl`, default 900s); local sources (file/biopharm/sqlite) read fresh.
- **Pipeline** dispatches generic + bespoke adapters and applies the cache; CLI gains
  `--refresh` (force re-fetch) and `--no-fetch` (offline, cache-only).
- Seeded generic demo sources (`hn_rss`, `hn_api`) in `sources.yaml` (off-board by default).

**Verified.** Generic `rss` (10) + `http_api` (10) + `news` (9) = 29 merged; cold fetch
populates the cache; warm render = all cache hits (no network); `--no-fetch` serves 29 offline;
`--refresh` re-fetches; `items`=29, `source_state` all `ok`; generic `sqlite` maps biotech.db.

**Not yet (deferred):** the `web` (HTML scrape) adapter is deferred to **Phase 3** — it needs
the Claude resolver to infer selectors (a hand-authored selector adapter would be brittle).
Cross-source **dedup (M4)** is not yet implemented; items merge in source/position order, so the
same story from two sources can appear twice. True background refresh (vs read-through) lands
with the M7 server (Phase 4).

**State.** Phase 2 complete. Next: Phase 3 (Claude resolver → cached recipes; the `run_llm_task`
billing seam from D24) — or Phase 4 (FastAPI server + interactions) if you prefer the UI first.

---

## D30 (2026-06-19) — Phase 3 built (Claude resolver + cost gate + web adapter)

**Built.**
- **LLM layer** (`src/list_renderer/llm/`) — the D24 seam: `run_llm_task` (single call
  path: prompt caching on the system prefix, JSON forced via `output_config.format`, adaptive
  thinking for Opus 4.8) → logs cost + payer to the `llm_tasks` ledger; `BillingContext`
  (v1 = `self`, key from `.env`); `estimate_cost` / `format_cost_panel` (offline char/4
  estimate, ASCII-safe panel).
- **Resolver** (`src/list_renderer/resolver/`) — `prepare` fetches the page HTML + builds the
  prompt + cost estimate **without** calling Claude; `resolve_source` makes the billed call and
  returns a recipe; `recipes.py` persists to the `recipes` table (keyed by source_signature,
  reused forever). Deterministic **free RSS shortcut**: a declared `<link rel=alternate
  type=application/rss+xml>` yields an `rss` recipe with **no Claude call**.
- **CLI** `scripts/4_resolve_source.py` — `prepare` → print cost panel → **mandatory `[y/N]`
  gate** (only `--yes` bypasses; `--estimate-only` never calls; `--force-claude` ignores the
  free shortcut) → `run_llm_task` → save recipe → optionally `--source-id`/`--add-to-board`.
- **`web` adapter** (`adapters/web.py`, deferred from Phase 2) — replays a recipe's CSS
  selectors via BeautifulSoup; pipeline loads the recipe by `source.recipe_id` and feeds
  `extract_spec` to it (no Claude at render).
- Config: `config/resolver.yaml` (model=Opus 4.8 default, pricing, cache multipliers,
  calibration, governance seeds) + `config/module_3_resolver_prompt.md` (cacheable; treats page
  HTML as untrusted DATA per NN-1 / prompt-injection isolation).

**Cost discipline (the explicit ask).** Estimate is shown BEFORE any call; the `[y/N]` gate is
mandatory; recipes are cached so renders replay free (D4); the `llm_tasks` ledger records
`task_type` + `payer` per call (D24). Default model Opus 4.8 ($5/$25 per 1M; cache read 0.1x /
write 1.25x), switchable to Sonnet 4.6 / Haiku 4.5 in `resolver.yaml`.

**Verified WITHOUT spending.** Cost panel renders (~9,085 tok → ~$0.096/call for a sample page
on Opus 4.8); `[y/N]` decline aborts with `llm_tasks`=0 / `recipes`=0; the free RSS shortcut
saves an `rss` recipe at $0.00; a `web` recipe scrapes 5 items via selectors; the registry
merges news (9) + resolved-rss (10) + scraped-web (5). The billed `run_llm_task` path executes
only on explicit approval — not exercised in verification.

**State.** Phase 3 complete. `web` adapter now built (closes the Phase 2 deferral). Next:
Phase 4 (FastAPI server + interactions, M7) or Phase 5 (ranking, M6).

---

## D31 (2026-06-19) — Phase 4 built (local server + interactions + PWA, M7)

**Built.**
- **Local board server** (`src/list_renderer/server.py` + `scripts/4_serve.py` +
  `run_4_List_server.bat`) — stdlib `http.server` `ThreadingHTTPServer` bound to
  **127.0.0.1** only (localhost single-user + trusted → no auth/session, D27 §16.5). Serves
  the template + the `/api/*` JSON contract; opens the browser; one SQLite connection per
  request (threads can't share connections). Endpoints:
  - `GET /api/board` (`?refresh=1`, `?no_fetch=1`) → the same `LIST_DATA` shape as the file
    sidecar (§13.7 / D15), plus `meta.{mode,seen,liked,n_hidden}`. Also re-writes the on-disk
    sidecar each call so a later `file://` open shows the same board.
  - `GET /api/sources` / `POST /api/sources` → list sources + on-board flags / toggle a source
    on/off the board (the **source-selection UI**, closes the Phase-3 "no toggle UI" gap).
  - `POST /api/interact` → append a **batch** of buffered signals (D6).
  - `POST /api/interest` → capture a declared interest (`interests`, `status='pending'`);
    auto-discovery/registration (M5) stays Phase 6 (D7).
  - `GET /api/health`, static `/`, `/list_results_data.js`, `/manifest.webmanifest`,
    `/service-worker.js`, `/icon.svg`.
- **Interaction capture** (`src/list_renderer/interactions.py`) — append-only writes to
  `interactions` with the verbatim `context_json` feature/score snapshot at event time (D19).
  Actions: `impression, open, read_more, like, hide, dwell, scroll_past` (+`unhide`,
  forward-compat). Derived views: `hidden_item_ids` (filtered out of the board — an immediate
  deterministic override, the learned version is D21), `seen_item_ids` (D22 dimming),
  `liked_item_ids`.
- **Template v2** (`Outputs/list_results.html`, hash-bumped per D1) — **one data contract, two
  delivery modes**: a loader shim uses `GET /api/board` when served (http) and falls back to the
  `window.LIST_DATA` sidecar over `file://`. Interaction hooks: `IntersectionObserver`
  impression + dwell + `scroll_past`; title/Read-more click → `open`/`read_more`; a per-row
  kebab menu → Like (one-way positive signal) / Hide (removes the row + persists) / Open. Events
  buffer in `localStorage` and flush batched to `/api/interact` on a timer + `pagehide`
  (`sendBeacon`). A slide-over **Sources panel** toggles sources and adds an interest. Seen rows
  dim; liked rows show a heart. **Server-only controls are hidden over `file://`**, so the
  static sidecar render remains the regression check (still renders fully offline).
- **PWA** (D26) — `Outputs/manifest.webmanifest` + `service-worker.js` (app-shell cache-first,
  `GET /api/board` network-first w/ cache fallback) + `icon.svg`; responsive/mobile-first CSS
  (wrapping toolbar, larger tap targets, ≤600px tuning). SW registers in served mode only.
- **Pipeline** — every rendered item is now stamped with a stable `id` (`cache.item_id`) even
  for local sources (file/biopharm/sqlite), so interactions key off it. New `build_board()`
  wraps the registry render with hide-filtering + seen/liked/meta for the server delivery.

**Framework note (the D25 nuance).** D25 names FastAPI as the backend; M7's spec text specifies
stdlib `http.server` (mirroring `0_Renderer` / `3_Biopharmcatalyst_parser`). **Local v1 uses
stdlib** — zero new dependency, repo-consistent. D25's actual guardrail is the *fixed `/api/*`
HTTP/JSON contract*, not the framework: the hosted port swaps the server impl (FastAPI +
Postgres + workers, §14) behind the **same contract**, so this is a swap, not a divergence.

**Verified WITHOUT a browser.** Board build stamps `id` on all items; `record_batch` persists;
`hidden_item_ids` filters the board (9→8, `n_hidden=1`); `seen`/`liked` derive correctly. Server
boots on 127.0.0.1; `GET /api/health|board|sources` and `POST /api/interact|sources|interest`
all return correct JSON; live `/api/board` serves SWR cache (9 items) and syncs the sidecar; the
served template carries the v2 marker + relative asset links; 404s for unknown paths. `file://`
regression: `4_render_list.py --no-fetch` still writes a valid 9-item sidecar. Test interaction/
interest rows were cleaned afterward (DB back to 0/0). Full in-browser click-through (impression/
dwell/menu) not automated (no headless browser available) — JS reviewed; degrades to no-ops over
`file://`.

**Not yet (deferred):** ranking/order is still source/position order (M6 = Phase 5 consumes this
log); cross-source **dedup (M4)** still open; `/api/interest` captures but does not yet discover
sources (M5 = Phase 6); icons are a single SVG (no rasterized 192/512 PNG — fine for local
install). True background SWR (vs the server's read-through + client SW network-first) is
adequate for local single-user.

**State.** Phase 4 complete. Next: Phase 5 (ranking, M6) — now has a live `interactions` signal
to fit against — or Phase 4-lite dedup (M4) first.

---

## D32 (2026-06-19) — Phase 5 built (learning & ranking, M6)

**Built.**
- **Transparent weighted-feature scorer** (`src/list_renderer/ranking.py`, D5) —
  `score = Σ weight_f · feature_f` over a small inspectable feature set: `position_prior`
  (cold-start order anchor / recency proxy), `recency` (exp half-life decay), `source_affinity`
  (learned), `interest_match` (declared interests, M5), `topic_affinity` (learned),
  `length`, `seen_penalty` (D22). Base weights live in **`config/ranking.yaml`** (user-tunable,
  like 2_Funds M6b's modifier); the *learned* per-source / per-topic affinities live in
  `ranking_state`. **Fails open**: any error serves the unranked source/position order — ranking
  never blanks the board. Stable sort, so equal scores keep feed order.
- **Offline re-fit** (`ranking.fit_affinities` + `scripts/4_learn_ranking.py`) — reads the
  append-only `interactions` log, attributes each signal to the item's source/topics via the
  **`context_json` snapshot (D19)** — the reason that snapshot exists — using the spec-§8 signal
  weights (`like +3, read_more +2, open +1, dwell≥30s +1, scroll_past −0.5, hide −3`), and writes
  `affinity = tanh(net / scale)` ∈ [-1,1] to `ranking_state`. A **data-sufficiency gate**
  (`learn.min_interactions = 15`, like 2_Funds M7-β/γ) keeps it cold-start until enough signal.
  Free + offline (no network, no Claude).
- **Deterministic user rules (D21)** — `config/ranking.yaml::rules`: `mute_keywords` (hard
  filter, applied pre-score) + `boost_topics` (weight bump, added post-score). v1 reads rules from
  config; the Tier-B `rules` table + management API is deferred (no migration this phase).
- **Seen de-prioritization (D22)** — `seen_penalty` lowers already-seen items (seen set derived
  from `impression/open/read_more` interactions).
- **Wiring** — `pipeline.build_payload_from_registry` now ranks results descending by default
  (`rank=True`, `include_breakdown=False`), so both the served board and the file render are
  ordered; `score` flows through `normalize_result` (already passed through), and an optional
  `score_breakdown` (D5/§8 explainability) is available when requested (off by default to keep the
  sidecar lean — a Phase-7 toggle will surface it). The **server auto-re-fits on startup** so each
  restart applies last session's signals.

**Cold-start deliberately ≈ unranked.** Until signals/interests/dates exist, `position_prior`
dominates and the board stays in its natural source/feed order — the new ordering only diverges
as the user acts. This keeps the regression (same items render) intact while making ranking the
product.

**Dormant-but-wired features.** Adapters don't yet emit `published_at`/`topics` (that's M4
normalization), so `recency` and `topic_affinity`/topic-`interest_match` contribute neutrally for
now; the active levers are `source_affinity` (the live learning signal), site-`interest_match`,
`seen_penalty`, and `length`. When M4 lands, recency/topics light up with no ranker change.

**Verified.** Cold start: scores strictly descending, lead source preserved. 15 synthetic
interactions (like hn_rss / hide news_default) cross the gate → fit attributes affinity to **only
the interacted sources** (hn_rss +0.999, news_default −0.995, no spurious sources) → the liked
source floats to the top of the re-ranked board. Mute keyword drops matching items; bad config
path fails open to input order; `4_learn_ranking.py` reports cold-start cleanly at 0 interactions;
the render CLI still emits scored, descending output with no `score_breakdown` leakage. All test
interactions/ranking_state cleaned; on-board reset to seed intent (news_default only).

**Not yet (deferred):** cross-source **dedup (M4)** still open (same story can double-render);
graduating the naïve per-feature affinity to a real regression (spec §8 "once enough rows
exist"); the Tier-B `rules` table + UI; recency/topic features await M4; interest **discovery**
(M5 = Phase 6) still only captures.

**State.** Phase 5 complete. Next: Phase 6 (interest input/discovery, M5) or Phase 4-lite dedup
(M4).

---

## D33 (2026-06-19) — Attribute/feature extraction strategy (resolves OD-d topic tagging)

**Decision.** Item attributes (the things ranking learns over, M6) are extracted in **staged
layers of increasing cost**, deliberately *not* a single fixed set, because attribute granularity
trades off against generalization (the sparsity / bias–variance problem): coarse attributes are
dense and generalize but cap resolution; fine attributes resolve but starve of signal and, at the
limit (the `item_id` itself), cannot generalize across content churn at all.

**Principles.**
1. **Two kinds of attribute, kept separate.** *Structural* (free, deterministic, dense — `source`,
   domain, section/path, `language`, recency, length, kind) is the **generalization backbone**;
   *semantic* (what the item is about — topics, entities, stance) requires extraction and is where
   cost lives. The structural backbone alone already powers source-affinity (live today) and
   carries a feed reader far — `source` is a strong taste proxy.
2. **Similarity ≠ named attributes.** Inferring "more like what I engaged with" does **not** require
   enumerating attributes; **embeddings** give similarity geometrically (cosine) with no taxonomy
   and no per-label sparsity. Named, controlled-vocabulary topics are for *interpretability + user
   rules* (D21 mute/boost must name something); embeddings are for *similarity + dedup*. Curator
   wants **both**, for different jobs.
3. **Multi-granularity with back-off, not a single granularity.** Estimate fine attributes but
   shrink them toward their coarse parent by data volume (`source → section → topic → entity`).
   The current `affinity = tanh(net / scale)` is a crude shrinkage; a real hierarchy gives fine
   resolution *and* generalization instead of trading one for the other. (Note: the linear
   weighted-sum model, D5, cannot represent attribute *interactions* without engineered
   cross-features — attribute design and model class are coupled.)
4. **Controlled-but-evolving vocabulary.** Seed a small topic taxonomy; let the tagger propose new
   topics; review additions in **batches** (`feedback_avoid_multiplying_user_requests`), never
   interactively. **Prune by decision-relevance** — an attribute that never correlates with any
   user's signal is dead weight; the data says which attributes earn their place.

**The cost ladder (and where each lands in Curator).**
- **L1 Structural — free, now.** Already live; `source` does the work.
- **L2 Keyword / entity tagging — cheap, local, no LLM — lands with M4.** Maps items into a
  controlled topic vocab; noisy but interpretable; enables D21 rules and lights up the
  dormant `recency`/`topic` ranking features with **no ranker change**. (The OD-d "keyword/entity
  v1" assumption, now confirmed as the M4 step.)
- **L3 Embeddings — medium, local-capable, no generative LLM — v2.** The real similarity engine;
  computed **once per item as Tier-A enrichment** (O(items), shared — same economics as recipes,
  D12). Simultaneously solves **cross-source dedup** (near-duplicate detection M4 needs anyway),
  emergent topic clusters, and a "similarity-to-liked-centroid" ranking feature.
- **L4 Generative LLM — expensive — optional, later.** Reserved for semantics the cheaper layers
  cannot infer (stance, novelty/quality, reading level, multilingual topic unification). Viable
  **only** as batched, cached, **once-per-item** Tier-A enrichment — never per render (D23/D4) —
  behind the existing `run_llm_task` seam (D24), so it is purely additive.

**Why.** A hard-defined universal *structural* set is sufficient to start and is the necessary
dense backbone; **embeddings**, not an LLM, are the right and cheaper tool for similarity (and need
no attribute definitions); a generative LLM is neither necessary nor the first reach — it is a
targeted quality layer for the few semantic attributes cheap methods miss. Staging by cost keeps
the per-item economics intact (D12) and each layer ships independently with no rewrite.

**State.** Resolves OD-d. L1 built; **L2 = the M4 build**; L3 (embeddings) is the v2 follow-on
(also the dedup engine); L4 (LLM tagging) deferred behind D24. No schema change needed for L1/L2
(`items.topics_json` exists); L3 adds an embedding column/table as Tier-A enrichment.

---

## D34 (2026-06-19) — M4 built (normalization, attributes L1/L2, cross-source dedup)

**Built.** The normalization layer between fetch (M2) and rank (M6), realizing D33 L1+L2 and
closing the long-standing dedup gap:
- **L1 structural attributes (free).** Adapters now emit ISO-8601 UTC `published_at`:
  `_rss_parse.item_date_iso` (RSS `pubDate` / Atom `updated`/`published`) covers `rss`+`news`;
  `http_api._to_iso` (epoch s/ms or ISO string) covers JSON APIs — wired for HN via
  `fields.published: created_at_i`. This **lights up the dormant `recency` ranking feature** with
  no ranker change. (Feeds that omit a date — e.g. FierceBiotech's — fall back to neutral 0.5.)
- **L2 keyword topic tagging.** `config/topics.yaml` = a **controlled, evolving** vocabulary
  (12 seed topics → keyword lists); `normalize.tag_topics` does case-insensitive word-boundary
  matching over title+snippet → `item.topics`. This **lights up `topic_affinity` + topic
  `interest_match`** and gives D21 mute/boost something to name. Cheap, local, deterministic,
  interpretable — no LLM (D33).
- **Cross-source dedup.** `normalize.dedup` collapses the same story from two feeds: exact
  canonical-URL match (host-normalized, tracking-param-stripped), exact normalized-title match,
  then a high-threshold (0.85) title-token Jaccard for near-duplicates (guarded by a min-token
  floor to avoid false merges). First occurrence wins (preserves feed/ranking order); merged
  sources recorded on `dup_sources`.
- **Wiring.** `pipeline.build_payload_from_registry` runs `normalize.normalize` on the merged
  results **after fetch/id-stamp, before ranking**, logging the dedup count. Enrichment mutates
  only the per-render dicts (cache untouched), so re-tagging is always current with the vocab.
  `topics` flows through `normalize_result` to the sidecar (and into interaction `context_json`,
  so `fit_affinities` now learns topic affinity); `domain`/`dup_sources` stay internal.
- **Fail open.** Any normalization error returns the un-normalized list — never blanks the board.

**Verified.** Tagger returns correct topics with no false positives on generic text; `canonical_url`
unifies www/case/tracking variants; dedup collapses 4→2 with `dup_sources` recorded; epoch→ISO
correct; the registry board tags news items (biotech/markets) and recency makes a fresh item
outrank a 10-day-old one; **live** fetches emit `published_at` (HN, Le Figaro, CNBC). No DB writes
in verification.

**Not yet (deferred):** per-item `language` detection (NN-3 — adapters pass through; needs a lib,
deferred); persisting `topics_json`/`domain` to the `items` table (render-time tagging suffices
for ranking today); **L3 embeddings** (the taxonomy-free similarity + semantic-dedup engine, v2);
**L4 LLM tagging** (optional, behind D24). The dedup is lexical (URL/title); semantic near-dups
across very different headlines wait for L3.

**State.** M4 built. Ranking now has live recency + topic signal in addition to source affinity.
Next: Phase 6 (interest input/discovery, M5) or v2 embeddings (D33 L3).

---

## Still-open decisions (spec §11)

- **OD-d Topic tagging** → ✅ **resolved by D33** — staged attribute extraction: structural (L1,
  now) → keyword/entity controlled-vocab (L2, M4) → embeddings (L3, v2; also dedup + similarity) →
  LLM tagging (L4, optional, behind D24). No remaining open decisions.

---

## D16 (2026-06-19) — Identity: `user_id` stamped from day 1 (was OD-f)

**Decision (user-confirmed).** Every per-user row (subscriptions, interests, interactions,
ranking weights, board config) carries a `user_id` even in local single-user mode, populated
with a local default user. The hosted port then only swaps the auth provider + storage
backend — no data migration of historical interactions.

**Why.** Delivers the "swap, not rewrite" promise (D11/D9-amended) at trivial cost. Avoids a
painful retrofit of the interaction log (the irreplaceable training data) later.

**State.** Adopt in Phase 1 schema.

---

## D17 (2026-06-19) — Content retention: metadata + transient raw only (was OD-g)

**Decision (user-confirmed).** Store Highlight **metadata** durably (title, snippet, link,
tags, dates, source). Keep **raw fetched HTML/JSON only transiently** for recipe debugging
(short TTL, Blob/local file), never as the durable record. Do **not** cache full article text.

**Why.** Copyright/ToS-safe (critical once hosted/redistributed; aligns with
`project_data_provider_switch` posture), keeps the DB small, and the snippet is sufficient for
the ranking features chosen in D5. Full-text ranking/search is a later opt-in if ever needed.

**State.** Adopt in Phase 1; `items` stores metadata, raw kept transient.

---

## D18 (2026-06-19) — Board model: single board, `board_id`-ready (was OD-h)

**Decision (user-confirmed).** v1 presents a **single board** per user, but every
board-scoped row carries a `board_id` (defaulted to the one board). Multiple named boards
later is then an additive feature, not a schema rewrite.

**Why.** Keeps v1 UI simple while preserving the option. `board_id` is cheap to carry now,
expensive to backfill.

**State.** Adopt in Phase 1 schema (subscriptions/config keyed by `board_id`).

---

## D19 (2026-06-19) — Signal capture: snapshot features per interaction (was OD-i)

**Decision (user-confirmed).** Each interaction row stores, alongside `(user_id, item_id,
action, ts)`, a **verbatim snapshot of the item's feature/score context at event time**
(topics, source, recency bucket, the score + feature values that produced its rank). The
ranking model is therefore always reconstructible/trainable even after content churns or
recipes change.

**Why.** Directly the 2_Funds **M7-α "snapshot verbatim" lesson** — without the at-the-time
context, retuning or re-fitting destroys the ability to reconstruct what the user actually
reacted to. Slightly heavier writes; negligible vs the value of trainable history.

**State.** Adopt in Phase 1 `interactions` schema.

---

## D20 (2026-06-19) — Non-negotiables baked into Phase 1

**Decision (user-confirmed, adopt as-is).**
- **NN-1 Untrusted content + prompt-injection isolation** — treat all fetched content as
  hostile; sanitize before render; never pass page text to Claude as instructions (only as
  clearly delimited data).
- **NN-2 Source politeness/compliance** — robots.txt + ToS check, conditional GET
  (ETag / If-Modified-Since), per-source rate limit + backoff. (Shared cache already implies
  one fetch per source per TTL.)
- **NN-3 i18n correctness** — UTF-8 end-to-end + per-item `language` + `published_at`
  normalized to UTC.
- **NN-4 Cost/health telemetry ledger from day 1** — Claude spend, fetch success/fail, recipe
  yield; prerequisite for hosted budget governance (D13).

**Why.** Each is cheap as a day-1 rule and a legal/security/correctness liability if
retrofitted. NN-1/NN-2 are security/legal; NN-3 already bit us (Le Figaro encoding); NN-4
underpins D13.

**State.** Bake into Phase 1.
