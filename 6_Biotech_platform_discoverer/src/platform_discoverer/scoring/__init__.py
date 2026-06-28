"""Claude scoring layer (spec §9–§10) — the judgment Stage 3 embeddings can't do.

rubric  — the evidence-bundle builder, the §9.5 system prompt, the §9.3 output schema, validation.
composite — the §10 weighted composite + moat/substance penalties + confidence.
(The tiered orchestration lives in ``stage4`` and the API wrapper in ``clients.anthropic_client``.)
"""
