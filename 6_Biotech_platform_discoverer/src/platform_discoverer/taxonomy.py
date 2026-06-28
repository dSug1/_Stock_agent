"""Mechanism taxonomy tagger (spec §4 vocabulary, used by Stage 1 §5.3 and later Claude §9).

Deterministic keyword/synonym matcher over the controlled vocabulary in ``config/taxonomy.yaml``
(37 mechanisms across oncology / autoimmune / gpcr). Given a company's text (name + business
description + sector/industry, and later harvested MeSH/OpenAlex concepts), returns the mechanism ids
it matches and which terms hit (for the audit log).

Matching is recall-leaning by design: word-boundary, case-insensitive, over each mechanism's
``synonyms`` + ``example_targets``. Over-tagging is safe here — a wrongly-tagged company is just
scored and rejected later; the danger to avoid is *under*-tagging, which (with require_ta_tag) routes
a company to the review queue. Very short terms (< 3 chars, e.g. the ``C3``/``C5`` gene symbols) are
dropped to avoid spurious hits.
"""

from __future__ import annotations

import re
from typing import Any, Optional

MIN_TERM_LEN = 3


class TaxonomyTagger:
    def __init__(self, taxonomy: dict, *, min_term_len: int = MIN_TERM_LEN):
        self.mechs = taxonomy.get("mechanisms", []) or []
        self.branch_of = {m["id"]: m.get("branch") for m in self.mechs}
        self.maturity_of = {m["id"]: m.get("maturity") for m in self.mechs}
        # one compiled alternation per mechanism over its (synonyms + example_targets) terms
        self._patterns: list[tuple[re.Pattern, str]] = []
        for m in self.mechs:
            terms = sorted({t.strip() for t in
                            (m.get("synonyms", []) or []) + (m.get("example_targets", []) or [])
                            if t and len(t.strip()) >= min_term_len},
                           key=len, reverse=True)
            if not terms:
                continue
            alt = "|".join(re.escape(t) for t in terms)
            self._patterns.append((re.compile(rf"\b(?:{alt})\b", re.IGNORECASE), m["id"]))

    def tag(self, text: Optional[str]) -> list[dict[str, Any]]:
        """Return [{id, branch, maturity, terms[]}] for every mechanism whose terms appear in text."""
        if not text:
            return []
        out: list[dict[str, Any]] = []
        for rx, mid in self._patterns:
            hits = rx.findall(text)
            if hits:
                seen: list[str] = []
                for h in hits:
                    h = h.lower()
                    if h not in seen:
                        seen.append(h)
                out.append({"id": mid, "branch": self.branch_of.get(mid),
                            "maturity": self.maturity_of.get(mid), "terms": seen})
        return out

    def tag_ids(self, text: Optional[str]) -> list[str]:
        return [h["id"] for h in self.tag(text)]
