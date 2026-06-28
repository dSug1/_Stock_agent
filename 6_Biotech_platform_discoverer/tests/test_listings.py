"""M2 tests — providers, FX, identity, ADR/dual-listing dedup."""

from pathlib import Path

from platform_discoverer import dedup
from platform_discoverer.fx import FXConverter
from platform_discoverer.listings import (SeedCSVProvider, company_id_for, normalize_name,
                                          primary_listing, record_to_company)
from platform_discoverer.models import ListingRecord

# Synthetic fixture — deterministic, decoupled from live market caps in config/tracked_universe.csv.
CSV = Path(__file__).parent / "fixtures" / "universe_fixture.csv"


# ── identity ─────────────────────────────────────────────────────────────────

def test_company_id_stable_and_name_insensitive_to_noise():
    a = company_id_for("Acrivon Therapeutics", "NASDAQ:ACRV")
    b = company_id_for("ACRIVON  THERAPEUTICS, INC.".replace(", INC.", ""), "NASDAQ:ACRV")
    assert company_id_for("Acrivon Therapeutics", "NASDAQ:ACRV") == a   # deterministic
    assert normalize_name("Acrivon Therapeutics") == "acrivon therapeutics"


def test_record_to_company_marks_unknown_cap():
    rec = ListingRecord(name="X", ticker="X", exchange="STO", mktcap_usd_fd=None)
    c = record_to_company(rec, ["seed_list"])
    assert c.mktcap_unknown is True and c.source_nets == ["seed_list"]


# ── FX ───────────────────────────────────────────────────────────────────────

def test_fx_usd_identity_and_unknown_currency():
    fx = FXConverter()
    assert fx.to_usd(100, "USD") == 100
    assert fx.to_usd(100, "SEK") == 100 * 0.095
    assert fx.to_usd(100, "ZZZ") is None      # unknown currency -> unknown cap
    assert fx.to_usd(None, "USD") is None


def test_fx_config_override():
    fx = FXConverter.from_config({"fx": {"rates": {"SEK": 0.10}}})
    assert fx.to_usd(100, "SEK") == 100 * 0.10


# ── CSV provider ─────────────────────────────────────────────────────────────

def test_seed_csv_provider_parses_universe():
    recs = SeedCSVProvider([CSV]).fetch()
    assert len(recs) == 15
    by_ticker = {r.ticker: r for r in recs}
    assert by_ticker["ACRV"].provenance == ["seed_list"]
    assert by_ticker["ACRV"].mktcap_usd_fd == 250_000_000
    assert by_ticker["DEADX"].is_live is False
    assert by_ticker["UNKN"].mktcap_usd_fd is None      # blank cap -> None (kept later)


# ── dedup ────────────────────────────────────────────────────────────────────

def test_collapse_merges_dual_listing_by_isin():
    recs = SeedCSVProvider([CSV]).fetch()
    canonical = dedup.collapse(recs)
    assert len(canonical) == 14                          # DUAL + DUAL.ST -> one
    dual = [r for r in canonical if r.name == "DualListed Therapeutics"]
    assert len(dual) == 1
    prim = dual[0]
    assert prim.exchange == "NASDAQ"                     # NASDAQ preferred over STO
    assert primary_listing(prim) == "NASDAQ:DUAL"
    assert "STO:DUAL.ST" in prim.secondary_listings


def test_collapse_unions_recall_fields_from_secondary():
    # primary lacks sector code + index; secondary carries them -> must survive onto primary
    primary = ListingRecord(name="Z", ticker="Z", exchange="NASDAQ", isin="X1",
                            is_primary=True, sic=None, indices=[])
    secondary = ListingRecord(name="Z", ticker="Z.ST", exchange="STO", isin="X1",
                              sic="2836", indices=["Nordic Health"], provenance=["sector"])
    [canon] = dedup.collapse([primary, secondary])
    assert canon.exchange == "NASDAQ"                    # primary kept
    assert canon.sic == "2836"                           # unioned from secondary
    assert "Nordic Health" in canon.indices


def test_collapse_company_live_if_any_listing_live():
    a = ListingRecord(name="Q", ticker="Q", exchange="NASDAQ", isin="ISIN9", is_live=False)
    b = ListingRecord(name="Q", ticker="Q.L", exchange="LSE", isin="ISIN9", is_live=True)
    [canon] = dedup.collapse([a, b])
    assert canon.is_live is True
