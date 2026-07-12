"""M20 — foreign (ISIN-keyed) cap enrichment offline tests (injected OpenFIGI + yfinance; no network)."""

from __future__ import annotations

import pytest

from early_detection.clients import openfigi
from early_detection.config import Config
from early_detection.enrich import enrich_caps_isin
from early_detection.models import Entity
from early_detection.store import Store


# ── OpenFIGI ISIN → yfinance symbol ──────────────────────────────────────────────
def test_yf_symbol_picks_primary_venue_and_maps_suffix():
    recs = [{"ticker": "ZEAL", "exchCode": "DC"},        # Copenhagen (primary) → .CO
            {"ticker": "22Z", "exchCode": "GR"}]          # a German cross-listing
    sym = openfigi.yf_symbol_for_isin("DK0060257814", map_fn=lambda isin, **kw: recs)
    assert sym == "ZEAL.CO"


def test_yf_symbol_skips_composite_mtf_codes():
    # first records are pan-European MTF composites (not real exchanges) → skipped; BB is the real venue
    recs = [{"ticker": "ARGXEUR", "exchCode": "EO"}, {"ticker": "ARGXEUR", "exchCode": "XH"},
            {"ticker": "ARGX", "exchCode": "BB"}]
    assert openfigi.yf_symbol_for_isin("NL0010832176", map_fn=lambda isin, **kw: recs) == "ARGX.BR"


def test_yf_symbol_none_when_unmappable():
    assert openfigi.yf_symbol_for_isin("XX", map_fn=lambda isin, **kw: [{"ticker": "Z", "exchCode": "ZZ"}]) is None
    assert openfigi.yf_symbol_for_isin("XX", map_fn=lambda isin, **kw: []) is None


# ── enrich stage ─────────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "e.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "e.db", mktcap_floor_usd=10_000_000, mktcap_ceiling_usd=3_000_000_000)


def _seed(store):
    store.upsert_entity(Entity(entity_id="wd:1", legal_name="Small Bio", isin="SE0000000001",
                               jurisdiction="SE", mktcap_unknown=True))       # small → active
    store.upsert_entity(Entity(entity_id="wd:2", legal_name="Mega Pharma", isin="DK0000000002",
                               jurisdiction="DK", mktcap_unknown=True))       # mega → above ceiling
    store.upsert_entity(Entity(entity_id="wd:3", legal_name="No Symbol Co", isin="FR0000000003",
                               jurisdiction="FR", mktcap_unknown=True))       # unresolved → kept unknown


def test_enrich_isin_fills_flags_and_excludes_megacap(store, cfg):
    _seed(store)
    syms = {"SE0000000001": "SMALL.ST", "DK0000000002": "MEGA.CO"}            # FR → no symbol
    caps = {"SMALL.ST": {"mktcap_native": 5_000_000_000, "currency": "SEK"},  # 5B SEK ≈ $0.48B → active
            "MEGA.CO": {"mktcap_native": 30_000_000_000, "currency": "DKK"}}  # 30B DKK ≈ $4.35B → ceiling
    res = enrich_caps_isin(store, cfg, per_sec=0, resolve=lambda isin: syms.get(isin),
                           fetch=lambda sym: caps.get(sym))
    assert res.attempted == 3 and res.filled == 2 and res.misses == 1
    assert res.above_ceiling == 1                                             # MEGA excluded

    small = store.get_entity("wd:1")
    assert small.market_cap_usd and small.below_floor == 0 and small.above_ceiling == 0  # active
    mega = store.get_entity("wd:2")
    assert mega.above_ceiling == 1                                            # dropped from active universe
    nosym = store.get_entity("wd:3")
    assert nosym.market_cap_usd is None and nosym.mktcap_unknown == 1         # kept, still unknown
    assert nosym.enriched_at is not None                                     # stamped → not retried


def test_enrich_isin_worklist_excludes_capped_and_tickerless_us(store, cfg):
    _seed(store)
    store.apply_cap("wd:1", market_cap_usd=1e8, currency="USD", floor_usd=cfg.mktcap_floor_usd,
                    ceiling_usd=cfg.mktcap_ceiling_usd, enriched_at="2026-01-01")   # already capped
    todo = {e.entity_id for e in store.entities_needing_cap_isin()}
    assert "wd:1" not in todo and {"wd:2", "wd:3"} <= todo                    # capped one excluded
