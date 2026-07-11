"""Client-level offline tests (no network). Guards the Batch custom_id encoding — a real bug: our
entity_ids carry ':' and '|', which the Batch API rejects (custom_id must be ^[a-zA-Z0-9_-]{1,64}$).
This went unnoticed until Batch became the project default because prior runs used --realtime."""

from __future__ import annotations

import re

import pytest

from early_detection.clients.anthropic_client import _dec_cid, _enc_cid

_CUSTOM_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@pytest.mark.parametrize("entity_id", [
    "cik:0001421642",
    "isin:US00489L1098",
    "tkx:MSLE|NASDAQ",             # ticker+exchange form carries BOTH ':' and '|'
    "lei:5493001KJTIIGC8Y1R12",
    "tkx:BRK.B|NYSE",             # a dot too
])
def test_custom_id_roundtrip_and_charset(entity_id):
    cid = _enc_cid(entity_id)
    assert _CUSTOM_ID.match(cid), f"{cid!r} violates the batch custom_id pattern"
    assert _dec_cid(cid) == entity_id


def test_custom_id_rejects_overlong_id():
    with pytest.raises(ValueError):
        _enc_cid("x" * 60)         # base64url of 60 chars = 80 > 64 → must fail loudly, not truncate
