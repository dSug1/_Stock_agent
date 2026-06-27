# _Stock_agent — Security audit & remediation (2026-06-27)

Authorized defensive audit of the whole repo (62K LOC, 6 components) + full remediation. All 16
findings (S1–S16) are **implemented and verified**. This file is the authoritative record.

## Threat model
Personal, local, single-user tools — but several expose **local HTTP servers** (one was LAN-bound),
**all fetch external web data** (SEC/EDGAR, RSS/web sources, yfinance, award/jury feeds), and some feed
external text into the **Claude API**. So the real surface is: server exposure / path-traversal /
arbitrary-write, SSRF, response-size DoS, XSS in generated reports, untrusted-XML, prompt-injection, and
secret handling.

## Baseline already-safe (verified, left as-is)
Parameterized SQL throughout (the `f-string` queries are schema-DDL with internal constants, not
external input); `yaml.safe_load` only; no `eval`/`exec`/`pickle`/`os.system`/`shell=True` (subprocess
is list-form with internal args); `.env` gitignored + untracked (only `.env.example` committed); API
keys read from env, never logged; XML via ElementTree (no external-entity XXE — only the expansion-DoS
gap, fixed by S11); prompt-injection largely contained (external text delimited as data, model output
schema-validated, drives no unsafe action — except the 4_List resolve→fetch chain, fixed by S1/S6).

## Findings & remediation (all DONE)

| ID | Sev | Component — locus | Weakness | Fix implemented |
|----|-----|----|----|----|
| **S1** | High | 4_List adapters (`_rss_parse`,`web`,`http_api`,`resolver/resolve`) | **SSRF** — fetched user/config/Claude URLs with no allow-list → cloud metadata / other localhost services / `file://`; redirects unvalidated; no size cap | New `adapters/_net.py`: `validate_url()` (http/https only; reject loopback/private/link-local/reserved/multicast IPs + `169.254.169.254`) + `fetch_bytes()` (redirect-revalidating opener, 64 MiB cap). All four fetchers routed through it. |
| **S2** | High | 4_List `Outputs/list_results.html` `esc()` | **XSS** — `esc()` didn't encode quotes → attribute breakout from feed/Claude titles | `esc()` now also encodes `"`→`&quot;`, `'`→`&#39;` (source template). |
| **S3** | High | 2_Funds `scripts/6_serve_report.py` | **Arbitrary file write** — unvalidated `quarter` + Windows backslash escaped `Outputs/` | `_valid_quarter()` whitelist `\d{4}Q[1-4]` on GET+PUT + `_within_outputs()` containment assert. |
| **S4** | High | 0_Renderer `2_stock_visualizer.py:939` | **LAN exposure** — Flask bound `0.0.0.0`, no auth | Bind `127.0.0.1`. |
| **S5** | Med | 3_Biopharm `scripts/3_7_serve_selection.py` | **Path traversal** — `startswith` guard allowed sibling-prefix dirs | True ancestry via `resolved.relative_to(outputs)` + reject `..` pre-resolve. |
| **S6** | Med | 4_List `resolver/resolve.py` | **Prompt-injection → SSRF amplification** — Claude-returned `feed_url` fetched every refresh | `validate_url()` on Claude-returned URLs before `save_recipe` and again at fetch. |
| **S7** | Med | 4_List `Outputs/list_results.html` | **`javascript:`/`data:` href + window.open** | `safeUrl()` gates hrefs/`window.open` to http/https only. |
| **S8** | Med | local servers (4_List `server.py`, 2_Funds `6_serve_report.py`, 3_Biopharm `3_7_serve_selection.py`) | **CSRF on loopback** — no Origin check → a visited webpage can POST to localhost | Reject POST/PUT if `Origin` present and not the loopback origin; require JSON content-type; body-size cap. |
| **S9** | Med | 0_Renderer `2_stock_visualizer.py` | **DoS / fan-out** — unbounded `tickers`; uncapped search response | Cap `tickers[:25]` (400 if huge); bound Yahoo search read to ~5 MB. |
| **S10** | Med/Low | every component's HTTP getters | **No response size cap (OOM)** | 64 MiB capped read (`read(MAX+1)`+reject). 5_Hype: shared `nethttp.capped_read` across 11 ingest getters + archive (also closed handoff §4(a)). 2_Funds/3_Biopharm/1_not_used: in their getters. (Discovery getter already capped — D36.) |
| **S11** | Low | 2_Funds, 3_Biopharm, 1_not_used XML parsers | **XML entity-expansion (billion-laughs)** | `defusedxml.ElementTree.fromstring` (try/except ImportError → stdlib). `defusedxml` added to `requirements.txt`. |
| **S12** | Low | 0_Renderer `index.html`, chart template | **postMessage no origin check** (UI-only) | `if (ev.origin !== location.origin) return;`; `'*'` targets → `location.origin`. |
| **S13** | Low | 0_Renderer API routes | **Error detail leakage** — raw `str(exc)` to client | Log server-side, return generic error. |
| **S14** | Low | 4_List `http_api.py` | **Config `headers` unvalidated** (Host/auth override) | `_safe_headers()` drops hop-by-hop + `Host`. |
| **S15** | Low | 2_Funds/3_Biopharm Claude dispatch | **Prompt-injection (already contained)** — optional hardening | 3_Biopharm: context pack wrapped in `<untrusted_context>` + note. (Already data-delimited + schema-validated.) |
| **S16** | Low | 1_not_used (dead code) | Same uncapped-fetch/XML gaps if revived | Caps + defusedxml + `name` validation in `probe_missing_ciks`. (Component is dead code — reachable only via its own Task Scheduler `.bat`.) |

## Verification
- All 36 changed Python files `py_compile` clean.
- New security tests pass: 2_Funds `test_security_hardening` (31), 3_Biopharm `test_serve_selection_security`+`test_edgar_response_cap` (12), 4_List `test_net` (12), 5_Hype `test_nethttp` (3).
- Full suites green: 3_Biopharm **530 passed**/4 skipped (pre-existing), 2_Funds **41**, 5_Hype **173**, 1_not_used **89**. 0_Renderer + 4_List have no suites → compile + import smoke.
- Live checks: 4_List `validate_url` blocks `169.254.169.254`/`127.0.0.1`, allows public; 2_Funds `_valid_quarter` blocks `foo\..\..\evil`, accepts `2024Q1`.

## Key invariant: discovery is zero-LLM (no prompt-injection surface)
5_Hype's theme-discovery fetches the most third-party feeds but runs **zero-LLM** (local embeddings +
cosine), so scraped jury text never enters a model prompt — no injection surface there. This holds only
until the optional Claude theme-*naming* step (spec §6) is built; at that point apply the S15 pattern.

## Residual / not changed (by design)
- **Pre-existing test failure in 1_not_used (`test_cusip_resolver_cache_hits_api_once`) — FIXED
  2026-06-27.** Was a stale test mock (OpenFIGI response omitted the `securityType` the resolver's
  US-equity allow-list requires); fixed the mock to include `"Common Stock"` (the resolver filter,
  correct, was left untouched). Suite now **89 passed**.
- `3_Biopharmcatalyst_parser/data/render_price_cache.json` shows as modified — a **test-run data
  artifact**, not a code change.
- `requirements.txt` is UTF-16 (repo convention); `defusedxml>=0.7` appended in matching encoding.

## Standing policy (repo-wide)
Treat all fetched/scraped content as untrusted: safe loaders only; parameterized SQL; `html.escape` +
http(s)-only hrefs in any generated report; size-capped reads (64 MiB); SSRF allow-list + private-IP
block for any **user/config-supplied** URL; local servers bind `127.0.0.1` with an Origin/CSRF check on
writes. Any keyed provider reads its key from env/`.env`, never logged/committed.
