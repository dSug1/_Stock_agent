"""Re-score gating hashes (6_Biotech D11/D19 pattern, reused).

A score is skippable only when both the **config** (scoring-relevant sections + prompt version) and the
**evidence** (the per-ticker dimension rows) are unchanged and within TTL. A retune re-opens every ticker;
new evidence re-opens just that ticker. Both stamped on every `scores` row so a prediction is always
attributable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def config_hash(cfg: dict, prompt_version: str) -> str:
    """Hash of the sections that change a score: target/blend/harvest/sources + Claude models + prompt."""
    cl = cfg.get("claude", {})
    keys = {
        "probability": cfg.get("probability"),
        "blend": cfg.get("blend"),
        "harvest": cfg.get("harvest"),
        "sources": cfg.get("sources"),
        "claude": {k: cl.get(k) for k in (
            "triage_model", "rubric_model", "finalize_model", "contested_band",
            "web_search_variant", "max_output_tokens", "max_searches_per_company")},
        "prompt_version": prompt_version,
    }
    return _h(keys)


def evidence_fingerprint(evidence_rows: Iterable) -> str:
    """Hash of the (dimension, score, features) triples — new harvest output re-opens the ticker."""
    items = sorted((r["dimension"], r["score"], r["features_json"]) for r in evidence_rows)
    return _h(items)
