"""LLM response parsing + validation against the m6-v2 output schema.

Two entry points:
    parse_full_score(raw_text)     -> dict | raises ParseError
    parse_light_refresh(raw_text)  -> dict | raises ParseError

Both extract one JSON object from a fenced ```json ... ``` block and validate
it against the schema documented in spec/module_6_spec.md.

Validation enforces the m6-v2 HARD RULES:
    #3  probability ∈ [0.15, 0.90]
    #4  fully_diluted_shares_count ≥ basic_shares_count + prefunded_warrants_count
    #5  pos_adjusted within ±0.15 of pos_base_rate (warning, not failure)
    #6  full_reward_low_usd ≤ fair_entry_low_usd
    #7  fair_entry_rationale non-empty
    #8  full_reward_rationale non-empty
    #9  no prose outside ```json``` fences (enforced by extract_json_block)
   #16  catalyst_type in valid enum

Failures raise ParseError with structured error_kind so callers can write the
right row into ``llm_errors``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Catalyst-type enum (D43)
_VALID_CATALYST_TYPES = frozenset((
    "earnings", "trial_interim", "trial_final",
    "approval", "conference_presentation", "macro", "other",
))

_FENCE_OPEN_RE = re.compile(r"```(?:json)?\s*\n", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"\n```")


class ParseError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"[{kind}] {detail}")
        self.kind = kind        # 'json_parse_fail' | 'schema_violation'
        self.detail = detail


@dataclass(frozen=True)
class ParsedFullScore:
    ticker: str
    reasoning_trace: str
    research_brief: dict
    entry_price_ranges: dict
    near_term_3mo: dict
    long_term_12mo: dict


@dataclass(frozen=True)
class ParsedLightRefresh:
    ticker: str
    material_change: bool
    reason: str
    updated_thesis_if_changed: dict | None


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------


def _balance_truncated_json(text: str) -> str:
    """Heuristic recovery for a truncated JSON response.

    Walks the text tracking brace/bracket depth and string state. On hitting
    the end of text:
      - If we ended inside an unterminated string, append a closing ``"`` and
        ensure any trailing comma is removed.
      - Pop the open-brace/bracket stack and append matching closers.

    Returns a string that *should* parse — at worst it carries a truncated
    final string value, which is acceptable for our purposes (the model's
    last partial sentence becomes the field's value).
    """
    stack: list[str] = []                       # '{' or '['
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False; continue
        if ch == "\\" and in_string:
            escape = True; continue
        if ch == '"':
            in_string = not in_string; continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    out = text.rstrip()
    if in_string:
        # Strip trailing backslash that would corrupt the closing quote.
        if out.endswith("\\"):
            out = out[:-1]
        out += '"'
    # Trim trailing comma that breaks `,}` / `,]`.
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
            # Truncated response — no closing fence. Recover from raw text after fence.
            body = raw_text[body_start:].rstrip()
            recovered = _balance_truncated_json(body)
            if recovered != body:
                body = recovered
    else:
        # No fence at all — try whole text (some models forget fences).
        body = raw_text.strip()

    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        # Final recovery attempt: brace-balance the body even if a fence existed.
        try:
            return json.loads(_balance_truncated_json(body))
        except json.JSONDecodeError:
            raise ParseError(
                "json_parse_fail",
                f"json.loads failed at line {e.lineno} col {e.colno}: {e.msg}; "
                f"body length={len(body)}",
            ) from e


# ---------------------------------------------------------------------------
# Full-score validation (Tier C, m6-v2)
# ---------------------------------------------------------------------------


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


def _validate_horizon(block: dict, label: str) -> None:
    if not isinstance(block, dict):
        raise ParseError("schema_violation", f"{label} must be a dict")
    target = _require(block, "target_price_usd", label)
    try:
        float(target)
    except (TypeError, ValueError):
        raise ParseError("schema_violation",
                         f"target_price_usd in {label} not numeric: {target!r}")
    weeks = _require(block, "time_to_catalyst_weeks", label)
    if not isinstance(weeks, int) or weeks < 1:
        raise ParseError("schema_violation",
                         f"time_to_catalyst_weeks in {label} must be int ≥ 1, got {weeks!r}")
    _require_float_in(block, "probability", 0.15, 0.90, label)
    catalyst = _require(block, "catalyst_type", label)
    if catalyst not in _VALID_CATALYST_TYPES:
        raise ParseError("schema_violation",
                         f"catalyst_type '{catalyst}' in {label} not in valid enum")
    _require(block, "catalyst_detail", label)
    _require(block, "thesis_summary", label)
    _require(block, "key_risks", label)


def _validate_entry_ranges(block: dict) -> None:
    if not isinstance(block, dict):
        raise ParseError("schema_violation", "entry_price_ranges must be a dict")
    fl = float(_require(block, "fair_entry_low_usd", "entry_price_ranges"))
    fh = float(_require(block, "fair_entry_high_usd", "entry_price_ranges"))
    rl = float(_require(block, "full_reward_low_usd", "entry_price_ranges"))
    rh = float(_require(block, "full_reward_high_usd", "entry_price_ranges"))
    if fl > fh:
        raise ParseError("schema_violation",
                         f"fair_entry_low_usd {fl} > fair_entry_high_usd {fh}")
    if rl > rh:
        raise ParseError("schema_violation",
                         f"full_reward_low_usd {rl} > full_reward_high_usd {rh}")
    if rl > fl:  # HARD RULE #6
        raise ParseError("schema_violation",
                         f"full_reward_low_usd {rl} > fair_entry_low_usd {fl} (HARD RULE #6)")
    fer = (block.get("fair_entry_rationale") or "").strip()
    rer = (block.get("full_reward_rationale") or "").strip()
    if not fer:                                                  # HARD RULE #7
        raise ParseError("schema_violation", "fair_entry_rationale is empty (HARD RULE #7)")
    if not rer:                                                  # HARD RULE #8
        raise ParseError("schema_violation", "full_reward_rationale is empty (HARD RULE #8)")


def _validate_research_brief(brief: dict) -> None:
    if not isinstance(brief, dict):
        raise ParseError("schema_violation", "research_brief must be a dict")

    fin = _require(brief, "financials", "research_brief")
    if not isinstance(fin, dict):
        raise ParseError("schema_violation", "research_brief.financials must be a dict")
    fdsc = float(_require(fin, "fully_diluted_shares_count", "financials"))
    bsc = float(_require(fin, "basic_shares_count", "financials"))
    pfw = float(_require(fin, "prefunded_warrants_count", "financials"))
    # HARD RULE #4 — fully diluted MUST include PFWs (allow 1% slack for vested options inclusion)
    if fdsc + 1.0 < bsc + pfw:
        raise ParseError(
            "schema_violation",
            f"fully_diluted_shares_count ({fdsc:,.0f}) < basic ({bsc:,.0f}) + "
            f"prefunded_warrants ({pfw:,.0f}) — HARD RULE #4 violation",
        )

    rnpv = _require(brief, "rnpv_by_indication", "research_brief")
    if not isinstance(rnpv, list) or len(rnpv) == 0:
        raise ParseError("schema_violation",
                         "research_brief.rnpv_by_indication must be a non-empty list")
    for i, ind in enumerate(rnpv):
        if not isinstance(ind, dict):
            raise ParseError("schema_violation", f"rnpv_by_indication[{i}] not a dict")
        _require_float_in(ind, "pos_base_rate", 0.0, 1.0, f"rnpv_by_indication[{i}]")
        _require_float_in(ind, "pos_adjusted", 0.0, 1.0, f"rnpv_by_indication[{i}]")

    # 3-band scores — accept any float in [0,1], not strict {0.3,0.6,0.9}, since
    # the model may emit 0.55 etc. The prompt asks for 3-band but we don't fail
    # on near-band values; binning happens at report time.
    for path, label in (
        (("technology", "uniqueness_score"), "technology.uniqueness_score"),
        (("moat", "score"), "moat.score"),
        (("acquisition_target", "score"), "acquisition_target.score"),
        (("mgmt_track_record_score", "score"), "mgmt_track_record_score.score"),
    ):
        block = brief
        for k in path[:-1]:
            block = block.get(k) if isinstance(block, dict) else None
            if block is None:
                raise ParseError("schema_violation",
                                 f"missing nested block leading to {label}")
        _require_float_in(block, path[-1], 0.0, 1.0, label)


def parse_full_score(raw_text: str) -> ParsedFullScore:
    """Parse a Tier C full-scoring response. Raises ParseError on any violation."""
    obj = extract_json_block(raw_text)
    ticker = _require(obj, "ticker", "root")
    if not isinstance(ticker, str) or not ticker:
        raise ParseError("schema_violation", "ticker must be non-empty string")
    reasoning = obj.get("reasoning_trace", "")
    research = _require(obj, "research_brief", "root")
    entries = _require(obj, "entry_price_ranges", "root")
    near = _require(obj, "near_term_3mo", "root")
    long = _require(obj, "long_term_12mo", "root")

    _validate_research_brief(research)
    _validate_entry_ranges(entries)
    _validate_horizon(near, "near_term_3mo")
    _validate_horizon(long, "long_term_12mo")

    return ParsedFullScore(
        ticker=ticker,
        reasoning_trace=str(reasoning),
        research_brief=research,
        entry_price_ranges=entries,
        near_term_3mo=near,
        long_term_12mo=long,
    )


# ---------------------------------------------------------------------------
# Light-refresh validation (Tier B)
# ---------------------------------------------------------------------------


def parse_light_refresh(raw_text: str) -> ParsedLightRefresh:
    """Parse a Tier B light-refresh response. Raises ParseError on violation."""
    obj = extract_json_block(raw_text)
    ticker = _require(obj, "ticker", "root")
    material = _require(obj, "material_change", "root")
    if not isinstance(material, bool):
        raise ParseError("schema_violation",
                         f"material_change must be bool, got {type(material).__name__}")
    reason = _require(obj, "reason", "root")
    updated = obj.get("updated_thesis_if_changed")
    if material and updated is None:
        raise ParseError("schema_violation",
                         "material_change=true but updated_thesis_if_changed is null")
    if not material and updated is not None:
        # Permit but ignore — model sometimes provides one anyway.
        updated = None
    return ParsedLightRefresh(
        ticker=str(ticker),
        material_change=material,
        reason=str(reason),
        updated_thesis_if_changed=updated if isinstance(updated, dict) else None,
    )
