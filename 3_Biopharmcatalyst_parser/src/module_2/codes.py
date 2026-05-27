"""SEC Form 4 transaction-code lookup table.

Spec §4.3.4 v1 minimum + a few more codes from the official SEC Form 4
instructions. The single source of truth for ``transaction_code_meaning``
(stored verbatim in ``edgar_form4_transactions.transaction_code_meaning``)
and the ``is_open_market`` derivation (TRUE iff code IN {'P','S'}).

Spec §2.3 mandates ``is_open_market = TRUE iff code IN ('P','S')`` —
don't broaden this set without a spec rev. Open-market purchases and
sales are the only insider transactions that signal a voluntary capital
commitment by the insider; everything else (grants, exercises, tax
withholding, gifts) is either compensation mechanics or non-economic.
"""
from __future__ import annotations


# (code → (meaning, is_open_market))
TRANSACTION_CODES: dict[str, tuple[str, bool]] = {
    # --- General transaction codes ---
    "P": ("Open-market or private purchase of non-derivative", True),
    "S": ("Open-market or private sale of non-derivative", True),
    "V": ("Voluntary reported transaction", False),

    # --- Rule 16b-3 transaction codes (compensation-related) ---
    "A": ("Grant, award or other acquisition", False),
    "D": ("Disposition to issuer of issuer equity securities", False),
    "F": ("Payment of exercise price or tax liability via security withholding", False),
    "I": ("Discretionary transaction (Rule 16b-3(f))", False),
    "M": ("Exercise or conversion of derivative security exempted under Rule 16b-3", False),

    # --- Derivative-security codes ---
    "C": ("Conversion of derivative security", False),
    "E": ("Expiration of short derivative position", False),
    "H": ("Expiration (or cancellation) of long derivative position with value received", False),
    "O": ("Exercise of out-of-the-money derivative", False),
    "X": ("Exercise of in-the-money or at-the-money derivative", False),

    # --- Other ---
    "G": ("Bona fide gift", False),
    "L": ("Small acquisition under Rule 16a-6", False),
    "W": ("Acquisition or disposition by will or laws of descent", False),
    "Z": ("Deposit into or withdrawal from voting trust", False),
    "J": ("Other acquisition or disposition", False),
    "K": ("Transaction in equity swap or similar instrument", False),
    "U": ("Disposition pursuant to a tender of shares in a change-of-control transaction", False),
}


def meaning_for(code: str) -> str:
    """Human-readable label for a SEC Form 4 transaction code. Unknown
    codes get a generic 'Other ({code})' label rather than raising,
    so a stray code in real data doesn't crash the parser."""
    code = (code or "").strip().upper()
    if not code:
        return ""
    entry = TRANSACTION_CODES.get(code)
    if entry is None:
        return f"Other ({code})"
    return entry[0]


def is_open_market(code: str) -> bool:
    """TRUE iff code IN {'P','S'} (spec §2.3 + spec §4.3.4 table).

    The spec is strict about this: only P (purchase) and S (sale) count
    as open-market. Everything else is compensation mechanics or
    derivative-exercise plumbing and should NOT be used as an insider-
    conviction signal.
    """
    return (code or "").strip().upper() in ("P", "S")
