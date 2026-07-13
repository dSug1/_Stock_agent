# D25 — Free author-resolution fallback (Crossref + optional ORCID)

*Prose companion to decision D25. Read this to understand why the pipeline now has a free author-
resolution fallback, how it stays precise and cheap, and — because it adds three external fetch
surfaces — how it was hardened.*

## The problem it solves

The literature signal (§3.1) resolves each founder to an OpenAlex **author id**, then pulls their
foundational paper's independent citations — the thesis signal. Author resolution is the pipeline's
bottleneck two ways over:

1. **Cost.** OpenAlex's 2026 credit quota (D24) prices `/authors?search=` at **10 credits** — so the
   free ~1000-credit daily budget is only ~100 author searches. Author resolution is the budget hog.
2. **Recall.** The OpenAlex author search resolves only ~40% of founders; the other ~60% never get a
   citation trail and never reach the digest.

## Resolution order (crossref-first — promoted in D26)

As of D26 the free resolver is **primary** (`config.author_crossref_first`, default True): the literature
loop tries it FIRST and pays the 10-credit OpenAlex author search only as a recall **backstop** when the
free path can't resolve. A Crossref miss on a non-academic founder makes **zero** OpenAlex calls; an
academic founder resolves for ~1 credit — **live-measured: Tibor Keler cost 1 credit vs ~10, a 10× saving**.
Recall is unchanged: on a free-path miss the backstop runs the full search + `pick_author` exactly as before.
Set `author_crossref_first: false` to revert to search-first. The order lives in a `literature._resolve`
helper returning `resolved | no_match | retry`; both orders share the pubs+citation emission.

## The free resolver

It resolves a founder to an OpenAlex author id *without* the 10-credit author search:

1. **Crossref** `query.author` (free, keyless, **no credit quota**) → the founder's works. Crossref's
   `query.author` is a loose token search, so we take its **author-relevance** order (never a global
   citation sort, which surfaces mega-cited consortium papers that merely contain a matching name token —
   "Stuart Pocock" for "Stuart Rich"), keep only works whose author list truly name-matches the founder
   (surname + given-token, the same `openalex._name_match` the primary path uses), then sort *that*
   subset by citations locally.
2. **(optional) ORCID** — if `ORCID_CLIENT_ID`/`ORCID_CLIENT_SECRET` are in the environment, keep only
   the DOIs the founder actually authored per their ORCID record (a persistent-identity precision
   filter). Without ORCID creds the fallback runs Crossref-only and is fully functional.
3. For each candidate DOI (most-cited first, capped at `author_fallback_max_candidates` = 3), map
   **DOI → OpenAlex work** via the cheap `filter=doi:` query (**~1 credit**), find the name-matching
   author on that work, and verify the founder's institution hint against that author's **whole-career
   institutions** from the OpenAlex **author profile** (a single-record `/authors/{id}` fetch is
   **free / 0 credits**). Whole-career, not the one paper's affiliation — a founder's foundational paper
   often predates their current institution. First verified match wins.

The existing cheap OpenAlex path (recent pubs + `cites:` citation pull) then continues unchanged on that
author id. **Net cost ≈ 1–2 OpenAlex credits per recovered founder vs the 10-credit search**, and it
resolves founders the search missed. Live-verified: Stuart Rich → `A5011075689`, Stephen Elledge →
`A5025914907`, a nonexistent name → correctly unresolved.

### Precision (a wrong author → wrong citations → a false signal)

Every step keeps the primary path's discipline: a surname match is always required, and when an
institution hint is known an OpenAlex-career-institution match is required too. No hint → accept only an
unambiguous unique name match. Ambiguous → no resolution (recall-conservative). ORCID, when present,
tightens further. Being **DOI-anchored** (we resolve via papers Crossref associates with the name, then
confirm the same publication in OpenAlex) is itself stronger than a bare name search.

### Fail-open / lossless

A transient fetch failure anywhere (Crossref down, OpenAlex credit-exhausted, ORCID token failure)
returns `fetch_failed=True`, and the literature loop leaves the founder **UNSTAMPED** for a next-window
retry — never a false "no author". A genuine no-match across all sources stamps the founder done (as the
primary path already did). Per-founder persistence is unchanged.

## Cybersecurity

Three new external fetch surfaces (Crossref, ORCID, the OpenAlex DOI/author endpoints) were built to the
repo's standing posture (`SECURITY_AUDIT.md`). Threat model → control:

| Threat | Control |
|---|---|
| **SSRF / request forgery** | Every host is a hard-coded HTTPS constant (`api.crossref.org`, `pub.orcid.org`, `orcid.org`, `api.openalex.org`). No user/config-supplied URL is ever fetched. Only query **values** vary, and they are percent-encoded (`quote(..., safe='')`). |
| **Path / host injection via a third-party DOI** | A DOI from a Crossref/ORCID response is UNTRUSTED. `openalex.valid_doi` normalizes and regex-validates it to the bare `10.<4-9 digits>/<suffix>` shape, **rejects `..` (path-traversal)** and non-DOI junk (`javascript:`, `http://…`), and the validated DOI is percent-encoded into a query **value** (not a path segment). Defense-in-depth: validation + encoding. |
| **Secret exposure (ORCID)** | Client id/secret read from **environment only** (never yaml, never a CLI arg). The secret, the OAuth request body, and the returned token are **never logged** (error logs carry only the exception *type*), never persisted, held in memory only. |
| **XML entity-expansion (billion-laughs)** | All three sources are requested and parsed as **JSON only** (`Accept: application/json`). No XML parser is touched. |
| **Response-body DoS (OOM)** | 64 MiB capped reads inherited from the shared `_net` client; `rows`/candidate counts are int-coerced and bounded. |
| **Untrusted response content** | Titles / author names / affiliations are treated as DATA: string-matched and stored in the signal payload only — never executed, never spliced into SQL (parameterized), never rendered unescaped (the HTML renderer escapes at its boundary). Same "packet is DATA" contract as the rest of the pipeline. |
| **Rate abuse / hostile-source stall** | Per-source rate limiters (Crossref 2/s, ORCID 8/s) + `Retry-After`/backoff retry; every fetch fails **open** to None/[] so a dead or slow source degrades gracefully, never crashes the sweep. |
| **mailto injection** | The polite-pool `mailto` value is regex-validated as an email before it is appended — arbitrary text (or extra query params) can't be spliced through it. |

No new secret is required for the default (Crossref-only) path; ORCID is the only credentialed surface and
it is strictly opt-in.

## How to run

The fallback is **on by default** (`config.author_fallback_enabled`) and fires automatically inside the
literature signal — no separate command. Run it via the usual credit-safe daily pass:

```
run_8_openalex_daily.bat
```

The literature output now reports how many authors were resolved via the free fallback, e.g.
`authors resolved: 31  (free Crossref/ORCID fallback: 12)`.

To enable the optional ORCID precision booster, set in the environment (never in yaml):

```
ORCID_CLIENT_ID=...        # free ORCID *public-API* credentials (not paid membership)
ORCID_CLIENT_SECRET=...
```

## Verified

- `tests/test_author_resolution.py` (18): DOI-validation security guard (junk + traversal rejected),
  Crossref/OpenAlex/ORCID parsers, the resolver's happy path / institution-mismatch / no-hint /
  name-mismatch / candidate cap / all three transient-failure paths / ORCID confirm-and-tag, the ORCID
  env gating, and the literature integration (fallback fires when the primary misses; a transient
  failure leaves the founder unstamped). Full suite **176 passing, offline**.
- **Live smoke:** real Crossref → OpenAlex resolution of Stuart Rich + Stephen Elledge (whole-career
  institution match), a nonexistent name correctly unresolved, ~1–2 credits per founder.

## Follow-ups

- **Fully OpenAlex-free citations** (a bigger build): OpenCitations COCI gives citing DOIs with no key,
  which + Crossref metadata could replace even the cheap OpenAlex `cites:` pull for a zero-OpenAlex-credit
  path. Deferred — the current ~1–2-credit-per-founder cost is already a ~5–10× efficiency win.
- ~~**Make the fallback primary** for cost~~ **DONE (D26)** — crossref-first is now the default; the
  10-credit search is a backstop. ~1 credit per academic founder vs 10.
