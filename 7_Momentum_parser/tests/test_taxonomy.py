"""Top-down signal taxonomy — the shipped file is valid + the validator rejects malformed ones (M11, §4b)."""

import pytest

from momentum_parser.taxonomy import (CLASSES, SCHEDULES, SCOPES, load_taxonomy,
                                      regime_buckets)


def _write(tmp_path, body: str):
    p = tmp_path / "tax.yaml"
    p.write_text(body, encoding="utf-8")
    return {"topdown": {"taxonomy_file": str(p)}}


def test_shipped_taxonomy_loads_and_is_valid():
    tax = load_taxonomy()                                  # default path = config/signals_taxonomy.yaml
    sigs = tax["signals"]
    assert len(sigs) >= 18                                 # the §4b.2 table
    ids = [s.id for s in sigs]
    assert len(ids) == len(set(ids))                       # unique ids
    for s in sigs:
        assert s.cls in CLASSES and s.scope in SCOPES and s.schedule in SCHEDULES
        assert 0.0 <= s.w_prior <= 1.0
    # each taxonomy class is represented (broad coverage, not just macro)
    assert {s.cls for s in sigs} == CLASSES


def test_regime_buckets_include_all_plus_regimes():
    tax = load_taxonomy()
    buckets = regime_buckets(tax)
    assert buckets[0] == "all"                             # the always-present fallback
    assert "risk_on" in buckets and "risk_off" in buckets


def test_rejects_duplicate_id(tmp_path):
    cfg = _write(tmp_path, """
version: 1
signals:
  - {id: a, class: monetary, scope: market, schedule: dated, w_prior: 0.1}
  - {id: a, class: rotation, scope: factor, schedule: continuous, w_prior: 0.1}
""")
    with pytest.raises(ValueError, match="duplicate"):
        load_taxonomy(cfg)


def test_rejects_bad_enum(tmp_path):
    cfg = _write(tmp_path, """
version: 1
signals:
  - {id: a, class: NONSENSE, scope: market, schedule: dated, w_prior: 0.1}
""")
    with pytest.raises(ValueError, match="bad class"):
        load_taxonomy(cfg)


def test_rejects_weight_out_of_range(tmp_path):
    cfg = _write(tmp_path, """
version: 1
signals:
  - {id: a, class: monetary, scope: market, schedule: dated, w_prior: 1.9}
""")
    with pytest.raises(ValueError, match="w_prior"):
        load_taxonomy(cfg)


def test_rejects_empty(tmp_path):
    cfg = _write(tmp_path, "version: 1\nsignals: []\n")
    with pytest.raises(ValueError, match="no signals"):
        load_taxonomy(cfg)


def test_rejects_missing_field(tmp_path):
    cfg = _write(tmp_path, """
version: 1
signals:
  - {id: a, class: monetary, scope: market, schedule: dated}
""")
    with pytest.raises(ValueError, match="w_prior"):
        load_taxonomy(cfg)
