"""Module 7 — LLM response parsing + m7-v1 schema validation.

Pure compute. Two entry points:

    extract_json_block(raw_text)  -> dict      | raises ParseError
    parse_deep_dive(raw_text)     -> ParsedDeepDive | raises ParseError

`extract_json_block` is copy-adapted from `2_Funds_parser/src/module_6/
parsing.py` — it recovers truncated responses by brace-balancing and
quoting fixes so a `stop_reason='max_tokens'` truncation doesn't lose
the entire reply.

The 10 HARD RULES from spec §5.5 are enforced here. Failures raise
`ParseError(kind, detail)` with structured `kind` so callers can write
the right row into `deep_dive_errors`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional


# ───────────────────────── ParseError ──────────────────────────


class ParseError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"[{kind}] {detail}")
        self.kind = kind          # 'json_parse_fail' | 'schema_violation' | 'catalyst_already_passed'
        self.detail = detail


# ─────────────────────── JSON-fence extractor ──────────────────


_FENCE_OPEN_RE = re.compile(r"```(?:json)?\s*\n", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"\n```")


def _balance_truncated_json(text: str) -> str:
    """Heuristic recovery for a truncated JSON response.

    Walks the text tracking brace/bracket depth and string state. At EOF:
      - If still inside an unterminated string, append closing ``"``.
      - Pop the open-brace/bracket stack, appending matching closers.
    """
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    out = text.rstrip()
    if in_string:
        if out.endswith("\\"):
            out = out[:-1]
        out += '"'
    out = out.rstrip()
    if out.endswith(","):
        out = out[:-1]
    while stack:
        ch = stack.pop()
        out += "}" if ch == "{" else "]"
    return out


def extract_json_block(raw_text: str) -> dict:
    """Pull the first ```json``` fenced block (or recover a truncated one)."""
    if not raw_text or not raw_text.strip():
        raise ParseError("json_parse_fail", "raw_text is empty")

    open_match = _FENCE_OPEN_RE.search(raw_text)
    if open_match:
        body_start = open_match.end()
        close_match = _FENCE_CLOSE_RE.search(raw_text, body_start)
        if close_match:
            body = raw_text[body_start:close_match.start()].strip()
        else:
            body = raw_text[body_start:].rstrip()
            recovered = _balance_truncated_json(body)
            if recovered != body:
                body = recovered
    else:
        body = raw_text.strip()

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        try:
            return json.loads(_balance_truncated_json(body))
        except json.JSONDecodeError:
            raise ParseError(
                "json_parse_fail",
                f"json.loads failed at line {e.lineno} col {e.colno}: {e.msg}; "
                f"body length={len(body)}",
            ) from e


# ─────────────────────── primitive validators ──────────────────


def _require(obj: dict, key: str, context: str) -> Any:
    if key not in obj:
        raise ParseError("schema_violation", f"missing '{key}' in {context}")
    return obj[key]


def _require_float_in(obj: dict, key: str, lo: float, hi: float, context: str) -> float:
    v = _require(obj, key, context)
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ParseError("schema_violation",
                         f"'{key}' in {context} must be float, got {type(v).__name__} {v!r}")
    if not (lo <= f <= hi):
        raise ParseError("schema_violation",
                         f"'{key}'={f} in {context} outside [{lo}, {hi}]")
    return f


# ───────────────────────── result dataclass ─────────────────────


@dataclass(frozen=True)
class ParsedDeepDive:
    ticker: str
    reasoning_trace: str
    drug_profile: dict
    clinical_evidence: dict
    rnpv_by_indication: list[dict]
    rnpv_total_usd: float
    rnpv_per_share_usd: float
    lead_indication: str
    catalyst_outcome: dict                       # p_clinical, hi/lo, moves, anchor
    financial_overhang: dict
    management_track_record: dict                # {score, summary}
    acquisition_target: dict                     # {score, rationale}
    thesis_summary: str
    key_risks: list[str]
    catalyst_date_sanity_check: dict
    # D35 — present ONLY for M8 rescue dispatches. Standard M7 responses
    # leave these fields absent in the JSON; the parser tolerates that.
    claude_resolved_catalyst_date: Optional[str] = None    # ISO YYYY-MM-DD
    catalyst_date_source: Optional[str] = None             # short citation


# ─────────────── per-block validators (HARD RULES) ─────────────


_VALID_COMPETITION_LANDSCAPE = frozenset(
    ("first_to_market", "follower", "crowded")
)


def _validate_drug_profile(dp: dict) -> None:
    """HARD RULE #10 — competition_landscape consistency."""
    if not isinstance(dp, dict):
        raise ParseError("schema_violation", "drug_profile must be a dict")
    for k in ("moa", "moa_class_precedent", "differentiation",
              "competition_landscape", "patent_moat", "fda_designations",
              "tam_usd"):
        _require(dp, k, "drug_profile")
    landscape = dp.get("competition_landscape")
    if landscape not in _VALID_COMPETITION_LANDSCAPE:
        raise ParseError(
            "schema_violation",
            f"competition_landscape={landscape!r} not in {sorted(_VALID_COMPETITION_LANDSCAPE)}",
        )
    bar = dp.get("competition_bar_set_by_others")
    if landscape == "first_to_market" and bar not in (None, "", "null"):
        raise ParseError(
            "schema_violation",
            "HARD RULE #10: competition_landscape='first_to_market' requires "
            f"competition_bar_set_by_others to be null (got {bar!r})",
        )
    pm = dp.get("patent_moat")
    if not isinstance(pm, dict):
        raise ParseError("schema_violation", "drug_profile.patent_moat must be a dict")
    if not isinstance(dp.get("fda_designations"), list):
        raise ParseError(
            "schema_violation",
            f"drug_profile.fda_designations must be a list, got {type(dp.get('fda_designations')).__name__}",
        )


def _validate_rnpv_by_indication(rnpv: list, lead: str) -> None:
    """HARD RULES #5, #6, #7."""
    if not isinstance(rnpv, list) or len(rnpv) == 0:
        raise ParseError("schema_violation",
                         "rnpv_by_indication must be a non-empty list (HARD RULE #5)")
    indication_names: list[str] = []
    for i, ind in enumerate(rnpv):
        if not isinstance(ind, dict):
            raise ParseError("schema_violation", f"rnpv_by_indication[{i}] not a dict")
        _require_float_in(ind, "pos_base_rate", 0.0, 1.0,
                          f"rnpv_by_indication[{i}]")
        _require_float_in(ind, "pos_adjusted", 0.0, 1.0,
                          f"rnpv_by_indication[{i}]")
        name = (ind.get("indication") or "").strip()
        if not name:
            raise ParseError("schema_violation",
                             f"rnpv_by_indication[{i}].indication is empty")
        indication_names.append(name.lower())
    lead_lc = (lead or "").strip().lower()
    if not lead_lc:
        raise ParseError("schema_violation", "lead_indication is empty (HARD RULE #7)")
    # Substring match either direction (model often abbreviates).
    match = any(
        lead_lc == n or lead_lc in n or n in lead_lc for n in indication_names
    )
    if not match:
        raise ParseError(
            "schema_violation",
            f"HARD RULE #7: lead_indication '{lead}' not in rnpv_by_indication[].indication: "
            f"{indication_names}",
        )


def _validate_catalyst_outcome(co: dict) -> None:
    """HARD RULES #1, #2, #3, #4."""
    if not isinstance(co, dict):
        raise ParseError("schema_violation", "catalyst_outcome must be a dict")
    p = _require_float_in(co, "p_clinical", 0.10, 0.90, "catalyst_outcome")
    p_lo = _require_float_in(co, "p_clinical_low", 0.0, 1.0, "catalyst_outcome")
    p_hi = _require_float_in(co, "p_clinical_high", 0.0, 1.0, "catalyst_outcome")
    if not (p_lo <= p <= p_hi):
        raise ParseError(
            "schema_violation",
            f"HARD RULE #2: p_clinical_low {p_lo} <= p_clinical {p} <= p_clinical_high {p_hi} violated",
        )
    hit = co.get("expected_move_on_hit_pct")
    miss = co.get("expected_move_on_miss_pct")
    if hit is None or miss is None:
        raise ParseError("schema_violation",
                         "expected_move_on_hit_pct + expected_move_on_miss_pct required")
    try:
        hit_f = float(hit)
        miss_f = float(miss)
    except (TypeError, ValueError):
        raise ParseError("schema_violation",
                         f"move values must be numeric (got hit={hit!r}, miss={miss!r})")
    if hit_f < 0:
        raise ParseError(
            "schema_violation",
            f"HARD RULE #3: expected_move_on_hit_pct must be >= 0, got {hit_f}",
        )
    if miss_f > 0:
        raise ParseError(
            "schema_violation",
            f"HARD RULE #3: expected_move_on_miss_pct must be <= 0, got {miss_f}",
        )
    if hit_f > 400:
        raise ParseError(
            "schema_violation",
            f"HARD RULE #4: expected_move_on_hit_pct {hit_f} > 400 (outlier)",
        )
    if miss_f < -90:
        raise ParseError(
            "schema_violation",
            f"HARD RULE #4: expected_move_on_miss_pct {miss_f} < -90 (outlier)",
        )


def _validate_management_score(mtr: dict) -> None:
    if not isinstance(mtr, dict):
        raise ParseError("schema_violation", "management_track_record must be a dict")
    _require_float_in(mtr, "score", 0.0, 1.0, "management_track_record")
    _require(mtr, "summary", "management_track_record")


def _validate_acquisition_target(at: dict) -> None:
    if not isinstance(at, dict):
        raise ParseError("schema_violation", "acquisition_target must be a dict")
    _require_float_in(at, "score", 0.0, 1.0, "acquisition_target")
    _require(at, "rationale", "acquisition_target")


def _validate_catalyst_sanity(cs: dict) -> None:
    """HARD RULE #8 — catalyst hasn't already passed."""
    if not isinstance(cs, dict):
        raise ParseError("schema_violation", "catalyst_date_sanity_check must be a dict")
    passed = cs.get("catalyst_passed_already")
    if not isinstance(passed, bool):
        raise ParseError(
            "schema_violation",
            f"catalyst_date_sanity_check.catalyst_passed_already must be bool, got {type(passed).__name__}",
        )
    if passed is True:
        raise ParseError(
            "catalyst_already_passed",
            f"HARD RULE #8: model reports catalyst_passed_already=true "
            f"(notes={cs.get('notes', '')!r}). Skipping this ticker.",
        )
    ir_consistent = cs.get("ir_page_consistent")
    if not isinstance(ir_consistent, bool):
        raise ParseError(
            "schema_violation",
            f"catalyst_date_sanity_check.ir_page_consistent must be bool, got {type(ir_consistent).__name__}",
        )


# ───────────────────────── public parser ────────────────────────


def parse_deep_dive(raw_text: str) -> ParsedDeepDive:
    """Parse + validate one m7-v1 response. Raises ParseError on violation."""
    obj = extract_json_block(raw_text)

    ticker = _require(obj, "ticker", "root")
    if not isinstance(ticker, str) or not ticker.strip():
        raise ParseError("schema_violation", "ticker must be non-empty string")

    reasoning = obj.get("reasoning_trace", "") or ""

    drug_profile = _require(obj, "drug_profile", "root")
    _validate_drug_profile(drug_profile)

    clinical_evidence = _require(obj, "clinical_evidence", "root")
    if not isinstance(clinical_evidence, dict):
        raise ParseError("schema_violation", "clinical_evidence must be a dict")

    rnpv = _require(obj, "rnpv_by_indication", "root")
    rnpv_total = _require(obj, "rnpv_total_usd", "root")
    rnpv_per_share = _require(obj, "rnpv_per_share_usd", "root")
    lead = _require(obj, "lead_indication", "root")
    try:
        float(rnpv_total)
        float(rnpv_per_share)
    except (TypeError, ValueError):
        raise ParseError("schema_violation",
                         f"rnpv_total_usd / rnpv_per_share_usd must be numeric "
                         f"(got {rnpv_total!r}, {rnpv_per_share!r})")
    _validate_rnpv_by_indication(rnpv, lead)

    catalyst_outcome = _require(obj, "catalyst_outcome", "root")
    _validate_catalyst_outcome(catalyst_outcome)

    fin_overhang = _require(obj, "financial_overhang", "root")
    if not isinstance(fin_overhang, dict):
        raise ParseError("schema_violation", "financial_overhang must be a dict")

    mtr = _require(obj, "management_track_record", "root")
    _validate_management_score(mtr)

    acq = _require(obj, "acquisition_target", "root")
    _validate_acquisition_target(acq)

    thesis = _require(obj, "thesis_summary", "root")
    if not isinstance(thesis, str) or not thesis.strip():
        raise ParseError("schema_violation", "thesis_summary must be non-empty string")

    key_risks = _require(obj, "key_risks", "root")
    if not isinstance(key_risks, list):
        raise ParseError("schema_violation",
                         f"key_risks must be a list, got {type(key_risks).__name__}")

    sanity = _require(obj, "catalyst_date_sanity_check", "root")
    _validate_catalyst_sanity(sanity)             # may raise catalyst_already_passed

    # D35 — optional M8 rescue fields. Standard M7 responses omit these.
    # We accept either string or None / missing; light validation only.
    resolved_date = obj.get("claude_resolved_catalyst_date")
    date_source = obj.get("catalyst_date_source")
    if resolved_date is not None and not isinstance(resolved_date, str):
        raise ParseError(
            "schema_violation",
            f"claude_resolved_catalyst_date must be a string, got {type(resolved_date).__name__}",
        )
    if date_source is not None and not isinstance(date_source, str):
        raise ParseError(
            "schema_violation",
            f"catalyst_date_source must be a string, got {type(date_source).__name__}",
        )

    return ParsedDeepDive(
        ticker=ticker.strip(),
        reasoning_trace=str(reasoning),
        drug_profile=drug_profile,
        clinical_evidence=clinical_evidence,
        rnpv_by_indication=list(rnpv),
        rnpv_total_usd=float(rnpv_total),
        rnpv_per_share_usd=float(rnpv_per_share),
        lead_indication=str(lead),
        catalyst_outcome=catalyst_outcome,
        financial_overhang=fin_overhang,
        management_track_record=mtr,
        acquisition_target=acq,
        thesis_summary=str(thesis),
        key_risks=[str(x) for x in key_risks],
        catalyst_date_sanity_check=sanity,
        claude_resolved_catalyst_date=resolved_date,
        catalyst_date_source=date_source,
    )
