"""Signal ingestion jobs (spec §3) — each writes `signal` rows for the active universe, zero-LLM.

Phase 2 starts with capital-markets (EDGAR §3.5). Literature/patents/trials follow. Each job is
fail-soft and idempotent (signal_id is a stable hash of the underlying event).
"""
