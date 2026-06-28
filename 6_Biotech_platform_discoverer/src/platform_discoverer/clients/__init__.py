"""External data clients for the screener.

M2b ships the listing-directory clients (free hybrid: SEC EDGAR by SIC for US, Wikidata SPARQL for
Europe/Nordic, yfinance for per-ticker enrichment). Later milestones add the evidence-harvest clients
(OpenAlex, ClinicalTrials, PatentsView, EDGAR full-text — spec §6).

All network calls are fail-open, rate-limited, size-capped (64 MiB), and send the repo `USER_AGENT`.
Endpoints are hard-coded public APIs (no user-supplied URLs ⇒ no SSRF surface).
"""
