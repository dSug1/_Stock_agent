# Module 5 — Interest input & source discovery

**Status:** ✅ built (Phase 6, D35) · **Dir:** `4_List_renderer/`
**Decisions:** D7 (auto-resolve + audit, no per-item prompts), D4 (billed Claude stays gated),
D10 (news/RSS + social first), D35 (this build). Spec §6 M5.

M5 closes the loop: the user **declares an area of interest** and the app turns it into concrete,
registered sources on the board — so the universe grows by intent, not by hand-editing config.

---

## 1. Flow

```
value ("semiconductors" | "NVDA" | "https://site.com")
   → classify  (site | ticker | topic)
   → resolve + register a discovered source (origin='discovered', on board)
   → store the interest (interests table)  → feeds M6 interest_match
```

`src/list_renderer/interests.py` · `scripts/4_add_interest.py` · `POST /api/interest`.

## 2. Classification (`classify`)

| Input | Kind | Example |
|---|---|---|
| URL or `domain.tld[/...]` | `site` | `https://theverge.com`, `www.bbc.com/news` |
| `$TICK` or 3–5 uppercase letters | `ticker` | `$TSLA`, `NVDA` |
| anything else | `topic` | `semiconductors`, `AI safety` |

(`query` folds into `topic` for v1 ranking. Ticker detection requires 3+ uppercase to avoid
tagging `AI`/`ML` as tickers; an explicit `$` prefix always wins.)

## 3. Resolution (all free; no Claude in the server path)

- **topic / ticker → Google News RSS search source.** `google_news_feed(q)` →
  `news.google.com/rss/search?q=…`; registered as a generic `rss` source. The board immediately
  surfaces items (with `published_at` from M4) — no new adapter code. Tickers search `"<T> stock"`.
- **site → free RSS discovery.** `discover_site_feed` returns the URL if it looks like a feed,
  else the page's declared `<link rel=alternate type=application/rss+xml>` (the M3 resolver
  shortcut). Discovered feed → registered `rss` source.
- **site with no declared feed → `pending`.** A billed Claude resolution is **deferred** to the
  `[y/N]`-gated `scripts/4_resolve_source.py` (D4); the server never calls Claude (no terminal),
  keeping M5 non-interactive (D7). The report tells the user the exact command.

Registered sources use a stable id (`disc_<kind>_<slug>`), `origin='discovered'`, and are placed
on the board. Re-adding the same interest is idempotent (de-duped on kind+value).

## 4. Ranking link

Each interest is written to `interests` (`active` | `pending`). The M6 `interest_match` feature
reads that table directly, so a declared topic boosts matching items immediately (verified: a
matching item outscores a non-matching one). The discovered source then *supplies* matching items.

## 5. Audited, not interactive (D7) · fail-open

Resolution leaves an audit trail (an `interests` row + a `discovered` source) and never prompts
per item (`feedback_avoid_multiplying_user_requests`). Any network/discovery failure stores the
interest `pending` and reports why — it never raises or blanks the board.

## 6. Run

```bash
python scripts/4_add_interest.py "semiconductors"              # topic → search source
python scripts/4_add_interest.py "NVDA"                        # ticker → "NVDA stock" search
python scripts/4_add_interest.py "https://www.theverge.com"    # site → discovered RSS feed
python scripts/4_add_interest.py --kind topic "AI safety"      # force the kind
python scripts/4_add_interest.py --list                        # show declared interests
# In the served board: the "Sources" panel → "Add an interest" box (POST /api/interest).
```

## 7. Not in M5 (later)

Deeper **ticker → finance binding** (v1 uses a news search) · a query-as-saved-search distinct
from topic · UX for resolving `pending` site interests (today reported inline + via `--list`) ·
auto-running the billed resolver for pending sites (kept manual + gated, D4).
