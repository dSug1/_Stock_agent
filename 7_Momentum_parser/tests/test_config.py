"""Config + universe load."""

from momentum_parser.config import load_config
from momentum_parser.universe import load_universe


def test_config_loads_with_expected_sections():
    cfg = load_config()
    for section in ("signals", "probability", "prices", "store", "export"):
        assert section in cfg, f"missing config section: {section}"
    assert cfg["probability"]["horizon_days"] == 5


def test_universe_loads_seed_csv():
    cfg = load_config()
    rows = load_universe(cfg)
    tickers = {r.ticker for r in rows}
    assert "AAPL" in tickers
    assert all(t == t.upper() for t in tickers)
