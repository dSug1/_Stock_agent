# 4_List_renderer — Overall Specification

**Working product name:** Curator (a personalized highlights board)
**Module dir:** `4_List_renderer/`
**Status:** Phases 0–5 built (template + adapters + SWR cache + Claude resolver + live server &
interactions + PWA + learning/ranking) + M4 normalization (attributes L1/L2 + cross-source dedup);
Phases 6–8 pending. This spec defines the v1 target.
**Last updated:** 2026-06-19

---

## 1. Purpose & vision

Curator lets a user **define their own universe of sources** — the user, not a platform,
decides which websites, databases, feeds, and APIs the content is drawn from — then renders
the **highlights** from that universe into a single, consistent HTML board. The same stable
visual template is reused for every source — a Google-search-style result list — so adding a
new kind of source never changes the UI ("adaptive display"). The content is infinite, but
**the user bounds the universe.**

Three capabilities lift it above a static renderer:

1. **Flexible sources.** A source can be website content (RSS / scraped page / API), a
   local database, a remote database, a file, or one of this repo's own pipelines
   (e.g. `3_Biopharmcatalyst_parser`). New source *kinds* are added as adapters behind a
   common contract.
2. **Claude-assisted fetch/extract resolution.** When a source is new or its markup is
   messy (e.g. the FierceBiotech `<title>` that wraps the headline in an `<a>`, or Le
   Figaro's encoding), the app can call the Claude API to **infer how to fetch and how to
   extract/clean the fields**, then cache that as a reusable *recipe* so later renders are
   deterministic and free.
3. **Learning to rank.** The app observes what the user actually reads (clicks, dwell,
   explicit like/hide) and learns a per-user interest model, so highlights are displayed
   in **descending order of predicted interest**. The user can also **declare areas of
   interest** (a website, a topic), which the app resolves into concrete sources and folds
   into ranking.

Curator is **local-first**, matching the rest of this repo: one local process, SQLite
state, a local HTTP server for browser write-backs, no cloud except outbound source
fetches and Claude API calls.

---

## 1b. Positioning & prior art

**One-line positioning.**

> Curator is a **personalized recommender feed (Netflix-like ranking) over a user-defined
> universe of sources.** The user — not a platform — decides which websites, databases, and
> feeds the catalog is drawn from; an LLM assembles and parses whatever they add (so *any*
> source works), and items are surfaced under a search-engine-like rights model (snippet +
> link-out). The content is infinite, but **the user bounds the universe.**

**The core differentiator: the user owns the universe.** Algorithmic feeds (Google/Apple
News, Artifact, SmartNews) pick the catalog *for* you; classic RSS readers only accept feeds
someone *already publishes*. Curator lets the user **declare** the universe — add a website, a
subreddit, a local/remote DB, an API — and the LLM resolver figures out how to ingest it.
Personalization then ranks *within the user's chosen universe*, not within a platform's.

**Closest comparables (what we take / leave):**
- **AI-prioritizing RSS readers** — Feedly (Leo), NewsBlur (preference trainer), Inoreader,
  Readwise Reader. Closest overall; we borrow their training/rules ideas, but they require
  pre-existing feeds.
- **Algorithmic news feeds** — Google/Apple News, Flipboard, SmartNews, Artifact (defunct
  2024). The platform picks the catalog — the opposite of a user-defined universe.
- **LLM extraction infra** — Firecrawl, Jina Reader, Kadoa, Diffbot. These *are* our resolver,
  but sold as developer infra; none pool recipes into a reading product.

**What no incumbent combines:** (1) a **user-defined universe** of arbitrary sources, (2) an
LLM universal-adapter with **recipes shared/amortized across users** (Tier A, §13), (3) one
fixed template for heterogeneous sources, and (4) transparent, user-tunable ranking +
local-first privacy.

**Netflix-analogy caveat.** The *ranking layer* is Netflix-like; the *content layer* is the
opposite. Netflix owns a finite, static catalog; Curator sits on an open, perishable,
**user-assembled** catalog it does not own (hence snippet + link-out, D17). The hard, novel
engineering is **ingestion** (the resolver) — something Netflix never needs.

### 1b.1 Features mined from prior art — proposed dispositions

Decisions on which incumbent features apply to Curator. Dispositions are proposals; the
significant ones are formalized in `decisions.md` (D21–D23).

| Feature (seen in) | Disposition | Note |
|---|---|---|
| Per-tag/author/source up-down training (NewsBlur) | **Adopt v1** | Feeds M6 learned weights + M5 explicit-interest weights. |
| Mute / boost rules: hide keywords, prioritize topics (Feedly Leo, Inoreader) | **Adopt v1** | Deterministic rules layer over learned ranking: mute = hard filter, boost = weight bump. (D21) |
| Keyword/topic monitoring = saved search as a feed (Inoreader, Google Alerts) | **Adopt v1** | This *is* M5 "interest kind = query" → source discovery. Add a query-as-source adapter. |
| Folders / boards grouping sources | **Already covered** | D18 board model (single v1, `board_id` ready → folders later). |
| Seen / unread state + mark-read + unread counts (all readers) | **Adopt v1** | Lightweight per-user item state; also suppresses already-seen items in ranking. (D22) |
| Save / read-later + highlights & annotations (Pocket, Readwise, Matter) | **Defer (backlog)** | Interaction model already supports a `save` action; UI later. |
| AI per-article summaries / digests (Particle, Perplexity, Artifact) | **Exclude v1** | Conflicts with D4 (Claude not per-render). Optional later as cached Tier-A enrichment, opt-in + cost-gated. (D23) |
| Multi-perspective / bias labels (Ground News) | **Defer** | Niche; needs cross-source story clustering. |
| OPML import / export | **Adopt v1** | Cheap onboarding — import existing feed lists; export the universe. |
| Source-health indicators ("feed broken") | **Adopt v1** | Free from `fetch_log` + recipe status (NN-4). |
| Full-text search over content | **Defer** | Gated by D17 (metadata-only); v1 search = title/snippet/tags. |
| Cross-device sync + offline | **Already covered** | Tier B (hosted) sync + Tier C (local) offline cache, §13. |
| Keyboard power-navigation (j/k) (Reeder, Inoreader) | **Defer (Phase 7 polish)** | Power-user nav. |
| "Why am I seeing this" transparency | **Adopt / emphasize** | Our D5 score-breakdown — a differentiator few incumbents offer. |

---

## 1c. Technology stack (decided — D25/D26)

| Layer | Choice | Notes |
|---|---|---|
| **Client** | One **responsive PWA** (HTML/CSS/JS) — the existing template, mobile-first, installable on desktop + mobile, offline via service worker | Desktop + mobile from one codebase (D26). Optional later Capacitor/Tauri wrap, no UI rewrite. |
| **Backend** | **Python / FastAPI** | Reuses the whole `_Stock_agent` Python stack (adapters, ranking, biopharm reader). Serves the `/api/*` contract + the static PWA locally. |
| **Backend hosting** | Any **Python-friendly cloud** — Render / Railway / Fly / Azure Container Apps | "Vercel" was illustrative (D25). Static PWA can sit on any CDN incl. Vercel. |
| **Local v1** | The **same FastAPI app on `localhost`** | Local and hosted run identical code; only storage backends differ (§13). |
| **Storage** | SQLite (local) → Postgres + Redis/KV + object storage (hosted) | Per the three-tier model (§13). |
| **LLM** | Anthropic Python SDK via the single `run_llm_task` runner (D24) | Prompt caching mandatory; cost-gated. |

**Guardrail (D25):** the backend is reachable only through a fixed **HTTP/JSON contract**
(`/api/board`, `/api/interact`, `/api/interest`) + the tier interfaces, so the PWA never changes
if the API language is ever swapped (TS/Next.js fallback exists only if Vercel-native becomes
mandatory). **C# was rejected** — not a native serverless runtime on Vercel-class hosts, and
diverges from the repo.

---

## 2. Design principles (inherited from repo conventions)

These are load-bearing and come from existing user memories / module decisions. Do not
re-litigate them when implementing.

- **Template + data sidecar split.** A stable, hash-versioned `list_results.html` plus a
  per-run `list_results_data.js` sidecar. Only the sidecar is rewritten each render.
  (`feedback_html_template_data_split`.)
- **Browser writes go through a local HTTP server**, never the File System Access API.
  All user actions (select sources, add interest, like/hide, capture dwell) POST to a
  local server which persists to SQLite. (`feedback_browser_writes_via_local_server`.)
- **Stale-while-revalidate by default.** Render the cached highlights instantly, refresh
  sources in the background. (`feedback_swr_pattern`.)
- **Cache all user-selectable state**; restore on startup; URL params override cache when
  present. (`feedback_cache_user_prefs`.)
- **SQL-only durable storage** (`data/list_renderer.db`); the sidecar JSON is the sole
  on-disk serialization exception. Schema evolves via additive migrations.
- **Claude calls are cost-gated and cache-first.** Prompt caching is mandatory; billed
  dispatches sit behind a mandatory `[y/N]` gate; batch where possible. (`claude-api`,
  mirrors 2_Funds M6 / 3_Biopharm M7-M8.)
- **Don't multiply user requests.** Resolve ambiguity in code with recipes + audit rows;
  do not prompt the user per ambiguous parse. Batched triage is the only exception.
  (`feedback_avoid_multiplying_user_requests`.)
- **Output folder convention.** `Outputs/` = files the user opens; `data/` = durable
  cache/state; `_intermediate_outputs/` = code-only plumbing.
  (`feedback_output_folder_convention`.)
- **Provider/ToS hygiene.** Any finance source via yfinance is local-personal only until a
  licensed provider is swapped. (`project_data_provider_switch`.)

---

## 3. High-level architecture

```
                 ┌─────────────────────────────────────────────────────────┐
                 │                    Curator (local)                       │
                 │                                                          │
  user adds  ───▶│  M5 Interest input ──┐                                   │
 site/topic      │   (resolve→discover) │                                   │
                 │                       ▼                                   │
                 │   M1 Source registry (sources, selection)  ◀── M3 Claude │
                 │           │                                    resolver  │
                 │           ▼                                  (recipes,    │
                 │   M2 Adapter layer (fetch raw items)  ◀──────  cached)   │
                 │           │   rss · web · sqlite · http · file · biopharm │
                 │           ▼                                              │
                 │   M4 Normalize + dedup → Highlight records               │
                 │           │                                              │
                 │           ▼                                              │
                 │   M6 Ranking (interest model) → ordered highlights       │
                 │           │            ▲                                 │
                 │           ▼            │ interactions (clicks/dwell/like) │
                 │   M0 Render core → list_results_data.js (sidecar)        │
                 │           │            ▲                                 │
                 └───────────┼────────────┼─────────────────────────────────┘
                             ▼            │  POST /api/*
              Outputs/list_results.html ──┘  (M7 local server)
                   (stable template, opened in browser)
```

**Render data flow (SWR):**

```
render → load enabled sources (M1)
       → for each: serve cached items instantly (SWR cache, M2/M4)         → sidecar v1
       → background: re-fetch via adapter+recipe (M2/M3), normalize (M4)
       → score & order by interest (M6)
       → rewrite sidecar (M0)                                              → sidecar v2
```

---

## 4. The rendering contract

### 4.1 Template (M0, exists)

`Outputs/list_results.html` reads `window.LIST_DATA` from the sidecar and renders a
result list: per row a favicon (or letter avatar), bold site name, `›`-separated
breadcrumb, kebab, blue title (+ optional green verified check), and a snippet with
emboldened terms and an optional "Read more". The template is **fixed**; restyling bumps a
version hash but never changes the data contract.

### 4.2 Sidecar payload

```
window.LIST_DATA = {
  query:   string,         // board heading / what was searched
  brand:   string,         // wordmark
  results: Highlight[],    // already ordered (descending interest) by M6
  meta?:   { rendered_at, sources[], mode: "swr"|"fresh", model_version }
}
```

Delivered as a written `list_results_data.js` (local) or `GET /api/board` JSON (hosted) —
same shape either way; see §13.7 / D15.

### 4.3 Highlight record (the unified schema)

Today's template fields, plus identity + ranking fields the JS uses for ordering and
interaction capture. The template ignores unknown fields, so this is backward-compatible;
interaction hooks land with the M7 template version.

| Field | Type | Used by | Notes |
|---|---|---|---|
| `id` | string | M6/M7 | stable hash of (source_id, url) — interaction key |
| `source_id` | string | M6/M7 | which source produced it |
| `site_name` | string | template | bold label |
| `url_breadcrumb` | string | template | `domain › path › seg` |
| `favicon` | string\|null | template | null → letter avatar |
| `title` | string | template | headline |
| `title_url` | string | template | link |
| `verified` | bool | template | green check |
| `snippet` | string | template | summary text |
| `bold_terms` | string[] | template | emboldened substrings |
| `read_more_url` | string\|null | template | link |
| `published_at` | string\|null | M6 | recency feature |
| `topics` | string[] | M6 | tags for interest matching |
| `score` | number | M6 | predicted interest (ordering) |

`src/list_renderer/render.py::normalize_result` is the single choke point that projects a
Highlight down to the template's known keys.

---

## 5. Data model (`data/list_renderer.db`)

SQLite, additive migrations (repo pattern). Indicative schema; exact columns finalized per
module as built. Reflects the Phase-1 decisions D16–D20: `user_id` on every per-user row
(D16), metadata-only item storage with transient raw (D17), `board_id` carried (D18),
per-interaction feature snapshot (D19), and per-item `language` (D20/NN-3).

**Tier A — shared / user-independent** (§13.1):
- **`sources`** — `(id PK, kind, name, label, config_json, adapter, recipe_id, enabled,
  origin, language, created_at, updated_at)`. `kind ∈ {rss, web, sqlite, http_api, file,
  biopharm, …}`. `origin ∈ {seed, user, discovered}`. `config_json` carries per-source auth
  + politeness state (ETag/Last-Modified, rate limit; NN-2).
- **`recipes`** — `(id PK, source_signature, kind, fetch_spec_json, extract_spec_json,
  prompt_version, resolved_by, confidence, status, created_at, last_validated_at)`. The
  Claude-resolved "how to fetch + how to extract" recipe. Keyed by `source_signature` so
  identical sources reuse one recipe. `status ∈ {active, needs_revalidation, failed}`.
- **`items`** — `(id PK, source_id, item_key, title, url, snippet, published_at_utc,
  language, captured_at, topics_json, raw_hash)`. SWR cache of Highlight **metadata** (D17 —
  no full article text; raw HTML kept transiently elsewhere for recipe debugging).
  `item_key` = source-local id; `id` = global hash; `raw_hash` detects change.

**Tier B — per-user profile** (every row carries `user_id`, D16):
- **`users`** — identity/account (D27): `(id PK, kind, email, display_name, role, status,
  plan, byok_key_ref, created_at, updated_at)`. `kind ∈ {local, account}`; a single `local`
  default user in local mode. `role ∈ {user, admin}`; `plan`/`byok_key_ref` tie to billing
  (D24).
- **`auth_identities`** / **`sessions`** / **`login_audit`** — auth scaffold (D27); created in
  the v1 schema but **empty** in local single-user mode (localhost is trusted). Populated only
  in hosted multi-user mode. `sessions` stores `token_hash` (never the raw token) + `ip_hash`
  (IP is PII → hashed/truncated, D14).
- **`subscriptions`** — `(user_id, board_id, source_id, position, enabled, created_at)`.
  Which sources a user follows, per board (D18).
- **`boards`** — `(id PK, user_id, name, config_json, created_at)`. One default board in v1.
- **`interests`** — `(id PK, user_id, kind, value, weight, status, created_at)`. `kind ∈
  {topic, site, query, ticker}`. User-declared areas of interest (M5).
- **`interactions`** — `(id PK, user_id, board_id, item_id, action, value, dwell_ms,
  created_at, context_json)`. `action ∈ {impression, open, read_more, like, hide, dwell,
  scroll_past}`. Append-only learning log; **`context_json` is the verbatim feature/score
  snapshot at event time** (D19) so the model stays trainable through content/recipe churn.
- **`ranking_state`** — `(user_id, feature, weight, updated_at)` learned per-feature deltas +
  model metadata. Base weights live in `config/ranking.yaml`; this holds the learned
  adjustments + last-fit timestamp.

**Tier A — operational / audit:**
- **`fetch_log`** — `(id PK, source_id, started_at, status, http_status, n_items, bytes,
  error)`. Source fetch health/telemetry (NN-4).
- **`llm_tasks`** — `(id PK, task_type, ref_id, billing_context, payer, user_id, usd,
  input_tokens, output_tokens, cache_read, status, created_at)`. The single ledger for every
  Claude call routed through `run_llm_task` (D24). v1: only `task_type='resolve_recipe'`,
  `billing_context='self'`, `payer='self'`. Seeds hosted budget governance (D13) and future
  per-payer billing (BYOK / corporate / advertiser).
- **`render_runs`** — `(id PK, user_id, board_id, started_at, n_sources, n_items, mode,
  notes)`. Optional render audit.

---

## 6. Module breakdown

Numbering follows the repo convention (top-level scripts may carry a `4_` prefix; `src/`
packages may not). Per-module specs (`spec/module_*.md`) are spun out as each is built.

### M0 — Render core & template (✅ built)
`src/list_renderer/render.py` + `Outputs/list_results.html` + sidecar. Owns the contract in
§4. Stable; only restyles bump the version. Done.

### M1 — Source registry & selection
SQLite-backed CRUD over `sources`; tracks which sources are enabled. Seeded from
`config/sources.yaml`. Exposes `list_enabled()`, `add()`, `set_enabled()`. Selection state
also mirrored to localStorage for instant UI (server is source of truth).

### M2 — Adapter layer (fetch)
Pluggable adapters keyed by `source.kind`, each returning a list of raw items. Seed adapters
exist: `file`, `biopharm` (reads `3_Biopharmcatalyst_parser/data/biotech.db`), `news` (RSS
for FierceBiotech / Le Figaro / CNBC). v1 adds a generic `rss`, `web` (scrape), `sqlite`,
and `http_api` adapter. An adapter consumes a source's `recipe` (from M3) to know *where*
and *how* to pull. Contract: `fetch(source, recipe) -> list[RawItem]`.

### M3 — Claude resolver (recipes)
When a source is new, or an adapter's parse fails / drifts, M3 calls Claude to infer:
- **Fetch spec** — e.g. discover the RSS URL for a site, the API endpoint + params, or the
  page URL + pagination.
- **Extract spec** — field map / selectors / cleaning rules to turn raw content into
  Highlight fields (title, link, snippet, date), including the messy cases this project
  already hit (title-in-`<a>`, HTML entity / encoding cleanup, CDATA).

Output is a **recipe** persisted to `recipes` and reused forever; re-resolution happens only
on new source, parse failure, or structural drift (detected via `raw_hash` mismatch + parse
yield drop). Billed dispatches sit behind a mandatory `[y/N]` gate, use prompt caching, and
batch multiple sources in one run where possible. Resolution never prompts the user per
ambiguous field — it writes a recipe with a `confidence` and an audit row; low-confidence
recipes are surfaced for optional batched review, not interactive Q&A.

### M4 — Normalization & dedup (✅ built L1/L2 + dedup, D34)
Maps heterogeneous `RawItem`s to Highlight records, assigns stable `id`s, and dedups across
sources (same story from two feeds). Also the home of **attribute extraction** that feeds M6
ranking, staged by cost per **D33**: M4 adds the **L1 structural** attributes (`published_at`,
domain/section, `language`, length — which light up the dormant `recency` feature) plus **L2
keyword/entity** topic tagging into a controlled vocabulary (`items.topics_json`). **L3
embeddings** (the similarity + near-duplicate-dedup engine, a Tier-A per-item enrichment) and
**L4 optional generative-LLM tagging** (behind the D24 seam, once-per-item cached) are later.
Output feeds M6.

### M5 — Interest input & source discovery
User submits an **area of interest** (a website URL, a topic, a query, a ticker). M5:
1. Classifies the input kind.
2. Resolves it into concrete source(s): a website → discover its feed/recipe (via M3); a
   topic/query → map to feeds/queries/searches; a ticker → bind to the biopharm/finance
   source.
3. Registers the discovered source(s) in M1 (`origin='discovered'`) and stores the interest
   in `interests`.
4. The topic also becomes a **ranking feature** (M6 boosts matching items).

Resolution is automatic + audited (no per-item prompting), per the avoid-multiplying memory.

### M6 — Learning & ranking (✅ built, Phase 5, D32)
Computes a per-item **interest score** and orders highlights descending. Design:
- **Features** per item: source affinity, topic/interest match, entities, recency, length,
  and explicit-interest boosts from M5.
- **Model v1 (transparent weighted sum).** `score = Σ weight_f · feature_f`, mirroring
  2_Funds M6b's 14-component user-tunable modifier. Base weights in `config/ranking.yaml`
  (inspectable, user-tunable); **learned deltas** fit offline from the `interactions` table
  (like ↑, hide ↓, open/dwell weak ↑, scroll_past weak ↓) and stored in `ranking_state`.
- **Cold start.** Until enough signal, order by recency + declared-interest match.
- **Loop.** Re-fit is a free, offline batch step (`scripts/4_learn_ranking.py`), run on a
  schedule or after each render. Ranking auto-applies to display order (that's the product),
  but the model is fully inspectable and the weights tunable — no black box.

### M7 — Local server & UI interactions (✅ built, Phase 4, D31)
A local HTTP server (stdlib `http.server`, mirrors `0_Renderer` / `3_Biopharm`; bound to
127.0.0.1, no auth — localhost is trusted) that:
- Serves `list_results.html` + sidecar + PWA assets and auto-opens the browser.
- Exposes the `/api/*` contract: `GET/POST /api/sources` (select sources), `POST /api/interest`
  (capture; discovery=Phase 6), `POST /api/interact` (batched like/hide/open/read_more/dwell/
  scroll_past/impression), `GET /api/board` (the §13.7 delivery), `GET /api/health`.
- Persists durable signals append-only to `interactions` with the `context_json` snapshot
  (D19); mirrors lightweight UI prefs / an offline event buffer to localStorage.
The template gained interaction hooks (impression + dwell + scroll_past via IntersectionObserver,
a per-row kebab menu with like/hide, click capture on titles/Read-more) keyed by Highlight `id`,
a source-selection panel, and PWA bits (manifest + service worker, D26) — a new hash-versioned
template revision that still renders over `file://` (server-only controls hidden). The local
server is stdlib, not FastAPI (D25's guardrail is the contract, not the framework; the hosted
port swaps the impl behind the same `/api/*`).

### M8 — Render orchestrator
End-to-end pipeline tying M1→M6 together with SWR: instant cached render, background refresh,
sidecar rewrite. `scripts/4_render_list.py` is the seed (today it has `--source {file,
biopharm,news}`); v1 generalizes it to "render the enabled source set."

---

## 7. Claude resolver — detail

- **Resolve-once, replay-free.** A recipe is the durable artifact; rendering replays recipes
  with zero API cost. This is the cost firewall.
- **When billed:** (a) a brand-new source with no recipe; (b) an adapter parse failure; (c)
  structural drift (sustained drop in parse yield or `raw_hash` shape change). Otherwise $0.
- **Gate + caching:** mandatory `[y/N]` before any billed call (only an explicit `--yes`
  bypasses, single-shot); prompt caching mandatory; batch all pending resolutions into one
  dispatch.
- **Recipe shape:** `fetch_spec` (kind-specific: feed URL / endpoint / selector + pagination)
  + `extract_spec` (field→rule map + cleaning steps) + `confidence` + `prompt_version`.
- **Validation:** after resolving, dry-run the recipe and store yield/sample so a human can
  batch-review low-confidence recipes without an interactive loop.
- **Config:** `config/resolver.yaml` (model id, max billed sources/run, caching) +
  `config/module_3_resolver_prompt.md` (cacheable system prompt). Default model: latest Opus
  (`claude-opus-4-8`) per repo `claude-api` guidance; revisit per cost.

---

## 8. Learning & ranking — detail

- **Signal weighting (initial, tunable):** `like +3`, `read_more +2`, `open +1`,
  `dwell` bucketed (`>30s +1`), `scroll_past −0.5`, `hide −3`. Stored verbatim in
  `interactions`; never overwritten (audit trail, mirrors M7-α's snapshot discipline).
- **Fit:** periodic logistic/linear fit of feature weights against a positive/negative label
  derived from signals; clamp to sane bounds; persist deltas to `ranking_state`. Start naïve
  (per-feature empirical lift) and graduate to a small regression once enough rows exist —
  same data-sufficiency gating philosophy as 2_Funds M7-β/γ.
- **Explainability:** each Highlight can carry a `score_breakdown` (optional, behind a
  detail toggle) so the user sees *why* something ranked high — consistent with this repo's
  preference for transparent, inspectable scoring over opaque models.
- **Features / attributes (D33):** what ranking learns over is extracted in staged layers
  (structural → keyword/entity → embeddings → optional LLM), with multi-granularity back-off so
  fine attributes resolve without losing generalization. Embeddings (not an LLM) are the tool for
  similarity; named topics are for interpretability + rules (D21). Today only structural `source`
  affinity is active; the rest light up as M4/v2 land — no ranker change required.
- **Privacy:** all interaction data is local SQLite; nothing leaves the machine.

---

## 9. Folder layout (target)

```
4_List_renderer/
├─ run_4_List_render.bat            # render-only / view (exists)
├─ run_4_List_server.bat            # (M7) server + browser + interaction capture
├─ config/
│   ├─ sources.yaml                 # seed sources + default selection
│   ├─ ranking.yaml                 # interest-model base feature weights (tunable)
│   ├─ resolver.yaml                # Claude resolver settings (model, gate, caching)
│   └─ module_3_resolver_prompt.md  # cacheable resolver system prompt
├─ data/
│   ├─ list_renderer.db             # sources / recipes / items / interactions / interests
│   └─ sample_input.json            # file-source demo (exists)
├─ Outputs/
│   ├─ list_results.html            # stable template, hash-versioned (exists)
│   └─ list_results_data.js         # per-run sidecar — only file rewritten (exists)
├─ _intermediate_outputs/           # optional plumbing
├─ scripts/
│   ├─ 4_render_list.py             # M8 render orchestrator CLI (exists)
│   ├─ 4_resolve_source.py          # M3 — Claude resolve/refresh a recipe (billed, gated)
│   ├─ 4_add_interest.py            # M5 — add area of interest, discover+register sources
│   ├─ 4_learn_ranking.py           # M6 — re-fit interest weights from interactions
│   └─ 4_serve.py                   # M7 — local server + browser launch
├─ spec/
│   ├─ Overall_specification.md     # this file
│   ├─ decisions.md                 # D1.. decision log
│   └─ module_*.md                  # per-module specs (spun out as built)
└─ src/list_renderer/
    ├─ render.py                    # M0 (exists)
    ├─ sources.py                   # M1 registry
    ├─ adapters/                    # M2 — base + file/biopharm/news (exist) + rss/web/sqlite/http
    ├─ resolver/                    # M3 — Claude recipe engine
    ├─ normalize.py                 # M4 — unify + dedup + tag
    ├─ interests.py                 # M5 — classify + discover
    ├─ ranking.py                   # M6 — interest model
    └─ server.py                    # M7 — local server
```

---

## 10. Build order / roadmap

| Phase | Modules | Outcome |
|---|---|---|
| **0 (done)** | M0 + 3 demo adapters + CLI + bat | Fixed template renders file/biopharm/news. |
| **1 (done)** | M1 registry, tiered DB schema (+identity/auth scaffold), M8 registry render | Sources live in SQLite; render = the board's enabled source set. (D28) |
| **2 (done)** | M2 generic `rss/http_api/sqlite` adapters + SWR `items` cache | Any feed/DB/API addable by config; read-through cache + serve-stale. `web` scrape → Phase 3 (needs resolver); dedup → M4. (D29) |
| **3 (done)** | M3 resolver + `recipes` + LLM runner (D24 seam) + `web` adapter | New/messy sources self-resolve to cached recipes; estimate + `[y/N]` gate; free RSS shortcut. (D30) |
| **4 (done)** | M7 (stdlib server + `/api/*`), `interactions`, template v2 hooks, PWA | User selects sources + like/hide/click; signals persist append-only; board served live. (D31) |
| **5 (done)** | M6 `ranking.py` + `ranking.yaml` + `ranking_state`, `4_learn_ranking.py` | Highlights ordered by a transparent weighted-feature model; affinities learned offline from interactions; mute/boost rules (D21) + seen-penalty (D22); fail-open. (D32) |
| **6 — Interest input** | M5, `interests` | User adds a site/topic; app discovers + registers sources, boosts ranking. (Capture built in P4; discovery here.) |
| **7 — Polish** | iOS layout, score-explain toggle, scheduler | Mobile board, transparency, auto-refresh. |
| **8 — Hosted skeleton (BACKLOG, later)** | Next.js/Vercel multi-user shell (see §13 + below) | Multi-user deployment; same modules, swapped backends. |

Each phase ships independently and leaves the app runnable. **Phase 1 (local single-user) is
the immediate build.** Deferred backlog: hosted skeleton (§14), AI summaries & "who pays"
billing model (§15). Note: §15's call-path seams (D24) are built *within* Phase 3, even though
the summary feature itself is deferred.

---

## 14. Backlog — Hosted multi-user deployment (deferred)

Captured for later; **not** part of the immediate local build. Implements the multi-user
topology from §13 (D11–D15) on the Python stack (D25). Because local v1 *is* a FastAPI app, the
hosted version is mostly a **storage-backend + auth + worker** addition, not a rewrite. Scope
when picked up:

- **App shell** — deploy the existing FastAPI app to a Python-friendly cloud (Render / Railway /
  Fly / Azure Container Apps); serve the static PWA from a CDN; the board view uses the §13.7
  delivery (`GET /api/board`) it already uses locally.
- **Storage** — port the §5 SQLite schema to **Postgres** migrations, partitioned by tier (A:
  catalog/recipes/items+enrichment; B: users/subscriptions/interactions/weights); **Redis** for
  hot caches (per-user sidecar TTL, source-fetch SWR, Claude-budget + rate-limit counters);
  object storage for raw snapshots. Swap the storage layer behind the tier interfaces.
- **API** — the same FastAPI `/api/board`, `/api/interact`, `/api/interest` (+ source CRUD)
  endpoints; add multi-user auth + `user_id` scoping.
- **Background workers** — platform cron + a worker process (or Celery/RQ/Arq, or QStash
  webhooks) for: source refresh (run recipes, upsert content cache, SWR), Claude resolution
  queue (system budget, D13), and periodic per-user weight re-fit. Never in a request path.
- **Auth & tenancy** — FastAPI auth (OAuth/OIDC); `user_id` row-level isolation; Tier-A writes
  restricted to system workers (D14); fetched content sanitized before render.
- **Governance** — system budget counters in Redis (daily Claude spend cap, new-domain quota +
  queue, global dedup), allow/deny lists, admin review surface for low-confidence recipes.
- **Carry-over constraints** — licensed data provider for any finance source before public
  launch (`project_data_provider_switch`); data export + delete; opt-in anonymized popularity
  priors only.
- **Fallback** — only if Vercel-native becomes mandatory: a TS/Next.js API layer behind the same
  `/api/*` contract (D25). PWA + storage tiers unchanged.

Prerequisite: Phases 1–6 (local) should be substantially built first so the tier interfaces and
recipe/ranking logic are proven before swapping in the hosted storage backend.

---

## 15. Backlog — AI summaries & the "who pays" billing model (deferred)

AI per-article summaries are **excluded from v1** (D23) but planned later. The blocking
question is **who pays for the summarization task**, with three candidate models (D24):

| Model | Who pays | Trade-offs |
|---|---|---|
| **(a) User BYOK** | the user, via their own Anthropic API key | No cost to operator; user controls spend. Friction (user must hold a key); key storage/security (encrypted, Tier B/local). |
| **(b) App-owner corporate key** | operator, pooled key + volume discount, metered per user | Frictionless UX; operator captures batch/cache discounts. Operator bears cost → needs quotas/plan limits + abuse control. |
| **(c) Advertiser-subsidized** | advertiser, funding an AI-task budget pool in exchange for ad surface | Free to the user. Requires an ad surface + attention metrics → must reconcile with D14 privacy (opt-in, anonymized). |

These are **not mutually exclusive** — the `billing_context` abstraction (D24) lets them
coexist (e.g. free tier = corporate-key-with-quota, power users = BYOK, ad-supported tier =
advertiser pool). The decision is *which to offer and how to combine*, taken when the feature
is built.

**v1 must already be compatible (built in Phase 3, D24):**
- All Claude calls routed through one `run_llm_task(task_type, payload, billing_context)`.
- Credentials/budget come from a pluggable `billing_context` (v1 = `self` only).
- The `llm_tasks` ledger records `task_type` + `payer`/`billing_context` per call (§5).
- Summaries land as **Tier-A per-item cached enrichment** (additive to `items`), shared across
  users, never per-render — so cost is O(items), reusable across all three payer models.
- The result schema (§4.3) stays open to a future `sponsored`/ad item kind (no template fork).

When built, summaries reuse the resolver's entire call path as a new `task_type` — the only
new work is the summary prompt, the chosen `billing_context`(s), and (for model c) the ad
surface.

---

## 16. Identity, accounts & authentication (D27)

Designed now so the app scales from **one user on `localhost` (v1)** to **many users in the
cloud** with *no data migration* — every per-user row already carries `user_id` (D16).

### 16.1 Separation of concerns

- **`users`** — the account/identity (id, kind, email, display_name, role, status, plan,
  byok_key_ref). *Who* the user is.
- **`auth_identities`** — *how* they sign in. One user → many linked identities
  (password / Google / GitHub / Apple / magic-link). Passwords hashed with **argon2id**;
  OAuth stores only `provider` + `provider_subject`.
- **`sessions`** — active logins: a **hash** of the session token (never the raw token),
  `ip_hash`, `user_agent`, `created/last_seen/expires`, `revoked`.
- **`login_audit`** — security/audit trail (success/fail/logout/refresh) with `ip_hash` +
  `user_agent`, for anomaly detection and rate-limiting.

### 16.2 The scale-up path (localhost → cloud)

| | v1 (localhost, single-user) | Hosted (multi-user) |
|---|---|---|
| Users | one implicit `local` user | real accounts (`kind='account'`) |
| Sign-in | none (trusted localhost) | OAuth/OIDC + magic-link (+ optional password) |
| Sessions | none | `sessions` rows, cookie/JWT to the PWA |
| Auth tables | created but **empty** (scaffold) | populated |
| Storage | SQLite | Postgres (§13) |
| Data migration | — | **none** — all rows are already `user_id`-scoped |

Turning on multi-user is therefore three additions — enable auth, swap storage, create real
users — not a rewrite.

### 16.3 IP address & privacy

IP is PII: stored **hashed/truncated** (e.g. /24), short retention, **Tier B only**, included
in export/delete (D14). Used for security, rate-limiting, and optional geo — never sold or
shared (cross-user popularity priors stay anonymized + opt-in).

### 16.4 Security baseline (hosted)

HTTPS-only; argon2id password hashing; opaque-random or rotating JWT session tokens with only
a hash stored; CSRF protection for cookie sessions; rate-limited auth endpoints; **BYOK keys
encrypted at rest** (D24). `role ∈ {user, admin}` gates the admin recipe-review surface (D13);
tenant isolation by `user_id` (D14).

### 16.5 v1 behavior

`localhost` is single-user and trusted — **no sign-in, no sessions, no IP capture**. The
`local` user is implicit; the FastAPI server binds to `127.0.0.1`. The auth tables exist as
scaffold so the hosted port is purely additive.

---

## 11. Resolved scope decisions & remaining assumptions

User-confirmed 2026-06-19 (✅); remaining assumptions take the first option (◻).

1. ✅ **Claude boundary = recipe-resolution only.** Claude infers fetch/extract recipes,
   caches them, and is NOT in the per-render path. No live per-render summarization in v1.
   (Locks D4.)
2. ✅ **Learning signals = implicit + explicit.** Track clicks, dwell, and scroll-past
   (implicit) plus like/hide (explicit); explicit weighted higher. (Locks D6; implicit
   tracking is in scope.)
3. ✅ **First real source kinds = News/RSS + Social/forums** (Reddit, X/Twitter, Hacker
   News). The repo's own biopharm/finance pipelines stay as *demo* adapters, not a v1
   priority. M2's generic-adapter work therefore prioritizes `rss`/`web` then social-feed
   adapters; M5 interest input targets these kinds first. (Updates D2; see D10.)
4. ✅ **Device target = desktop + mobile via one responsive PWA** (D26). Mobile-first responsive
   is now a v1 requirement; service worker (offline + cached paint) lands with the client.
5. ✅ **Tech stack = Python/FastAPI backend + responsive PWA**, any Python-friendly cloud (D25).
6. ✅ **Topic tagging / attribute extraction method (D33).** Staged by cost: L1 structural
   (free, now) → L2 keyword/entity controlled-vocab (M4) → L3 embeddings (v2; the similarity +
   dedup engine, Tier-A per-item enrichment) → L4 generative-LLM tagging (optional, behind D24,
   once-per-item cached). Structural backbone is sufficient to start; embeddings (not an LLM) are
   the right tool for similarity; LLM is a targeted quality layer only. See D33.
7. ◻ **Single vs multi-user.** Assumed single local user (no auth) for v1; multi-user is the
   hosted topology (§13).

---

## 12. Cross-cutting rules (do not re-litigate)

1. One fixed template; all sources adapt to it (adaptive display). Restyle = version bump,
   never a data-contract change.
2. `normalize_result` is the only place that shapes data for the template.
3. SQL-only durable state; sidecar JSON is the sole on-disk serialization exception.
4. Claude is cache-first and cost-gated; recipes are the durable artifact, renders replay
   them for free.
5. Browser writes go through the local server; durable state is SQLite, UI prefs mirror to
   localStorage.
6. SWR on every fetch path.
7. Interaction logs are append-only (audit trail for ranking).
8. Don't prompt the user per ambiguous parse/resolution — resolve in code with confidence +
   audit; batch any human review.
9. Update this spec + `decisions.md` at every module completion (repo discipline).

---

## 13. Deployment topologies & multi-user hosting

The same M0–M8 module map serves two topologies; only the **storage backend and runtime**
change. The architecture is therefore topology-agnostic: write modules against the tier
interfaces, not a specific store.

- **Local single-user** (v1 default) — SQLite + localStorage + one local process. All three
  storage tiers below collapse onto one machine.
- **Hosted multi-user** (e.g. Vercel) — the three tiers separate across the browser, per-user
  DB rows, and a shared system layer with background workers.

### 13.1 The three storage tiers

The central question for hosting is *what lives where*. Three tiers, by ownership:

| Tier | What | Hosted location | Local-mode equivalent | Why here |
|---|---|---|---|---|
| **A. Shared / user-independent** | Claude-resolved **fetch/extract recipes**; canonical **source catalog**; **fetched content cache** (items + topic tags / entities / dedup keys / optional embeddings); optional anonymized popularity priors | Postgres (durable) + KV/Redis (hot, TTL) + Blob (raw snapshots); written **only** by system workers | SQLite tables (`recipes`, `sources`, `items`) | Cost amortization (§13.3). Resolve/fetch **once**, serve **all**. Respects publisher rate limits. |
| **B. Per-user profile (server)** | account/auth; **subscriptions** (selected sources + board order/config); **declared interests**; **interaction log**; **personal ranking weights**; synced settings | Postgres rows keyed by `user_id`, row-level isolated | SQLite (single implicit user) | Private, cross-device, follows the user. The training data + model for *their* ranking. |
| **C. Local / client** | session token; **cached last sidecar** (instant paint / offline); **optimistic interaction buffer**; scroll position / expanded-row UI state; device-only prefs (e.g. reduced-motion) | Browser localStorage / IndexedDB / cookie | localStorage + the on-disk sidecar | Latency, offline, UX. Server (or URL param) is always authoritative (`feedback_cache_user_prefs`). |

**Worked example (the user's case): "how to fetch fiercebiotech.com."** The Claude-resolved
recipe is **Tier A** — resolved once for the domain, reused by every subscriber forever. The
fetched FierceBiotech articles are **Tier A** too (one fetch per TTL serves all subscribers).
*Which* user follows FierceBiotech, and *how they rank* its articles, is **Tier B**. The
scroll position and the last-seen board snapshot are **Tier C**.

### 13.2 What is NOT per-user (important)

To keep per-user cost near zero, deliberately push to Tier A everything that isn't a function
of *who* is asking:
- Recipes (how to fetch + extract a source).
- Raw fetched items + their enrichment (tags, entities, dedup, embeddings).
- Source-level metadata (favicon, canonical name, health/last-fetch status).

Tier B holds only the **thin personalization layer**: the user's subscription set, their
interaction history, and their learned weight vector. Everything a user sees is a *join* of
shared Tier-A content with their Tier-B model.

### 13.3 Why the shared layer is the whole point (cost economics)

- **Claude resolution = O(distinct sources)**, not O(users × sources). A domain resolves
  once globally; a new user who follows only known sources costs **~$0 of Claude**.
- **Source fetching = O(distinct sources × refresh cadence)**, not O(users). One fetch per
  source per TTL serves every subscriber — inherently rate-limit-friendly and polite to
  publishers.
- **Per-user marginal cost ≈** cheap storage (subscriptions + interaction log + a small
  weight vector) + one ranking join. The shared layer is a public good; the per-user layer
  is thin. This is the economic justification for hosting at all.

### 13.4 Hosted runtime (Python stack — D25)

The backend is **FastAPI on a Python-friendly cloud** (Render / Railway / Fly / Azure
Container Apps), serving the same code as local v1. The static PWA sits on any CDN. Background
jobs run as scheduled/worker processes (the platform's cron + a worker dyno, or a queue) —
**never in a user request path**.

| Concern | Mapping (Python stack) |
|---|---|
| Static PWA (template + assets + service worker) | Any CDN (Cloudflare / Netlify / Vercel static / the app's own static mount) |
| API (board fetch, interaction ingest, interest add, source CRUD) | **FastAPI** service (stateless request handlers) |
| Relational state (catalog, recipes, subscriptions, interactions, weights) | Managed **Postgres** (Neon / Supabase / RDS / the platform's PG add-on) |
| Hot caches (per-user sidecars w/ short TTL, source-fetch SWR cache, rate-limit + Claude-budget counters) | **Redis** (Upstash / the platform's Redis add-on) |
| Large raw payloads (raw HTML/JSON for recipe debugging) | Object storage (S3 / R2 / platform blob) |
| Background refresh & Claude resolution (out of request path) | Cron + a **worker process** (or a task queue: Celery/RQ/Arq, or Upstash QStash webhooks) |
| Auth | FastAPI auth (OAuth/OIDC via Authlib, or a managed provider) — session cookie / JWT to the PWA |

> If Vercel-native deployment ever becomes mandatory, the documented fallback (D25) is a
> TypeScript/Next.js API layer behind the same `/api/*` contract — the PWA and storage tiers
> are unchanged.

### 13.5 Data flows (hosted)

- **New/unknown source.** User adds an interest → classify → if no recipe exists for that
  source signature, **enqueue resolution** → a worker calls Claude under the system budget →
  recipe written to Tier A → the source becomes fetchable for **everyone**.
- **Refresh (independent of any user).** Cron iterates active sources (any source with ≥1
  subscriber) → runs the cached recipe → upserts items into the Tier-A content cache
  (SWR/TTL) → computes shared enrichment. **One fetch per source**, regardless of subscriber
  count.
- **Board request.** `GET /api/board` → join (user subscriptions × shared items) → rank with
  the user's weights → per-user sidecar JSON → cache in KV (short TTL, invalidated on new
  interactions) → template renders.
- **Interaction.** Client buffers like/hide/dwell/scroll → batched `POST /api/interact` →
  append to the user's interaction log → a periodic worker re-fits that user's weights.

### 13.6 Governance changes vs local mode

- **Cost gate.** Local mode uses an interactive `[y/N]` (D4). Hosted has no terminal, so it
  is replaced by **system budget governance**: a per-day global Claude spend cap, a
  new-domain resolution quota + queue, global dedup (resolve a domain once), allow/deny
  lists, and admin review for low-confidence or novel domains. (D13.)
- **Tenancy & integrity.** Tier-A tables are written **only by system workers**, never
  directly by user requests — this prevents recipe/content poisoning via a crafted source.
  Tier-B rows are isolated by `user_id` (row-level security). All fetched third-party content
  is **untrusted**: sanitize before it reaches the template. (D14.)
- **Privacy.** Interaction data is private per user; Tier A holds **no per-user PII**; any
  cross-user popularity prior is opt-in and anonymized/aggregated. Support data export +
  delete. (D14.)

### 13.7 Rendering delivery (contract addendum)

The Highlight schema (§4.3) is identical in both topologies; only delivery differs:
- **Local** — the CLI/server writes `list_results_data.js`; the template loads
  `window.LIST_DATA` via `<script>`.
- **Hosted** — the template fetches `GET /api/board` → the same `LIST_DATA` JSON shape. A
  small loader shim selects the mode (static sidecar present → use it; else fetch the API).
  (D15.)
