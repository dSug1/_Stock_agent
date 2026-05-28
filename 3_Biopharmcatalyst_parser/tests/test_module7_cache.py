"""Module 7 — catalyst-identity cache unit tests (D17)."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.cache import (  # noqa: E402
    compute_catalyst_signature,
    lookup_cache,
    partition_feed_by_cache,
)
from module_7.deep_dives_db import (  # noqa: E402
    db_connect,
    init_deep_dives_db,
    upsert_deep_dive_row,
)


# ─────────────── signature normalisation ───────────────────────────


def test_signature_includes_all_four_fields():
    sig = compute_catalyst_signature(
        drug="TX45", stage="phase2",
        next_catalyst_type="Topline Data",
        catalyst_date_iso="2026-09-15",
    )
    parts = sig.split("|")
    assert parts == ["tx45", "phase2", "topline data", "2026-09-15"]


def test_signature_lowercases_and_strips():
    """Cosmetic case / whitespace must not invalidate cache."""
    sig_a = compute_catalyst_signature(
        drug="TX45 ", stage="Phase2",
        next_catalyst_type="Topline Data",
        catalyst_date_iso="2026-09-15",
    )
    sig_b = compute_catalyst_signature(
        drug=" tx45", stage="phase2",
        next_catalyst_type="topline data",
        catalyst_date_iso="2026-09-15",
    )
    assert sig_a == sig_b


def test_signature_handles_none_fields():
    sig = compute_catalyst_signature(
        drug="TX45", stage=None,
        next_catalyst_type="Topline Data",
        catalyst_date_iso=None,
    )
    assert sig.split("|") == ["tx45", "", "topline data", ""]


def test_signature_distinguishes_drug_change():
    a = compute_catalyst_signature(
        drug="TX45", stage="phase2",
        next_catalyst_type="Topline Data", catalyst_date_iso="2026-09-15",
    )
    b = compute_catalyst_signature(
        drug="TX46", stage="phase2",
        next_catalyst_type="Topline Data", catalyst_date_iso="2026-09-15",
    )
    assert a != b


def test_signature_distinguishes_stage_change():
    a = compute_catalyst_signature(
        drug="TX45", stage="phase2",
        next_catalyst_type="Topline Data", catalyst_date_iso="2026-09-15",
    )
    b = compute_catalyst_signature(
        drug="TX45", stage="phase3",
        next_catalyst_type="Topline Data", catalyst_date_iso="2026-09-15",
    )
    assert a != b


def test_signature_distinguishes_catalyst_type_change():
    a = compute_catalyst_signature(
        drug="TX45", stage="phase2",
        next_catalyst_type="Topline Data", catalyst_date_iso="2026-09-15",
    )
    b = compute_catalyst_signature(
        drug="TX45", stage="phase2",
        next_catalyst_type="Interim Data", catalyst_date_iso="2026-09-15",
    )
    assert a != b


def test_signature_distinguishes_date_change():
    a = compute_catalyst_signature(
        drug="TX45", stage="phase2",
        next_catalyst_type="Topline Data", catalyst_date_iso="2026-09-15",
    )
    b = compute_catalyst_signature(
        drug="TX45", stage="phase2",
        next_catalyst_type="Topline Data", catalyst_date_iso="2026-10-15",
    )
    assert a != b


# ───────────────────────── lookup_cache ────────────────────────────


def _make_pk(**over):
    base = dict(snapshot_date="2026-05-28", ticker="TCRX",
                drug="TX45", nct_number="NCT06234567",
                next_catalyst_type="Topline Data")
    base.update(over)
    return base


def _write_prior(conn, *, signature, prompt_version="m7-v1:abc1234",
                 p_clinical=0.40, run_id=1, **pk_over):
    row = {
        **_make_pk(**pk_over),
        "run_id": run_id,
        "p_clinical": p_clinical,
        "prompt_version": prompt_version,
        "model": "claude-opus-4-7",
        "raw_text": "raw",
        "catalyst_signature": signature,
    }
    upsert_deep_dive_row(conn, row)


def test_lookup_no_prior_row_is_miss(tmp_path: Path):
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        res = lookup_cache(
            cx, ticker="TCRX", drug="TX45", nct_number="NCT06234567",
            next_catalyst_type="Topline Data",
            current_signature="tx45|phase2|topline data|2026-09-15",
            current_prompt_version="m7-v1:abc1234",
        )
    assert res.is_hit is False
    assert res.reason == "no_prior_row"


def test_lookup_identity_match_is_hit(tmp_path: Path):
    init_deep_dives_db(tmp_path / "dd.db")
    sig = "tx45|phase2|topline data|2026-09-15"
    with db_connect(tmp_path / "dd.db") as cx:
        _write_prior(cx, signature=sig)
        res = lookup_cache(
            cx, ticker="TCRX", drug="TX45", nct_number="NCT06234567",
            next_catalyst_type="Topline Data",
            current_signature=sig,
            current_prompt_version="m7-v1:abc1234",
        )
    assert res.is_hit is True
    assert res.reason == "identity_match"
    assert res.prior_run_id == 1


def test_lookup_identity_changed_is_miss(tmp_path: Path):
    """Catalyst date moved from 2026-09-15 to 2026-10-15 → miss."""
    init_deep_dives_db(tmp_path / "dd.db")
    old_sig = "tx45|phase2|topline data|2026-09-15"
    new_sig = "tx45|phase2|topline data|2026-10-15"
    with db_connect(tmp_path / "dd.db") as cx:
        _write_prior(cx, signature=old_sig)
        res = lookup_cache(
            cx, ticker="TCRX", drug="TX45", nct_number="NCT06234567",
            next_catalyst_type="Topline Data",
            current_signature=new_sig,
            current_prompt_version="m7-v1:abc1234",
        )
    assert res.is_hit is False
    assert res.reason == "identity_changed"


def test_lookup_stage_changed_is_miss(tmp_path: Path):
    """phase2 → phase3 → miss."""
    init_deep_dives_db(tmp_path / "dd.db")
    old_sig = "tx45|phase2|topline data|2026-09-15"
    new_sig = "tx45|phase3|topline data|2026-09-15"
    with db_connect(tmp_path / "dd.db") as cx:
        _write_prior(cx, signature=old_sig)
        res = lookup_cache(
            cx, ticker="TCRX", drug="TX45", nct_number="NCT06234567",
            next_catalyst_type="Topline Data",
            current_signature=new_sig,
            current_prompt_version="m7-v1:abc1234",
        )
    assert res.is_hit is False
    assert res.reason == "identity_changed"


def test_lookup_prompt_version_changed_is_miss(tmp_path: Path):
    """YAML edit → SHA-7 changed → cache wholesale invalidated."""
    init_deep_dives_db(tmp_path / "dd.db")
    sig = "tx45|phase2|topline data|2026-09-15"
    with db_connect(tmp_path / "dd.db") as cx:
        _write_prior(cx, signature=sig, prompt_version="m7-v1:aaaaaaa")
        res = lookup_cache(
            cx, ticker="TCRX", drug="TX45", nct_number="NCT06234567",
            next_catalyst_type="Topline Data",
            current_signature=sig,
            current_prompt_version="m7-v1:bbbbbbb",
        )
    assert res.is_hit is False
    assert res.reason == "prompt_version_changed"


def test_lookup_prior_row_failed_is_miss(tmp_path: Path):
    """Error rows (p_clinical IS NULL) must not grant a cache hit."""
    init_deep_dives_db(tmp_path / "dd.db")
    sig = "tx45|phase2|topline data|2026-09-15"
    with db_connect(tmp_path / "dd.db") as cx:
        _write_prior(cx, signature=sig, p_clinical=None)
        res = lookup_cache(
            cx, ticker="TCRX", drug="TX45", nct_number="NCT06234567",
            next_catalyst_type="Topline Data",
            current_signature=sig,
            current_prompt_version="m7-v1:abc1234",
        )
    assert res.is_hit is False
    assert res.reason == "prior_row_failed"


def test_lookup_picks_latest_run_id(tmp_path: Path):
    """When two prior rows exist for the same PK, the newest run_id wins.

    Realistic scenario: run #1 hit a parse error (p_clinical=NULL); run #2
    succeeded; we now consider whether run #3 should call the API.
    """
    init_deep_dives_db(tmp_path / "dd.db")
    sig = "tx45|phase2|topline data|2026-09-15"
    with db_connect(tmp_path / "dd.db") as cx:
        _write_prior(cx, signature=sig, p_clinical=None, run_id=1)
        _write_prior(cx, signature=sig, p_clinical=0.42, run_id=2)
        res = lookup_cache(
            cx, ticker="TCRX", drug="TX45", nct_number="NCT06234567",
            next_catalyst_type="Topline Data",
            current_signature=sig,
            current_prompt_version="m7-v1:abc1234",
        )
    assert res.is_hit is True
    assert res.prior_run_id == 2


# ───────────────────── partition_feed_by_cache ─────────────────────


def _cand(ticker, drug, stage, catalyst_date, **over):
    base = dict(ticker=ticker, drug=drug, nct_number=f"NCT{ticker}",
                next_catalyst_type="Topline Data",
                stage=stage, catalyst_date_iso=catalyst_date)
    base.update(over)
    return base


def test_partition_force_refresh_dispatches_everything(tmp_path: Path):
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        # Pre-populate so we'd otherwise have a cache hit.
        sig = "tx45|phase2|topline data|2026-09-15"
        _write_prior(cx, signature=sig, run_id=1)
        cands = [_cand("TCRX", "TX45", "phase2", "2026-09-15")]
        to_dispatch, skipped = partition_feed_by_cache(
            cx, cands, current_prompt_version="m7-v1:abc1234",
            force_refresh=True,
        )
    assert len(to_dispatch) == 1
    assert len(skipped) == 0
    assert to_dispatch[0]["cache_lookup"].reason == "force_refresh"


def test_partition_mixed_hits_and_misses(tmp_path: Path):
    """Three candidates: one identity-match, one date-shifted, one new."""
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        # Prior for TCRX TX45 phase2 2026-09-15 — will identity-match cand 0
        _write_prior(cx, ticker="TCRX", drug="TX45", nct_number="NCTTCRX",
                     signature="tx45|phase2|topline data|2026-09-15",
                     run_id=1)
        # Prior for RCKT RP-A501 phase1 2026-07-01 — cand 1 shifts date
        _write_prior(cx, ticker="RCKT", drug="RP-A501", nct_number="NCTRCKT",
                     signature="rp-a501|phase1|topline data|2026-07-01",
                     run_id=2)
        # No prior for SVRA — cand 2 is fresh

        cands = [
            _cand("TCRX", "TX45",    "phase2", "2026-09-15"),    # match
            _cand("RCKT", "RP-A501", "phase1", "2026-08-15"),    # date shift
            _cand("SVRA", "STK-001", "phase2", "2026-10-01"),    # fresh
        ]
        to_dispatch, skipped = partition_feed_by_cache(
            cx, cands, current_prompt_version="m7-v1:abc1234",
        )

    assert len(skipped) == 1
    assert skipped[0]["ticker"] == "TCRX"
    assert skipped[0]["cache_lookup"].reason == "identity_match"

    assert len(to_dispatch) == 2
    by_t = {c["ticker"]: c for c in to_dispatch}
    assert by_t["RCKT"]["cache_lookup"].reason == "identity_changed"
    assert by_t["SVRA"]["cache_lookup"].reason == "no_prior_row"


def test_partition_writes_signature_on_each_candidate(tmp_path: Path):
    init_deep_dives_db(tmp_path / "dd.db")
    with db_connect(tmp_path / "dd.db") as cx:
        cands = [_cand("TCRX", "TX45", "phase2", "2026-09-15")]
        to_dispatch, _ = partition_feed_by_cache(
            cx, cands, current_prompt_version="m7-v1:abc1234",
        )
    assert to_dispatch[0]["catalyst_signature"] == "tx45|phase2|topline data|2026-09-15"
