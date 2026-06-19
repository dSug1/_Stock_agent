"""Cost estimation (offline) for Claude calls.

Estimates BEFORE any network call so the user can authorize spend (D4). Uses a
char/4 token heuristic (no API needed); the real cost is logged post-call from
`usage`. Pricing + cache multipliers come from config/resolver.yaml.
"""

from __future__ import annotations

_CHARS_PER_TOKEN = 4.0


def _tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def estimate_cost(
    *,
    system: str,
    user: str,
    max_output_tokens: int,
    model: str,
    pricing: dict,
    cache_write_multiplier: float = 1.25,
    cache_read_multiplier: float = 0.10,
    calibration_factor: float = 1.0,
    cached_system: bool = True,
) -> dict:
    """Return an estimate dict for one call. Output assumed at the max budget
    (worst case). The system prompt is small and cached (write on first call)."""
    rate = pricing.get(model)
    if not rate:
        raise ValueError(f"no pricing for model {model!r} in resolver.yaml")

    sys_tok = _tokens(system)
    usr_tok = _tokens(user)
    out_tok = int(max_output_tokens)

    in_rate = rate["input"] / 1_000_000
    out_rate = rate["output"] / 1_000_000

    # System is cacheable (write premium on first call); user HTML is full price.
    sys_mult = cache_write_multiplier if cached_system else 1.0
    input_usd = (sys_tok * sys_mult + usr_tok) * in_rate
    output_usd = out_tok * out_rate
    total = (input_usd + output_usd) * calibration_factor

    return {
        "model": model,
        "input_tokens": sys_tok + usr_tok,
        "output_tokens_max": out_tok,
        "input_usd": input_usd * calibration_factor,
        "output_usd": output_usd * calibration_factor,
        "total_usd": total,
        "note": "estimate (char/4 heuristic, output at max); actual logged post-call",
    }


def format_cost_panel(est: dict, *, n_calls: int = 1) -> str:
    """Human-readable cost preview for the [y/N] gate. ASCII-only (Windows
    consoles use cp1252 for stdout and choke on box-drawing chars)."""
    total = est["total_usd"] * n_calls
    bar = "-" * 54
    lines = [
        "  +" + bar,
        "  | CLAUDE COST ESTIMATE",
        "  +" + bar,
        f"  | model        : {est['model']}",
        f"  | calls        : {n_calls}",
        f"  | input tokens : ~{est['input_tokens']:,} / call",
        f"  | output budget: ~{est['output_tokens_max']:,} tokens / call",
        f"  | est. cost    : ${est['total_usd']:.4f} / call"
        + (f"  ->  ${total:.4f} total" if n_calls != 1 else ""),
        f"  | ({est['note']})",
        "  +" + bar,
    ]
    return "\n".join(lines)
