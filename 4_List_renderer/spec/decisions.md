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

## Still-open decisions (spec §11)

- **OD-a Device target** — desktop-first assumed; iOS-first would reorder template work.
- **OD-d Topic tagging** — keyword/entity v1 assumed; confirm if embeddings wanted up front.

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
