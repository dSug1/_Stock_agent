"""Specialist-fund cross-reference — smart-money confirmation on a theme's *listed* names (spec §4b).

A specialist fund taking a **new** 13F position in a theme constituent while the theme is still early
is a *capital jury* — an independent expert vote, in money. This module reads the curated universe
(``config/specialist_funds.yaml``: per-sector specialist/crossover 13F filers + thematic-ETF
fallbacks, D25/D26) and crosses {specialist new buys ∪ ETF additions} against a theme's Track-A
tickers (from ``resolve.track_a_tickers``).

The new-position *data* is sourced upstream: biotech reuses ``2_Funds_parser``'s already-fetched
13F holdings/new_positions (D3); other sectors use EDGAR 13F for the listed CIKs here, or ETF
holdings deltas where no specialist files (most of semis/cyber/fintech/energy/space/materials). That
sourcing is wired later; the scorer below is pure so it can be tested + driven by any provider.
"""

import logging
import sqlite3

log = logging.getLogger(__name__)


def load_specialist_funds(path: str) -> dict:
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _sectors(cfg: dict) -> dict:
    """Sector -> {funds:[...], etf_fallback:[...]}, skipping the YAML comment scalars."""
    return {k: v for k, v in cfg.items() if isinstance(v, dict)}


def fund_ciks(cfg: dict) -> dict:
    """{CIK: {name, type, sector}} for every fund with a (non-blank) CIK — the EDGAR 13F worklist."""
    out = {}
    for sector, block in _sectors(cfg).items():
        for f in block.get("funds", []) or []:
            cik = (f.get("cik") or "").strip()
            if cik:
                out[cik] = {"name": f.get("name"), "type": f.get("type"), "sector": sector}
    return out


def sector_etfs(cfg: dict) -> dict:
    """{sector: [ETF tickers]} — the holdings-delta fallback where no specialist 13F exists."""
    return {s: list(b.get("etf_fallback", []) or []) for s, b in _sectors(cfg).items()}


def cik_type_map(cfg: dict) -> dict:
    """{10-digit CIK: fund type} from specialist_funds.yaml — used to tag funds read from another DB."""
    out = {}
    for block in _sectors(cfg).values():
        for f in block.get("funds", []) or []:
            cik = (f.get("cik") or "").strip()
            if cik:
                out[cik.zfill(10)] = f.get("type") or "specialist"
    return out


# ─── real new-position provider: reuse 2_Funds_parser's 13F holdings (D3/D25) ─

def _latest_per_fund_positions(conn: sqlite3.Connection, period: str) -> dict:
    """{(fund_id, TICKER): (fund_name, cik)} held in `period`, using each fund's LATEST filing for that
    period (matches module_3.bridge.load_holdings_for_period) and net shares > 0. Stdlib SQL — we reuse
    2_Funds' signal output, not its pandas code (the 5_Hype standalone rule, D3)."""
    sql = """
        SELECT h.fund_id, f.name AS fund_name, f.cik AS cik, UPPER(h.ticker) AS ticker,
               SUM(h.shares) AS shares
          FROM holdings h
          JOIN (SELECT fund_id, MAX(filing_date) AS latest FROM holdings
                 WHERE period_of_report = ? GROUP BY fund_id) l
            ON h.fund_id = l.fund_id AND h.filing_date = l.latest
          JOIN funds f ON f.id = h.fund_id
         WHERE h.period_of_report = ? AND h.ticker IS NOT NULL AND h.ticker <> ''
         GROUP BY h.fund_id, UPPER(h.ticker)
        HAVING SUM(h.shares) > 0
    """
    out = {}
    for r in conn.execute(sql, (period, period)):
        out[(r["fund_id"], r["ticker"])] = (r["fund_name"], r["cik"])
    return out


def new_buys_from_2funds(db_path: str, cfg: dict | None = None, *, quarter: str | None = None) -> dict:
    """Specialist-fund NEW 13F positions from the 2_Funds_parser holdings DB. A (fund, ticker) is NEW
    in a quarter if the fund holds it then but did NOT the prior quarter (module_3's `is_new`). Returns
    {TICKER: [{name, type}, ...]}. `cfg` (specialist_funds.yaml) tags each fund's type by CIK; default
    'specialist' (the 2_Funds set is all biotech specialists). Fail-open to {} if the DB is unreadable."""
    types = cik_type_map(cfg) if cfg else {}
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period_of_report FROM holdings WHERE period_of_report IS NOT NULL "
            "ORDER BY period_of_report DESC")]
        if not periods:
            return {}
        cur_q = quarter or periods[0]
        prior = next((p for p in periods if p < cur_q), None)
        cur = _latest_per_fund_positions(conn, cur_q)
        pri = _latest_per_fund_positions(conn, prior) if prior else {}
        out: dict = {}
        for (fund_id, ticker), (fund_name, cik) in cur.items():
            if (fund_id, ticker) in pri:                       # held last quarter -> not new
                continue
            ftype = types.get((cik or "").zfill(10), "specialist")
            out.setdefault(ticker, []).append({"name": fund_name, "type": ftype})
        log.info("2_Funds new buys: quarter=%s prior=%s -> %d tickers", cur_q, prior, len(out))
        return out
    except sqlite3.Error as exc:                                # missing table / unreadable -> fail-open
        log.warning("2_Funds new-buys read failed (%s): %s", db_path, exc)
        return {}
    finally:
        if conn is not None:
            conn.close()


def cross_reference(theme_tickers, new_buys: dict, cfg_weights: dict) -> list[dict]:
    """Confirmation signals for one theme.

    ``theme_tickers``: the theme's Track-A tickers (iterable).
    ``new_buys``: {TICKER: [{name, type}, ...]} — funds/ETFs that just opened a position in that name.
    Returns, per intersecting ticker, a weighted smart-money score (pure specialists > crossover/ETF).
    """
    spec_w = cfg_weights.get("specialist_weight", 1.0)
    cross_w = cfg_weights.get("crossover_weight", 0.5)
    wanted = {t.upper() for t in theme_tickers}
    out = []
    for tk, buyers in new_buys.items():
        u = tk.upper()
        if u not in wanted:
            continue
        score = 0.0
        for b in buyers:
            score += spec_w if b.get("type") == "specialist" else cross_w
        out.append({
            "ticker": u,
            "n_buyers": len(buyers),
            "buyers": [b.get("name") for b in buyers],
            "smart_money_score": round(score, 3),
        })
    out.sort(key=lambda r: (-r["smart_money_score"], r["ticker"]))
    return out
