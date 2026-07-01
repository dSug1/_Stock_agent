"""M18 — outputs polish: signals.md catalyst/variant columns + HTML regime banner + ledger panel. Offline."""

import json

from momentum_parser import render, stage4_export
from momentum_parser.models import Prediction
from momentum_parser.store import Store


def _pred(s, t, run_id, asof, p_up=0.55, p_claude=0.5, p_model=0.6, d=0.1):
    s.write_prediction(Prediction(ticker=t, asof_date=asof, horizon_days=5, p_up=p_up,
                                  expected_return=0.01, confidence=0.2, composite=0.3, signals=[],
                                  last_close=100.0, p_claude=p_claude, p_model=p_model,
                                  disagreement=d, review=False), run_id)


def test_signals_md_has_catalyst_and_variant_columns(tmp_path):
    s = Store(tmp_path / "t.db")
    _pred(s, "AAA", "run1", "2026-07-01")
    s.upsert_catalysts("AAA", [("2026-07-06", "earnings", "Q2", "test")])
    s.write_score("AAA", "2026-07-01", "run1", tier="rubric", p_up=0.5, conviction=0.3,
                  variant_json=json.dumps({"our_view": "hawkish CPI beat likely, rates underpriced"}))
    out = tmp_path / "signals.md"
    stage4_export.run(s, {}, "run1", out=str(out), log=lambda m: None)
    text = out.read_text(encoding="utf-8")
    assert "Days→cat" in text and "Our view" in text                 # new columns
    assert "| 5 |" in text                                           # 2026-07-01 -> 2026-07-06 = 5 days
    assert "hawkish CPI beat likely" in text                         # the variant thesis surfaced


def test_report_regime_and_ledger_panels(tmp_path):
    s = Store(tmp_path / "t.db")
    _pred(s, "AAA", "run1", "2026-07-01")
    cfg = {"outputs": {"dir": str(tmp_path)}, "validation": {"min_samples": 100}}

    doc = render.render(s, cfg, "run1").read_text(encoding="utf-8")
    assert "no settled outcomes yet" in doc                          # graceful when the ledger is empty

    s.upsert_macro_signal("2026-07-01", "risk_regime", active=True, surprise=0.4, regime="risk_on")
    s.append_ledger("AAA", "2026-06-01", "run0", 5, p_up=0.8, p_final=0.8, predicted_label="up", sigma_week=0.05)
    s.settle_ledger("AAA", "2026-06-01", "run0", realized_return=0.05, realized_label="up", scored_at="t")
    doc2 = render.render(s, cfg, "run1").read_text(encoding="utf-8")
    assert "Regime:" in doc2 and "risk-on" in doc2                   # regime banner from the harvest
    assert "Forward ledger" in doc2 and "Brier" in doc2              # live-ledger metrics panel
    assert "UNDERPOWERED" in doc2                                    # n=1 < 100
