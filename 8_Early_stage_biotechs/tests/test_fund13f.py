"""D23 — 13F-holdings seed provider offline tests (temp holdings DB; injected EDGAR lookup; no network)."""

from __future__ import annotations

import sqlite3

from early_detection.providers import fund13f


def _make_db(tmp_path, rows):
    """rows: (fund_id, period, ticker, name, put_call). Minimal 2_fundparser-shaped holdings DB."""
    p = tmp_path / "2_fundparser.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE holdings (fund_id INT, period_of_report TEXT, ticker TEXT, "
                "name_of_issuer TEXT, put_call TEXT)")
    con.executemany("INSERT INTO holdings VALUES (?,?,?,?,?)", rows)
    con.commit(); con.close()
    return p


# injected EDGAR getcompany lookup: ticker -> (cik, sic, name)
_LOOKUP = {
    "APLS": (1492422, "2834", "Apellis Pharmaceuticals, Inc."),   # therapeutics
    "CELC": (1603454, "8071", "Celcuity Inc."),                   # medical labs → diagnostics (biotech-adjacent)
    "PNW": (764622, "4911", "Pinnacle West Capital Corp"),        # utility → excluded
    "XRAY": (818479, "3841", "Dentsply Sirona"),                  # device SIC → admitted
}


def test_admits_biotech_span_by_sic_excludes_nonbiotech(tmp_path):
    db = _make_db(tmp_path, [
        (1, "2026-03-31", "APLS", "APELLIS PHARMACEUTICALS INC", None),
        (1, "2026-03-31", "CELC", "Celcuity Inc.", None),      # SIC 8071 → diagnostics (still admitted)
        (2, "2026-03-31", "PNW", "PINNACLE WEST CAP CORP", None),   # utility 4911 → dropped
        (2, "2026-03-31", "XRAY", "DENTSPLY", None),           # device 3841 → admitted
        (1, "2026-03-31", "ZZZZ", "UNKNOWN TICKER", None),     # unresolved → skipped
    ])
    out = fund13f.load_fund13f_listings(db, lookup=lambda tk: _LOOKUP.get(tk))
    got = {l.ticker: (l.cik, l.sector_normalized) for l in out}
    assert set(got) == {"APLS", "CELC", "XRAY"}                # PNW (utility) + ZZZZ (unresolved) excluded
    assert got["APLS"] == ("0001492422", "therapeutics")
    assert got["CELC"][1] == "diagnostics"                     # keyword-less biotech admitted via broadened SIC
    assert got["XRAY"][1] == "devices"
    assert all(l.provenance == ["fund13f"] and l.country == "US" for l in out)


def test_excludes_broad_filers(tmp_path):
    rows = [(9, "2026-03-31", t, f"{t} INC", None) for t in ("APLS", "CELC", "XRAY")]   # broad filer (3 > 2)
    rows += [(1, "2026-03-31", "APLS", "APELLIS PHARMACEUTICALS INC", None)]            # specialist keeps APLS
    db = _make_db(tmp_path, rows)
    out = fund13f.load_fund13f_listings(db, lookup=lambda tk: _LOOKUP.get(tk), max_holdings_per_filer=2)
    assert {l.ticker for l in out} == {"APLS"}


def test_uses_latest_period_and_skips_options(tmp_path):
    db = _make_db(tmp_path, [
        (1, "2025-12-31", "APLS", "APELLIS PHARMACEUTICALS INC", None),   # older period → ignored
        (1, "2026-03-31", "CELC", "Celcuity Inc.", None),                 # latest → admitted
        (1, "2026-03-31", "XRAY", "DENTSPLY", "Call"),                    # option → skipped
    ])
    out = fund13f.load_fund13f_listings(db, lookup=lambda tk: _LOOKUP.get(tk))
    assert {l.ticker for l in out} == {"CELC"}


def test_missing_db_fail_soft(tmp_path):
    assert fund13f.load_fund13f_listings(tmp_path / "nope.db", lookup=lambda tk: None) == []
